"""Zuletzt bekannter Zustand der HA-Entitäten, nur im Speicher.

Der HA-Zuhörer hält ihn über den WebSocket aktuell, die Steuerung ergänzt ihn bei jedem
Abgleich. Er dient nur der Anzeige im Status; geschaltet wird immer nach frischem Ist aus HA.
"""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from beachhub_hall.config import HeizungKonfig


def _dezimal(wert: object) -> Decimal | None:
    if wert is None:
        return None
    try:
        d = Decimal(str(wert))
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


@dataclass
class Lage:
    ist: dict[str, dict[str, Any]] = field(default_factory=dict)
    ha_verbunden: bool = False
    soll_heizung: Decimal | None = None

    def state(self, entity: str | None) -> str | None:
        if entity is None or entity not in self.ist:
            return None
        wert = self.ist[entity].get("state")
        return str(wert) if wert is not None else None

    def ist_an(self, entity: str | None) -> bool | None:
        s = self.state(entity)
        return None if s is None else s == "on"

    def temperatur(self, heizung: HeizungKonfig) -> Decimal | None:
        if heizung.ist_sensor:
            return _dezimal(self.state(heizung.ist_sensor))
        if heizung.entity and heizung.entity in self.ist:
            attribute = self.ist[heizung.entity].get("attributes") or {}
            return _dezimal(attribute.get("current_temperature"))
        return None
