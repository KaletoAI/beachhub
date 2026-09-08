import uuid
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import DECIMAL, Boolean, Date, DateTime, ForeignKey, String, Time, text
from sqlalchemy.dialects.postgresql import UUID, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from beachhub_core.models.base import Base, UUIDMixin, ZeitstempelMixin, utcnow
from beachhub_core.models.kunden import Kunde
from beachhub_core.models.stammdaten import Feld


class Buchung(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "buchung"
    ANGEFRAGT, RESERVIERT, BESTAETIGT = "angefragt", "reserviert", "bestaetigt"
    DURCHGEFUEHRT, NICHT_ERSCHIENEN = "durchgefuehrt", "nicht_erschienen"
    STORNIERT, ABGELEHNT, VERFALLEN = "storniert", "abgelehnt", "verfallen"
    AKTIVE_STATUS = (ANGEFRAGT, RESERVIERT, BESTAETIGT, DURCHGEFUEHRT, NICHT_ERSCHIENEN)

    feld_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("feld.id"), nullable=False
    )
    kunde_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("kunde.id"), nullable=False
    )
    beginn: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ende: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    preis: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    zahlungsart: Mapped[str] = mapped_column(String(10), nullable=False)
    pin_hash: Mapped[str | None] = mapped_column(String(200))
    pin_verschluesselt: Mapped[str | None] = mapped_column(String(300))
    dauerbuchung_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dauerbuchung.id")
    )
    anfrage_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), unique=True)
    reserviert_bis: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rechnung_position_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rechnung_position.id", use_alter=True, name="fk_buchung_rechnung_position_id"),
    )
    anwesenheit: Mapped[str] = mapped_column(String(20), default="unbekannt", nullable=False)
    quelle: Mapped[str] = mapped_column(String(10), nullable=False)  # admin | portal | dauer

    feld: Mapped[Feld] = relationship()
    kunde: Mapped[Kunde] = relationship()
    storno: Mapped["Storno | None"] = relationship(
        back_populates="buchung", uselist=False, foreign_keys="Storno.buchung_id"
    )

    __table_args__ = (
        ExcludeConstraint(  # type: ignore[no-untyped-call]
            ("feld_id", "="),
            (text("tstzrange(beginn, ende)"), "&&"),
            using="gist",
            where=text(
                "status IN ('angefragt','reserviert','bestaetigt','durchgefuehrt','nicht_erschienen')"  # noqa: E501
            ),
            name="buchung_keine_ueberlappung",
        ),
    )

    @property
    def aktiv(self) -> bool:
        return self.status in self.AKTIVE_STATUS


class Dauerbuchung(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "dauerbuchung"
    kunde_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("kunde.id"), nullable=False
    )
    feld_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("feld.id"), nullable=False
    )
    wochentag: Mapped[int] = mapped_column(nullable=False)
    start: Mapped[time] = mapped_column(Time, nullable=False)
    ende: Mapped[time] = mapped_column(Time, nullable=False)
    gueltig_von: Mapped[date] = mapped_column(Date, nullable=False)
    gueltig_bis: Mapped[date] = mapped_column(Date, nullable=False)
    pin_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    pin_verschluesselt: Mapped[str] = mapped_column(String(300), nullable=False)
    beendet_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    beendet_ab: Mapped[date | None] = mapped_column(Date)
    kunde: Mapped[Kunde] = relationship()
    feld: Mapped[Feld] = relationship()
    buchungen: Mapped[list[Buchung]] = relationship(order_by="Buchung.beginn")


class Sperre(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "sperre"
    feld_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("feld.id"))
    beginn: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ende: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    grund: Mapped[str] = mapped_column(String(200), nullable=False)
    __table_args__ = (
        ExcludeConstraint(  # type: ignore[no-untyped-call]
            ("feld_id", "="),
            (text("tstzrange(beginn, ende)"), "&&"),
            using="gist",
            where=text("feld_id IS NOT NULL"),
            name="sperre_keine_ueberlappung",
        ),
    )


class Storno(UUIDMixin, Base):
    __tablename__ = "storno"
    buchung_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("buchung.id"), nullable=False, unique=True
    )
    zeitpunkt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    durch: Mapped[str] = mapped_column(String(10), nullable=False)  # kunde | betreiber | system
    kostenfrei: Mapped[bool] = mapped_column(Boolean, nullable=False)
    grund: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    nachbuchung_offen: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    nachbuchung_buchung_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("buchung.id")
    )
    freigestellt_betrag: Mapped[Decimal] = mapped_column(
        DECIMAL(10, 2), default=Decimal("0.00"), nullable=False
    )
    buchung: Mapped[Buchung] = relationship(back_populates="storno", foreign_keys=[buchung_id])
