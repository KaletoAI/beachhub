import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from beachhub_core.models.base import Base, UUIDMixin, ZeitstempelMixin


class Ereignis(UUIDMixin, ZeitstempelMixin, Base):
    """Ereignisse aus der Halle (und später aus Portal und Admin). Die Halle zählt ihre
    Ereignisse mit seq je Dienst-ID; das Paar ist eindeutig und macht Nachlieferungen
    idempotent."""

    __tablename__ = "ereignis"
    __table_args__ = (
        UniqueConstraint("halle_dienst_id", "halle_seq", name="ereignis_halle_seq_eindeutig"),
    )
    quelle: Mapped[str] = mapped_column(String(10), nullable=False)  # halle|portal|admin|system
    typ: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    zeitpunkt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    # Ohne Fremdschlüssel: Die Halle meldet, was sie kennt; ein Ereignis darf nie verloren
    # gehen, nur weil ein Bezug unbekannt ist.
    feld_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    buchung_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    daten_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    halle_dienst_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    halle_seq: Mapped[int | None] = mapped_column(BigInteger)


class HallenStatusZeile(Base):
    """Zuletzt gemeldeter Status der Halle – genau eine Zeile (id = 1)."""

    __tablename__ = "hallen_status"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    daten_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    empfangen_am: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HalleDienst(Base):
    """Bis wohin ein Hallendienst (je `dienst_id`) zuletzt lückenlos bestätigt wurde. Getrennt
    von `Ereignis`, damit das Aufräumen alter Ereignisse (90 Tage, Hallendienst-Spec § 10) die
    Zählung nie zurückwirft, und als Zeile für eine Sperre (`SELECT … FOR UPDATE`), die
    parallele Lieferungen derselben Dienst-ID serialisiert."""

    __tablename__ = "halle_dienst"
    dienst_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    bestaetigt_bis: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
