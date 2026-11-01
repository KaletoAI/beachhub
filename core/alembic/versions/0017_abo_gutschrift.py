"""abo_gutschrift: Abo-Absagen werden Guthaben statt Korrekturbeleg, kein Absagekontingent mehr.

- `storno.abo_gutschrift` hält den Betrag, der für einen während des Abos kostenfrei gewordenen
  Abo-Termin ohne Korrekturbeleg als Guthaben gutgeschrieben wurde. Die Saisonrechnung bleibt
  unverändert.
- Die Einstellung `abo_freie_absagen` entfällt: Termine, die ein Abo-Kunde nicht nimmt, werden
  schon beim Anlegen ausgelassen. Ihre gespeicherte Zeile wird entfernt.
- Konto-Dokumente aller Abo-Kunden werden zur Neuveröffentlichung markiert, damit das Portal
  kein Absagekontingent mehr anzeigt.

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-01

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("storno", sa.Column("abo_gutschrift", sa.DECIMAL(10, 2), nullable=True))
    op.execute("DELETE FROM konfiguration WHERE schluessel = 'abo_freie_absagen'")
    op.execute(
        "INSERT INTO lesestand_version (dokument, version, geaendert) "
        "SELECT DISTINCT 'konto:' || k.id, 0, true FROM kunde k "
        "JOIN dauerbuchung d ON d.kunde_id = k.id WHERE k.anonymisiert_am IS NULL "
        "ON CONFLICT (dokument) DO UPDATE SET geaendert = true"
    )


def downgrade() -> None:
    conn = op.get_bind()
    if conn.scalar(sa.text("SELECT count(*) FROM storno WHERE abo_gutschrift IS NOT NULL")):
        # Ohne die Spalte gälte ein gutgeschriebener Termin als kostenfrei abgesagt: Eine
        # Neuausstellung ließe ihn aus, obwohl sein Betrag bereits Guthaben ist.
        raise RuntimeError("Downgrade 0017 verweigert: Es gibt bereits Abo-Gutschriften.")
    op.drop_column("storno", "abo_gutschrift")
