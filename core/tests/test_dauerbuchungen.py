from datetime import date, time
from decimal import Decimal

import pytest
from beachhub_core import clock
from beachhub_core.models import (
    Betriebszeit,
    Buchung,
    Dauerbuchung,
    Feld,
    FeldRaster,
    Kundengruppe,
    Sperre,
    Tarif,
)
from beachhub_core.services import buchungen, dauerbuchungen, kunden, pin
from beachhub_shared.zeit import kombiniere
from sqlalchemy.orm import Session


@pytest.fixture
def welt(db: Session):
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    g = Kundengruppe(name="Verein", standard_zahlungsart="rechnung")
    db.add_all([f, g, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    k = kunden.lege_an(db, name="TSV", email="tsv@x.de", kundengruppe_id=g.id)
    k2 = kunden.lege_an(db, name="B", email="b@x.de", kundengruppe_id=g.id)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    return f, k, k2


def test_planen_zeigt_termine_und_kollisionen(db: Session, welt) -> None:
    f, k, k2 = welt
    d = date(2027, 12, 7)  # Dienstag
    buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k2.id,
        beginn=kombiniere(d, time(19)),
        ende=kombiniere(d, time(20)),
    )
    db.add(
        Sperre(
            feld_id=f.id,
            beginn=kombiniere(date(2027, 12, 21), time(9)),
            ende=kombiniere(date(2027, 12, 22), time(9)),
            grund="X",
        )
    )
    db.commit()
    plan = dauerbuchungen.plane(
        db,
        kunde_id=k.id,
        feld_id=f.id,
        wochentag=1,
        start=time(19),
        ende=time(21),
        gueltig_von=date(2027, 12, 1),
        gueltig_bis=date(2027, 12, 31),
    )
    assert [t.datum for t in plan] == [
        date(2027, 12, 7),
        date(2027, 12, 14),
        date(2027, 12, 21),
        date(2027, 12, 28),
    ]
    assert len(plan[0].kollisionen) == 1 and isinstance(plan[2].kollisionen[0], Sperre)
    assert plan[1].preis == Decimal("60.00")


def test_anlegen_mit_auslassen_und_gemeinsamer_pin(db: Session, welt) -> None:
    f, k, k2 = welt
    d = date(2027, 12, 7)
    fremd = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k2.id,
        beginn=kombiniere(d, time(19)),
        ende=kombiniere(d, time(20)),
    )
    db.commit()
    with pytest.raises(dauerbuchungen.DauerbuchungsFehler, match="entscheidung_fehlt"):
        dauerbuchungen.lege_an(
            db,
            kunde_id=k.id,
            feld_id=f.id,
            wochentag=1,
            start=time(19),
            ende=time(21),
            gueltig_von=date(2027, 12, 1),
            gueltig_bis=date(2027, 12, 31),
            admin_user_id=None,
            auslassen=set(),
            entscheidungen={},
        )
    db.rollback()
    dauer = dauerbuchungen.lege_an(
        db,
        kunde_id=k.id,
        feld_id=f.id,
        wochentag=1,
        start=time(19),
        ende=time(21),
        gueltig_von=date(2027, 12, 1),
        gueltig_bis=date(2027, 12, 31),
        admin_user_id=None,
        auslassen={date(2027, 12, 28)},
        entscheidungen={fremd.id: "stornieren"},
    )
    db.commit()
    assert len(dauer.buchungen) == 3
    assert {b.pin_hash for b in dauer.buchungen} == {dauer.pin_hash}
    assert db.get(Buchung, fremd.id).status == "storniert"
    assert all(b.zahlungsart == "rechnung" and b.quelle == "dauer" for b in dauer.buchungen)
    assert pin.entschluessele(dauer.pin_verschluesselt) == pin.entschluessele(
        dauer.buchungen[0].pin_verschluesselt
    )


def test_anlegen_ohne_tarif_wirft_und_schreibt_nichts(db: Session, welt) -> None:
    f, k, _ = welt
    db.query(Tarif).delete()
    db.commit()
    with pytest.raises(dauerbuchungen.DauerbuchungsFehler, match="kein_tarif"):
        dauerbuchungen.lege_an(
            db,
            kunde_id=k.id,
            feld_id=f.id,
            wochentag=1,
            start=time(19),
            ende=time(21),
            gueltig_von=date(2027, 12, 1),
            gueltig_bis=date(2027, 12, 31),
            admin_user_id=None,
            auslassen=set(),
            entscheidungen={},
        )
    db.rollback()
    assert db.query(Dauerbuchung).count() == 0
    assert db.query(Buchung).count() == 0


def test_beenden_storniert_kuenftige(db: Session, welt) -> None:
    f, k, _ = welt
    dauer = dauerbuchungen.lege_an(
        db,
        kunde_id=k.id,
        feld_id=f.id,
        wochentag=1,
        start=time(19),
        ende=time(21),
        gueltig_von=date(2027, 12, 1),
        gueltig_bis=date(2027, 12, 31),
        admin_user_id=None,
        auslassen=set(),
        entscheidungen={},
    )
    db.commit()
    dauerbuchungen.beende(db, dauer, ab=date(2027, 12, 20), admin_user_id=None)
    db.commit()
    status = [b.status for b in dauer.buchungen]
    assert status == ["bestaetigt", "bestaetigt", "storniert", "storniert"]
    assert dauer.beendet_ab == date(2027, 12, 20) and dauer.beendet_am is not None
