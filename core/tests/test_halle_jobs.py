from datetime import timedelta
from pathlib import Path

from beachhub_core import clock, jobs
from beachhub_core.config import settings
from beachhub_core.models import AppSetting, HallenStatusZeile, LesestandVersion
from beachhub_core.services import halle, lesestand
from sqlalchemy.orm import Session


def test_kontakt_alarm_einmal_und_nur_nach_erstkontakt(db: Session, mail_ausgang: list) -> None:
    jetzt = clock.now(db)
    assert halle.pruefe_kontakt(db, jetzt) is False  # Halle noch nie gemeldet
    db.add(HallenStatusZeile(id=1, daten_json=None, empfangen_am=jetzt - timedelta(minutes=59)))
    db.commit()
    assert halle.pruefe_kontakt(db, jetzt) is False
    assert halle.pruefe_kontakt(db, jetzt + timedelta(minutes=2)) is True
    assert [m["betreff"] for m in mail_ausgang] == ["[Beachhub] Halle ohne Kontakt"]
    assert halle.pruefe_kontakt(db, jetzt + timedelta(minutes=30)) is False
    assert len(mail_ausgang) == 1
    assert db.get(AppSetting, halle.KONTAKT_MARKER) is not None


def test_plan_wird_nachts_neu_signiert(db: Session) -> None:
    Path(settings.signatur_privatschluessel_pfad).unlink(missing_ok=True)
    lesestand.erzeuge_schluessel()
    # Version prüfen als "> vorher" statt "== 2": lesestand.publiziere vergibt seit dem
    # Portal-Merge max(bisherige Version + 1, Unixzeit in Millisekunden) (Ruling Task 5), eine
    # feste Version 2 hält also nicht mehr.
    vorher = lesestand.publiziere(db, "hallenplan")
    db.commit()
    jobs.hallenplan_nachts(db)
    zeile = db.get(LesestandVersion, "hallenplan")
    db.refresh(zeile)
    assert zeile.version > vorher.version and zeile.geaendert is False


def test_scheduler_kennt_die_hallenjobs() -> None:
    s = jobs.starte_scheduler()
    try:
        assert s.get_job("halle_kontakt") is not None
        assert s.get_job("hallenplan_nachts") is not None
    finally:
        jobs.stoppe_scheduler()
