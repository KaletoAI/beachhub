from pathlib import Path

from sqlalchemy.orm import Session

from beachhub_core import mail
from beachhub_core.config import settings
from beachhub_core.models import Buchung, Dauerbuchung, Kunde, Rechnung, Storno
from beachhub_core.services import konfiguration, pin
from beachhub_core.templating import templates


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
            pin=pin.entschluessele(b.pin_verschluesselt or ""),
            vorlauf=vorlauf,
        ),
    )


def storno(db: Session, s: Storno) -> None:
    mail.sende(
        s.buchung.kunde.email, "Stornierung Ihrer Buchung", _text("storno", s=s, b=s.buchung)
    )


def zahlungsfrist_abgelaufen(db: Session, b: Buchung) -> None:
    mail.sende(b.kunde.email, "Reservierung verfallen", _text("zahlungsfrist_abgelaufen", b=b))


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
    if not r.pdf_pfad:
        from beachhub_core.services import rechnung_pdf  # Zyklus vermeiden

        rechnung_pdf.erzeuge(db, r)
    anhang = [(f"{r.nummer}.pdf", Path(r.pdf_pfad).read_bytes())] if r.pdf_pfad else []
    mail.sende(r.kunde.email, f"Rechnung {r.nummer}", _text("rechnung", r=r), anhaenge=anhang)


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
