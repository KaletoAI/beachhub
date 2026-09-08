"""Tests laufen gegen eine echte PostgreSQL-Datenbank (TEST_DATABASE_URL).

Vor jedem Test wird das Schema neu aufgebaut. Für lokale Läufe:
  docker compose -f core/docker-compose.yml up -d db
  export TEST_DATABASE_URL=postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub_test
"""

import os
import tempfile
from collections.abc import Iterator

_tmp = tempfile.mkdtemp(prefix="beachhub-core-test-")
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub_test"
)
os.environ["DATA_DIR"] = _tmp
os.environ["SIGNATUR_PRIVATSCHLUESSEL_PFAD"] = f"{_tmp}/signatur.key"
os.environ["SECRET_KEY"] = "test-secret-key-0123456789abcdef"
os.environ["PIN_SCHLUESSEL"] = "dGVzdC1waW4tc2NobHVlc3NlbC0zMi1ieXRlcy0hIQ=="
os.environ["APP_ENV"] = "dev"
os.environ["COOKIE_SECURE"] = "false"
os.environ["SMTP_HOST"] = ""
os.environ["ENABLE_SCHEDULER"] = "false"

import pyotp  # noqa: E402
import pytest  # noqa: E402
from beachhub_core import auth  # noqa: E402
from beachhub_core.database import SessionLocal, engine, stelle_extensions_sicher  # noqa: E402
from beachhub_core.main import app  # noqa: E402
from beachhub_core.models import AdminUser, Base  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402


@pytest.fixture(autouse=True)
def frisches_schema() -> Iterator[None]:
    stelle_extensions_sicher(engine)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture(autouse=True)
def _rate_limits_leeren() -> None:
    auth.reset_rate_limits()


@pytest.fixture
def db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def admin(db: Session) -> tuple[AdminUser, str]:
    user, secret = auth.lege_admin_an(db, name="admin", passwort="test-passwort-1234")
    db.commit()
    return user, secret


@pytest.fixture
def eingeloggt(client: TestClient, admin: tuple[AdminUser, str]) -> TestClient:
    _, secret = admin
    r = client.post(
        "/admin/login",
        data={"name": "admin", "passwort": "test-passwort-1234", "code": pyotp.TOTP(secret).now()},
        follow_redirects=False,
    )
    assert r.status_code == 303
    seite = client.get("/admin")
    import re

    m = re.search(r'name="csrf_token" value="([^"]+)"', seite.text)
    assert m
    client.csrf = m.group(1)  # type: ignore[attr-defined]
    return client
