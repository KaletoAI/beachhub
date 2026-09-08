from datetime import date, time
from decimal import Decimal

import pytest
from beachhub_core import clock
from beachhub_core.models import Betriebszeit, Feld, FeldRaster, Kundengruppe, Sperre, Tarif
from beachhub_core.services import buchungen, kunden, sperren
from beachhub_shared.zeit import kombiniere
from sqlalchemy.orm import Session

D = date(2027, 12, 1)


@pytest.fixture
def welt(db: Session):
    f1, f2 = Feld(name="F1", reihenfolge=1), Feld(name="F2", reihenfolge=2)
    for f in (f1, f2):
        f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    g = Kundengruppe(name="Privat")
    db.add_all([f1, f2, g, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    k = kunden.lege_an(db, name="A", email="a@x.de", kundengruppe_id=g.id)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    return f1, f2, k


def test_entscheidung_pflicht_und_stornieren(db: Session, welt) -> None:
    f1, f2, k = welt
    b = buchungen.lege_an(
        db,
        feld_id=f1.id,
        kunde_id=k.id,
        beginn=kombiniere(D, time(19)),
        ende=kombiniere(D, time(21)),
    )
    db.commit()
    with pytest.raises(sperren.SperrenFehler, match="entscheidung_fehlt"):
        sperren.lege_an(
            db,
            feld_ids=None,
            beginn=kombiniere(D, time(18)),
            ende=kombiniere(D, time(23)),
            grund="Turnier",
            admin_user_id=None,
            entscheidungen={},
        )
    s = sperren.lege_an(
        db,
        feld_ids=None,
        beginn=kombiniere(D, time(18)),
        ende=kombiniere(D, time(23)),
        grund="Turnier",
        admin_user_id=None,
        entscheidungen={b.id: "stornieren"},
    )
    db.commit()
    assert len(s) == 1 and s[0].feld_id is None and b.status == "storniert"
    with pytest.raises(buchungen.BuchungsFehler, match="belegt"):
        buchungen.lege_an(
            db,
            feld_id=f2.id,
            kunde_id=k.id,
            beginn=kombiniere(D, time(20)),
            ende=kombiniere(D, time(21)),
        )


def test_behalten_laesst_buchung_stehen(db: Session, welt) -> None:
    f1, f2, k = welt
    b = buchungen.lege_an(
        db,
        feld_id=f1.id,
        kunde_id=k.id,
        beginn=kombiniere(D, time(19)),
        ende=kombiniere(D, time(21)),
    )
    s = sperren.lege_an(
        db,
        feld_ids=[f1.id, f2.id],
        beginn=kombiniere(D, time(19)),
        ende=kombiniere(D, time(21)),
        grund="Wartung",
        admin_user_id=None,
        entscheidungen={b.id: "behalten"},
    )
    db.commit()
    assert len(s) == 2 and b.status == "bestaetigt"
    sperren.loesche(db, s[0], admin_user_id=None)
    db.commit()
    assert db.query(Sperre).count() == 1


def test_ueberlappende_sperre_wirft_fachfehler(db: Session, welt) -> None:
    f1, f2, k = welt
    # Create initial Sperre on f1 19–21
    _ = sperren.lege_an(
        db,
        feld_ids=[f1.id],
        beginn=kombiniere(D, time(19)),
        ende=kombiniere(D, time(21)),
        grund="Initial",
        admin_user_id=None,
        entscheidungen={},
    )
    db.commit()
    assert db.query(Sperre).count() == 1
    # Try to create overlapping Sperre on f1 20–22, should raise ueberlappt
    with pytest.raises(sperren.SperrenFehler, match="ueberlappt"):
        sperren.lege_an(
            db,
            feld_ids=[f1.id],
            beginn=kombiniere(D, time(20)),
            ende=kombiniere(D, time(22)),
            grund="Overlapping",
            admin_user_id=None,
            entscheidungen={},
        )
    db.commit()
    assert db.query(Sperre).count() == 1
    # Try to create hall-wide Sperre overlapping the field Sperre, should also raise
    with pytest.raises(sperren.SperrenFehler, match="ueberlappt"):
        sperren.lege_an(
            db,
            feld_ids=None,
            beginn=kombiniere(D, time(20)),
            ende=kombiniere(D, time(22)),
            grund="Hall-wide overlapping",
            admin_user_id=None,
            entscheidungen={},
        )
    db.commit()
    assert db.query(Sperre).count() == 1
