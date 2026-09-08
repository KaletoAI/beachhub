import uuid
from datetime import datetime
from decimal import Decimal

from beachhub_shared.slots import Slot, slots_im_zeitraum, zeitraum_ist_slotfolge
from beachhub_shared.zeit import lokal, lokales_datum
from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_core.models import Feld, Tarif
from beachhub_core.services import slots_db


def _passt(t: Tarif, feld_id: uuid.UUID, slot: Slot, kundengruppe_id: uuid.UUID | None) -> bool:
    lok = lokal(slot.beginn)
    if t.feld_id is not None and t.feld_id != feld_id:
        return False
    if t.wochentag is not None and t.wochentag != lok.weekday():
        return False
    if t.uhrzeit_von is not None and t.uhrzeit_bis is not None:
        if not (t.uhrzeit_von <= lok.time() < t.uhrzeit_bis):
            return False
    if t.kundengruppe_id is not None and t.kundengruppe_id != kundengruppe_id:
        return False
    if t.gueltig_von is not None and lok.date() < t.gueltig_von:
        return False
    if t.gueltig_bis is not None and lok.date() > t.gueltig_bis:
        return False
    return True


def _spezifitaet(t: Tarif) -> int:
    return sum(
        [
            t.feld_id is not None,
            t.wochentag is not None,
            t.uhrzeit_von is not None and t.uhrzeit_bis is not None,
            t.kundengruppe_id is not None,
            t.gueltig_von is not None or t.gueltig_bis is not None,
        ]
    )


def regel_fuer_slot(
    db: Session, *, feld_id: uuid.UUID, slot: Slot, kundengruppe_id: uuid.UUID | None
) -> Tarif | None:
    kandidaten = [
        t
        for t in db.scalars(select(Tarif).where(Tarif.aktiv.is_(True))).all()
        if _passt(t, feld_id, slot, kundengruppe_id)
    ]
    if not kandidaten:
        return None
    return max(kandidaten, key=lambda t: (_spezifitaet(t), t.created_at))


def ermittle_preis(
    db: Session,
    *,
    feld_id: uuid.UUID,
    beginn: datetime,
    ende: datetime,
    kundengruppe_id: uuid.UUID | None,
) -> Decimal | None:
    feld = db.get(Feld, feld_id)
    if feld is None:
        return None
    tages = slots_db.tages_slots(db, feld, lokales_datum(beginn))
    if not zeitraum_ist_slotfolge(beginn, ende, tages):
        return None
    summe = Decimal("0.00")
    for slot in slots_im_zeitraum(beginn, ende, tages):
        regel = regel_fuer_slot(db, feld_id=feld_id, slot=slot, kundengruppe_id=kundengruppe_id)
        if regel is None:
            return None
        summe += regel.preis
    return summe
