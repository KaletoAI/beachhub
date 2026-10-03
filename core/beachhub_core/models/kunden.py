import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import DECIMAL, Boolean, CheckConstraint, Date, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from beachhub_core.models.base import Base, UUIDMixin, ZeitstempelMixin


class Kunde(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "kunde"
    __table_args__ = (CheckConstraint("guthaben >= 0", name="kunde_guthaben_nicht_negativ"),)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    adresse_strasse: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    adresse_plz: Mapped[str] = mapped_column(String(10), default="", nullable=False)
    adresse_ort: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    # Rechnungskunden buchen nicht online; ihre Buchungen legt der Betreiber an (A-KUND-7).
    # Die Zahlungsart steht an der Buchung (A-ZAHL-1).
    rechnungskunde: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    guthaben: Mapped[Decimal] = mapped_column(
        DECIMAL(10, 2), default=Decimal("0.00"), nullable=False
    )
    portal_konto_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), unique=True)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(100))
    anonymisiert_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Mitgliedschaft (A-KUND-4): Die Kundengruppe ist nicht gespeichert, sondern ergibt sich aus
    # mitglied_bis und dem Leistungsdatum (services/kundengruppen.effektive_gruppe).
    mitglied_bis: Mapped[date | None] = mapped_column(Date)
    mitglied_antrag_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mitglied_antrag_hinweis: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    mitglied_freigeschaltet_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mitglied_freigeschaltet_von: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    mitglied_beendet_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mitglied_beendet_grund: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    # Das mitglied_bis, an dessen Ablauf zuletzt erinnert wurde – je Ablauf höchstens eine Mail.
    mitglied_erinnert_fuer: Mapped[date | None] = mapped_column(Date)


class GuthabenBuchung(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "guthaben_buchung"
    kunde_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("kunde.id"), nullable=False
    )
    betrag: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    art: Mapped[str] = mapped_column(String(20), nullable=False)
    bezug_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    notiz: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    admin_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
