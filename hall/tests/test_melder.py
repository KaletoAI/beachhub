import asyncio
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from beachhub_hall import plan
from beachhub_hall.aufgaben.melder import Melder, baue_status
from beachhub_hall.clock import SimulierteUhr
from beachhub_hall.config import FeldZuordnung, HeizungKonfig, TuerKonfig, Zuordnung
from beachhub_hall.db import lies, schreibe
from beachhub_hall.ereignisse import Ereignisse
from beachhub_hall.lage import Lage
from sqlalchemy.orm import Session, sessionmaker

from tests.core_simulator import CoreSimulator
from tests.hilfen import F1, F2, MASTER_HASH, baue_plan, signiertes_dokument, t

ZUORDNUNG = Zuordnung(
    master_pin_hash=MASTER_HASH,
    felder={
        F1: FeldZuordnung("light.feld_1", "binary_sensor.praesenz_feld_1"),
        F2: FeldZuordnung("light.feld_2", None),
    },
    heizung=HeizungKonfig("climate.halle"),
    tuer=TuerKonfig("lock.eingang", kontakt="binary_sensor.tuer"),
)


class Aufbau:
    def __init__(self, sitzungen: sessionmaker[Session], uhr: SimulierteUhr) -> None:
        self.sitzungen, self.uhr = sitzungen, uhr
        self.core = CoreSimulator()
        self.client = self.core.client()
        self.ereignisse = Ereignisse(sitzungen, uhr)
        self.lage = Lage()
        self.plan_wecker = asyncio.Event()
        self.melder = Melder(
            sitzungen,
            uhr,
            self.client,
            self.ereignisse,
            lambda: baue_status(sitzungen, ZUORDNUNG, self.lage, self.ereignisse),
            self.plan_wecker,
        )


@pytest.fixture
async def a(sitzungen: sessionmaker[Session], uhr: SimulierteUhr) -> AsyncIterator[Aufbau]:
    aufbau = Aufbau(sitzungen, uhr)
    yield aufbau
    await aufbau.client.schliesse()


async def test_sendet_ereignisse_und_status(a: Aufbau) -> None:
    a.ereignisse.melde("dienst_gestartet", version="0.1.0")
    a.ereignisse.melde("licht_geschaltet", feld_id=F1, an=True)
    antwort = await a.melder.einmal()
    assert antwort is not None and antwort.bestaetigt_bis == 2
    assert a.core.typen() == ["dienst_gestartet", "licht_geschaltet"]
    assert a.ereignisse.offen() == 0
    assert a.core.status[-1].warteschlange == 2  # Stand beim Absenden
    with a.sitzungen() as db:
        assert lies(db, "letzter_kontakt") == t(17).isoformat()


async def test_offline_mit_backoff_nach_der_uhr(a: Aufbau) -> None:
    a.ereignisse.melde("dienst_gestartet")
    a.core.offline = True
    assert await a.melder.einmal() is None
    anfragen = a.core.anfragen
    assert await a.melder.einmal() is None
    assert a.core.anfragen == anfragen  # innerhalb des Backoffs kein neuer Versuch
    a.uhr.vor(seconds=2)
    a.core.offline = False
    assert await a.melder.einmal() is not None
    assert a.ereignisse.offen() == 0


async def test_plan_neu_weckt_den_plan_abruf(a: Aufbau) -> None:
    a.core.plan_neu = True
    await a.melder.einmal()
    assert a.plan_wecker.is_set()


async def test_mehr_als_200_ereignisse(a: Aufbau) -> None:
    for _ in range(250):
        a.ereignisse.melde("pin_abgelehnt")
    a.ereignisse.neu.clear()
    await a.melder.einmal()
    assert len(a.core.empfangen) == 200 and a.ereignisse.neu.is_set()
    await a.melder.leeren()
    assert [e.seq for e in a.core.empfangen] == list(range(1, 251))
    assert a.ereignisse.offen() == 0


async def test_leeren_bricht_ab_wenn_offline(a: Aufbau) -> None:
    a.ereignisse.melde("dienst_gestartet")
    a.core.offline = True
    await asyncio.wait_for(a.melder.leeren(), timeout=2)
    assert a.ereignisse.offen() == 1


async def test_status_inhalt(a: Aufbau) -> None:
    dok = signiertes_dokument(baue_plan([]), 4, a.core.privat)
    with a.sitzungen() as db:
        plan.speichere(db, dok, baue_plan([]), t(16))
        schreibe(db, "handbetrieb", True)
        schreibe(db, "letzter_abruf", t(16, 30).isoformat())
        schreibe(
            db,
            "praesenz",
            {F1: {"seit": t(16).isoformat(), "ohne_buchung_seit": None, "alarm": False}},
        )
        db.commit()
    a.lage.ha_verbunden = True
    a.lage.soll_heizung = Decimal("18.0")
    a.lage.ist = {
        "light.feld_1": {"state": "on"},
        "light.feld_2": {"state": "off"},
        "climate.halle": {"state": "heat", "attributes": {"current_temperature": 12.5}},
        "lock.eingang": {"state": "locked"},
        "binary_sensor.tuer": {"state": "off"},
    }
    s = baue_status(a.sitzungen, ZUORDNUNG, a.lage, a.ereignisse)
    assert s.planversion == 4 and s.handbetrieb is True and s.ha_erreichbar is True
    assert s.letzter_abruf == t(16, 30)
    felder = {f.feld_id: f for f in s.felder}
    assert felder[F1].licht_ist is True and felder[F1].praesenz is True
    assert felder[F2].licht_ist is False and felder[F2].praesenz is None
    assert s.heizung.soll == Decimal("18.0") and s.heizung.ist == Decimal("12.5")
    assert s.tuer.verriegelt is True and s.tuer.offen is False
    assert s.version_dienst == "0.1.0"


def test_lage_temperatur_robust() -> None:
    lage = Lage(
        ist={"sensor.t": {"state": "unavailable"}, "climate.h": {"state": "heat", "attributes": {}}}
    )
    assert lage.temperatur(HeizungKonfig("climate.h", "sensor.t")) is None
    assert lage.temperatur(HeizungKonfig("climate.h")) is None
    lage.ist["sensor.t"] = {"state": "nan"}
    assert lage.temperatur(HeizungKonfig("climate.h", "sensor.t")) is None
    lage.ist["sensor.t"] = {"state": "17.25"}
    assert lage.temperatur(HeizungKonfig("climate.h", "sensor.t")) == Decimal("17.25")
    assert lage.ist_an(None) is None and lage.ist_an("light.unbekannt") is None
