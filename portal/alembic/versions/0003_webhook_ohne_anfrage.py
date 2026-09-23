"""webhook_ohne_anfrage

`webhook_eingang.anfrage_id` wird nullable: Eine Rückmeldung, deren Nutzlast das Kanal-Schema
verletzt (z. B. ein überlanger Signatur-Header), wird nur noch als `webhook_eingang` aufbewahrt,
ohne begleitende Anfrage (Ruling Fix-Runde 1, Task 13) – statt mit einem 500 abzubrechen.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-23

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = "spiegel"


def upgrade() -> None:
    op.alter_column("webhook_eingang", "anfrage_id", nullable=True, schema=S)


def downgrade() -> None:
    op.alter_column("webhook_eingang", "anfrage_id", nullable=False, schema=S)
