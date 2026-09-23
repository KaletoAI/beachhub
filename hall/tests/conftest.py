from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from beachhub_hall.clock import SimulierteUhr
from beachhub_hall.config import lade_zuordnung
from beachhub_hall.db import oeffne
from beachhub_hall.dienst import Dienst
from beachhub_hall.ha import HaClient
from sqlalchemy.orm import Session, sessionmaker

from tests.core_simulator import CoreSimulator
from tests.ha_simulator import HaSimulator, standard_entitaeten
from tests.hilfen import TOML_BEISPIEL, FakeSchlaf, t


@pytest.fixture
def sitzungen(tmp_path: Path) -> sessionmaker[Session]:
    return oeffne(tmp_path / "hall.sqlite")


@pytest.fixture
def uhr() -> SimulierteUhr:
    return SimulierteUhr(t(17))


@pytest.fixture
async def ha() -> AsyncIterator[HaSimulator]:
    sim = HaSimulator()
    standard_entitaeten(sim)
    await sim.start()
    yield sim
    await sim.stop()


async def baue_dienst(
    sitzungen: sessionmaker[Session],
    uhr: SimulierteUhr,
    ha: HaSimulator,
    tmp_path: Path,
    *,
    zuhoerer_backoff: float = 1.0,
) -> tuple[Dienst, CoreSimulator]:
    """Verdrahtet einen kompletten Dienst gegen simulierte Uhr, HA- und Core-Simulator –
    gemeinsame Grundlage der Fixtures `dienst` (test_dienst.py) und `aufbau`
    (test_integration.py), damit der Aufbau nicht doppelt gepflegt wird (Ruling Task 11)."""
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


@pytest.fixture
async def dienst(
    sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator, tmp_path: Path
) -> AsyncIterator[tuple[Dienst, CoreSimulator]]:
    d, core = await baue_dienst(sitzungen, uhr, ha, tmp_path, zuhoerer_backoff=0.01)
    yield d, core
    await d.schliesse()


@pytest.fixture
async def aufbau(
    sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator, tmp_path: Path
) -> AsyncIterator[tuple[Dienst, CoreSimulator]]:
    d, core = await baue_dienst(sitzungen, uhr, ha, tmp_path)
    yield d, core
    await d.schliesse()
