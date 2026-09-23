from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from beachhub_hall.clock import SimulierteUhr
from beachhub_hall.db import oeffne
from beachhub_hall.dienst import Dienst
from sqlalchemy.orm import Session, sessionmaker

from tests.core_simulator import CoreSimulator
from tests.dienst_hilfen import baue_dienst
from tests.ha_simulator import HaSimulator, standard_entitaeten
from tests.hilfen import t


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
