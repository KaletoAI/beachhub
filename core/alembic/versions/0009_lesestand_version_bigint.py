"""lesestand version bigint

lesestand_version.version wird als max(bisherige Version + 1, Unixzeit in Millisekunden)
vergeben (Ruling Lesestand-Versionen) und übersteigt damit einen 32-Bit-INTEGER.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "lesestand_version",
        "version",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "lesestand_version",
        "version",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
    )
