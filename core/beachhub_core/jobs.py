import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from beachhub_core import clock
from beachhub_core.database import SessionLocal
from beachhub_core.models import Kunde
from beachhub_core.services import (
    benachrichtigung,
    halle,
    lesestand,
    mitgliedschaft,
    online_buchung,
)

logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


def verfall_ausfuehren(db: Session) -> int:
    """Reservierungen mit abgelaufener Zahlungsfrist verfallen lassen (A-ZAHL-3)."""
    verfallen = online_buchung.verfalle_abgelaufene(db)
    db.commit()
    for b in verfallen:
        benachrichtigung.zahlungsfrist_abgelaufen(db, b)
    return len(verfallen)


def _job_lesestand() -> None:
    with SessionLocal() as db:
        try:
            lesestand.verarbeite_geaenderte(db)
        except Exception:
            logger.exception("Lesestand-Aktualisierung fehlgeschlagen")


def mitgliedschaft_ausfuehren(db: Session) -> None:
    """Jahresabgleich melden und an den Ablauf der Mitgliedschaft erinnern (A-KUND-5, A-MAIL-2).
    Erst committen, dann Mails – ein Rollback darf keine Mail zurücklassen."""
    lauf = mitgliedschaft.tageslauf(db)
    db.commit()
    if lauf.abgleich is not None:
        benachrichtigung.abgleich_faellig(lauf.abgleich)
    for kunde_id in lauf.erinnert:
        k = db.get(Kunde, kunde_id)
        if k is not None:
            benachrichtigung.mitgliedschaft_erinnerung(db, k)


def _job_mitgliedschaft() -> None:
    with SessionLocal() as db:
        try:
            mitgliedschaft_ausfuehren(db)
        except Exception:
            logger.exception("Tageslauf der Mitgliedschaft fehlgeschlagen")


def _job_verfall() -> None:
    with SessionLocal() as db:
        try:
            verfall_ausfuehren(db)
        except Exception:
            logger.exception("Verfall der Reservierungen fehlgeschlagen")


def hallenplan_nachts(db: Session) -> None:
    """Das 7-Tage-Fenster wandert jede Nacht einen Tag weiter. Ohne neue Version fehlte der
    Halle nach und nach der letzte Tag, auch wenn sich keine Buchung ändert."""
    lesestand.markiere_geaendert(db, "hallenplan")
    db.commit()
    lesestand.verarbeite_geaenderte(db)


def _job_hallenplan() -> None:
    with SessionLocal() as db:
        try:
            hallenplan_nachts(db)
        except Exception:
            logger.exception("Nächtlicher Hallenplan fehlgeschlagen")


def _job_halle_kontakt() -> None:
    with SessionLocal() as db:
        try:
            halle.pruefe_kontakt(db, clock.now(db))
        except Exception:
            logger.exception("Prüfung des Hallenkontakts fehlgeschlagen")


def starte_scheduler() -> BackgroundScheduler:
    global _scheduler
    s = BackgroundScheduler(timezone="Europe/Berlin")
    s.add_job(_job_lesestand, IntervalTrigger(minutes=5), id="lesestand", replace_existing=True)
    s.add_job(_job_verfall, IntervalTrigger(minutes=1), id="verfall", replace_existing=True)
    s.add_job(
        _job_mitgliedschaft,
        CronTrigger(hour=7, minute=0),
        id="mitgliedschaft",
        replace_existing=True,
    )
    s.add_job(
        _job_halle_kontakt, IntervalTrigger(minutes=5), id="halle_kontakt", replace_existing=True
    )
    s.add_job(
        _job_hallenplan,
        CronTrigger(hour=0, minute=5),
        id="hallenplan_nachts",
        replace_existing=True,
    )
    s.start()
    _scheduler = s
    return s


def stoppe_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
