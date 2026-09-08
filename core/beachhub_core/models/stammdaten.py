import uuid
from datetime import date, time
from decimal import Decimal

from sqlalchemy import DECIMAL, Boolean, Date, ForeignKey, Integer, String, Time
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from beachhub_core.models.base import Base, UUIDMixin, ZeitstempelMixin


class Feld(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "feld"
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    aktiv: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reihenfolge: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ha_licht_entity: Mapped[str | None] = mapped_column(String(200))
    ha_praesenz_entity: Mapped[str | None] = mapped_column(String(200))
    heizzone: Mapped[str | None] = mapped_column(String(100))
    raster: Mapped[list["FeldRaster"]] = relationship(
        back_populates="feld", cascade="all, delete-orphan", order_by="FeldRaster.wochentag"
    )


class FeldRaster(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "feld_raster"
    feld_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("feld.id"), nullable=False
    )
    wochentag: Mapped[int | None] = mapped_column(Integer)  # 0=Mo … 6=So, None = alle
    modus: Mapped[str] = mapped_column(String(10), nullable=False)  # dauer | fenster
    slot_minuten: Mapped[int | None] = mapped_column(Integer)
    # [["19:00","21:00"]]
    fenster_json: Mapped[list[list[str]]] = mapped_column(JSONB, default=list, nullable=False)
    feld: Mapped[Feld] = relationship(back_populates="raster")


class Betriebszeit(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "betriebszeit"
    wochentag: Mapped[int] = mapped_column(Integer, nullable=False)
    oeffnet: Mapped[time] = mapped_column(Time, nullable=False)
    schliesst: Mapped[time] = mapped_column(Time, nullable=False)
    gueltig_von: Mapped[date | None] = mapped_column(Date)
    gueltig_bis: Mapped[date | None] = mapped_column(Date)


class Ausnahmetag(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "ausnahmetag"
    datum: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    geschlossen: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    oeffnet: Mapped[time | None] = mapped_column(Time)
    schliesst: Mapped[time | None] = mapped_column(Time)
    grund: Mapped[str] = mapped_column(String(200), default="", nullable=False)


class Kundengruppe(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "kundengruppe"
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    standard_zahlungsart: Mapped[str] = mapped_column(String(10), default="online", nullable=False)


class Tarif(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "tarif"
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    preis: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    feld_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("feld.id"))
    wochentag: Mapped[int | None] = mapped_column(Integer)
    uhrzeit_von: Mapped[time | None] = mapped_column(Time)
    uhrzeit_bis: Mapped[time | None] = mapped_column(Time)
    kundengruppe_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("kundengruppe.id")
    )
    gueltig_von: Mapped[date | None] = mapped_column(Date)
    gueltig_bis: Mapped[date | None] = mapped_column(Date)
    aktiv: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
