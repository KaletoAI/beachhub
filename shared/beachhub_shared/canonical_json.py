"""Kanonische JSON-Serialisierung für Signaturen: gleicher Inhalt → gleiche Bytes."""

import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any


def _konvertiere(o: Any) -> Any:
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, datetime | date):
        return o.isoformat()
    if isinstance(o, uuid.UUID):
        return str(o)
    raise TypeError(f"Nicht serialisierbar: {type(o).__name__}")


def dumps(obj: Any) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_konvertiere
    ).encode("utf-8")
