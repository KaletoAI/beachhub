"""saison_und_korrektur: Saisonrechnung je Dauerbuchung, (Teil-)Stornorechnungen mit Bezug auf
die korrigierte Rechnung und Position, freie Abo-Absagen, Guthabenverrechnung auf Rechnungen
(Stufe 1a-II). Der Monatslauf entfällt: Marker und Einstellung werden gelöscht.

Enthält alle Schemaänderungen von Plan 1a-II.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (Tabelle, Spalte, Zieltabelle) – jeweils UUID, NULL erlaubt, Fremdschlüssel auf <Ziel>.id
_VERWEISE = (
    ("rechnung", "dauerbuchung_id", "dauerbuchung"),
    ("rechnung", "korrigiert_rechnung_id", "rechnung"),
    ("rechnung_position", "korrigiert_durch_id", "rechnung_position"),
    ("storno", "korrektur_rechnung_id", "rechnung"),
    ("zahlung", "rechnung_id", "rechnung"),
)


def upgrade() -> None:
    for tabelle, spalte, ziel in _VERWEISE:
        op.add_column(tabelle, sa.Column(spalte, sa.UUID(), nullable=True))
        op.create_foreign_key(f"{tabelle}_{spalte}_fkey", tabelle, ziel, [spalte], ["id"])
    op.create_index("ix_zahlung_rechnung_id", "zahlung", ["rechnung_id"])
    op.add_column(
        "storno",
        sa.Column("freie_absage", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.alter_column("storno", "freie_absage", server_default=None)
    # Bestehende Stornorechnungen verweisen künftig auf die Rechnung, die sie aufheben.
    op.execute(
        "UPDATE rechnung s SET korrigiert_rechnung_id = r.id "
        "FROM rechnung r WHERE r.storniert_durch_id = s.id"
    )
    op.execute("DELETE FROM app_setting WHERE key = 'monatslauf_letzter'")
    op.execute("DELETE FROM konfiguration WHERE schluessel = 'rechnung_tag_im_folgemonat'")


def downgrade() -> None:
    op.drop_column("storno", "freie_absage")
    op.drop_index("ix_zahlung_rechnung_id", table_name="zahlung")
    for tabelle, spalte, _ in reversed(_VERWEISE):
        op.drop_constraint(f"{tabelle}_{spalte}_fkey", tabelle, type_="foreignkey")
        op.drop_column(tabelle, spalte)
