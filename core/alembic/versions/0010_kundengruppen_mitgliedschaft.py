"""kundengruppen_mitgliedschaft: zwei feste Kundengruppen mit Steuersatz, Rechnungskunde und
Mitgliedschaft am Kunden, Gruppe und Steuersatz an der Buchung (Stufe 1a, A-KUND-1 bis -7).

Vorhandene Kundengruppen werden verworfen – bis Mitte 2027 gibt es keine produktiven Daten
(Projektentscheidung 24.09.2026): Tarife verlieren ihren Gruppenbezug, Buchungen kommen in die
Gruppe „Nicht-Mitglied“, Termine von Dauerbuchungen bekommen die Zahlungsart `saison`, übrige
Rechnungsbuchungen `manuell`.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _mitglied_spalten() -> list[sa.Column]:
    # Je Aufruf neue Column-Objekte: Alembic hängt eine Spalte an ihre Tabelle.
    return [
        sa.Column("mitglied_bis", sa.Date(), nullable=True),
        sa.Column("mitglied_antrag_am", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "mitglied_antrag_hinweis", sa.String(length=500), server_default="", nullable=False
        ),
        sa.Column("mitglied_freigeschaltet_am", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mitglied_freigeschaltet_von", sa.UUID(), nullable=True),
        sa.Column("mitglied_beendet_am", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "mitglied_beendet_grund", sa.String(length=300), server_default="", nullable=False
        ),
    ]


def upgrade() -> None:
    # Kunden und Tarife lösen sich von den alten Gruppen, bevor diese verschwinden.
    op.drop_column("kunde", "kundengruppe_id")
    op.drop_column("kunde", "zahlungsart")
    op.execute("UPDATE tarif SET kundengruppe_id = NULL")
    op.execute("DELETE FROM kundengruppe")
    op.drop_column("kundengruppe", "standard_zahlungsart")
    op.add_column(
        "kundengruppe", sa.Column("ust_satz", sa.DECIMAL(precision=5, scale=2), nullable=False)
    )
    op.add_column("kundengruppe", sa.Column("ist_mitglied", sa.Boolean(), nullable=False))
    op.create_unique_constraint("kundengruppe_ist_mitglied_key", "kundengruppe", ["ist_mitglied"])
    op.execute(
        "INSERT INTO kundengruppe (id, name, ust_satz, ist_mitglied, created_at, updated_at) "
        "VALUES (gen_random_uuid(), 'DJK-Mitglied', 7.00, true, now(), now()), "
        "(gen_random_uuid(), 'Nicht-Mitglied', 19.00, false, now(), now())"
    )

    op.add_column(
        "kunde",
        sa.Column("rechnungskunde", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.alter_column("kunde", "rechnungskunde", server_default=None)
    for spalte in _mitglied_spalten():
        op.add_column("kunde", spalte)
    op.alter_column("kunde", "mitglied_antrag_hinweis", server_default=None)
    op.alter_column("kunde", "mitglied_beendet_grund", server_default=None)

    # Buchungen: Gruppe und Satz festschreiben, Zahlungsarten auf die neue Bedeutung bringen.
    op.add_column("buchung", sa.Column("kundengruppe_id", sa.UUID(), nullable=True))
    op.add_column("buchung", sa.Column("ust_satz", sa.DECIMAL(precision=5, scale=2), nullable=True))
    op.execute(
        "UPDATE buchung SET ust_satz = 19.00, "
        "kundengruppe_id = (SELECT id FROM kundengruppe WHERE NOT ist_mitglied)"
    )
    op.alter_column("buchung", "kundengruppe_id", nullable=False)
    op.alter_column("buchung", "ust_satz", nullable=False)
    op.create_foreign_key(
        "buchung_kundengruppe_id_fkey", "buchung", "kundengruppe", ["kundengruppe_id"], ["id"]
    )
    op.execute("UPDATE buchung SET zahlungsart = 'saison' WHERE dauerbuchung_id IS NOT NULL")
    op.execute("UPDATE buchung SET zahlungsart = 'manuell' WHERE zahlungsart = 'rechnung'")
    op.execute("DELETE FROM konfiguration WHERE schluessel = 'portal_kundengruppe'")


def downgrade() -> None:
    op.drop_constraint("buchung_kundengruppe_id_fkey", "buchung", type_="foreignkey")
    op.drop_column("buchung", "ust_satz")
    op.drop_column("buchung", "kundengruppe_id")
    op.execute(
        "UPDATE buchung SET zahlungsart = 'rechnung' WHERE zahlungsart IN ('saison', 'manuell')"
    )
    for spalte in reversed(_mitglied_spalten()):
        op.drop_column("kunde", spalte.name)
    op.drop_column("kunde", "rechnungskunde")

    op.drop_constraint("kundengruppe_ist_mitglied_key", "kundengruppe", type_="unique")
    op.drop_column("kundengruppe", "ist_mitglied")
    op.drop_column("kundengruppe", "ust_satz")
    op.add_column(
        "kundengruppe",
        sa.Column(
            "standard_zahlungsart", sa.String(length=10), server_default="online", nullable=False
        ),
    )
    op.alter_column("kundengruppe", "standard_zahlungsart", server_default=None)
    op.add_column(
        "kunde",
        sa.Column("zahlungsart", sa.String(length=10), server_default="online", nullable=False),
    )
    op.alter_column("kunde", "zahlungsart", server_default=None)
    op.add_column("kunde", sa.Column("kundengruppe_id", sa.UUID(), nullable=True))
    op.execute(
        "UPDATE kunde SET kundengruppe_id = (SELECT id FROM kundengruppe ORDER BY name LIMIT 1)"
    )
    op.alter_column("kunde", "kundengruppe_id", nullable=False)
    op.create_foreign_key(
        "kunde_kundengruppe_id_fkey", "kunde", "kundengruppe", ["kundengruppe_id"], ["id"]
    )
