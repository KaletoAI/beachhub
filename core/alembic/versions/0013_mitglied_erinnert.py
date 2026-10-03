"""mitglied_erinnert: merkt, an welchen Ablauf der Mitgliedschaft zuletzt erinnert wurde
(A-MAIL-2).

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("kunde", sa.Column("mitglied_erinnert_fuer", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("kunde", "mitglied_erinnert_fuer")
