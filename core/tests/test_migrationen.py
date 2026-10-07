"""Alembic-Migrationen müssen das gleiche Schema erzeugen wie die Modelle."""

import os
from datetime import date
from decimal import Decimal

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from beachhub_core.database import engine
from beachhub_core.models import Base, Gutschein, Nummernkreis, Rechnung
from beachhub_core.services import kunden, rechnungen
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session


def test_upgrade_head_erzeugt_alle_tabellen() -> None:
    Base.metadata.drop_all(bind=engine)
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    command.upgrade(cfg, "head")
    tabellen = set(inspect(engine).get_table_names())
    erwartet = set(Base.metadata.tables) | {"alembic_version"}
    assert erwartet <= tabellen
    with engine.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
    command.downgrade(cfg, "base")


@pytest.mark.parametrize("bestand", ["beleg", "gutschein", "nummernkreis"])
def test_migration_0015_downgrade_schuetzt_gutscheinbestand(bestand: str) -> None:
    Base.metadata.drop_all(bind=engine)
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    command.upgrade(cfg, "0015")
    with Session(engine) as db:
        if bestand == "beleg":
            k = kunden.lege_an(db, name="Käufer", email="kauf@example.org")
            r = rechnungen._neue_rechnung(
                db,
                k,
                "gutschein",
                [rechnungen.Posten(None, "Kaufbeleg", Decimal("26"), Decimal("0"))],
                date(2027, 12, 1),
                date(2027, 12, 1),
                "bezahlt",
                quelle="system",
                kreis="gutschein",
                gutschein_rolle="kauf",
            )
            r.pdf_sha256 = "a" * 64
            nummer = r.nummer
        elif bestand == "gutschein":
            db.add(
                Gutschein(
                    code="FREI-TEST",
                    art="frei",
                    modell="einheit",
                    steuer="keine",
                    minuten=120,
                    status="aktiv",
                )
            )
        else:
            db.add(Nummernkreis(kreis="gutschein", jahr=2027, letzte_nummer=4))
        db.commit()
    with pytest.raises(RuntimeError, match="Downgrade 0015 verweigert"):
        command.downgrade(cfg, "0014")
    with Session(engine) as db:
        if bestand == "beleg":
            r = db.query(Rechnung).one()
            assert (r.nummer, r.brutto, r.pdf_sha256) == (nummer, Decimal("26.00"), "a" * 64)
        elif bestand == "gutschein":
            assert db.query(Gutschein).one().code == "FREI-TEST"
        else:
            assert db.query(Nummernkreis).one().letzte_nummer == 4
        assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0015"


def test_migration_0015_erhaelt_nummernkreis() -> None:
    Base.metadata.drop_all(bind=engine)
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    command.upgrade(cfg, "0014")
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO nummernkreis (jahr, letzte_nummer) VALUES (2027, 41), (2026, 7)")
        )
    command.upgrade(cfg, "0015")
    with Session(engine) as db:
        assert rechnungen.naechste_nummer(db, 2027) == "2027-00042"
        assert rechnungen.naechste_nummer(db, 2026) == "2026-00008"
        assert rechnungen.naechste_nummer(db, 2027, "gutschein") == "GB-2027-00001"
        db.rollback()
    command.downgrade(cfg, "0014")
    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT jahr, letzte_nummer FROM nummernkreis ORDER BY jahr")
        ).all() == [(2026, 7), (2027, 41)]
    command.downgrade(cfg, "base")
