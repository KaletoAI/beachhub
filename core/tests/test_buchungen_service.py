from datetime import date, time
from decimal import Decimal

import pytest
from beachhub_core import clock
from beachhub_core.models import (
    Audit,
    Betriebszeit,
    Feld,
    FeldRaster,
    Kundengruppe,
    Sperre,
    Tarif,
)
from beachhub_core.services import buchungen, kunden, pin
from beachhub_shared.zeit import kombiniere
from sqlalchemy.orm import Session

D = date(2027, 12, 1)


@pytest.fixture
def welt(db: Session):
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    g = Kundengruppe(name="Privat")
    db.add_all([f, g, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    k = kunden.lege_an(db, name="A", email="a@x.de", kundengruppe_id=g.id)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    return f, k


def test_anlegen_setzt_preis_pin_status(db: Session, welt) -> None:
    f, k = welt
    b = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k.id,
        beginn=kombiniere(D, time(19)),
        ende=kombiniere(D, time(21)),
    )
    db.commit()
    assert b.status == "bestaetigt" and b.preis == Decimal("60.00") and b.zahlungsart == "online"
    assert b.pin_hash == pin.hash(pin.entschluessele(b.pin_verschluesselt))
    assert db.query(Audit).filter_by(objekt_typ="buchung", objekt_id=b.id).count() == 1


@pytest.mark.parametrize(
    "von,bis,grund",
    [
        (time(19, 30), time(21), "ausserhalb_betriebszeit"),  # keine Slotfolge
        (time(22), time(23, 30), "ausserhalb_betriebszeit"),
    ],
)
def test_zeitraum_muss_slotfolge_sein(db: Session, welt, von, bis, grund) -> None:
    f, k = welt
    with pytest.raises(buchungen.BuchungsFehler) as e:
        buchungen.lege_an(
            db, feld_id=f.id, kunde_id=k.id, beginn=kombiniere(D, von), ende=kombiniere(D, bis)
        )
    assert e.value.grund == grund


def test_kollision_mit_buchung_und_sperre(db: Session, welt) -> None:
    f, k = welt
    buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k.id,
        beginn=kombiniere(D, time(19)),
        ende=kombiniere(D, time(21)),
    )
    db.add(
        Sperre(
            feld_id=None,
            beginn=kombiniere(D, time(9)),
            ende=kombiniere(D, time(12)),
            grund="Wartung",
        )
    )
    db.commit()
    with pytest.raises(buchungen.BuchungsFehler, match="belegt"):
        buchungen.lege_an(
            db,
            feld_id=f.id,
            kunde_id=k.id,
            beginn=kombiniere(D, time(20)),
            ende=kombiniere(D, time(22)),
        )
    with pytest.raises(buchungen.BuchungsFehler, match="belegt"):
        buchungen.lege_an(
            db,
            feld_id=f.id,
            kunde_id=k.id,
            beginn=kombiniere(D, time(10)),
            ende=kombiniere(D, time(11)),
        )
    kol = buchungen.finde_kollisionen(
        db, feld_id=f.id, beginn=kombiniere(D, time(11)), ende=kombiniere(D, time(20))
    )
    assert len(kol) == 2


def test_fenster_nur_wenn_gefordert(db: Session, welt) -> None:
    f, k = welt  # Override 25.11., Fenster 14 Tage → 1.12. liegt drin, 20.12. nicht
    weit = date(2027, 12, 20)
    buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k.id,
        beginn=kombiniere(weit, time(19)),
        ende=kombiniere(weit, time(20)),
    )
    with pytest.raises(buchungen.BuchungsFehler, match="ausserhalb_fenster"):
        buchungen.lege_an(
            db,
            feld_id=f.id,
            kunde_id=k.id,
            beginn=kombiniere(weit, time(20)),
            ende=kombiniere(weit, time(21)),
            pruefe_fenster=True,
        )


def test_vergangenheit_und_kein_tarif(db: Session, welt) -> None:
    f, k = welt
    with pytest.raises(buchungen.BuchungsFehler, match="vergangenheit"):
        buchungen.lege_an(
            db,
            feld_id=f.id,
            kunde_id=k.id,
            beginn=kombiniere(date(2027, 11, 1), time(19)),
            ende=kombiniere(date(2027, 11, 1), time(20)),
        )
    db.query(Tarif).delete()
    db.commit()
    with pytest.raises(buchungen.BuchungsFehler, match="kein_tarif"):
        buchungen.lege_an(
            db,
            feld_id=f.id,
            kunde_id=k.id,
            beginn=kombiniere(D, time(19)),
            ende=kombiniere(D, time(20)),
        )
