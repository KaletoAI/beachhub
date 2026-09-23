"""Tabellen für den Kanal zum Portal und die Online-Zahlung (Spec Portal-Kern § 4–5)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DECIMAL, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from beachhub_core.models.base import Base, UUIDMixin, ZeitstempelMixin, utcnow
from beachhub_core.models.buchungen import Buchung
from beachhub_core.models.kunden import Kunde


class AnfrageVerarbeitet(Base):
    """Jede Portal-Anfrage wird genau einmal verarbeitet; eine erneut zugestellte Anfrage
    bekommt die hier gespeicherte Antwort."""

    __tablename__ = "anfrage_verarbeitet"
    anfrage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    typ: Mapped[str] = mapped_column(String(40), nullable=False)
    antwort_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    verarbeitet_am: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Zahlung(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "zahlung"
    OFFEN, BEZAHLT, ABGEBROCHEN = "offen", "bezahlt", "abgebrochen"

    kunde_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("kunde.id"), nullable=False
    )
    buchung_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("buchung.id"), index=True
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_ref: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    betrag: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(12), default=OFFEN, nullable=False)
    empfangen_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rohdaten_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Bezahlseite des Anbieters; erscheint im Lesestand, solange die Reservierung offen ist.
    checkout_url: Mapped[str | None] = mapped_column(String(1000))
    kunde: Mapped[Kunde] = relationship()
    buchung: Mapped[Buchung | None] = relationship()
