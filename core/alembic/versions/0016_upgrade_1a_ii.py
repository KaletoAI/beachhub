"""upgrade_1a_ii: Nachzügler zum Upgrade auf Stufe 1a-II, ohne Schemaänderung.

- Konto-Dokumente aller Kunden mit Abo und alle bereits veröffentlichten Konto-Dokumente werden
  zur Neuveröffentlichung markiert. Erst damit tragen sie `abo`, `freie_absagen_rest` und
  `stornierbar` für Abo-Termine.
- Liegt der Stichtag `saisonende_guthabenliste` im laufenden Jahr schon zurück, gilt die
  Saisonende-Erinnerung dieses Jahres als erledigt. Sonst käme direkt nach dem Upgrade mitten
  in der Saison die Mail „Die Saison ist zu Ende“. Die Guthabenliste bleibt jederzeit abrufbar.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-15

"""

import re
from collections.abc import Sequence
from datetime import date, datetime
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SAISONENDE_MARKER = "guthabenliste_letzter"


def _heute(conn: sa.Connection) -> date:
    """Fachliches Datum wie `clock.today`: Admin-Override, sonst Europe/Berlin."""
    override = conn.scalar(sa.text("SELECT value FROM app_setting WHERE key = 'datum_override'"))
    if override:
        return date.fromisoformat(override)
    return datetime.now(ZoneInfo("Europe/Berlin")).date()


def _stichtag(conn: sa.Connection, jahr: int) -> date:
    wert = conn.scalar(
        sa.text("SELECT wert FROM konfiguration WHERE schluessel = 'saisonende_guthabenliste'")
    )
    m = re.fullmatch(r"\s*(\d{1,2})\.(\d{1,2})\.?\s*", wert or "30.04.")
    tag, monat = (int(m.group(1)), int(m.group(2))) if m else (30, 4)
    return date(jahr, monat, tag)


def upgrade() -> None:
    op.execute("UPDATE lesestand_version SET geaendert = true WHERE dokument LIKE 'konto:%'")
    op.execute(
        "INSERT INTO lesestand_version (dokument, version, geaendert) "
        "SELECT DISTINCT 'konto:' || k.id, 0, true FROM kunde k "
        "JOIN dauerbuchung d ON d.kunde_id = k.id WHERE k.anonymisiert_am IS NULL "
        "ON CONFLICT (dokument) DO UPDATE SET geaendert = true"
    )
    conn = op.get_bind()
    heute = _heute(conn)
    if heute >= _stichtag(conn, heute.year):
        conn.execute(
            sa.text(
                "INSERT INTO app_setting (key, value) VALUES (:key, :jahr) "
                "ON CONFLICT (key) DO NOTHING"
            ),
            {"key": SAISONENDE_MARKER, "jahr": str(heute.year)},
        )


def downgrade() -> None:
    # Reine Datenmigration: Markierungen und Marker sind auch unter 0015 gültig.
    pass
