import json
from decimal import Decimal

import pytest
from beachhub_hall.clock import SimulierteUhr
from beachhub_hall.db import EreignisZeile
from beachhub_hall.ereignisse import Ereignisse
from sqlalchemy.orm import Session, sessionmaker

from tests.hilfen import F1


def test_melden_und_ausliefern(sitzungen: sessionmaker[Session], uhr: SimulierteUhr) -> None:
    e = Ereignisse(sitzungen, uhr)
    assert not e.neu.is_set()
    assert e.melde("dienst_gestartet", version="0.1.0") == 1
    assert e.neu.is_set()
    uhr.vor(minutes=1)
    assert e.melde("heizung_gesetzt", soll=Decimal("18.0")) == 2
    assert e.melde("licht_geschaltet", feld_id=F1, an=True) == 3
    liste = e.unbestaetigt()
    assert [x.seq for x in liste] == [1, 2, 3]
    assert liste[1].daten == {"soll": "18.0"} and liste[1].zeitpunkt == uhr.jetzt()
    assert liste[2].feld_id == F1
    assert [x.seq for x in e.unbestaetigt(limit=2)] == [1, 2]
    assert e.offen() == 3
    e.bestaetige_bis(2)
    assert e.offen() == 1 and [x.seq for x in e.unbestaetigt()] == [3]


def test_unbekannter_typ(sitzungen: sessionmaker[Session], uhr: SimulierteUhr) -> None:
    with pytest.raises(ValueError, match="gibt_es_nicht"):
        Ereignisse(sitzungen, uhr).melde("gibt_es_nicht")


def test_aufraeumen_nur_bestaetigte_nach_90_tagen(
    sitzungen: sessionmaker[Session], uhr: SimulierteUhr
) -> None:
    e = Ereignisse(sitzungen, uhr)
    e.melde("dienst_gestartet")
    e.melde("dienst_gestartet")
    e.bestaetige_bis(1)
    uhr.vor(days=89)
    assert e.raeume_auf() == 0
    uhr.vor(days=2)
    assert e.raeume_auf() == 1
    with sitzungen() as db:
        rest = db.query(EreignisZeile).all()
        assert [z.seq for z in rest] == [2]
        assert json.loads(rest[0].daten_json) == {}
    assert e.offen() == 1
