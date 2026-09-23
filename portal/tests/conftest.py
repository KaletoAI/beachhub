"""Tests laufen gegen eine echte PostgreSQL-Datenbank (TEST_PORTAL_DATABASE_URL).

Vor jedem Test wird das Schema `spiegel` neu aufgebaut. Lokal:
  export TEST_PORTAL_DATABASE_URL=postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub_portal_test
"""

import os
import shutil
import tempfile
from collections.abc import Iterator
from datetime import timedelta

from hilfen import JETZT, KUNDE_ID, OEFFENTLICH

_tmp = tempfile.mkdtemp(prefix="beachhub-portal-test-")
os.environ["PORTAL_DATABASE_URL"] = os.environ.get(
    "TEST_PORTAL_DATABASE_URL",
    "postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub_portal_test",
)
os.environ["PORTAL_DATA_DIR"] = _tmp
os.environ["PORTAL_SECRET_KEY"] = "test-secret-key-0123456789abcdef"
os.environ["PORTAL_APP_ENV"] = "dev"
os.environ["PORTAL_COOKIE_SECURE"] = "false"
os.environ["PORTAL_SMTP_HOST"] = ""
os.environ["PORTAL_ENABLE_SCHEDULER"] = "false"
os.environ["PORTAL_KANAL_TOKEN"] = "test-kanal-token"
os.environ["PORTAL_CORE_PUBLIC_KEY"] = OEFFENTLICH
os.environ["PORTAL_FAKE_ZAHLUNG"] = "true"
os.environ["PORTAL_BETREIBER_EMAIL"] = "halle@example.org"
os.environ["PORTAL_BASE_URL"] = "http://testserver"

import pytest  # noqa: E402
from beachhub_portal import auth, mail, uhr  # noqa: E402
from beachhub_portal.config import settings  # noqa: E402
from beachhub_portal.database import SessionLocal, engine, stelle_schema_sicher  # noqa: E402
from beachhub_portal.main import app  # noqa: E402
from beachhub_portal.models import Base, Sitzung  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402


@pytest.fixture(autouse=True)
def frisches_schema() -> Iterator[None]:
    ordner = settings.data_dir / "rechnungen_tmp"
    if ordner.exists():
        shutil.rmtree(ordner)
    stelle_schema_sicher(engine)
    with engine.begin() as conn:
        # test_grundgeruest.py steuert Alembic direkt; ein abgebrochener Lauf hinterließe sonst
        # einen veralteten Versionsstand.
        conn.execute(text("DROP TABLE IF EXISTS spiegel.alembic_version"))
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


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


class _Uhr:
    def __init__(self) -> None:
        self.jetzt = JETZT

    def weiter(self, **dauer: float) -> None:
        self.jetzt += timedelta(**dauer)


@pytest.fixture
def uhr_steht(monkeypatch: pytest.MonkeyPatch) -> _Uhr:
    u = _Uhr()
    monkeypatch.setattr(uhr, "jetzt", lambda: u.jetzt)
    return u


@pytest.fixture(autouse=True)
def _rate_limits_leeren() -> None:
    auth.reset_rate_limits()


@pytest.fixture(autouse=True)
def mail_ausgang(monkeypatch: pytest.MonkeyPatch) -> Iterator[list]:
    ausgang: list = []
    monkeypatch.setattr(mail, "TEST_AUSGANG", ausgang)
    yield ausgang


@pytest.fixture
def angemeldet(uhr_steht: _Uhr, client: TestClient, db: Session) -> TestClient:
    """Browser mit Session des Kontos „Anna“ (Kunde KUNDE_ID); die Uhr steht auf JETZT.
    `angemeldet.csrf` enthält das CSRF-Token, `angemeldet.konto_id` die Konto-ID."""
    konto, token = auth.melde_an(db, "anna@example.org", uhr.jetzt())
    konto.anzeigename = "Anna"
    konto.kunde_id = KUNDE_ID
    db.commit()
    client.cookies.set(auth.COOKIE, token)
    client.csrf = db.scalar(select(Sitzung.csrf_token).where(Sitzung.konto_id == konto.id))  # type: ignore[attr-defined]
    client.konto_id = konto.id  # type: ignore[attr-defined]
    return client
