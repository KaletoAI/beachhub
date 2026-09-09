from datetime import date

from beachhub_core import clock, jobs
from beachhub_core.models import AppSetting
from sqlalchemy.orm import Session


def test_monatslauf_faellig_nur_einmal_pro_monat(db: Session) -> None:
    clock.set_override(db, date(2028, 1, 2))
    assert jobs.monatslauf_faellig(db) is None  # Tag 2 < 3
    clock.set_override(db, date(2028, 1, 3))
    assert jobs.monatslauf_faellig(db) == (2027, 12)
    assert (
        jobs.monatslauf_ausfuehren(db) == 0
    )  # keine Rechnungskunden → 0 Rechnungen, Marker trotzdem
    db.commit()
    assert db.get(AppSetting, "monatslauf_letzter").value == "2027-12"
    assert jobs.monatslauf_faellig(db) is None
    clock.set_override(db, date(2028, 2, 5))
    assert jobs.monatslauf_faellig(db) == (2028, 1)
