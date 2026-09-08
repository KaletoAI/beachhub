from pathlib import Path

from sqlalchemy.orm import Session

from beachhub_core import mail
from beachhub_core.config import settings
from beachhub_core.models import Buchung, Dauerbuchung, Rechnung, Storno
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


def rechnung(db: Session, r: Rechnung) -> None:
    if not r.pdf_pfad:
        from beachhub_core.services import rechnung_pdf  # Zyklus vermeiden

        rechnung_pdf.erzeuge(db, r)
    anhang = [(f"{r.nummer}.pdf", Path(r.pdf_pfad).read_bytes())] if r.pdf_pfad else []
    mail.sende(r.kunde.email, f"Rechnung {r.nummer}", _text("rechnung", r=r), anhaenge=anhang)


def betreiber_alarm(betreff: str, text: str) -> None:
    mail.sende(settings.email_from, f"[Beachhub] {betreff}", text)
