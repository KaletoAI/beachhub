"""Anfragetabelle des Portals: der Briefkasten für das Hauptsystem (Spec Portal-Kern § 2).

Das Portal entscheidet nichts. Es legt Anfragen ab, liefert sie aus und merkt sich die Antwort.
"""

import uuid
from datetime import datetime, timedelta
from typing import Any

from beachhub_shared import kanal
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from beachhub_portal import uhr
from beachhub_portal.models import Anfrage, KanalKontakt, Konto
from beachhub_portal.services import wecker

ERNEUT_NACH = timedelta(seconds=60)
HOECHSTENS = 50


def stelle(
    db: Session, *, typ: str, konto_id: uuid.UUID | None, nutzlast: dict[str, Any]
) -> Anfrage:
    """Legt eine Anfrage an, committet und weckt wartende Long-Polls."""
    schema = kanal.NUTZLAST.get(typ)
    if schema is None:
        raise ValueError(f"Unbekannter Anfragetyp: {typ}")
    schema.model_validate(nutzlast)  # das Portal schickt nur Nutzlasten, die der Vertrag kennt
    a = Anfrage(
        id=uuid.uuid4(),
        typ=typ,
        konto_id=konto_id,
        nutzlast_json=nutzlast,
        erstellt_am=uhr.jetzt(),
        status=Anfrage.OFFEN,
    )
    db.add(a)
    db.commit()
    wecker.wecke()
    return a


def abholen(db: Session, jetzt: datetime) -> list[kanal.Anfrage]:
    zeilen = list(
        db.scalars(
            select(Anfrage)
            .where(
                or_(
                    Anfrage.status == Anfrage.OFFEN,
                    and_(
                        Anfrage.status == Anfrage.ABGEHOLT,
                        Anfrage.abgeholt_am < jetzt - ERNEUT_NACH,
                    ),
                )
            )
            .order_by(Anfrage.erstellt_am)
            .limit(HOECHSTENS)
            .with_for_update(skip_locked=True)
        ).all()
    )
    konto_ids = {z.konto_id for z in zeilen if z.konto_id is not None}
    kunden: dict[uuid.UUID, uuid.UUID | None] = {}
    if konto_ids:
        for konto_id, kunde_id in db.execute(
            select(Konto.id, Konto.kunde_id).where(Konto.id.in_(konto_ids))
        ).tuples():
            kunden[konto_id] = kunde_id
    liste: list[kanal.Anfrage] = []
    for z in zeilen:
        z.status = Anfrage.ABGEHOLT
        z.abgeholt_am = jetzt
        liste.append(
            kanal.Anfrage(
                anfrage_id=z.id,
                typ=z.typ,
                konto_id=z.konto_id,
                kunde_id=kunden.get(z.konto_id) if z.konto_id else None,
                nutzlast=z.nutzlast_json,
                erstellt_am=z.erstellt_am,
            )
        )
    db.merge(KanalKontakt(id=1, letzter_abruf=jetzt))
    db.commit()
    return liste


def beantworte(db: Session, anfrage_id: uuid.UUID, antwort: kanal.Antwort, jetzt: datetime) -> bool:
    a = db.get(Anfrage, anfrage_id, with_for_update=True)
    if a is None or a.status == Anfrage.BEANTWORTET:
        return False
    daten = antwort.model_dump(mode="json", exclude_none=True)
    if a.typ == "konto_angelegt" and antwort.status == "ok" and antwort.kunde_id and a.konto_id:
        konto = db.get(Konto, a.konto_id)
        if konto is not None:
            konto.kunde_id = antwort.kunde_id
    a.status = Anfrage.BEANTWORTET
    a.antwort_json = daten
    a.beantwortet_am = jetzt
    return True
