from datetime import date, time
from decimal import Decimal

import pytest
from beachhub_core.models import Betriebszeit, Feld, FeldRaster, Kundengruppe, Tarif
from beachhub_core.services import tarife
from beachhub_shared.zeit import kombiniere
from sqlalchemy.orm import Session

MI = date(2027, 12, 1)


@pytest.fixture
def basis(db: Session) -> tuple[Feld, Kundengruppe, Kundengruppe]:
    f = Feld(name="Feld 1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    privat, verein = (
        Kundengruppe(name="Privat"),
        Kundengruppe(name="Verein", standard_zahlungsart="rechnung"),
    )
    db.add_all([f, privat, verein])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.commit()
    return f, privat, verein


def test_summe_ueber_slots_und_spezifischste_regel(db: Session, basis) -> None:
    f, privat, verein = basis
    db.add_all(
        [
            Tarif(name="Standard", preis=Decimal("30.00")),
            Tarif(name="Abend", preis=Decimal("40.00"), uhrzeit_von=time(18), uhrzeit_bis=time(23)),
            Tarif(
                name="Verein Abend",
                preis=Decimal("35.00"),
                uhrzeit_von=time(18),
                uhrzeit_bis=time(23),
                kundengruppe_id=verein.id,
            ),
        ]
    )
    db.commit()
    p = tarife.ermittle_preis(
        db,
        feld_id=f.id,
        beginn=kombiniere(MI, time(17)),
        ende=kombiniere(MI, time(19)),
        kundengruppe_id=privat.id,
    )
    assert p == Decimal("70.00")  # 17-18 Standard 30 + 18-19 Abend 40
    p = tarife.ermittle_preis(
        db,
        feld_id=f.id,
        beginn=kombiniere(MI, time(18)),
        ende=kombiniere(MI, time(20)),
        kundengruppe_id=verein.id,
    )
    assert p == Decimal("70.00")  # 2 × Verein Abend


def test_keine_regel_ergibt_none(db: Session, basis) -> None:
    f, privat, _ = basis
    assert (
        tarife.ermittle_preis(
            db,
            feld_id=f.id,
            beginn=kombiniere(MI, time(17)),
            ende=kombiniere(MI, time(18)),
            kundengruppe_id=privat.id,
        )
        is None
    )


def test_keine_slotfolge_ergibt_none(db: Session, basis) -> None:
    f, privat, _ = basis
    db.add(Tarif(name="Standard", preis=Decimal("30.00")))
    db.commit()
    assert (
        tarife.ermittle_preis(
            db,
            feld_id=f.id,
            beginn=kombiniere(MI, time(17, 30)),
            ende=kombiniere(MI, time(18, 30)),
            kundengruppe_id=privat.id,
        )
        is None
    )


def test_gueltigkeit_und_inaktiv(db: Session, basis) -> None:
    f, privat, _ = basis
    db.add_all(
        [
            Tarif(name="Alt", preis=Decimal("10.00"), gueltig_bis=date(2027, 11, 30)),
            Tarif(name="Aus", preis=Decimal("1.00"), aktiv=False),
            Tarif(name="Neu", preis=Decimal("20.00"), gueltig_von=date(2027, 12, 1)),
        ]
    )
    db.commit()
    assert tarife.ermittle_preis(
        db,
        feld_id=f.id,
        beginn=kombiniere(MI, time(17)),
        ende=kombiniere(MI, time(18)),
        kundengruppe_id=privat.id,
    ) == Decimal("20.00")
