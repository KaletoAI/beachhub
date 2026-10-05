"""Kauf-Snapshots und atomarer Abschluss; externe Effekte ausschließlich im Nachlauf."""

import re
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from beachhub_shared import kanal
from beachhub_shared.zeit import lokales_datum
from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_core import clock, zahlung
from beachhub_core.models import Gutschein, Kunde, Kundengruppe, Rechnung, Zahlung
from beachhub_core.services import (
    audit,
    benachrichtigung,
    guthaben,
    gutschein_codes,
    gutschein_sperren,
    gutscheine,
    konfiguration,
    kundengruppen,
    rechnungen,
)
from beachhub_core.services.ergebnis import Ergebnis, Nachlauf, abgelehnt, ignoriert, ok
from beachhub_core.services.gutschein_typen import GutscheinSperrkontext

NULL = Decimal("0.00")
_EMAIL = re.compile(r"[^\s@<>:,;]+@[^\s@<>:,;]+\.[^\s@<>:,;]+")


def gruppenhinweis(db: Session, g: Gutschein) -> str:
    gruppe = db.get(Kundengruppe, g.kundengruppe_id) if g.kundengruppe_id else None
    name = "Mitgliedsgutschein" if gruppe is not None and gruppe.ist_mitglied else "Gutschein"
    einschraenkung = (
        "nur für DJK-Mitglieder einlösbar" if g.nur_mitglieder else "für alle einlösbar"
    )
    return f"{name} – {einschraenkung}"


def anfragen(
    db: Session,
    *,
    kunde: Kunde,
    empfaenger_email: str | None,
    betrag: Decimal | None,
    rueckkehr_url: str,
    erwartetes_konto_id: uuid.UUID | None = None,
) -> Ergebnis:
    gutschein_sperren.bereite_vorgang_vor(db, kunde_ids=[kunde.id], gutschein_ids=[])
    if kunde.anonymisiert_am is not None:
        return abgelehnt("konto_gesperrt")
    if kunde.portal_konto_id is None or (
        erwartetes_konto_id is not None and kunde.portal_konto_id != erwartetes_konto_id
    ):
        return abgelehnt("konto_unbekannt")
    if kunde.rechnungskunde and not konfiguration.hole(db, "rechnungskunden_gutscheinkauf"):
        return abgelehnt("rechnungskunde")
    email = empfaenger_email.strip().lower() if empfaenger_email is not None else None
    if email is not None and (len(email) > 200 or _EMAIL.fullmatch(email) is None):
        return abgelehnt("ungueltig")
    jetzt = clock.now(db)
    ablauf = jetzt + timedelta(minutes=konfiguration.hole(db, "zahlungsfrist_minuten"))
    gruppe = kundengruppen.effektive_gruppe(db, kunde, lokales_datum(jetzt))
    modell = konfiguration.hole(db, "gutschein_modell")
    steuer = konfiguration.hole(db, "gutschein_steuer")
    if modell == "einheit":
        if betrag is not None:
            return abgelehnt("ungueltig")
        preis = konfiguration.hole(
            db,
            "gutschein_preis_mitglied" if gruppe.ist_mitglied else "gutschein_preis_nichtmitglied",
        )
        if preis <= NULL:
            return abgelehnt("kein_angebot")
    else:
        if betrag is None or betrag not in konfiguration.hole(db, "gutschein_betraege").betraege:
            return abgelehnt("kein_angebot")
        preis = betrag
    g = Gutschein(
        id=uuid.uuid4(),
        art="kauf",
        modell=modell,
        steuer=steuer,
        minuten=konfiguration.hole(db, "gutschein_minuten") if modell == "einheit" else None,
        nennwert_brutto=preis if modell == "wert" else None,
        restwert=preis if modell == "wert" else None,
        kaufpreis_brutto=preis,
        kundengruppe_id=gruppe.id,
        ust_satz=gruppe.ust_satz,
        nur_mitglieder=bool(
            gruppe.ist_mitglied
            and steuer == "beim_kauf"
            and konfiguration.hole(db, "mitgliedsgutschein_nur_mitglieder")
        ),
        kaeufer_kunde_id=kunde.id,
        inhaber_kunde_id=kunde.id,
        ausgestellt_am=jetzt,
        kauf_reserviert_bis=ablauf,
        gueltig_tage_snapshot=konfiguration.hole(db, "gutschein_gueltig_tage"),
        status=Gutschein.OFFEN,
        empfaenger_email=email,
    )
    anbieter = zahlung.anbieter()
    sitzung = anbieter.erzeuge_sitzung(
        betrag=preis,
        referenz=g.id,
        ablauf=ablauf,
        rueckkehr_url=rueckkehr_url,
    )
    z = Zahlung(
        kunde_id=kunde.id,
        provider=anbieter.name,
        provider_ref=sitzung.provider_ref,
        betrag=preis,
        checkout_url=sitzung.checkout_url,
    )
    db.add(z)
    db.flush()
    g.zahlung_id = z.id
    gutschein_codes.fuege_mit_neuem_code_ein(db, g)
    gutscheine._aenderung(db, g, None, quelle="portal", admin_user_id=None)
    return Ergebnis(
        kanal.Antwort(
            status="reserviert",
            preis=preis,
            guthaben_verrechnet=NULL,
            zu_zahlen=preis,
            checkout_url=z.checkout_url,
            reserviert_bis=g.kauf_reserviert_bis,
        )
    )


def _abschliessen_vorbereitet(
    db: Session, g: Gutschein, *, zeitpunkt: datetime, quelle: str
) -> Rechnung:
    if g.rechnung_id is not None:
        r = db.get(Rechnung, g.rechnung_id)
        assert r is not None
        return r
    z = db.get(Zahlung, g.zahlung_id) if g.zahlung_id else None
    if (
        g.art != "kauf"
        or g.status != Gutschein.OFFEN
        or z is None
        or z.status != Zahlung.BEZAHLT
        or z.kunde_id != g.kaeufer_kunde_id
        or z.betrag != g.kaufpreis_brutto
        or g.kauf_reserviert_bis is None
        or zeitpunkt >= g.kauf_reserviert_bis
    ):
        raise ValueError("Der Gutscheinkauf kann nicht abgeschlossen werden")
    vorher = audit.als_dict(g)
    kaeufer = db.get(Kunde, g.kaeufer_kunde_id)
    assert kaeufer is not None and g.gueltig_tage_snapshot is not None
    assert g.kaufpreis_brutto is not None and g.ust_satz is not None
    g.kaufdatum = lokales_datum(zeitpunkt)
    g.gueltig_bis = g.kaufdatum + timedelta(days=g.gueltig_tage_snapshot)
    r = rechnungen._neue_rechnung(
        db,
        kaeufer,
        "gutschein",
        [
            rechnungen.Posten(
                None,
                gruppenhinweis(db, g),
                g.kaufpreis_brutto,
                g.ust_satz if g.steuer == "beim_kauf" else NULL,
            )
        ],
        g.kaufdatum,
        g.kaufdatum,
        "bezahlt",
        quelle=quelle,
        kreis="rechnung" if g.steuer == "beim_kauf" else "gutschein",
        gutschein_rolle="kauf",
        gutschein_ids=[g.id],
    )
    g.rechnung_id, z.rechnung_id, g.status = r.id, r.id, Gutschein.AKTIV
    gutscheine._aenderung(db, g, vorher, quelle=quelle, admin_user_id=None)
    db.flush()
    return r


def abschliessen(db: Session, g: Gutschein, *, quelle: str = "portal") -> Rechnung:
    """Interner Abschluss ausschließlich einer bereits verifiziert bezahlten Kaufzahlung."""
    kontext = gutschein_sperren.bereite_vorgang_vor(db, kunde_ids=[], gutschein_ids=[g.id])
    return _abschliessen_vorbereitet(
        db, kontext.gutscheine[g.id], zeitpunkt=clock.now(db), quelle=quelle
    )


def _nachlauf(gutschein_id: uuid.UUID, rechnung_id: uuid.UUID) -> Nachlauf:
    def lauf(db: Session) -> None:
        # Versandfehler einzelner Code-Mails/PDFs dürfen die anderen Empfänger nicht verhindern.
        benachrichtigung.gutschein_gekauft(db, gutschein_id)
        benachrichtigung.belege_versenden(db, [rechnung_id])

    return lauf


def _brich_ab(db: Session, g: Gutschein, *, quelle: str) -> None:
    if g.status != Gutschein.OFFEN:
        return
    vorher = audit.als_dict(g)
    g.status = Gutschein.ABGEBROCHEN
    z = db.get(Zahlung, g.zahlung_id) if g.zahlung_id else None
    if z is not None and z.status != Zahlung.BEZAHLT:
        z.status = Zahlung.ABGEBROCHEN
    gutscheine._aenderung(db, g, vorher, quelle=quelle, admin_user_id=None)
    db.flush()


def zahlung_eingegangen(
    db: Session,
    z: Zahlung,
    betrag: Decimal,
    *,
    sperrkontext: GutscheinSperrkontext | None = None,
) -> Ergebnis | None:
    """Interner Dispatch nach Providerverifikation; keine neue Sperrmenge nach Zahlungslocks."""
    gid = db.scalar(select(Gutschein.id).where(Gutschein.zahlung_id == z.id))
    if gid is None:
        return None
    if not betrag.is_finite() or betrag <= NULL:
        return ignoriert("betrag_ungueltig")
    kontext = sperrkontext or gutschein_sperren.bereite_vorgang_vor(
        db, kunde_ids=[z.kunde_id], gutschein_ids=[gid], zahlung_ids=[z.id]
    )
    if gid not in kontext.gutscheine or z.id not in kontext.zahlungen:
        raise gutschein_sperren.GutscheinVorbereitungWiederholen("Kaufzuordnung geändert")
    g, z = kontext.gutscheine[gid], kontext.zahlungen[z.id]
    if g.zahlung_id != z.id or z.kunde_id != g.kaeufer_kunde_id or z.buchung_id is not None:
        raise gutschein_sperren.GutscheinVorbereitungWiederholen("Kaufzuordnung geändert")
    if z.status == Zahlung.BEZAHLT:
        return ok()
    jetzt = clock.now(db)
    z.status, z.empfangen_am = Zahlung.BEZAHLT, jetzt
    assert g.kaufpreis_brutto is not None and g.kauf_reserviert_bis is not None
    rechtzeitig = g.status == Gutschein.OFFEN and jetzt < g.kauf_reserviert_bis
    nachlauf = []
    if rechtzeitig and betrag >= g.kaufpreis_brutto:
        r = _abschliessen_vorbereitet(db, g, zeitpunkt=jetzt, quelle="portal")
        nachlauf.append(_nachlauf(g.id, r.id))
        gutschrift = betrag - g.kaufpreis_brutto
        grund = "Überzahlung beim Gutscheinkauf"
    else:
        _brich_ab(db, g, quelle="portal")
        gutschrift = betrag
        grund = "Unterzahlung beim Gutscheinkauf" if rechtzeitig else "Zahlung nach Kaufabbruch"
    if gutschrift > NULL:
        guthaben.buche(
            db,
            kunde=z.kunde,
            betrag=gutschrift,
            art="ueberzahlung",
            bezug_id=z.id,
            notiz=grund,
            quelle="portal",
        )
        from beachhub_core.services.online_buchung import alarm

        nachlauf.append(
            alarm(
                grund,
                f"Zahlung {z.provider_ref} zum Gutscheinkauf {g.id}: "
                f"{betrag} € eingegangen; {gutschrift} € Käuferguthaben für {z.kunde.name} "
                f"<{z.kunde.email}>. {grund}.",
            )
        )
    db.flush()
    return Ergebnis(kanal.Antwort(status="ok"), nachlauf)


def brich_abgelaufene_ab(db: Session) -> int:
    """Eigener Batch mit vollständiger Kunden-/Zahlungs-/Gutscheinmenge vor Mutationen."""
    jetzt = clock.now(db)
    ids = list(
        db.scalars(
            select(Gutschein.id).where(
                Gutschein.art == "kauf",
                Gutschein.status == Gutschein.OFFEN,
                Gutschein.kauf_reserviert_bis <= jetzt,
            )
        )
    )
    kontext = gutschein_sperren.bereite_vorgang_vor(db, kunde_ids=[], gutschein_ids=ids)
    anzahl = 0
    for g in kontext.gutscheine.values():
        if (
            g.status == Gutschein.OFFEN
            and g.kauf_reserviert_bis is not None
            and jetzt >= g.kauf_reserviert_bis
        ):
            _brich_ab(db, g, quelle="system")
            anzahl += 1
    return anzahl
