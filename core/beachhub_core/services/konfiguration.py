"""Konfigurationswerte: Defaults im Code, Überschreibung in der Tabelle `konfiguration`."""

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from beachhub_core.models import Konfiguration
from beachhub_core.services import audit

DEFAULTS: dict[str, tuple[type, Any]] = {
    "fenster_tage": (int, 14),
    "mindestvorlauf_minuten": (int, 60),
    "storno_frist_stunden": (int, 24),
    "zahlungsfrist_minuten": (int, 15),
    "ust_satz": (Decimal, Decimal("19.00")),
    "rechnung_tag_im_folgemonat": (int, 3),
    "rechnung_zahlungsziel_tage": (int, 14),
    "heiz_vorlauf_minuten": (int, 60),
    "licht_vorlauf_minuten": (int, 5),
    "licht_nachlauf_minuten": (int, 5),
    "zutritt_vorlauf_minuten": (int, 15),
    "spiel_temperatur": (Decimal, Decimal("16.0")),
    "grund_temperatur": (Decimal, Decimal("8.0")),
    "antwort_hinweis_sekunden": (int, 120),
    "pin_laenge": (int, 6),
}

_TYP_NAME = {int: "int", Decimal: "decimal", str: "str", bool: "bool"}


def _parse(typ: type, roh: str) -> Any:
    if typ is bool:
        return roh.lower() in ("1", "true", "ja")
    return typ(roh)


def hole(db: Session, schluessel: str) -> Any:
    typ, default = DEFAULTS[schluessel]
    zeile = db.get(Konfiguration, schluessel)
    return _parse(typ, zeile.wert) if zeile else default


def setze(db: Session, schluessel: str, wert: Any, admin_user_id: uuid.UUID | None = None) -> None:
    typ, default = DEFAULTS[schluessel]
    zeile = db.get(Konfiguration, schluessel)
    vorher = zeile.wert if zeile else str(default)
    neu = str(_parse(typ, str(wert)))
    if zeile:
        zeile.wert = neu
    else:
        db.add(Konfiguration(schluessel=schluessel, wert=neu, typ=_TYP_NAME[typ]))
    audit.protokolliere(
        db,
        quelle="admin",
        objekt_typ="konfiguration",
        objekt_id=None,
        vorher={"wert": vorher},
        nachher={"wert": neu, "schluessel": schluessel},
        admin_user_id=admin_user_id,
    )
