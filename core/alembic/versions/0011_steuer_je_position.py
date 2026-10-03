"""steuer_je_position: Netto und Steuer je Rechnungsposition, kein Satz am Rechnungskopf
(A-RECH-8). Die Einstellung ust_satz entfällt; jeder Satz kommt aus der Buchung.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for spalte in ("netto", "ust"):
        op.add_column(
            "rechnung_position",
            sa.Column(
                spalte, sa.DECIMAL(precision=10, scale=2), server_default="0", nullable=False
            ),
        )
    # round() auf numeric rundet in PostgreSQL kaufmännisch – wie ROUND_HALF_UP im Code.
    op.execute("UPDATE rechnung_position SET netto = round(brutto / (1 + ust_satz / 100), 2)")
    op.execute("UPDATE rechnung_position SET ust = brutto - netto")
    for spalte in ("netto", "ust"):
        op.alter_column("rechnung_position", spalte, server_default=None)
    op.drop_column("rechnung", "ust_satz")
    op.execute("DELETE FROM konfiguration WHERE schluessel = 'ust_satz'")


def downgrade() -> None:
    op.add_column(
        "rechnung",
        sa.Column(
            "ust_satz", sa.DECIMAL(precision=5, scale=2), server_default="19.00", nullable=False
        ),
    )
    op.execute(
        "UPDATE rechnung r SET ust_satz = p.satz FROM (SELECT rechnung_id, max(ust_satz) AS satz "
        "FROM rechnung_position GROUP BY rechnung_id) p WHERE p.rechnung_id = r.id"
    )
    op.alter_column("rechnung", "ust_satz", server_default=None)
    op.drop_column("rechnung_position", "ust")
    op.drop_column("rechnung_position", "netto")
