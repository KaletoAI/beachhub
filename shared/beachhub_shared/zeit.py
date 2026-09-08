from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")


def kombiniere(datum: date, uhrzeit: time) -> datetime:
    """Lokale Berliner Uhrzeit an einem Datum → UTC-aware datetime."""
    return datetime.combine(datum, uhrzeit, tzinfo=BERLIN).astimezone(UTC)


def lokal(dt: datetime) -> datetime:
    return dt.astimezone(BERLIN)


def lokales_datum(dt: datetime) -> date:
    return lokal(dt).date()
