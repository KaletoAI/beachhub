from datetime import date, datetime

from beachhub_core import clock
from beachhub_shared.zeit import BERLIN
from sqlalchemy.orm import Session


def test_override_ersetzt_datum(db: Session) -> None:
    clock.set_override(db, date(2027, 12, 24))
    assert clock.today(db) == date(2027, 12, 24)
    assert clock.now(db).tzinfo is not None
    clock.set_override(db, None)
    assert clock.override(db) is None
    # Beide Seiten werden innerhalb derselben Sekunde ausgewertet; ein
    # Mitternachts-Race ist hier hinnehmbar.
    assert clock.today(db) == datetime.now(BERLIN).date()
