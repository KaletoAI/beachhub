"""Portal und Hauptsystem im selben Prozess, jedes mit eigener Test-Datenbank.

Beide lesen ihre Einstellungen beim Import. Das Portal nutzt das Präfix PORTAL_, deshalb
stören sich die Variablen nicht. Der Signaturschlüssel des Hauptsystems entsteht vor dem Import,
damit das Portal den passenden öffentlichen Schlüssel bekommt.
"""

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

from beachhub_shared.signatur import erzeuge_schluesselpaar

KANAL_TOKEN = "e2e-kanal-token"
_tmp = Path(tempfile.mkdtemp(prefix="beachhub-e2e-"))
(_tmp / "core").mkdir()
_privat, _oeffentlich = erzeuge_schluesselpaar()
(_tmp / "core" / "signatur.key").write_text(_privat + "\n", encoding="utf-8")

os.environ.update(
    {
        "DATABASE_URL": os.environ.get(
            "TEST_DATABASE_URL",
            "postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub_test",
        ),
        "DATA_DIR": str(_tmp / "core"),
        "SIGNATUR_PRIVATSCHLUESSEL_PFAD": str(_tmp / "core" / "signatur.key"),
        "SECRET_KEY": "e2e-secret-key-0123456789abcdef",
        "PIN_SCHLUESSEL": "ZTJlLXBpbi1zY2hsdWVzc2VsLTMyLWJ5dGVzLSEhIQ==",
        "APP_ENV": "dev",
        "SMTP_HOST": "",
        "ENABLE_SCHEDULER": "false",
        "ENABLE_KANAL": "false",
        "PORTAL_URL": "",
        "ZAHLUNG_PROVIDER": "fake",
        # K3: die Rückkehradresse nach der Zahlung bildet das Hauptsystem aus dieser Adresse
        # (rueckkehr_url = portal_oeffentliche_url + /zahlung/zurueck?anfrage=…), nicht aus
        # portal_url (die bleibt die mTLS-Kanaladresse und ist für den Kunden-Browser untauglich).
        "PORTAL_OEFFENTLICHE_URL": "http://testserver",
        "PORTAL_DATABASE_URL": os.environ.get(
            "TEST_PORTAL_DATABASE_URL",
            "postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub_portal_test",
        ),
        "PORTAL_DATA_DIR": str(_tmp / "portal"),
        "PORTAL_SECRET_KEY": "e2e-portal-secret-0123456789abcdef",
        "PORTAL_APP_ENV": "dev",
        "PORTAL_COOKIE_SECURE": "false",
        "PORTAL_SMTP_HOST": "",
        "PORTAL_ENABLE_SCHEDULER": "false",
        "PORTAL_KANAL_TOKEN": KANAL_TOKEN,
        "PORTAL_CORE_PUBLIC_KEY": _oeffentlich,
        "PORTAL_FAKE_ZAHLUNG": "true",
        "PORTAL_BASE_URL": "http://testserver",
    }
)

import pytest  # noqa: E402
from beachhub_core import mail as core_mail  # noqa: E402
from beachhub_core.database import engine as core_engine  # noqa: E402
from beachhub_core.database import stelle_extensions_sicher  # noqa: E402
from beachhub_core.kanal import Kanal  # noqa: E402
from beachhub_core.models import Base as CoreBase  # noqa: E402
from beachhub_portal import auth as portal_auth  # noqa: E402
from beachhub_portal import mail as portal_mail  # noqa: E402
from beachhub_portal.database import engine as portal_engine  # noqa: E402
from beachhub_portal.database import stelle_schema_sicher  # noqa: E402
from beachhub_portal.main import app as portal_app  # noqa: E402
from beachhub_portal.models import Base as PortalBase  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402


@pytest.fixture(autouse=True)
def frische_datenbanken() -> Iterator[None]:
    for ordner in ("core/lesestand", "core/rechnungen", "portal/rechnungen_tmp"):
        shutil.rmtree(_tmp / ordner, ignore_errors=True)
    stelle_extensions_sicher(core_engine)
    with core_engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    CoreBase.metadata.drop_all(bind=core_engine)
    CoreBase.metadata.create_all(bind=core_engine)
    stelle_schema_sicher(portal_engine)
    with portal_engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS spiegel.alembic_version"))
    PortalBase.metadata.drop_all(bind=portal_engine)
    PortalBase.metadata.create_all(bind=portal_engine)
    yield


@pytest.fixture(autouse=True)
def mails(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    postfach: dict[str, list] = {"core": [], "portal": []}
    monkeypatch.setattr(core_mail, "TEST_AUSGANG", postfach["core"])
    monkeypatch.setattr(portal_mail, "TEST_AUSGANG", postfach["portal"])
    portal_auth.reset_rate_limits()
    return postfach


@pytest.fixture
def browser() -> TestClient:
    return TestClient(portal_app)


@pytest.fixture
def hauptsystem() -> Kanal:
    client = TestClient(portal_app, headers={"Authorization": f"Bearer {KANAL_TOKEN}"})
    return Kanal(client, warten=0)
