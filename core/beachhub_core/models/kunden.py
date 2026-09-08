import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DECIMAL, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from beachhub_core.models.base import Base, UUIDMixin, ZeitstempelMixin
from beachhub_core.models.stammdaten import Kundengruppe


class Kunde(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "kunde"
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    adresse_strasse: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    adresse_plz: Mapped[str] = mapped_column(String(10), default="", nullable=False)
    adresse_ort: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    kundengruppe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("kundengruppe.id"), nullable=False
    )
    zahlungsart: Mapped[str] = mapped_column(String(10), nullable=False)  # online | rechnung
    guthaben: Mapped[Decimal] = mapped_column(
        DECIMAL(10, 2), default=Decimal("0.00"), nullable=False
    )
    portal_konto_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), unique=True)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(100))
    anonymisiert_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    kundengruppe: Mapped[Kundengruppe] = relationship()


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
