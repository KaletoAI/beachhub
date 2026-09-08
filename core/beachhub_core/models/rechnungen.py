import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DECIMAL, Date, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from beachhub_core.models.base import Base, UUIDMixin, ZeitstempelMixin
from beachhub_core.models.kunden import Kunde


class Nummernkreis(Base):
    __tablename__ = "nummernkreis"
    jahr: Mapped[int] = mapped_column(Integer, primary_key=True)
    letzte_nummer: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Rechnung(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "rechnung"
    nummer: Mapped[str] = mapped_column(String(12), nullable=False, unique=True)
    kunde_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("kunde.id"), nullable=False
    )
    art: Mapped[str] = mapped_column(String(10), nullable=False)  # einzel | sammel | storno
    datum: Mapped[date] = mapped_column(Date, nullable=False)
    leistung_von: Mapped[date] = mapped_column(Date, nullable=False)
    leistung_bis: Mapped[date] = mapped_column(Date, nullable=False)
    faellig_am: Mapped[date] = mapped_column(Date, nullable=False)
    ust_satz: Mapped[Decimal] = mapped_column(DECIMAL(5, 2), nullable=False)
    netto: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    ust: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    brutto: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)  # offen | bezahlt | storniert
    bezahlt_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pdf_pfad: Mapped[str | None] = mapped_column(String(300))
    pdf_sha256: Mapped[str | None] = mapped_column(String(64))
    storniert_durch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rechnung.id")
    )
    adresse_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    kunde: Mapped[Kunde] = relationship()
    positionen: Mapped[list["RechnungPosition"]] = relationship(
        back_populates="rechnung",
        cascade="all, delete-orphan",
        order_by="RechnungPosition.reihenfolge",
    )


class RechnungPosition(UUIDMixin, Base):
    __tablename__ = "rechnung_position"
    rechnung_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rechnung.id"), nullable=False
    )
    reihenfolge: Mapped[int] = mapped_column(Integer, nullable=False)
    buchung_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("buchung.id")
    )
    text: Mapped[str] = mapped_column(String(300), nullable=False)
    menge: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    einzelpreis_brutto: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    ust_satz: Mapped[Decimal] = mapped_column(DECIMAL(5, 2), nullable=False)
    brutto: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    rechnung: Mapped[Rechnung] = relationship(back_populates="positionen")

    __table_args__ = (
        Index(
            "ux_rechnung_position_buchung",
            "buchung_id",
            unique=True,
            postgresql_where=sql_text("buchung_id IS NOT NULL"),
        ),
    )
