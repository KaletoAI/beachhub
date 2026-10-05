"""Portal-Buchungen: Reservierung mit Online-Zahlung, Bestätigung, Storno und Verfall.

A-ZAHL-1 bis -4. Jede Funktion arbeitet in der Transaktion des Aufrufers und committet nicht.
"""

import logging
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from beachhub_shared import kanal
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from beachhub_core import clock, zahlung
from beachhub_core.models import (
    Buchung,
    Dauerbuchung,
    GuthabenBuchung,
    Kunde,
    Storno,
    Zahlung,
    utcnow,
)
from beachhub_core.services import (
    benachrichtigung,
    buchungen,
    guthaben,
    gutschein_belege,
    gutschein_einloesung,
    gutschein_sperren,
    konfiguration,
    kunden,
    storno,
)
from beachhub_core.services.ergebnis import Ergebnis, Nachlauf, abgelehnt, ignoriert, ok

logger = logging.getLogger(__name__)
NULL = Decimal("0.00")

# lege_an kennt feinere Gründe als der Kanalvertrag (Hauptspec § 8.1).
_GRUND = {"vergangenheit": "ausserhalb_fenster", "kunde_unbekannt": "konto_gesperrt"}

__all__ = [
    "Ergebnis",
    "alarm",
    "anfragen",
    "storniere_fuer_kunde",
    "verfalle_abgelaufene",
    "zahlung_eingegangen",
]


def _bestaetigung_versenden(buchung_id: uuid.UUID, rechnung_ids: Sequence[uuid.UUID]) -> Nachlauf:
    def lauf(db: Session) -> None:
        b = db.get(Buchung, buchung_id)
        if b is None:
            return
        try:
            benachrichtigung.buchung_bestaetigt(db, b)
        except Exception:
            logger.exception("Buchungsbestätigung %s nicht versandt", buchung_id)
            db.rollback()
        benachrichtigung.belege_versenden(db, rechnung_ids)

    return lauf


def _storno_mail(storno_id: uuid.UUID) -> Nachlauf:
    def lauf(db: Session) -> None:
        s = db.get(Storno, storno_id)
        if s is not None:
            benachrichtigung.storno(db, s)
            benachrichtigung.belege_versenden(db, [s.korrektur_rechnung_id])

    return lauf


def alarm(betreff: str, text: str) -> Nachlauf:
    """Schickt dem Betreiber nach dem Commit eine Alarm-Mail mit dem gegebenen Betreff/Text."""

    def lauf(db: Session) -> None:
        benachrichtigung.betreiber_alarm(betreff, text)

    return lauf


def _bestaetige(db: Session, b: Buchung, antwort: kanal.Antwort) -> Ergebnis:
    buchungen.setze_status(db, b, Buchung.BESTAETIGT, quelle="portal")
    b.reserviert_bis = None
    belege = gutschein_belege.rechnung_fuer_buchung(db, b, quelle="portal")
    gutschein_einloesung.bestaetige_einloesungen(db, b)
    ids = ([belege.geldrechnung.id] if belege.geldrechnung else []) + [
        r.id for r in belege.gutscheinbelege
    ]
    return Ergebnis(antwort, [_bestaetigung_versenden(b.id, ids)])


def _buche_verrechnung_zurueck(db: Session, b: Buchung, quelle: str) -> None:
    """Gibt das bei der Reservierung verrechnete Guthaben zurück. Zählt bereits erfolgte
    Rückbuchungen mit, damit ein zweiter Aufruf nichts doppelt gutschreibt."""
    summe = db.scalar(
        select(func.coalesce(func.sum(GuthabenBuchung.betrag), 0)).where(
            GuthabenBuchung.bezug_id == b.id,
            GuthabenBuchung.art.in_(("verrechnung", "rueckbuchung")),
        )
    )
    offen = -Decimal(str(summe))
    if offen > NULL:
        guthaben.buche(
            db,
            kunde=b.kunde,
            betrag=offen,
            art="rueckbuchung",
            bezug_id=b.id,
            notiz="Reservierung ohne Zahlung beendet",
            quelle=quelle,
        )


def anfragen(
    db: Session,
    *,
    kunde: Kunde,
    anfrage_id: uuid.UUID,
    feld_id: uuid.UUID,
    beginn: datetime,
    ende: datetime,
    rueckkehr_url: str,
    gutschein_codes: Sequence[str] = (),
) -> Ergebnis:
    # Bleibt bei einer abgelehnten Buchung außerhalb ihres Savepoints erhalten.
    kontext = gutschein_einloesung.bereite_codes_vor(db, kunde, gutschein_codes)
    if gutschein_codes:
        try:
            gutschein_sperren.pruefe_rate_limit(db, kunde.id)
        except gutschein_sperren.GutscheinRateLimitFehler:
            return abgelehnt("zu_viele_versuche")

    def ablehnen(grund: str) -> Ergebnis:
        if gutschein_codes:
            gutschein_sperren.merke_fehlversuch(db, kunde.id, anfrage_id=anfrage_id)
        return abgelehnt(grund)

    if kunde.anonymisiert_am is not None:
        return ablehnen("konto_gesperrt")
    if not kunden.darf_online_buchen(db, kunde):
        # Rechnungskunden buchen nicht online; ihre Buchungen legt der Betreiber an (A-KUND-7).
        return ablehnen("rechnungskunde")
    try:
        with db.begin_nested():
            anlage = buchungen.lege_an_mit_preisstand(
                db,
                feld_id=feld_id,
                kunde_id=kunde.id,
                beginn=beginn,
                ende=ende,
                quelle="portal",
                pruefe_fenster=True,
                status=Buchung.RESERVIERT,
                anfrage_id=anfrage_id,
                zahlungsart="online",
            )
            b = anlage.buchung
            deckung = gutschein_einloesung.loese_ein(
                db,
                b,
                kunde,
                gutschein_codes,
                sperrkontext=kontext,
                preisstand=anlage.preisstand,
            )
    except (buchungen.BuchungsFehler, gutschein_einloesung.GutscheinFehler) as e:
        return ablehnen(_GRUND.get(e.grund, e.grund))
    except IntegrityError:
        # Exklusionsconstraint: Ein gleichzeitiger Kunde war schneller.
        return ablehnen("belegt")

    db.refresh(kunde)
    verrechnet = max(NULL, min(kunde.guthaben, b.preis - deckung.betrag))
    if verrechnet > NULL:
        guthaben.buche(
            db,
            kunde=kunde,
            betrag=-verrechnet,
            art="verrechnung",
            bezug_id=b.id,
            notiz="Verrechnung mit Portal-Buchung",
            quelle="portal",
        )
    rest = b.preis - deckung.betrag - verrechnet
    if rest == NULL:
        if deckung.einloesungen and deckung.betrag == b.preis:
            b.zahlungsart = "gutschein"
        antwort = kanal.Antwort(
            status="bestaetigt",
            buchung_id=b.id,
            preis=b.preis,
            guthaben_verrechnet=verrechnet,
            gutschein_verrechnet=deckung.betrag if gutschein_codes else None,
        )
        return _bestaetige(db, b, antwort)

    anbieter = zahlung.anbieter()
    ablauf = clock.now(db) + timedelta(minutes=konfiguration.hole(db, "zahlungsfrist_minuten"))
    sitzung = anbieter.erzeuge_sitzung(
        betrag=rest, referenz=b.id, ablauf=ablauf, rueckkehr_url=rueckkehr_url
    )
    db.add(
        Zahlung(
            kunde_id=kunde.id,
            buchung_id=b.id,
            provider=anbieter.name,
            provider_ref=sitzung.provider_ref,
            betrag=rest,
            checkout_url=sitzung.checkout_url,
        )
    )
    b.reserviert_bis = ablauf
    db.flush()
    return Ergebnis(
        kanal.Antwort(
            status="reserviert",
            buchung_id=b.id,
            preis=b.preis,
            guthaben_verrechnet=verrechnet,
            gutschein_verrechnet=deckung.betrag if gutschein_codes else None,
            zu_zahlen=rest,
            checkout_url=sitzung.checkout_url,
            reserviert_bis=ablauf,
        )
    )


def zahlung_eingegangen(db: Session, n: kanal.ZahlungEingegangen) -> Ergebnis:
    anbieter = zahlung.anbieter_fuer(n.provider)
    if anbieter is None:
        return ignoriert("anbieter_unbekannt")
    ref = anbieter.verifiziere(n.rohdaten, n.signatur_header)
    if ref is None:
        # Nur Anbieter und Länge: Die Rohdaten können Zahlungsdaten des Kunden enthalten.
        logger.warning(
            "Zahlungsrückmeldung (%s) nicht verifizierbar, %d Zeichen",
            n.provider,
            len(n.rohdaten),
        )
        return ignoriert("nicht_verifiziert")
    # Vollständige Menge vor Zahlung und Buchung; IDs zuerst ohne Schreibsperre.
    ids = db.execute(
        select(Zahlung.id, Zahlung.kunde_id, Zahlung.buchung_id).where(Zahlung.provider_ref == ref)
    ).first()
    if ids is not None:
        if ids.buchung_id is not None:
            kontext = gutschein_sperren.bereite_buchungen_vor(
                db, [ids.buchung_id], kunde_ids=[ids.kunde_id]
            )
        else:
            kontext = gutschein_sperren.bereite_vorgang_vor(
                db, kunde_ids=[ids.kunde_id], gutschein_ids=[], zahlung_ids=[ids.id]
            )
        z = kontext.zahlungen.get(ids.id)
        if z is None or z.kunde_id != ids.kunde_id or z.buchung_id != ids.buchung_id:
            raise gutschein_sperren.GutscheinVorbereitungWiederholen("Zahlungszuordnung geändert")
    else:
        z = None
    if z is None:
        logger.warning("Zahlungsrückmeldung zu unbekannter Referenz %s", ref)
        text = (
            f"Zahlungsrückmeldung ({n.provider}) zu unbekannter Referenz {ref} eingegangen; "
            "keine passende Zahlung im System gefunden."
        )
        return Ergebnis(
            kanal.Antwort(status="ignoriert", grund="zahlung_unbekannt"),
            [alarm("Zahlungsrückmeldung ohne Zuordnung", text)],
        )
    if z.status == Zahlung.BEZAHLT:
        return ok()
    # Dem Anbieter trauen, nie den Rohdaten (A-ZAHL-2).
    zustand, betrag = anbieter.status(ref)
    if zustand == "offen":
        return ignoriert("offen")
    if zustand == "bezahlt" and betrag <= NULL:
        # Ein unplausibler Betrag darf die Zahlung nicht als bezahlt markieren – sonst würde
        # eine später eingehende echte Zahlung am frühen `z.status == BEZAHLT`-Ausstieg oben
        # folgenlos verpuffen (Reservierung verfällt, Geld wäre weg).
        logger.warning(
            "Zahlungsrückmeldung zu %s mit unplausiblem Betrag %s ignoriert", ref, betrag
        )
        return ignoriert("betrag_ungueltig")
    if zustand != "bezahlt":
        z.status = Zahlung.ABGEBROCHEN
        return ok()

    z.status = Zahlung.BEZAHLT
    z.empfangen_am = utcnow()
    z.rohdaten_json = {"provider": n.provider, "rohdaten": n.rohdaten[:4000]}
    b = (
        db.scalar(
            select(Buchung)
            .where(Buchung.id == z.buchung_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if z.buchung_id
        else None
    )
    if b is not None and b.status == Buchung.RESERVIERT and betrag >= z.betrag:
        erg = _bestaetige(db, b, kanal.Antwort(status="ok"))
        ueberzahlt = betrag - z.betrag
        if ueberzahlt > NULL:
            guthaben.buche(
                db,
                kunde=b.kunde,
                betrag=ueberzahlt,
                art="ueberzahlung",
                bezug_id=z.id,
                notiz="Überzahlung bei Online-Buchung",
                quelle="portal",
            )
            text = (
                f"Die Zahlung {ref} über {betrag} € zur Buchung {b.id} überzahlt den offenen "
                f"Betrag ({z.betrag} €) um {ueberzahlt} €. Die Differenz wurde dem Kunden "
                f"{b.kunde.name} <{b.kunde.email}> als Guthaben gutgeschrieben."
            )
            erg.nach_commit.append(alarm("Überzahlung bei Online-Buchung", text))
        return erg

    # Geld ist da, aber es gibt nichts (mehr) zu bestätigen: Guthaben, der Betreiber entscheidet.
    # betrag > NULL ist hier garantiert (der Zweig oben hat betrag <= NULL bereits behandelt).
    grund = (
        "Zahlung nach Verfall oder Storno"
        if betrag >= z.betrag
        else "Zahlung unter dem offenen Betrag"
    )
    guthaben.buche(
        db,
        kunde=z.kunde,
        betrag=betrag,
        art="ueberzahlung",
        bezug_id=z.id,
        notiz=grund,
        quelle="portal",
    )
    text = (
        f"Die Zahlung {ref} über {betrag} € zur Buchung {z.buchung_id} ist eingegangen, "
        f"konnte aber nichts bestätigen ({grund}). Der Betrag wurde dem Kunden "
        f"{z.kunde.name} <{z.kunde.email}> als Guthaben gutgeschrieben."
    )
    return Ergebnis(kanal.Antwort(status="ok"), [alarm(grund, text)])


def storniere_fuer_kunde(db: Session, *, kunde: Kunde, buchung_id: uuid.UUID) -> Ergebnis:
    # Finanzielle Stornos sperren den Kunden vor Buchung/Dauerbuchung/Rechnung.
    kunden.sperre_mehrere(db, [kunde.id])
    b = db.scalar(
        select(Buchung)
        .where(Buchung.id == buchung_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        b is None
        or b.kunde_id != kunde.id
        or b.status not in (Buchung.RESERVIERT, Buchung.BESTAETIGT)
    ):
        return abgelehnt("nicht_gefunden")
    if clock.now(db) >= b.beginn:
        return abgelehnt("zu_spaet")
    if not b.im_portal_stornierbar:
        # Einzelbuchungen des Betreibers storniert nur er.
        return abgelehnt("nicht_stornierbar")
    war_reserviert = b.status == Buchung.RESERVIERT
    s = storno.storniere(
        db,
        b,
        durch="kunde",
        grund="Storno im Portal",
        # Eine Reservierung ist nie bezahlt worden; ihr Storno kostet nichts.
        kostenfrei=True if war_reserviert else None,
    )
    if war_reserviert:
        _buche_verrechnung_zurueck(db, b, quelle="portal")
    rest = None
    if b.dauerbuchung_id is not None:
        dauer = db.get(Dauerbuchung, b.dauerbuchung_id)
        rest = storno.freie_absagen_rest(db, dauer) if dauer is not None else None
    antwort = kanal.Antwort(
        status="ok",
        kostenfrei=s.kostenfrei,
        freie_absage=s.freie_absage if b.dauerbuchung_id is not None else None,
        verbleibende_freie_absagen=rest,
    )
    return Ergebnis(antwort, [_storno_mail(s.id)])


def verfalle_abgelaufene(db: Session) -> list[Buchung]:
    jetzt = clock.now(db)
    abgelaufen_filter = (
        Buchung.status == Buchung.RESERVIERT,
        Buchung.reserviert_bis.is_not(None),
        Buchung.reserviert_bis < jetzt,
    )
    kandidaten = db.execute(select(Buchung.id, Buchung.kunde_id).where(*abgelaufen_filter)).all()
    gutschein_sperren.bereite_buchungen_vor(db, (bid for bid, _ in kandidaten))
    # Nach Warten auf Kunden erneut unter Buchungssperre prüfen; inzwischen
    # bestätigte/stornierte Reservierungen dürfen weder verfallen noch Guthaben erhalten.
    abgelaufen = list(
        db.scalars(
            select(Buchung)
            .where(Buchung.id.in_([bid for bid, _ in kandidaten]), *abgelaufen_filter)
            .order_by(Buchung.id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        ).all()
    )
    for b in abgelaufen:
        buchungen.setze_status(db, b, Buchung.VERFALLEN, quelle="system")
        _buche_verrechnung_zurueck(db, b, quelle="system")
    return abgelaufen
