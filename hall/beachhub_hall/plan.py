"""Betriebsplan: prüfen, atomar ersetzen, laden (Hallendienst-Spec § 5 „Plan-Abruf“).

Geprüft wird in dieser Reihenfolge: Form, Signatur, Dokumentname, Version, Inhalt. Erst ein
vollständig geprüfter Plan ersetzt den alten – in einer Transaktion, damit ein Fehler
mittendrin nie einen halben Plan hinterlässt.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from beachhub_shared import signatur
from beachhub_shared.hallenplan import DOKUMENT, HallenplanInhalt, PlanBuchung
from beachhub_shared.lesestand import Dokument
from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from beachhub_hall.db import (
    PlanBuchungZeile,
    PlanFeldZeile,
    PlanKonfigZeile,
    PlanMetaZeile,
    PlanSperreZeile,
)


class PlanFehler(Exception):  # noqa: N818
    def __init__(self, grund: str) -> None:
        super().__init__(grund)
        self.grund = grund


@dataclass(frozen=True)
class GespeicherterPlan:
    version: int
    empfangen_am: datetime
    inhalt: HallenplanInhalt


def pruefe(
    roh: dict[str, Any], oeffentlich_hex: str, aktuelle_version: int
) -> tuple[Dokument, HallenplanInhalt]:
    try:
        dok = Dokument.model_validate(roh)
    except ValidationError as e:
        raise PlanFehler("schema") from e
    daten = dok.model_dump(mode="json", exclude={"signatur"})
    if not signatur.pruefe(daten, dok.signatur, oeffentlich_hex):
        raise PlanFehler("signatur")
    if dok.dokument != DOKUMENT:
        raise PlanFehler("schema")
    if dok.version <= aktuelle_version:
        raise PlanFehler("version_alt")
    try:
        inhalt = HallenplanInhalt.model_validate(dok.inhalt)
    except ValidationError as e:
        raise PlanFehler("schema") from e
    return dok, inhalt


def speichere(db: Session, dok: Dokument, inhalt: HallenplanInhalt, empfangen_am: datetime) -> None:
    try:
        for tabelle in (
            PlanBuchungZeile,
            PlanSperreZeile,
            PlanFeldZeile,
            PlanKonfigZeile,
            PlanMetaZeile,
        ):
            db.execute(delete(tabelle))
        db.add(
            PlanMetaZeile(
                id=1,
                version=dok.version,
                erzeugt_am=dok.erzeugt_am,
                gueltig_bis=inhalt.gueltig_bis,
                empfangen_am=empfangen_am,
                dokument_json=dok.model_dump_json(),
            )
        )
        db.add_all(PlanFeldZeile(feld_id=f.id, name=f.name, aktiv=f.aktiv) for f in inhalt.felder)
        db.add_all(
            PlanBuchungZeile(
                buchung_id=b.buchung_id,
                feld_id=b.feld_id,
                beginn=b.beginn,
                ende=b.ende,
                pin_hash=b.pin_hash,
            )
            for b in inhalt.buchungen
        )
        db.add_all(
            PlanSperreZeile(feld_id=s.feld_id, beginn=s.beginn, ende=s.ende) for s in inhalt.sperren
        )
        db.add_all(
            PlanKonfigZeile(schluessel=k, wert=str(v))
            for k, v in inhalt.konfig.model_dump(mode="json").items()
        )
        db.commit()
    except Exception:
        db.rollback()
        raise


def lade(db: Session) -> GespeicherterPlan | None:
    meta = db.get(PlanMetaZeile, 1)
    if meta is None:
        return None
    dok = Dokument.model_validate_json(meta.dokument_json)
    return GespeicherterPlan(
        version=meta.version,
        empfangen_am=meta.empfangen_am,
        inhalt=HallenplanInhalt.model_validate(dok.inhalt),
    )


def version(db: Session) -> int:
    meta = db.get(PlanMetaZeile, 1)
    return meta.version if meta else 0


def buchungen_mit_pin(db: Session, pin_hash: str) -> list[PlanBuchung]:
    zeilen = db.scalars(select(PlanBuchungZeile).where(PlanBuchungZeile.pin_hash == pin_hash))
    return [
        PlanBuchung(
            buchung_id=z.buchung_id,
            feld_id=z.feld_id,
            beginn=z.beginn,
            ende=z.ende,
            pin_hash=z.pin_hash,
        )
        for z in zeilen
    ]
