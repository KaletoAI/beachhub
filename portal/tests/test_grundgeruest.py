import os

import hilfen
from alembic import command
from alembic.config import Config
from beachhub_portal.config import Settings
from beachhub_portal.database import engine
from beachhub_portal.models import Base, Lesestand
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlalchemy.orm import Session


def test_health_mit_sicherheitskoepfen(client: TestClient) -> None:
    r = client.get("/health")
    assert r.json() == {"status": "ok"}
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"


def test_favicon(client: TestClient) -> None:
    assert client.get("/favicon.ico").status_code == 200


def test_fehlerseite_fuer_menschen(client: TestClient) -> None:
    r = client.get("/gibtsnicht")
    assert r.status_code == 404
    assert "Seite nicht gefunden" in r.text


def test_produktionsfehler() -> None:
    schlecht = Settings(
        app_env="production",
        secret_key="change-me",
        fake_zahlung=True,
        kanal_token="",
        core_public_key="",
        cookie_secure=False,
    )
    assert len(schlecht.produktionsfehler) == 5
    gut = Settings(
        app_env="production",
        secret_key="x" * 32,
        fake_zahlung=False,
        kanal_token="t",
        core_public_key="ab",
        cookie_secure=True,
    )
    assert gut.produktionsfehler == []


def test_migration_erzeugt_alle_tabellen() -> None:
    Base.metadata.drop_all(bind=engine)
    hier = os.path.dirname(__file__)
    cfg = Config(os.path.join(hier, "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(hier, "..", "alembic"))
    command.upgrade(cfg, "head")
    tabellen = set(inspect(engine).get_table_names(schema="spiegel"))
    assert {t.name for t in Base.metadata.tables.values()} <= tabellen
    command.downgrade(cfg, "base")


def test_lesestand_version_ist_bigint_ueber_2_hoch_31(db: Session) -> None:
    """Ruling: lesestand.version ist BigInteger, weil das Hauptsystem Versionen als
    Unixzeit in Millisekunden vergibt – das übersteigt den 32-Bit-Bereich (2**31)."""
    grosse_version = 2**31 + 1_732_000_000_000
    hilfen.speichere(db, "belegung", hilfen.belegung(), version=grosse_version)

    db.expire_all()
    zeile = db.get(Lesestand, "belegung")

    assert zeile is not None
    assert zeile.version == grosse_version
