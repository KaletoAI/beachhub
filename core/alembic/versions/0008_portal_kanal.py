"""portal kanal

Tabellen für den Kanal zum Portal: verarbeitete Anfragen (Idempotenz) und Zahlungen des
Online-Zahlungsdienstes.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "anfrage_verarbeitet",
        sa.Column("anfrage_id", sa.UUID(), nullable=False),
        sa.Column("typ", sa.String(length=40), nullable=False),
        sa.Column("antwort_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("verarbeitet_am", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("anfrage_id"),
    )
    op.create_table(
        "zahlung",
        sa.Column("kunde_id", sa.UUID(), nullable=False),
        sa.Column("buchung_id", sa.UUID(), nullable=True),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("provider_ref", sa.String(length=200), nullable=False),
        sa.Column("betrag", sa.DECIMAL(precision=10, scale=2), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("empfangen_am", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rohdaten_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("checkout_url", sa.String(length=1000), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["buchung_id"], ["buchung.id"]),
        sa.ForeignKeyConstraint(["kunde_id"], ["kunde.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_ref"),
    )
    op.create_index("ix_zahlung_buchung_id", "zahlung", ["buchung_id"])


def downgrade() -> None:
    op.drop_index("ix_zahlung_buchung_id", table_name="zahlung")
    op.drop_table("zahlung")
    op.drop_table("anfrage_verarbeitet")
