import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from beachhub_core.models import Audit


def _json(v: Any) -> Any:
    if isinstance(v, Decimal | uuid.UUID):
        return str(v)
    if isinstance(v, datetime | date | time):
        return v.isoformat()
    return v


def als_dict(obj: Any) -> dict[str, Any]:
    """Spaltenwerte eines Modells als JSON-fähiges dict (ohne created_at/updated_at)."""
    mapper = inspect(obj).mapper
    return {
        c.key: _json(getattr(obj, c.key))
        for c in mapper.column_attrs
        if c.key not in ("created_at", "updated_at", "pin_hash", "pin_verschluesselt")
    }


def protokolliere(
    db: Session,
    *,
    quelle: str,
    objekt_typ: str,
    objekt_id: uuid.UUID | None,
    vorher: dict[str, Any] | None,
    nachher: dict[str, Any] | None,
    admin_user_id: uuid.UUID | None = None,
) -> Audit:
    eintrag = Audit(
        quelle=quelle,
        objekt_typ=objekt_typ,
        objekt_id=objekt_id,
        vorher_json=vorher,
        nachher_json=nachher,
        admin_user_id=admin_user_id,
    )
    db.add(eintrag)
    return eintrag
