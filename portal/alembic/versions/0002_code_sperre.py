"""code_sperre

Fehlversuche beim Code-Login je Adresse, unabhängig von einzelnen Login-Tokens (die bei jeder
neuen Anforderung verworfen werden): Obergrenze 20 Fehlversuche je Adresse in 24 Stunden,
dauerhaft in der Datenbank, damit ein Neustart des Portals sie nicht zurücksetzt (Ruling
Fix-Runde 1, Task 10).

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = "spiegel"


def upgrade() -> None:
    op.create_table(
        "code_fehlversuch",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=200), nullable=False),
        sa.Column("versucht_am", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema=S,
    )
    op.create_index("ix_spiegel_code_fehlversuch_email", "code_fehlversuch", ["email"], schema=S)


def downgrade() -> None:
    op.drop_table("code_fehlversuch", schema=S)
