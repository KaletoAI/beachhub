"""halle: Ereignisse und Status des Hallendienstes

`halle_dienst` hält je Dienst-ID die zuletzt lückenlos bestätigte seq – getrennt von
`ereignis`, damit das spätere Aufräumen alter Ereignisse (90 Tage, Spec § 10) die Zählung nicht
zurückwirft, und als Sperrzeile für parallele Lieferungen derselben Dienst-ID.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ereignis",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("quelle", sa.String(length=10), nullable=False),
        sa.Column("typ", sa.String(length=40), nullable=False),
        sa.Column("zeitpunkt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("feld_id", sa.UUID(), nullable=True),
        sa.Column("buchung_id", sa.UUID(), nullable=True),
        sa.Column("daten_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("halle_dienst_id", sa.UUID(), nullable=True),
        sa.Column("halle_seq", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("halle_dienst_id", "halle_seq", name="ereignis_halle_seq_eindeutig"),
    )
    op.create_index("ix_ereignis_typ", "ereignis", ["typ"])
    op.create_index("ix_ereignis_zeitpunkt", "ereignis", ["zeitpunkt"])
    op.create_table(
        "hallen_status",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("daten_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("empfangen_am", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "halle_dienst",
        sa.Column("dienst_id", sa.UUID(), nullable=False),
        sa.Column("bestaetigt_bis", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("dienst_id"),
    )


def downgrade() -> None:
    op.drop_table("halle_dienst")
    op.drop_table("hallen_status")
    op.drop_index("ix_ereignis_zeitpunkt", table_name="ereignis")
    op.drop_index("ix_ereignis_typ", table_name="ereignis")
    op.drop_table("ereignis")
