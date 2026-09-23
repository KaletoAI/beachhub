"""Baut einen kompletten Dienst gegen simulierte Uhr, HA- und Core-Simulator – gemeinsame
Grundlage der Fixtures `dienst` (test_dienst.py) und `aufbau` (test_integration.py) sowie für
Tests, die einen frischen Dienst außerhalb dieser Fixtures brauchen (z. B. die Reihenfolge beim
Herunterfahren). Eigenes Modul statt `hilfen.py`: `tests.core_simulator` importiert bereits aus
`hilfen.py`, ein Import von `core_simulator` dort zurück wäre ein Zirkelbezug.
"""

from pathlib import Path

from beachhub_hall.clock import SimulierteUhr
from beachhub_hall.config import lade_zuordnung
from beachhub_hall.dienst import Dienst
from beachhub_hall.ha import HaClient
from sqlalchemy.orm import Session, sessionmaker

from tests.core_simulator import CoreSimulator
from tests.ha_simulator import HaSimulator
from tests.hilfen import TOML_BEISPIEL, FakeSchlaf


async def baue_dienst(
    sitzungen: sessionmaker[Session],
    uhr: SimulierteUhr,
    ha: HaSimulator,
    tmp_path: Path,
    *,
    zuhoerer_backoff: float = 1.0,
) -> tuple[Dienst, CoreSimulator]:
    toml = tmp_path / "hall.toml"
    toml.write_text(TOML_BEISPIEL, encoding="utf-8")
    core = CoreSimulator()
    d = Dienst(
        oeffentlich_hex=core.oeffentlich,
        zuordnung=lade_zuordnung(toml),
        sitzungen=sitzungen,
        uhr=uhr,
        ha=HaClient(ha.url, HaSimulator.TOKEN),
        core=core.client(),
        schlafen=FakeSchlaf(),
        zuhoerer_backoff=zuhoerer_backoff,
    )
    return d, core
