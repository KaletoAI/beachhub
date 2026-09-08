"""Eine Uhr für die Fachlogik. Admin kann das Datum überschreiben (Tests, Abnahme)."""

from datetime import UTC, date, datetime

from beachhub_shared.zeit import BERLIN, lokales_datum
from sqlalchemy.orm import Session

from beachhub_core.models import AppSetting

OVERRIDE_KEY = "datum_override"


def override(db: Session) -> date | None:
    zeile = db.get(AppSetting, OVERRIDE_KEY)
    return date.fromisoformat(zeile.value) if zeile and zeile.value else None


def now(db: Session) -> datetime:
    echt = datetime.now(UTC)
    o = override(db)
    if o is None:
        return echt
    lok = echt.astimezone(BERLIN)
    return datetime.combine(o, lok.time(), tzinfo=BERLIN).astimezone(UTC)


def today(db: Session) -> date:
    return lokales_datum(now(db))


def set_override(db: Session, datum: date | None) -> None:
    zeile = db.get(AppSetting, OVERRIDE_KEY)
    if datum is None:
        if zeile:
            db.delete(zeile)
    elif zeile:
        zeile.value = datum.isoformat()
    else:
        db.add(AppSetting(key=OVERRIDE_KEY, value=datum.isoformat()))
    db.commit()
