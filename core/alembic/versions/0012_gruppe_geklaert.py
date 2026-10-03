"""gruppe_geklaert: Buchung als geklärt markieren, wenn die Mitgliedschaft vor dem Termin endete
(Klärungsliste, A-KUND-4).

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "buchung", sa.Column("gruppe_geklaert_am", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("buchung", "gruppe_geklaert_am")
