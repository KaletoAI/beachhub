import uuid
from datetime import date, time
from decimal import Decimal, InvalidOperation
from typing import TypeVar

T = TypeVar("T")


def pflicht(wert: T | None, name: str) -> T:
    if wert is None:
        raise ValueError(f"{name} fehlt")
    return wert


def t_zeit(v: str | None) -> time | None:
    v = (v or "").strip()
    if not v:
        return None
    try:
        h, m = v.split(":")
        return time(int(h), int(m))
    except ValueError as e:
        raise ValueError("Uhrzeit bitte als HH:MM angeben") from e


def t_datum(v: str | None) -> date | None:
    v = (v or "").strip()
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError as e:
        raise ValueError("Datum bitte als JJJJ-MM-TT angeben") from e


def t_int(v: str | None) -> int | None:
    v = (v or "").strip()
    if not v:
        return None
    try:
        return int(v)
    except ValueError as e:
        raise ValueError("Bitte eine ganze Zahl angeben") from e


def t_uuid(v: str | None) -> uuid.UUID | None:
    v = (v or "").strip()
    if not v:
        return None
    try:
        return uuid.UUID(v)
    except ValueError as e:
        raise ValueError("Ungültige Auswahl") from e


def t_betrag(v: str | None) -> Decimal | None:
    v = (
        (v or "").strip().replace(".", "").replace(",", ".")
        if v and "," in v
        else (v or "").strip()
    )
    if not v:
        return None
    try:
        return Decimal(v).quantize(Decimal("0.01"))
    except InvalidOperation as e:
        raise ValueError("Betrag ungültig") from e


def t_fenster(v: str | None) -> list[tuple[str, str]]:
    out = []
    for teil in (v or "").split(","):
        teil = teil.strip()
        if not teil:
            continue
        try:
            a, b = teil.split("-")
        except ValueError as e:
            raise ValueError(
                "Zeitfenster bitte als HH:MM-HH:MM angeben, mehrere durch Komma getrennt"
            ) from e
        out.append((a.strip(), b.strip()))
    return out
