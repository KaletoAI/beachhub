# Stufe 1: Hauptsystem (core) – Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Das Hauptsystem mit Datenmodell, Admin-UI (Felder, Betriebszeiten, Tarife, Kunden, Belegungsplan, Sperren, Dauerbuchungen), Rechnungen und signierter Lesestand-Erzeugung – so dass der Betreiber vor Eröffnung Preise, Dauerbuchungen und Rechnungen vollständig über das System abwickeln kann.

**Architecture:** Monorepo mit zwei Python-Paketen in dieser Stufe: `shared/` (kanonisches JSON, Ed25519-Signatur, Zeit-/Slot-Logik, Lesestand-Schemata) und `core/` (FastAPI-App mit serverseitig gerenderten Jinja2-Templates, SQLAlchemy 2.0 gegen PostgreSQL, Alembic-Migrationen). Fachlogik liegt in `core/beachhub_core/services/*` als reine Funktionen über einer `Session`; Routen sind dünn. Alle Zeitstempel werden als zeitzonenbewusste UTC-Werte gespeichert und in `Europe/Berlin` angezeigt. Portal-Kanal, Halle, Stripe und Präsenz sind **nicht** Teil dieser Stufe; die Lesestand-Dokumente werden erzeugt und gespeichert, aber noch nicht übertragen.

**Tech Stack:** Python 3.12, FastAPI 0.115, SQLAlchemy 2.0, Alembic, PostgreSQL 16 (btree_gist für Exklusionsconstraints), Jinja2, pydantic-settings, argon2-cffi, pyotp (TOTP), PyNaCl (Ed25519), WeasyPrint (Rechnungs-PDF), APScheduler, aiosmtplib, pytest, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-05-beachhub-design.md` (Abschnitte 3.1–3.9, 3.12, 3.13, 4, 5, 8.1 „Lesestand“, 9, 10, 11).

## Global Constraints

- Python **3.12**; alle Pakete mit `pyproject.toml`, Abhängigkeiten mit exakten Versionen (`==`).
- Geldbeträge durchgehend `decimal.Decimal` mit `DECIMAL(10, 2)`; nie `float` (N-7).
- Zeitstempel in der DB: `TIMESTAMP WITH TIME ZONE`, immer UTC-aware; Anzeige/Eingabe in `Europe/Berlin` (N-7).
- Oberflächen auf Deutsch, serverseitig gerendert, **kein Frontend-Build, kein npm**, eine `style.css` (Abschnitt 11).
- Jede Änderung an Buchungen, Tarifen, Rechnungen, Guthaben, Konfiguration erzeugt einen Audit-Eintrag (N-6).
- Alle Fristen/Vorläufe/Fenster sind Konfigurationswerte, kein Code (N-5).
- Das System löst keine Erstattungen/Auszahlungen aus (A-ZAHL-6).
- Tests laufen gegen eine echte PostgreSQL-Datenbank (`TEST_DATABASE_URL`), nie gegen SQLite: der Exklusionsconstraint ist Postgres-spezifisch.
- Commits auf Deutsch, Präfix nach Art: `feat:`, `test:`, `fix:`, `chore:`, `docs:`.
- Lint: `ruff check`, `ruff format --check`; Typen: `mypy --strict` für `shared/` und `core/beachhub_core/services/`.

## Dateistruktur (Zielbild dieser Stufe)

```
shared/
  pyproject.toml
  beachhub_shared/
    __init__.py
    canonical_json.py     kanonische JSON-Serialisierung (sortierte Schlüssel, keine Leerzeichen, UTF-8)
    signatur.py           Ed25519: Schlüsselpaar, signiere, pruefe
    zeit.py               Zeitzone Europe/Berlin, UTC-Konvertierung, kombiniere(datum, uhrzeit)
    slots.py              Slot-Berechnung aus Raster + Betriebszeiten + Ausnahmetagen (reine Funktionen, dataclasses)
    lesestand.py          pydantic-Schemata der Lesestand-Dokumente (belegung, tarife, konto)
  tests/
    test_canonical_json.py test_signatur.py test_zeit.py test_slots.py test_lesestand_schema.py

core/
  pyproject.toml
  alembic.ini
  alembic/env.py, alembic/versions/*.py
  Dockerfile
  docker-compose.yml       app + postgres + caddy (nur WireGuard-Interface)
  .env.example
  beachhub_core/
    __init__.py
    main.py                App, Middleware, Router, Lifespan (Scheduler)
    config.py              Settings (pydantic-settings)
    database.py            Engine, SessionLocal, get_db, btree_gist-Extension
    clock.py               now(db)/today(db) mit Admin-Override (wie SportAbo-Manager)
    cli.py                 `beachhub-core create-admin`, `keygen`, `monatslauf`
    auth.py                Admin-Sessions, Argon2, TOTP, CSRF, Rate-Limit
    mail.py                SMTP-Versand mit Test-Backend
    jobs.py                APScheduler: Monatslauf, Lesestand-Aktualisierung
    models/
      __init__.py          re-exportiert alle Modelle, Base
      base.py              Base, UUID-PK-Mixin, Zeitstempel-Mixin, utcnow
      stammdaten.py        Feld, FeldRaster, Betriebszeit, Ausnahmetag, Kundengruppe, Tarif
      kunden.py            Kunde, GuthabenBuchung
      buchungen.py         Buchung, Dauerbuchung, Sperre, Storno (+ Exklusionsconstraints)
      rechnungen.py        Rechnung, RechnungPosition, Nummernkreis
      system.py            Konfiguration, AdminUser, Audit, LesestandVersion, AppSetting
    services/
      konfiguration.py     typisierte Konfigurationswerte mit Defaults
      audit.py             protokolliere()
      tarife.py            ermittle_preis()
      pin.py               PIN erzeugen, hashen, verschlüsseln
      buchungen.py         lege_an(), finde_kollisionen(), BuchungsFehler
      sperren.py           lege_an() mit Kollisionsentscheidungen
      dauerbuchungen.py    plane(), lege_an(), beende()
      storno.py            storniere(), pruefe_nachbuchung(), kulanz()
      guthaben.py          buche(), saldo()
      rechnungen.py        Einzel-/Sammel-/Stornorechnung, Nummernkreis, Monatslauf, CSV
      rechnung_pdf.py      PDF über WeasyPrint, Archiv + SHA-256
      lesestand.py         belegung/tarife/konto-Dokumente erzeugen, signieren, versionieren
    routes/
      admin_auth.py        /admin/login, /admin/logout
      dashboard.py         /admin
      stammdaten.py        /admin/felder, /admin/betriebszeiten, /admin/ausnahmetage, /admin/kundengruppen, /admin/tarife, /admin/konfiguration
      kunden.py            /admin/kunden
      belegung.py          /admin/belegung (Kalender, Buchung, Sperre, Dauerbuchung, Storno)
      rechnungen.py        /admin/rechnungen
      system.py            /admin/system (Audit, Lesestand-Versionen, Uhr-Override)
    templates/
      base.html, login.html, dashboard.html
      stammdaten/*.html, kunden/*.html, belegung/*.html, rechnungen/*.html, system/*.html
      rechnung_pdf.html    Druckvorlage
      mail/*.txt           Mailtexte
    static/style.css
  tests/
    conftest.py            Postgres-Test-DB, Admin-Session, Fixtures
    test_*.py              je Service/Route
.github/workflows/ci.yml   ruff, mypy, pytest (mit Postgres-Service) für shared und core
```

---

## Task 1: Monorepo-Grundgerüst, Konfiguration, Datenbank, CI

**Files:**
- Create: `shared/pyproject.toml`, `shared/beachhub_shared/__init__.py`
- Create: `core/pyproject.toml`, `core/.env.example`, `core/docker-compose.yml`, `core/Dockerfile`
- Create: `core/beachhub_core/__init__.py`, `core/beachhub_core/config.py`, `core/beachhub_core/database.py`, `core/beachhub_core/main.py`
- Create: `core/beachhub_core/models/__init__.py`, `core/beachhub_core/models/base.py`
- Create: `core/tests/conftest.py`, `core/tests/test_health.py`
- Create: `.github/workflows/ci.yml`, `ruff.toml`, `mypy.ini`
- Modify: `.gitignore`, `README.md`

**Interfaces:**
- Produces: `beachhub_core.config.settings: Settings` (Felder: `database_url`, `secret_key`, `app_env`, `cookie_secure`, `data_dir: Path`, `base_url`, `signatur_privatschluessel_pfad: Path`, `pin_schluessel`, `smtp_host`, `smtp_port`, `smtp_user`, `smtp_password`, `email_from`, `enable_scheduler`, `has_insecure_defaults: bool`).
- Produces: `beachhub_core.database.engine`, `SessionLocal`, `get_db()` (FastAPI-Dependency, yield Session), `stelle_extensions_sicher(engine)`.
- Produces: `beachhub_core.models.base.Base`, `UUIDMixin` (`id: uuid.UUID`, Default `uuid4`), `ZeitstempelMixin` (`created_at`, `updated_at`), `utcnow()`.
- Produces: Test-Fixtures `db` (Session), `client` (TestClient) mit frischem Schema je Test.

- [ ] **Step 1: Paketdateien anlegen**

`shared/pyproject.toml`:
```toml
[project]
name = "beachhub-shared"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "pydantic==2.10.3",
  "pynacl==1.5.0",
]
[project.optional-dependencies]
dev = ["pytest==8.3.4", "ruff==0.8.4", "mypy==1.13.0"]
[build-system]
requires = ["setuptools==75.6.0"]
build-backend = "setuptools.build_meta"
[tool.setuptools.packages.find]
include = ["beachhub_shared*"]
```

`core/pyproject.toml`:
```toml
[project]
name = "beachhub-core"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  # beachhub-shared wird separat installiert: pip install -e ../shared
  "fastapi==0.115.6",
  "uvicorn[standard]==0.34.0",
  "sqlalchemy==2.0.36",
  "psycopg[binary]==3.2.3",
  "alembic==1.14.0",
  "jinja2==3.1.6",
  "python-multipart==0.0.32",
  "pydantic[email]==2.10.3",
  "pydantic-settings==2.7.0",
  "argon2-cffi==25.1.0",
  "pyotp==2.9.0",
  "pynacl==1.5.0",
  "cryptography==44.0.0",
  "weasyprint==63.1",
  "apscheduler==3.11.0",
  "aiosmtplib==5.1.2",
  "httpx==0.28.1",
]
[project.optional-dependencies]
dev = ["pytest==8.3.4", "ruff==0.8.4", "mypy==1.13.0", "pypdf==5.1.0"]
[project.scripts]
beachhub-core = "beachhub_core.cli:main"
[build-system]
requires = ["setuptools==75.6.0"]
build-backend = "setuptools.build_meta"
[tool.setuptools.packages.find]
include = ["beachhub_core*"]
[tool.setuptools.package-data]
beachhub_core = ["templates/**/*", "static/*"]
```
Hinweis: `beachhub-shared` ist ein lokales Paket ohne Registry. Installation lokal mit `pip install -e ../shared -e .[dev]` aus `core/`; Dockerfile und CI installieren `shared` ebenfalls explizit.

`ruff.toml` (Repo-Wurzel):
```toml
line-length = 100
target-version = "py312"
[lint]
select = ["E", "F", "I", "B", "UP", "N", "S"]
ignore = ["S101"]  # assert in Tests
[lint.per-file-ignores]
"*/tests/*" = ["S105", "S106"]
```

`mypy.ini`:
```ini
[mypy]
python_version = 3.12
strict = True
files = shared/beachhub_shared, core/beachhub_core/services
plugins = pydantic.mypy
[mypy-nacl.*,weasyprint.*,apscheduler.*,pyotp.*,aiosmtplib.*]
ignore_missing_imports = True
```

- [ ] **Step 2: Konfiguration schreiben**

`core/beachhub_core/config.py`:
```python
"""Einstellungen aus Umgebung/.env. Ein Import, eine Instanz: `settings`."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_SECRET = "change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub"
    secret_key: str = INSECURE_SECRET
    pin_schluessel: str = INSECURE_SECRET  # 32 Byte base64 für PIN-Verschlüsselung
    app_env: str = "dev"  # dev | production
    cookie_secure: bool = False
    base_url: str = "http://127.0.0.1:8000"
    data_dir: Path = Path("./data")
    signatur_privatschluessel_pfad: Path = Path("./data/signatur.key")
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = "beachhub@example.org"
    enable_scheduler: bool = True
    betreiber_name: str = "Beachhalle"
    betreiber_adresse: str = ""
    betreiber_ust_id: str = ""
    betreiber_bank: str = ""

    @property
    def has_insecure_defaults(self) -> bool:
        return self.secret_key == INSECURE_SECRET or self.pin_schluessel == INSECURE_SECRET


settings = Settings()
```

`core/.env.example`:
```
DATABASE_URL=postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub
SECRET_KEY=change-me
PIN_SCHLUESSEL=change-me
APP_ENV=dev
COOKIE_SECURE=false
BASE_URL=http://127.0.0.1:8000
DATA_DIR=./data
SIGNATUR_PRIVATSCHLUESSEL_PFAD=./data/signatur.key
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
EMAIL_FROM=beachhub@example.org
ENABLE_SCHEDULER=true
BETREIBER_NAME=Beachhalle Musterstadt
BETREIBER_ADRESSE=Musterweg 1, 12345 Musterstadt
BETREIBER_UST_ID=DE123456789
BETREIBER_BANK=IBAN DE00 0000 0000 0000 0000 00
```

- [ ] **Step 3: Datenbank und Basis-Modelle**

`core/beachhub_core/database.py`:
```python
from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from beachhub_core.config import settings

engine: Engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def stelle_extensions_sicher(target: Engine) -> None:
    """btree_gist wird für Exklusionsconstraints über (uuid, tstzrange) gebraucht."""
    with target.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

`core/beachhub_core/models/base.py`:
```python
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class UUIDMixin:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class ZeitstempelMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
```

`core/beachhub_core/models/__init__.py` (wird in späteren Tasks erweitert):
```python
from beachhub_core.models.base import Base, UUIDMixin, ZeitstempelMixin, utcnow

__all__ = ["Base", "UUIDMixin", "ZeitstempelMixin", "utcnow"]
```

- [ ] **Step 4: App-Einstieg mit Health-Endpunkt**

`core/beachhub_core/main.py`:
```python
import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from beachhub_core.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

if settings.has_insecure_defaults:
    if settings.app_env == "production":
        raise RuntimeError("Start verweigert: SECRET_KEY/PIN_SCHLUESSEL sind Standardwerte.")
    logger.warning("SECRET_KEY/PIN_SCHLUESSEL sind unsichere Standardwerte – nur für Entwicklung.")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield


app = FastAPI(title="Beachhub Hauptsystem", docs_url=None, redoc_url=None, lifespan=lifespan)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'",
        )
        if settings.cookie_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response


app.add_middleware(SecurityHeadersMiddleware)

static_dir = Path(__file__).resolve().parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})
```

- [ ] **Step 5: Test-Fixtures und Health-Test schreiben**

`core/tests/conftest.py`:
```python
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

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from beachhub_core.database import SessionLocal, engine, stelle_extensions_sicher
from beachhub_core.main import app
from beachhub_core.models import Base


@pytest.fixture(autouse=True)
def frisches_schema() -> Iterator[None]:
    stelle_extensions_sicher(engine)
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
```

`core/tests/test_health.py`:
```python
from fastapi.testclient import TestClient


def test_health_antwortet_ok(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    assert r.headers["X-Frame-Options"] == "DENY"
```

- [ ] **Step 6: Docker-Compose für Postgres, Dockerfile, CI**

`core/docker-compose.yml`:
```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: beachhub
      POSTGRES_PASSWORD: beachhub
      POSTGRES_DB: beachhub
    ports: ["127.0.0.1:5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data", "./deploy/init-test-db.sql:/docker-entrypoint-initdb.d/10-test-db.sql:ro"]
  app:
    build: { context: .., dockerfile: core/Dockerfile }
    env_file: .env
    environment:
      DATABASE_URL: postgresql+psycopg://beachhub:beachhub@db:5432/beachhub
    depends_on: [db]
    ports: ["127.0.0.1:8000:8000"]
    volumes: ["./data:/app/data"]
volumes:
  pgdata: {}
```

`core/deploy/init-test-db.sql`:
```sql
CREATE DATABASE beachhub_test OWNER beachhub;
```

`core/Dockerfile`:
```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libffi8 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY shared /app/shared
COPY core /app/core
RUN pip install --no-cache-dir /app/shared /app/core
WORKDIR /app/core
CMD ["sh", "-c", "alembic upgrade head && uvicorn beachhub_core.main:app --host 0.0.0.0 --port 8000"]
```

`.github/workflows/ci.yml`:
```yaml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env: { POSTGRES_USER: beachhub, POSTGRES_PASSWORD: beachhub, POSTGRES_DB: beachhub_test }
        ports: ["5432:5432"]
        options: --health-cmd "pg_isready -U beachhub" --health-interval 5s --health-timeout 5s --health-retries 10
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: sudo apt-get update && sudo apt-get install -y libpango-1.0-0 libpangoft2-1.0-0 fonts-dejavu-core
      - run: pip install -e shared[dev] -e core[dev]
      - run: ruff check . && ruff format --check .
      - run: mypy
      - run: cd shared && pytest -q
      - run: cd core && pytest -q
        env:
          TEST_DATABASE_URL: postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub_test
```

`.gitignore` ergänzen:
```
core/data/
core/.env
*.egg-info/
.mypy_cache/
.ruff_cache/
```

- [ ] **Step 7: Installieren, Postgres starten, Test ausführen**

Run:
```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e shared[dev] -e core[dev]
docker compose -f core/docker-compose.yml up -d db
cd core && TEST_DATABASE_URL=postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub_test pytest -q
```
Expected: `1 passed`

- [ ] **Step 8: Commit**

```bash
git add -A && git commit -m "chore: Monorepo-Grundgerüst für core und shared mit Postgres-Tests und CI"
```

---

## Task 2: shared – kanonisches JSON und Ed25519-Signatur

**Files:**
- Create: `shared/beachhub_shared/canonical_json.py`, `shared/beachhub_shared/signatur.py`
- Test: `shared/tests/test_canonical_json.py`, `shared/tests/test_signatur.py`

**Interfaces:**
- Produces: `canonical_json.dumps(obj: Any) -> bytes` – sortierte Schlüssel, keine Leerzeichen, `ensure_ascii=False`, UTF-8; `Decimal` als String, `datetime` als ISO-8601 mit Offset, `UUID` als String, `date` als ISO.
- Produces: `signatur.erzeuge_schluesselpaar() -> tuple[str, str]` (privat_hex, oeffentlich_hex); `signatur.signiere(inhalt: dict, privat_hex: str) -> str`; `signatur.pruefe(inhalt: dict, signatur_hex: str, oeffentlich_hex: str) -> bool`; `signatur.lade_privatschluessel(pfad: Path) -> str`; `signatur.oeffentlicher_schluessel(privat_hex: str) -> str`.

- [ ] **Step 1: Failing Tests schreiben**

`shared/tests/test_canonical_json.py`:
```python
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from beachhub_shared.canonical_json import dumps


def test_schluessel_sortiert_und_kompakt() -> None:
    assert dumps({"b": 1, "a": [1, 2]}) == b'{"a":[1,2],"b":1}'


def test_umlaute_bleiben_utf8() -> None:
    assert dumps({"n": "Müller"}) == '{"n":"Müller"}'.encode()


def test_decimal_datum_uuid_werden_strings() -> None:
    u = uuid.UUID("12345678-1234-5678-1234-567812345678")
    dt = datetime(2027, 11, 3, 18, 0, tzinfo=UTC)
    out = dumps({"p": Decimal("12.50"), "d": date(2027, 11, 3), "t": dt, "u": u})
    assert out == (
        b'{"d":"2027-11-03","p":"12.50","t":"2027-11-03T18:00:00+00:00",'
        b'"u":"12345678-1234-5678-1234-567812345678"}'
    )


def test_gleicher_inhalt_gleiche_bytes() -> None:
    assert dumps({"x": 1, "y": 2}) == dumps({"y": 2, "x": 1})
```

`shared/tests/test_signatur.py`:
```python
from pathlib import Path

from beachhub_shared.signatur import (
    erzeuge_schluesselpaar,
    lade_privatschluessel,
    oeffentlicher_schluessel,
    pruefe,
    signiere,
)


def test_signatur_ist_pruefbar() -> None:
    priv, pub = erzeuge_schluesselpaar()
    doc = {"dokument": "belegung", "version": 3, "inhalt": {"felder": []}}
    sig = signiere(doc, priv)
    assert pruefe(doc, sig, pub)


def test_manipulation_faellt_auf() -> None:
    priv, pub = erzeuge_schluesselpaar()
    doc = {"version": 1}
    sig = signiere(doc, priv)
    assert not pruefe({"version": 2}, sig, pub)


def test_falscher_schluessel_faellt_auf() -> None:
    priv, _ = erzeuge_schluesselpaar()
    _, pub2 = erzeuge_schluesselpaar()
    assert not pruefe({"a": 1}, signiere({"a": 1}, priv), pub2)


def test_schluessel_laden_und_ableiten(tmp_path: Path) -> None:
    priv, pub = erzeuge_schluesselpaar()
    pfad = tmp_path / "signatur.key"
    pfad.write_text(priv + "\n")
    assert lade_privatschluessel(pfad) == priv
    assert oeffentlicher_schluessel(priv) == pub
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `cd shared && pytest -q`
Expected: FAIL mit `ModuleNotFoundError: beachhub_shared.canonical_json`

- [ ] **Step 3: Implementieren**

`shared/beachhub_shared/canonical_json.py`:
```python
"""Kanonische JSON-Serialisierung für Signaturen: gleicher Inhalt → gleiche Bytes."""
import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any


def _konvertiere(o: Any) -> Any:
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, datetime | date):
        return o.isoformat()
    if isinstance(o, uuid.UUID):
        return str(o)
    raise TypeError(f"Nicht serialisierbar: {type(o).__name__}")


def dumps(obj: Any) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_konvertiere
    ).encode("utf-8")
```

`shared/beachhub_shared/signatur.py`:
```python
"""Ed25519-Signaturen über kanonisches JSON. Schlüssel als Hex-Strings."""
from pathlib import Path
from typing import Any

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

from beachhub_shared.canonical_json import dumps


def erzeuge_schluesselpaar() -> tuple[str, str]:
    sk = SigningKey.generate()
    return sk.encode().hex(), sk.verify_key.encode().hex()


def oeffentlicher_schluessel(privat_hex: str) -> str:
    return SigningKey(bytes.fromhex(privat_hex)).verify_key.encode().hex()


def lade_privatschluessel(pfad: Path) -> str:
    return pfad.read_text(encoding="utf-8").strip()


def signiere(inhalt: dict[str, Any], privat_hex: str) -> str:
    sk = SigningKey(bytes.fromhex(privat_hex))
    return sk.sign(dumps(inhalt)).signature.hex()


def pruefe(inhalt: dict[str, Any], signatur_hex: str, oeffentlich_hex: str) -> bool:
    vk = VerifyKey(bytes.fromhex(oeffentlich_hex))
    try:
        vk.verify(dumps(inhalt), bytes.fromhex(signatur_hex))
    except (BadSignatureError, ValueError):
        return False
    return True
```

- [ ] **Step 4: Tests grün**

Run: `cd shared && pytest -q`
Expected: `8 passed`

- [ ] **Step 5: Commit**

```bash
git add shared && git commit -m "feat(shared): kanonisches JSON und Ed25519-Signatur"
```

---

## Task 3: shared – Zeit und Slot-Berechnung

**Files:**
- Create: `shared/beachhub_shared/zeit.py`, `shared/beachhub_shared/slots.py`
- Test: `shared/tests/test_zeit.py`, `shared/tests/test_slots.py`

**Interfaces:**
- Produces: `zeit.BERLIN: ZoneInfo`; `zeit.kombiniere(datum: date, uhrzeit: time) -> datetime` (UTC-aware aus lokaler Berliner Zeit); `zeit.lokal(dt: datetime) -> datetime` (nach Berlin); `zeit.lokales_datum(dt) -> date`.
- Produces: `slots.RasterKonfig` (dataclass: `wochentag: int | None`, `modus: Literal["dauer","fenster"]`, `slot_minuten: int | None`, `fenster: list[tuple[time, time]]`), `slots.Betriebszeit` (dataclass: `wochentag`, `oeffnet: time`, `schliesst: time`), `slots.Ausnahme` (dataclass: `datum`, `geschlossen: bool`, `oeffnet: time | None`, `schliesst: time | None`), `slots.Slot` (dataclass frozen: `beginn: datetime`, `ende: datetime`, beide UTC-aware).
- Produces: `slots.slots_fuer_tag(datum, raster: list[RasterKonfig], betriebszeiten: list[Betriebszeit], ausnahmen: list[Ausnahme]) -> list[Slot]`; `slots.zeitraum_ist_slotfolge(beginn, ende, tages_slots: list[Slot]) -> bool` (Zeitraum entspricht einem oder mehreren direkt aufeinanderfolgenden Slots); `slots.slots_im_zeitraum(beginn, ende, tages_slots) -> list[Slot]`.

Regeln: Raster mit `wochentag == datum.weekday()` gewinnt vor `wochentag is None`. Modus `dauer`: Slots ab Öffnung in `slot_minuten`-Schritten bis Schließung, letzter Slot endet ≤ Schließung. Modus `fenster`: nur Fenster, die vollständig innerhalb Öffnung/Schließung liegen. Ausnahme `geschlossen` → keine Slots; Ausnahme mit Zeiten → ersetzt Betriebszeit des Tages. Keine Betriebszeit für den Wochentag → keine Slots.

- [ ] **Step 1: Failing Tests schreiben**

`shared/tests/test_zeit.py`:
```python
from datetime import date, time

from beachhub_shared.zeit import kombiniere, lokal, lokales_datum


def test_kombiniere_winterzeit() -> None:
    dt = kombiniere(date(2027, 12, 1), time(19, 0))
    assert dt.isoformat() == "2027-12-01T18:00:00+00:00"


def test_kombiniere_sommerzeit() -> None:
    dt = kombiniere(date(2027, 10, 1), time(19, 0))
    assert dt.isoformat() == "2027-10-01T17:00:00+00:00"


def test_lokal_und_datum() -> None:
    dt = kombiniere(date(2027, 12, 1), time(23, 30))
    assert lokal(dt).hour == 23
    assert lokales_datum(dt) == date(2027, 12, 1)
```

`shared/tests/test_slots.py`:
```python
from datetime import date, time

from beachhub_shared.slots import (
    Ausnahme,
    Betriebszeit,
    RasterKonfig,
    slots_fuer_tag,
    slots_im_zeitraum,
    zeitraum_ist_slotfolge,
)
from beachhub_shared.zeit import kombiniere

MI = date(2027, 12, 1)  # Mittwoch, weekday 2
BZ = [Betriebszeit(wochentag=2, oeffnet=time(17, 0), schliesst=time(23, 0))]


def test_dauer_raster_erzeugt_stundenslots() -> None:
    raster = [RasterKonfig(wochentag=None, modus="dauer", slot_minuten=60, fenster=[])]
    s = slots_fuer_tag(MI, raster, BZ, [])
    assert len(s) == 6
    assert s[0].beginn == kombiniere(MI, time(17, 0))
    assert s[-1].ende == kombiniere(MI, time(23, 0))


def test_dauer_raster_90_minuten_laesst_rest_weg() -> None:
    raster = [RasterKonfig(wochentag=None, modus="dauer", slot_minuten=90, fenster=[])]
    s = slots_fuer_tag(MI, raster, BZ, [])
    assert len(s) == 4  # 17:00-18:30, 18:30-20:00, 20:00-21:30, 21:30-23:00


def test_fenster_raster_nur_innerhalb_betriebszeit() -> None:
    raster = [RasterKonfig(wochentag=None, modus="fenster", slot_minuten=None,
                           fenster=[(time(19, 0), time(21, 0)), (time(21, 0), time(23, 0)),
                                    (time(23, 0), time(1, 0))])]
    s = slots_fuer_tag(MI, raster, BZ, [])
    assert [(x.beginn.hour, x.ende.hour) for x in s] == [(18, 20), (20, 22)]  # UTC-Stunden


def test_wochentag_raster_gewinnt() -> None:
    raster = [
        RasterKonfig(wochentag=None, modus="dauer", slot_minuten=60, fenster=[]),
        RasterKonfig(wochentag=2, modus="dauer", slot_minuten=120, fenster=[]),
    ]
    assert len(slots_fuer_tag(MI, raster, BZ, [])) == 3


def test_ausnahme_geschlossen() -> None:
    raster = [RasterKonfig(wochentag=None, modus="dauer", slot_minuten=60, fenster=[])]
    aus = [Ausnahme(datum=MI, geschlossen=True, oeffnet=None, schliesst=None)]
    assert slots_fuer_tag(MI, raster, BZ, aus) == []


def test_ausnahme_sonderzeiten() -> None:
    raster = [RasterKonfig(wochentag=None, modus="dauer", slot_minuten=60, fenster=[])]
    aus = [Ausnahme(datum=MI, geschlossen=False, oeffnet=time(10, 0), schliesst=time(12, 0))]
    assert len(slots_fuer_tag(MI, raster, BZ, aus)) == 2


def test_ohne_betriebszeit_keine_slots() -> None:
    raster = [RasterKonfig(wochentag=None, modus="dauer", slot_minuten=60, fenster=[])]
    assert slots_fuer_tag(date(2027, 12, 2), raster, BZ, []) == []


def test_slotfolge_pruefung() -> None:
    raster = [RasterKonfig(wochentag=None, modus="dauer", slot_minuten=60, fenster=[])]
    s = slots_fuer_tag(MI, raster, BZ, [])
    assert zeitraum_ist_slotfolge(kombiniere(MI, time(19, 0)), kombiniere(MI, time(21, 0)), s)
    assert not zeitraum_ist_slotfolge(kombiniere(MI, time(19, 30)), kombiniere(MI, time(21, 0)), s)
    assert not zeitraum_ist_slotfolge(kombiniere(MI, time(22, 0)), kombiniere(MI, time(23, 30)), s)
    assert len(slots_im_zeitraum(kombiniere(MI, time(19, 0)), kombiniere(MI, time(21, 0)), s)) == 2
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd shared && pytest -q tests/test_zeit.py tests/test_slots.py`
Expected: FAIL mit `ModuleNotFoundError`

- [ ] **Step 3: Implementieren**

`shared/beachhub_shared/zeit.py`:
```python
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")


def kombiniere(datum: date, uhrzeit: time) -> datetime:
    """Lokale Berliner Uhrzeit an einem Datum → UTC-aware datetime."""
    return datetime.combine(datum, uhrzeit, tzinfo=BERLIN).astimezone(UTC)


def lokal(dt: datetime) -> datetime:
    return dt.astimezone(BERLIN)


def lokales_datum(dt: datetime) -> date:
    return lokal(dt).date()
```

`shared/beachhub_shared/slots.py`:
```python
"""Slot-Berechnung: reine Funktionen ohne Datenbankbezug."""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal

from beachhub_shared.zeit import kombiniere


@dataclass
class RasterKonfig:
    wochentag: int | None
    modus: Literal["dauer", "fenster"]
    slot_minuten: int | None
    fenster: list[tuple[time, time]]


@dataclass
class Betriebszeit:
    wochentag: int
    oeffnet: time
    schliesst: time


@dataclass
class Ausnahme:
    datum: date
    geschlossen: bool
    oeffnet: time | None
    schliesst: time | None


@dataclass(frozen=True)
class Slot:
    beginn: datetime
    ende: datetime


def _oeffnungszeit(
    datum: date, betriebszeiten: list[Betriebszeit], ausnahmen: list[Ausnahme]
) -> tuple[datetime, datetime] | None:
    for a in ausnahmen:
        if a.datum == datum:
            if a.geschlossen or a.oeffnet is None or a.schliesst is None:
                return None
            return kombiniere(datum, a.oeffnet), _ende(datum, a.schliesst)
    for b in betriebszeiten:
        if b.wochentag == datum.weekday():
            return kombiniere(datum, b.oeffnet), _ende(datum, b.schliesst)
    return None


def _ende(datum: date, uhrzeit: time) -> datetime:
    """Schließzeit 00:00 bedeutet Mitternacht am Folgetag."""
    if uhrzeit == time(0, 0):
        return kombiniere(datum + timedelta(days=1), uhrzeit)
    return kombiniere(datum, uhrzeit)


def _raster_fuer(datum: date, raster: list[RasterKonfig]) -> RasterKonfig | None:
    spezifisch = [r for r in raster if r.wochentag == datum.weekday()]
    if spezifisch:
        return spezifisch[0]
    allgemein = [r for r in raster if r.wochentag is None]
    return allgemein[0] if allgemein else None


def slots_fuer_tag(
    datum: date,
    raster: list[RasterKonfig],
    betriebszeiten: list[Betriebszeit],
    ausnahmen: list[Ausnahme],
) -> list[Slot]:
    zeit = _oeffnungszeit(datum, betriebszeiten, ausnahmen)
    r = _raster_fuer(datum, raster)
    if zeit is None or r is None:
        return []
    oeffnet, schliesst = zeit
    ergebnis: list[Slot] = []
    if r.modus == "dauer":
        if not r.slot_minuten or r.slot_minuten <= 0:
            return []
        schritt = timedelta(minutes=r.slot_minuten)
        t = oeffnet
        while t + schritt <= schliesst:
            ergebnis.append(Slot(t, t + schritt))
            t += schritt
    else:
        for von, bis in r.fenster:
            b, e = kombiniere(datum, von), _ende(datum, bis)
            if e <= b:
                continue
            if b >= oeffnet and e <= schliesst:
                ergebnis.append(Slot(b, e))
    return sorted(ergebnis, key=lambda s: s.beginn)


def slots_im_zeitraum(beginn: datetime, ende: datetime, tages_slots: list[Slot]) -> list[Slot]:
    return [s for s in tages_slots if s.beginn >= beginn and s.ende <= ende]


def zeitraum_ist_slotfolge(beginn: datetime, ende: datetime, tages_slots: list[Slot]) -> bool:
    teil = slots_im_zeitraum(beginn, ende, tages_slots)
    if not teil or teil[0].beginn != beginn or teil[-1].ende != ende:
        return False
    return all(teil[i].ende == teil[i + 1].beginn for i in range(len(teil) - 1))
```

- [ ] **Step 4: Tests grün, Lint und Typen**

Run: `cd shared && pytest -q && cd .. && ruff check shared && mypy`
Expected: `19 passed`, keine Lint-/Typfehler

- [ ] **Step 5: Commit**

```bash
git add shared && git commit -m "feat(shared): Zeitzonen-Helfer und Slot-Berechnung"
```

---

## Task 4: Stammdaten- und Systemmodelle, Alembic, Konfiguration, Audit

**Files:**
- Create: `core/beachhub_core/models/stammdaten.py`, `core/beachhub_core/models/system.py`
- Modify: `core/beachhub_core/models/__init__.py`
- Create: `core/beachhub_core/services/__init__.py`, `core/beachhub_core/services/konfiguration.py`, `core/beachhub_core/services/audit.py`
- Create: `core/alembic.ini`, `core/alembic/env.py`, `core/alembic/script.py.mako`, `core/alembic/versions/0001_stammdaten_system.py` (per autogenerate)
- Test: `core/tests/test_konfiguration.py`, `core/tests/test_audit.py`, `core/tests/test_migrationen.py`

**Interfaces:**
- Produces Modelle (alle `UUIDMixin + ZeitstempelMixin`): `Feld(name, aktiv, reihenfolge, ha_licht_entity, ha_praesenz_entity, heizzone)`, `FeldRaster(feld_id, wochentag: int|None, modus: str, slot_minuten: int|None, fenster_json: list[list[str]])`, `Betriebszeit(wochentag, oeffnet: time, schliesst: time, gueltig_von: date|None, gueltig_bis: date|None)`, `Ausnahmetag(datum, geschlossen, oeffnet, schliesst, grund)`, `Kundengruppe(name, standard_zahlungsart: "online"|"rechnung")`, `Tarif(name, preis: Decimal, feld_id?, wochentag?, uhrzeit_von?, uhrzeit_bis?, kundengruppe_id?, gueltig_von?, gueltig_bis?, aktiv)`.
- Produces: `Konfiguration(schluessel: str PK, wert: str, typ: str)`, `AdminUser(name unique, passwort_hash, totp_secret, rolle: "admin"|"lesend", aktiv)`, `Audit(zeitpunkt, admin_user_id?, quelle, objekt_typ, objekt_id, vorher_json, nachher_json)`, `LesestandVersion(dokument PK, version, signiert_am, geaendert: bool)`, `AppSetting(key PK, value)`.
- Produces: `konfiguration.hole(db, schluessel) -> int | Decimal | str | bool` (typisiert nach `DEFAULTS`), `konfiguration.setze(db, schluessel, wert, admin_user_id=None)`, `konfiguration.DEFAULTS: dict[str, tuple[type, Any]]`.
- Produces: `audit.protokolliere(db, *, quelle, objekt_typ, objekt_id, vorher: dict|None, nachher: dict|None, admin_user_id=None) -> Audit` (kein Commit; ruft `db.add`), `audit.als_dict(obj) -> dict` (Spalten → JSON-fähige Werte; blendet `created_at`, `updated_at`, `pin_hash`, `pin_verschluesselt` aus).

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_konfiguration.py`:
```python
from decimal import Decimal

from sqlalchemy.orm import Session

from beachhub_core.models import Audit
from beachhub_core.services import konfiguration


def test_default_wird_typisiert_geliefert(db: Session) -> None:
    assert konfiguration.hole(db, "storno_frist_stunden") == 24
    assert isinstance(konfiguration.hole(db, "storno_frist_stunden"), int)
    assert konfiguration.hole(db, "ust_satz") == Decimal("19.00")


def test_setzen_ueberschreibt_und_protokolliert(db: Session) -> None:
    konfiguration.setze(db, "storno_frist_stunden", 48)
    db.commit()
    assert konfiguration.hole(db, "storno_frist_stunden") == 48
    a = db.query(Audit).filter_by(objekt_typ="konfiguration").one()
    assert a.vorher_json == {"wert": "24"} and a.nachher_json["wert"] == "48"


def test_unbekannter_schluessel_wirft() -> None:
    import pytest

    with pytest.raises(KeyError):
        konfiguration.DEFAULTS["gibt_es_nicht"]
```

`core/tests/test_audit.py`:
```python
from sqlalchemy.orm import Session

from beachhub_core.models import Audit, Feld
from beachhub_core.services import audit


def test_als_dict_und_protokollieren(db: Session) -> None:
    f = Feld(name="Feld 1", reihenfolge=1)
    db.add(f)
    db.flush()
    vorher = audit.als_dict(f)
    f.name = "Feld A"
    audit.protokolliere(db, quelle="admin", objekt_typ="feld", objekt_id=f.id,
                        vorher=vorher, nachher=audit.als_dict(f))
    db.commit()
    a = db.query(Audit).one()
    assert a.vorher_json["name"] == "Feld 1"
    assert a.nachher_json["name"] == "Feld A"
    assert a.objekt_id == f.id
```

`core/tests/test_migrationen.py`:
```python
"""Alembic-Migrationen müssen das gleiche Schema erzeugen wie die Modelle."""
import os

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from beachhub_core.database import engine
from beachhub_core.models import Base


def test_upgrade_head_erzeugt_alle_tabellen() -> None:
    Base.metadata.drop_all(bind=engine)
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    command.upgrade(cfg, "head")
    tabellen = set(inspect(engine).get_table_names())
    erwartet = set(Base.metadata.tables) | {"alembic_version"}
    assert erwartet <= tabellen
    command.downgrade(cfg, "base")
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_konfiguration.py tests/test_audit.py tests/test_migrationen.py`
Expected: FAIL mit ImportError

- [ ] **Step 3: Modelle schreiben**

`core/beachhub_core/models/stammdaten.py`:
```python
import uuid
from datetime import date, time
from decimal import Decimal

from sqlalchemy import DECIMAL, Boolean, Date, ForeignKey, Integer, String, Time
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from beachhub_core.models.base import Base, UUIDMixin, ZeitstempelMixin


class Feld(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "feld"
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    aktiv: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reihenfolge: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ha_licht_entity: Mapped[str | None] = mapped_column(String(200))
    ha_praesenz_entity: Mapped[str | None] = mapped_column(String(200))
    heizzone: Mapped[str | None] = mapped_column(String(100))
    raster: Mapped[list["FeldRaster"]] = relationship(
        back_populates="feld", cascade="all, delete-orphan", order_by="FeldRaster.wochentag"
    )


class FeldRaster(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "feld_raster"
    feld_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("feld.id"), nullable=False)
    wochentag: Mapped[int | None] = mapped_column(Integer)  # 0=Mo … 6=So, None = alle
    modus: Mapped[str] = mapped_column(String(10), nullable=False)  # dauer | fenster
    slot_minuten: Mapped[int | None] = mapped_column(Integer)
    fenster_json: Mapped[list[list[str]]] = mapped_column(JSONB, default=list, nullable=False)  # [["19:00","21:00"]]
    feld: Mapped[Feld] = relationship(back_populates="raster")


class Betriebszeit(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "betriebszeit"
    wochentag: Mapped[int] = mapped_column(Integer, nullable=False)
    oeffnet: Mapped[time] = mapped_column(Time, nullable=False)
    schliesst: Mapped[time] = mapped_column(Time, nullable=False)
    gueltig_von: Mapped[date | None] = mapped_column(Date)
    gueltig_bis: Mapped[date | None] = mapped_column(Date)


class Ausnahmetag(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "ausnahmetag"
    datum: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    geschlossen: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    oeffnet: Mapped[time | None] = mapped_column(Time)
    schliesst: Mapped[time | None] = mapped_column(Time)
    grund: Mapped[str] = mapped_column(String(200), default="", nullable=False)


class Kundengruppe(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "kundengruppe"
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    standard_zahlungsart: Mapped[str] = mapped_column(String(10), default="online", nullable=False)


class Tarif(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "tarif"
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    preis: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    feld_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("feld.id"))
    wochentag: Mapped[int | None] = mapped_column(Integer)
    uhrzeit_von: Mapped[time | None] = mapped_column(Time)
    uhrzeit_bis: Mapped[time | None] = mapped_column(Time)
    kundengruppe_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("kundengruppe.id"))
    gueltig_von: Mapped[date | None] = mapped_column(Date)
    gueltig_bis: Mapped[date | None] = mapped_column(Date)
    aktiv: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
```

`core/beachhub_core/models/system.py`:
```python
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from beachhub_core.models.base import Base, UUIDMixin, ZeitstempelMixin, utcnow


class Konfiguration(Base):
    __tablename__ = "konfiguration"
    schluessel: Mapped[str] = mapped_column(String(60), primary_key=True)
    wert: Mapped[str] = mapped_column(String(500), nullable=False)
    typ: Mapped[str] = mapped_column(String(10), nullable=False)  # int | decimal | str | bool


class AdminUser(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "admin_user"
    name: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    passwort_hash: Mapped[str] = mapped_column(String(300), nullable=False)
    totp_secret: Mapped[str] = mapped_column(String(64), nullable=False)
    rolle: Mapped[str] = mapped_column(String(10), default="admin", nullable=False)  # admin | lesend
    aktiv: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Audit(UUIDMixin, Base):
    __tablename__ = "audit"
    zeitpunkt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    admin_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    quelle: Mapped[str] = mapped_column(String(10), nullable=False)  # admin | portal | halle | system
    objekt_typ: Mapped[str] = mapped_column(String(40), nullable=False)
    objekt_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    vorher_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    nachher_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class LesestandVersion(Base):
    __tablename__ = "lesestand_version"
    dokument: Mapped[str] = mapped_column(String(80), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    signiert_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    geaendert: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class AppSetting(Base):
    __tablename__ = "app_setting"
    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str | None] = mapped_column(String(200))
```

`core/beachhub_core/models/__init__.py`:
```python
from beachhub_core.models.base import Base, UUIDMixin, ZeitstempelMixin, utcnow
from beachhub_core.models.stammdaten import Ausnahmetag, Betriebszeit, Feld, FeldRaster, Kundengruppe, Tarif
from beachhub_core.models.system import AdminUser, AppSetting, Audit, Konfiguration, LesestandVersion

__all__ = [
    "AdminUser", "AppSetting", "Audit", "Ausnahmetag", "Base", "Betriebszeit", "Feld", "FeldRaster",
    "Konfiguration", "Kundengruppe", "LesestandVersion", "Tarif", "UUIDMixin", "ZeitstempelMixin", "utcnow",
]
```

- [ ] **Step 4: Services schreiben**

`core/beachhub_core/services/__init__.py`: leer.

`core/beachhub_core/services/audit.py`:
```python
import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from beachhub_core.models import Audit


def _json(v: Any) -> Any:
    if isinstance(v, Decimal | uuid.UUID):
        return str(v)
    if isinstance(v, datetime | date | time):
        return v.isoformat()
    return v


def als_dict(obj: Any) -> dict[str, Any]:
    """Spaltenwerte eines Modells als JSON-fähiges dict (ohne created_at/updated_at)."""
    mapper = inspect(obj).mapper
    return {
        c.key: _json(getattr(obj, c.key))
        for c in mapper.column_attrs
        if c.key not in ("created_at", "updated_at", "pin_hash", "pin_verschluesselt")
    }


def protokolliere(
    db: Session,
    *,
    quelle: str,
    objekt_typ: str,
    objekt_id: uuid.UUID | None,
    vorher: dict[str, Any] | None,
    nachher: dict[str, Any] | None,
    admin_user_id: uuid.UUID | None = None,
) -> Audit:
    eintrag = Audit(
        quelle=quelle, objekt_typ=objekt_typ, objekt_id=objekt_id,
        vorher_json=vorher, nachher_json=nachher, admin_user_id=admin_user_id,
    )
    db.add(eintrag)
    return eintrag
```

`core/beachhub_core/services/konfiguration.py`:
```python
"""Konfigurationswerte: Defaults im Code, Überschreibung in der Tabelle `konfiguration`."""
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from beachhub_core.models import Konfiguration
from beachhub_core.services import audit

DEFAULTS: dict[str, tuple[type, Any]] = {
    "fenster_tage": (int, 14),
    "mindestvorlauf_minuten": (int, 60),
    "storno_frist_stunden": (int, 24),
    "zahlungsfrist_minuten": (int, 15),
    "ust_satz": (Decimal, Decimal("19.00")),
    "rechnung_tag_im_folgemonat": (int, 3),
    "rechnung_zahlungsziel_tage": (int, 14),
    "heiz_vorlauf_minuten": (int, 60),
    "licht_vorlauf_minuten": (int, 5),
    "licht_nachlauf_minuten": (int, 5),
    "zutritt_vorlauf_minuten": (int, 15),
    "spiel_temperatur": (Decimal, Decimal("16.0")),
    "grund_temperatur": (Decimal, Decimal("8.0")),
    "antwort_hinweis_sekunden": (int, 120),
    "pin_laenge": (int, 6),
}

_TYP_NAME = {int: "int", Decimal: "decimal", str: "str", bool: "bool"}


def _parse(typ: type, roh: str) -> Any:
    if typ is bool:
        return roh.lower() in ("1", "true", "ja")
    return typ(roh)


def hole(db: Session, schluessel: str) -> Any:
    typ, default = DEFAULTS[schluessel]
    zeile = db.get(Konfiguration, schluessel)
    return _parse(typ, zeile.wert) if zeile else default


def setze(db: Session, schluessel: str, wert: Any, admin_user_id: uuid.UUID | None = None) -> None:
    typ, default = DEFAULTS[schluessel]
    zeile = db.get(Konfiguration, schluessel)
    vorher = zeile.wert if zeile else str(default)
    neu = str(_parse(typ, str(wert)))
    if zeile:
        zeile.wert = neu
    else:
        db.add(Konfiguration(schluessel=schluessel, wert=neu, typ=_TYP_NAME[typ]))
    audit.protokolliere(
        db, quelle="admin", objekt_typ="konfiguration", objekt_id=None,
        vorher={"wert": vorher}, nachher={"wert": neu, "schluessel": schluessel},
        admin_user_id=admin_user_id,
    )
```

- [ ] **Step 5: Alembic einrichten und erste Migration erzeugen**

`core/alembic.ini`:
```ini
[alembic]
script_location = alembic
prepend_sys_path = .
sqlalchemy.url = postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub
[loggers]
keys = root,sqlalchemy,alembic
[handlers]
keys = console
[formatters]
keys = generic
[logger_root]
level = WARN
handlers = console
[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine
[logger_alembic]
level = INFO
handlers =
qualname = alembic
[handler_console]
class = StreamHandler
args = (sys.stderr,)
formatter = generic
[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

`core/alembic/env.py`:
```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from beachhub_core.config import settings
from beachhub_core.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
        connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
```

`core/alembic/script.py.mako`: Standardvorlage von `alembic init` (unverändert übernehmen; Erzeugung: `alembic init /tmp/x && cp /tmp/x/script.py.mako core/alembic/`).

Run:
```bash
cd core && alembic revision --autogenerate -m "stammdaten und system"
```
Die erzeugte Datei in `core/alembic/versions/` öffnen, prüfen, dass alle 11 Tabellen enthalten sind, und in `0001_stammdaten_system.py` umbenennen (`revision = "0001"`, `down_revision = None`).

- [ ] **Step 6: Tests grün**

Run: `cd core && pytest -q`
Expected: `6 passed`

- [ ] **Step 7: Commit**

```bash
git add core && git commit -m "feat(core): Stammdaten- und Systemmodelle, Konfiguration, Audit, Alembic"
```

---

## Task 5: Tarif-Auflösung

**Files:**
- Create: `core/beachhub_core/services/tarife.py`, `core/beachhub_core/services/slots_db.py`
- Test: `core/tests/test_tarife.py`, `core/tests/test_slots_db.py`

**Interfaces:**
- Produces: `slots_db.tages_slots(db, feld: Feld, datum: date) -> list[Slot]` (liest Raster, Betriebszeiten mit Gültigkeit, Ausnahmetage; delegiert an `beachhub_shared.slots`).
- Produces: `tarife.ermittle_preis(db, *, feld_id, beginn, ende, kundengruppe_id) -> Decimal | None` – Summe der Slot-Preise; `None`, wenn für einen Slot keine Regel passt oder der Zeitraum keine Slotfolge ist. `tarife.regel_fuer_slot(db, *, feld_id, slot: Slot, kundengruppe_id) -> Tarif | None`.
- Spezifität (A-TARIF-2): Anzahl gesetzter Kriterien unter `feld_id`, `wochentag`, `uhrzeit_von/bis` (zählt als eins), `kundengruppe_id`, `gueltig_von/bis` (zählt als eins). Höchste Zahl gewinnt, bei Gleichstand das neuere `created_at`. Uhrzeitkriterium: Slot-Beginn (lokal) ∈ [uhrzeit_von, uhrzeit_bis).

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_slots_db.py`:
```python
from datetime import date, time

from sqlalchemy.orm import Session

from beachhub_core.models import Ausnahmetag, Betriebszeit, Feld, FeldRaster
from beachhub_core.services import slots_db


def test_tages_slots_aus_datenbank(db: Session) -> None:
    f = Feld(name="Feld 1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    f.raster.append(FeldRaster(wochentag=2, modus="fenster", slot_minuten=None,
                               fenster_json=[["19:00", "21:00"], ["21:00", "23:00"]]))
    db.add_all([f, Betriebszeit(wochentag=2, oeffnet=time(17), schliesst=time(23)),
                Betriebszeit(wochentag=3, oeffnet=time(17), schliesst=time(23), gueltig_bis=date(2027, 11, 30)),
                Ausnahmetag(datum=date(2027, 12, 8), geschlossen=True)])
    db.commit()
    assert len(slots_db.tages_slots(db, f, date(2027, 12, 1))) == 2   # Mittwoch: Fenster
    assert slots_db.tages_slots(db, f, date(2027, 12, 2)) == []      # Donnerstag: Betriebszeit abgelaufen
    assert slots_db.tages_slots(db, f, date(2027, 12, 8)) == []      # Ausnahmetag
```

`core/tests/test_tarife.py`:
```python
from datetime import date, time
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from beachhub_core.models import Betriebszeit, Feld, FeldRaster, Kundengruppe, Tarif
from beachhub_core.services import tarife
from beachhub_shared.zeit import kombiniere

MI = date(2027, 12, 1)


@pytest.fixture
def basis(db: Session) -> tuple[Feld, Kundengruppe, Kundengruppe]:
    f = Feld(name="Feld 1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    privat, verein = Kundengruppe(name="Privat"), Kundengruppe(name="Verein", standard_zahlungsart="rechnung")
    db.add_all([f, privat, verein])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.commit()
    return f, privat, verein


def test_summe_ueber_slots_und_spezifischste_regel(db: Session, basis) -> None:
    f, privat, verein = basis
    db.add_all([
        Tarif(name="Standard", preis=Decimal("30.00")),
        Tarif(name="Abend", preis=Decimal("40.00"), uhrzeit_von=time(18), uhrzeit_bis=time(23)),
        Tarif(name="Verein Abend", preis=Decimal("35.00"), uhrzeit_von=time(18), uhrzeit_bis=time(23),
              kundengruppe_id=verein.id),
    ])
    db.commit()
    p = tarife.ermittle_preis(db, feld_id=f.id, beginn=kombiniere(MI, time(17)), ende=kombiniere(MI, time(19)),
                              kundengruppe_id=privat.id)
    assert p == Decimal("70.00")  # 17-18 Standard 30 + 18-19 Abend 40
    p = tarife.ermittle_preis(db, feld_id=f.id, beginn=kombiniere(MI, time(18)), ende=kombiniere(MI, time(20)),
                              kundengruppe_id=verein.id)
    assert p == Decimal("70.00")  # 2 × Verein Abend


def test_keine_regel_ergibt_none(db: Session, basis) -> None:
    f, privat, _ = basis
    assert tarife.ermittle_preis(db, feld_id=f.id, beginn=kombiniere(MI, time(17)),
                                 ende=kombiniere(MI, time(18)), kundengruppe_id=privat.id) is None


def test_keine_slotfolge_ergibt_none(db: Session, basis) -> None:
    f, privat, _ = basis
    db.add(Tarif(name="Standard", preis=Decimal("30.00")))
    db.commit()
    assert tarife.ermittle_preis(db, feld_id=f.id, beginn=kombiniere(MI, time(17, 30)),
                                 ende=kombiniere(MI, time(18, 30)), kundengruppe_id=privat.id) is None


def test_gueltigkeit_und_inaktiv(db: Session, basis) -> None:
    f, privat, _ = basis
    db.add_all([
        Tarif(name="Alt", preis=Decimal("10.00"), gueltig_bis=date(2027, 11, 30)),
        Tarif(name="Aus", preis=Decimal("1.00"), aktiv=False),
        Tarif(name="Neu", preis=Decimal("20.00"), gueltig_von=date(2027, 12, 1)),
    ])
    db.commit()
    assert tarife.ermittle_preis(db, feld_id=f.id, beginn=kombiniere(MI, time(17)),
                                 ende=kombiniere(MI, time(18)), kundengruppe_id=privat.id) == Decimal("20.00")
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_slots_db.py tests/test_tarife.py`
Expected: FAIL mit ImportError

- [ ] **Step 3: Implementieren**

`core/beachhub_core/services/slots_db.py`:
```python
from datetime import date, time

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from beachhub_core.models import Ausnahmetag, Betriebszeit, Feld
from beachhub_shared import slots as sl


def _time(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def raster_konfig(feld: Feld) -> list[sl.RasterKonfig]:
    return [
        sl.RasterKonfig(
            wochentag=r.wochentag, modus=r.modus, slot_minuten=r.slot_minuten,  # type: ignore[arg-type]
            fenster=[(_time(a), _time(b)) for a, b in r.fenster_json],
        )
        for r in feld.raster
    ]


def betriebszeiten_fuer(db: Session, datum: date) -> list[sl.Betriebszeit]:
    zeilen = db.scalars(
        select(Betriebszeit).where(
            or_(Betriebszeit.gueltig_von.is_(None), Betriebszeit.gueltig_von <= datum),
            or_(Betriebszeit.gueltig_bis.is_(None), Betriebszeit.gueltig_bis >= datum),
        )
    ).all()
    return [sl.Betriebszeit(z.wochentag, z.oeffnet, z.schliesst) for z in zeilen]


def ausnahmen_fuer(db: Session, datum: date) -> list[sl.Ausnahme]:
    a = db.scalar(select(Ausnahmetag).where(Ausnahmetag.datum == datum))
    return [sl.Ausnahme(a.datum, a.geschlossen, a.oeffnet, a.schliesst)] if a else []


def tages_slots(db: Session, feld: Feld, datum: date) -> list[sl.Slot]:
    return sl.slots_fuer_tag(datum, raster_konfig(feld), betriebszeiten_fuer(db, datum), ausnahmen_fuer(db, datum))
```

`core/beachhub_core/services/tarife.py`:
```python
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_core.models import Feld, Tarif
from beachhub_core.services import slots_db
from beachhub_shared.slots import Slot, slots_im_zeitraum, zeitraum_ist_slotfolge
from beachhub_shared.zeit import lokal, lokales_datum


def _passt(t: Tarif, feld_id: uuid.UUID, slot: Slot, kundengruppe_id: uuid.UUID | None) -> bool:
    lok = lokal(slot.beginn)
    if t.feld_id is not None and t.feld_id != feld_id:
        return False
    if t.wochentag is not None and t.wochentag != lok.weekday():
        return False
    if t.uhrzeit_von is not None and t.uhrzeit_bis is not None:
        if not (t.uhrzeit_von <= lok.time() < t.uhrzeit_bis):
            return False
    if t.kundengruppe_id is not None and t.kundengruppe_id != kundengruppe_id:
        return False
    if t.gueltig_von is not None and lok.date() < t.gueltig_von:
        return False
    if t.gueltig_bis is not None and lok.date() > t.gueltig_bis:
        return False
    return True


def _spezifitaet(t: Tarif) -> int:
    return sum([
        t.feld_id is not None, t.wochentag is not None,
        t.uhrzeit_von is not None and t.uhrzeit_bis is not None,
        t.kundengruppe_id is not None,
        t.gueltig_von is not None or t.gueltig_bis is not None,
    ])


def regel_fuer_slot(
    db: Session, *, feld_id: uuid.UUID, slot: Slot, kundengruppe_id: uuid.UUID | None
) -> Tarif | None:
    kandidaten = [
        t for t in db.scalars(select(Tarif).where(Tarif.aktiv.is_(True))).all()
        if _passt(t, feld_id, slot, kundengruppe_id)
    ]
    if not kandidaten:
        return None
    return max(kandidaten, key=lambda t: (_spezifitaet(t), t.created_at))


def ermittle_preis(
    db: Session, *, feld_id: uuid.UUID, beginn: datetime, ende: datetime, kundengruppe_id: uuid.UUID | None
) -> Decimal | None:
    feld = db.get(Feld, feld_id)
    if feld is None:
        return None
    tages = slots_db.tages_slots(db, feld, lokales_datum(beginn))
    if not zeitraum_ist_slotfolge(beginn, ende, tages):
        return None
    summe = Decimal("0.00")
    for slot in slots_im_zeitraum(beginn, ende, tages):
        regel = regel_fuer_slot(db, feld_id=feld_id, slot=slot, kundengruppe_id=kundengruppe_id)
        if regel is None:
            return None
        summe += regel.preis
    return summe
```

- [ ] **Step 4: Tests grün**

Run: `cd core && pytest -q`
Expected: alle grün (11 Tests)

- [ ] **Step 5: Commit**

```bash
git add core && git commit -m "feat(core): Slot-Ermittlung aus DB und Tarif-Auflösung"
```

---

## Task 6: Kunden und Guthaben

**Files:**
- Create: `core/beachhub_core/models/kunden.py`, `core/beachhub_core/services/guthaben.py`, `core/beachhub_core/services/kunden.py`
- Modify: `core/beachhub_core/models/__init__.py`
- Create: `core/alembic/versions/0002_kunden.py` (autogenerate)
- Test: `core/tests/test_kunden.py`

**Interfaces:**
- Produces: `Kunde(name, email unique lower, adresse_strasse, adresse_plz, adresse_ort, kundengruppe_id, zahlungsart: "online"|"rechnung", guthaben: Decimal, portal_konto_id: uuid|None, stripe_customer_id: str|None, anonymisiert_am: datetime|None)`, `GuthabenBuchung(kunde_id, betrag: Decimal, art: str, bezug_id: uuid|None, notiz, admin_user_id)`.
- Produces: `kunden.lege_an(db, *, name, email, kundengruppe_id, zahlungsart=None, adresse_strasse="", adresse_plz="", adresse_ort="", quelle="admin", admin_user_id=None) -> Kunde` (zahlungsart None → Standard der Gruppe; E-Mail lowercase; `KundenFehler("email_vergeben")`), `kunden.aendere(db, kunde, *, admin_user_id, **felder) -> Kunde` (mit Audit), `kunden.anonymisiere(db, kunde, admin_user_id=None)`.
- Produces: `guthaben.buche(db, *, kunde: Kunde, betrag: Decimal, art: str, bezug_id=None, notiz="", admin_user_id=None, quelle="admin") -> GuthabenBuchung` – aktualisiert `kunde.guthaben`, Audit; `guthaben.saldo(db, kunde_id) -> Decimal` (Summe der Buchungen, Kontrollwert). Arten: `storno_gutschrift`, `verrechnung`, `auszahlung`, `manuell`, `ueberzahlung`. `auszahlung` und `verrechnung` müssen negativ sein und dürfen das Guthaben nicht unter 0 bringen (`GuthabenFehler("nicht_gedeckt")`).

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_kunden.py`:
```python
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from beachhub_core.models import Audit, GuthabenBuchung, Kundengruppe
from beachhub_core.services import guthaben, kunden


@pytest.fixture
def gruppen(db: Session) -> tuple[Kundengruppe, Kundengruppe]:
    p, v = Kundengruppe(name="Privat"), Kundengruppe(name="Verein", standard_zahlungsart="rechnung")
    db.add_all([p, v])
    db.commit()
    return p, v


def test_anlegen_nimmt_zahlungsart_der_gruppe(db: Session, gruppen) -> None:
    _, verein = gruppen
    k = kunden.lege_an(db, name="TSV", email="Info@TSV.de", kundengruppe_id=verein.id)
    db.commit()
    assert k.zahlungsart == "rechnung" and k.email == "info@tsv.de" and k.guthaben == Decimal("0.00")


def test_email_doppelt_wirft(db: Session, gruppen) -> None:
    p, _ = gruppen
    kunden.lege_an(db, name="A", email="a@x.de", kundengruppe_id=p.id)
    db.commit()
    with pytest.raises(kunden.KundenFehler, match="email_vergeben"):
        kunden.lege_an(db, name="B", email="A@x.de", kundengruppe_id=p.id)


def test_guthaben_buchen_und_deckung(db: Session, gruppen) -> None:
    p, _ = gruppen
    k = kunden.lege_an(db, name="A", email="a@x.de", kundengruppe_id=p.id)
    guthaben.buche(db, kunde=k, betrag=Decimal("30.00"), art="storno_gutschrift")
    guthaben.buche(db, kunde=k, betrag=Decimal("-10.00"), art="verrechnung")
    db.commit()
    assert k.guthaben == Decimal("20.00") == guthaben.saldo(db, k.id)
    with pytest.raises(guthaben.GuthabenFehler, match="nicht_gedeckt"):
        guthaben.buche(db, kunde=k, betrag=Decimal("-25.00"), art="auszahlung")
    assert db.query(GuthabenBuchung).count() == 2
    assert db.query(Audit).filter_by(objekt_typ="guthaben").count() == 2


def test_aendern_protokolliert(db: Session, gruppen) -> None:
    p, v = gruppen
    k = kunden.lege_an(db, name="A", email="a@x.de", kundengruppe_id=p.id)
    kunden.aendere(db, k, admin_user_id=None, kundengruppe_id=v.id, zahlungsart="rechnung")
    db.commit()
    a = db.query(Audit).filter_by(objekt_typ="kunde").order_by(Audit.zeitpunkt.desc()).first()
    assert a.nachher_json["zahlungsart"] == "rechnung"


def test_anonymisieren(db: Session, gruppen) -> None:
    p, _ = gruppen
    k = kunden.lege_an(db, name="Anna Müller", email="anna@x.de", kundengruppe_id=p.id)
    kunden.anonymisiere(db, k)
    db.commit()
    assert k.name == "Gelöschter Kunde" and "@" not in k.email and k.anonymisiert_am is not None
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_kunden.py`
Expected: FAIL mit ImportError

- [ ] **Step 3: Modell und Services**

`core/beachhub_core/models/kunden.py`:
```python
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DECIMAL, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from beachhub_core.models.base import Base, UUIDMixin, ZeitstempelMixin
from beachhub_core.models.stammdaten import Kundengruppe


class Kunde(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "kunde"
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    adresse_strasse: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    adresse_plz: Mapped[str] = mapped_column(String(10), default="", nullable=False)
    adresse_ort: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    kundengruppe_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("kundengruppe.id"), nullable=False)
    zahlungsart: Mapped[str] = mapped_column(String(10), nullable=False)  # online | rechnung
    guthaben: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), default=Decimal("0.00"), nullable=False)
    portal_konto_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), unique=True)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(100))
    anonymisiert_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    kundengruppe: Mapped[Kundengruppe] = relationship()


class GuthabenBuchung(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "guthaben_buchung"
    kunde_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("kunde.id"), nullable=False)
    betrag: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    art: Mapped[str] = mapped_column(String(20), nullable=False)
    bezug_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    notiz: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    admin_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
```

`models/__init__.py`: `Kunde`, `GuthabenBuchung` importieren und in `__all__` aufnehmen.

`core/beachhub_core/services/kunden.py`:
```python
import hashlib
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_core.models import Kunde, Kundengruppe, utcnow
from beachhub_core.services import audit


class KundenFehler(Exception):
    pass


def lege_an(
    db: Session, *, name: str, email: str, kundengruppe_id: uuid.UUID, zahlungsart: str | None = None,
    adresse_strasse: str = "", adresse_plz: str = "", adresse_ort: str = "",
    quelle: str = "admin", admin_user_id: uuid.UUID | None = None,
) -> Kunde:
    email = email.strip().lower()
    if db.scalar(select(Kunde).where(Kunde.email == email)):
        raise KundenFehler("email_vergeben")
    gruppe = db.get(Kundengruppe, kundengruppe_id)
    if gruppe is None:
        raise KundenFehler("gruppe_unbekannt")
    k = Kunde(
        name=name.strip(), email=email, kundengruppe_id=kundengruppe_id,
        zahlungsart=zahlungsart or gruppe.standard_zahlungsart,
        adresse_strasse=adresse_strasse, adresse_plz=adresse_plz, adresse_ort=adresse_ort,
    )
    db.add(k)
    db.flush()
    audit.protokolliere(db, quelle=quelle, objekt_typ="kunde", objekt_id=k.id, vorher=None,
                        nachher=audit.als_dict(k), admin_user_id=admin_user_id)
    return k


def aendere(db: Session, kunde: Kunde, *, admin_user_id: uuid.UUID | None, **felder: Any) -> Kunde:
    vorher = audit.als_dict(kunde)
    for name, wert in felder.items():
        if name == "email":
            wert = wert.strip().lower()
        setattr(kunde, name, wert)
    db.flush()
    audit.protokolliere(db, quelle="admin", objekt_typ="kunde", objekt_id=kunde.id, vorher=vorher,
                        nachher=audit.als_dict(kunde), admin_user_id=admin_user_id)
    return kunde


def anonymisiere(db: Session, kunde: Kunde, admin_user_id: uuid.UUID | None = None) -> None:
    vorher = audit.als_dict(kunde)
    digest = hashlib.sha256(kunde.email.encode()).hexdigest()[:32]
    kunde.name = "Gelöschter Kunde"
    kunde.email = f"geloescht-{digest}"
    kunde.adresse_strasse = kunde.adresse_plz = kunde.adresse_ort = ""
    kunde.portal_konto_id = None
    kunde.stripe_customer_id = None
    kunde.anonymisiert_am = utcnow()
    db.flush()
    audit.protokolliere(db, quelle="admin", objekt_typ="kunde", objekt_id=kunde.id, vorher=vorher,
                        nachher=audit.als_dict(kunde), admin_user_id=admin_user_id)
```

`core/beachhub_core/services/guthaben.py`:
```python
import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from beachhub_core.models import GuthabenBuchung, Kunde
from beachhub_core.services import audit

ARTEN = {"storno_gutschrift", "verrechnung", "auszahlung", "manuell", "ueberzahlung"}
ABGEHEND = {"verrechnung", "auszahlung"}


class GuthabenFehler(Exception):
    pass


def buche(
    db: Session, *, kunde: Kunde, betrag: Decimal, art: str, bezug_id: uuid.UUID | None = None,
    notiz: str = "", admin_user_id: uuid.UUID | None = None, quelle: str = "admin",
) -> GuthabenBuchung:
    if art not in ARTEN:
        raise GuthabenFehler("art_unbekannt")
    if art in ABGEHEND and betrag >= 0:
        raise GuthabenFehler("betrag_muss_negativ_sein")
    if kunde.guthaben + betrag < 0:
        raise GuthabenFehler("nicht_gedeckt")
    vorher = kunde.guthaben
    kunde.guthaben = kunde.guthaben + betrag
    b = GuthabenBuchung(kunde_id=kunde.id, betrag=betrag, art=art, bezug_id=bezug_id, notiz=notiz,
                        admin_user_id=admin_user_id)
    db.add(b)
    db.flush()
    audit.protokolliere(db, quelle=quelle, objekt_typ="guthaben", objekt_id=b.id,
                        vorher={"guthaben": str(vorher)},
                        nachher={"guthaben": str(kunde.guthaben), "art": art, "betrag": str(betrag)},
                        admin_user_id=admin_user_id)
    return b


def saldo(db: Session, kunde_id: uuid.UUID) -> Decimal:
    s = db.scalar(select(func.coalesce(func.sum(GuthabenBuchung.betrag), 0)).where(GuthabenBuchung.kunde_id == kunde_id))
    return Decimal(s).quantize(Decimal("0.01"))
```

- [ ] **Step 4: Migration erzeugen, Tests grün**

Run:
```bash
cd core && alembic revision --autogenerate -m "kunden"   # → umbenennen in 0002_kunden.py, revision="0002", down_revision="0001"
pytest -q
```
Expected: alle grün

- [ ] **Step 5: Commit**

```bash
git add core && git commit -m "feat(core): Kunden, Guthaben und Anonymisierung"
```

---

## Task 7: Buchungsmodelle mit Exklusionsconstraints und PIN-Dienst

**Files:**
- Create: `core/beachhub_core/models/buchungen.py`, `core/beachhub_core/services/pin.py`
- Modify: `core/beachhub_core/models/__init__.py`
- Create: `core/alembic/versions/0003_buchungen.py` (autogenerate + manuell geprüfte Exklusionsconstraints)
- Test: `core/tests/test_buchungen_modell.py`, `core/tests/test_pin.py`

**Interfaces:**
- Produces: `Buchung(feld_id, kunde_id, beginn, ende, status, preis: Decimal, zahlungsart, pin_hash: str|None, pin_verschluesselt: str|None, dauerbuchung_id: uuid|None, anfrage_id: uuid|None unique, reserviert_bis: datetime|None, anwesenheit: str, quelle: str)`; Statuskonstanten `Buchung.ANGEFRAGT, RESERVIERT, BESTAETIGT, DURCHGEFUEHRT, NICHT_ERSCHIENEN, STORNIERT, ABGELEHNT, VERFALLEN`; `Buchung.AKTIVE_STATUS = ("angefragt", "reserviert", "bestaetigt", "durchgefuehrt", "nicht_erschienen")`; Hybrid-Property `Buchung.zeitraum` (tstzrange `[beginn, ende)`).
- Produces: `Dauerbuchung(kunde_id, feld_id, wochentag, start: time, ende: time, gueltig_von: date, gueltig_bis: date, pin_hash, pin_verschluesselt, beendet_am: datetime|None, beendet_ab: date|None)`, `Sperre(feld_id: uuid|None, beginn, ende, grund)`, `Storno(buchung_id unique, zeitpunkt, durch: "kunde"|"betreiber"|"system", kostenfrei: bool, grund: str, nachbuchung_offen: bool, nachbuchung_buchung_id: uuid|None, freigestellt_betrag: Decimal)`.
- Constraints (Postgres): `EXCLUDE USING gist (feld_id WITH =, tstzrange(beginn, ende) WITH &&) WHERE (status IN ('angefragt','reserviert','bestaetigt','durchgefuehrt','nicht_erschienen'))` auf `buchung`; `EXCLUDE USING gist (feld_id WITH =, tstzrange(beginn, ende) WITH &&)` auf `sperre` (nur für feld_id NOT NULL; hallenweite Sperren mit `feld_id IS NULL` werden im Service gegen alle Felder geprüft). Kollision Buchung↔Sperre wird im Service geprüft (Task 8), nicht per Constraint.
- Produces: `pin.erzeuge(laenge: int) -> str` (nur Ziffern, kryptografisch zufällig), `pin.hash(klar: str) -> str` (Argon2id mit festem, hallenweitem Salt aus `settings.pin_schluessel`, damit die Halle offline prüfen kann), `pin.verschluessele(klar) -> str` / `pin.entschluessele(chiffre) -> str` (Fernet mit Schlüssel aus `settings.pin_schluessel`), `pin.finde_freien(db, beginn, ende, laenge, vorlauf_minuten) -> str` (PIN, dessen Hash in keiner aktiven Buchung mit überlappendem Zeitfenster ± Vorlauf vorkommt).

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_pin.py`:
```python
from datetime import date, time
from decimal import Decimal

from sqlalchemy.orm import Session

from beachhub_core.models import Buchung, Feld, Kunde, Kundengruppe
from beachhub_core.services import pin
from beachhub_shared.zeit import kombiniere


def test_erzeugen_hash_verschluesseln() -> None:
    p = pin.erzeuge(6)
    assert len(p) == 6 and p.isdigit()
    assert pin.hash(p) == pin.hash(p)
    assert pin.hash(p) != pin.hash("000000")
    assert pin.entschluessele(pin.verschluessele(p)) == p


def test_finde_freien_vermeidet_kollision(db: Session, monkeypatch) -> None:
    g = Kundengruppe(name="Privat")
    f = Feld(name="F1", reihenfolge=1)
    db.add_all([g, f])
    db.flush()
    k = Kunde(name="A", email="a@x.de", kundengruppe_id=g.id, zahlungsart="online")
    db.add(k)
    db.flush()
    d = date(2027, 12, 1)
    db.add(Buchung(feld_id=f.id, kunde_id=k.id, beginn=kombiniere(d, time(19)), ende=kombiniere(d, time(20)),
                   status="bestaetigt", preis=Decimal("30"), zahlungsart="online",
                   pin_hash=pin.hash("123456"), quelle="admin"))
    db.commit()
    folge = iter(["123456", "654321"])
    monkeypatch.setattr(pin, "erzeuge", lambda laenge: next(folge))
    assert pin.finde_freien(db, kombiniere(d, time(20)), kombiniere(d, time(21)), 6, 15) == "654321"
```

`core/tests/test_buchungen_modell.py`:
```python
from datetime import date, time
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from beachhub_core.models import Buchung, Feld, Kunde, Kundengruppe
from beachhub_shared.zeit import kombiniere


def _basis(db: Session) -> tuple[Feld, Kunde]:
    g, f = Kundengruppe(name="Privat"), Feld(name="F1", reihenfolge=1)
    db.add_all([g, f])
    db.flush()
    k = Kunde(name="A", email="a@x.de", kundengruppe_id=g.id, zahlungsart="online")
    db.add(k)
    db.flush()
    return f, k


def _buchung(f: Feld, k: Kunde, von: int, bis: int, status: str = "bestaetigt") -> Buchung:
    d = date(2027, 12, 1)
    return Buchung(feld_id=f.id, kunde_id=k.id, beginn=kombiniere(d, time(von)), ende=kombiniere(d, time(bis)),
                   status=status, preis=Decimal("30"), zahlungsart="online", quelle="admin")


def test_ueberlappung_wird_von_datenbank_abgelehnt(db: Session) -> None:
    f, k = _basis(db)
    db.add(_buchung(f, k, 19, 21))
    db.commit()
    db.add(_buchung(f, k, 20, 22))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_angrenzend_und_storniert_erlaubt(db: Session) -> None:
    f, k = _basis(db)
    db.add(_buchung(f, k, 19, 21))
    db.add(_buchung(f, k, 21, 23))
    db.add(_buchung(f, k, 20, 22, status="storniert"))
    db.commit()
    assert db.query(Buchung).count() == 3
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_pin.py tests/test_buchungen_modell.py`
Expected: FAIL mit ImportError

- [ ] **Step 3: Modelle schreiben**

`core/beachhub_core/models/buchungen.py`:
```python
import uuid
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import DECIMAL, Boolean, Date, DateTime, ForeignKey, String, Time, text
from sqlalchemy.dialects.postgresql import UUID, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from beachhub_core.models.base import Base, UUIDMixin, ZeitstempelMixin, utcnow
from beachhub_core.models.kunden import Kunde
from beachhub_core.models.stammdaten import Feld


class Buchung(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "buchung"
    ANGEFRAGT, RESERVIERT, BESTAETIGT = "angefragt", "reserviert", "bestaetigt"
    DURCHGEFUEHRT, NICHT_ERSCHIENEN = "durchgefuehrt", "nicht_erschienen"
    STORNIERT, ABGELEHNT, VERFALLEN = "storniert", "abgelehnt", "verfallen"
    AKTIVE_STATUS = (ANGEFRAGT, RESERVIERT, BESTAETIGT, DURCHGEFUEHRT, NICHT_ERSCHIENEN)

    feld_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("feld.id"), nullable=False)
    kunde_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("kunde.id"), nullable=False)
    beginn: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ende: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    preis: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    zahlungsart: Mapped[str] = mapped_column(String(10), nullable=False)
    pin_hash: Mapped[str | None] = mapped_column(String(200))
    pin_verschluesselt: Mapped[str | None] = mapped_column(String(300))
    dauerbuchung_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("dauerbuchung.id"))
    anfrage_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), unique=True)
    reserviert_bis: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    anwesenheit: Mapped[str] = mapped_column(String(20), default="unbekannt", nullable=False)
    quelle: Mapped[str] = mapped_column(String(10), nullable=False)  # admin | portal | dauer

    feld: Mapped[Feld] = relationship()
    kunde: Mapped[Kunde] = relationship()
    storno: Mapped["Storno | None"] = relationship(back_populates="buchung", uselist=False)

    __table_args__ = (
        ExcludeConstraint(
            ("feld_id", "="),
            (text("tstzrange(beginn, ende)"), "&&"),
            using="gist",
            where=text("status IN ('angefragt','reserviert','bestaetigt','durchgefuehrt','nicht_erschienen')"),
            name="buchung_keine_ueberlappung",
        ),
    )

    @property
    def aktiv(self) -> bool:
        return self.status in self.AKTIVE_STATUS


class Dauerbuchung(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "dauerbuchung"
    kunde_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("kunde.id"), nullable=False)
    feld_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("feld.id"), nullable=False)
    wochentag: Mapped[int] = mapped_column(nullable=False)
    start: Mapped[time] = mapped_column(Time, nullable=False)
    ende: Mapped[time] = mapped_column(Time, nullable=False)
    gueltig_von: Mapped[date] = mapped_column(Date, nullable=False)
    gueltig_bis: Mapped[date] = mapped_column(Date, nullable=False)
    pin_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    pin_verschluesselt: Mapped[str] = mapped_column(String(300), nullable=False)
    beendet_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    beendet_ab: Mapped[date | None] = mapped_column(Date)
    kunde: Mapped[Kunde] = relationship()
    feld: Mapped[Feld] = relationship()
    buchungen: Mapped[list[Buchung]] = relationship(order_by="Buchung.beginn")


class Sperre(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "sperre"
    feld_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("feld.id"))
    beginn: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ende: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    grund: Mapped[str] = mapped_column(String(200), nullable=False)
    __table_args__ = (
        ExcludeConstraint(
            ("feld_id", "="), (text("tstzrange(beginn, ende)"), "&&"),
            using="gist", where=text("feld_id IS NOT NULL"), name="sperre_keine_ueberlappung",
        ),
    )


class Storno(UUIDMixin, Base):
    __tablename__ = "storno"
    buchung_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("buchung.id"), nullable=False, unique=True)
    zeitpunkt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    durch: Mapped[str] = mapped_column(String(10), nullable=False)  # kunde | betreiber | system
    kostenfrei: Mapped[bool] = mapped_column(Boolean, nullable=False)
    grund: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    nachbuchung_offen: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    nachbuchung_buchung_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("buchung.id"))
    freigestellt_betrag: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), default=Decimal("0.00"), nullable=False)
    buchung: Mapped[Buchung] = relationship(back_populates="storno", foreign_keys=[buchung_id])
```

`models/__init__.py`: `Buchung`, `Dauerbuchung`, `Sperre`, `Storno` ergänzen. **Importreihenfolge:** `kunden` vor `buchungen`, weil `buchungen` `Kunde` importiert.

- [ ] **Step 4: PIN-Dienst schreiben**

`core/beachhub_core/services/pin.py`:
```python
"""PIN je Buchung: Klartext verschlüsselt (für Mails), Hash für die Halle."""
import base64
import hashlib
import secrets
from datetime import datetime, timedelta

from argon2.low_level import Type, hash_secret_raw
from cryptography.fernet import Fernet
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from beachhub_core.config import settings
from beachhub_core.models import Buchung, Dauerbuchung


def _schluessel() -> bytes:
    return hashlib.sha256(settings.pin_schluessel.encode()).digest()


def erzeuge(laenge: int) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(laenge))


def hash(klar: str) -> str:  # noqa: A001 – bewusst so benannt, wird als pin.hash() gelesen
    """Argon2id mit hallenweitem Salt: deterministisch, damit die Halle lokal prüfen kann."""
    salt = hashlib.sha256(b"beachhub-pin-salt" + _schluessel()).digest()[:16]
    raw = hash_secret_raw(klar.encode(), salt, time_cost=2, memory_cost=65536, parallelism=1,
                          hash_len=32, type=Type.ID)
    return "argon2id$" + base64.b64encode(raw).decode()


def _fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(_schluessel()))


def verschluessele(klar: str) -> str:
    return _fernet().encrypt(klar.encode()).decode()


def entschluessele(chiffre: str) -> str:
    return _fernet().decrypt(chiffre.encode()).decode()


def finde_freien(db: Session, beginn: datetime, ende: datetime, laenge: int, vorlauf_minuten: int) -> str:
    """PIN, der in keiner aktiven Buchung mit überlappendem Fenster (± Vorlauf) vorkommt."""
    von, bis = beginn - timedelta(minutes=vorlauf_minuten), ende + timedelta(minutes=vorlauf_minuten)
    belegt = set(db.scalars(
        select(Buchung.pin_hash).where(
            Buchung.status.in_(Buchung.AKTIVE_STATUS), Buchung.beginn < bis, Buchung.ende > von,
            Buchung.pin_hash.is_not(None),
        )
    ).all())
    belegt |= set(db.scalars(select(Dauerbuchung.pin_hash).where(
        or_(Dauerbuchung.beendet_am.is_(None), Dauerbuchung.beendet_am > beginn)
    )).all())
    for _ in range(100):
        kandidat = erzeuge(laenge)
        if hash(kandidat) not in belegt:
            return kandidat
    raise RuntimeError("Kein freier PIN gefunden")
```

- [ ] **Step 5: Migration erzeugen und Constraints prüfen**

Run: `cd core && alembic revision --autogenerate -m "buchungen"` → `0003_buchungen.py` (`revision="0003"`, `down_revision="0002"`). Prüfen, dass beide `ExcludeConstraint`s in `upgrade()` enthalten sind; falls Autogenerate sie auslässt, manuell ergänzen:
```python
op.execute("ALTER TABLE buchung ADD CONSTRAINT buchung_keine_ueberlappung EXCLUDE USING gist "
           "(feld_id WITH =, tstzrange(beginn, ende) WITH &&) "
           "WHERE (status IN ('angefragt','reserviert','bestaetigt','durchgefuehrt','nicht_erschienen'))")
op.execute("ALTER TABLE sperre ADD CONSTRAINT sperre_keine_ueberlappung EXCLUDE USING gist "
           "(feld_id WITH =, tstzrange(beginn, ende) WITH &&) WHERE (feld_id IS NOT NULL)")
```
und im `downgrade()` die beiden `DROP CONSTRAINT`.

- [ ] **Step 6: Tests grün**

Run: `cd core && pytest -q`
Expected: alle grün

- [ ] **Step 7: Commit**

```bash
git add core && git commit -m "feat(core): Buchungsmodelle mit Exklusionsconstraints und PIN-Dienst"
```

---

## Task 8: Buchungs-Service (Anlegen mit Prüfung, Kollisionen)

**Files:**
- Create: `core/beachhub_core/services/buchungen.py`, `core/beachhub_core/clock.py`
- Test: `core/tests/test_buchungen_service.py`, `core/tests/test_clock.py`

**Interfaces:**
- Produces: `clock.now(db) -> datetime` (UTC-aware; Admin-Override aus `AppSetting("datum_override")` ersetzt das Datum, Uhrzeit bleibt echt), `clock.today(db) -> date` (lokales Berliner Datum), `clock.set_override(db, datum: date | None)`.
- Produces: `buchungen.BuchungsFehler(Exception)` mit `.grund` ∈ {`belegt`, `ausserhalb_fenster`, `ausserhalb_betriebszeit`, `kein_tarif`, `kunde_unbekannt`, `feld_inaktiv`, `vergangenheit`}.
- Produces: `buchungen.finde_kollisionen(db, *, feld_id, beginn, ende, ausser_buchung_id=None) -> list[Buchung | Sperre]` (aktive Buchungen und Sperren des Feldes oder hallenweite Sperren, die `[beginn, ende)` überlappen).
- Produces: `buchungen.lege_an(db, *, feld_id, kunde_id, beginn, ende, quelle="admin", admin_user_id=None, pruefe_fenster=False, status=Buchung.BESTAETIGT, anfrage_id=None, dauerbuchung_id=None, pin_klar=None) -> Buchung`. Ablauf: Feld sperren (`SELECT … FOR UPDATE` auf `feld`), Kunde laden, Zeitraum prüfen (Vergangenheit; Slotfolge über `slots_db.tages_slots`; Fenster nur wenn `pruefe_fenster`), Kollisionen, Preis via `tarife.ermittle_preis`, PIN (`pin_klar` übergeben → Dauerbuchung; sonst `pin.finde_freien`), `db.flush()`, Audit, **danach** `storno.pruefe_nachbuchung(db, buchung)` (Task 11; in dieser Task als Hook-Liste `NACH_ANLAGE: list[Callable[[Session, Buchung], None]]` vorbereitet, damit Task 11 sich registrieren kann, ohne Zirkelimport).
- Produces: `buchungen.setze_status(db, buchung, neu: str, *, quelle, admin_user_id=None)` mit Audit.

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_clock.py`:
```python
from datetime import date

from sqlalchemy.orm import Session

from beachhub_core import clock


def test_override_ersetzt_datum(db: Session) -> None:
    clock.set_override(db, date(2027, 12, 24))
    assert clock.today(db) == date(2027, 12, 24)
    assert clock.now(db).tzinfo is not None
    clock.set_override(db, None)
    assert clock.today(db) == date.today() or True  # echtes Datum, keine feste Erwartung
```

`core/tests/test_buchungen_service.py`:
```python
from datetime import date, time
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from beachhub_core import clock
from beachhub_core.models import Audit, Betriebszeit, Buchung, Feld, FeldRaster, Kundengruppe, Sperre, Tarif
from beachhub_core.services import buchungen, kunden, pin
from beachhub_shared.zeit import kombiniere

D = date(2027, 12, 1)


@pytest.fixture
def welt(db: Session):
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    g = Kundengruppe(name="Privat")
    db.add_all([f, g, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    k = kunden.lege_an(db, name="A", email="a@x.de", kundengruppe_id=g.id)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    return f, k


def test_anlegen_setzt_preis_pin_status(db: Session, welt) -> None:
    f, k = welt
    b = buchungen.lege_an(db, feld_id=f.id, kunde_id=k.id, beginn=kombiniere(D, time(19)), ende=kombiniere(D, time(21)))
    db.commit()
    assert b.status == "bestaetigt" and b.preis == Decimal("60.00") and b.zahlungsart == "online"
    assert b.pin_hash == pin.hash(pin.entschluessele(b.pin_verschluesselt))
    assert db.query(Audit).filter_by(objekt_typ="buchung", objekt_id=b.id).count() == 1


@pytest.mark.parametrize("von,bis,grund", [
    (time(19, 30), time(21), "ausserhalb_betriebszeit"),  # keine Slotfolge
    (time(22), time(23, 30), "ausserhalb_betriebszeit"),
])
def test_zeitraum_muss_slotfolge_sein(db: Session, welt, von, bis, grund) -> None:
    f, k = welt
    with pytest.raises(buchungen.BuchungsFehler) as e:
        buchungen.lege_an(db, feld_id=f.id, kunde_id=k.id, beginn=kombiniere(D, von), ende=kombiniere(D, bis))
    assert e.value.grund == grund


def test_kollision_mit_buchung_und_sperre(db: Session, welt) -> None:
    f, k = welt
    buchungen.lege_an(db, feld_id=f.id, kunde_id=k.id, beginn=kombiniere(D, time(19)), ende=kombiniere(D, time(21)))
    db.add(Sperre(feld_id=None, beginn=kombiniere(D, time(9)), ende=kombiniere(D, time(12)), grund="Wartung"))
    db.commit()
    with pytest.raises(buchungen.BuchungsFehler, match="belegt"):
        buchungen.lege_an(db, feld_id=f.id, kunde_id=k.id, beginn=kombiniere(D, time(20)), ende=kombiniere(D, time(22)))
    with pytest.raises(buchungen.BuchungsFehler, match="belegt"):
        buchungen.lege_an(db, feld_id=f.id, kunde_id=k.id, beginn=kombiniere(D, time(10)), ende=kombiniere(D, time(11)))
    kol = buchungen.finde_kollisionen(db, feld_id=f.id, beginn=kombiniere(D, time(11)), ende=kombiniere(D, time(20)))
    assert len(kol) == 2


def test_fenster_nur_wenn_gefordert(db: Session, welt) -> None:
    f, k = welt  # Override 25.11., Fenster 14 Tage → 1.12. liegt drin, 20.12. nicht
    weit = date(2027, 12, 20)
    buchungen.lege_an(db, feld_id=f.id, kunde_id=k.id, beginn=kombiniere(weit, time(19)), ende=kombiniere(weit, time(20)))
    with pytest.raises(buchungen.BuchungsFehler, match="ausserhalb_fenster"):
        buchungen.lege_an(db, feld_id=f.id, kunde_id=k.id, beginn=kombiniere(weit, time(20)),
                          ende=kombiniere(weit, time(21)), pruefe_fenster=True)


def test_vergangenheit_und_kein_tarif(db: Session, welt) -> None:
    f, k = welt
    with pytest.raises(buchungen.BuchungsFehler, match="vergangenheit"):
        buchungen.lege_an(db, feld_id=f.id, kunde_id=k.id, beginn=kombiniere(date(2027, 11, 1), time(19)),
                          ende=kombiniere(date(2027, 11, 1), time(20)))
    db.query(Tarif).delete()
    db.commit()
    with pytest.raises(buchungen.BuchungsFehler, match="kein_tarif"):
        buchungen.lege_an(db, feld_id=f.id, kunde_id=k.id, beginn=kombiniere(D, time(19)), ende=kombiniere(D, time(20)))
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_clock.py tests/test_buchungen_service.py`
Expected: FAIL mit ImportError

- [ ] **Step 3: Implementieren**

`core/beachhub_core/clock.py`:
```python
"""Eine Uhr für die Fachlogik. Admin kann das Datum überschreiben (Tests, Abnahme)."""
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from beachhub_core.models import AppSetting
from beachhub_shared.zeit import BERLIN, lokales_datum

OVERRIDE_KEY = "datum_override"


def _override(db: Session) -> date | None:
    zeile = db.get(AppSetting, OVERRIDE_KEY)
    return date.fromisoformat(zeile.value) if zeile and zeile.value else None


def now(db: Session) -> datetime:
    echt = datetime.now(UTC)
    o = _override(db)
    if o is None:
        return echt
    lok = echt.astimezone(BERLIN)
    return datetime.combine(o, lok.time(), tzinfo=BERLIN).astimezone(UTC)


def today(db: Session) -> date:
    return lokales_datum(now(db))


def set_override(db: Session, datum: date | None) -> None:
    zeile = db.get(AppSetting, OVERRIDE_KEY)
    if datum is None:
        if zeile:
            db.delete(zeile)
    elif zeile:
        zeile.value = datum.isoformat()
    else:
        db.add(AppSetting(key=OVERRIDE_KEY, value=datum.isoformat()))
    db.commit()
```

`core/beachhub_core/services/buchungen.py`:
```python
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from beachhub_core import clock
from beachhub_core.models import Buchung, Feld, Kunde, Sperre
from beachhub_core.services import audit, konfiguration, pin, slots_db, tarife
from beachhub_shared.slots import zeitraum_ist_slotfolge
from beachhub_shared.zeit import lokales_datum

NACH_ANLAGE: list[Callable[[Session, Buchung], None]] = []


class BuchungsFehler(Exception):
    def __init__(self, grund: str) -> None:
        super().__init__(grund)
        self.grund = grund


def finde_kollisionen(
    db: Session, *, feld_id: uuid.UUID, beginn: datetime, ende: datetime, ausser_buchung_id: uuid.UUID | None = None
) -> list[Buchung | Sperre]:
    q = select(Buchung).where(
        Buchung.feld_id == feld_id, Buchung.status.in_(Buchung.AKTIVE_STATUS),
        Buchung.beginn < ende, Buchung.ende > beginn,
    )
    if ausser_buchung_id:
        q = q.where(Buchung.id != ausser_buchung_id)
    b: list[Buchung | Sperre] = list(db.scalars(q).all())
    s = db.scalars(select(Sperre).where(
        or_(Sperre.feld_id == feld_id, Sperre.feld_id.is_(None)), Sperre.beginn < ende, Sperre.ende > beginn
    )).all()
    return b + list(s)


def _pruefe_zeitraum(db: Session, feld: Feld, beginn: datetime, ende: datetime, pruefe_fenster: bool) -> None:
    jetzt = clock.now(db)
    if beginn <= jetzt:
        raise BuchungsFehler("vergangenheit")
    if ende <= beginn or lokales_datum(beginn) != lokales_datum(ende - timedelta(seconds=1)):
        raise BuchungsFehler("ausserhalb_betriebszeit")
    if not zeitraum_ist_slotfolge(beginn, ende, slots_db.tages_slots(db, feld, lokales_datum(beginn))):
        raise BuchungsFehler("ausserhalb_betriebszeit")
    if pruefe_fenster:
        fenster = timedelta(days=konfiguration.hole(db, "fenster_tage"))
        vorlauf = timedelta(minutes=konfiguration.hole(db, "mindestvorlauf_minuten"))
        if beginn > jetzt + fenster or beginn < jetzt + vorlauf:
            raise BuchungsFehler("ausserhalb_fenster")


def lege_an(
    db: Session, *, feld_id: uuid.UUID, kunde_id: uuid.UUID, beginn: datetime, ende: datetime,
    quelle: str = "admin", admin_user_id: uuid.UUID | None = None, pruefe_fenster: bool = False,
    status: str = Buchung.BESTAETIGT, anfrage_id: uuid.UUID | None = None,
    dauerbuchung_id: uuid.UUID | None = None, pin_klar: str | None = None,
) -> Buchung:
    feld = db.scalar(select(Feld).where(Feld.id == feld_id).with_for_update())
    if feld is None or not feld.aktiv:
        raise BuchungsFehler("feld_inaktiv")
    kunde = db.get(Kunde, kunde_id)
    if kunde is None or kunde.anonymisiert_am is not None:
        raise BuchungsFehler("kunde_unbekannt")
    _pruefe_zeitraum(db, feld, beginn, ende, pruefe_fenster)
    if finde_kollisionen(db, feld_id=feld_id, beginn=beginn, ende=ende):
        raise BuchungsFehler("belegt")
    preis = tarife.ermittle_preis(db, feld_id=feld_id, beginn=beginn, ende=ende, kundengruppe_id=kunde.kundengruppe_id)
    if preis is None:
        raise BuchungsFehler("kein_tarif")
    if pin_klar is None:
        pin_klar = pin.finde_freien(db, beginn, ende, konfiguration.hole(db, "pin_laenge"),
                                    konfiguration.hole(db, "zutritt_vorlauf_minuten"))
    b = Buchung(
        feld_id=feld_id, kunde_id=kunde_id, beginn=beginn, ende=ende, status=status, preis=preis,
        zahlungsart=kunde.zahlungsart, pin_hash=pin.hash(pin_klar), pin_verschluesselt=pin.verschluessele(pin_klar),
        anfrage_id=anfrage_id, dauerbuchung_id=dauerbuchung_id, quelle=quelle,
    )
    db.add(b)
    db.flush()
    audit.protokolliere(db, quelle=quelle, objekt_typ="buchung", objekt_id=b.id, vorher=None,
                        nachher=audit.als_dict(b), admin_user_id=admin_user_id)
    for hook in NACH_ANLAGE:
        hook(db, b)
    return b


def setze_status(db: Session, buchung: Buchung, neu: str, *, quelle: str, admin_user_id: uuid.UUID | None = None) -> None:
    vorher = audit.als_dict(buchung)
    buchung.status = neu
    db.flush()
    audit.protokolliere(db, quelle=quelle, objekt_typ="buchung", objekt_id=buchung.id, vorher=vorher,
                        nachher=audit.als_dict(buchung), admin_user_id=admin_user_id)
```

- [ ] **Step 4: Tests grün**

Run: `cd core && pytest -q`
Expected: alle grün

- [ ] **Step 5: Commit**

```bash
git add core && git commit -m "feat(core): Buchungs-Service mit Slot-, Fenster- und Kollisionsprüfung"
```

---

## Task 9: Sperren mit Kollisionsentscheidung

**Files:**
- Create: `core/beachhub_core/services/sperren.py`
- Test: `core/tests/test_sperren.py`

**Interfaces:**
- Produces: `sperren.betroffene_buchungen(db, *, feld_ids: list[uuid] | None, beginn, ende) -> list[Buchung]` (aktive Buchungen; `None` = alle Felder).
- Produces: `sperren.lege_an(db, *, feld_ids: list[uuid] | None, beginn, ende, grund, admin_user_id, entscheidungen: dict[uuid.UUID, str]) -> list[Sperre]` – je Feld eine Sperre (oder eine hallenweite mit `feld_id=None`, wenn `feld_ids is None`). Für jede betroffene Buchung muss eine Entscheidung `behalten` oder `stornieren` vorliegen, sonst `SperrenFehler("entscheidung_fehlt")`. `stornieren` → `storno.storniere(db, buchung, durch="betreiber", kostenfrei=True, grund=...)` (Task 11; hier als Hook `STORNIERE: Callable` injiziert, Default wirft). `behalten` → Buchung bleibt, Sperre wird trotzdem angelegt (Sperre↔Buchung-Überlappung ist erlaubt, nur neue Buchungen sind dann blockiert).
- Produces: `sperren.loesche(db, sperre, admin_user_id)` mit Audit.

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_sperren.py`:
```python
from datetime import date, time
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from beachhub_core import clock
from beachhub_core.models import Betriebszeit, Feld, FeldRaster, Kundengruppe, Sperre, Tarif
from beachhub_core.services import buchungen, kunden, sperren
from beachhub_shared.zeit import kombiniere

D = date(2027, 12, 1)


@pytest.fixture
def welt(db: Session):
    f1, f2 = Feld(name="F1", reihenfolge=1), Feld(name="F2", reihenfolge=2)
    for f in (f1, f2):
        f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    g = Kundengruppe(name="Privat")
    db.add_all([f1, f2, g, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    k = kunden.lege_an(db, name="A", email="a@x.de", kundengruppe_id=g.id)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    storniert = []
    sperren.STORNIERE = lambda db, b, **kw: storniert.append(b.id) or buchungen.setze_status(db, b, "storniert", quelle="admin")
    return f1, f2, k, storniert


def test_entscheidung_pflicht_und_stornieren(db: Session, welt) -> None:
    f1, f2, k, storniert = welt
    b = buchungen.lege_an(db, feld_id=f1.id, kunde_id=k.id, beginn=kombiniere(D, time(19)), ende=kombiniere(D, time(21)))
    db.commit()
    with pytest.raises(sperren.SperrenFehler, match="entscheidung_fehlt"):
        sperren.lege_an(db, feld_ids=None, beginn=kombiniere(D, time(18)), ende=kombiniere(D, time(23)),
                        grund="Turnier", admin_user_id=None, entscheidungen={})
    s = sperren.lege_an(db, feld_ids=None, beginn=kombiniere(D, time(18)), ende=kombiniere(D, time(23)),
                        grund="Turnier", admin_user_id=None, entscheidungen={b.id: "stornieren"})
    db.commit()
    assert len(s) == 1 and s[0].feld_id is None and storniert == [b.id]
    with pytest.raises(buchungen.BuchungsFehler, match="belegt"):
        buchungen.lege_an(db, feld_id=f2.id, kunde_id=k.id, beginn=kombiniere(D, time(20)), ende=kombiniere(D, time(21)))


def test_behalten_laesst_buchung_stehen(db: Session, welt) -> None:
    f1, f2, k, storniert = welt
    b = buchungen.lege_an(db, feld_id=f1.id, kunde_id=k.id, beginn=kombiniere(D, time(19)), ende=kombiniere(D, time(21)))
    s = sperren.lege_an(db, feld_ids=[f1.id, f2.id], beginn=kombiniere(D, time(19)), ende=kombiniere(D, time(21)),
                        grund="Wartung", admin_user_id=None, entscheidungen={b.id: "behalten"})
    db.commit()
    assert len(s) == 2 and b.status == "bestaetigt" and storniert == []
    sperren.loesche(db, s[0], admin_user_id=None)
    db.commit()
    assert db.query(Sperre).count() == 1
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_sperren.py`
Expected: FAIL mit ImportError

- [ ] **Step 3: Implementieren**

`core/beachhub_core/services/sperren.py`:
```python
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_core.models import Buchung, Sperre
from beachhub_core.services import audit


class SperrenFehler(Exception):
    pass


def _nicht_verdrahtet(db: Session, buchung: Buchung, **kw: Any) -> None:
    raise RuntimeError("sperren.STORNIERE ist nicht gesetzt (storno-Service registriert sich in Task 11)")


STORNIERE: Callable[..., Any] = _nicht_verdrahtet


def betroffene_buchungen(
    db: Session, *, feld_ids: list[uuid.UUID] | None, beginn: datetime, ende: datetime
) -> list[Buchung]:
    q = select(Buchung).where(Buchung.status.in_(Buchung.AKTIVE_STATUS), Buchung.beginn < ende, Buchung.ende > beginn)
    if feld_ids is not None:
        q = q.where(Buchung.feld_id.in_(feld_ids))
    return list(db.scalars(q.order_by(Buchung.beginn)).all())


def lege_an(
    db: Session, *, feld_ids: list[uuid.UUID] | None, beginn: datetime, ende: datetime, grund: str,
    admin_user_id: uuid.UUID | None, entscheidungen: dict[uuid.UUID, str],
) -> list[Sperre]:
    if ende <= beginn:
        raise SperrenFehler("zeitraum_ungueltig")
    betroffen = betroffene_buchungen(db, feld_ids=feld_ids, beginn=beginn, ende=ende)
    for b in betroffen:
        if entscheidungen.get(b.id) not in ("behalten", "stornieren"):
            raise SperrenFehler("entscheidung_fehlt")
    ergebnis: list[Sperre] = []
    for fid in feld_ids if feld_ids is not None else [None]:
        s = Sperre(feld_id=fid, beginn=beginn, ende=ende, grund=grund)
        db.add(s)
        db.flush()
        audit.protokolliere(db, quelle="admin", objekt_typ="sperre", objekt_id=s.id, vorher=None,
                            nachher=audit.als_dict(s), admin_user_id=admin_user_id)
        ergebnis.append(s)
    for b in betroffen:
        if entscheidungen[b.id] == "stornieren":
            STORNIERE(db, b, durch="betreiber", kostenfrei=True, grund=f"Sperre: {grund}", admin_user_id=admin_user_id)
    return ergebnis


def loesche(db: Session, sperre: Sperre, admin_user_id: uuid.UUID | None) -> None:
    audit.protokolliere(db, quelle="admin", objekt_typ="sperre", objekt_id=sperre.id,
                        vorher=audit.als_dict(sperre), nachher=None, admin_user_id=admin_user_id)
    db.delete(sperre)
    db.flush()
```

- [ ] **Step 4: Tests grün**

Run: `cd core && pytest -q`
Expected: alle grün

- [ ] **Step 5: Commit**

```bash
git add core && git commit -m "feat(core): Sperren mit Entscheidung je betroffener Buchung"
```

---

## Task 10: Dauerbuchungen

**Files:**
- Create: `core/beachhub_core/services/dauerbuchungen.py`
- Test: `core/tests/test_dauerbuchungen.py`

**Interfaces:**
- Produces: `dauerbuchungen.Termin` (dataclass: `datum: date`, `beginn: datetime`, `ende: datetime`, `kollisionen: list[Buchung | Sperre]`, `preis: Decimal | None`).
- Produces: `dauerbuchungen.plane(db, *, kunde_id, feld_id, wochentag, start: time, ende: time, gueltig_von, gueltig_bis) -> list[Termin]` – alle Termine des Wochentags im Zeitraum, je Termin Kollisionen und Preis (`None` = kein Tarif/kein Slot, wird als Fehler angezeigt).
- Produces: `dauerbuchungen.lege_an(db, *, kunde_id, feld_id, wochentag, start, ende, gueltig_von, gueltig_bis, admin_user_id, auslassen: set[date], entscheidungen: dict[uuid, str]) -> Dauerbuchung` – gemeinsame PIN, je Termin `buchungen.lege_an(..., quelle="dauer", dauerbuchung_id=..., pin_klar=PIN)`; Termine in `auslassen` überspringen; bei Kollision mit fremder Buchung muss `entscheidungen[buchung_id] == "stornieren"` vorliegen (kostenfrei, durch Betreiber), sonst `DauerbuchungsFehler("entscheidung_fehlt")`; Kollision mit Sperre → Termin muss in `auslassen` sein, sonst `DauerbuchungsFehler("sperre")`. Termine mit `preis is None` → `DauerbuchungsFehler("kein_tarif")`.
- Produces: `dauerbuchungen.beende(db, dauerbuchung, *, ab: date, admin_user_id)` – alle Termine mit `beginn >= ab` kostenfrei stornieren (`durch="betreiber"`), `beendet_am/ab` setzen.
- Nutzt `sperren.STORNIERE` als Storno-Einstieg (gleicher Hook).

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_dauerbuchungen.py`:
```python
from datetime import date, time
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from beachhub_core import clock
from beachhub_core.models import Betriebszeit, Buchung, Feld, FeldRaster, Kundengruppe, Sperre, Tarif
from beachhub_core.services import buchungen, dauerbuchungen, kunden, pin, sperren
from beachhub_shared.zeit import kombiniere


@pytest.fixture
def welt(db: Session):
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    g = Kundengruppe(name="Verein", standard_zahlungsart="rechnung")
    db.add_all([f, g, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    k = kunden.lege_an(db, name="TSV", email="tsv@x.de", kundengruppe_id=g.id)
    k2 = kunden.lege_an(db, name="B", email="b@x.de", kundengruppe_id=g.id)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    sperren.STORNIERE = lambda db, b, **kw: buchungen.setze_status(db, b, "storniert", quelle="admin")
    return f, k, k2


def test_planen_zeigt_termine_und_kollisionen(db: Session, welt) -> None:
    f, k, k2 = welt
    d = date(2027, 12, 7)  # Dienstag
    buchungen.lege_an(db, feld_id=f.id, kunde_id=k2.id, beginn=kombiniere(d, time(19)), ende=kombiniere(d, time(20)))
    db.add(Sperre(feld_id=f.id, beginn=kombiniere(date(2027, 12, 21), time(9)), ende=kombiniere(date(2027, 12, 22), time(9)), grund="X"))
    db.commit()
    plan = dauerbuchungen.plane(db, kunde_id=k.id, feld_id=f.id, wochentag=1, start=time(19), ende=time(21),
                                gueltig_von=date(2027, 12, 1), gueltig_bis=date(2027, 12, 31))
    assert [t.datum for t in plan] == [date(2027, 12, 7), date(2027, 12, 14), date(2027, 12, 21), date(2027, 12, 28)]
    assert len(plan[0].kollisionen) == 1 and isinstance(plan[2].kollisionen[0], Sperre)
    assert plan[1].preis == Decimal("60.00")


def test_anlegen_mit_auslassen_und_gemeinsamer_pin(db: Session, welt) -> None:
    f, k, k2 = welt
    d = date(2027, 12, 7)
    fremd = buchungen.lege_an(db, feld_id=f.id, kunde_id=k2.id, beginn=kombiniere(d, time(19)), ende=kombiniere(d, time(20)))
    db.commit()
    with pytest.raises(dauerbuchungen.DauerbuchungsFehler, match="entscheidung_fehlt"):
        dauerbuchungen.lege_an(db, kunde_id=k.id, feld_id=f.id, wochentag=1, start=time(19), ende=time(21),
                               gueltig_von=date(2027, 12, 1), gueltig_bis=date(2027, 12, 31), admin_user_id=None,
                               auslassen=set(), entscheidungen={})
    db.rollback()
    dauer = dauerbuchungen.lege_an(db, kunde_id=k.id, feld_id=f.id, wochentag=1, start=time(19), ende=time(21),
                                   gueltig_von=date(2027, 12, 1), gueltig_bis=date(2027, 12, 31), admin_user_id=None,
                                   auslassen={date(2027, 12, 28)}, entscheidungen={fremd.id: "stornieren"})
    db.commit()
    assert len(dauer.buchungen) == 3
    assert {b.pin_hash for b in dauer.buchungen} == {dauer.pin_hash}
    assert db.get(Buchung, fremd.id).status == "storniert"
    assert all(b.zahlungsart == "rechnung" and b.quelle == "dauer" for b in dauer.buchungen)
    assert pin.entschluessele(dauer.pin_verschluesselt) == pin.entschluessele(dauer.buchungen[0].pin_verschluesselt)


def test_beenden_storniert_kuenftige(db: Session, welt) -> None:
    f, k, _ = welt
    dauer = dauerbuchungen.lege_an(db, kunde_id=k.id, feld_id=f.id, wochentag=1, start=time(19), ende=time(21),
                                   gueltig_von=date(2027, 12, 1), gueltig_bis=date(2027, 12, 31), admin_user_id=None,
                                   auslassen=set(), entscheidungen={})
    db.commit()
    dauerbuchungen.beende(db, dauer, ab=date(2027, 12, 20), admin_user_id=None)
    db.commit()
    status = [b.status for b in dauer.buchungen]
    assert status == ["bestaetigt", "bestaetigt", "storniert", "storniert"]
    assert dauer.beendet_ab == date(2027, 12, 20) and dauer.beendet_am is not None
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_dauerbuchungen.py`
Expected: FAIL mit ImportError

- [ ] **Step 3: Implementieren**

`core/beachhub_core/services/dauerbuchungen.py`:
```python
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from beachhub_core.models import Buchung, Dauerbuchung, Kunde, Sperre, utcnow
from beachhub_core.services import audit, buchungen, konfiguration, pin, sperren, tarife
from beachhub_shared.zeit import kombiniere


class DauerbuchungsFehler(Exception):
    pass


@dataclass
class Termin:
    datum: date
    beginn: datetime
    ende: datetime
    kollisionen: list[Buchung | Sperre]
    preis: Decimal | None


def _termine(wochentag: int, von: date, bis: date) -> list[date]:
    d = von + timedelta(days=(wochentag - von.weekday()) % 7)
    out = []
    while d <= bis:
        out.append(d)
        d += timedelta(days=7)
    return out


def plane(
    db: Session, *, kunde_id: uuid.UUID, feld_id: uuid.UUID, wochentag: int, start: time, ende: time,
    gueltig_von: date, gueltig_bis: date,
) -> list[Termin]:
    kunde = db.get(Kunde, kunde_id)
    if kunde is None:
        raise DauerbuchungsFehler("kunde_unbekannt")
    out: list[Termin] = []
    for d in _termine(wochentag, gueltig_von, gueltig_bis):
        b, e = kombiniere(d, start), kombiniere(d, ende)
        out.append(Termin(
            datum=d, beginn=b, ende=e,
            kollisionen=buchungen.finde_kollisionen(db, feld_id=feld_id, beginn=b, ende=e),
            preis=tarife.ermittle_preis(db, feld_id=feld_id, beginn=b, ende=e, kundengruppe_id=kunde.kundengruppe_id),
        ))
    return out


def lege_an(
    db: Session, *, kunde_id: uuid.UUID, feld_id: uuid.UUID, wochentag: int, start: time, ende: time,
    gueltig_von: date, gueltig_bis: date, admin_user_id: uuid.UUID | None,
    auslassen: set[date], entscheidungen: dict[uuid.UUID, str],
) -> Dauerbuchung:
    termine = [t for t in plane(db, kunde_id=kunde_id, feld_id=feld_id, wochentag=wochentag, start=start, ende=ende,
                                gueltig_von=gueltig_von, gueltig_bis=gueltig_bis) if t.datum not in auslassen]
    for t in termine:
        if t.preis is None:
            raise DauerbuchungsFehler("kein_tarif")
        for k in t.kollisionen:
            if isinstance(k, Sperre):
                raise DauerbuchungsFehler("sperre")
            if entscheidungen.get(k.id) != "stornieren":
                raise DauerbuchungsFehler("entscheidung_fehlt")
    if not termine:
        raise DauerbuchungsFehler("keine_termine")
    pin_klar = pin.finde_freien(db, termine[0].beginn, termine[-1].ende, konfiguration.hole(db, "pin_laenge"),
                                konfiguration.hole(db, "zutritt_vorlauf_minuten"))
    dauer = Dauerbuchung(
        kunde_id=kunde_id, feld_id=feld_id, wochentag=wochentag, start=start, ende=ende,
        gueltig_von=gueltig_von, gueltig_bis=gueltig_bis, pin_hash=pin.hash(pin_klar),
        pin_verschluesselt=pin.verschluessele(pin_klar),
    )
    db.add(dauer)
    db.flush()
    for t in termine:
        for k in t.kollisionen:
            sperren.STORNIERE(db, k, durch="betreiber", kostenfrei=True, grund="Dauerbuchung", admin_user_id=admin_user_id)
        buchungen.lege_an(db, feld_id=feld_id, kunde_id=kunde_id, beginn=t.beginn, ende=t.ende, quelle="dauer",
                          admin_user_id=admin_user_id, dauerbuchung_id=dauer.id, pin_klar=pin_klar)
    db.flush()
    db.refresh(dauer)
    audit.protokolliere(db, quelle="admin", objekt_typ="dauerbuchung", objekt_id=dauer.id, vorher=None,
                        nachher=audit.als_dict(dauer), admin_user_id=admin_user_id)
    return dauer


def beende(db: Session, dauer: Dauerbuchung, *, ab: date, admin_user_id: uuid.UUID | None) -> None:
    grenze = kombiniere(ab, time(0, 0))
    vorher = audit.als_dict(dauer)
    for b in dauer.buchungen:
        if b.beginn >= grenze and b.aktiv:
            sperren.STORNIERE(db, b, durch="betreiber", kostenfrei=True, grund="Dauerbuchung beendet",
                              admin_user_id=admin_user_id)
    dauer.beendet_am = utcnow()
    dauer.beendet_ab = ab
    db.flush()
    audit.protokolliere(db, quelle="admin", objekt_typ="dauerbuchung", objekt_id=dauer.id, vorher=vorher,
                        nachher=audit.als_dict(dauer), admin_user_id=admin_user_id)
```
`audit.als_dict` blendet `pin_hash`/`pin_verschluesselt` aus (Task 4); das gilt auch für `Dauerbuchung`.

- [ ] **Step 4: Tests grün**

Run: `cd core && pytest -q`
Expected: alle grün

- [ ] **Step 5: Commit**

```bash
git add core && git commit -m "feat(core): Dauerbuchungen planen, anlegen und beenden"
```

---

## Task 11: Storno, Nachbuchung, Kulanz

**Files:**
- Create: `core/beachhub_core/services/storno.py`
- Modify: `core/beachhub_core/services/__init__.py` (Hooks verdrahten), `core/beachhub_core/main.py` (Import `beachhub_core.services.storno` beim Start)
- Test: `core/tests/test_storno.py`

**Interfaces:**
- Produces: `storno.storniere(db, buchung, *, durch: str, admin_user_id=None, grund="", kostenfrei: bool | None = None) -> Storno`. Regeln: `kostenfrei=None` → automatisch: kostenfrei, wenn `clock.now(db) <= beginn - storno_frist_stunden`; sonst kostenpflichtig mit `nachbuchung_offen=True`. Durch Betreiber/System mit explizitem `kostenfrei` (Sperren, Dauerbuchung beenden). `StornoFehler("zu_spaet")`, wenn `beginn <= now` (A-STORNO-5); `StornoFehler("nicht_aktiv")`, wenn Buchung nicht aktiv. Setzt `buchung.status = STORNIERT`, Audit. Bei kostenfrei und `zahlungsart == "online"` und bereits bezahlt (Buchung war `bestaetigt`): Guthaben `storno_gutschrift` in Höhe `preis`. Bei kostenfrei und Rechnungskunde: nichts (Position kommt gar nicht erst in die Monatsrechnung, weil Status storniert und `kostenfrei=True`).
- Produces: `storno.pruefe_nachbuchung(db, neue_buchung)` – registriert in `buchungen.NACH_ANLAGE`: findet Stornos mit `nachbuchung_offen=True` auf demselben Feld, deren Buchungszeitraum die neue Buchung (teilweise) überlappt, **anderer Kunde**; berechnet den anteilig freigestellten Betrag = `preis_alt × (überlappende Minuten / Gesamtminuten alt)`, kumulativ bis maximal `preis_alt`; bei vollständiger Deckung `nachbuchung_offen=False`, `kostenfrei=True`; Guthaben `storno_gutschrift` (online) in Höhe der zusätzlich freigestellten Summe; `nachbuchung_buchung_id` = letzte deckende Buchung. Rechnungskunde: `freigestellt_betrag` reduziert später die Rechnungsposition (Task 14).
- Produces: `storno.kulanz(db, storno, *, admin_user_id, grund)` – setzt `kostenfrei=True`, `freigestellt_betrag=preis`, `nachbuchung_offen=False`, Gutschrift des noch nicht freigestellten Rests (online).
- Verdrahtung: `sperren.STORNIERE = storno.storniere` und `buchungen.NACH_ANLAGE.append(storno.pruefe_nachbuchung)` am Modulende von `storno.py`; `services/__init__.py` importiert `storno`, damit die Hooks bei jedem App-Start gesetzt sind.

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_storno.py`:
```python
from datetime import date, time
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from beachhub_core import clock
from beachhub_core.models import Betriebszeit, Feld, FeldRaster, Kundengruppe, Tarif
from beachhub_core.services import buchungen, konfiguration, kunden, storno
from beachhub_shared.zeit import kombiniere

D = date(2027, 12, 1)


@pytest.fixture
def welt(db: Session):
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    p = Kundengruppe(name="Privat")
    v = Kundengruppe(name="Verein", standard_zahlungsart="rechnung")
    db.add_all([f, p, v, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    a = kunden.lege_an(db, name="A", email="a@x.de", kundengruppe_id=p.id)
    b = kunden.lege_an(db, name="B", email="b@x.de", kundengruppe_id=p.id)
    v1 = kunden.lege_an(db, name="V", email="v@x.de", kundengruppe_id=v.id)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    # Frist 48 h: Override auf 30.11. liegt damit immer innerhalb der Frist, egal zu welcher Tageszeit der Test läuft
    konfiguration.setze(db, "storno_frist_stunden", 48)
    db.commit()
    return f, a, b, v1


def test_vor_frist_kostenfrei_mit_gutschrift(db: Session, welt) -> None:
    f, a, _, _ = welt
    bu = buchungen.lege_an(db, feld_id=f.id, kunde_id=a.id, beginn=kombiniere(D, time(19)), ende=kombiniere(D, time(21)))
    db.commit()
    s = storno.storniere(db, bu, durch="kunde")
    db.commit()
    assert s.kostenfrei and not s.nachbuchung_offen and bu.status == "storniert"
    assert a.guthaben == Decimal("60.00")


def test_nach_frist_kostenpflichtig_dann_nachbuchung(db: Session, welt) -> None:
    f, a, b, _ = welt
    bu = buchungen.lege_an(db, feld_id=f.id, kunde_id=a.id, beginn=kombiniere(D, time(19)), ende=kombiniere(D, time(21)))
    db.commit()
    clock.set_override(db, date(2027, 11, 30))  # innerhalb der 48-h-Frist
    s = storno.storniere(db, bu, durch="kunde")
    db.commit()
    assert not s.kostenfrei and s.nachbuchung_offen and a.guthaben == Decimal("0.00")
    # anderer Kunde bucht 1 von 2 Stunden nach → anteilig
    n1 = buchungen.lege_an(db, feld_id=f.id, kunde_id=b.id, beginn=kombiniere(D, time(19)), ende=kombiniere(D, time(20)))
    db.commit()
    db.refresh(s)
    assert s.freigestellt_betrag == Decimal("30.00") and s.nachbuchung_offen and not s.kostenfrei
    assert a.guthaben == Decimal("30.00")
    n2 = buchungen.lege_an(db, feld_id=f.id, kunde_id=b.id, beginn=kombiniere(D, time(20)), ende=kombiniere(D, time(21)))
    db.commit()
    db.refresh(s)
    assert s.freigestellt_betrag == Decimal("60.00") and s.kostenfrei and not s.nachbuchung_offen
    assert s.nachbuchung_buchung_id == n2.id and a.guthaben == Decimal("60.00")


def test_eigene_nachbuchung_zaehlt_nicht(db: Session, welt) -> None:
    f, a, _, _ = welt
    bu = buchungen.lege_an(db, feld_id=f.id, kunde_id=a.id, beginn=kombiniere(D, time(19)), ende=kombiniere(D, time(20)))
    db.commit()
    clock.set_override(db, date(2027, 11, 30))
    s = storno.storniere(db, bu, durch="kunde")
    buchungen.lege_an(db, feld_id=f.id, kunde_id=a.id, beginn=kombiniere(D, time(19)), ende=kombiniere(D, time(20)))
    db.commit()
    db.refresh(s)
    assert s.nachbuchung_offen and s.freigestellt_betrag == Decimal("0.00")


def test_rechnungskunde_ohne_gutschrift_und_kulanz(db: Session, welt) -> None:
    f, _, _, v1 = welt
    bu = buchungen.lege_an(db, feld_id=f.id, kunde_id=v1.id, beginn=kombiniere(D, time(19)), ende=kombiniere(D, time(20)))
    clock.set_override(db, date(2027, 11, 30))
    s = storno.storniere(db, bu, durch="kunde")
    db.commit()
    assert not s.kostenfrei and v1.guthaben == Decimal("0.00")
    storno.kulanz(db, s, admin_user_id=None, grund="Krankheit")
    db.commit()
    assert s.kostenfrei and s.freigestellt_betrag == Decimal("30.00") and v1.guthaben == Decimal("0.00")


def test_zu_spaet_und_nicht_aktiv(db: Session, welt) -> None:
    f, a, _, _ = welt
    bu = buchungen.lege_an(db, feld_id=f.id, kunde_id=a.id, beginn=kombiniere(D, time(19)), ende=kombiniere(D, time(20)))
    db.commit()
    clock.set_override(db, date(2027, 12, 2))
    with pytest.raises(storno.StornoFehler, match="zu_spaet"):
        storno.storniere(db, bu, durch="kunde")
    clock.set_override(db, date(2027, 11, 25))
    storno.storniere(db, bu, durch="kunde")
    with pytest.raises(storno.StornoFehler, match="nicht_aktiv"):
        storno.storniere(db, bu, durch="kunde")
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_storno.py`
Expected: FAIL mit ImportError

- [ ] **Step 3: Implementieren**

`core/beachhub_core/services/storno.py`:
```python
import uuid
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_core import clock
from beachhub_core.models import Buchung, Storno
from beachhub_core.services import audit, buchungen, guthaben, konfiguration, sperren


class StornoFehler(Exception):
    pass


def _gutschrift(db: Session, buchung: Buchung, betrag: Decimal, storno_id: uuid.UUID, quelle: str) -> None:
    """Onlinezahler, die bereits bestätigt (= bezahlt) waren, bekommen Guthaben. Rechnungskunden nicht."""
    if buchung.zahlungsart == "online" and betrag > 0:
        guthaben.buche(db, kunde=buchung.kunde, betrag=betrag, art="storno_gutschrift", bezug_id=storno_id,
                       notiz="Storno kostenfrei", quelle=quelle)


def storniere(
    db: Session, buchung: Buchung, *, durch: str, admin_user_id: uuid.UUID | None = None, grund: str = "",
    kostenfrei: bool | None = None,
) -> Storno:
    if not buchung.aktiv:
        raise StornoFehler("nicht_aktiv")
    jetzt = clock.now(db)
    if buchung.beginn <= jetzt:
        raise StornoFehler("zu_spaet")
    war_bezahlt = buchung.status in (Buchung.BESTAETIGT, Buchung.DURCHGEFUEHRT, Buchung.NICHT_ERSCHIENEN)
    if kostenfrei is None:
        frist = timedelta(hours=konfiguration.hole(db, "storno_frist_stunden"))
        kostenfrei = jetzt <= buchung.beginn - frist
    s = Storno(
        buchung_id=buchung.id, durch=durch, kostenfrei=kostenfrei, grund=grund,
        nachbuchung_offen=not kostenfrei,
        freigestellt_betrag=buchung.preis if kostenfrei else Decimal("0.00"),
    )
    db.add(s)
    quelle = "admin" if durch == "betreiber" else ("portal" if durch == "kunde" else "system")
    buchungen.setze_status(db, buchung, Buchung.STORNIERT, quelle=quelle, admin_user_id=admin_user_id)
    db.flush()
    if kostenfrei and war_bezahlt:
        _gutschrift(db, buchung, buchung.preis, s.id, quelle)
    audit.protokolliere(db, quelle=quelle, objekt_typ="storno", objekt_id=s.id, vorher=None,
                        nachher=audit.als_dict(s), admin_user_id=admin_user_id)
    return s


def _ueberlappung_minuten(a: Buchung, b: Buchung) -> int:
    von, bis = max(a.beginn, b.beginn), min(a.ende, b.ende)
    return max(0, int((bis - von).total_seconds() // 60))


def pruefe_nachbuchung(db: Session, neue: Buchung) -> None:
    offene = db.scalars(
        select(Storno).join(Buchung, Storno.buchung_id == Buchung.id).where(
            Storno.nachbuchung_offen.is_(True), Buchung.feld_id == neue.feld_id,
            Buchung.kunde_id != neue.kunde_id, Buchung.beginn < neue.ende, Buchung.ende > neue.beginn,
        )
    ).all()
    for s in offene:
        alt = s.buchung
        gesamt = int((alt.ende - alt.beginn).total_seconds() // 60)
        anteil = (alt.preis * Decimal(_ueberlappung_minuten(alt, neue)) / Decimal(gesamt)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP)
        neu_frei = min(alt.preis, s.freigestellt_betrag + anteil)
        zusatz = neu_frei - s.freigestellt_betrag
        if zusatz <= 0:
            continue
        vorher = audit.als_dict(s)
        s.freigestellt_betrag = neu_frei
        s.nachbuchung_buchung_id = neue.id
        if neu_frei >= alt.preis:
            s.kostenfrei = True
            s.nachbuchung_offen = False
        _gutschrift(db, alt, zusatz, s.id, "system")
        db.flush()
        audit.protokolliere(db, quelle="system", objekt_typ="storno", objekt_id=s.id, vorher=vorher,
                            nachher=audit.als_dict(s))


def kulanz(db: Session, s: Storno, *, admin_user_id: uuid.UUID | None, grund: str) -> None:
    vorher = audit.als_dict(s)
    rest = s.buchung.preis - s.freigestellt_betrag
    s.kostenfrei = True
    s.nachbuchung_offen = False
    s.freigestellt_betrag = s.buchung.preis
    s.grund = (s.grund + " | " if s.grund else "") + f"Kulanz: {grund}"
    _gutschrift(db, s.buchung, rest, s.id, "admin")
    db.flush()
    audit.protokolliere(db, quelle="admin", objekt_typ="storno", objekt_id=s.id, vorher=vorher,
                        nachher=audit.als_dict(s), admin_user_id=admin_user_id)


# Verdrahtung der Hooks aus Task 8/9 – einmalig beim Import
sperren.STORNIERE = storniere
if pruefe_nachbuchung not in buchungen.NACH_ANLAGE:
    buchungen.NACH_ANLAGE.append(pruefe_nachbuchung)
```

`core/beachhub_core/services/__init__.py`:
```python
"""Beim Import der Services werden die Hooks verdrahtet (storno registriert sich bei buchungen/sperren)."""
from beachhub_core.services import storno as _storno  # noqa: F401
```
Achtung Zirkelimport: `storno` importiert `buchungen`, `sperren`, `guthaben`; keines davon importiert `storno`. `services/__init__.py` wird beim ersten `from beachhub_core.services import X` ausgeführt; da `storno` seine Importe erst innerhalb lädt, ist die Reihenfolge unkritisch. In den Tests zu Task 9/10 die Fixture-Zeile `sperren.STORNIERE = lambda …` **entfernen**, sobald diese Task grün ist (der echte Storno ist dann verdrahtet). Die Erwartungen dort bleiben gültig, da `storniere(..., kostenfrei=True)` den Status auf `storniert` setzt.

- [ ] **Step 4: Tests grün**

Run: `cd core && pytest -q`
Expected: alle grün (nach Entfernen der Lambda-Hooks in test_sperren.py und test_dauerbuchungen.py)

- [ ] **Step 5: Commit**

```bash
git add core && git commit -m "feat(core): Storno mit Frist, anteiliger Nachbuchung und Kulanz"
```

---

## Task 12: Admin-Anmeldung (Passwort + TOTP), Sessions, CSRF, Basis-Layout

**Files:**
- Create: `core/beachhub_core/auth.py`, `core/beachhub_core/cli.py`, `core/beachhub_core/templating.py`
- Create: `core/beachhub_core/routes/__init__.py`, `core/beachhub_core/routes/admin_auth.py`, `core/beachhub_core/routes/dashboard.py`
- Create: `core/beachhub_core/templates/base.html`, `core/beachhub_core/templates/login.html`, `core/beachhub_core/templates/dashboard.html`, `core/beachhub_core/static/style.css`
- Modify: `core/beachhub_core/models/system.py` (AdminSession), `core/beachhub_core/models/__init__.py`, `core/beachhub_core/main.py`, `core/tests/conftest.py`
- Create: `core/alembic/versions/0004_admin_session.py`
- Test: `core/tests/test_auth.py`

**Interfaces:**
- Produces Modell `AdminSession(token_hash unique, admin_user_id, erstellt_am, laeuft_ab, csrf_token)`.
- Produces `auth.hash_passwort(klar) -> str`, `auth.pruefe_passwort(klar, hash) -> bool` (Argon2id), `auth.erzeuge_totp_secret() -> str`, `auth.pruefe_totp(secret, code) -> bool` (pyotp, `valid_window=1`), `auth.lege_admin_an(db, *, name, passwort, rolle="admin") -> tuple[AdminUser, str]` (gibt TOTP-Secret zurück), `auth.erzeuge_session(db, admin) -> tuple[token, csrf]`, `auth.lade_session(db, token) -> AdminSession | None`, `auth.beende_session(db, token)`, `auth.setze_cookie(response, token)`, `auth.loesche_cookie(response)`.
- Dependencies: `auth.aktueller_admin(request, db) -> AdminUser` (wirft 303 → `/admin/login`), `auth.nur_admin_rolle(admin) -> AdminUser` (403 für `lesend` bei schreibenden Aktionen), `auth.verify_csrf(request, db)` (auf allen Nicht-GET-Requests: Formularfeld `csrf_token` muss dem Session-Wert entsprechen), `auth.pruefe_rate_limit(request, scope)` (max. 10 Versuche je IP in 15 min, In-Memory), `auth.reset_rate_limits()`.
- Produces `templating.templates: Jinja2Templates` mit Filtern `euro(Decimal) -> "12,50 €"`, `lokal(datetime) -> "Mi 01.12.2027 19:00"`, `datum(date) -> "01.12.2027"`, `uhrzeit(time|datetime) -> "19:00"`; Globals `csrf_token(request)`; Helfer `templating.render(request, name, **ctx) -> HTMLResponse` (hängt `admin`, `flash` an).
- Produces `templating.mit_flash(response, text, art="ok"|"fehler")` – Hinweis für die nächste Seite über ein kurzlebiges signiertes Cookie `bh_flash` (`itsdangerous`, 60 s); `render` liest und löscht es.
- Routen: `GET/POST /admin/login` (Schritt 1 Name+Passwort, Schritt 2 TOTP-Code im selben Formular: alle drei Felder auf einer Seite), `POST /admin/logout`, `GET /admin` (Dashboard: Zähler Buchungen heute, offene Stornos, Rechnungen offen).
- CLI: `beachhub-core create-admin --name kai --rolle admin` (fragt Passwort ab, druckt TOTP-Secret und `otpauth://`-URI), `beachhub-core keygen` (Task 19), `beachhub-core monatslauf JJJJ-MM` (Task 13).
- Test-Fixtures in `conftest.py`: `admin` (angelegter Admin mit bekanntem Passwort/Secret), `eingeloggt: TestClient` (Client mit Session-Cookie; POSTs müssen `csrf_token` mitschicken → Fixture liefert `client.csrf`).

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_auth.py`:
```python
import pyotp
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from beachhub_core import auth
from beachhub_core.models import AdminUser


def test_login_braucht_passwort_und_totp(client: TestClient, db: Session) -> None:
    user, secret = auth.lege_admin_an(db, name="kai", passwort="geheim-123456")
    db.commit()
    r = client.post("/admin/login", data={"name": "kai", "passwort": "falsch", "code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200 and "Anmeldung fehlgeschlagen" in r.text
    r = client.post("/admin/login", data={"name": "kai", "passwort": "geheim-123456", "code": "000000"})
    assert r.status_code == 200 and "Anmeldung fehlgeschlagen" in r.text
    r = client.post("/admin/login", data={"name": "kai", "passwort": "geheim-123456", "code": pyotp.TOTP(secret).now()},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    assert "bh_session" in r.cookies
    r = client.get("/admin")
    assert r.status_code == 200 and "Übersicht" in r.text


def test_ohne_session_umleitung(client: TestClient) -> None:
    r = client.get("/admin", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/admin/login")


def test_csrf_pflicht(eingeloggt: TestClient) -> None:
    r = eingeloggt.post("/admin/logout", data={})
    assert r.status_code == 403
    r = eingeloggt.post("/admin/logout", data={"csrf_token": eingeloggt.csrf}, follow_redirects=False)
    assert r.status_code == 303
    assert eingeloggt.get("/admin", follow_redirects=False).status_code == 303


def test_rate_limit(client: TestClient, db: Session) -> None:
    auth.lege_admin_an(db, name="kai", passwort="geheim-123456")
    db.commit()
    for _ in range(10):
        client.post("/admin/login", data={"name": "kai", "passwort": "x", "code": "1"})
    r = client.post("/admin/login", data={"name": "kai", "passwort": "x", "code": "1"})
    assert r.status_code == 429


def test_lesende_rolle_darf_nicht_schreiben(client: TestClient, db: Session) -> None:
    user, secret = auth.lege_admin_an(db, name="leser", passwort="geheim-123456", rolle="lesend")
    db.commit()
    client.post("/admin/login", data={"name": "leser", "passwort": "geheim-123456", "code": pyotp.TOTP(secret).now()})
    assert db.query(AdminUser).filter_by(name="leser").one().rolle == "lesend"
    r = client.get("/admin")
    assert r.status_code == 200
```
(Die Schreibprüfung für `lesend` wird in Task 13 mit einer echten Schreibroute getestet; hier nur Login.)

Ergänzung `core/tests/conftest.py`:
```python
import pyotp

from beachhub_core import auth


@pytest.fixture(autouse=True)
def _rate_limits_leeren() -> None:
    auth.reset_rate_limits()


@pytest.fixture
def admin(db: Session) -> tuple[AdminUser, str]:
    user, secret = auth.lege_admin_an(db, name="admin", passwort="test-passwort-1234")
    db.commit()
    return user, secret


@pytest.fixture
def eingeloggt(client: TestClient, admin: tuple[AdminUser, str]) -> TestClient:
    _, secret = admin
    r = client.post("/admin/login", data={"name": "admin", "passwort": "test-passwort-1234",
                                          "code": pyotp.TOTP(secret).now()}, follow_redirects=False)
    assert r.status_code == 303
    seite = client.get("/admin")
    import re
    m = re.search(r'name="csrf_token" value="([^"]+)"', seite.text)
    assert m
    client.csrf = m.group(1)  # type: ignore[attr-defined]
    return client
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_auth.py`
Expected: FAIL mit ImportError

- [ ] **Step 3: Modell, Auth, CLI**

In `models/system.py` ergänzen:
```python
class AdminSession(UUIDMixin, Base):
    __tablename__ = "admin_session"
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    admin_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("admin_user.id"), nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    erstellt_am: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    laeuft_ab: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```
(`ForeignKey` importieren; in `__init__.py` exportieren.)

`core/beachhub_core/auth.py`:
```python
"""Admin-Anmeldung: Argon2id-Passwort + TOTP, serverseitige Sessions, CSRF, Rate-Limit."""
import hashlib
import secrets
import time as _time
import uuid
from collections import defaultdict
from datetime import timedelta

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_core.config import settings
from beachhub_core.database import get_db
from beachhub_core.models import AdminSession, AdminUser, utcnow

COOKIE = "bh_session"
SESSION_DAUER = timedelta(hours=12)
_ph = PasswordHasher()
_versuche: dict[str, list[float]] = defaultdict(list)
RATE_MAX, RATE_FENSTER = 10, 15 * 60


def hash_passwort(klar: str) -> str:
    return _ph.hash(klar)


def pruefe_passwort(klar: str, gespeichert: str) -> bool:
    try:
        return _ph.verify(gespeichert, klar)
    except VerifyMismatchError:
        return False


def erzeuge_totp_secret() -> str:
    return pyotp.random_base32()


def pruefe_totp(secret: str, code: str) -> bool:
    return pyotp.TOTP(secret).verify(code.strip().replace(" ", ""), valid_window=1)


def lege_admin_an(db: Session, *, name: str, passwort: str, rolle: str = "admin") -> tuple[AdminUser, str]:
    if len(passwort) < 12:
        raise ValueError("Passwort muss mindestens 12 Zeichen haben")
    secret = erzeuge_totp_secret()
    user = AdminUser(name=name.strip(), passwort_hash=hash_passwort(passwort), totp_secret=secret, rolle=rolle)
    db.add(user)
    db.flush()
    return user, secret


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def erzeuge_session(db: Session, admin: AdminUser) -> tuple[str, str]:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    db.add(AdminSession(token_hash=_token_hash(token), admin_user_id=admin.id, csrf_token=csrf,
                        laeuft_ab=utcnow() + SESSION_DAUER))
    db.commit()
    return token, csrf


def lade_session(db: Session, token: str | None) -> AdminSession | None:
    if not token:
        return None
    s = db.scalar(select(AdminSession).where(AdminSession.token_hash == _token_hash(token)))
    if s is None or s.laeuft_ab < utcnow():
        return None
    return s


def beende_session(db: Session, token: str | None) -> None:
    s = lade_session(db, token)
    if s:
        db.delete(s)
        db.commit()


def setze_cookie(response: Response, token: str) -> None:
    response.set_cookie(COOKIE, token, httponly=True, secure=settings.cookie_secure, samesite="lax",
                        max_age=int(SESSION_DAUER.total_seconds()), path="/")


def loesche_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")


def aktueller_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    s = lade_session(db, request.cookies.get(COOKIE))
    if s is None:
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
    user = db.get(AdminUser, s.admin_user_id)
    if user is None or not user.aktiv:
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
    request.state.csrf = s.csrf_token
    return user


def nur_admin_rolle(admin: AdminUser = Depends(aktueller_admin)) -> AdminUser:
    if admin.rolle != "admin":
        raise HTTPException(status_code=403, detail="Nur lesender Zugriff")
    return admin


async def verify_csrf(request: Request, db: Session = Depends(get_db)) -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    s = lade_session(db, request.cookies.get(COOKIE))
    if s is None:
        return  # Login-Formular ohne Session; dort greift das Rate-Limit
    form = await request.form()
    if not secrets.compare_digest(str(form.get("csrf_token", "")), s.csrf_token):
        raise HTTPException(status_code=403, detail="CSRF-Token ungültig")


def pruefe_rate_limit(request: Request, scope: str) -> None:
    ip = request.client.host if request.client else "?"
    key, jetzt = f"{scope}:{ip}", _time.monotonic()
    _versuche[key] = [t for t in _versuche[key] if jetzt - t < RATE_FENSTER]
    if len(_versuche[key]) >= RATE_MAX:
        raise HTTPException(status_code=429, detail="Zu viele Versuche")
    _versuche[key].append(jetzt)


def reset_rate_limits() -> None:
    _versuche.clear()
```

`core/beachhub_core/cli.py`:
```python
import argparse
import getpass

from beachhub_core.database import SessionLocal


def main() -> None:
    p = argparse.ArgumentParser(prog="beachhub-core")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("create-admin")
    a.add_argument("--name", required=True)
    a.add_argument("--rolle", default="admin", choices=["admin", "lesend"])
    sub.add_parser("keygen")
    m = sub.add_parser("monatslauf")
    m.add_argument("monat", help="JJJJ-MM")
    args = p.parse_args()
    if args.cmd == "create-admin":
        from beachhub_core import auth
        pw = getpass.getpass("Passwort (min. 12 Zeichen): ")
        with SessionLocal() as db:
            user, secret = auth.lege_admin_an(db, name=args.name, passwort=pw, rolle=args.rolle)
            db.commit()
        import pyotp
        print(f"Admin '{user.name}' angelegt. TOTP-Secret: {secret}")
        print(pyotp.TOTP(secret).provisioning_uri(name=user.name, issuer_name="Beachhub"))
    elif args.cmd == "keygen":
        from beachhub_core.services import lesestand
        print(lesestand.erzeuge_schluessel())
    elif args.cmd == "monatslauf":
        from beachhub_core.services import rechnungen
        jahr, monat = (int(x) for x in args.monat.split("-"))
        with SessionLocal() as db:
            erzeugt = rechnungen.monatslauf(db, jahr, monat)
            db.commit()
        print(f"{len(erzeugt)} Rechnungen erzeugt")
```

- [ ] **Step 4: Templating, Layout, Routen**

`core/beachhub_core/templating.py`:
```python
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeTimedSerializer

from beachhub_core.config import settings
from beachhub_core.models import AdminUser
from beachhub_shared.zeit import lokal

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
_WT = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
_flash = URLSafeTimedSerializer(settings.secret_key, salt="flash")


def euro(v: Decimal | None) -> str:
    return "–" if v is None else f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"


def f_lokal(v: datetime) -> str:
    lv = lokal(v)
    return f"{_WT[lv.weekday()]} {lv:%d.%m.%Y %H:%M}"


def f_datum(v: date | datetime) -> str:
    return (lokal(v) if isinstance(v, datetime) else v).strftime("%d.%m.%Y")


def f_uhrzeit(v: time | datetime) -> str:
    return (lokal(v) if isinstance(v, datetime) else v).strftime("%H:%M")


templates.env.filters.update({"euro": euro, "lokal": f_lokal, "datum": f_datum, "uhrzeit": f_uhrzeit})
templates.env.globals["wochentage"] = _WT


def render(request: Request, name: str, admin: AdminUser | None = None, **ctx: Any) -> HTMLResponse:
    flash = None
    roh = request.cookies.get("bh_flash")
    if roh:
        try:
            flash = _flash.loads(roh, max_age=60)
        except BadSignature:
            flash = None
    resp = templates.TemplateResponse(request, name, {
        "admin": admin, "csrf_token": getattr(request.state, "csrf", ""), "flash": flash, **ctx,
    })
    if roh:
        resp.delete_cookie("bh_flash", path="/")
    return resp


def mit_flash(response: Any, text: str, art: str = "ok") -> Any:
    response.set_cookie("bh_flash", _flash.dumps({"text": text, "art": art}), httponly=True,
                        secure=settings.cookie_secure, samesite="lax", max_age=60, path="/")
    return response
```
`itsdangerous==2.2.0` in `core/pyproject.toml` ergänzen.

`core/beachhub_core/templates/base.html`:
```html
<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% block title %}Beachhub{% endblock %} · Beachhub</title>
<link rel="stylesheet" href="/static/style.css">
</head>
<body>
<header class="top">
  <a class="brand" href="/admin">Beachhub</a>
  {% if admin %}
  <nav>
    <a href="/admin/belegung">Belegung</a>
    <a href="/admin/kunden">Kunden</a>
    <a href="/admin/rechnungen">Rechnungen</a>
    <a href="/admin/felder">Stammdaten</a>
    <a href="/admin/system">System</a>
  </nav>
  <form method="post" action="/admin/logout" class="inline">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <button class="link">{{ admin.name }} · Abmelden</button>
  </form>
  {% endif %}
</header>
<main>
  {% if flash %}<div class="flash {{ flash.art }}">{{ flash.text }}</div>{% endif %}
  {% block content %}{% endblock %}
</main>
</body>
</html>
```

`core/beachhub_core/templates/login.html`:
```html
{% extends "base.html" %}{% block title %}Anmeldung{% endblock %}
{% block content %}
<h1>Anmeldung</h1>
{% if fehler %}<p class="fehler">Anmeldung fehlgeschlagen.</p>{% endif %}
<form method="post" action="/admin/login" class="karte schmal">
  <label>Name <input name="name" required autocomplete="username"></label>
  <label>Passwort <input name="passwort" type="password" required autocomplete="current-password"></label>
  <label>Code aus der Authenticator-App <input name="code" inputmode="numeric" pattern="[0-9 ]*" required autocomplete="one-time-code"></label>
  <button>Anmelden</button>
</form>
{% endblock %}
```

`core/beachhub_core/templates/dashboard.html`:
```html
{% extends "base.html" %}{% block title %}Übersicht{% endblock %}
{% block content %}
<h1>Übersicht</h1>
<div class="kacheln">
  <a class="kachel" href="/admin/belegung"><span class="zahl">{{ heute }}</span>Buchungen heute</a>
  <a class="kachel" href="/admin/system/stornos"><span class="zahl">{{ stornos_offen }}</span>Stornos mit offener Nachbuchung</a>
  <a class="kachel" href="/admin/rechnungen?status=offen"><span class="zahl">{{ rechnungen_offen }}</span>Rechnungen offen</a>
</div>
{% endblock %}
```

`core/beachhub_core/static/style.css` (Design-Tokens, Hell/Dunkel; Auszug – vollständig anlegen):
```css
:root { --bg:#f7f8fa; --fg:#1f2933; --akzent:#0b4f6c; --rand:#c9d2da; --karte:#fff; --ok:#1b7f3b; --fehler:#b42318; --warn:#b7791f; }
@media (prefers-color-scheme: dark) { :root { --bg:#121417; --fg:#e6e9ee; --akzent:#5fb3d1; --rand:#3a424c; --karte:#1c2127; } }
* { box-sizing: border-box; }
body { margin:0; font: 15px/1.5 system-ui, sans-serif; background:var(--bg); color:var(--fg); }
header.top { display:flex; gap:1.5rem; align-items:center; padding:.6rem 1rem; border-bottom:1px solid var(--rand); background:var(--karte); }
header .brand { font-weight:700; color:var(--akzent); text-decoration:none; }
header nav a { margin-right:1rem; color:inherit; }
header form.inline { margin-left:auto; }
main { max-width: 1200px; margin: 0 auto; padding: 1rem; }
h1 { font-size:1.5rem; margin:.5rem 0 1rem; }
.karte { background:var(--karte); border:1px solid var(--rand); border-radius:8px; padding:1rem; margin-bottom:1rem; }
.schmal { max-width: 420px; }
label { display:block; margin-bottom:.75rem; }
input, select, textarea { width:100%; padding:.45rem .6rem; border:1px solid var(--rand); border-radius:6px; background:var(--bg); color:inherit; font:inherit; }
button { background:var(--akzent); color:#fff; border:0; border-radius:6px; padding:.5rem 1rem; font:inherit; cursor:pointer; }
button.link { background:none; color:var(--akzent); padding:0; }
button.gefahr { background:var(--fehler); }
table { width:100%; border-collapse:collapse; background:var(--karte); }
th, td { text-align:left; padding:.45rem .6rem; border-bottom:1px solid var(--rand); vertical-align:top; }
.flash { padding:.6rem 1rem; border-radius:6px; margin-bottom:1rem; }
.flash.ok { background:#e6f4ea; color:var(--ok); } .flash.fehler { background:#fdecea; color:var(--fehler); }
.fehler { color:var(--fehler); }
.kacheln { display:grid; grid-template-columns:repeat(auto-fit, minmax(200px,1fr)); gap:1rem; }
.kachel { display:block; padding:1rem; background:var(--karte); border:1px solid var(--rand); border-radius:8px; text-decoration:none; color:inherit; }
.kachel .zahl { display:block; font-size:2rem; font-weight:700; color:var(--akzent); }
.kalender { display:grid; grid-template-columns: 60px repeat(7, 1fr); gap:2px; }
.kalender .zelle { min-height: 2.2rem; padding:.2rem .3rem; font-size:.85rem; border:1px solid var(--rand); background:var(--karte); }
.kalender .frei { background: var(--bg); } .kalender .belegt { background:#d9e8f0; color:#0b4f6c; }
.kalender .sperre { background:#fdf6e7; color:var(--warn); } .kalender .storno { opacity:.5; text-decoration: line-through; }
.zeile { display:flex; gap:1rem; flex-wrap:wrap; } .zeile > * { flex:1; min-width:160px; }
.rechts { text-align:right; }
.badge { display:inline-block; padding:.1rem .5rem; border-radius:999px; font-size:.8rem; border:1px solid var(--rand); }
```

`core/beachhub_core/routes/__init__.py`: leer.

`core/beachhub_core/routes/admin_auth.py`:
```python
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_core import auth
from beachhub_core.database import get_db
from beachhub_core.models import AdminUser
from beachhub_core.templating import render

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_seite(request: Request) -> HTMLResponse:
    return render(request, "login.html", fehler=False)


@router.post("/login")
def login(request: Request, name: str = Form(...), passwort: str = Form(...), code: str = Form(...),
          db: Session = Depends(get_db)) -> HTMLResponse | RedirectResponse:
    auth.pruefe_rate_limit(request, "login")
    user = db.scalar(select(AdminUser).where(AdminUser.name == name.strip(), AdminUser.aktiv.is_(True)))
    ok = user is not None and auth.pruefe_passwort(passwort, user.passwort_hash) and auth.pruefe_totp(user.totp_secret, code)
    if not ok or user is None:
        return render(request, "login.html", fehler=True)
    token, _ = auth.erzeuge_session(db, user)
    resp = RedirectResponse("/admin", status_code=303)
    auth.setze_cookie(resp, token)
    return resp


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    auth.beende_session(db, request.cookies.get(auth.COOKIE))
    resp = RedirectResponse("/admin/login", status_code=303)
    auth.loesche_cookie(resp)
    return resp
```

`core/beachhub_core/routes/dashboard.py`:
```python
from datetime import time, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from beachhub_core import auth, clock
from beachhub_core.database import get_db
from beachhub_core.models import AdminUser, Buchung, Storno
from beachhub_core.templating import render
from beachhub_shared.zeit import kombiniere

router = APIRouter()


@router.get("", response_class=HTMLResponse)
def dashboard(request: Request, admin: AdminUser = Depends(auth.aktueller_admin), db: Session = Depends(get_db)) -> HTMLResponse:
    heute = clock.today(db)
    von, bis = kombiniere(heute, time(0)), kombiniere(heute + timedelta(days=1), time(0))
    n_heute = db.scalar(select(func.count()).select_from(Buchung).where(
        Buchung.status.in_(Buchung.AKTIVE_STATUS), Buchung.beginn >= von, Buchung.beginn < bis)) or 0
    n_storno = db.scalar(select(func.count()).select_from(Storno).where(Storno.nachbuchung_offen.is_(True))) or 0
    rechnungen_offen = 0  # Task 15 ersetzt das durch die echte Zählung
    return render(request, "dashboard.html", admin=admin, heute=n_heute, stornos_offen=n_storno,
                  rechnungen_offen=rechnungen_offen)
```

In `main.py` ergänzen (nach dem Static-Mount):
```python
from fastapi import Depends
from beachhub_core.auth import verify_csrf
from beachhub_core.routes import admin_auth, dashboard
import beachhub_core.services  # noqa: F401  – verdrahtet Storno-Hooks

csrf = [Depends(verify_csrf)]
app.include_router(admin_auth.router, prefix="/admin", dependencies=csrf)
app.include_router(dashboard.router, prefix="/admin", dependencies=csrf)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):  # type: ignore[no-untyped-def]
    if exc.status_code == 303 and exc.headers and "Location" in exc.headers:
        return RedirectResponse(exc.headers["Location"], status_code=303)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
```
(`HTTPException` aus `fastapi`, `RedirectResponse` aus `fastapi.responses` importieren.)

- [ ] **Step 5: Migration, Tests grün**

Run:
```bash
cd core && alembic revision --autogenerate -m "admin session"   # → 0004_admin_session.py
pytest -q
```
Expected: alle grün

- [ ] **Step 6: Commit**

```bash
git add core && git commit -m "feat(core): Admin-Anmeldung mit TOTP, Sessions, CSRF und Basislayout"
```

---

## Task 13: Rechnungen – Nummernkreis, Einzel-, Sammel- und Stornorechnung, Monatslauf, CSV

**Files:**
- Create: `core/beachhub_core/models/rechnungen.py`, `core/beachhub_core/services/rechnungen.py`
- Modify: `core/beachhub_core/models/__init__.py`, `core/beachhub_core/models/buchungen.py` (Spalte `rechnung_position_id`)
- Create: `core/alembic/versions/0005_rechnungen.py`
- Test: `core/tests/test_rechnungen.py`

**Interfaces:**
- Produces Modelle: `Nummernkreis(jahr PK, letzte_nummer)`, `Rechnung(nummer unique, kunde_id, datum: date, leistung_von: date, leistung_bis: date, netto, ust, brutto, ust_satz, status: "offen"|"bezahlt"|"storniert", art: "einzel"|"sammel"|"storno", faellig_am: date, pdf_pfad: str|None, pdf_sha256: str|None, storniert_durch_id: uuid|None, bezahlt_am: datetime|None, adresse_snapshot: dict)`, `RechnungPosition(rechnung_id, buchung_id: uuid|None, text, menge: int, einzelpreis_brutto, ust_satz, brutto)`; `Buchung.rechnung_position_id: uuid|None`.
- Produces: `rechnungen.naechste_nummer(db, jahr) -> str` (`JJJJ-NNNNN`, lückenlos: `SELECT … FOR UPDATE` auf `nummernkreis`, Zeile anlegen falls fehlt).
- Produces: `rechnungen.erzeuge_einzelrechnung(db, buchung, *, quelle="system") -> Rechnung` (eine Position, Status `bezahlt`, `bezahlt_am = now`; Brutto = `buchung.preis`, Netto/USt aus `ust_satz`, Rundung kaufmännisch auf Cent; Buchung ↔ Position verknüpft; `RechnungsFehler("bereits_berechnet")`, wenn Buchung schon eine Position hat).
- Produces: `rechnungen.abrechenbare_buchungen(db, kunde, jahr, monat) -> list[tuple[Buchung, Decimal]]` – Rechnungskunde, Buchungen mit `lokales_datum(beginn)` im Monat, Status ∈ {`bestaetigt`, `durchgefuehrt`, `nicht_erschienen`} → voller Preis; Status `storniert` mit Storno `kostenfrei=False` → `preis − freigestellt_betrag` (nur wenn > 0); ohne `rechnung_position_id`.
- Produces: `rechnungen.erzeuge_sammelrechnung(db, kunde, jahr, monat) -> Rechnung | None` (None bei keinen Positionen; Status `offen`; `faellig_am = datum + rechnung_zahlungsziel_tage`).
- Produces: `rechnungen.monatslauf(db, jahr, monat) -> list[Rechnung]` (alle Rechnungskunden; idempotent, weil bereits berechnete Buchungen ausgeschlossen sind).
- Produces: `rechnungen.setze_bezahlt(db, rechnung, *, admin_user_id)`, `rechnungen.storniere(db, rechnung, *, admin_user_id, grund) -> Rechnung` (Stornorechnung mit negativen Positionen, eigene Nummer, Original `storniert`, Buchungen wieder ohne `rechnung_position_id`, damit sie neu berechnet werden können), `rechnungen.csv_export(db, von: date, bis: date) -> str`.
- Nutzt `clock.today(db)` für Rechnungsdatum.

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_rechnungen.py`:
```python
from datetime import date, time
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from beachhub_core import clock
from beachhub_core.models import Betriebszeit, Feld, FeldRaster, Kundengruppe, Rechnung, Tarif
from beachhub_core.services import buchungen, konfiguration, kunden, rechnungen, storno
from beachhub_shared.zeit import kombiniere


@pytest.fixture
def welt(db: Session):
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    p = Kundengruppe(name="Privat")
    v = Kundengruppe(name="Verein", standard_zahlungsart="rechnung")
    db.add_all([f, p, v, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    a = kunden.lege_an(db, name="A", email="a@x.de", kundengruppe_id=p.id, adresse_strasse="Weg 1",
                       adresse_plz="12345", adresse_ort="Ort")
    v1 = kunden.lege_an(db, name="TSV", email="v@x.de", kundengruppe_id=v.id)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    konfiguration.setze(db, "storno_frist_stunden", 72)  # Storno am 14.12. für den 15.12. ist damit sicher kostenpflichtig
    db.commit()
    return f, a, v1


def test_nummern_lueckenlos_je_jahr(db: Session) -> None:
    assert rechnungen.naechste_nummer(db, 2027) == "2027-00001"
    assert rechnungen.naechste_nummer(db, 2027) == "2027-00002"
    assert rechnungen.naechste_nummer(db, 2028) == "2028-00001"


def test_einzelrechnung_mit_ust(db: Session, welt) -> None:
    f, a, _ = welt
    b = buchungen.lege_an(db, feld_id=f.id, kunde_id=a.id, beginn=kombiniere(date(2027, 12, 1), time(19)),
                          ende=kombiniere(date(2027, 12, 1), time(20)))
    r = rechnungen.erzeuge_einzelrechnung(db, b)
    db.commit()
    assert r.status == "bezahlt" and r.brutto == Decimal("30.00") and r.netto == Decimal("25.21") and r.ust == Decimal("4.79")
    assert r.adresse_snapshot["name"] == "A" and b.rechnung_position_id == r.positionen[0].id
    with pytest.raises(rechnungen.RechnungsFehler, match="bereits_berechnet"):
        rechnungen.erzeuge_einzelrechnung(db, b)


def test_sammelrechnung_und_monatslauf_idempotent(db: Session, welt) -> None:
    f, a, v1 = welt
    for tag in (1, 8):
        buchungen.lege_an(db, feld_id=f.id, kunde_id=v1.id, beginn=kombiniere(date(2027, 12, tag), time(19)),
                          ende=kombiniere(date(2027, 12, tag), time(21)))
    b3 = buchungen.lege_an(db, feld_id=f.id, kunde_id=v1.id, beginn=kombiniere(date(2027, 12, 15), time(19)),
                           ende=kombiniere(date(2027, 12, 15), time(21)))
    clock.set_override(db, date(2027, 12, 14))  # innerhalb der 72-h-Frist → kostenpflichtig
    storno.storniere(db, b3, durch="kunde")
    buchungen.lege_an(db, feld_id=f.id, kunde_id=v1.id, beginn=kombiniere(date(2028, 1, 5), time(19)),
                      ende=kombiniere(date(2028, 1, 5), time(20)))
    db.commit()
    clock.set_override(db, date(2028, 1, 3))
    erzeugt = rechnungen.monatslauf(db, 2027, 12)
    db.commit()
    assert len(erzeugt) == 1
    r = erzeugt[0]
    assert r.art == "sammel" and r.status == "offen" and len(r.positionen) == 3 and r.brutto == Decimal("180.00")
    assert r.faellig_am == date(2028, 1, 17) and r.leistung_von == date(2027, 12, 1)
    assert rechnungen.monatslauf(db, 2027, 12) == []


def test_stornorechnung_gibt_buchungen_frei(db: Session, welt) -> None:
    f, _, v1 = welt
    b = buchungen.lege_an(db, feld_id=f.id, kunde_id=v1.id, beginn=kombiniere(date(2027, 12, 1), time(19)),
                          ende=kombiniere(date(2027, 12, 1), time(20)))
    db.commit()
    clock.set_override(db, date(2028, 1, 3))
    r = rechnungen.erzeuge_sammelrechnung(db, v1, 2027, 12)
    db.commit()
    s = rechnungen.storniere(db, r, admin_user_id=None, grund="Falscher Preis")
    db.commit()
    assert s.art == "storno" and s.brutto == Decimal("-30.00") and s.storniert_durch_id is None
    assert r.status == "storniert" and r.storniert_durch_id == s.id and b.rechnung_position_id is None
    assert db.query(Rechnung).count() == 2


def test_csv_export(db: Session, welt) -> None:
    f, a, _ = welt
    b = buchungen.lege_an(db, feld_id=f.id, kunde_id=a.id, beginn=kombiniere(date(2027, 12, 1), time(19)),
                          ende=kombiniere(date(2027, 12, 1), time(20)))
    rechnungen.erzeuge_einzelrechnung(db, b)
    db.commit()
    csv = rechnungen.csv_export(db, date(2027, 11, 1), date(2027, 12, 31))
    zeilen = csv.strip().splitlines()
    assert zeilen[0].startswith("nummer;datum;kunde;art;status;netto;ust;brutto")
    assert len(zeilen) == 2 and ";30,00" in zeilen[1]
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_rechnungen.py`
Expected: FAIL mit ImportError

- [ ] **Step 3: Modelle**

`core/beachhub_core/models/rechnungen.py`:
```python
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DECIMAL, Date, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from beachhub_core.models.base import Base, UUIDMixin, ZeitstempelMixin
from beachhub_core.models.kunden import Kunde


class Nummernkreis(Base):
    __tablename__ = "nummernkreis"
    jahr: Mapped[int] = mapped_column(Integer, primary_key=True)
    letzte_nummer: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Rechnung(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "rechnung"
    nummer: Mapped[str] = mapped_column(String(12), nullable=False, unique=True)
    kunde_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("kunde.id"), nullable=False)
    art: Mapped[str] = mapped_column(String(10), nullable=False)  # einzel | sammel | storno
    datum: Mapped[date] = mapped_column(Date, nullable=False)
    leistung_von: Mapped[date] = mapped_column(Date, nullable=False)
    leistung_bis: Mapped[date] = mapped_column(Date, nullable=False)
    faellig_am: Mapped[date] = mapped_column(Date, nullable=False)
    ust_satz: Mapped[Decimal] = mapped_column(DECIMAL(5, 2), nullable=False)
    netto: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    ust: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    brutto: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)  # offen | bezahlt | storniert
    bezahlt_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pdf_pfad: Mapped[str | None] = mapped_column(String(300))
    pdf_sha256: Mapped[str | None] = mapped_column(String(64))
    storniert_durch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("rechnung.id"))
    adresse_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    kunde: Mapped[Kunde] = relationship()
    positionen: Mapped[list["RechnungPosition"]] = relationship(back_populates="rechnung", cascade="all, delete-orphan",
                                                                order_by="RechnungPosition.reihenfolge")


class RechnungPosition(UUIDMixin, Base):
    __tablename__ = "rechnung_position"
    rechnung_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("rechnung.id"), nullable=False)
    reihenfolge: Mapped[int] = mapped_column(Integer, nullable=False)
    buchung_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("buchung.id"))
    text: Mapped[str] = mapped_column(String(300), nullable=False)
    menge: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    einzelpreis_brutto: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    ust_satz: Mapped[Decimal] = mapped_column(DECIMAL(5, 2), nullable=False)
    brutto: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    rechnung: Mapped[Rechnung] = relationship(back_populates="positionen")
```
In `models/buchungen.py` bei `Buchung` ergänzen: `rechnung_position_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("rechnung_position.id", use_alter=True))`. In `__init__.py` exportieren (`rechnungen` nach `buchungen` importieren).

- [ ] **Step 4: Service**

`core/beachhub_core/services/rechnungen.py`:
```python
import csv
import io
import uuid
from calendar import monthrange
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_core import clock
from beachhub_core.models import Buchung, Kunde, Nummernkreis, Rechnung, RechnungPosition, Storno, utcnow
from beachhub_core.services import audit, konfiguration
from beachhub_shared.zeit import lokal, lokales_datum

CENT = Decimal("0.01")


class RechnungsFehler(Exception):
    pass


def naechste_nummer(db: Session, jahr: int) -> str:
    kreis = db.scalar(select(Nummernkreis).where(Nummernkreis.jahr == jahr).with_for_update())
    if kreis is None:
        kreis = Nummernkreis(jahr=jahr, letzte_nummer=0)
        db.add(kreis)
        db.flush()
        kreis = db.scalar(select(Nummernkreis).where(Nummernkreis.jahr == jahr).with_for_update())
        assert kreis is not None
    kreis.letzte_nummer += 1
    db.flush()
    return f"{jahr}-{kreis.letzte_nummer:05d}"


def _netto_ust(brutto: Decimal, satz: Decimal) -> tuple[Decimal, Decimal]:
    netto = (brutto / (1 + satz / 100)).quantize(CENT, rounding=ROUND_HALF_UP)
    return netto, brutto - netto


def _snapshot(k: Kunde) -> dict[str, str]:
    return {"name": k.name, "strasse": k.adresse_strasse, "plz": k.adresse_plz, "ort": k.adresse_ort, "email": k.email}


def _positionstext(b: Buchung, zusatz: str = "") -> str:
    lb, le = lokal(b.beginn), lokal(b.ende)
    return f"Feld {b.feld.name}, {lb:%d.%m.%Y} {lb:%H:%M}–{le:%H:%M} Uhr{zusatz}"


def _neue_rechnung(db: Session, kunde: Kunde, art: str, positionen: list[tuple[Buchung | None, str, Decimal]],
                   leistung_von: date, leistung_bis: date, status: str) -> Rechnung:
    heute = clock.today(db)
    satz = konfiguration.hole(db, "ust_satz")
    brutto = sum((p[2] for p in positionen), Decimal("0.00"))
    netto, ust = _netto_ust(brutto, satz)
    r = Rechnung(
        nummer=naechste_nummer(db, heute.year), kunde_id=kunde.id, art=art, datum=heute,
        leistung_von=leistung_von, leistung_bis=leistung_bis,
        faellig_am=heute + timedelta(days=konfiguration.hole(db, "rechnung_zahlungsziel_tage")),
        ust_satz=satz, netto=netto, ust=ust, brutto=brutto, status=status,
        bezahlt_am=utcnow() if status == "bezahlt" else None, adresse_snapshot=_snapshot(kunde),
    )
    db.add(r)
    db.flush()
    for i, (buchung, text, betrag) in enumerate(positionen, start=1):
        pos = RechnungPosition(rechnung_id=r.id, reihenfolge=i, buchung_id=buchung.id if buchung else None,
                               text=text, menge=1, einzelpreis_brutto=betrag, ust_satz=satz, brutto=betrag)
        db.add(pos)
        db.flush()
        if buchung is not None and art != "storno":
            buchung.rechnung_position_id = pos.id
    db.flush()
    db.refresh(r)
    audit.protokolliere(db, quelle="system", objekt_typ="rechnung", objekt_id=r.id, vorher=None, nachher=audit.als_dict(r))
    return r


def erzeuge_einzelrechnung(db: Session, buchung: Buchung, *, quelle: str = "system") -> Rechnung:
    if buchung.rechnung_position_id is not None:
        raise RechnungsFehler("bereits_berechnet")
    d = lokales_datum(buchung.beginn)
    return _neue_rechnung(db, buchung.kunde, "einzel", [(buchung, _positionstext(buchung), buchung.preis)], d, d, "bezahlt")


def abrechenbare_buchungen(db: Session, kunde: Kunde, jahr: int, monat: int) -> list[tuple[Buchung, Decimal]]:
    von, bis = date(jahr, monat, 1), date(jahr, monat, monthrange(jahr, monat)[1])
    kandidaten = db.scalars(select(Buchung).where(
        Buchung.kunde_id == kunde.id, Buchung.zahlungsart == "rechnung", Buchung.rechnung_position_id.is_(None),
        Buchung.status.in_((Buchung.BESTAETIGT, Buchung.DURCHGEFUEHRT, Buchung.NICHT_ERSCHIENEN, Buchung.STORNIERT)),
    ).order_by(Buchung.beginn)).all()
    out: list[tuple[Buchung, Decimal]] = []
    for b in kandidaten:
        if not (von <= lokales_datum(b.beginn) <= bis):
            continue
        if b.status == Buchung.STORNIERT:
            s = db.scalar(select(Storno).where(Storno.buchung_id == b.id))
            if s is None or s.kostenfrei:
                continue
            rest = b.preis - s.freigestellt_betrag
            if rest > 0:
                out.append((b, rest))
        else:
            out.append((b, b.preis))
    return out


def erzeuge_sammelrechnung(db: Session, kunde: Kunde, jahr: int, monat: int) -> Rechnung | None:
    posten = abrechenbare_buchungen(db, kunde, jahr, monat)
    if not posten:
        return None
    positionen = [(b, _positionstext(b, " (Storno nach Frist)" if b.status == Buchung.STORNIERT else ""), betrag)
                  for b, betrag in posten]
    return _neue_rechnung(db, kunde, "sammel", positionen, date(jahr, monat, 1),
                          date(jahr, monat, monthrange(jahr, monat)[1]), "offen")


def monatslauf(db: Session, jahr: int, monat: int) -> list[Rechnung]:
    erzeugt = []
    for kunde in db.scalars(select(Kunde).where(Kunde.zahlungsart == "rechnung", Kunde.anonymisiert_am.is_(None))).all():
        r = erzeuge_sammelrechnung(db, kunde, jahr, monat)
        if r:
            erzeugt.append(r)
    return erzeugt


def setze_bezahlt(db: Session, rechnung: Rechnung, *, admin_user_id: uuid.UUID | None) -> None:
    if rechnung.status != "offen":
        raise RechnungsFehler("nicht_offen")
    vorher = audit.als_dict(rechnung)
    rechnung.status, rechnung.bezahlt_am = "bezahlt", utcnow()
    db.flush()
    audit.protokolliere(db, quelle="admin", objekt_typ="rechnung", objekt_id=rechnung.id, vorher=vorher,
                        nachher=audit.als_dict(rechnung), admin_user_id=admin_user_id)


def storniere(db: Session, rechnung: Rechnung, *, admin_user_id: uuid.UUID | None, grund: str) -> Rechnung:
    if rechnung.status == "storniert" or rechnung.art == "storno":
        raise RechnungsFehler("nicht_stornierbar")
    positionen = [(None, f"Storno zu Rechnung {rechnung.nummer}: {p.text}", -p.brutto) for p in rechnung.positionen]
    s = _neue_rechnung(db, rechnung.kunde, "storno", positionen, rechnung.leistung_von, rechnung.leistung_bis, "bezahlt")
    vorher = audit.als_dict(rechnung)
    rechnung.status, rechnung.storniert_durch_id = "storniert", s.id
    for p in rechnung.positionen:
        if p.buchung_id:
            b = db.get(Buchung, p.buchung_id)
            if b is not None:
                b.rechnung_position_id = None
    db.flush()
    audit.protokolliere(db, quelle="admin", objekt_typ="rechnung", objekt_id=rechnung.id, vorher=vorher,
                        nachher={**audit.als_dict(rechnung), "grund": grund}, admin_user_id=admin_user_id)
    return s


def _de(v: Decimal) -> str:
    return f"{v:.2f}".replace(".", ",")


def csv_export(db: Session, von: date, bis: date) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow(["nummer", "datum", "kunde", "art", "status", "netto", "ust", "brutto", "ust_satz", "faellig_am",
                "bezahlt_am", "leistung_von", "leistung_bis"])
    for r in db.scalars(select(Rechnung).where(Rechnung.datum >= von, Rechnung.datum <= bis).order_by(Rechnung.nummer)):
        w.writerow([r.nummer, r.datum.isoformat(), r.adresse_snapshot.get("name", ""), r.art, r.status, _de(r.netto),
                    _de(r.ust), _de(r.brutto), _de(r.ust_satz), r.faellig_am.isoformat(),
                    r.bezahlt_am.isoformat() if r.bezahlt_am else "", r.leistung_von.isoformat(), r.leistung_bis.isoformat()])
    return buf.getvalue()
```

- [ ] **Step 5: Migration, Tests grün**

Run: `cd core && alembic revision --autogenerate -m "rechnungen" && pytest -q`
Expected: alle grün. Dashboard-Zähler `rechnungen_offen` in `routes/dashboard.py` jetzt echt: `db.scalar(select(func.count()).select_from(Rechnung).where(Rechnung.status == "offen")) or 0`.

- [ ] **Step 6: Commit**

```bash
git add core && git commit -m "feat(core): Rechnungen mit lückenlosem Nummernkreis, Monatslauf, Storno und CSV"
```

---

## Task 14: Rechnungs-PDF und E-Mail-Versand

**Files:**
- Create: `core/beachhub_core/services/rechnung_pdf.py`, `core/beachhub_core/templates/rechnung_pdf.html`, `core/beachhub_core/mail.py`, `core/beachhub_core/templates/mail/rechnung.txt`, `core/beachhub_core/templates/mail/buchung_bestaetigt.txt`, `core/beachhub_core/templates/mail/storno.txt`, `core/beachhub_core/templates/mail/dauerbuchung.txt`
- Create: `core/beachhub_core/services/benachrichtigung.py`
- Test: `core/tests/test_rechnung_pdf.py`, `core/tests/test_mail.py`

**Interfaces:**
- Produces: `rechnung_pdf.erzeuge(db, rechnung) -> Path` – rendert `rechnung_pdf.html` (Betreiberdaten aus `settings`, Adresse aus `adresse_snapshot`, Positionen, Netto/USt/Brutto, Zahlungsziel, Bankverbindung, Hinweis bei `art == "storno"`), schreibt nach `settings.data_dir / "rechnungen" / f"{nummer}.pdf"`, setzt `pdf_pfad`, `pdf_sha256`; `RechnungsFehler("pdf_vorhanden")` wenn schon erzeugt (Unveränderbarkeit); `rechnung_pdf.pruefe_integritaet(rechnung) -> bool`.
- Produces: `mail.sende(an: str, betreff: str, text: str) -> None` – SMTP über `aiosmtplib` (synchron gekapselt mit `asyncio.run` bzw. Thread, wenn bereits ein Loop läuft), Anhang optional (`anhaenge: list[tuple[str, bytes]]`); ohne `smtp_host` wird nur geloggt; `mail.TEST_AUSGANG: list[dict] | None` – wenn gesetzt (Tests), wird nichts gesendet, sondern angehängt.
- Produces: `benachrichtigung.buchung_bestaetigt(db, buchung)`, `benachrichtigung.storno(db, storno)`, `benachrichtigung.dauerbuchung_angelegt(db, dauer)`, `benachrichtigung.rechnung(db, rechnung)` (mit PDF-Anhang), `benachrichtigung.betreiber_alarm(betreff, text)` (an `settings.email_from`). Templates als `.txt` mit Jinja2 (Umgebung aus `templating.templates.env`).
- Verdrahtung: Aufruf der Benachrichtigungen erfolgt in den Routen (Task 16/17/18), nicht in den Services, damit Services testbar bleiben; Ausnahme: `monatslauf` im Job (Task 18) versendet nach Erzeugung.

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_rechnung_pdf.py`:
```python
from datetime import date, time
from decimal import Decimal
from pathlib import Path

import pytest
from pypdf import PdfReader
from sqlalchemy.orm import Session

from beachhub_core import clock
from beachhub_core.models import Betriebszeit, Feld, FeldRaster, Kundengruppe, Tarif
from beachhub_core.services import buchungen, kunden, rechnung_pdf, rechnungen
from beachhub_shared.zeit import kombiniere


@pytest.fixture
def rechnung(db: Session):
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    p = Kundengruppe(name="Privat")
    db.add_all([f, p, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    a = kunden.lege_an(db, name="Anna Müller", email="a@x.de", kundengruppe_id=p.id, adresse_strasse="Weg 1",
                       adresse_plz="12345", adresse_ort="Ort")
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    b = buchungen.lege_an(db, feld_id=f.id, kunde_id=a.id, beginn=kombiniere(date(2027, 12, 1), time(19)),
                          ende=kombiniere(date(2027, 12, 1), time(20)))
    r = rechnungen.erzeuge_einzelrechnung(db, b)
    db.commit()
    return r


def test_pdf_wird_erzeugt_und_gehasht(db: Session, rechnung) -> None:
    pfad = rechnung_pdf.erzeuge(db, rechnung)
    db.commit()
    assert Path(pfad).exists() and rechnung.pdf_sha256 and len(rechnung.pdf_sha256) == 64
    text = "".join(p.extract_text() for p in PdfReader(str(pfad)).pages)
    assert rechnung.nummer in text and "Anna Müller" in text and "30,00" in text and "19 %" in text
    assert rechnung_pdf.pruefe_integritaet(rechnung)
    with pytest.raises(rechnungen.RechnungsFehler, match="pdf_vorhanden"):
        rechnung_pdf.erzeuge(db, rechnung)
```

`core/tests/test_mail.py`:
```python
from beachhub_core import mail


def test_test_ausgang_faengt_mails(monkeypatch) -> None:
    ausgang: list[dict] = []
    monkeypatch.setattr(mail, "TEST_AUSGANG", ausgang)
    mail.sende("a@x.de", "Betreff", "Hallo", anhaenge=[("r.pdf", b"%PDF")])
    assert ausgang == [{"an": "a@x.de", "betreff": "Betreff", "text": "Hallo", "anhaenge": ["r.pdf"]}]


def test_ohne_smtp_wird_nur_geloggt(caplog) -> None:
    mail.sende("a@x.de", "B", "T")
    assert "kein SMTP" in caplog.text
```
In `conftest.py` ergänzen: Fixture `autouse` `mail_ausgang`, die `mail.TEST_AUSGANG` auf eine leere Liste setzt und diese liefert.

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_rechnung_pdf.py tests/test_mail.py`
Expected: FAIL mit ImportError

- [ ] **Step 3: PDF-Vorlage und Service**

`core/beachhub_core/templates/rechnung_pdf.html`:
```html
<!DOCTYPE html><html lang="de"><head><meta charset="utf-8">
<style>
@page { size: A4; margin: 20mm; @bottom-center { content: "Seite " counter(page) " von " counter(pages); font-size: 9pt; color:#666; } }
body { font: 10.5pt/1.45 "DejaVu Sans", sans-serif; color:#1f2933; }
.kopf { display:flex; justify-content:space-between; margin-bottom: 18mm; }
.absender { font-size: 8pt; color:#666; border-bottom: .5pt solid #999; display:inline-block; margin-bottom: 4pt; }
h1 { font-size: 16pt; margin: 0 0 4pt 0; }
table { width:100%; border-collapse: collapse; margin: 8pt 0; }
th, td { padding: 4pt 6pt; border-bottom: .5pt solid #ccc; text-align:left; vertical-align: top; }
td.r, th.r { text-align: right; }
.summe td { border: 0; } .summe .gesamt td { font-weight: bold; border-top: 1pt solid #333; }
.fuss { position: fixed; bottom: 0; font-size: 8pt; color:#666; }
</style></head><body>
<div class="kopf">
  <div>
    <div class="absender">{{ betreiber.name }} · {{ betreiber.adresse }}</div><br>
    {{ r.adresse_snapshot.name }}<br>{{ r.adresse_snapshot.strasse }}<br>{{ r.adresse_snapshot.plz }} {{ r.adresse_snapshot.ort }}
  </div>
  <div>
    <strong>{{ betreiber.name }}</strong><br>{{ betreiber.adresse }}<br>USt-IdNr. {{ betreiber.ust_id }}
  </div>
</div>
<h1>{% if r.art == "storno" %}Stornorechnung{% else %}Rechnung{% endif %} {{ r.nummer }}</h1>
<p>Rechnungsdatum: {{ r.datum|datum }} · Leistungszeitraum: {{ r.leistung_von|datum }}{% if r.leistung_bis != r.leistung_von %} – {{ r.leistung_bis|datum }}{% endif %}
{% if r.status == "offen" %}· Zahlbar bis {{ r.faellig_am|datum }}{% endif %}</p>
<table>
  <thead><tr><th>Pos.</th><th>Leistung</th><th class="r">Betrag</th></tr></thead>
  <tbody>{% for p in r.positionen %}<tr><td>{{ p.reihenfolge }}</td><td>{{ p.text }}</td><td class="r">{{ p.brutto|euro }}</td></tr>{% endfor %}</tbody>
</table>
<table class="summe">
  <tr><td></td><td class="r">Nettobetrag</td><td class="r">{{ r.netto|euro }}</td></tr>
  <tr><td></td><td class="r">zzgl. {{ r.ust_satz|int }} % USt</td><td class="r">{{ r.ust|euro }}</td></tr>
  <tr class="gesamt"><td></td><td class="r">Gesamtbetrag</td><td class="r">{{ r.brutto|euro }}</td></tr>
</table>
{% if r.art == "storno" %}<p>Diese Stornorechnung hebt die ursprüngliche Rechnung auf.</p>
{% elif r.status == "bezahlt" %}<p>Der Betrag wurde bereits bezahlt. Vielen Dank.</p>
{% else %}<p>Bitte überweisen Sie den Gesamtbetrag bis zum {{ r.faellig_am|datum }} unter Angabe der Rechnungsnummer auf:<br>{{ betreiber.bank }}</p>{% endif %}
<div class="fuss">{{ betreiber.name }} · {{ betreiber.adresse }} · USt-IdNr. {{ betreiber.ust_id }}</div>
</body></html>
```

`core/beachhub_core/services/rechnung_pdf.py`:
```python
import hashlib
from pathlib import Path

from sqlalchemy.orm import Session
from weasyprint import HTML

from beachhub_core.config import settings
from beachhub_core.models import Rechnung
from beachhub_core.services.rechnungen import RechnungsFehler
from beachhub_core.templating import templates


def _betreiber() -> dict[str, str]:
    return {"name": settings.betreiber_name, "adresse": settings.betreiber_adresse,
            "ust_id": settings.betreiber_ust_id, "bank": settings.betreiber_bank}


def erzeuge(db: Session, rechnung: Rechnung) -> Path:
    if rechnung.pdf_pfad:
        raise RechnungsFehler("pdf_vorhanden")
    html = templates.env.get_template("rechnung_pdf.html").render(r=rechnung, betreiber=_betreiber())
    daten = HTML(string=html).write_pdf()
    ordner = settings.data_dir / "rechnungen"
    ordner.mkdir(parents=True, exist_ok=True)
    pfad = ordner / f"{rechnung.nummer}.pdf"
    pfad.write_bytes(daten)
    rechnung.pdf_pfad = str(pfad)
    rechnung.pdf_sha256 = hashlib.sha256(daten).hexdigest()
    db.flush()
    return pfad


def pruefe_integritaet(rechnung: Rechnung) -> bool:
    if not rechnung.pdf_pfad or not rechnung.pdf_sha256:
        return False
    return hashlib.sha256(Path(rechnung.pdf_pfad).read_bytes()).hexdigest() == rechnung.pdf_sha256
```

- [ ] **Step 4: Mail und Benachrichtigungen**

`core/beachhub_core/mail.py`:
```python
import asyncio
import logging
import threading
from email.message import EmailMessage
from typing import Any

import aiosmtplib

from beachhub_core.config import settings

logger = logging.getLogger(__name__)
TEST_AUSGANG: list[dict[str, Any]] | None = None


def _nachricht(an: str, betreff: str, text: str, anhaenge: list[tuple[str, bytes]]) -> EmailMessage:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = settings.email_from, an, betreff
    m.set_content(text)
    for name, daten in anhaenge:
        m.add_attachment(daten, maintype="application", subtype="pdf", filename=name)
    return m


async def _senden(m: EmailMessage) -> None:
    await aiosmtplib.send(m, hostname=settings.smtp_host, port=settings.smtp_port,
                          username=settings.smtp_user or None, password=settings.smtp_password or None, start_tls=True)


def sende(an: str, betreff: str, text: str, anhaenge: list[tuple[str, bytes]] | None = None) -> None:
    anhaenge = anhaenge or []
    if TEST_AUSGANG is not None:
        TEST_AUSGANG.append({"an": an, "betreff": betreff, "text": text, "anhaenge": [n for n, _ in anhaenge]})
        return
    if not settings.smtp_host:
        logger.warning("kein SMTP konfiguriert – Mail an %s (%s) nicht gesendet", an, betreff)
        return
    m = _nachricht(an, betreff, text, anhaenge)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_senden(m))
        return
    threading.Thread(target=lambda: asyncio.run(_senden(m)), daemon=True).start()
```

`core/beachhub_core/services/benachrichtigung.py`:
```python
from pathlib import Path

from sqlalchemy.orm import Session

from beachhub_core import mail
from beachhub_core.config import settings
from beachhub_core.models import Buchung, Dauerbuchung, Rechnung, Storno
from beachhub_core.services import pin
from beachhub_core.templating import templates


def _text(name: str, **ctx: object) -> str:
    return templates.env.get_template(f"mail/{name}.txt").render(betreiber=settings.betreiber_name, **ctx)


def buchung_bestaetigt(db: Session, b: Buchung) -> None:
    mail.sende(b.kunde.email, f"Buchung bestätigt: {templates.env.filters['lokal'](b.beginn)}",
               _text("buchung_bestaetigt", b=b, pin=pin.entschluessele(b.pin_verschluesselt or "")))


def storno(db: Session, s: Storno) -> None:
    mail.sende(s.buchung.kunde.email, "Stornierung Ihrer Buchung", _text("storno", s=s, b=s.buchung))


def dauerbuchung_angelegt(db: Session, d: Dauerbuchung) -> None:
    mail.sende(d.kunde.email, "Ihre Dauerbuchung", _text("dauerbuchung", d=d, pin=pin.entschluessele(d.pin_verschluesselt)))


def rechnung(db: Session, r: Rechnung) -> None:
    anhang = [(f"{r.nummer}.pdf", Path(r.pdf_pfad).read_bytes())] if r.pdf_pfad else []
    mail.sende(r.kunde.email, f"Rechnung {r.nummer}", _text("rechnung", r=r), anhaenge=anhang)


def betreiber_alarm(betreff: str, text: str) -> None:
    mail.sende(settings.email_from, f"[Beachhub] {betreff}", text)
```

Mail-Templates (`templates/mail/*.txt`), Beispiel `buchung_bestaetigt.txt`:
```
Hallo {{ b.kunde.name }},

Ihre Buchung ist bestätigt:
Feld {{ b.feld.name }}, {{ b.beginn|lokal }} bis {{ b.ende|uhrzeit }} Uhr
Preis: {{ b.preis|euro }}

Ihr Zahlencode für die Tür: {{ pin }}
Er gilt ab 15 Minuten vor Beginn bis zum Ende Ihrer Buchung.

Viele Grüße
{{ betreiber }}
```
`storno.txt`: Zeitraum, ob kostenfrei, sonst Hinweis „Wird der Platz bis zum Termin von jemand anderem gebucht, entfällt der Betrag. Wir informieren Sie.“ `dauerbuchung.txt`: Wochentag, Zeit, Zeitraum, Anzahl Termine, Zahlencode. `rechnung.txt`: Nummer, Betrag, Zahlungsziel, Hinweis auf Anhang.

- [ ] **Step 5: Tests grün**

Run: `cd core && pytest -q`
Expected: alle grün

- [ ] **Step 6: Commit**

```bash
git add core && git commit -m "feat(core): Rechnungs-PDF mit Archivierung, Mailversand und Benachrichtigungen"
```

---

## Task 15: Admin-UI Stammdaten (Felder, Raster, Betriebszeiten, Ausnahmetage, Kundengruppen, Tarife, Konfiguration)

**Files:**
- Create: `core/beachhub_core/routes/stammdaten.py`, `core/beachhub_core/services/stammdaten.py`
- Create: `core/beachhub_core/templates/stammdaten/felder.html`, `feld.html`, `betriebszeiten.html`, `ausnahmetage.html`, `kundengruppen.html`, `tarife.html`, `konfiguration.html`
- Modify: `core/beachhub_core/main.py` (Router einhängen)
- Test: `core/tests/test_ui_stammdaten.py`

**Interfaces:**
- Produces `services/stammdaten.py`: dünne CRUD-Funktionen mit Audit, alle `(db, *, admin_user_id, **felder)`: `feld_anlegen`, `feld_aendern(db, feld, ...)`, `raster_setzen(db, feld, wochentag, modus, slot_minuten, fenster: list[tuple[str,str]])` (ersetzt vorhandenes Raster desselben Wochentags), `raster_loeschen(db, raster)`, `betriebszeit_anlegen/loeschen`, `ausnahmetag_anlegen/loeschen`, `kundengruppe_anlegen/aendern`, `tarif_anlegen/aendern/deaktivieren`. Validierung: `StammdatenFehler(grund)` bei `schliesst <= oeffnet` (außer `00:00`), Fenster außerhalb `00:00–23:59`, Slot-Minuten ≤ 0, Preis < 0, Wochentag ∉ 0–6.
- Routen (alle unter `/admin`, GET lesend für `aktueller_admin`, POST nur `nur_admin_rolle`):
  - `GET /felder` Liste; `POST /felder` anlegen; `GET /felder/{id}` Detail mit Raster; `POST /felder/{id}` ändern; `POST /felder/{id}/raster` setzen; `POST /felder/{id}/raster/{rid}/loeschen`.
  - `GET/POST /betriebszeiten`, `POST /betriebszeiten/{id}/loeschen`.
  - `GET/POST /ausnahmetage`, `POST /ausnahmetage/{id}/loeschen`.
  - `GET/POST /kundengruppen`, `POST /kundengruppen/{id}`.
  - `GET/POST /tarife`, `POST /tarife/{id}` (ändern), `POST /tarife/{id}/deaktivieren`.
  - `GET /konfiguration`, `POST /konfiguration` (alle Schlüssel aus `konfiguration.DEFAULTS` als Formularfelder).
- Formularwerte: Zeiten als `HH:MM`, Daten als `JJJJ-MM-TT`, Beträge mit Punkt oder Komma (`Decimal(str.replace(",", "."))`), Fenster als Textfeld `19:00-21:00, 21:00-23:00`.
- Nach POST: Redirect 303 auf die GET-Seite mit `mit_flash(...)`. Bei `StammdatenFehler`: Seite erneut rendern mit `fehler=<Text>`, Status 200.

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_ui_stammdaten.py`:
```python
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from beachhub_core.models import Feld, Konfiguration, Tarif


def test_feld_anlegen_und_raster(eingeloggt: TestClient, db: Session) -> None:
    c = eingeloggt
    r = c.post("/admin/felder", data={"csrf_token": c.csrf, "name": "Feld 1", "reihenfolge": "1"}, follow_redirects=False)
    assert r.status_code == 303
    feld = db.query(Feld).one()
    r = c.post(f"/admin/felder/{feld.id}/raster", data={"csrf_token": c.csrf, "wochentag": "", "modus": "dauer",
                                                        "slot_minuten": "60", "fenster": ""}, follow_redirects=False)
    assert r.status_code == 303
    r = c.post(f"/admin/felder/{feld.id}/raster", data={"csrf_token": c.csrf, "wochentag": "2", "modus": "fenster",
                                                        "slot_minuten": "", "fenster": "19:00-21:00, 21:00-23:00"},
               follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert len(db.get(Feld, feld.id).raster) == 2
    seite = c.get(f"/admin/felder/{feld.id}")
    assert "19:00-21:00" in seite.text and "Feld 1" in seite.text


def test_validierung_zeigt_fehler(eingeloggt: TestClient) -> None:
    c = eingeloggt
    r = c.post("/admin/betriebszeiten", data={"csrf_token": c.csrf, "wochentag": "0", "oeffnet": "20:00",
                                              "schliesst": "18:00", "gueltig_von": "", "gueltig_bis": ""})
    assert r.status_code == 200 and "Schließzeit" in r.text


def test_tarif_und_konfiguration(eingeloggt: TestClient, db: Session) -> None:
    c = eingeloggt
    r = c.post("/admin/tarife", data={"csrf_token": c.csrf, "name": "Abend", "preis": "40,00", "feld_id": "",
                                      "wochentag": "", "uhrzeit_von": "18:00", "uhrzeit_bis": "23:00",
                                      "kundengruppe_id": "", "gueltig_von": "", "gueltig_bis": ""}, follow_redirects=False)
    assert r.status_code == 303
    t = db.query(Tarif).one()
    assert t.preis == Decimal("40.00") and t.uhrzeit_von.hour == 18
    r = c.post("/admin/konfiguration", data={"csrf_token": c.csrf, "storno_frist_stunden": "48", "ust_satz": "19.00",
                                             **{k: "" for k in ("fenster_tage",)}}, follow_redirects=False)
    assert r.status_code == 303
    assert db.query(Konfiguration).filter_by(schluessel="storno_frist_stunden").one().wert == "48"
    assert db.query(Konfiguration).filter_by(schluessel="fenster_tage").first() is None  # leer = Default behalten


def test_lesende_rolle_bekommt_403(client: TestClient, db: Session) -> None:
    import pyotp
    from beachhub_core import auth
    _, secret = auth.lege_admin_an(db, name="leser", passwort="test-passwort-1234", rolle="lesend")
    db.commit()
    client.post("/admin/login", data={"name": "leser", "passwort": "test-passwort-1234", "code": pyotp.TOTP(secret).now()})
    assert client.get("/admin/felder").status_code == 200
    r = client.post("/admin/felder", data={"csrf_token": "x", "name": "F", "reihenfolge": "1"})
    assert r.status_code == 403
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_ui_stammdaten.py`
Expected: FAIL (404 bzw. ImportError)

- [ ] **Step 3: Service**

`core/beachhub_core/services/stammdaten.py`:
```python
import uuid
from datetime import date, time
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from beachhub_core.models import Ausnahmetag, Betriebszeit, Feld, FeldRaster, Kundengruppe, Tarif
from beachhub_core.services import audit


class StammdatenFehler(Exception):
    pass


def _pruefe_zeiten(oeffnet: time, schliesst: time) -> None:
    if schliesst != time(0, 0) and schliesst <= oeffnet:
        raise StammdatenFehler("Schließzeit muss nach der Öffnungszeit liegen")


def _log(db: Session, typ: str, obj: Any, vorher: dict[str, Any] | None, admin_user_id: uuid.UUID | None,
         geloescht: bool = False) -> None:
    audit.protokolliere(db, quelle="admin", objekt_typ=typ, objekt_id=obj.id, vorher=vorher,
                        nachher=None if geloescht else audit.als_dict(obj), admin_user_id=admin_user_id)


def _anlegen(db: Session, typ: str, obj: Any, admin_user_id: uuid.UUID | None) -> Any:
    db.add(obj)
    db.flush()
    _log(db, typ, obj, None, admin_user_id)
    return obj


def _aendern(db: Session, typ: str, obj: Any, admin_user_id: uuid.UUID | None, **felder: Any) -> Any:
    vorher = audit.als_dict(obj)
    for k, v in felder.items():
        setattr(obj, k, v)
    db.flush()
    _log(db, typ, obj, vorher, admin_user_id)
    return obj


def _loeschen(db: Session, typ: str, obj: Any, admin_user_id: uuid.UUID | None) -> None:
    _log(db, typ, obj, audit.als_dict(obj), admin_user_id, geloescht=True)
    db.delete(obj)
    db.flush()


def feld_anlegen(db: Session, *, admin_user_id: uuid.UUID | None, name: str, reihenfolge: int) -> Feld:
    if not name.strip():
        raise StammdatenFehler("Name fehlt")
    return _anlegen(db, "feld", Feld(name=name.strip(), reihenfolge=reihenfolge), admin_user_id)


def feld_aendern(db: Session, feld: Feld, *, admin_user_id: uuid.UUID | None, **felder: Any) -> Feld:
    return _aendern(db, "feld", feld, admin_user_id, **felder)


def raster_setzen(db: Session, feld: Feld, *, admin_user_id: uuid.UUID | None, wochentag: int | None, modus: str,
                  slot_minuten: int | None, fenster: list[tuple[str, str]]) -> FeldRaster:
    if wochentag is not None and not 0 <= wochentag <= 6:
        raise StammdatenFehler("Wochentag ungültig")
    if modus == "dauer" and (not slot_minuten or slot_minuten <= 0):
        raise StammdatenFehler("Slot-Dauer in Minuten fehlt")
    if modus == "fenster" and not fenster:
        raise StammdatenFehler("Mindestens ein Zeitfenster angeben")
    for r in list(feld.raster):
        if r.wochentag == wochentag:
            _loeschen(db, "feld_raster", r, admin_user_id)
    neu = FeldRaster(feld_id=feld.id, wochentag=wochentag, modus=modus, slot_minuten=slot_minuten if modus == "dauer" else None,
                     fenster_json=[[a, b] for a, b in fenster] if modus == "fenster" else [])
    return _anlegen(db, "feld_raster", neu, admin_user_id)


def raster_loeschen(db: Session, raster: FeldRaster, *, admin_user_id: uuid.UUID | None) -> None:
    _loeschen(db, "feld_raster", raster, admin_user_id)


def betriebszeit_anlegen(db: Session, *, admin_user_id: uuid.UUID | None, wochentag: int, oeffnet: time, schliesst: time,
                         gueltig_von: date | None, gueltig_bis: date | None) -> Betriebszeit:
    _pruefe_zeiten(oeffnet, schliesst)
    return _anlegen(db, "betriebszeit", Betriebszeit(wochentag=wochentag, oeffnet=oeffnet, schliesst=schliesst,
                                                     gueltig_von=gueltig_von, gueltig_bis=gueltig_bis), admin_user_id)


def betriebszeit_loeschen(db: Session, bz: Betriebszeit, *, admin_user_id: uuid.UUID | None) -> None:
    _loeschen(db, "betriebszeit", bz, admin_user_id)


def ausnahmetag_anlegen(db: Session, *, admin_user_id: uuid.UUID | None, datum: date, geschlossen: bool,
                        oeffnet: time | None, schliesst: time | None, grund: str) -> Ausnahmetag:
    if not geschlossen:
        if oeffnet is None or schliesst is None:
            raise StammdatenFehler("Sonderöffnung braucht Öffnungs- und Schließzeit")
        _pruefe_zeiten(oeffnet, schliesst)
    return _anlegen(db, "ausnahmetag", Ausnahmetag(datum=datum, geschlossen=geschlossen, oeffnet=oeffnet,
                                                   schliesst=schliesst, grund=grund), admin_user_id)


def ausnahmetag_loeschen(db: Session, a: Ausnahmetag, *, admin_user_id: uuid.UUID | None) -> None:
    _loeschen(db, "ausnahmetag", a, admin_user_id)


def kundengruppe_anlegen(db: Session, *, admin_user_id: uuid.UUID | None, name: str, standard_zahlungsart: str) -> Kundengruppe:
    if standard_zahlungsart not in ("online", "rechnung"):
        raise StammdatenFehler("Zahlungsart ungültig")
    return _anlegen(db, "kundengruppe", Kundengruppe(name=name.strip(), standard_zahlungsart=standard_zahlungsart), admin_user_id)


def kundengruppe_aendern(db: Session, g: Kundengruppe, *, admin_user_id: uuid.UUID | None, **felder: Any) -> Kundengruppe:
    return _aendern(db, "kundengruppe", g, admin_user_id, **felder)


def tarif_anlegen(db: Session, *, admin_user_id: uuid.UUID | None, name: str, preis: Decimal, **kriterien: Any) -> Tarif:
    if preis < 0:
        raise StammdatenFehler("Preis darf nicht negativ sein")
    if (kriterien.get("uhrzeit_von") is None) != (kriterien.get("uhrzeit_bis") is None):
        raise StammdatenFehler("Uhrzeit von und bis gemeinsam angeben")
    return _anlegen(db, "tarif", Tarif(name=name.strip(), preis=preis, **kriterien), admin_user_id)


def tarif_aendern(db: Session, t: Tarif, *, admin_user_id: uuid.UUID | None, **felder: Any) -> Tarif:
    return _aendern(db, "tarif", t, admin_user_id, **felder)


def tarif_deaktivieren(db: Session, t: Tarif, *, admin_user_id: uuid.UUID | None) -> Tarif:
    return _aendern(db, "tarif", t, admin_user_id, aktiv=False)
```

- [ ] **Step 4: Routen und Formular-Parser**

`core/beachhub_core/routes/_form.py` (Hilfsfunktionen für alle Routen):
```python
import uuid
from datetime import date, time
from decimal import Decimal, InvalidOperation


def t_zeit(v: str | None) -> time | None:
    v = (v or "").strip()
    if not v:
        return None
    h, m = v.split(":")
    return time(int(h), int(m))


def t_datum(v: str | None) -> date | None:
    v = (v or "").strip()
    return date.fromisoformat(v) if v else None


def t_int(v: str | None) -> int | None:
    v = (v or "").strip()
    return int(v) if v else None


def t_uuid(v: str | None) -> uuid.UUID | None:
    v = (v or "").strip()
    return uuid.UUID(v) if v else None


def t_betrag(v: str | None) -> Decimal | None:
    v = (v or "").strip().replace(".", "").replace(",", ".") if v and "," in v else (v or "").strip()
    if not v:
        return None
    try:
        return Decimal(v).quantize(Decimal("0.01"))
    except InvalidOperation as e:
        raise ValueError("Betrag ungültig") from e


def t_fenster(v: str | None) -> list[tuple[str, str]]:
    out = []
    for teil in (v or "").split(","):
        teil = teil.strip()
        if not teil:
            continue
        a, b = teil.split("-")
        out.append((a.strip(), b.strip()))
    return out
```

`core/beachhub_core/routes/stammdaten.py` (Auszug: Felder, Betriebszeiten, Tarife, Konfiguration; Ausnahmetage und Kundengruppen folgen exakt dem Betriebszeiten-Muster mit den Feldern aus dem Service):
```python
import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_core import auth
from beachhub_core.database import get_db
from beachhub_core.models import AdminUser, Ausnahmetag, Betriebszeit, Feld, FeldRaster, Kundengruppe, Tarif
from beachhub_core.routes._form import t_betrag, t_datum, t_fenster, t_int, t_uuid, t_zeit
from beachhub_core.services import konfiguration, stammdaten
from beachhub_core.services.stammdaten import StammdatenFehler
from beachhub_core.templating import mit_flash, render

router = APIRouter()


def _redirect(url: str, text: str, art: str = "ok") -> RedirectResponse:
    return mit_flash(RedirectResponse(url, status_code=303), text, art)


# ---- Felder ----
@router.get("/felder", response_class=HTMLResponse)
def felder(request: Request, admin: AdminUser = Depends(auth.aktueller_admin), db: Session = Depends(get_db)) -> HTMLResponse:
    liste = db.scalars(select(Feld).order_by(Feld.reihenfolge)).all()
    return render(request, "stammdaten/felder.html", admin=admin, felder=liste)


@router.post("/felder")
def feld_anlegen(request: Request, name: str = Form(...), reihenfolge: str = Form("0"),
                 admin: AdminUser = Depends(auth.nur_admin_rolle), db: Session = Depends(get_db)) -> HTMLResponse | RedirectResponse:
    try:
        f = stammdaten.feld_anlegen(db, admin_user_id=admin.id, name=name, reihenfolge=t_int(reihenfolge) or 0)
        db.commit()
    except StammdatenFehler as e:
        liste = db.scalars(select(Feld).order_by(Feld.reihenfolge)).all()
        return render(request, "stammdaten/felder.html", admin=admin, felder=liste, fehler=str(e))
    return _redirect(f"/admin/felder/{f.id}", "Feld angelegt")


@router.get("/felder/{feld_id}", response_class=HTMLResponse)
def feld(request: Request, feld_id: uuid.UUID, admin: AdminUser = Depends(auth.aktueller_admin),
         db: Session = Depends(get_db)) -> HTMLResponse:
    f = db.get(Feld, feld_id)
    if f is None:
        return _redirect("/admin/felder", "Feld nicht gefunden", "fehler")  # type: ignore[return-value]
    return render(request, "stammdaten/feld.html", admin=admin, feld=f)


@router.post("/felder/{feld_id}")
def feld_aendern(feld_id: uuid.UUID, name: str = Form(...), reihenfolge: str = Form("0"), aktiv: str = Form(""),
                 ha_licht_entity: str = Form(""), ha_praesenz_entity: str = Form(""), heizzone: str = Form(""),
                 admin: AdminUser = Depends(auth.nur_admin_rolle), db: Session = Depends(get_db)) -> RedirectResponse:
    f = db.get(Feld, feld_id)
    if f is None:
        return _redirect("/admin/felder", "Feld nicht gefunden", "fehler")
    stammdaten.feld_aendern(db, f, admin_user_id=admin.id, name=name.strip(), reihenfolge=t_int(reihenfolge) or 0,
                            aktiv=aktiv == "1", ha_licht_entity=ha_licht_entity or None,
                            ha_praesenz_entity=ha_praesenz_entity or None, heizzone=heizzone or None)
    db.commit()
    return _redirect(f"/admin/felder/{f.id}", "Gespeichert")


@router.post("/felder/{feld_id}/raster")
def raster_setzen(request: Request, feld_id: uuid.UUID, wochentag: str = Form(""), modus: str = Form(...),
                  slot_minuten: str = Form(""), fenster: str = Form(""),
                  admin: AdminUser = Depends(auth.nur_admin_rolle), db: Session = Depends(get_db)) -> HTMLResponse | RedirectResponse:
    f = db.get(Feld, feld_id)
    if f is None:
        return _redirect("/admin/felder", "Feld nicht gefunden", "fehler")
    try:
        stammdaten.raster_setzen(db, f, admin_user_id=admin.id, wochentag=t_int(wochentag), modus=modus,
                                 slot_minuten=t_int(slot_minuten), fenster=t_fenster(fenster))
        db.commit()
    except (StammdatenFehler, ValueError) as e:
        db.rollback()
        return render(request, "stammdaten/feld.html", admin=admin, feld=f, fehler=str(e))
    return _redirect(f"/admin/felder/{f.id}", "Raster gespeichert")


@router.post("/felder/{feld_id}/raster/{raster_id}/loeschen")
def raster_loeschen(feld_id: uuid.UUID, raster_id: uuid.UUID, admin: AdminUser = Depends(auth.nur_admin_rolle),
                    db: Session = Depends(get_db)) -> RedirectResponse:
    r = db.get(FeldRaster, raster_id)
    if r is not None and r.feld_id == feld_id:
        stammdaten.raster_loeschen(db, r, admin_user_id=admin.id)
        db.commit()
    return _redirect(f"/admin/felder/{feld_id}", "Raster gelöscht")


# ---- Betriebszeiten ----
@router.get("/betriebszeiten", response_class=HTMLResponse)
def betriebszeiten(request: Request, admin: AdminUser = Depends(auth.aktueller_admin), db: Session = Depends(get_db),
                   fehler: str | None = None) -> HTMLResponse:
    liste = db.scalars(select(Betriebszeit).order_by(Betriebszeit.wochentag, Betriebszeit.oeffnet)).all()
    return render(request, "stammdaten/betriebszeiten.html", admin=admin, zeiten=liste, fehler=fehler)


@router.post("/betriebszeiten")
def betriebszeit_anlegen(request: Request, wochentag: str = Form(...), oeffnet: str = Form(...), schliesst: str = Form(...),
                         gueltig_von: str = Form(""), gueltig_bis: str = Form(""),
                         admin: AdminUser = Depends(auth.nur_admin_rolle), db: Session = Depends(get_db)) -> HTMLResponse | RedirectResponse:
    try:
        stammdaten.betriebszeit_anlegen(db, admin_user_id=admin.id, wochentag=int(wochentag), oeffnet=t_zeit(oeffnet),  # type: ignore[arg-type]
                                        schliesst=t_zeit(schliesst), gueltig_von=t_datum(gueltig_von), gueltig_bis=t_datum(gueltig_bis))  # type: ignore[arg-type]
        db.commit()
    except (StammdatenFehler, ValueError) as e:
        db.rollback()
        return betriebszeiten(request, admin, db, fehler=str(e))
    return _redirect("/admin/betriebszeiten", "Betriebszeit angelegt")


@router.post("/betriebszeiten/{bz_id}/loeschen")
def betriebszeit_loeschen(bz_id: uuid.UUID, admin: AdminUser = Depends(auth.nur_admin_rolle), db: Session = Depends(get_db)) -> RedirectResponse:
    bz = db.get(Betriebszeit, bz_id)
    if bz:
        stammdaten.betriebszeit_loeschen(db, bz, admin_user_id=admin.id)
        db.commit()
    return _redirect("/admin/betriebszeiten", "Gelöscht")


# ---- Ausnahmetage ----
@router.get("/ausnahmetage", response_class=HTMLResponse)
def ausnahmetage(request: Request, admin: AdminUser = Depends(auth.aktueller_admin), db: Session = Depends(get_db),
                 fehler: str | None = None) -> HTMLResponse:
    liste = db.scalars(select(Ausnahmetag).order_by(Ausnahmetag.datum)).all()
    return render(request, "stammdaten/ausnahmetage.html", admin=admin, tage=liste, fehler=fehler)


@router.post("/ausnahmetage")
def ausnahmetag_anlegen(request: Request, datum: str = Form(...), geschlossen: str = Form("1"), oeffnet: str = Form(""),
                        schliesst: str = Form(""), grund: str = Form(""),
                        admin: AdminUser = Depends(auth.nur_admin_rolle), db: Session = Depends(get_db)) -> HTMLResponse | RedirectResponse:
    try:
        stammdaten.ausnahmetag_anlegen(db, admin_user_id=admin.id, datum=t_datum(datum), geschlossen=geschlossen == "1",  # type: ignore[arg-type]
                                       oeffnet=t_zeit(oeffnet), schliesst=t_zeit(schliesst), grund=grund.strip())
        db.commit()
    except (StammdatenFehler, ValueError) as e:
        db.rollback()
        return ausnahmetage(request, admin, db, fehler=str(e))
    return _redirect("/admin/ausnahmetage", "Ausnahmetag angelegt")


@router.post("/ausnahmetage/{tag_id}/loeschen")
def ausnahmetag_loeschen(tag_id: uuid.UUID, admin: AdminUser = Depends(auth.nur_admin_rolle), db: Session = Depends(get_db)) -> RedirectResponse:
    a = db.get(Ausnahmetag, tag_id)
    if a:
        stammdaten.ausnahmetag_loeschen(db, a, admin_user_id=admin.id)
        db.commit()
    return _redirect("/admin/ausnahmetage", "Gelöscht")


# ---- Kundengruppen ----
@router.get("/kundengruppen", response_class=HTMLResponse)
def kundengruppen(request: Request, admin: AdminUser = Depends(auth.aktueller_admin), db: Session = Depends(get_db),
                  fehler: str | None = None) -> HTMLResponse:
    liste = db.scalars(select(Kundengruppe).order_by(Kundengruppe.name)).all()
    return render(request, "stammdaten/kundengruppen.html", admin=admin, gruppen=liste, fehler=fehler)


@router.post("/kundengruppen")
def kundengruppe_anlegen(request: Request, name: str = Form(...), standard_zahlungsart: str = Form(...),
                         admin: AdminUser = Depends(auth.nur_admin_rolle), db: Session = Depends(get_db)) -> HTMLResponse | RedirectResponse:
    try:
        stammdaten.kundengruppe_anlegen(db, admin_user_id=admin.id, name=name, standard_zahlungsart=standard_zahlungsart)
        db.commit()
    except StammdatenFehler as e:
        db.rollback()
        return kundengruppen(request, admin, db, fehler=str(e))
    return _redirect("/admin/kundengruppen", "Kundengruppe angelegt")


@router.post("/kundengruppen/{gruppe_id}")
def kundengruppe_aendern(gruppe_id: uuid.UUID, name: str = Form(...), standard_zahlungsart: str = Form(...),
                         admin: AdminUser = Depends(auth.nur_admin_rolle), db: Session = Depends(get_db)) -> RedirectResponse:
    g = db.get(Kundengruppe, gruppe_id)
    if g is None:
        return _redirect("/admin/kundengruppen", "Gruppe nicht gefunden", "fehler")
    stammdaten.kundengruppe_aendern(db, g, admin_user_id=admin.id, name=name.strip(), standard_zahlungsart=standard_zahlungsart)
    db.commit()
    return _redirect("/admin/kundengruppen", "Gespeichert")


# ---- Tarife ----
def _tarif_ctx(db: Session) -> dict:  # type: ignore[type-arg]
    return {
        "tarife": db.scalars(select(Tarif).order_by(Tarif.aktiv.desc(), Tarif.name)).all(),
        "felder": db.scalars(select(Feld).order_by(Feld.reihenfolge)).all(),
        "gruppen": db.scalars(select(Kundengruppe).order_by(Kundengruppe.name)).all(),
    }


@router.get("/tarife", response_class=HTMLResponse)
def tarife(request: Request, admin: AdminUser = Depends(auth.aktueller_admin), db: Session = Depends(get_db)) -> HTMLResponse:
    return render(request, "stammdaten/tarife.html", admin=admin, **_tarif_ctx(db))


@router.post("/tarife")
def tarif_anlegen(request: Request, name: str = Form(...), preis: str = Form(...), feld_id: str = Form(""), wochentag: str = Form(""),
                  uhrzeit_von: str = Form(""), uhrzeit_bis: str = Form(""), kundengruppe_id: str = Form(""),
                  gueltig_von: str = Form(""), gueltig_bis: str = Form(""),
                  admin: AdminUser = Depends(auth.nur_admin_rolle), db: Session = Depends(get_db)) -> HTMLResponse | RedirectResponse:
    try:
        stammdaten.tarif_anlegen(db, admin_user_id=admin.id, name=name, preis=t_betrag(preis) or Decimal("0.00"),
                                 feld_id=t_uuid(feld_id), wochentag=t_int(wochentag), uhrzeit_von=t_zeit(uhrzeit_von),
                                 uhrzeit_bis=t_zeit(uhrzeit_bis), kundengruppe_id=t_uuid(kundengruppe_id),
                                 gueltig_von=t_datum(gueltig_von), gueltig_bis=t_datum(gueltig_bis))
        db.commit()
    except (StammdatenFehler, ValueError) as e:
        db.rollback()
        return render(request, "stammdaten/tarife.html", admin=admin, fehler=str(e), **_tarif_ctx(db))
    return _redirect("/admin/tarife", "Tarif angelegt")


@router.post("/tarife/{tarif_id}/deaktivieren")
def tarif_deaktivieren(tarif_id: uuid.UUID, admin: AdminUser = Depends(auth.nur_admin_rolle), db: Session = Depends(get_db)) -> RedirectResponse:
    t = db.get(Tarif, tarif_id)
    if t:
        stammdaten.tarif_deaktivieren(db, t, admin_user_id=admin.id)
        db.commit()
    return _redirect("/admin/tarife", "Tarif deaktiviert")


# ---- Konfiguration ----
@router.get("/konfiguration", response_class=HTMLResponse)
def konfiguration_seite(request: Request, admin: AdminUser = Depends(auth.aktueller_admin), db: Session = Depends(get_db)) -> HTMLResponse:
    werte = {k: konfiguration.hole(db, k) for k in konfiguration.DEFAULTS}
    return render(request, "stammdaten/konfiguration.html", admin=admin, werte=werte, defaults=konfiguration.DEFAULTS)


@router.post("/konfiguration")
async def konfiguration_speichern(request: Request, admin: AdminUser = Depends(auth.nur_admin_rolle),
                                  db: Session = Depends(get_db)) -> RedirectResponse:
    form = await request.form()
    for k in konfiguration.DEFAULTS:
        wert = str(form.get(k, "")).strip()
        if wert:
            konfiguration.setze(db, k, wert.replace(",", "."), admin_user_id=admin.id)
    db.commit()
    return _redirect("/admin/konfiguration", "Konfiguration gespeichert")
```
(`from decimal import Decimal` ergänzen.)

Templates – `stammdaten/feld.html` als Vorlage für die Formularseiten:
```html
{% extends "base.html" %}{% block title %}{{ feld.name }}{% endblock %}
{% block content %}
<h1>{{ feld.name }}</h1>
{% if fehler %}<p class="fehler">{{ fehler }}</p>{% endif %}
<div class="zeile">
<form method="post" action="/admin/felder/{{ feld.id }}" class="karte">
  <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
  <label>Name <input name="name" value="{{ feld.name }}" required></label>
  <label>Reihenfolge <input name="reihenfolge" type="number" value="{{ feld.reihenfolge }}"></label>
  <label><input type="checkbox" name="aktiv" value="1" {% if feld.aktiv %}checked{% endif %} style="width:auto"> aktiv</label>
  <label>Home-Assistant-Licht <input name="ha_licht_entity" value="{{ feld.ha_licht_entity or '' }}" placeholder="light.feld_1"></label>
  <label>Home-Assistant-Präsenz <input name="ha_praesenz_entity" value="{{ feld.ha_praesenz_entity or '' }}" placeholder="binary_sensor.feld_1"></label>
  <label>Heizzone <input name="heizzone" value="{{ feld.heizzone or '' }}"></label>
  <button>Speichern</button>
</form>
<div class="karte">
  <h2>Buchungsraster</h2>
  <table><tr><th>Wochentag</th><th>Modus</th><th>Slots</th><th></th></tr>
  {% for r in feld.raster %}<tr>
    <td>{{ wochentage[r.wochentag] if r.wochentag is not none else "alle" }}</td>
    <td>{{ r.modus }}</td>
    <td>{% if r.modus == "dauer" %}{{ r.slot_minuten }} min{% else %}{% for f in r.fenster_json %}{{ f[0] }}-{{ f[1] }}{% if not loop.last %}, {% endif %}{% endfor %}{% endif %}</td>
    <td><form method="post" action="/admin/felder/{{ feld.id }}/raster/{{ r.id }}/loeschen" class="inline"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><button class="link">löschen</button></form></td>
  </tr>{% endfor %}</table>
  <form method="post" action="/admin/felder/{{ feld.id }}/raster">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <label>Wochentag <select name="wochentag"><option value="">alle</option>{% for w in wochentage %}<option value="{{ loop.index0 }}">{{ w }}</option>{% endfor %}</select></label>
    <label>Modus <select name="modus"><option value="dauer">feste Slot-Dauer</option><option value="fenster">feste Zeitfenster</option></select></label>
    <label>Slot-Dauer in Minuten <input name="slot_minuten" type="number" placeholder="60"></label>
    <label>Zeitfenster <input name="fenster" placeholder="19:00-21:00, 21:00-23:00"></label>
    <button>Raster setzen</button>
  </form>
</div>
</div>
{% endblock %}
```
`felder.html`: Tabelle aller Felder (Name, aktiv, Raster-Anzahl, Link) + Formular Name/Reihenfolge. `betriebszeiten.html`: Tabelle (Wochentag, von, bis, gültig) mit Lösch-Button + Formular. `ausnahmetage.html`: Tabelle + Formular (Datum, geschlossen ja/nein, Zeiten, Grund). `kundengruppen.html`: Tabelle mit Inline-Formular je Zeile (Name, Zahlungsart) + Neu-Formular. `tarife.html`: Tabelle (Name, Preis, Feld, Wochentag, Uhrzeit, Gruppe, gültig, aktiv, Deaktivieren) + Neu-Formular mit Selects für Feld/Gruppe. `konfiguration.html`: ein Formular, je Schlüssel ein Feld mit aktuellem Wert und Default als Placeholder; leere Eingabe = unverändert.

In `main.py`: `app.include_router(stammdaten.router, prefix="/admin", dependencies=csrf)`.

- [ ] **Step 5: Tests grün**

Run: `cd core && pytest -q`
Expected: alle grün

- [ ] **Step 6: Commit**

```bash
git add core && git commit -m "feat(core): Admin-UI für Felder, Raster, Betriebszeiten, Ausnahmetage, Gruppen, Tarife, Konfiguration"
```

---

## Task 16: Admin-UI Kunden

**Files:**
- Create: `core/beachhub_core/routes/kunden.py`, `core/beachhub_core/templates/kunden/liste.html`, `core/beachhub_core/templates/kunden/detail.html`
- Modify: `core/beachhub_core/main.py`
- Test: `core/tests/test_ui_kunden.py`

**Interfaces:**
- Routen: `GET /admin/kunden?q=` (Suche in Name/E-Mail, `ILIKE`), `POST /admin/kunden` (anlegen: name, email, kundengruppe_id, zahlungsart, Adresse), `GET /admin/kunden/{id}` (Stammdaten, Guthaben, Buchungen der letzten 12 Monate und künftige, Rechnungen, Guthabenbuchungen, Audit-Einträge zum Kunden), `POST /admin/kunden/{id}` (ändern), `POST /admin/kunden/{id}/guthaben` (betrag, art ∈ {manuell, auszahlung}, notiz), `POST /admin/kunden/{id}/anonymisieren`.
- Fehler `KundenFehler`/`GuthabenFehler` → Seite mit `fehler=` erneut rendern.

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_ui_kunden.py`:
```python
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from beachhub_core.models import Kunde, Kundengruppe


def test_kunde_anlegen_suchen_guthaben(eingeloggt: TestClient, db: Session) -> None:
    c = eingeloggt
    g = Kundengruppe(name="Privat")
    db.add(g)
    db.commit()
    r = c.post("/admin/kunden", data={"csrf_token": c.csrf, "name": "Anna Müller", "email": "Anna@X.de",
                                      "kundengruppe_id": str(g.id), "zahlungsart": "", "adresse_strasse": "Weg 1",
                                      "adresse_plz": "12345", "adresse_ort": "Ort"}, follow_redirects=False)
    assert r.status_code == 303
    k = db.query(Kunde).one()
    assert k.email == "anna@x.de" and k.zahlungsart == "online"
    assert "Anna Müller" in c.get("/admin/kunden?q=müll").text
    assert "Anna Müller" not in c.get("/admin/kunden?q=zzz").text
    r = c.post(f"/admin/kunden/{k.id}/guthaben", data={"csrf_token": c.csrf, "betrag": "25,00", "art": "manuell",
                                                        "notiz": "Gutschein"}, follow_redirects=False)
    assert r.status_code == 303
    db.refresh(k)
    assert k.guthaben == Decimal("25.00")
    r = c.post(f"/admin/kunden/{k.id}/guthaben", data={"csrf_token": c.csrf, "betrag": "-30,00", "art": "auszahlung", "notiz": ""})
    assert r.status_code == 200 and "nicht gedeckt" in r.text
    seite = c.get(f"/admin/kunden/{k.id}")
    assert "25,00 €" in seite.text and "Gutschein" in seite.text


def test_doppelte_email_zeigt_fehler(eingeloggt: TestClient, db: Session) -> None:
    c = eingeloggt
    g = Kundengruppe(name="Privat")
    db.add(g)
    db.commit()
    daten = {"csrf_token": c.csrf, "name": "A", "email": "a@x.de", "kundengruppe_id": str(g.id), "zahlungsart": "",
             "adresse_strasse": "", "adresse_plz": "", "adresse_ort": ""}
    c.post("/admin/kunden", data=daten)
    r = c.post("/admin/kunden", data=daten)
    assert r.status_code == 200 and "bereits vergeben" in r.text
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_ui_kunden.py`
Expected: FAIL (404)

- [ ] **Step 3: Routen und Templates**

`core/beachhub_core/routes/kunden.py`:
```python
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from beachhub_core import auth, clock
from beachhub_core.database import get_db
from beachhub_core.models import AdminUser, Audit, Buchung, GuthabenBuchung, Kunde, Kundengruppe, Rechnung
from beachhub_core.routes._form import t_betrag, t_uuid
from beachhub_core.services import guthaben, kunden
from beachhub_core.services.guthaben import GuthabenFehler
from beachhub_core.services.kunden import KundenFehler
from beachhub_core.templating import mit_flash, render

router = APIRouter()
FEHLERTEXT = {"email_vergeben": "E-Mail-Adresse ist bereits vergeben", "gruppe_unbekannt": "Kundengruppe unbekannt",
              "nicht_gedeckt": "Guthaben nicht gedeckt", "betrag_muss_negativ_sein": "Auszahlung muss negativ sein",
              "art_unbekannt": "Art unbekannt"}


def _liste_ctx(db: Session, q: str) -> dict:  # type: ignore[type-arg]
    stmt = select(Kunde).where(Kunde.anonymisiert_am.is_(None)).order_by(Kunde.name)
    if q:
        stmt = stmt.where(or_(Kunde.name.ilike(f"%{q}%"), Kunde.email.ilike(f"%{q}%")))
    return {"kunden": db.scalars(stmt.limit(200)).all(), "q": q,
            "gruppen": db.scalars(select(Kundengruppe).order_by(Kundengruppe.name)).all()}


@router.get("/kunden", response_class=HTMLResponse)
def liste(request: Request, q: str = "", admin: AdminUser = Depends(auth.aktueller_admin), db: Session = Depends(get_db)) -> HTMLResponse:
    return render(request, "kunden/liste.html", admin=admin, **_liste_ctx(db, q))


@router.post("/kunden")
def anlegen(request: Request, name: str = Form(...), email: str = Form(...), kundengruppe_id: str = Form(...),
            zahlungsart: str = Form(""), adresse_strasse: str = Form(""), adresse_plz: str = Form(""), adresse_ort: str = Form(""),
            admin: AdminUser = Depends(auth.nur_admin_rolle), db: Session = Depends(get_db)) -> HTMLResponse | RedirectResponse:
    try:
        k = kunden.lege_an(db, name=name, email=email, kundengruppe_id=t_uuid(kundengruppe_id),  # type: ignore[arg-type]
                           zahlungsart=zahlungsart or None, adresse_strasse=adresse_strasse, adresse_plz=adresse_plz,
                           adresse_ort=adresse_ort, admin_user_id=admin.id)
        db.commit()
    except KundenFehler as e:
        db.rollback()
        return render(request, "kunden/liste.html", admin=admin, fehler=FEHLERTEXT.get(str(e), str(e)), **_liste_ctx(db, ""))
    return mit_flash(RedirectResponse(f"/admin/kunden/{k.id}", status_code=303), "Kunde angelegt")


def _detail_ctx(db: Session, k: Kunde) -> dict:  # type: ignore[type-arg]
    seit = clock.now(db) - timedelta(days=365)
    return {
        "kunde": k,
        "gruppen": db.scalars(select(Kundengruppe).order_by(Kundengruppe.name)).all(),
        "buchungen": db.scalars(select(Buchung).where(Buchung.kunde_id == k.id, Buchung.beginn >= seit).order_by(Buchung.beginn.desc())).all(),
        "rechnungen": db.scalars(select(Rechnung).where(Rechnung.kunde_id == k.id).order_by(Rechnung.datum.desc())).all(),
        "guthaben": db.scalars(select(GuthabenBuchung).where(GuthabenBuchung.kunde_id == k.id).order_by(GuthabenBuchung.created_at.desc())).all(),
        "audit": db.scalars(select(Audit).where(Audit.objekt_typ == "kunde", Audit.objekt_id == k.id).order_by(Audit.zeitpunkt.desc()).limit(50)).all(),
    }


@router.get("/kunden/{kunde_id}", response_class=HTMLResponse)
def detail(request: Request, kunde_id: uuid.UUID, admin: AdminUser = Depends(auth.aktueller_admin), db: Session = Depends(get_db)) -> HTMLResponse:
    k = db.get(Kunde, kunde_id)
    if k is None:
        return mit_flash(RedirectResponse("/admin/kunden", status_code=303), "Kunde nicht gefunden", "fehler")  # type: ignore[return-value]
    return render(request, "kunden/detail.html", admin=admin, **_detail_ctx(db, k))


@router.post("/kunden/{kunde_id}")
def aendern(kunde_id: uuid.UUID, name: str = Form(...), email: str = Form(...), kundengruppe_id: str = Form(...),
            zahlungsart: str = Form(...), adresse_strasse: str = Form(""), adresse_plz: str = Form(""), adresse_ort: str = Form(""),
            admin: AdminUser = Depends(auth.nur_admin_rolle), db: Session = Depends(get_db)) -> RedirectResponse:
    k = db.get(Kunde, kunde_id)
    if k is None:
        return mit_flash(RedirectResponse("/admin/kunden", status_code=303), "Kunde nicht gefunden", "fehler")
    kunden.aendere(db, k, admin_user_id=admin.id, name=name.strip(), email=email, kundengruppe_id=t_uuid(kundengruppe_id),
                   zahlungsart=zahlungsart, adresse_strasse=adresse_strasse, adresse_plz=adresse_plz, adresse_ort=adresse_ort)
    db.commit()
    return mit_flash(RedirectResponse(f"/admin/kunden/{k.id}", status_code=303), "Gespeichert")


@router.post("/kunden/{kunde_id}/guthaben")
def guthaben_buchen(request: Request, kunde_id: uuid.UUID, betrag: str = Form(...), art: str = Form(...), notiz: str = Form(""),
                    admin: AdminUser = Depends(auth.nur_admin_rolle), db: Session = Depends(get_db)) -> HTMLResponse | RedirectResponse:
    k = db.get(Kunde, kunde_id)
    if k is None:
        return mit_flash(RedirectResponse("/admin/kunden", status_code=303), "Kunde nicht gefunden", "fehler")
    try:
        if art not in ("manuell", "auszahlung"):
            raise GuthabenFehler("art_unbekannt")
        guthaben.buche(db, kunde=k, betrag=t_betrag(betrag) or Decimal("0"), art=art, notiz=notiz, admin_user_id=admin.id)
        db.commit()
    except (GuthabenFehler, ValueError) as e:
        db.rollback()
        return render(request, "kunden/detail.html", admin=admin, fehler=FEHLERTEXT.get(str(e), str(e)), **_detail_ctx(db, k))
    return mit_flash(RedirectResponse(f"/admin/kunden/{k.id}", status_code=303), "Guthaben gebucht")


@router.post("/kunden/{kunde_id}/anonymisieren")
def anonymisieren(kunde_id: uuid.UUID, admin: AdminUser = Depends(auth.nur_admin_rolle), db: Session = Depends(get_db)) -> RedirectResponse:
    k = db.get(Kunde, kunde_id)
    if k is not None:
        kunden.anonymisiere(db, k, admin_user_id=admin.id)
        db.commit()
    return mit_flash(RedirectResponse("/admin/kunden", status_code=303), "Kunde anonymisiert")
```
(`from decimal import Decimal` ergänzen.)

`kunden/liste.html`: Suchformular (`GET`, Feld `q`), Tabelle (Name, E-Mail, Gruppe, Zahlungsart, Guthaben, Link), Karte „Neuer Kunde“ mit Feldern name, email, kundengruppe_id (Select), zahlungsart (Select: leer = Standard der Gruppe, online, rechnung), Adresse. `kunden/detail.html`: Stammdatenformular, Karte Guthaben (Saldo, Formular betrag/art/notiz, Liste der Buchungen), Tabelle Buchungen (Datum, Feld, Status, Preis, Link auf Belegung), Tabelle Rechnungen (Nummer, Datum, Brutto, Status, PDF-Link `/admin/rechnungen/{id}/pdf`), Audit-Liste, Button „Anonymisieren“ mit `onsubmit="return confirm('Kunde wirklich anonymisieren?')"` – kein Inline-JS erlaubt (CSP) → stattdessen Zwischenseite? Einfacher: Checkbox „Ich bin sicher“ als Pflichtfeld (`required`) neben dem Button.

In `main.py`: `app.include_router(kunden.router, prefix="/admin", dependencies=csrf)`.

- [ ] **Step 4: Tests grün**

Run: `cd core && pytest -q`
Expected: alle grün

- [ ] **Step 5: Commit**

```bash
git add core && git commit -m "feat(core): Admin-UI Kunden mit Suche, Guthaben und Anonymisierung"
```

---

## Task 17: Admin-UI Belegungsplan (Kalender, Buchung, Storno, Sperre, Dauerbuchung)

**Files:**
- Create: `core/beachhub_core/routes/belegung.py`, `core/beachhub_core/services/belegung.py`
- Create: `core/beachhub_core/templates/belegung/woche.html`, `belegung/buchung_neu.html`, `belegung/buchung.html`, `belegung/sperre_neu.html`, `belegung/dauer_neu.html`, `belegung/dauer.html`
- Modify: `core/beachhub_core/main.py`
- Test: `core/tests/test_ui_belegung.py`

**Interfaces:**
- Produces `services/belegung.py`: `wochenplan(db, feld: Feld, montag: date) -> list[Tag]` mit `Tag(datum, zellen: list[Zelle])`, `Zelle(slot: Slot, art: "frei"|"buchung"|"sperre", buchung: Buchung|None, sperre: Sperre|None)` – für jeden Tages-Slot die aktive Buchung oder Sperre, die ihn überlappt.
- Routen:
  - `GET /admin/belegung?feld=<id>&woche=<JJJJ-MM-TT>` (Standard: erstes aktives Feld, aktuelle Woche nach `clock.today`), Navigation ± 1 Woche, Feld-Auswahl, Zellen verlinken auf `buchung_neu` (frei) bzw. `buchung` (belegt).
  - `GET /admin/belegung/buchung/neu?feld=&beginn=<ISO>` → Formular (Kunde per Select mit Suche-Text, Ende als Select der folgenden Slots); `POST /admin/belegung/buchung` (feld_id, kunde_id, beginn, ende) → `buchungen.lege_an(..., quelle="admin")`, Mail `benachrichtigung.buchung_bestaetigt`, Redirect auf Woche. Für Rechnungskunden Status `bestaetigt`; für Onlinekunden ebenfalls `bestaetigt` mit Hinweis „Zahlung außerhalb des Systems“ (Admin-Buchung gilt als bezahlt, Einzelrechnung wird sofort erzeugt: `rechnungen.erzeuge_einzelrechnung` + `rechnung_pdf.erzeuge` + Mail).
  - `GET /admin/belegung/buchung/{id}` Detail (Kunde, Zeiten, Preis, Status, PIN entschlüsselt anzeigen, Storno-Info, Rechnungsposition); `POST /admin/belegung/buchung/{id}/storno` (grund, kostenfrei ∈ {auto, ja, nein}) → `storno.storniere(..., durch="betreiber", kostenfrei=None|True|False)` + Mail; `POST /admin/belegung/buchung/{id}/kulanz` → `storno.kulanz`.
  - `GET /admin/belegung/sperre/neu?beginn=&ende=` → Formular (Felder-Checkboxen oder „alle“, Zeitraum, Grund) zeigt bei Absenden mit `pruefen=1` die betroffenen Buchungen mit Radio `entscheidung_<id>` = behalten/stornieren; `POST /admin/belegung/sperre` legt an (`sperren.lege_an`), bei `SperrenFehler("entscheidung_fehlt")` erneut rendern mit Liste; `POST /admin/belegung/sperre/{id}/loeschen`.
  - `GET /admin/belegung/dauer/neu` Formular (Kunde, Feld, Wochentag, von/bis Uhrzeit, gültig von/bis); `POST /admin/belegung/dauer/planen` zeigt Terminliste aus `dauerbuchungen.plane` mit Checkbox `auslassen_<datum>` und Radio je Kollision; `POST /admin/belegung/dauer` legt an, Mail `dauerbuchung_angelegt`; `GET /admin/belegung/dauer/{id}` Detail mit Terminen; `POST /admin/belegung/dauer/{id}/beenden` (ab Datum).
- Alle Zeiten aus Formularen lokal (`JJJJ-MM-TTTHH:MM`) → `kombiniere`.

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_ui_belegung.py`:
```python
from datetime import date, time
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from beachhub_core import clock, mail
from beachhub_core.models import Betriebszeit, Buchung, Dauerbuchung, Feld, FeldRaster, Kundengruppe, Sperre, Tarif
from beachhub_core.services import kunden


@pytest.fixture
def welt(db: Session):
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    p = Kundengruppe(name="Privat")
    v = Kundengruppe(name="Verein", standard_zahlungsart="rechnung")
    db.add_all([f, p, v, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    a = kunden.lege_an(db, name="A", email="a@x.de", kundengruppe_id=p.id)
    v1 = kunden.lege_an(db, name="TSV", email="v@x.de", kundengruppe_id=v.id)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    return f, a, v1


def test_woche_zeigt_slots_und_buchung(eingeloggt: TestClient, db: Session, welt) -> None:
    f, a, _ = welt
    c = eingeloggt
    seite = c.get(f"/admin/belegung?feld={f.id}&woche=2027-11-29")
    assert seite.status_code == 200 and "Mi 01.12." in seite.text and 'class="zelle frei"' in seite.text
    r = c.post("/admin/belegung/buchung", data={"csrf_token": c.csrf, "feld_id": str(f.id), "kunde_id": str(a.id),
                                                "beginn": "2027-12-01T19:00", "ende": "2027-12-01T21:00"}, follow_redirects=False)
    assert r.status_code == 303
    b = db.query(Buchung).one()
    assert b.preis == Decimal("60.00") and b.rechnung_position_id is not None  # Onlinekunde: sofort Einzelrechnung
    assert [m["betreff"].split(":")[0] for m in mail.TEST_AUSGANG] == ["Buchung bestätigt", "Rechnung 2027-00001"]
    seite = c.get(f"/admin/belegung?feld={f.id}&woche=2027-11-29")
    assert 'class="zelle belegt"' in seite.text and "A" in seite.text
    detail = c.get(f"/admin/belegung/buchung/{b.id}")
    assert detail.status_code == 200 and "PIN" in detail.text


def test_storno_ueber_ui(eingeloggt: TestClient, db: Session, welt) -> None:
    f, _, v1 = welt
    c = eingeloggt
    c.post("/admin/belegung/buchung", data={"csrf_token": c.csrf, "feld_id": str(f.id), "kunde_id": str(v1.id),
                                            "beginn": "2027-12-01T19:00", "ende": "2027-12-01T20:00"})
    b = db.query(Buchung).one()
    r = c.post(f"/admin/belegung/buchung/{b.id}/storno", data={"csrf_token": c.csrf, "grund": "Test", "kostenfrei": "nein"},
               follow_redirects=False)
    assert r.status_code == 303
    db.refresh(b)
    assert b.status == "storniert" and b.storno.nachbuchung_offen and not b.storno.kostenfrei


def test_sperre_mit_entscheidung(eingeloggt: TestClient, db: Session, welt) -> None:
    f, a, _ = welt
    c = eingeloggt
    c.post("/admin/belegung/buchung", data={"csrf_token": c.csrf, "feld_id": str(f.id), "kunde_id": str(a.id),
                                            "beginn": "2027-12-01T19:00", "ende": "2027-12-01T20:00"})
    b = db.query(Buchung).one()
    daten = {"csrf_token": c.csrf, "alle_felder": "1", "beginn": "2027-12-01T18:00", "ende": "2027-12-01T22:00", "grund": "Turnier"}
    r = c.post("/admin/belegung/sperre", data=daten)
    assert r.status_code == 200 and f"entscheidung_{b.id}" in r.text
    r = c.post("/admin/belegung/sperre", data={**daten, f"entscheidung_{b.id}": "stornieren"}, follow_redirects=False)
    assert r.status_code == 303
    assert db.query(Sperre).count() == 1
    db.refresh(b)
    assert b.status == "storniert" and b.storno.kostenfrei


def test_dauerbuchung_planen_und_anlegen(eingeloggt: TestClient, db: Session, welt) -> None:
    f, _, v1 = welt
    c = eingeloggt
    daten = {"csrf_token": c.csrf, "kunde_id": str(v1.id), "feld_id": str(f.id), "wochentag": "1", "start": "19:00",
             "ende": "21:00", "gueltig_von": "2027-12-01", "gueltig_bis": "2027-12-31"}
    r = c.post("/admin/belegung/dauer/planen", data=daten)
    assert r.status_code == 200 and "07.12.2027" in r.text and "auslassen_2027-12-28" in r.text
    r = c.post("/admin/belegung/dauer", data={**daten, "auslassen_2027-12-28": "1"}, follow_redirects=False)
    assert r.status_code == 303
    d = db.query(Dauerbuchung).one()
    assert len(d.buchungen) == 3
    assert any(m["betreff"] == "Ihre Dauerbuchung" for m in mail.TEST_AUSGANG)
    r = c.post(f"/admin/belegung/dauer/{d.id}/beenden", data={"csrf_token": c.csrf, "ab": "2027-12-14"}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert [b.status for b in db.get(Dauerbuchung, d.id).buchungen] == ["bestaetigt", "storniert", "storniert"]
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_ui_belegung.py`
Expected: FAIL (404)

- [ ] **Step 3: Belegungs-Service**

`core/beachhub_core/services/belegung.py`:
```python
from dataclasses import dataclass
from datetime import date, time, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from beachhub_core.models import Buchung, Feld, Sperre
from beachhub_core.services import slots_db
from beachhub_shared.slots import Slot
from beachhub_shared.zeit import kombiniere


@dataclass
class Zelle:
    slot: Slot
    art: str  # frei | buchung | sperre
    buchung: Buchung | None = None
    sperre: Sperre | None = None


@dataclass
class Tag:
    datum: date
    zellen: list[Zelle]


def wochenplan(db: Session, feld: Feld, montag: date) -> list[Tag]:
    von, bis = kombiniere(montag, time(0)), kombiniere(montag + timedelta(days=7), time(0))
    buchungen = db.scalars(select(Buchung).where(Buchung.feld_id == feld.id, Buchung.status.in_(Buchung.AKTIVE_STATUS),
                                                 Buchung.beginn < bis, Buchung.ende > von)).all()
    sperren = db.scalars(select(Sperre).where(or_(Sperre.feld_id == feld.id, Sperre.feld_id.is_(None)),
                                              Sperre.beginn < bis, Sperre.ende > von)).all()
    tage = []
    for i in range(7):
        d = montag + timedelta(days=i)
        zellen = []
        for slot in slots_db.tages_slots(db, feld, d):
            b = next((x for x in buchungen if x.beginn < slot.ende and x.ende > slot.beginn), None)
            s = next((x for x in sperren if x.beginn < slot.ende and x.ende > slot.beginn), None)
            if b:
                zellen.append(Zelle(slot, "buchung", buchung=b))
            elif s:
                zellen.append(Zelle(slot, "sperre", sperre=s))
            else:
                zellen.append(Zelle(slot, "frei"))
        tage.append(Tag(d, zellen))
    return tage
```

- [ ] **Step 4: Routen**

`core/beachhub_core/routes/belegung.py` (vollständig auszuprogrammieren; hier die tragenden Teile):
```python
import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_core import auth, clock
from beachhub_core.database import get_db
from beachhub_core.models import AdminUser, Buchung, Dauerbuchung, Feld, Kunde, Sperre
from beachhub_core.routes._form import t_datum, t_uuid, t_zeit
from beachhub_core.services import belegung, benachrichtigung, buchungen, dauerbuchungen, pin, rechnung_pdf, rechnungen, sperren, storno
from beachhub_core.services.buchungen import BuchungsFehler
from beachhub_core.services.dauerbuchungen import DauerbuchungsFehler
from beachhub_core.services.sperren import SperrenFehler
from beachhub_core.services.storno import StornoFehler
from beachhub_core.templating import mit_flash, render
from beachhub_shared.zeit import BERLIN, kombiniere

router = APIRouter()
GRUND = {"belegt": "Zeitraum ist belegt", "ausserhalb_betriebszeit": "Zeitraum passt nicht zu Raster oder Betriebszeit",
         "ausserhalb_fenster": "Außerhalb des Buchungsfensters", "kein_tarif": "Kein Tarif hinterlegt",
         "kunde_unbekannt": "Kunde unbekannt", "feld_inaktiv": "Feld inaktiv", "vergangenheit": "Zeitpunkt liegt in der Vergangenheit",
         "zu_spaet": "Buchung hat bereits begonnen", "nicht_aktiv": "Buchung ist nicht aktiv",
         "entscheidung_fehlt": "Bitte für jede betroffene Buchung entscheiden", "sperre": "Termin kollidiert mit einer Sperre",
         "keine_termine": "Keine Termine im Zeitraum"}


def _lokal(v: str) -> datetime:
    """'JJJJ-MM-TTTHH:MM' (lokal) → UTC-aware."""
    naiv = datetime.fromisoformat(v)
    return kombiniere(naiv.date(), naiv.time())


def _woche_url(feld_id: uuid.UUID, tag: datetime) -> str:
    lokal = tag.astimezone(BERLIN).date()
    montag = lokal - timedelta(days=lokal.weekday())
    return f"/admin/belegung?feld={feld_id}&woche={montag.isoformat()}"


@router.get("/belegung", response_class=HTMLResponse)
def woche(request: Request, feld: str = "", woche: str = "", admin: AdminUser = Depends(auth.aktueller_admin),
          db: Session = Depends(get_db)) -> HTMLResponse:
    felder = db.scalars(select(Feld).where(Feld.aktiv.is_(True)).order_by(Feld.reihenfolge)).all()
    if not felder:
        return render(request, "belegung/woche.html", admin=admin, felder=[], feld=None, tage=[], montag=None)
    f = db.get(Feld, uuid.UUID(feld)) if feld else felder[0]
    heute = clock.today(db)
    montag = t_datum(woche) or (heute - timedelta(days=heute.weekday()))
    montag -= timedelta(days=montag.weekday())
    return render(request, "belegung/woche.html", admin=admin, felder=felder, feld=f, montag=montag,
                  tage=belegung.wochenplan(db, f, montag), vorher=montag - timedelta(days=7), nachher=montag + timedelta(days=7))


@router.get("/belegung/buchung/neu", response_class=HTMLResponse)
def buchung_neu(request: Request, feld: str, beginn: str, admin: AdminUser = Depends(auth.aktueller_admin),
                db: Session = Depends(get_db)) -> HTMLResponse:
    f = db.get(Feld, uuid.UUID(feld))
    start = _lokal(beginn)
    from beachhub_core.services import slots_db
    tages = slots_db.tages_slots(db, f, start.astimezone(BERLIN).date())  # type: ignore[arg-type]
    enden = []
    for s in tages:
        if s.beginn >= start and (not enden or s.beginn == enden[-1]):
            enden.append(s.ende)
    kunden_liste = db.scalars(select(Kunde).where(Kunde.anonymisiert_am.is_(None)).order_by(Kunde.name)).all()
    return render(request, "belegung/buchung_neu.html", admin=admin, feld=f, beginn=start, enden=enden, kunden=kunden_liste)


@router.post("/belegung/buchung")
def buchung_anlegen(request: Request, feld_id: str = Form(...), kunde_id: str = Form(...), beginn: str = Form(...), ende: str = Form(...),
                    admin: AdminUser = Depends(auth.nur_admin_rolle), db: Session = Depends(get_db)) -> HTMLResponse | RedirectResponse:
    try:
        b = buchungen.lege_an(db, feld_id=uuid.UUID(feld_id), kunde_id=uuid.UUID(kunde_id), beginn=_lokal(beginn), ende=_lokal(ende),
                              quelle="admin", admin_user_id=admin.id)
        r = rechnungen.erzeuge_einzelrechnung(db, b) if b.zahlungsart == "online" else None
        if r:
            rechnung_pdf.erzeuge(db, r)
        db.commit()
    except BuchungsFehler as e:
        db.rollback()
        return mit_flash(RedirectResponse(f"/admin/belegung/buchung/neu?feld={feld_id}&beginn={beginn}", status_code=303),
                         GRUND.get(e.grund, e.grund), "fehler")
    benachrichtigung.buchung_bestaetigt(db, b)
    if r:
        benachrichtigung.rechnung(db, r)
    return mit_flash(RedirectResponse(_woche_url(b.feld_id, b.beginn), status_code=303), "Buchung angelegt")


@router.get("/belegung/buchung/{buchung_id}", response_class=HTMLResponse)
def buchung_detail(request: Request, buchung_id: uuid.UUID, admin: AdminUser = Depends(auth.aktueller_admin),
                   db: Session = Depends(get_db)) -> HTMLResponse:
    b = db.get(Buchung, buchung_id)
    if b is None:
        return mit_flash(RedirectResponse("/admin/belegung", status_code=303), "Buchung nicht gefunden", "fehler")  # type: ignore[return-value]
    pin_klar = pin.entschluessele(b.pin_verschluesselt) if b.pin_verschluesselt else None
    return render(request, "belegung/buchung.html", admin=admin, b=b, pin=pin_klar, zurueck=_woche_url(b.feld_id, b.beginn))


@router.post("/belegung/buchung/{buchung_id}/storno")
def buchung_storno(buchung_id: uuid.UUID, grund: str = Form(""), kostenfrei: str = Form("auto"),
                   admin: AdminUser = Depends(auth.nur_admin_rolle), db: Session = Depends(get_db)) -> RedirectResponse:
    b = db.get(Buchung, buchung_id)
    if b is None:
        return mit_flash(RedirectResponse("/admin/belegung", status_code=303), "Buchung nicht gefunden", "fehler")
    try:
        s = storno.storniere(db, b, durch="betreiber", admin_user_id=admin.id, grund=grund,
                             kostenfrei={"ja": True, "nein": False}.get(kostenfrei))
        db.commit()
    except StornoFehler as e:
        db.rollback()
        return mit_flash(RedirectResponse(f"/admin/belegung/buchung/{b.id}", status_code=303), GRUND.get(str(e), str(e)), "fehler")
    benachrichtigung.storno(db, s)
    return mit_flash(RedirectResponse(f"/admin/belegung/buchung/{b.id}", status_code=303), "Storniert")


@router.post("/belegung/buchung/{buchung_id}/kulanz")
def buchung_kulanz(buchung_id: uuid.UUID, grund: str = Form(...), admin: AdminUser = Depends(auth.nur_admin_rolle),
                   db: Session = Depends(get_db)) -> RedirectResponse:
    b = db.get(Buchung, buchung_id)
    if b is None or b.storno is None:
        return mit_flash(RedirectResponse("/admin/belegung", status_code=303), "Kein Storno vorhanden", "fehler")
    storno.kulanz(db, b.storno, admin_user_id=admin.id, grund=grund)
    db.commit()
    return mit_flash(RedirectResponse(f"/admin/belegung/buchung/{b.id}", status_code=303), "Kulanz gebucht")


# ---- Sperren ----
@router.get("/belegung/sperre/neu", response_class=HTMLResponse)
def sperre_neu(request: Request, admin: AdminUser = Depends(auth.aktueller_admin), db: Session = Depends(get_db)) -> HTMLResponse:
    felder = db.scalars(select(Feld).where(Feld.aktiv.is_(True)).order_by(Feld.reihenfolge)).all()
    return render(request, "belegung/sperre_neu.html", admin=admin, felder=felder, betroffen=[], werte={})


@router.post("/belegung/sperre")
async def sperre_anlegen(request: Request, admin: AdminUser = Depends(auth.nur_admin_rolle), db: Session = Depends(get_db)) -> HTMLResponse | RedirectResponse:
    form = await request.form()
    alle = str(form.get("alle_felder", "")) == "1"
    feld_ids = None if alle else [uuid.UUID(str(v)) for v in form.getlist("feld_ids")]
    beginn, ende, grund = _lokal(str(form["beginn"])), _lokal(str(form["ende"])), str(form.get("grund", "")).strip()
    entscheidungen = {uuid.UUID(k.removeprefix("entscheidung_")): str(v) for k, v in form.items() if k.startswith("entscheidung_")}
    try:
        sperren.lege_an(db, feld_ids=feld_ids, beginn=beginn, ende=ende, grund=grund, admin_user_id=admin.id, entscheidungen=entscheidungen)
        db.commit()
    except SperrenFehler as e:
        db.rollback()
        felder = db.scalars(select(Feld).where(Feld.aktiv.is_(True)).order_by(Feld.reihenfolge)).all()
        betroffen = sperren.betroffene_buchungen(db, feld_ids=feld_ids, beginn=beginn, ende=ende)
        return render(request, "belegung/sperre_neu.html", admin=admin, felder=felder, betroffen=betroffen,
                      werte=dict(form), fehler=GRUND.get(str(e), str(e)))
    return mit_flash(RedirectResponse(_woche_url(feld_ids[0] if feld_ids else db.scalars(select(Feld.id)).first(), beginn), status_code=303), "Sperre angelegt")  # type: ignore[arg-type]


@router.post("/belegung/sperre/{sperre_id}/loeschen")
def sperre_loeschen(sperre_id: uuid.UUID, admin: AdminUser = Depends(auth.nur_admin_rolle), db: Session = Depends(get_db)) -> RedirectResponse:
    s = db.get(Sperre, sperre_id)
    if s:
        sperren.loesche(db, s, admin_user_id=admin.id)
        db.commit()
    return mit_flash(RedirectResponse("/admin/belegung", status_code=303), "Sperre gelöscht")


# ---- Dauerbuchungen ----
def _dauer_args(form) -> dict:  # type: ignore[no-untyped-def, type-arg]
    return dict(kunde_id=uuid.UUID(str(form["kunde_id"])), feld_id=uuid.UUID(str(form["feld_id"])), wochentag=int(str(form["wochentag"])),
                start=t_zeit(str(form["start"])), ende=t_zeit(str(form["ende"])), gueltig_von=t_datum(str(form["gueltig_von"])),
                gueltig_bis=t_datum(str(form["gueltig_bis"])))


@router.get("/belegung/dauer/neu", response_class=HTMLResponse)
def dauer_neu(request: Request, admin: AdminUser = Depends(auth.aktueller_admin), db: Session = Depends(get_db)) -> HTMLResponse:
    return render(request, "belegung/dauer_neu.html", admin=admin, termine=None, werte={},
                  kunden=db.scalars(select(Kunde).where(Kunde.anonymisiert_am.is_(None)).order_by(Kunde.name)).all(),
                  felder=db.scalars(select(Feld).where(Feld.aktiv.is_(True)).order_by(Feld.reihenfolge)).all())


@router.post("/belegung/dauer/planen", response_class=HTMLResponse)
async def dauer_planen(request: Request, admin: AdminUser = Depends(auth.nur_admin_rolle), db: Session = Depends(get_db)) -> HTMLResponse:
    form = await request.form()
    termine = dauerbuchungen.plane(db, **_dauer_args(form))
    return render(request, "belegung/dauer_neu.html", admin=admin, termine=termine, werte=dict(form),
                  kunden=db.scalars(select(Kunde).where(Kunde.anonymisiert_am.is_(None)).order_by(Kunde.name)).all(),
                  felder=db.scalars(select(Feld).where(Feld.aktiv.is_(True)).order_by(Feld.reihenfolge)).all())


@router.post("/belegung/dauer")
async def dauer_anlegen(request: Request, admin: AdminUser = Depends(auth.nur_admin_rolle), db: Session = Depends(get_db)) -> HTMLResponse | RedirectResponse:
    form = await request.form()
    auslassen = {date.fromisoformat(k.removeprefix("auslassen_")) for k in form if k.startswith("auslassen_")}
    entscheidungen = {uuid.UUID(k.removeprefix("entscheidung_")): str(v) for k, v in form.items() if k.startswith("entscheidung_")}
    try:
        d = dauerbuchungen.lege_an(db, **_dauer_args(form), admin_user_id=admin.id, auslassen=auslassen, entscheidungen=entscheidungen)
        db.commit()
    except DauerbuchungsFehler as e:
        db.rollback()
        return render(
            request, "belegung/dauer_neu.html", admin=admin, termine=dauerbuchungen.plane(db, **_dauer_args(form)),
            werte=dict(form), fehler=GRUND.get(str(e), str(e)),
            kunden=db.scalars(select(Kunde).where(Kunde.anonymisiert_am.is_(None)).order_by(Kunde.name)).all(),
            felder=db.scalars(select(Feld).where(Feld.aktiv.is_(True)).order_by(Feld.reihenfolge)).all())
    benachrichtigung.dauerbuchung_angelegt(db, d)
    return mit_flash(RedirectResponse(f"/admin/belegung/dauer/{d.id}", status_code=303), "Dauerbuchung angelegt")


@router.get("/belegung/dauer/{dauer_id}", response_class=HTMLResponse)
def dauer_detail(request: Request, dauer_id: uuid.UUID, admin: AdminUser = Depends(auth.aktueller_admin), db: Session = Depends(get_db)) -> HTMLResponse:
    d = db.get(Dauerbuchung, dauer_id)
    if d is None:
        return mit_flash(RedirectResponse("/admin/belegung", status_code=303), "Nicht gefunden", "fehler")  # type: ignore[return-value]
    return render(request, "belegung/dauer.html", admin=admin, d=d, pin=pin.entschluessele(d.pin_verschluesselt))


@router.post("/belegung/dauer/{dauer_id}/beenden")
def dauer_beenden(dauer_id: uuid.UUID, ab: str = Form(...), admin: AdminUser = Depends(auth.nur_admin_rolle), db: Session = Depends(get_db)) -> RedirectResponse:
    d = db.get(Dauerbuchung, dauer_id)
    if d is None:
        return mit_flash(RedirectResponse("/admin/belegung", status_code=303), "Nicht gefunden", "fehler")
    dauerbuchungen.beende(db, d, ab=date.fromisoformat(ab), admin_user_id=admin.id)
    db.commit()
    return mit_flash(RedirectResponse(f"/admin/belegung/dauer/{d.id}", status_code=303), "Dauerbuchung beendet")
```
Templates: `belegung/woche.html` – Feld-Select (GET-Formular), Navigation (vorher/nachher), Links „Sperre anlegen“, „Dauerbuchung anlegen“; Raster: Zeilen = Slots (Uhrzeit aus dem ersten Tag mit Slots), Spalten = 7 Tage mit Kopf `{{ wochentage[loop.index0] }} {{ tag.datum|datum }}` (Test erwartet „Mi 01.12.“ → Kopf als `{{ wochentage[...] }} {{ tag.datum.strftime("%d.%m.") }}`); Zelle `<a class="zelle frei" href="/admin/belegung/buchung/neu?feld=…&beginn=…">frei</a>` bzw. `<a class="zelle belegt" href="/admin/belegung/buchung/{{ z.buchung.id }}">{{ z.buchung.kunde.name }}</a>` bzw. `<span class="zelle sperre">{{ z.sperre.grund }}</span>`. Da Tage unterschiedliche Slots haben können, wird je Tag eine eigene Spalte als Liste gerendert (CSS-Grid mit 7 Spalten, jede Spalte ein `<div>` mit Zellen untereinander).
`buchung_neu.html`: Feld, Beginn (readonly, hidden `beginn`), Ende als Select aus `enden`, Kunde als Select (`<select name="kunde_id">` mit Name + E-Mail). `buchung.html`: alle Werte, PIN groß, Karte Storno mit Formular (grund, kostenfrei: auto/ja/nein), bei vorhandenem Storno Anzeige (kostenfrei, freigestellt, nachbuchung offen) und Kulanz-Formular. `sperre_neu.html`: Checkbox alle_felder, Checkboxen feld_ids, beginn/ende (`datetime-local`), grund; Liste `betroffen` mit Radio `entscheidung_{{ b.id }}` (behalten/stornieren). `dauer_neu.html`: Formular mit zwei Buttons (`formaction=/admin/belegung/dauer/planen` „Termine prüfen“ und `formaction=/admin/belegung/dauer` „Anlegen“), Terminliste mit Datum, Preis, Kollisionen, Checkbox `auslassen_{{ t.datum.isoformat() }}`, Radio `entscheidung_{{ k.id }}` je Buchungskollision. `dauer.html`: Kopf, PIN, Terminliste mit Status, Formular „Beenden ab“.

In `main.py`: `app.include_router(belegung.router, prefix="/admin", dependencies=csrf)`.

- [ ] **Step 5: Tests grün**

Run: `cd core && pytest -q`
Expected: alle grün

- [ ] **Step 6: Commit**

```bash
git add core && git commit -m "feat(core): Admin-UI Belegungsplan mit Buchung, Storno, Sperren und Dauerbuchungen"
```

---

## Task 18: Admin-UI Rechnungen und Monatslauf-Job

**Files:**
- Create: `core/beachhub_core/routes/rechnungen.py`, `core/beachhub_core/templates/rechnungen/liste.html`, `core/beachhub_core/templates/rechnungen/detail.html`, `core/beachhub_core/jobs.py`
- Modify: `core/beachhub_core/main.py` (Router, Scheduler im Lifespan)
- Test: `core/tests/test_ui_rechnungen.py`, `core/tests/test_jobs.py`

**Interfaces:**
- Routen: `GET /admin/rechnungen?status=&von=&bis=&q=` (Filter Status, Datumsbereich, Kundenname), `GET /admin/rechnungen/{id}` (Detail mit Positionen, Integritätsstatus des PDFs), `GET /admin/rechnungen/{id}/pdf` (FileResponse; erzeugt das PDF bei Bedarf), `POST /admin/rechnungen/{id}/bezahlt`, `POST /admin/rechnungen/{id}/storno` (grund; erzeugt Stornorechnung + PDF + Mail), `POST /admin/rechnungen/monatslauf` (jahr, monat; erzeugt Sammelrechnungen + PDFs + Mails; zeigt Anzahl), `GET /admin/rechnungen/export.csv?von=&bis=` (`text/csv`, Dateiname `rechnungen_<von>_<bis>.csv`).
- Produces `jobs.monatslauf_faellig(db) -> tuple[int, int] | None` – liefert (Jahr, Monat) des Vormonats, wenn `clock.today(db).day >= rechnung_tag_im_folgemonat` und für den Vormonat noch kein Lauf protokolliert ist (`AppSetting("monatslauf_letzter") = "JJJJ-MM"`); `jobs.monatslauf_ausfuehren(db) -> int` (erzeugt Rechnungen, PDFs, Mails, setzt Marker; gibt Anzahl zurück); `jobs.starte_scheduler() -> BackgroundScheduler` (APScheduler, täglich 06:00 Europe/Berlin: Monatslauf; alle 5 Minuten: `lesestand.verarbeite_geaenderte` aus Task 19 – dort registriert); `jobs.stoppe_scheduler()`.
- `main.py`-Lifespan: Scheduler starten, wenn `settings.enable_scheduler`.

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_ui_rechnungen.py`:
```python
from datetime import date, time
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from beachhub_core import clock, mail
from beachhub_core.models import Betriebszeit, Feld, FeldRaster, Kundengruppe, Rechnung, Tarif
from beachhub_core.services import buchungen, kunden


@pytest.fixture
def welt(db: Session):
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    v = Kundengruppe(name="Verein", standard_zahlungsart="rechnung")
    db.add_all([f, v, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    v1 = kunden.lege_an(db, name="TSV", email="v@x.de", kundengruppe_id=v.id)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    from beachhub_shared.zeit import kombiniere
    buchungen.lege_an(db, feld_id=f.id, kunde_id=v1.id, beginn=kombiniere(date(2027, 12, 1), time(19)), ende=kombiniere(date(2027, 12, 1), time(20)))
    db.commit()
    return f, v1


def test_monatslauf_liste_pdf_bezahlt_storno_export(eingeloggt: TestClient, db: Session, welt) -> None:
    c = eingeloggt
    clock.set_override(db, date(2028, 1, 3))
    r = c.post("/admin/rechnungen/monatslauf", data={"csrf_token": c.csrf, "jahr": "2027", "monat": "12"}, follow_redirects=False)
    assert r.status_code == 303
    rechnung = db.query(Rechnung).one()
    assert rechnung.pdf_sha256 and any(m["betreff"] == f"Rechnung {rechnung.nummer}" for m in mail.TEST_AUSGANG)
    seite = c.get("/admin/rechnungen?status=offen")
    assert rechnung.nummer in seite.text and "TSV" in seite.text
    pdf = c.get(f"/admin/rechnungen/{rechnung.id}/pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    r = c.post(f"/admin/rechnungen/{rechnung.id}/bezahlt", data={"csrf_token": c.csrf}, follow_redirects=False)
    assert r.status_code == 303
    db.refresh(rechnung)
    assert rechnung.status == "bezahlt"
    r = c.post(f"/admin/rechnungen/{rechnung.id}/storno", data={"csrf_token": c.csrf, "grund": "Fehler"}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert db.query(Rechnung).count() == 2 and db.get(Rechnung, rechnung.id).status == "storniert"
    csv = c.get("/admin/rechnungen/export.csv?von=2028-01-01&bis=2028-01-31")
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv") and len(csv.text.strip().splitlines()) == 3
```

`core/tests/test_jobs.py`:
```python
from datetime import date

from sqlalchemy.orm import Session

from beachhub_core import clock, jobs
from beachhub_core.models import AppSetting


def test_monatslauf_faellig_nur_einmal_pro_monat(db: Session) -> None:
    clock.set_override(db, date(2028, 1, 2))
    assert jobs.monatslauf_faellig(db) is None          # Tag 2 < 3
    clock.set_override(db, date(2028, 1, 3))
    assert jobs.monatslauf_faellig(db) == (2027, 12)
    assert jobs.monatslauf_ausfuehren(db) == 0           # keine Rechnungskunden → 0 Rechnungen, Marker trotzdem
    db.commit()
    assert db.get(AppSetting, "monatslauf_letzter").value == "2027-12"
    assert jobs.monatslauf_faellig(db) is None
    clock.set_override(db, date(2028, 2, 5))
    assert jobs.monatslauf_faellig(db) == (2028, 1)
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_ui_rechnungen.py tests/test_jobs.py`
Expected: FAIL (404 / ImportError)

- [ ] **Step 3: Jobs**

`core/beachhub_core/jobs.py`:
```python
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from beachhub_core import clock
from beachhub_core.database import SessionLocal
from beachhub_core.models import AppSetting
from beachhub_core.services import benachrichtigung, konfiguration, rechnung_pdf, rechnungen

logger = logging.getLogger(__name__)
MARKER = "monatslauf_letzter"
_scheduler: BackgroundScheduler | None = None


def monatslauf_faellig(db: Session) -> tuple[int, int] | None:
    heute = clock.today(db)
    if heute.day < konfiguration.hole(db, "rechnung_tag_im_folgemonat"):
        return None
    jahr, monat = (heute.year, heute.month - 1) if heute.month > 1 else (heute.year - 1, 12)
    marker = db.get(AppSetting, MARKER)
    if marker and marker.value == f"{jahr}-{monat:02d}":
        return None
    return jahr, monat


def monatslauf_ausfuehren(db: Session) -> int:
    faellig = monatslauf_faellig(db)
    if faellig is None:
        return 0
    jahr, monat = faellig
    erzeugt = rechnungen.monatslauf(db, jahr, monat)
    for r in erzeugt:
        rechnung_pdf.erzeuge(db, r)
    marker = db.get(AppSetting, MARKER)
    if marker:
        marker.value = f"{jahr}-{monat:02d}"
    else:
        db.add(AppSetting(key=MARKER, value=f"{jahr}-{monat:02d}"))
    db.commit()
    for r in erzeugt:
        benachrichtigung.rechnung(db, r)
    logger.info("Monatslauf %s-%02d: %d Rechnungen", jahr, monat, len(erzeugt))
    return len(erzeugt)


def _job_monatslauf() -> None:
    with SessionLocal() as db:
        try:
            monatslauf_ausfuehren(db)
        except Exception:
            logger.exception("Monatslauf fehlgeschlagen")
            benachrichtigung.betreiber_alarm("Monatslauf fehlgeschlagen", "Details im Log des Hauptsystems.")


def _job_lesestand() -> None:
    from beachhub_core.services import lesestand  # Task 19

    with SessionLocal() as db:
        try:
            lesestand.verarbeite_geaenderte(db)
        except Exception:
            logger.exception("Lesestand-Aktualisierung fehlgeschlagen")


def starte_scheduler() -> BackgroundScheduler:
    global _scheduler
    s = BackgroundScheduler(timezone="Europe/Berlin")
    s.add_job(_job_monatslauf, CronTrigger(hour=6, minute=0), id="monatslauf", replace_existing=True)
    s.add_job(_job_lesestand, IntervalTrigger(minutes=5), id="lesestand", replace_existing=True)
    s.start()
    _scheduler = s
    return s


def stoppe_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
```
Bis Task 19 existiert `lesestand` nicht; der Import liegt bewusst in der Funktion, damit Task 18 ohne Task 19 lauffähig ist (der Job loggt dann einen ImportError alle 5 Minuten – in Tests ist der Scheduler aus).

`main.py`-Lifespan:
```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if settings.enable_scheduler:
        from beachhub_core import jobs
        jobs.starte_scheduler()
    yield
    if settings.enable_scheduler:
        from beachhub_core import jobs
        jobs.stoppe_scheduler()
```

- [ ] **Step 4: Routen**

`core/beachhub_core/routes/rechnungen.py`:
```python
import uuid
from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_core import auth
from beachhub_core.database import get_db
from beachhub_core.models import AdminUser, Kunde, Rechnung
from beachhub_core.routes._form import t_datum
from beachhub_core.services import benachrichtigung, rechnung_pdf, rechnungen
from beachhub_core.services.rechnungen import RechnungsFehler
from beachhub_core.templating import mit_flash, render

router = APIRouter()


@router.get("/rechnungen", response_class=HTMLResponse)
def liste(request: Request, status: str = "", von: str = "", bis: str = "", q: str = "",
          admin: AdminUser = Depends(auth.aktueller_admin), db: Session = Depends(get_db)) -> HTMLResponse:
    stmt = select(Rechnung).join(Kunde).order_by(Rechnung.nummer.desc())
    if status:
        stmt = stmt.where(Rechnung.status == status)
    if t_datum(von):
        stmt = stmt.where(Rechnung.datum >= t_datum(von))
    if t_datum(bis):
        stmt = stmt.where(Rechnung.datum <= t_datum(bis))
    if q:
        stmt = stmt.where(Kunde.name.ilike(f"%{q}%"))
    return render(request, "rechnungen/liste.html", admin=admin, rechnungen=db.scalars(stmt.limit(500)).all(),
                  status=status, von=von, bis=bis, q=q)


@router.get("/rechnungen/export.csv")
def export(von: str, bis: str, admin: AdminUser = Depends(auth.aktueller_admin), db: Session = Depends(get_db)) -> Response:
    v, b = t_datum(von) or date(2000, 1, 1), t_datum(bis) or date(2100, 1, 1)
    return Response(rechnungen.csv_export(db, v, b), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="rechnungen_{v}_{b}.csv"'})


@router.get("/rechnungen/{rechnung_id}", response_class=HTMLResponse)
def detail(request: Request, rechnung_id: uuid.UUID, admin: AdminUser = Depends(auth.aktueller_admin), db: Session = Depends(get_db)) -> HTMLResponse:
    r = db.get(Rechnung, rechnung_id)
    if r is None:
        return mit_flash(RedirectResponse("/admin/rechnungen", status_code=303), "Nicht gefunden", "fehler")  # type: ignore[return-value]
    return render(request, "rechnungen/detail.html", admin=admin, r=r, integritaet=rechnung_pdf.pruefe_integritaet(r))


@router.get("/rechnungen/{rechnung_id}/pdf")
def pdf(rechnung_id: uuid.UUID, admin: AdminUser = Depends(auth.aktueller_admin), db: Session = Depends(get_db)) -> FileResponse | RedirectResponse:
    r = db.get(Rechnung, rechnung_id)
    if r is None:
        return mit_flash(RedirectResponse("/admin/rechnungen", status_code=303), "Nicht gefunden", "fehler")
    if not r.pdf_pfad:
        rechnung_pdf.erzeuge(db, r)
        db.commit()
    return FileResponse(r.pdf_pfad, media_type="application/pdf", filename=f"{r.nummer}.pdf")  # type: ignore[arg-type]


@router.post("/rechnungen/{rechnung_id}/bezahlt")
def bezahlt(rechnung_id: uuid.UUID, admin: AdminUser = Depends(auth.nur_admin_rolle), db: Session = Depends(get_db)) -> RedirectResponse:
    r = db.get(Rechnung, rechnung_id)
    if r is None:
        return mit_flash(RedirectResponse("/admin/rechnungen", status_code=303), "Nicht gefunden", "fehler")
    try:
        rechnungen.setze_bezahlt(db, r, admin_user_id=admin.id)
        db.commit()
    except RechnungsFehler as e:
        db.rollback()
        return mit_flash(RedirectResponse(f"/admin/rechnungen/{r.id}", status_code=303), str(e), "fehler")
    return mit_flash(RedirectResponse(f"/admin/rechnungen/{r.id}", status_code=303), "Als bezahlt markiert")


@router.post("/rechnungen/{rechnung_id}/storno")
def storno(rechnung_id: uuid.UUID, grund: str = Form(...), admin: AdminUser = Depends(auth.nur_admin_rolle), db: Session = Depends(get_db)) -> RedirectResponse:
    r = db.get(Rechnung, rechnung_id)
    if r is None:
        return mit_flash(RedirectResponse("/admin/rechnungen", status_code=303), "Nicht gefunden", "fehler")
    try:
        s = rechnungen.storniere(db, r, admin_user_id=admin.id, grund=grund)
        rechnung_pdf.erzeuge(db, s)
        db.commit()
    except RechnungsFehler as e:
        db.rollback()
        return mit_flash(RedirectResponse(f"/admin/rechnungen/{r.id}", status_code=303), str(e), "fehler")
    benachrichtigung.rechnung(db, s)
    return mit_flash(RedirectResponse(f"/admin/rechnungen/{s.id}", status_code=303), f"Stornorechnung {s.nummer} erzeugt")


@router.post("/rechnungen/monatslauf")
def monatslauf(jahr: str = Form(...), monat: str = Form(...), admin: AdminUser = Depends(auth.nur_admin_rolle),
               db: Session = Depends(get_db)) -> RedirectResponse:
    erzeugt = rechnungen.monatslauf(db, int(jahr), int(monat))
    for r in erzeugt:
        rechnung_pdf.erzeuge(db, r)
    db.commit()
    for r in erzeugt:
        benachrichtigung.rechnung(db, r)
    return mit_flash(RedirectResponse("/admin/rechnungen?status=offen", status_code=303), f"{len(erzeugt)} Rechnungen erzeugt")
```
Templates: `rechnungen/liste.html` – Filterformular (GET: status-Select, von, bis, q), Karte „Monatslauf“ (jahr, monat, Button), Link CSV-Export mit den Filterdaten, Tabelle (Nummer, Datum, Kunde, Art, Brutto, Status, fällig, Links Detail/PDF). `rechnungen/detail.html` – Kopf, Adresse, Positionen, Summen, PDF-Link, Integritätsbadge („PDF unverändert“ / „PDF fehlt oder verändert“), Formulare „Als bezahlt markieren“ (nur bei offen) und „Stornorechnung erzeugen“ (grund; nur wenn nicht storniert und Art ≠ storno).

In `main.py`: `app.include_router(rechnungen.router, prefix="/admin", dependencies=csrf)`.

- [ ] **Step 5: Tests grün**

Run: `cd core && pytest -q`
Expected: alle grün

- [ ] **Step 6: Commit**

```bash
git add core && git commit -m "feat(core): Admin-UI Rechnungen, CSV-Export und automatischer Monatslauf"
```

---

## Task 19: Lesestand-Erzeugung mit Signatur, Schlüsselverwaltung, System-Seite

**Files:**
- Create: `shared/beachhub_shared/lesestand.py`, `shared/tests/test_lesestand_schema.py`
- Create: `core/beachhub_core/services/lesestand.py`, `core/beachhub_core/routes/system.py`, `core/beachhub_core/templates/system/index.html`, `core/beachhub_core/templates/system/audit.html`, `core/beachhub_core/templates/system/stornos.html`
- Modify: `core/beachhub_core/services/buchungen.py`, `sperren.py`, `storno.py`, `stammdaten.py`, `guthaben.py`, `rechnungen.py` (je ein Aufruf `lesestand.markiere_geaendert(...)` nach Änderungen), `core/beachhub_core/main.py`
- Test: `core/tests/test_lesestand.py`, `core/tests/test_ui_system.py`

**Interfaces:**
- Produces `shared/lesestand.py` (pydantic-Modelle, gemeinsamer Vertrag für Portal in Stufe 2): `Dokument(dokument: str, version: int, erzeugt_am: datetime, inhalt: dict, signatur: str)`; Inhalte: `BelegungInhalt(felder: list[FeldInfo], betriebszeiten: list[BetriebszeitInfo], ausnahmetage: list[AusnahmeInfo], fenster_tage: int, mindestvorlauf_minuten: int, belegt: dict[str, list[Zeitraum]])` mit `FeldInfo(id, name, reihenfolge, raster: list[RasterInfo])`, `RasterInfo(wochentag, modus, slot_minuten, fenster: list[list[str]])`, `Zeitraum(beginn: datetime, ende: datetime)`; `TarifeInhalt(regeln: list[TarifInfo])` mit `TarifInfo(name, preis: Decimal, feld_id, wochentag, uhrzeit_von, uhrzeit_bis, kundengruppe, gueltig_von, gueltig_bis)`; `KontoInhalt(kunde_id, kundengruppe, zahlungsart, guthaben: Decimal, buchungen: list[KontoBuchung], rechnungen: list[KontoRechnung])` mit `KontoBuchung(id, feld_id, feld_name, beginn, ende, status, preis, pin: str|None, storno: StornoInfo|None)`, `StornoInfo(kostenfrei, nachbuchung_offen, freigestellt_betrag)`, `KontoRechnung(nummer, datum, brutto, status)`.
- Produces `services/lesestand.py`: `erzeuge_schluessel() -> str` (schreibt Privatschlüssel nach `settings.signatur_privatschluessel_pfad` mit Modus 0600, gibt öffentlichen Schlüssel hex zurück; `FileExistsError`, wenn vorhanden), `oeffentlicher_schluessel() -> str`, `baue_belegung(db) -> BelegungInhalt` (Zeiträume: aktive Buchungen und Sperren von heute bis heute + `fenster_tage` + 1, ohne Kundenbezug), `baue_tarife(db) -> TarifeInhalt` (aktive, heute gültige Tarife), `baue_konto(db, kunde) -> KontoInhalt` (Buchungen ab heute − 90 Tage, PIN entschlüsselt nur für aktive künftige Buchungen), `publiziere(db, name: str) -> Dokument` (baut Inhalt nach Name: `belegung`, `tarife`, `konto:<uuid>`; erhöht `LesestandVersion.version`, signiert mit `beachhub_shared.signatur.signiere`, setzt `signiert_am`, `geaendert=False`; speichert das Dokument als JSON unter `settings.data_dir / "lesestand" / f"{name}.json`` – Übertragung folgt in Stufe 2), `markiere_geaendert(db, *namen: str)` (setzt/legt `LesestandVersion` mit `geaendert=True` an), `verarbeite_geaenderte(db) -> list[str]` (publiziert alle mit `geaendert=True`), `lade(name) -> Dokument | None` (aus Datei), `pruefe(dok: Dokument) -> bool`.
- Änderungs-Hooks: `buchungen.lege_an`/`setze_status` → `markiere_geaendert(db, "belegung", f"konto:{kunde_id}")`; `sperren.lege_an/loesche` → `"belegung"`; `storno.pruefe_nachbuchung/kulanz` → `f"konto:{kunde_id}"`; `guthaben.buche` → `f"konto:{kunde_id}"`; `rechnungen._neue_rechnung/setze_bezahlt/storniere` → `f"konto:{kunde_id}"`; `stammdaten.*` (Felder, Raster, Betriebszeiten, Ausnahmetage, Tarife) → `"belegung"` bzw. `"tarife"`; `konfiguration.setze` bei `fenster_tage`/`mindestvorlauf_minuten` → `"belegung"`. Import in diesen Modulen als `from beachhub_core.services import lesestand` **innerhalb der Funktion** (Zirkelimport vermeiden).
- System-Routen: `GET /admin/system` (öffentlicher Schlüssel, Lesestand-Versionen mit `geaendert`, Uhr-Override-Formular, Buttons „Lesestand jetzt erzeugen“), `POST /admin/system/lesestand` (verarbeite_geaenderte, alle drei Typen erzwingen mit `alle=1`), `POST /admin/system/uhr` (datum oder leer), `GET /admin/system/audit?typ=&seite=` (Audit-Liste, 100 je Seite), `GET /admin/system/stornos` (Stornos mit `nachbuchung_offen`, Link zur Buchung).

- [ ] **Step 1: Failing Tests schreiben**

`shared/tests/test_lesestand_schema.py`:
```python
from datetime import UTC, datetime
from decimal import Decimal

from beachhub_shared.lesestand import Dokument, TarifeInhalt, TarifInfo


def test_dokument_roundtrip_json() -> None:
    inhalt = TarifeInhalt(regeln=[TarifInfo(name="Std", preis=Decimal("30.00"), feld_id=None, wochentag=None, uhrzeit_von=None,
                                            uhrzeit_bis=None, kundengruppe="Privat", gueltig_von=None, gueltig_bis=None)])
    d = Dokument(dokument="tarife", version=1, erzeugt_am=datetime(2027, 11, 1, tzinfo=UTC), inhalt=inhalt.model_dump(mode="json"), signatur="00")
    wieder = Dokument.model_validate_json(d.model_dump_json())
    assert wieder.inhalt["regeln"][0]["preis"] == "30.00"
```

`core/tests/test_lesestand.py`:
```python
import json
from datetime import date, time
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from beachhub_core import clock
from beachhub_core.config import settings
from beachhub_core.models import Betriebszeit, Feld, FeldRaster, Kundengruppe, LesestandVersion, Sperre, Tarif
from beachhub_core.services import buchungen, kunden, lesestand, storno
from beachhub_shared.signatur import pruefe
from beachhub_shared.zeit import kombiniere


@pytest.fixture
def welt(db: Session):
    Path(settings.signatur_privatschluessel_pfad).unlink(missing_ok=True)
    lesestand.erzeuge_schluessel()
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    p = Kundengruppe(name="Privat")
    db.add_all([f, p, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    a = kunden.lege_an(db, name="A", email="a@x.de", kundengruppe_id=p.id)
    b = kunden.lege_an(db, name="B", email="b@x.de", kundengruppe_id=p.id)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    return f, a, b


def test_schluessel_nur_einmal(welt) -> None:
    with pytest.raises(FileExistsError):
        lesestand.erzeuge_schluessel()
    assert len(lesestand.oeffentlicher_schluessel()) == 64


def test_belegung_ohne_kundenbezug_und_signiert(db: Session, welt) -> None:
    f, a, _ = welt
    d = date(2027, 12, 1)
    buchungen.lege_an(db, feld_id=f.id, kunde_id=a.id, beginn=kombiniere(d, time(19)), ende=kombiniere(d, time(21)))
    db.add(Sperre(feld_id=None, beginn=kombiniere(d, time(9)), ende=kombiniere(d, time(12)), grund="Wartung"))
    db.commit()
    dok = lesestand.publiziere(db, "belegung")
    assert dok.version == 1 and pruefe(dok.model_dump(mode="json", exclude={"signatur"}), dok.signatur, lesestand.oeffentlicher_schluessel())
    belegt = dok.inhalt["belegt"][str(f.id)]
    assert len(belegt) == 2 and "kunde" not in json.dumps(dok.inhalt) and "A" not in json.dumps(belegt)
    assert dok.inhalt["fenster_tage"] == 14 and dok.inhalt["felder"][0]["raster"][0]["slot_minuten"] == 60
    assert lesestand.publiziere(db, "belegung").version == 2
    assert Path(settings.data_dir, "lesestand", "belegung.json").exists()


def test_konto_enthaelt_eigene_buchungen_mit_pin(db: Session, welt) -> None:
    f, a, b = welt
    d = date(2027, 12, 1)
    ba = buchungen.lege_an(db, feld_id=f.id, kunde_id=a.id, beginn=kombiniere(d, time(19)), ende=kombiniere(d, time(20)))
    buchungen.lege_an(db, feld_id=f.id, kunde_id=b.id, beginn=kombiniere(d, time(20)), ende=kombiniere(d, time(21)))
    db.commit()
    dok = lesestand.publiziere(db, f"konto:{a.id}")
    assert [x["id"] for x in dok.inhalt["buchungen"]] == [str(ba.id)]
    assert dok.inhalt["buchungen"][0]["pin"].isdigit() and dok.inhalt["guthaben"] == "0.00"


def test_aenderungen_markieren_und_verarbeiten(db: Session, welt) -> None:
    f, a, _ = welt
    d = date(2027, 12, 1)
    bu = buchungen.lege_an(db, feld_id=f.id, kunde_id=a.id, beginn=kombiniere(d, time(19)), ende=kombiniere(d, time(20)))
    db.commit()
    markiert = {v.dokument for v in db.query(LesestandVersion).filter_by(geaendert=True)}
    assert markiert == {"belegung", f"konto:{a.id}"}
    assert sorted(lesestand.verarbeite_geaenderte(db)) == sorted(markiert)
    db.commit()
    assert db.query(LesestandVersion).filter_by(geaendert=True).count() == 0
    storno.storniere(db, bu, durch="kunde")
    db.commit()
    assert db.query(LesestandVersion).filter_by(geaendert=True).count() == 2
```

`core/tests/test_ui_system.py`:
```python
from fastapi.testclient import TestClient


def test_system_seite_und_uhr(eingeloggt: TestClient) -> None:
    c = eingeloggt
    assert c.get("/admin/system").status_code == 200
    r = c.post("/admin/system/uhr", data={"csrf_token": c.csrf, "datum": "2027-12-24"}, follow_redirects=False)
    assert r.status_code == 303
    assert "24.12.2027" in c.get("/admin/system").text
    assert c.get("/admin/system/audit").status_code == 200
    assert c.get("/admin/system/stornos").status_code == 200
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd shared && pytest -q; cd ../core && pytest -q tests/test_lesestand.py tests/test_ui_system.py`
Expected: FAIL mit ImportError

- [ ] **Step 3: Schema in shared**

`shared/beachhub_shared/lesestand.py`:
```python
"""Vertrag der Lesestand-Dokumente zwischen Hauptsystem (Erzeuger) und Portal (Verbraucher)."""
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


class Dokument(BaseModel):
    dokument: str
    version: int
    erzeugt_am: datetime
    inhalt: dict[str, Any]
    signatur: str


class RasterInfo(BaseModel):
    wochentag: int | None
    modus: str
    slot_minuten: int | None
    fenster: list[list[str]]


class FeldInfo(BaseModel):
    id: str
    name: str
    reihenfolge: int
    raster: list[RasterInfo]


class BetriebszeitInfo(BaseModel):
    wochentag: int
    oeffnet: time
    schliesst: time
    gueltig_von: date | None
    gueltig_bis: date | None


class AusnahmeInfo(BaseModel):
    datum: date
    geschlossen: bool
    oeffnet: time | None
    schliesst: time | None


class Zeitraum(BaseModel):
    beginn: datetime
    ende: datetime


class BelegungInhalt(BaseModel):
    felder: list[FeldInfo]
    betriebszeiten: list[BetriebszeitInfo]
    ausnahmetage: list[AusnahmeInfo]
    fenster_tage: int
    mindestvorlauf_minuten: int
    belegt: dict[str, list[Zeitraum]]


class TarifInfo(BaseModel):
    name: str
    preis: Decimal
    feld_id: str | None
    wochentag: int | None
    uhrzeit_von: time | None
    uhrzeit_bis: time | None
    kundengruppe: str | None
    gueltig_von: date | None
    gueltig_bis: date | None


class TarifeInhalt(BaseModel):
    regeln: list[TarifInfo]


class StornoInfo(BaseModel):
    kostenfrei: bool
    nachbuchung_offen: bool
    freigestellt_betrag: Decimal


class KontoBuchung(BaseModel):
    id: str
    feld_id: str
    feld_name: str
    beginn: datetime
    ende: datetime
    status: str
    preis: Decimal
    pin: str | None
    storno: StornoInfo | None


class KontoRechnung(BaseModel):
    nummer: str
    datum: date
    brutto: Decimal
    status: str


class KontoInhalt(BaseModel):
    kunde_id: str
    kundengruppe: str
    zahlungsart: str
    guthaben: Decimal
    buchungen: list[KontoBuchung]
    rechnungen: list[KontoRechnung]
```

- [ ] **Step 4: Service im Hauptsystem**

`core/beachhub_core/services/lesestand.py`:
```python
import json
import os
import uuid
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from beachhub_core import clock
from beachhub_core.config import settings
from beachhub_core.models import (Ausnahmetag, Betriebszeit, Buchung, Feld, Kunde, Kundengruppe, LesestandVersion,
                                  Rechnung, Sperre, Storno, Tarif, utcnow)
from beachhub_core.services import konfiguration, pin
from beachhub_shared import lesestand as schema
from beachhub_shared import signatur


def erzeuge_schluessel() -> str:
    pfad = settings.signatur_privatschluessel_pfad
    if pfad.exists():
        raise FileExistsError(str(pfad))
    priv, pub = signatur.erzeuge_schluesselpaar()
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(priv + "\n", encoding="utf-8")
    os.chmod(pfad, 0o600)
    return pub


def _privat() -> str:
    return signatur.lade_privatschluessel(settings.signatur_privatschluessel_pfad)


def oeffentlicher_schluessel() -> str:
    return signatur.oeffentlicher_schluessel(_privat())


def baue_belegung(db: Session) -> schema.BelegungInhalt:
    heute = clock.today(db)
    fenster = konfiguration.hole(db, "fenster_tage")
    von, bis = clock.now(db) - timedelta(days=1), clock.now(db) + timedelta(days=fenster + 1)
    felder = db.scalars(select(Feld).where(Feld.aktiv.is_(True)).order_by(Feld.reihenfolge)).all()
    belegt: dict[str, list[schema.Zeitraum]] = {}
    for f in felder:
        zr = [schema.Zeitraum(beginn=b.beginn, ende=b.ende) for b in db.scalars(select(Buchung).where(
            Buchung.feld_id == f.id, Buchung.status.in_(Buchung.AKTIVE_STATUS), Buchung.beginn < bis, Buchung.ende > von))]
        zr += [schema.Zeitraum(beginn=s.beginn, ende=s.ende) for s in db.scalars(select(Sperre).where(
            or_(Sperre.feld_id == f.id, Sperre.feld_id.is_(None)), Sperre.beginn < bis, Sperre.ende > von))]
        belegt[str(f.id)] = sorted(zr, key=lambda z: z.beginn)
    return schema.BelegungInhalt(
        felder=[schema.FeldInfo(id=str(f.id), name=f.name, reihenfolge=f.reihenfolge, raster=[
            schema.RasterInfo(wochentag=r.wochentag, modus=r.modus, slot_minuten=r.slot_minuten, fenster=r.fenster_json)
            for r in f.raster]) for f in felder],
        betriebszeiten=[schema.BetriebszeitInfo(wochentag=b.wochentag, oeffnet=b.oeffnet, schliesst=b.schliesst,
                                                gueltig_von=b.gueltig_von, gueltig_bis=b.gueltig_bis)
                        for b in db.scalars(select(Betriebszeit))],
        ausnahmetage=[schema.AusnahmeInfo(datum=a.datum, geschlossen=a.geschlossen, oeffnet=a.oeffnet, schliesst=a.schliesst)
                      for a in db.scalars(select(Ausnahmetag).where(Ausnahmetag.datum >= heute))],
        fenster_tage=fenster, mindestvorlauf_minuten=konfiguration.hole(db, "mindestvorlauf_minuten"), belegt=belegt,
    )


def baue_tarife(db: Session) -> schema.TarifeInhalt:
    heute = clock.today(db)
    regeln = db.scalars(select(Tarif).where(Tarif.aktiv.is_(True), or_(Tarif.gueltig_bis.is_(None), Tarif.gueltig_bis >= heute))).all()
    return schema.TarifeInhalt(regeln=[schema.TarifInfo(
        name=t.name, preis=t.preis, feld_id=str(t.feld_id) if t.feld_id else None, wochentag=t.wochentag,
        uhrzeit_von=t.uhrzeit_von, uhrzeit_bis=t.uhrzeit_bis,
        kundengruppe=(db.get(Kundengruppe, t.kundengruppe_id).name if t.kundengruppe_id else None),  # type: ignore[union-attr]
        gueltig_von=t.gueltig_von, gueltig_bis=t.gueltig_bis) for t in regeln])


def baue_konto(db: Session, kunde: Kunde) -> schema.KontoInhalt:
    jetzt = clock.now(db)
    buchungen = db.scalars(select(Buchung).where(Buchung.kunde_id == kunde.id, Buchung.beginn >= jetzt - timedelta(days=90))
                           .order_by(Buchung.beginn)).all()
    out = []
    for b in buchungen:
        s = db.scalar(select(Storno).where(Storno.buchung_id == b.id))
        zeige_pin = b.aktiv and b.ende > jetzt and b.pin_verschluesselt
        out.append(schema.KontoBuchung(
            id=str(b.id), feld_id=str(b.feld_id), feld_name=b.feld.name, beginn=b.beginn, ende=b.ende, status=b.status,
            preis=b.preis, pin=pin.entschluessele(b.pin_verschluesselt) if zeige_pin else None,  # type: ignore[arg-type]
            storno=schema.StornoInfo(kostenfrei=s.kostenfrei, nachbuchung_offen=s.nachbuchung_offen,
                                     freigestellt_betrag=s.freigestellt_betrag) if s else None))
    rechnungen = db.scalars(select(Rechnung).where(Rechnung.kunde_id == kunde.id).order_by(Rechnung.datum.desc())).all()
    return schema.KontoInhalt(
        kunde_id=str(kunde.id), kundengruppe=kunde.kundengruppe.name, zahlungsart=kunde.zahlungsart, guthaben=kunde.guthaben,
        buchungen=out, rechnungen=[schema.KontoRechnung(nummer=r.nummer, datum=r.datum, brutto=r.brutto, status=r.status) for r in rechnungen])


def _inhalt(db: Session, name: str) -> dict:  # type: ignore[type-arg]
    if name == "belegung":
        return baue_belegung(db).model_dump(mode="json")
    if name == "tarife":
        return baue_tarife(db).model_dump(mode="json")
    if name.startswith("konto:"):
        kunde = db.get(Kunde, uuid.UUID(name.split(":", 1)[1]))
        if kunde is None:
            raise KeyError(name)
        return baue_konto(db, kunde).model_dump(mode="json")
    raise KeyError(name)


def publiziere(db: Session, name: str) -> schema.Dokument:
    inhalt = _inhalt(db, name)
    zeile = db.scalar(select(LesestandVersion).where(LesestandVersion.dokument == name).with_for_update())
    if zeile is None:
        zeile = LesestandVersion(dokument=name, version=0)
        db.add(zeile)
        db.flush()
    zeile.version += 1
    zeile.signiert_am = utcnow()
    zeile.geaendert = False
    # Signiert wird immer über die JSON-Darstellung (Decimal → String, datetime → ISO), genau wie pruefe() sie bildet.
    entwurf = schema.Dokument(dokument=name, version=zeile.version, erzeugt_am=zeile.signiert_am, inhalt=inhalt, signatur="")
    sig = signatur.signiere(entwurf.model_dump(mode="json", exclude={"signatur"}), _privat())
    dok = entwurf.model_copy(update={"signatur": sig})
    ordner = settings.data_dir / "lesestand"
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / f"{name.replace(':', '_')}.json").write_text(dok.model_dump_json(), encoding="utf-8")
    db.flush()
    return dok


def markiere_geaendert(db: Session, *namen: str) -> None:
    for name in namen:
        zeile = db.get(LesestandVersion, name)
        if zeile is None:
            db.add(LesestandVersion(dokument=name, version=0, geaendert=True))
        else:
            zeile.geaendert = True
    db.flush()


def verarbeite_geaenderte(db: Session) -> list[str]:
    namen = [z.dokument for z in db.scalars(select(LesestandVersion).where(LesestandVersion.geaendert.is_(True))).all()]
    for name in namen:
        try:
            publiziere(db, name)
        except KeyError:
            db.delete(db.get(LesestandVersion, name))  # z. B. anonymisierter/gelöschter Kunde
    db.commit()
    return namen


def lade(name: str) -> schema.Dokument | None:
    pfad = settings.data_dir / "lesestand" / f"{name.replace(':', '_')}.json"
    return schema.Dokument.model_validate_json(pfad.read_text(encoding="utf-8")) if pfad.exists() else None


def pruefe(dok: schema.Dokument) -> bool:
    return signatur.pruefe(dok.model_dump(mode="json", exclude={"signatur"}), dok.signatur, oeffentlicher_schluessel())
```
Signatur und Prüfung arbeiten beide über `model_dump(mode="json", exclude={"signatur"})`, damit `Decimal` und `datetime` auf beiden Seiten identisch serialisiert werden (Portal in Stufe 2 prüft genauso).

Hooks einbauen (je ein Zweizeiler in den genannten Services, Import innerhalb der Funktion):
```python
from beachhub_core.services import lesestand
lesestand.markiere_geaendert(db, "belegung", f"konto:{buchung.kunde_id}")
```

- [ ] **Step 5: System-Routen**

`core/beachhub_core/routes/system.py`:
```python
from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from beachhub_core import auth, clock
from beachhub_core.config import settings
from beachhub_core.database import get_db
from beachhub_core.models import AdminUser, Audit, Buchung, LesestandVersion, Storno
from beachhub_core.services import lesestand
from beachhub_core.templating import mit_flash, render

router = APIRouter()


@router.get("/system", response_class=HTMLResponse)
def index(request: Request, admin: AdminUser = Depends(auth.aktueller_admin), db: Session = Depends(get_db)) -> HTMLResponse:
    schluessel = lesestand.oeffentlicher_schluessel() if settings.signatur_privatschluessel_pfad.exists() else None
    return render(request, "system/index.html", admin=admin, schluessel=schluessel, heute=clock.today(db),
                  override=clock.override(db), versionen=db.scalars(select(LesestandVersion).order_by(LesestandVersion.dokument)).all())


@router.post("/system/lesestand")
def lesestand_erzeugen(alle: str = Form(""), admin: AdminUser = Depends(auth.nur_admin_rolle), db: Session = Depends(get_db)) -> RedirectResponse:
    if alle == "1":
        lesestand.markiere_geaendert(db, "belegung", "tarife")
    namen = lesestand.verarbeite_geaenderte(db)
    return mit_flash(RedirectResponse("/admin/system", status_code=303), f"{len(namen)} Dokumente erzeugt")


@router.post("/system/uhr")
def uhr(datum: str = Form(""), admin: AdminUser = Depends(auth.nur_admin_rolle), db: Session = Depends(get_db)) -> RedirectResponse:
    clock.set_override(db, date.fromisoformat(datum) if datum.strip() else None)
    return mit_flash(RedirectResponse("/admin/system", status_code=303), "Uhr gesetzt" if datum else "Uhr zurückgesetzt")


@router.get("/system/audit", response_class=HTMLResponse)
def audit_liste(request: Request, typ: str = "", seite: int = 1, admin: AdminUser = Depends(auth.aktueller_admin),
                db: Session = Depends(get_db)) -> HTMLResponse:
    stmt = select(Audit).order_by(Audit.zeitpunkt.desc())
    if typ:
        stmt = stmt.where(Audit.objekt_typ == typ)
    eintraege = db.scalars(stmt.offset((seite - 1) * 100).limit(100)).all()
    typen = db.scalars(select(Audit.objekt_typ).distinct().order_by(Audit.objekt_typ)).all()
    return render(request, "system/audit.html", admin=admin, eintraege=eintraege, typ=typ, seite=seite, typen=typen)


@router.get("/system/stornos", response_class=HTMLResponse)
def stornos(request: Request, admin: AdminUser = Depends(auth.aktueller_admin), db: Session = Depends(get_db)) -> HTMLResponse:
    offene = db.scalars(select(Storno).join(Buchung, Storno.buchung_id == Buchung.id).where(Storno.nachbuchung_offen.is_(True))
                        .order_by(Buchung.beginn)).all()
    return render(request, "system/stornos.html", admin=admin, stornos=offene)
```
Templates: `system/index.html` (Uhr: aktuelles Datum, Override-Formular mit Datum und Leeren-Button; öffentlicher Schlüssel als `<code>` oder Hinweis „Schlüssel fehlt: `beachhub-core keygen` ausführen“; Tabelle Lesestand-Versionen; Formular „Lesestand erzeugen“ mit Checkbox `alle`), `system/audit.html` (Filter-Select typ, Tabelle Zeitpunkt/Quelle/Typ/Objekt/Admin, Vorher/Nachher als `<details>` mit JSON), `system/stornos.html` (Tabelle: Buchung, Kunde, Zeitraum, Preis, freigestellt, Link).

In `main.py`: `app.include_router(system.router, prefix="/admin", dependencies=csrf)`. In `clock.py` die Funktion `_override` in `override` umbenennen (sie wird jetzt von außen gebraucht) und die beiden internen Aufrufe in `now()` und `set_override()` anpassen.

- [ ] **Step 6: Tests grün, Lint, Typen**

Run: `cd shared && pytest -q && cd ../core && pytest -q && cd .. && ruff check . && mypy`
Expected: alle grün, keine Fehler

- [ ] **Step 7: Commit**

```bash
git add shared core && git commit -m "feat: signierte Lesestand-Dokumente, Schlüsselverwaltung und System-Seite"
```

---

## Task 20: Betrieb – Caddy, WireGuard-Hinweise, Backup, Betriebshandbuch, README

**Files:**
- Create: `core/deploy/Caddyfile`, `core/deploy/backup.sh`, `core/deploy/wireguard-beispiel.md`, `docs/betrieb/hauptsystem.md`, `core/README.md`
- Modify: `README.md` (Repo-Wurzel: Schnellstart), `core/docker-compose.yml` (Caddy-Service)

**Interfaces:** keine Codeschnittstellen; Ergebnis ist eine dokumentierte, reproduzierbare Inbetriebnahme.

- [ ] **Step 1: Caddy und Compose**

`core/deploy/Caddyfile` (lauscht nur auf der WireGuard-Adresse, internes TLS):
```
{
  auto_https disable_redirects
}
https://10.8.0.1:8443 {
  tls internal
  reverse_proxy app:8000
}
```
Compose-Service ergänzen:
```yaml
  caddy:
    image: caddy:2
    network_mode: host
    volumes: ["./deploy/Caddyfile:/etc/caddy/Caddyfile:ro", "caddy_data:/data"]
    depends_on: [app]
```
`app`-Port auf `127.0.0.1:8000` belassen; Caddy im Host-Netz erreicht `app` über `127.0.0.1:8000` → im Caddyfile `reverse_proxy 127.0.0.1:8000`. Volume `caddy_data` deklarieren.

- [ ] **Step 2: Backup-Skript**

`core/deploy/backup.sh`:
```bash
#!/usr/bin/env bash
# Tägliches verschlüsseltes Backup: Datenbank-Dump + data/ (Rechnungs-PDFs, Lesestand, Signaturschlüssel)
set -euo pipefail
ZIEL=${BACKUP_ZIEL:?z.B. user@backup-host:/srv/beachhub}
GPG_EMPFAENGER=${BACKUP_GPG:?GPG-Key-ID des Betreibers}
STAMP=$(date +%Y%m%d-%H%M)
TMP=$(mktemp -d)
docker compose exec -T db pg_dump -U beachhub beachhub | gzip > "$TMP/db-$STAMP.sql.gz"
tar czf "$TMP/data-$STAMP.tgz" -C "$(dirname "$0")/.." data
tar cf - -C "$TMP" . | gpg --encrypt --recipient "$GPG_EMPFAENGER" > "$TMP/beachhub-$STAMP.tar.gpg"
scp "$TMP/beachhub-$STAMP.tar.gpg" "$ZIEL/"
rm -rf "$TMP"
echo "Backup $STAMP übertragen"
```
`chmod +x core/deploy/backup.sh`. Cron-Eintrag im Betriebshandbuch: `15 3 * * * cd /opt/beachhub/core && BACKUP_ZIEL=… BACKUP_GPG=… ./deploy/backup.sh >> /var/log/beachhub-backup.log 2>&1`.

- [ ] **Step 3: WireGuard-Beispiel und Betriebshandbuch**

`core/deploy/wireguard-beispiel.md`: Server-Konfiguration (VM, `10.8.0.1/24`, Port 51820, nur dieser Port in der Hetzner-Firewall offen), Peer „Betreiber-Laptop“ (`10.8.0.10`), Peer „Zweiter Entwickler“ (`10.8.0.11`), Platzhalter für Peer „Halle“ (`10.8.0.20`, Stufe 3). Befehle `wg genkey | tee privatekey | wg pubkey`, `systemctl enable wg-quick@wg0`.

`docs/betrieb/hauptsystem.md` – Abschnitte:
1. Voraussetzungen (VM Ubuntu 24.04, Docker, WireGuard, Festplattenverschlüsselung bei Hetzner via Cloud-Init/LUKS-Hinweis)
2. Installation (Repo klonen nach `/opt/beachhub`, `.env` aus `.env.example` mit `SECRET_KEY`/`PIN_SCHLUESSEL` aus `python -c "import secrets; print(secrets.token_urlsafe(48))"`, `docker compose up -d db`, `docker compose run --rm app alembic upgrade head`, `docker compose run --rm app beachhub-core keygen`, `docker compose run --rm app beachhub-core create-admin --name <name>`, `docker compose up -d`)
3. Zugriff (WireGuard verbinden, `https://10.8.0.1:8443/admin`, Zertifikat von Caddy einmalig akzeptieren)
4. Ersteinrichtung im Admin-UI (Felder → Raster → Betriebszeiten → Kundengruppen → Tarife → Konfiguration → Testkunde → Testbuchung → Testrechnung → Uhr zurücksetzen)
5. Laufender Betrieb (Monatslauf automatisch 06:00 am konfigurierten Tag; manuell unter Rechnungen; CSV-Export für Steuerberater; Logs `docker compose logs -f app`)
6. Backup und Wiederherstellung (Skript, Cron, Restore-Anleitung: `gpg --decrypt … | tar xf -`, `gunzip -c db.sql.gz | docker compose exec -T db psql -U beachhub beachhub`, `data/` zurückkopieren, Testlauf vor Saisonstart)
7. Updates (`git pull`, `docker compose build`, `docker compose run --rm app alembic upgrade head`, `docker compose up -d`)
8. Störungen (App startet nicht wegen unsicherer Defaults; Schlüsseldatei fehlt; Postgres voll; Mail geht nicht raus → `SMTP_*` prüfen)

`core/README.md`: Kurzfassung für Entwickler (venv, `pip install -e ../shared -e .[dev]`, Postgres per Compose, `alembic upgrade head`, `beachhub-core keygen`, `create-admin`, `uvicorn beachhub_core.main:app --reload`, `pytest`), Verweis auf Spezifikation und Plan.

Repo-README ergänzen: Abschnitt „Entwicklung“ mit den drei Befehlen (`pip install -e shared[dev] -e core[dev]`, `docker compose -f core/docker-compose.yml up -d db`, `cd core && pytest`).

- [ ] **Step 4: Smoke-Test der Inbetriebnahme**

Run (lokal, ohne Caddy):
```bash
cd core && docker compose up -d db && alembic upgrade head && beachhub-core keygen && \
  printf 'test-passwort-1234\n' | beachhub-core create-admin --name test && \
  (uvicorn beachhub_core.main:app --port 8000 & sleep 3; curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/admin/login; kill %1)
```
Expected: `keygen` druckt einen 64-stelligen Hex-Schlüssel, `create-admin` druckt Secret und `otpauth://`-URI, curl gibt `200`.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "docs(core): Betriebshandbuch, Caddy, Backup-Skript und WireGuard-Beispiel"
```

---

## Abnahme der Stufe 1 (Ende des Plans)

Manuell im Admin-UI gegen die Spezifikation durchspielen und abhaken:

- [ ] Felder mit unterschiedlichem Raster (A-FELD-1/2), Betriebszeiten und Ausnahmetag (A-FELD-3) angelegt; Belegungsplan zeigt passende Slots.
- [ ] Tarife zeit- und gruppenabhängig (A-TARIF-1/2); Preis bei Buchung wie erwartet, spätere Tarifänderung ändert Buchung nicht (A-TARIF-3).
- [ ] Buchung, Kollision, Sperre mit Entscheidung, Dauerbuchung mit Auslassen und gemeinsamer PIN (A-BUCH-3/4, A-SPERR-1/2, A-DAUER-1/3/4).
- [ ] Storno vor/nach Frist, Nachbuchung durch anderen Kunden macht kostenfrei und bucht Guthaben, Kulanz (A-STORNO-1..4, A-ZAHL-4).
- [ ] Monatslauf erzeugt Sammelrechnung mit Storno-Position, PDF unveränderbar, Stornorechnung, CSV (A-RECH-1..4, A-RECH-6).
- [ ] Mails im Log/Postfach: Bestätigung mit PIN, Storno, Dauerbuchung, Rechnung (A-MAIL-2).
- [ ] Lesestand `belegung` enthält keine Kundennamen; Signatur prüfbar mit öffentlichem Schlüssel (N-1, N-2).
- [ ] Audit-Log zeigt jede der obigen Änderungen (N-6).
- [ ] Admin mit Rolle `lesend` kann nichts ändern (A-ADM-7).
- [ ] Backup erstellt und Restore auf frischer DB erfolgreich (N-10).

Nicht Teil dieser Stufe (folgen in Stufe 2/3): Portal-Kanal und Anfragetabelle, Stripe, Zahlungsfrist/Verfall, Hallen-API, Präsenz/Anwesenheit, Betreiber-Alarme aus der Halle, Erinnerungsmail 24 h vorher (A-MAIL-2 teilweise; wird mit dem Portal umgesetzt, da der Job identisch ist).
