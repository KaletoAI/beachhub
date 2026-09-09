from dataclasses import dataclass
from datetime import date, time, timedelta

from beachhub_shared.slots import Slot
from beachhub_shared.zeit import kombiniere
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from beachhub_core.models import Buchung, Feld, Sperre
from beachhub_core.services import slots_db


@dataclass
class Zelle:
    slot: Slot
    art: str  # frei | buchung | sperre
    buchung: Buchung | None = None
    sperre: Sperre | None = None


@dataclass
class Tag:
    datum: date
    zellen: list[Zelle]


def wochenplan(db: Session, feld: Feld, montag: date) -> list[Tag]:
    von, bis = kombiniere(montag, time(0)), kombiniere(montag + timedelta(days=7), time(0))
    buchungen = db.scalars(
        select(Buchung).where(
            Buchung.feld_id == feld.id,
            Buchung.status.in_(Buchung.AKTIVE_STATUS),
            Buchung.beginn < bis,
            Buchung.ende > von,
        )
    ).all()
    sperren = db.scalars(
        select(Sperre).where(
            or_(Sperre.feld_id == feld.id, Sperre.feld_id.is_(None)),
            Sperre.beginn < bis,
            Sperre.ende > von,
        )
    ).all()
    tage = []
    for i in range(7):
        d = montag + timedelta(days=i)
        zellen = []
        for slot in slots_db.tages_slots(db, feld, d):
            b = next((x for x in buchungen if x.beginn < slot.ende and x.ende > slot.beginn), None)
            s = next((x for x in sperren if x.beginn < slot.ende and x.ende > slot.beginn), None)
            if b:
                zellen.append(Zelle(slot, "buchung", buchung=b))
            elif s:
                zellen.append(Zelle(slot, "sperre", sperre=s))
            else:
                zellen.append(Zelle(slot, "frei"))
        tage.append(Tag(d, zellen))
    return tage
