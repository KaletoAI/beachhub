"""Alembic-Migrationen müssen das gleiche Schema erzeugen wie die Modelle."""

import os

from alembic import command
from alembic.config import Config
from beachhub_core.database import engine
from beachhub_core.models import Base
from sqlalchemy import inspect


def test_upgrade_head_erzeugt_alle_tabellen() -> None:
    Base.metadata.drop_all(bind=engine)
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    command.upgrade(cfg, "head")
    tabellen = set(inspect(engine).get_table_names())
    erwartet = set(Base.metadata.tables) | {"alembic_version"}
    assert erwartet <= tabellen
    command.downgrade(cfg, "base")
