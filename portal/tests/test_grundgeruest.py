import os
from typing import Any

import hilfen
import pytest
from alembic import command
from alembic.config import Config
from beachhub_portal.config import Settings, pruefe_produktionsstart
from beachhub_portal.database import engine
from beachhub_portal.models import Anfrage, Base, Lesestand
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
    # base_url bleibt beim unsicheren Vorgabewert (http://127.0.0.1:8001) -> ein Fehler mehr,
    # smtp_host bleibt leer (conftest) -> noch einer.
    assert len(schlecht.produktionsfehler) == 7
    assert "PORTAL_SMTP_HOST fehlt" in schlecht.produktionsfehler
    gut = Settings(
        app_env="production",
        secret_key="x" * 32,
        fake_zahlung=False,
        kanal_token="t",
        core_public_key="ab",
        cookie_secure=True,
        base_url="https://buchung.example.org",
        smtp_host="mail.example.org",
    )
    assert gut.produktionsfehler == []


def test_produktionsfehler_base_url() -> None:
    """PORTAL_BASE_URL geht ohne mTLS in den Kunden-Browser (Rückkehr nach Zahlung, Links in
    Mails) und muss deshalb öffentlich per https erreichbar sein, nicht nur lokal."""
    basis: dict[str, Any] = dict(
        app_env="production",
        secret_key="x" * 32,
        fake_zahlung=False,
        kanal_token="t",
        core_public_key="ab",
        cookie_secure=True,
        smtp_host="mail.example.org",
    )
    kein_https = Settings(**basis, base_url="http://buchung.example.org")
    assert any("https" in f for f in kein_https.produktionsfehler)

    lokal = Settings(**basis, base_url="https://127.0.0.1:8001")
    assert any("127.0.0.1" in f for f in lokal.produktionsfehler)

    lokal_name = Settings(**basis, base_url="https://localhost:8001")
    assert any("localhost" in f for f in lokal_name.produktionsfehler)

    oeffentlich = Settings(**basis, base_url="https://buchung.example.org")
    assert oeffentlich.produktionsfehler == []


def test_pruefe_produktionsstart_verweigert_bei_produktionsfehlern() -> None:
    schlecht = Settings(
        app_env="production",
        secret_key="change-me",
        fake_zahlung=True,
        kanal_token="",
        core_public_key="",
        cookie_secure=False,
    )
    with pytest.raises(RuntimeError, match="Start verweigert"):
        pruefe_produktionsstart(schlecht)

    gut = Settings(
        app_env="production",
        secret_key="x" * 32,
        fake_zahlung=False,
        kanal_token="t",
        core_public_key="ab",
        cookie_secure=True,
        base_url="https://buchung.example.org",
        smtp_host="mail.example.org",
    )
    pruefe_produktionsstart(gut)  # kein Fehler

    # Im Entwicklungsmodus wird trotz unsicherer Werte nicht verweigert.
    pruefe_produktionsstart(Settings(app_env="dev", secret_key="change-me"))


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


def test_modell_defaults_folgen_uhr_jetzt(db: Session, uhr_steht: Any) -> None:
    """Model-Defaults rufen uhr.jetzt() über das Modul auf, damit Tests die Uhr anhalten
    können (monkeypatch.setattr(uhr, "jetzt", ...), siehe conftest.uhr_steht)."""
    a = Anfrage(typ="konto_angelegt", konto_id=None, nutzlast_json={})
    db.add(a)
    db.commit()
    db.refresh(a)

    assert a.erstellt_am == uhr_steht.jetzt
