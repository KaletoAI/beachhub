from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from beachhub_hall.clock import SimulierteUhr
from beachhub_hall.db import oeffne
from sqlalchemy.orm import Session, sessionmaker

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
