import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from beachhub_core import clock
from beachhub_core.database import SessionLocal
from beachhub_core.models import AppSetting
from beachhub_core.services import benachrichtigung, konfiguration, rechnung_pdf, rechnungen

logger = logging.getLogger(__name__)
MARKER = "monatslauf_letzter"
_scheduler: BackgroundScheduler | None = None


def monatslauf_faellig(db: Session) -> tuple[int, int] | None:
    heute = clock.today(db)
    if heute.day < konfiguration.hole(db, "rechnung_tag_im_folgemonat"):
        return None
    jahr, monat = (heute.year, heute.month - 1) if heute.month > 1 else (heute.year - 1, 12)
    marker = db.get(AppSetting, MARKER)
    if marker and marker.value == f"{jahr}-{monat:02d}":
        return None
    return jahr, monat


def monatslauf_ausfuehren(db: Session) -> int:
    faellig = monatslauf_faellig(db)
    if faellig is None:
        return 0
    jahr, monat = faellig
    erzeugt = rechnungen.monatslauf(db, jahr, monat)
    for r in erzeugt:
        rechnung_pdf.erzeuge(db, r)
    marker = db.get(AppSetting, MARKER)
    if marker:
        marker.value = f"{jahr}-{monat:02d}"
    else:
        db.add(AppSetting(key=MARKER, value=f"{jahr}-{monat:02d}"))
    db.commit()
    for r in erzeugt:
        benachrichtigung.rechnung(db, r)
    logger.info("Monatslauf %s-%02d: %d Rechnungen", jahr, monat, len(erzeugt))
    return len(erzeugt)


def _job_monatslauf() -> None:
    with SessionLocal() as db:
        try:
            monatslauf_ausfuehren(db)
        except Exception:
            logger.exception("Monatslauf fehlgeschlagen")
            benachrichtigung.betreiber_alarm(
                "Monatslauf fehlgeschlagen", "Details im Log des Hauptsystems."
            )


def _job_lesestand() -> None:
    from beachhub_core.services import lesestand  # Task 19

    with SessionLocal() as db:
        try:
            lesestand.verarbeite_geaenderte(db)
        except Exception:
            logger.exception("Lesestand-Aktualisierung fehlgeschlagen")


def starte_scheduler() -> BackgroundScheduler:
    global _scheduler
    s = BackgroundScheduler(timezone="Europe/Berlin")
    s.add_job(
        _job_monatslauf, CronTrigger(hour=6, minute=0), id="monatslauf", replace_existing=True
    )
    s.add_job(_job_lesestand, IntervalTrigger(minutes=5), id="lesestand", replace_existing=True)
    s.start()
    _scheduler = s
    return s


def stoppe_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
