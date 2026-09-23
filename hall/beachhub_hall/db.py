"""SQLite-Datenbank des Hallendienstes (WAL-Modus, eine Datei).

Alle Zeiten werden als UTC gespeichert. SQLite kennt keine Zeitzonen; `UTCZeit` speichert
deshalb ohne tzinfo und liefert beim Lesen immer `tzinfo=UTC` zurück.
"""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, String, Text, create_engine, event
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import TypeDecorator


class UTCZeit(TypeDecorator[datetime]):
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Zeit ohne Zeitzone")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    pass


class PlanMetaZeile(Base):
    __tablename__ = "plan_meta"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    erzeugt_am: Mapped[datetime] = mapped_column(UTCZeit, nullable=False)
    gueltig_bis: Mapped[datetime] = mapped_column(UTCZeit, nullable=False)
    empfangen_am: Mapped[datetime] = mapped_column(UTCZeit, nullable=False)
    # Das ganze signierte Dokument: Quelle für plan.lade(), die Tabellen darunter dienen Abfragen.
    dokument_json: Mapped[str] = mapped_column(Text, nullable=False)


class PlanBuchungZeile(Base):
    __tablename__ = "plan_buchung"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    buchung_id: Mapped[str] = mapped_column(String(36), nullable=False)
    feld_id: Mapped[str] = mapped_column(String(36), nullable=False)
    beginn: Mapped[datetime] = mapped_column(UTCZeit, nullable=False)
    ende: Mapped[datetime] = mapped_column(UTCZeit, nullable=False)
    pin_hash: Mapped[str] = mapped_column(String(200), nullable=False, index=True)


class PlanSperreZeile(Base):
    __tablename__ = "plan_sperre"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    feld_id: Mapped[str | None] = mapped_column(String(36))
    beginn: Mapped[datetime] = mapped_column(UTCZeit, nullable=False)
    ende: Mapped[datetime] = mapped_column(UTCZeit, nullable=False)


class PlanFeldZeile(Base):
    __tablename__ = "plan_feld"
    feld_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    aktiv: Mapped[bool] = mapped_column(Boolean, nullable=False)


class PlanKonfigZeile(Base):
    __tablename__ = "plan_konfig"
    schluessel: Mapped[str] = mapped_column(String(60), primary_key=True)
    wert: Mapped[str] = mapped_column(String(100), nullable=False)


class EreignisZeile(Base):
    __tablename__ = "ereignis_queue"
    # AUTOINCREMENT: eine gelöschte seq wird nie wieder vergeben (90-Tage-Aufräumen), sonst
    # hielte das Hauptsystem neue Ereignisse für Duplikate.
    __table_args__ = {"sqlite_autoincrement": True}
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    typ: Mapped[str] = mapped_column(String(40), nullable=False)
    zeitpunkt: Mapped[datetime] = mapped_column(UTCZeit, nullable=False)
    feld_id: Mapped[str | None] = mapped_column(String(36))
    buchung_id: Mapped[str | None] = mapped_column(String(36))
    daten_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    gesendet_am: Mapped[datetime | None] = mapped_column(UTCZeit, index=True)


class ZustandZeile(Base):
    __tablename__ = "zustand"
    schluessel: Mapped[str] = mapped_column(String(60), primary_key=True)
    wert_json: Mapped[str] = mapped_column(Text, nullable=False)


def _pragmas(dbapi_verbindung: Any, _eintrag: Any) -> None:
    cur = dbapi_verbindung.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def oeffne(pfad: Path) -> sessionmaker[Session]:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{pfad}")
    event.listen(engine, "connect", _pragmas)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def lies(db: Session, schluessel: str, standard: Any = None) -> Any:
    z = db.get(ZustandZeile, schluessel)
    return standard if z is None else json.loads(z.wert_json)


def schreibe(db: Session, schluessel: str, wert: Any) -> None:
    roh = json.dumps(wert, default=str)
    z = db.get(ZustandZeile, schluessel)
    if z is None:
        db.add(ZustandZeile(schluessel=schluessel, wert_json=roh))
    else:
        z.wert_json = roh
    db.flush()


def dienst_id(sitzungen: sessionmaker[Session]) -> str:
    """Einmal je Datenbankdatei erzeugt. Das Hauptsystem erkennt Ereignisse an
    (dienst_id, seq); nach einer Neuinstallation beginnt seq wieder bei 1."""
    with sitzungen() as db:
        wert = lies(db, "dienst_id")
        if wert is None:
            wert = str(uuid.uuid4())
            schreibe(db, "dienst_id", wert)
            db.commit()
        return str(wert)
