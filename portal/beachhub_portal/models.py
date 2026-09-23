"""Datenmodell des Portals, Schema `spiegel` (Spec Portal-Kern § 7).

Das Portal hält Konten, Login-Codes, Sessions, die Anfragetabelle und den signierten Lesestand.
Postadressen, Rechnungen und Zahlungsdaten liegen ausschließlich im Hauptsystem (N-2).
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, MetaData, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "spiegel"


def _jetzt() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA)


class Konto(Base):
    __tablename__ = "konto"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    # Leer, bis der Kunde auf /willkommen seinen Namen angegeben hat.
    anzeigename: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    kunde_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    erstellt_am: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_jetzt, nullable=False
    )


class LoginToken(Base):
    """An die E-Mail gebunden, nicht an ein Konto: So lässt sich nicht erkennen, ob eine
    Adresse schon ein Konto hat."""

    __tablename__ = "login_token"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    fehlversuche: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    laeuft_ab: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verwendet_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Sitzung(Base):
    __tablename__ = "session"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    konto_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.konto.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    laeuft_ab: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Anfrage(Base):
    __tablename__ = "anfrage"
    OFFEN, ABGEHOLT, BEANTWORTET = "offen", "abgeholt", "beantwortet"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    typ: Mapped[str] = mapped_column(String(40), nullable=False)
    # Ohne Fremdschlüssel: `konto_loeschen` muss das gelöschte Konto überleben.
    konto_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    nutzlast_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    erstellt_am: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_jetzt, nullable=False
    )
    status: Mapped[str] = mapped_column(String(12), default=OFFEN, nullable=False)
    abgeholt_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    antwort_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    beantwortet_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_anfrage_status_erstellt", "status", "erstellt_am"),)


class Lesestand(Base):
    __tablename__ = "lesestand"
    dokument: Mapped[str] = mapped_column(String(80), primary_key=True)
    # BigInteger: Das Hauptsystem vergibt Versionen als Unixzeit in Millisekunden
    # (Ruling Lesestand-Versionen) und übersteigt damit schnell den 32-Bit-Bereich.
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    erzeugt_am: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    signatur: Mapped[str] = mapped_column(String(200), nullable=False)
    inhalt_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    empfangen_am: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RechnungLink(Base):
    __tablename__ = "rechnung_link"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    konto_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.konto.id", ondelete="CASCADE"), nullable=False
    )
    rechnung_nr: Mapped[str] = mapped_column(String(20), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    pdf_pfad: Mapped[str] = mapped_column(String(300), nullable=False)
    laeuft_ab: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    abgerufen_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebhookEingang(Base):
    __tablename__ = "webhook_eingang"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    rohdaten: Mapped[str] = mapped_column(Text, nullable=False)
    signatur_header: Mapped[str | None] = mapped_column(Text)
    empfangen_am: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_jetzt, nullable=False
    )
    anfrage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)


class KanalKontakt(Base):
    """Eine Zeile: wann das Hauptsystem zuletzt Anfragen abgeholt hat."""

    __tablename__ = "kanal_kontakt"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    letzter_abruf: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
