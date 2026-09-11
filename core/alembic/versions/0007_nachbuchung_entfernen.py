"""nachbuchung entfernen

Der Betreiber hat die Nachbuchungsregel gestrichen: Ein nach der Frist storniertes
Entgelt bleibt fällig, auch wenn ein anderer Kunde den Platz anschließend bucht.
Damit entfallen die drei Spalten, die es nur wegen dieser Regel gab –
`freigestellt_betrag` diente der anteiligen Freistellung bei Teil-Nachbuchungen.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("storno", "nachbuchung_offen")
    op.drop_column("storno", "nachbuchung_buchung_id")
    op.drop_column("storno", "freigestellt_betrag")


def downgrade() -> None:
    op.add_column(
        "storno",
        sa.Column("nachbuchung_offen", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("storno", sa.Column("nachbuchung_buchung_id", sa.UUID(), nullable=True))
    op.add_column(
        "storno",
        sa.Column(
            "freigestellt_betrag",
            sa.DECIMAL(precision=10, scale=2),
            nullable=False,
            server_default="0.00",
        ),
    )
    op.create_foreign_key(
        "storno_nachbuchung_buchung_id_fkey",
        "storno",
        "buchung",
        ["nachbuchung_buchung_id"],
        ["id"],
    )
