"""Gutschein-Snapshots, Einlösungen und getrennte Belegnummernkreise (Stufe 1a-III)."""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "nummernkreis", sa.Column("kreis", sa.String(20), nullable=False, server_default="rechnung")
    )
    op.alter_column("nummernkreis", "kreis", server_default=None)
    op.drop_constraint("nummernkreis_pkey", "nummernkreis", type_="primary")
    op.create_primary_key("nummernkreis_pkey", "nummernkreis", ["kreis", "jahr"])
    op.create_check_constraint(
        "ck_nummernkreis_kreis", "nummernkreis", "kreis IN ('rechnung', 'gutschein')"
    )
    op.create_check_constraint("ck_nummernkreis_nummer", "nummernkreis", "letzte_nummer >= 0")
    op.alter_column("rechnung", "nummer", type_=sa.String(20), existing_type=sa.String(12))
    op.add_column("rechnung", sa.Column("gutschein_rolle", sa.String(12), nullable=True))
    op.create_check_constraint(
        "ck_rechnung_gutschein_rolle",
        "rechnung",
        "gutschein_rolle IN ('kauf', 'einloesung', 'geldanteil')",
    )
    op.create_table(
        "gutschein",
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("art", sa.String(length=4), nullable=False),
        sa.Column("modell", sa.String(length=7), nullable=False),
        sa.Column("steuer", sa.String(length=14), nullable=False),
        sa.Column("minuten", sa.Integer(), nullable=True),
        sa.Column("nennwert_brutto", sa.DECIMAL(precision=10, scale=2), nullable=True),
        sa.Column("restwert", sa.DECIMAL(precision=10, scale=2), nullable=True),
        sa.Column("kaufpreis_brutto", sa.DECIMAL(precision=10, scale=2), nullable=True),
        sa.Column("kundengruppe_id", sa.UUID(), nullable=True),
        sa.Column("ust_satz", sa.DECIMAL(precision=5, scale=2), nullable=True),
        sa.Column("nur_mitglieder", sa.Boolean(), nullable=False),
        sa.Column("feld_id", sa.UUID(), nullable=True),
        sa.Column("zeitraum_von", sa.Date(), nullable=True),
        sa.Column("zeitraum_bis", sa.Date(), nullable=True),
        sa.Column("max_einloesungen", sa.Integer(), nullable=False),
        sa.Column("kaeufer_kunde_id", sa.UUID(), nullable=True),
        sa.Column("inhaber_kunde_id", sa.UUID(), nullable=True),
        sa.Column("zahlung_id", sa.UUID(), nullable=True),
        sa.Column("rechnung_id", sa.UUID(), nullable=True),
        sa.Column("ausgestellt_von_admin_id", sa.UUID(), nullable=True),
        sa.Column("ausgestellt_am", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kaufdatum", sa.Date(), nullable=True),
        sa.Column("kauf_reserviert_bis", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gueltig_tage_snapshot", sa.Integer(), nullable=True),
        sa.Column("gueltig_bis", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("empfaenger_email", sa.String(length=320), nullable=True),
        sa.Column("notiz", sa.String(length=1000), nullable=False),
        sa.Column("serie", sa.String(length=100), nullable=False),
        sa.Column("zurueckgegeben_am", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rueckgabe_betrag", sa.DECIMAL(precision=10, scale=2), nullable=True),
        sa.Column("rueckgabe_grund", sa.String(length=1000), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("art IN ('kauf', 'frei')", name="ck_gutschein_art"),
        sa.CheckConstraint(
            "modell <> 'einheit' OR minuten IS NOT NULL", name="ck_gutschein_einheit"
        ),
        sa.CheckConstraint(
            "gueltig_tage_snapshot IS NULL OR gueltig_tage_snapshot > 0",
            name="ck_gutschein_gueltigtage",
        ),
        sa.CheckConstraint(
            "art <> 'kauf' OR status IN ('offen', 'abgebrochen') OR (gueltig_bis "
            "IS NOT NULL AND kaufdatum IS NOT NULL)",
            name="ck_gutschein_kaufabschluss",
        ),
        sa.CheckConstraint(
            "kaufpreis_brutto IS NULL OR kaufpreis_brutto BETWEEN 0 AND 99999999.99",
            name="ck_gutschein_kaufpreis",
        ),
        sa.CheckConstraint(
            "art <> 'kauf' OR (kaeufer_kunde_id IS NOT NULL AND kaufpreis_brutto "
            "IS NOT NULL AND kundengruppe_id IS NOT NULL AND ust_satz IS NOT NULL "
            "AND gueltig_tage_snapshot IS NOT NULL AND kauf_reserviert_bis IS NOT "
            "NULL)",
            name="ck_gutschein_kaufsnapshot",
        ),
        sa.CheckConstraint("minuten IS NULL OR minuten > 0", name="ck_gutschein_minuten"),
        sa.CheckConstraint("modell IN ('einheit', 'wert')", name="ck_gutschein_modell"),
        sa.CheckConstraint(
            "nennwert_brutto IS NULL OR nennwert_brutto BETWEEN 0 AND 99999999.99",
            name="ck_gutschein_nennwert",
        ),
        sa.CheckConstraint("max_einloesungen > 0", name="ck_gutschein_nutzungen"),
        sa.CheckConstraint(
            "restwert IS NULL OR (restwert BETWEEN 0 AND 99999999.99 AND "
            "nennwert_brutto IS NOT NULL AND restwert <= nennwert_brutto)",
            name="ck_gutschein_restwert",
        ),
        sa.CheckConstraint(
            "rueckgabe_betrag IS NULL OR rueckgabe_betrag BETWEEN 0 AND 99999999.99",
            name="ck_gutschein_rueckgabe",
        ),
        sa.CheckConstraint(
            "status IN ('offen', 'abgebrochen', 'aktiv', 'teilweise_eingeloest', "
            "'eingeloest', 'verfallen', 'gesperrt')",
            name="ck_gutschein_status",
        ),
        sa.CheckConstraint(
            "(art = 'kauf' AND steuer IN ('beim_kauf', 'bei_einloesung')) OR (art "
            "= 'frei' AND steuer = 'keine' AND modell = 'einheit')",
            name="ck_gutschein_steuer",
        ),
        sa.CheckConstraint(
            "ust_satz IS NULL OR ust_satz BETWEEN 0 AND 100", name="ck_gutschein_ust"
        ),
        sa.CheckConstraint(
            "modell <> 'wert' OR (nennwert_brutto IS NOT NULL AND restwert IS NOT NULL)",
            name="ck_gutschein_wert",
        ),
        sa.CheckConstraint(
            "zeitraum_von IS NULL OR zeitraum_bis IS NULL OR zeitraum_von <= zeitraum_bis",
            name="ck_gutschein_zeitraum",
        ),
        sa.ForeignKeyConstraint(["inhaber_kunde_id"], ["kunde.id"]),
        sa.ForeignKeyConstraint(["zahlung_id"], ["zahlung.id"]),
        sa.ForeignKeyConstraint(["kundengruppe_id"], ["kundengruppe.id"]),
        sa.ForeignKeyConstraint(["rechnung_id"], ["rechnung.id"]),
        sa.ForeignKeyConstraint(["ausgestellt_von_admin_id"], ["admin_user.id"]),
        sa.ForeignKeyConstraint(["feld_id"], ["feld.id"]),
        sa.ForeignKeyConstraint(["kaeufer_kunde_id"], ["kunde.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
        sa.UniqueConstraint("zahlung_id"),
    )
    op.create_index(
        "ix_gutschein_inhaber_kunde_id", "gutschein", ["inhaber_kunde_id"], unique=False
    )
    op.create_index(
        "ix_gutschein_kaeufer_kunde_id", "gutschein", ["kaeufer_kunde_id"], unique=False
    )
    op.create_table(
        "gutschein_einloesung",
        sa.Column("gutschein_id", sa.UUID(), nullable=False),
        sa.Column("buchung_id", sa.UUID(), nullable=False),
        sa.Column("kunde_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("minuten_gedeckt", sa.Integer(), nullable=True),
        sa.Column("betrag", sa.DECIMAL(precision=10, scale=2), nullable=False),
        sa.Column("zeitpunkt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rechnung_position_id", sa.UUID(), nullable=True),
        sa.Column("ursprung_position_id", sa.UUID(), nullable=True),
        sa.Column("beleg_brutto", sa.DECIMAL(precision=10, scale=2), nullable=True),
        sa.Column("beleg_netto", sa.DECIMAL(precision=10, scale=2), nullable=True),
        sa.Column("beleg_ust", sa.DECIMAL(precision=10, scale=2), nullable=True),
        sa.Column("korrektur_rechnung_id", sa.UUID(), nullable=True),
        sa.Column("rueckgaengig_am", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rueckgaengig_grund", sa.String(length=1000), nullable=True),
        sa.Column("rueckabwicklung_art", sa.String(length=9), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(beleg_brutto IS NULL AND beleg_netto IS NULL AND beleg_ust IS NULL) "
            "OR (beleg_brutto IS NOT NULL AND beleg_netto IS NOT NULL AND "
            "beleg_ust IS NOT NULL AND beleg_brutto BETWEEN 0 AND 99999999.99 AND "
            "beleg_netto BETWEEN 0 AND 99999999.99 AND beleg_ust BETWEEN 0 AND "
            "99999999.99 AND beleg_netto + beleg_ust = beleg_brutto)",
            name="ck_einloesung_belegbasis",
        ),
        sa.CheckConstraint("betrag BETWEEN 0 AND 99999999.99", name="ck_einloesung_betrag"),
        sa.CheckConstraint(
            "minuten_gedeckt IS NULL OR minuten_gedeckt > 0", name="ck_einloesung_minuten"
        ),
        sa.CheckConstraint(
            "rueckabwicklung_art IN ('gutschein', 'guthaben')", name="ck_einloesung_rueckart"
        ),
        sa.CheckConstraint(
            "status IN ('reserviert', 'bestaetigt', 'rueckgaengig')", name="ck_einloesung_status"
        ),
        sa.ForeignKeyConstraint(["buchung_id"], ["buchung.id"]),
        sa.ForeignKeyConstraint(["korrektur_rechnung_id"], ["rechnung.id"]),
        sa.ForeignKeyConstraint(["rechnung_position_id"], ["rechnung_position.id"]),
        sa.ForeignKeyConstraint(["kunde_id"], ["kunde.id"]),
        sa.ForeignKeyConstraint(["gutschein_id"], ["gutschein.id"]),
        sa.ForeignKeyConstraint(["ursprung_position_id"], ["rechnung_position.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_gutschein_einloesung_buchung_id", "gutschein_einloesung", ["buchung_id"], unique=False
    )
    op.create_index(
        "ix_gutschein_einloesung_gutschein_id",
        "gutschein_einloesung",
        ["gutschein_id"],
        unique=False,
    )
    op.create_table(
        "gutschein_fehlversuch",
        sa.Column("kunde_id", sa.UUID(), nullable=False),
        sa.Column("zeitpunkt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("anfrage_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["kunde_id"], ["kunde.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("anfrage_id"),
    )
    op.create_index(
        "ix_gutschein_fehlversuch_kunde_zeitpunkt",
        "gutschein_fehlversuch",
        ["kunde_id", "zeitpunkt"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM gutschein) "
            "OR EXISTS (SELECT 1 FROM gutschein_einloesung) "
            "OR EXISTS (SELECT 1 FROM gutschein_fehlversuch) "
            "OR EXISTS (SELECT 1 FROM rechnung WHERE gutschein_rolle IS NOT NULL "
            "OR nummer LIKE 'GB-%') OR EXISTS (SELECT 1 FROM nummernkreis "
            "WHERE kreis <> 'rechnung' AND letzte_nummer > 0)"
        )
    ):
        raise RuntimeError(
            "Downgrade 0015 verweigert: Gutscheinbestand, Gutscheinbelege oder "
            "verbrauchte Gutschein-Nummernkreise vorhanden."
        )
    if bind.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM rechnung WHERE length(nummer) > 12)")):
        raise RuntimeError("Downgrade 0015 verweigert: Rechnungsnummer ist länger als 12 Zeichen.")
    op.drop_table("gutschein_fehlversuch")
    op.drop_table("gutschein_einloesung")
    op.drop_table("gutschein")
    op.drop_constraint("ck_rechnung_gutschein_rolle", "rechnung", type_="check")
    op.drop_column("rechnung", "gutschein_rolle")
    op.alter_column("rechnung", "nummer", type_=sa.String(12), existing_type=sa.String(20))
    op.execute("DELETE FROM nummernkreis WHERE kreis <> 'rechnung'")
    op.drop_constraint("ck_nummernkreis_nummer", "nummernkreis", type_="check")
    op.drop_constraint("ck_nummernkreis_kreis", "nummernkreis", type_="check")
    op.drop_constraint("nummernkreis_pkey", "nummernkreis", type_="primary")
    op.drop_column("nummernkreis", "kreis")
    op.create_primary_key("nummernkreis_pkey", "nummernkreis", ["jahr"])
