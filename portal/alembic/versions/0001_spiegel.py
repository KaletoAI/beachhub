"""spiegel

Schema des Portals: Konten, Login-Codes, Sessions, Anfragetabelle, Lesestand,
Rechnungs-Einmal-Links, Briefkasten für Zahlungsrückmeldungen, Kontakt zum Hauptsystem.

lesestand.version ist BIGINT: Das Hauptsystem vergibt Versionen als Unixzeit in
Millisekunden (Ruling Lesestand-Versionen), das übersteigt schnell den 32-Bit-Bereich.

Revision ID: 0001
Revises:
Create Date: 2026-09-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = "spiegel"


def upgrade() -> None:
    op.create_table(
        "konto",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=200), nullable=False),
        sa.Column("anzeigename", sa.String(length=100), nullable=False),
        sa.Column("kunde_id", sa.UUID(), nullable=True),
        sa.Column("erstellt_am", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        schema=S,
    )
    op.create_table(
        "login_token",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=200), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("fehlversuche", sa.Integer(), nullable=False),
        sa.Column("laeuft_ab", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verwendet_am", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
        schema=S,
    )
    op.create_index("ix_spiegel_login_token_email", "login_token", ["email"], schema=S)
    op.create_table(
        "session",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("konto_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token", sa.String(length=64), nullable=False),
        sa.Column("laeuft_ab", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["konto_id"], [f"{S}.konto.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
        schema=S,
    )
    op.create_table(
        "anfrage",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("typ", sa.String(length=40), nullable=False),
        sa.Column("konto_id", sa.UUID(), nullable=True),
        sa.Column("nutzlast_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("erstellt_am", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("abgeholt_am", sa.DateTime(timezone=True), nullable=True),
        sa.Column("antwort_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("beantwortet_am", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema=S,
    )
    op.create_index("ix_anfrage_status_erstellt", "anfrage", ["status", "erstellt_am"], schema=S)
    op.create_table(
        "lesestand",
        sa.Column("dokument", sa.String(length=80), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("erzeugt_am", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signatur", sa.String(length=200), nullable=False),
        sa.Column("inhalt_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("empfangen_am", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("dokument"),
        schema=S,
    )
    op.create_table(
        "rechnung_link",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("konto_id", sa.UUID(), nullable=False),
        sa.Column("rechnung_nr", sa.String(length=20), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("pdf_pfad", sa.String(length=300), nullable=False),
        sa.Column("laeuft_ab", sa.DateTime(timezone=True), nullable=False),
        sa.Column("abgerufen_am", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["konto_id"], [f"{S}.konto.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
        schema=S,
    )
    op.create_table(
        "webhook_eingang",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("rohdaten", sa.Text(), nullable=False),
        sa.Column("signatur_header", sa.Text(), nullable=True),
        sa.Column("empfangen_am", sa.DateTime(timezone=True), nullable=False),
        sa.Column("anfrage_id", sa.UUID(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema=S,
    )
    op.create_table(
        "kanal_kontakt",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("letzter_abruf", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema=S,
    )


def downgrade() -> None:
    for tabelle in (
        "kanal_kontakt",
        "webhook_eingang",
        "rechnung_link",
        "lesestand",
        "anfrage",
        "session",
        "login_token",
        "konto",
    ):
        op.drop_table(tabelle, schema=S)
