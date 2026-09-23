"""Die eine Uhr des Portals. Alle Module rufen `uhr.jetzt()` über das Modul auf, damit Tests
die Zeit mit `monkeypatch.setattr(uhr, "jetzt", ...)` anhalten können."""

from datetime import UTC, datetime


def jetzt() -> datetime:
    return datetime.now(UTC)
