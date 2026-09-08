import uuid
from datetime import date, time
from decimal import Decimal, InvalidOperation


def t_zeit(v: str | None) -> time | None:
    v = (v or "").strip()
    if not v:
        return None
    h, m = v.split(":")
    return time(int(h), int(m))


def t_datum(v: str | None) -> date | None:
    v = (v or "").strip()
    return date.fromisoformat(v) if v else None


def t_int(v: str | None) -> int | None:
    v = (v or "").strip()
    return int(v) if v else None


def t_uuid(v: str | None) -> uuid.UUID | None:
    v = (v or "").strip()
    return uuid.UUID(v) if v else None


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
        a, b = teil.split("-")
        out.append((a.strip(), b.strip()))
    return out
