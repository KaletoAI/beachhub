import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from beachhub_core.models.base import Base, UUIDMixin, ZeitstempelMixin, utcnow


class Konfiguration(Base):
    __tablename__ = "konfiguration"
    schluessel: Mapped[str] = mapped_column(String(60), primary_key=True)
    wert: Mapped[str] = mapped_column(String(500), nullable=False)
    typ: Mapped[str] = mapped_column(String(10), nullable=False)  # int | decimal | str | bool


class AdminUser(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "admin_user"
    name: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    passwort_hash: Mapped[str] = mapped_column(String(300), nullable=False)
    totp_secret: Mapped[str] = mapped_column(String(64), nullable=False)
    rolle: Mapped[str] = mapped_column(String(10), default="admin", nullable=False)  # admin|lesend
    aktiv: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class AdminSession(UUIDMixin, Base):
    __tablename__ = "admin_session"
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    admin_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_user.id"), nullable=False
    )
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    erstellt_am: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    laeuft_ab: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Audit(UUIDMixin, Base):
    __tablename__ = "audit"
    zeitpunkt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    admin_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    quelle: Mapped[str] = mapped_column(String(10), nullable=False)  # admin|portal|halle|system
    objekt_typ: Mapped[str] = mapped_column(String(40), nullable=False)
    objekt_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    vorher_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    nachher_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class LesestandVersion(Base):
    __tablename__ = "lesestand_version"
    dokument: Mapped[str] = mapped_column(String(80), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    signiert_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    geaendert: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class AppSetting(Base):
    __tablename__ = "app_setting"
    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str | None] = mapped_column(String(200))
