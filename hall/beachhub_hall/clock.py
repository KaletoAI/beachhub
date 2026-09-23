"""Injizierbare Uhr. Die Fachlogik fragt nie `datetime.now()`, sondern immer eine Uhr –
so lassen sich Hallentage und 72 h Offline-Betrieb in Tests in Sekunden durchspielen."""

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol


class Uhr(Protocol):
    def jetzt(self) -> datetime: ...


class EchteUhr:
    def jetzt(self) -> datetime:
        return datetime.now(UTC)


class SimulierteUhr:
    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("Startzeit braucht eine Zeitzone")
        self._t = start

    def jetzt(self) -> datetime:
        return self._t

    def stelle(self, t: datetime) -> None:
        self._t = t

    def vor(self, **dauer: Any) -> None:
        self._t += timedelta(**dauer)
