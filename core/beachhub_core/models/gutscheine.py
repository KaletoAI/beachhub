"""Unveränderliche Kaufbedingungen und rückverfolgbare Gutscheinverbräuche (§ 5)."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    DECIMAL,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from beachhub_core.models.base import Base, UUIDMixin, ZeitstempelMixin, utcnow


class Gutschein(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "gutschein"
    OFFEN, AKTIV, ABGEBROCHEN = "offen", "aktiv", "abgebrochen"
    TEILWEISE_EINGELOEST, EINGELOEST = "teilweise_eingeloest", "eingeloest"
    VERFALLEN, GESPERRT = "verfallen", "gesperrt"

    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    art: Mapped[str] = mapped_column(String(4), nullable=False)
    # Diese Werte sind Kauf-Snapshots, keine Verweise auf aktuelle Einstellungen.
    modell: Mapped[str] = mapped_column(String(7), nullable=False)
    steuer: Mapped[str] = mapped_column(String(14), nullable=False)
    minuten: Mapped[int | None] = mapped_column(Integer)
    nennwert_brutto: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 2))
    restwert: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 2))
    kaufpreis_brutto: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 2))
    kundengruppe_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("kundengruppe.id")
    )
    ust_satz: Mapped[Decimal | None] = mapped_column(DECIMAL(5, 2))
    nur_mitglieder: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    feld_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("feld.id"))
    zeitraum_von: Mapped[date | None] = mapped_column(Date)
    zeitraum_bis: Mapped[date | None] = mapped_column(Date)
    max_einloesungen: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    kaeufer_kunde_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("kunde.id"), index=True
    )
    inhaber_kunde_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("kunde.id"), index=True
    )
    zahlung_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("zahlung.id"), unique=True
    )
    rechnung_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rechnung.id")
    )
    ausgestellt_von_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_user.id")
    )
    ausgestellt_am: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    kaufdatum: Mapped[date | None] = mapped_column(Date)
    kauf_reserviert_bis: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gueltig_tage_snapshot: Mapped[int | None] = mapped_column(Integer)
    gueltig_bis: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    empfaenger_email: Mapped[str | None] = mapped_column(String(320))
    notiz: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    serie: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    zurueckgegeben_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rueckgabe_betrag: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 2))
    rueckgabe_grund: Mapped[str | None] = mapped_column(String(1000))

    __table_args__ = (
        CheckConstraint("art IN ('kauf', 'frei')", name="ck_gutschein_art"),
        CheckConstraint("modell IN ('einheit', 'wert')", name="ck_gutschein_modell"),
        CheckConstraint(
            "(art = 'kauf' AND steuer IN ('beim_kauf', 'bei_einloesung')) OR (art "
            "= 'frei' AND steuer = 'keine' AND modell = 'einheit')",
            name="ck_gutschein_steuer",
        ),
        CheckConstraint(
            "status IN ('offen', 'abgebrochen', 'aktiv', 'teilweise_eingeloest', "
            "'eingeloest', 'verfallen', 'gesperrt')",
            name="ck_gutschein_status",
        ),
        CheckConstraint("minuten IS NULL OR minuten > 0", name="ck_gutschein_minuten"),
        CheckConstraint("max_einloesungen > 0", name="ck_gutschein_nutzungen"),
        CheckConstraint(
            "gueltig_tage_snapshot IS NULL OR gueltig_tage_snapshot > 0",
            name="ck_gutschein_gueltigtage",
        ),
        CheckConstraint(
            "zeitraum_von IS NULL OR zeitraum_bis IS NULL OR zeitraum_von <= zeitraum_bis",
            name="ck_gutschein_zeitraum",
        ),
        CheckConstraint(
            "nennwert_brutto IS NULL OR nennwert_brutto BETWEEN 0 AND 99999999.99",
            name="ck_gutschein_nennwert",
        ),
        CheckConstraint(
            "restwert IS NULL OR (restwert BETWEEN 0 AND 99999999.99 AND "
            "nennwert_brutto IS NOT NULL AND restwert <= nennwert_brutto)",
            name="ck_gutschein_restwert",
        ),
        CheckConstraint(
            "kaufpreis_brutto IS NULL OR kaufpreis_brutto BETWEEN 0 AND 99999999.99",
            name="ck_gutschein_kaufpreis",
        ),
        CheckConstraint(
            "rueckgabe_betrag IS NULL OR rueckgabe_betrag BETWEEN 0 AND 99999999.99",
            name="ck_gutschein_rueckgabe",
        ),
        CheckConstraint("ust_satz IS NULL OR ust_satz BETWEEN 0 AND 100", name="ck_gutschein_ust"),
        CheckConstraint("modell <> 'einheit' OR minuten IS NOT NULL", name="ck_gutschein_einheit"),
        CheckConstraint(
            "modell <> 'wert' OR (nennwert_brutto IS NOT NULL AND restwert IS NOT NULL)",
            name="ck_gutschein_wert",
        ),
        CheckConstraint(
            "art <> 'kauf' OR (kaeufer_kunde_id IS NOT NULL AND kaufpreis_brutto "
            "IS NOT NULL AND kundengruppe_id IS NOT NULL AND ust_satz IS NOT NULL "
            "AND gueltig_tage_snapshot IS NOT NULL AND kauf_reserviert_bis IS NOT "
            "NULL)",
            name="ck_gutschein_kaufsnapshot",
        ),
        CheckConstraint(
            "art <> 'kauf' OR status IN ('offen', 'abgebrochen') OR (gueltig_bis "
            "IS NOT NULL AND kaufdatum IS NOT NULL)",
            name="ck_gutschein_kaufabschluss",
        ),
    )


class GutscheinEinloesung(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "gutschein_einloesung"
    RESERVIERT, BESTAETIGT, RUECKGAENGIG = "reserviert", "bestaetigt", "rueckgaengig"

    gutschein_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("gutschein.id"), nullable=False, index=True
    )
    buchung_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("buchung.id"), nullable=False, index=True
    )
    kunde_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("kunde.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(12), nullable=False, default=RESERVIERT)
    minuten_gedeckt: Mapped[int | None] = mapped_column(Integer)
    betrag: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    zeitpunkt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    # Gutscheinpositionen tragen keinen direkten Buchungs-FK (Unique-Index bleibt bestehen).
    rechnung_position_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rechnung_position.id")
    )
    ursprung_position_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rechnung_position.id")
    )
    beleg_brutto: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 2))
    beleg_netto: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 2))
    beleg_ust: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 2))
    # Ein Bezug je Einlösung; mehrere Einlösungen dürfen einen Sammelgegenbeleg teilen.
    korrektur_rechnung_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rechnung.id")
    )
    rueckgaengig_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rueckgaengig_grund: Mapped[str | None] = mapped_column(String(1000))
    rueckabwicklung_art: Mapped[str | None] = mapped_column(String(9))

    __table_args__ = (
        CheckConstraint(
            "status IN ('reserviert', 'bestaetigt', 'rueckgaengig')", name="ck_einloesung_status"
        ),
        CheckConstraint("betrag BETWEEN 0 AND 99999999.99", name="ck_einloesung_betrag"),
        CheckConstraint(
            "minuten_gedeckt IS NULL OR minuten_gedeckt > 0", name="ck_einloesung_minuten"
        ),
        CheckConstraint(
            "rueckabwicklung_art IN ('gutschein', 'guthaben')", name="ck_einloesung_rueckart"
        ),
        CheckConstraint(
            "(beleg_brutto IS NULL AND beleg_netto IS NULL AND beleg_ust IS NULL) "
            "OR (beleg_brutto IS NOT NULL AND beleg_netto IS NOT NULL AND "
            "beleg_ust IS NOT NULL AND beleg_brutto BETWEEN 0 AND 99999999.99 AND "
            "beleg_netto BETWEEN 0 AND 99999999.99 AND beleg_ust BETWEEN 0 AND "
            "99999999.99 AND beleg_netto + beleg_ust = beleg_brutto)",
            name="ck_einloesung_belegbasis",
        ),
    )


class GutscheinFehlversuch(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "gutschein_fehlversuch"
    kunde_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("kunde.id"), nullable=False
    )
    zeitpunkt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    anfrage_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), unique=True)
    __table_args__ = (Index("ix_gutschein_fehlversuch_kunde_zeitpunkt", "kunde_id", "zeitpunkt"),)
