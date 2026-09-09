import json
from datetime import date, time
from decimal import Decimal
from pathlib import Path

import pytest
from beachhub_core import clock
from beachhub_core.config import settings
from beachhub_core.models import (
    Betriebszeit,
    Feld,
    FeldRaster,
    Kundengruppe,
    LesestandVersion,
    Sperre,
    Tarif,
)
from beachhub_core.services import buchungen, kunden, lesestand, storno
from beachhub_shared.signatur import pruefe
from beachhub_shared.zeit import kombiniere
from sqlalchemy.orm import Session


@pytest.fixture
def welt(db: Session):
    Path(settings.signatur_privatschluessel_pfad).unlink(missing_ok=True)
    lesestand.erzeuge_schluessel()
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    p = Kundengruppe(name="Privat")
    db.add_all([f, p, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    a = kunden.lege_an(db, name="A", email="a@x.de", kundengruppe_id=p.id)
    b = kunden.lege_an(db, name="B", email="b@x.de", kundengruppe_id=p.id)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    return f, a, b


def test_schluessel_nur_einmal(welt) -> None:
    with pytest.raises(FileExistsError):
        lesestand.erzeuge_schluessel()
    assert len(lesestand.oeffentlicher_schluessel()) == 64


def test_belegung_ohne_kundenbezug_und_signiert(db: Session, welt) -> None:
    f, a, _ = welt
    d = date(2027, 12, 1)
    buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=a.id,
        beginn=kombiniere(d, time(19)),
        ende=kombiniere(d, time(21)),
    )
    db.add(
        Sperre(
            feld_id=None,
            beginn=kombiniere(d, time(9)),
            ende=kombiniere(d, time(12)),
            grund="Wartung",
        )
    )
    db.commit()
    dok = lesestand.publiziere(db, "belegung")
    assert dok.version == 1 and pruefe(
        dok.model_dump(mode="json", exclude={"signatur"}),
        dok.signatur,
        lesestand.oeffentlicher_schluessel(),
    )
    belegt = dok.inhalt["belegt"][str(f.id)]
    assert (
        len(belegt) == 2 and "kunde" not in json.dumps(dok.inhalt) and "A" not in json.dumps(belegt)
    )
    assert (
        dok.inhalt["fenster_tage"] == 14
        and dok.inhalt["felder"][0]["raster"][0]["slot_minuten"] == 60
    )
    assert lesestand.publiziere(db, "belegung").version == 2
    assert Path(settings.data_dir, "lesestand", "belegung.json").exists()


def test_konto_enthaelt_eigene_buchungen_mit_pin(db: Session, welt) -> None:
    f, a, b = welt
    d = date(2027, 12, 1)
    ba = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=a.id,
        beginn=kombiniere(d, time(19)),
        ende=kombiniere(d, time(20)),
    )
    buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=b.id,
        beginn=kombiniere(d, time(20)),
        ende=kombiniere(d, time(21)),
    )
    db.commit()
    dok = lesestand.publiziere(db, f"konto:{a.id}")
    assert [x["id"] for x in dok.inhalt["buchungen"]] == [str(ba.id)]
    assert dok.inhalt["buchungen"][0]["pin"].isdigit() and dok.inhalt["guthaben"] == "0.00"


def test_aenderungen_markieren_und_verarbeiten(db: Session, welt) -> None:
    f, a, _ = welt
    d = date(2027, 12, 1)
    bu = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=a.id,
        beginn=kombiniere(d, time(19)),
        ende=kombiniere(d, time(20)),
    )
    db.commit()
    markiert = {v.dokument for v in db.query(LesestandVersion).filter_by(geaendert=True)}
    assert markiert == {"belegung", f"konto:{a.id}"}
    assert sorted(lesestand.verarbeite_geaenderte(db)) == sorted(markiert)
    db.commit()
    assert db.query(LesestandVersion).filter_by(geaendert=True).count() == 0
    storno.storniere(db, bu, durch="kunde")
    db.commit()
    assert db.query(LesestandVersion).filter_by(geaendert=True).count() == 2
