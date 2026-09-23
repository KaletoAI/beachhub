from pathlib import Path

import pytest
from beachhub_hall.clock import SimulierteUhr
from beachhub_hall.db import oeffne
from sqlalchemy.orm import Session, sessionmaker

from tests.hilfen import t


@pytest.fixture
def sitzungen(tmp_path: Path) -> sessionmaker[Session]:
    return oeffne(tmp_path / "hall.sqlite")


@pytest.fixture
def uhr() -> SimulierteUhr:
    return SimulierteUhr(t(17))
