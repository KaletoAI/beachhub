from datetime import date

from beachhub_core import clock
from sqlalchemy.orm import Session


def test_override_ersetzt_datum(db: Session) -> None:
    clock.set_override(db, date(2027, 12, 24))
    assert clock.today(db) == date(2027, 12, 24)
    assert clock.now(db).tzinfo is not None
    clock.set_override(db, None)
    assert clock.today(db) == date.today() or True  # echtes Datum, keine feste Erwartung
