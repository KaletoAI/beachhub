import logging
import uuid
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from beachhub_core import clock, mail
from beachhub_core.config import settings
from beachhub_core.models import (
    Buchung,
    Dauerbuchung,
    GuthabenBuchung,
    Gutschein,
    GutscheinEinloesung,
    Kunde,
    Rechnung,
    Storno,
)
from beachhub_core.services import gutschein_codes, konfiguration, pin
from beachhub_core.templating import euro, templates

logger = logging.getLogger(__name__)


def _text(name: str, **ctx: object) -> str:
    return templates.env.get_template(f"mail/{name}.txt").render(
        betreiber=settings.betreiber_name, **ctx
    )


def buchung_bestaetigt(db: Session, b: Buchung) -> None:
    vorlauf = konfiguration.hole(db, "zutritt_vorlauf_minuten")
    mail.sende(
        b.kunde.email,
        f"Buchung bestätigt: {templates.env.filters['lokal'](b.beginn)}",
        _text(
            "buchung_bestaetigt",
            b=b,
            gutschein_verrechnet=Decimal(
                str(
                    db.scalar(
                        select(func.coalesce(func.sum(GutscheinEinloesung.betrag), 0)).where(
                            GutscheinEinloesung.buchung_id == b.id,
                            GutscheinEinloesung.status != GutscheinEinloesung.RUECKGAENGIG,
                        )
                    )
                )
            ),
            pin=pin.entschluessele(b.pin_verschluesselt or ""),
            vorlauf=vorlauf,
        ),
    )


def storno(db: Session, s: Storno) -> None:
    rest = None
    if s.buchung.dauerbuchung_id is not None:
        from beachhub_core.services import storno as storno_dienst

        dauer = db.get(Dauerbuchung, s.buchung.dauerbuchung_id)
        rest = storno_dienst.freie_absagen_rest(db, dauer) if dauer is not None else None
    if s.durch == "betreiber":
        kostenpflichtiger_grund = "Der Betreiber hat den Termin kostenpflichtig storniert."
    elif s.durch == "system":
        kostenpflichtiger_grund = "Der Termin wurde kostenpflichtig storniert."
    elif rest == 0:
        kostenpflichtiger_grund = "Es stehen keine kostenfreien Absagen im Abo zur Verfügung."
    else:
        kostenpflichtiger_grund = "Die Absagefrist war bereits abgelaufen."
    mail.sende(
        s.buchung.kunde.email,
        "Stornierung Ihrer Buchung",
        _text(
            "storno", s=s, b=s.buchung, rest=rest, kostenpflichtiger_grund=kostenpflichtiger_grund
        ),
    )


def zahlungsfrist_abgelaufen(db: Session, b: Buchung) -> None:
    try:
        mail.sende(b.kunde.email, "Reservierung verfallen", _text("zahlungsfrist_abgelaufen", b=b))
    finally:
        rueckabwicklung_hinweise(db, [b.id])


def dauerbuchung_angelegt(db: Session, d: Dauerbuchung) -> None:
    vorlauf = konfiguration.hole(db, "zutritt_vorlauf_minuten")
    mail.sende(
        d.kunde.email,
        "Ihre Dauerbuchung",
        _text(
            "dauerbuchung",
            d=d,
            pin=pin.entschluessele(d.pin_verschluesselt),
            vorlauf=vorlauf,
        ),
    )


def mitgliedschaft_freigeschaltet(db: Session, k: Kunde) -> None:
    mail.sende(
        k.email,
        "Ihre Mitgliedschaft ist freigeschaltet",
        _text("mitgliedschaft_freigeschaltet", k=k),
    )


def mitgliedschaft_beendet(db: Session, k: Kunde) -> None:
    mail.sende(k.email, "Ihre Mitgliedschaft wurde beendet", _text("mitgliedschaft_beendet", k=k))


def mitgliedschaft_erinnerung(db: Session, k: Kunde) -> None:
    mail.sende(
        k.email, "Ihre Mitgliedschaft läuft bald ab", _text("mitgliedschaft_erinnerung", k=k)
    )


def abgleich_faellig(anzahl: int) -> None:
    betreiber_alarm(
        "Jahresabgleich der Mitglieder",
        "Der Stichtag für den jährlichen Abgleich der Mitglieder ist erreicht. Auf der Prüfliste "
        f"stehen {anzahl} Kunden.\n\nBitte in der Verwaltung unter Kunden → Mitglieder-Abgleich "
        "gegen die Mitgliederliste des Vereins prüfen und verlängern oder beenden.",
    )


def rechnung(db: Session, r: Rechnung) -> None:
    from beachhub_core.services import rechnung_pdf, rechnungen  # Zyklus vermeiden

    if not r.pdf_pfad:
        rechnung_pdf.erzeuge(db, r)
    anhang = [(f"{r.nummer}.pdf", Path(r.pdf_pfad).read_bytes())] if r.pdf_pfad else []
    gutschrift = db.scalar(
        select(func.coalesce(func.sum(GuthabenBuchung.betrag), 0)).where(
            GuthabenBuchung.bezug_id == r.id,
            GuthabenBuchung.art == "storno_gutschrift",
            GuthabenBuchung.kunde_id == r.kunde_id,
        )
    )
    kaufbeleg = r.gutschein_rolle == "kauf" and r.nummer.startswith("GB-")
    betreff = (
        f"Stornorechnung {r.nummer}"
        if r.art == "storno"
        else f"Kaufbeleg {r.nummer}"
        if kaufbeleg
        else f"Rechnung {r.nummer}"
    )
    mail.sende(
        r.kunde.email,
        betreff,
        _text(
            "rechnung",
            r=r,
            offen=rechnungen.offener_betrag(db, r),
            verrechnet=rechnungen.verrechnet(db, r),
            gutschrift=Decimal(str(gutschrift)),
            kaufbeleg=kaufbeleg,
        ),
        anhaenge=anhang,
    )


def belege_versenden(db: Session, rechnung_ids: Iterable[uuid.UUID | None]) -> None:
    """Nach dem Commit PDFs festschreiben und versenden; Fehler je Beleg isolieren."""
    from beachhub_core.services import rechnung_pdf

    for rid in dict.fromkeys(i for i in rechnung_ids if i is not None):
        nummer = str(rid)
        phase = "Rechnungs-PDF nicht erzeugt"
        try:
            r = db.get(Rechnung, rid)
            if r is None:
                continue
            nummer = r.nummer
            if not r.pdf_pfad:
                rechnung_pdf.erzeuge(db, r)
                db.commit()
            phase = "Rechnung nicht versandt"
            rechnung(db, r)
        except Exception:
            logger.exception("Belegversand für Rechnung %s fehlgeschlagen", nummer)
            db.rollback()
            try:
                betreiber_alarm(
                    phase,
                    f"Die Rechnung {nummer} konnte nicht per Mail versandt werden. "
                    "Details im Log des Hauptsystems.",
                )
            except Exception:
                logger.exception("Betreiber-Alarm für Rechnung %s fehlgeschlagen", nummer)
                db.rollback()


def gutschein_gekauft(db: Session, gutschein_id: uuid.UUID) -> None:
    """Nur abgeschlossene Käufe versenden; Geschenkempfänger erhalten kein Käufer-PDF."""
    from beachhub_core.services.gutschein_kauf import gruppenhinweis

    g = db.get(Gutschein, gutschein_id)
    if g is None or g.status in (Gutschein.OFFEN, Gutschein.ABGEBROCHEN) or g.rechnung_id is None:
        return
    kaeufer = db.get(Kunde, g.kaeufer_kunde_id) if g.kaeufer_kunde_id else None
    if kaeufer is None:
        return
    empfaenger = [(kaeufer.email, "gutschein_gekauft", "Ihr Gutschein")]
    if g.empfaenger_email and g.empfaenger_email != kaeufer.email:
        empfaenger.append((g.empfaenger_email, "gutschein_geschenkt", "Ein Gutschein für Sie"))
    for email, vorlage, betreff in empfaenger:
        try:
            mail.sende(
                email,
                betreff,
                _text(
                    vorlage,
                    g=g,
                    kaeufer=kaeufer,
                    code=gutschein_codes.anzeige(g.code),
                    gruppenhinweis=gruppenhinweis(db, g),
                ),
            )
        except Exception:
            logger.exception("Gutscheinmail %s nicht versandt", g.id)
            db.rollback()
            try:
                betreiber_alarm(
                    "Gutscheinmail nicht versandt",
                    f"Code-Mail zum Kauf {gutschein_id} fehlgeschlagen. Details im Log.",
                )
            except Exception:
                logger.exception(
                    "Betreiberhinweis zum Gutscheinkauf %s fehlgeschlagen", gutschein_id
                )
                db.rollback()


def stornos_versenden(
    db: Session,
    storno_ids: Iterable[uuid.UUID],
    *,
    belege: Iterable[uuid.UUID | None] = (),
) -> None:
    """Nach dem Commit: Stornobestätigung je Kunde (A-MAIL-2), danach die Korrekturbelege.
    Mehrere Stornos eines Kunden aus einem Vorgang (Sperre, Daueranlage, Abo-Ende) kommen in
    einer Mail; Fehler werden je Kunde isoliert. Gutschein-Rückabwicklungen erhalten wie bei
    `storno_nachlauf` ihren Hinweis. `belege` ergänzt weitere Korrekturbelege."""
    je_kunde: dict[uuid.UUID, list[uuid.UUID]] = {}
    buchung_ids: list[uuid.UUID] = []
    for sid in dict.fromkeys(storno_ids):
        s = db.get(Storno, sid)
        if s is not None:
            je_kunde.setdefault(s.buchung.kunde_id, []).append(s.id)
            buchung_ids.append(s.buchung_id)
    beleg_ids: list[uuid.UUID | None] = [*belege, *belege_der_buchungen(db, buchung_ids)]
    for kunde_id, ids in je_kunde.items():
        try:
            stornos = sorted(
                (s for i in ids if (s := db.get(Storno, i)) is not None),
                key=lambda s: s.buchung.beginn,
            )
            if len(stornos) == 1:
                storno(db, stornos[0])
            else:
                kunde = stornos[0].buchung.kunde
                mail.sende(
                    kunde.email,
                    "Stornierung Ihrer Buchungen",
                    _text("stornos", kunde=kunde, stornos=stornos),
                )
        except Exception:
            logger.exception("Stornobestätigung an Kunde %s fehlgeschlagen", kunde_id)
            db.rollback()
            try:
                betreiber_alarm(
                    "Stornobestätigung nicht versandt",
                    f"Die Stornobestätigung an Kunde {kunde_id} konnte nicht versandt werden. "
                    "Details im Log des Hauptsystems.",
                )
            except Exception:
                logger.exception("Betreiber-Alarm für Kunde %s fehlgeschlagen", kunde_id)
    rueckabwicklung_hinweise(db, buchung_ids)
    belege_versenden(db, beleg_ids)


def betreiber_alarm(betreff: str, text: str) -> None:
    mail.sende(settings.email_from, f"[Beachhub] {betreff}", text)


def mitgliedsantrag(db: Session, k: Kunde) -> None:
    betreiber_alarm(
        "Antrag auf Vereinsmitgliedschaft",
        f"{k.name} <{k.email}> beantragt im Portal die Freischaltung als Mitglied.\n"
        f"Angaben: {k.mitglied_antrag_hinweis}\n\n"
        "Bitte gegen die Mitgliederliste prüfen und in der Verwaltung unter "
        "Kunden → Mitgliedsanträge freischalten oder verwerfen.",
    )


def guthabenliste(anzahl: int, summe: Decimal) -> None:
    betreiber_alarm(
        "Guthabenliste zum Saisonende",
        f"Die Saison ist zu Ende. {anzahl} Kunden haben zusammen {euro(summe)} Guthaben.\n\n"
        "Die Liste steht in der Verwaltung unter Kunden → Guthabenliste:\n"
        f"{settings.base_url.rstrip('/')}/admin/kunden/guthabenliste\n\n"
        "Wer es wünscht, bekommt "
        "sein Guthaben ausgezahlt; überweisen Sie selbst und haken Sie die Auszahlung dort ab. "
        "Alles andere wird mit der nächsten Saisonrechnung oder Buchung verrechnet.",
    )


def belege_der_buchungen(db: Session, buchung_ids: Iterable[uuid.UUID]) -> list[uuid.UUID]:
    ids = list(buchung_ids)
    geld = db.scalars(select(Storno.korrektur_rechnung_id).where(Storno.buchung_id.in_(ids)))
    gutschein = db.scalars(
        select(GutscheinEinloesung.korrektur_rechnung_id).where(
            GutscheinEinloesung.buchung_id.in_(ids)
        )
    )
    return list(dict.fromkeys(rid for rid in [*geld, *gutschein] if rid is not None))


def gutschein_rueckabwicklung(db: Session, b: Buchung) -> None:
    """Einlöserhinweis ohne PDF; Gültigkeit wird zum Versand frisch geprüft."""
    es = list(
        db.scalars(
            select(GutscheinEinloesung).where(
                GutscheinEinloesung.buchung_id == b.id,
                GutscheinEinloesung.status == GutscheinEinloesung.RUECKGAENGIG,
            )
        )
    )
    if not es:
        return
    zurueck = []
    for e in es:
        if e.rueckabwicklung_art != "gutschein":
            continue
        g = db.get(Gutschein, e.gutschein_id)
        assert g is not None
        nutzbar = (
            g.status in (Gutschein.AKTIV, Gutschein.TEILWEISE_EINGELOEST)
            and g.gueltig_bis is not None
            and g.gueltig_bis >= clock.today(db)
            and g.zurueckgegeben_am is None
        )
        zurueck.append((gutschein_codes.anzeige(g.code), g.gueltig_bis, nutzbar))
    ids = belege_der_buchungen(db, [b.id])
    betrag = Decimal(
        str(
            db.scalar(
                select(func.coalesce(func.sum(GuthabenBuchung.betrag), 0)).where(
                    (
                        GuthabenBuchung.bezug_id.in_(ids)
                        & (GuthabenBuchung.art == "storno_gutschrift")
                    )
                    | (
                        (GuthabenBuchung.bezug_id == b.id) & (GuthabenBuchung.art == "rueckbuchung")
                    ),
                    GuthabenBuchung.kunde_id == b.kunde_id,
                )
            )
        )
    )
    mail.sende(
        b.kunde.email,
        "Gutschein-Rückabwicklung",
        _text(
            "gutschein_wieder_gueltig",
            b=b,
            zurueck=zurueck,
            guthaben_betrag=betrag,
        ),
    )


def rueckabwicklung_hinweise(db: Session, buchung_ids: Iterable[uuid.UUID]) -> None:
    for bid in dict.fromkeys(buchung_ids):
        try:
            b = db.get(Buchung, bid)
            if b is not None:
                gutschein_rueckabwicklung(db, b)
        except Exception:
            logger.exception("Gutschein-Rückabwicklungshinweis %s nicht versandt", bid)
            db.rollback()


def storno_nachlauf(db: Session, s: Storno) -> None:
    bid = s.buchung_id
    ids = belege_der_buchungen(db, [bid])
    try:
        storno(db, s)
    except Exception:
        logger.exception("Stornobestätigung %s nicht versandt", s.id)
        db.rollback()
    rueckabwicklung_hinweise(db, [bid])
    belege_versenden(db, ids)
