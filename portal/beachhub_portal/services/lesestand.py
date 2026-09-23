"""Signierter Lesestand im Portal: prüfen, speichern, typisiert lesen (Hauptspec § 8.1)."""

import uuid
from datetime import datetime
from typing import Any, Literal

from beachhub_shared import kanal, signatur
from beachhub_shared.lesestand import BelegungInhalt, Dokument, KontoInhalt, TarifeInhalt
from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_portal.config import settings
from beachhub_portal.models import Lesestand

Grund = Literal["signatur", "version_alt", "unbekannt"]


def uebernehme(db: Session, dok: Dokument, jetzt: datetime) -> Grund | None:
    """Übernimmt ein Dokument, wenn Name, Signatur und Version stimmen; sonst den Grund."""
    if not kanal.fuer_portal(dok.dokument):
        return "unbekannt"
    inhalt = dok.model_dump(mode="json", exclude={"signatur"})
    if not settings.core_public_key or not signatur.pruefe(
        inhalt, dok.signatur, settings.core_public_key
    ):
        return "signatur"
    zeile = db.get(Lesestand, dok.dokument, with_for_update=True)
    if zeile is not None and zeile.version >= dok.version:
        return "version_alt"
    if zeile is None:
        zeile = Lesestand(dokument=dok.dokument)
        db.add(zeile)
    zeile.version = dok.version
    zeile.erzeugt_am = dok.erzeugt_am
    zeile.signatur = dok.signatur
    zeile.inhalt_json = dok.inhalt
    zeile.empfangen_am = jetzt
    return None


def _inhalt(db: Session, name: str) -> dict[str, Any] | None:
    zeile = db.get(Lesestand, name)
    return zeile.inhalt_json if zeile is not None else None


def belegung(db: Session) -> BelegungInhalt | None:
    d = _inhalt(db, "belegung")
    return BelegungInhalt.model_validate(d) if d is not None else None


def tarife(db: Session) -> TarifeInhalt | None:
    d = _inhalt(db, "tarife")
    return TarifeInhalt.model_validate(d) if d is not None else None


def konto(db: Session, kunde_id: uuid.UUID | None) -> KontoInhalt | None:
    if kunde_id is None:
        return None
    d = _inhalt(db, f"konto:{kunde_id}")
    return KontoInhalt.model_validate(d) if d is not None else None


def versionen(db: Session) -> dict[str, int]:
    return {z.dokument: z.version for z in db.scalars(select(Lesestand))}


def loesche(db: Session, name: str) -> None:
    zeile = db.get(Lesestand, name)
    if zeile is not None:
        db.delete(zeile)
