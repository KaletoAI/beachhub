from datetime import date

from beachhub_core import clock, jobs
from beachhub_core.services import kunden
from sqlalchemy.orm import Session


def test_mitgliedschaft_tageslauf_schickt_mails_nach_commit(
    db: Session, mail_ausgang: list
) -> None:
    k = kunden.lege_an(db, name="Anna", email="anna@x.de")
    k.mitglied_bis = date(2028, 4, 30)
    db.commit()
    clock.set_override(db, date(2028, 4, 20))
    jobs.mitgliedschaft_ausfuehren(db)
    assert [m["betreff"] for m in mail_ausgang] == ["Ihre Mitgliedschaft läuft bald ab"]
    db.refresh(k)
    assert k.mitglied_erinnert_fuer == date(2028, 4, 30)


def test_mitgliedschaft_tageslauf_meldet_abgleich(db: Session, mail_ausgang: list) -> None:
    clock.set_override(db, date(2027, 8, 31))
    jobs.mitgliedschaft_ausfuehren(db)
    assert [m["betreff"] for m in mail_ausgang] == ["[Beachhub] Jahresabgleich der Mitglieder"]


def test_gutschein_verfall_job_committet(db: Session) -> None:
    from datetime import timedelta

    from beachhub_core.database import SessionLocal
    from beachhub_core.models import Gutschein
    from beachhub_core.services import gutscheine

    g = gutscheine.stelle_freicodes_aus(db, anzahl=1, gueltig_bis=None, admin_user_id=None)[0]
    gid = g.id
    db.commit()
    clock.set_override(db, g.gueltig_bis + timedelta(days=1))
    assert jobs.gutschein_verfall_ausfuehren(db) == 1
    with SessionLocal() as andere:
        assert andere.get(Gutschein, gid).status == "verfallen"


def test_gutschein_nachtjob_berlin_auch_bei_utc_server(monkeypatch) -> None:
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    from apscheduler.schedulers.background import BackgroundScheduler

    monkeypatch.setattr(BackgroundScheduler, "start", lambda self: None)
    scheduler = jobs.starte_scheduler()
    try:
        job = scheduler.get_job("gutschein_verfall")
        assert job is not None
        assert str(job.trigger.timezone) == "Europe/Berlin"
        for monat in (1, 7):
            jetzt = datetime(2028, monat, 10, 23, 30, tzinfo=UTC)
            naechster = job.trigger.get_next_fire_time(None, jetzt).astimezone(
                ZoneInfo("Europe/Berlin")
            )
            assert (naechster.hour, naechster.minute) == (0, 20)
    finally:
        jobs._scheduler = None
