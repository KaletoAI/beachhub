from datetime import date, time

from beachhub_shared import slots as sl
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from beachhub_core.models import Ausnahmetag, Betriebszeit, Feld


def _time(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def raster_konfig(feld: Feld) -> list[sl.RasterKonfig]:
    return [
        sl.RasterKonfig(
            wochentag=r.wochentag,
            modus=r.modus,  # type: ignore[arg-type]
            slot_minuten=r.slot_minuten,
            fenster=[(_time(a), _time(b)) for a, b in r.fenster_json],
        )
        for r in feld.raster
    ]


def betriebszeiten_fuer(db: Session, datum: date) -> list[sl.Betriebszeit]:
    zeilen = db.scalars(
        select(Betriebszeit).where(
            or_(Betriebszeit.gueltig_von.is_(None), Betriebszeit.gueltig_von <= datum),
            or_(Betriebszeit.gueltig_bis.is_(None), Betriebszeit.gueltig_bis >= datum),
        )
    ).all()
    return [sl.Betriebszeit(z.wochentag, z.oeffnet, z.schliesst) for z in zeilen]


def ausnahmen_fuer(db: Session, datum: date) -> list[sl.Ausnahme]:
    a = db.scalar(select(Ausnahmetag).where(Ausnahmetag.datum == datum))
    return [sl.Ausnahme(a.datum, a.geschlossen, a.oeffnet, a.schliesst)] if a else []


def tages_slots(db: Session, feld: Feld, datum: date) -> list[sl.Slot]:
    return sl.slots_fuer_tag(
        datum, raster_konfig(feld), betriebszeiten_fuer(db, datum), ausnahmen_fuer(db, datum)
    )
