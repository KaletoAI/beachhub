"""Aufräumen im Portal, alle 5 Minuten: abgelaufene Einmal-Links samt Dateien, verwaiste
PDF-Dateien, Login-Codes und Sessions, alte beantwortete Anfragen und Briefkasteneinträge,
Lesestände von Konten, die es im Portal nicht mehr gibt.

Läuft nur in einem Prozess/Worker (Task 17: `--workers 1`) – zwei parallele Läufe wären
harmlos (jede Löschung ist idempotent), würden aber unnötig konkurrieren."""

import logging
from datetime import datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from beachhub_portal import uhr
from beachhub_portal.config import settings
from beachhub_portal.database import SessionLocal
from beachhub_portal.models import (
    Anfrage,
    CodeFehlversuch,
    Konto,
    Lesestand,
    LoginToken,
    RechnungLink,
    Sitzung,
    WebhookEingang,
)

logger = logging.getLogger(__name__)
AUFBEWAHRUNG = timedelta(days=30)
# Ruling (Task 10, umgesetzt in Task 15): Fehlversuche beim Code-Login werden dauerhaft in der
# DB gezählt (Sperre über 24 h, unabhängig von einzelnen Login-Tokens) und deshalb erst hier,
# nicht beim Löschen eines einzelnen Kontos, wieder entfernt.
CODE_FEHLVERSUCH_AUFBEWAHRUNG = timedelta(hours=24)
_scheduler: BackgroundScheduler | None = None


def aufraeumen(db: Session, jetzt: datetime) -> dict[str, int]:
    n: dict[str, int] = {}
    abgelaufen = db.scalars(select(RechnungLink).where(RechnungLink.laeuft_ab <= jetzt)).all()
    for link in abgelaufen:
        Path(link.pdf_pfad).unlink(missing_ok=True)
        db.delete(link)
    n["rechnung_links"] = len(abgelaufen)

    # Dateien ohne Link, etwa nach einem Absturz zwischen Schreiben und Commit.
    bekannt = set(db.scalars(select(RechnungLink.pdf_pfad).where(RechnungLink.laeuft_ab > jetzt)))
    ordner = settings.data_dir / "rechnungen_tmp"
    grenze = (jetzt - timedelta(hours=1)).timestamp()
    waisen = 0
    if ordner.exists():
        for pfad in ordner.glob("*.pdf"):
            if str(pfad) not in bekannt and pfad.stat().st_mtime < grenze:
                pfad.unlink(missing_ok=True)
                waisen += 1
    n["waisen"] = waisen

    n["code_fehlversuch"] = db.execute(
        delete(CodeFehlversuch).where(
            CodeFehlversuch.versucht_am <= jetzt - CODE_FEHLVERSUCH_AUFBEWAHRUNG
        )
    ).rowcount
    n["login_token"] = db.execute(delete(LoginToken).where(LoginToken.laeuft_ab <= jetzt)).rowcount
    n["sitzungen"] = db.execute(delete(Sitzung).where(Sitzung.laeuft_ab <= jetzt)).rowcount
    n["anfragen"] = db.execute(
        delete(Anfrage).where(
            Anfrage.status == Anfrage.BEANTWORTET, Anfrage.beantwortet_am < jetzt - AUFBEWAHRUNG
        )
    ).rowcount
    n["webhooks"] = db.execute(
        delete(WebhookEingang).where(WebhookEingang.empfangen_am < jetzt - AUFBEWAHRUNG)
    ).rowcount

    # konto:-Dokumente können vor dem Konto ankommen; erst nach einem Tag gelten sie als verwaist.
    kunden = {
        f"konto:{k}" for k in db.scalars(select(Konto.kunde_id).where(Konto.kunde_id.is_not(None)))
    }
    verwaist = [
        z
        for z in db.scalars(
            select(Lesestand).where(
                Lesestand.dokument.like("konto:%"),
                Lesestand.empfangen_am < jetzt - timedelta(days=1),
            )
        )
        if z.dokument not in kunden
    ]
    for z in verwaist:
        db.delete(z)
    n["lesestand"] = len(verwaist)
    db.commit()
    return n


def _job_aufraeumen() -> None:
    with SessionLocal() as db:
        try:
            aufraeumen(db, uhr.jetzt())
        except Exception:
            logger.exception("Aufräumen fehlgeschlagen")


def starte_scheduler() -> BackgroundScheduler:
    global _scheduler
    s = BackgroundScheduler(timezone="Europe/Berlin")
    s.add_job(_job_aufraeumen, IntervalTrigger(minutes=5), id="aufraeumen", replace_existing=True)
    s.start()
    _scheduler = s
    return s


def stoppe_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
