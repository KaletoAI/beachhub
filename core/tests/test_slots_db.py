from datetime import date, time

from beachhub_core.models import Ausnahmetag, Betriebszeit, Feld, FeldRaster
from beachhub_core.services import slots_db
from sqlalchemy.orm import Session


def test_tages_slots_aus_datenbank(db: Session) -> None:
    f = Feld(name="Feld 1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    f.raster.append(
        FeldRaster(
            wochentag=2,
            modus="fenster",
            slot_minuten=None,
            fenster_json=[["19:00", "21:00"], ["21:00", "23:00"]],
        )
    )
    db.add_all(
        [
            f,
            Betriebszeit(wochentag=2, oeffnet=time(17), schliesst=time(23)),
            Betriebszeit(
                wochentag=3, oeffnet=time(17), schliesst=time(23), gueltig_bis=date(2027, 11, 30)
            ),
            Ausnahmetag(datum=date(2027, 12, 8), geschlossen=True),
        ]
    )
    db.commit()
    assert len(slots_db.tages_slots(db, f, date(2027, 12, 1))) == 2  # Mittwoch: Fenster
    assert (
        slots_db.tages_slots(db, f, date(2027, 12, 2)) == []
    )  # Donnerstag: Betriebszeit abgelaufen
    assert slots_db.tages_slots(db, f, date(2027, 12, 8)) == []  # Ausnahmetag
