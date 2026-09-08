import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from beachhub_core.models import Buchung, Sperre
from beachhub_core.services import audit


class SperrenFehler(Exception):  # noqa: N818
    pass


def _nicht_verdrahtet(db: Session, buchung: Buchung, **kw: Any) -> None:
    raise RuntimeError(
        "sperren.STORNIERE ist nicht gesetzt (storno-Service registriert sich in Task 11)"
    )


STORNIERE: Callable[..., Any] = _nicht_verdrahtet


def betroffene_buchungen(
    db: Session, *, feld_ids: list[uuid.UUID] | None, beginn: datetime, ende: datetime
) -> list[Buchung]:
    q = select(Buchung).where(
        Buchung.status.in_(Buchung.AKTIVE_STATUS), Buchung.beginn < ende, Buchung.ende > beginn
    )
    if feld_ids is not None:
        q = q.where(Buchung.feld_id.in_(feld_ids))
    return list(db.scalars(q.order_by(Buchung.beginn)).all())


def lege_an(
    db: Session,
    *,
    feld_ids: list[uuid.UUID] | None,
    beginn: datetime,
    ende: datetime,
    grund: str,
    admin_user_id: uuid.UUID | None,
    entscheidungen: dict[uuid.UUID, str],
) -> list[Sperre]:
    if ende <= beginn:
        raise SperrenFehler("zeitraum_ungueltig")
    # Check for overlapping Sperren before writing anything
    overlap_query = select(Sperre).where(Sperre.beginn < ende, Sperre.ende > beginn)
    if feld_ids is not None:
        overlap_query = overlap_query.where(
            or_(Sperre.feld_id.in_(feld_ids), Sperre.feld_id.is_(None))
        )
    if db.scalars(overlap_query).first() is not None:
        raise SperrenFehler("ueberlappt")
    betroffen = betroffene_buchungen(db, feld_ids=feld_ids, beginn=beginn, ende=ende)
    for b in betroffen:
        if entscheidungen.get(b.id) not in ("behalten", "stornieren"):
            raise SperrenFehler("entscheidung_fehlt")
    ergebnis: list[Sperre] = []
    fids: list[uuid.UUID | None] = list(feld_ids) if feld_ids is not None else [None]
    for fid in fids:
        s = Sperre(feld_id=fid, beginn=beginn, ende=ende, grund=grund)
        db.add(s)
        db.flush()
        audit.protokolliere(
            db,
            quelle="admin",
            objekt_typ="sperre",
            objekt_id=s.id,
            vorher=None,
            nachher=audit.als_dict(s),
            admin_user_id=admin_user_id,
        )
        ergebnis.append(s)
    for b in betroffen:
        if entscheidungen[b.id] == "stornieren":
            STORNIERE(
                db,
                b,
                durch="betreiber",
                kostenfrei=True,
                grund=f"Sperre: {grund}",
                admin_user_id=admin_user_id,
            )
    return ergebnis


def loesche(db: Session, sperre: Sperre, admin_user_id: uuid.UUID | None) -> None:
    audit.protokolliere(
        db,
        quelle="admin",
        objekt_typ="sperre",
        objekt_id=sperre.id,
        vorher=audit.als_dict(sperre),
        nachher=None,
        admin_user_id=admin_user_id,
    )
    db.delete(sperre)
    db.flush()
