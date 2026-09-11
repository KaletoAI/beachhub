"""geraetezuordnung entfernen

Welches Gerät zu welchem Feld gehört, gehört in das Addon auf dem Home-Assistant-Server.
Das Hauptsystem liefert nur den Belegungsplan je Feld; die Zuordnung von Lampe, Sensor und
Heizzone pflegt der Hallendienst vor Ort.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("feld", "ha_licht_entity")
    op.drop_column("feld", "ha_praesenz_entity")
    op.drop_column("feld", "heizzone")


def downgrade() -> None:
    op.add_column("feld", sa.Column("ha_licht_entity", sa.String(length=200), nullable=True))
    op.add_column("feld", sa.Column("ha_praesenz_entity", sa.String(length=200), nullable=True))
    op.add_column("feld", sa.Column("heizzone", sa.String(length=100), nullable=True))
