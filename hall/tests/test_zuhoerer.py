import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from datetime import timedelta

import pytest
from beachhub_hall.aufgaben.ha_zuhoerer import HaZuhoerer
from beachhub_hall.aufgaben.status_ha import StatusHa
from beachhub_hall.clock import SimulierteUhr
from beachhub_hall.config import (
    FeldZuordnung,
    HeizungKonfig,
    TastenfeldKonfig,
    TuerKonfig,
    Zuordnung,
)
from beachhub_hall.db import lies, schreibe
from beachhub_hall.ereignisse import Ereignisse
from beachhub_hall.ha import HaClient
from beachhub_hall.lage import Lage
from beachhub_hall.pin import PinPruefer
from beachhub_hall.tuer import Tuer
from beachhub_shared.hallenplan import PlanBuchung
from sqlalchemy.orm import Session, sessionmaker

from tests.ha_simulator import HaSimulator
from tests.hilfen import F1, F2, MASTER_HASH, MASTER_PIN, FakeSchlaf, buchung, speichere_plan, t

PIN = "482913"
ZUORDNUNG = Zuordnung(
    master_pin_hash=MASTER_HASH,
    felder={
        F1: FeldZuordnung("light.feld_1", "binary_sensor.praesenz_feld_1"),
        F2: FeldZuordnung("light.feld_2", "binary_sensor.praesenz_feld_2"),
    },
    heizung=HeizungKonfig("climate.halle"),
    tuer=TuerKonfig("lock.eingang", kontakt="binary_sensor.tuer"),
    tastenfeld=TastenfeldKonfig("esphome.beachhub_pin", "code", 3),
    handbetrieb="input_boolean.beachhub_handbetrieb",
)


def zustandswechsel(entity_id: str, state: str) -> dict[str, object]:
    return {
        "event_type": "state_changed",
        "data": {
            "entity_id": entity_id,
            "old_state": None,
            "new_state": {"entity_id": entity_id, "state": state, "attributes": {}},
        },
    }


class Aufbau:
    def __init__(
        self,
        sitzungen: sessionmaker[Session],
        uhr: SimulierteUhr,
        ha: HaSimulator,
        url: str | None = None,
    ) -> None:
        self.sitzungen, self.uhr, self.sim = sitzungen, uhr, ha
        self.client = HaClient(url or ha.url, HaSimulator.TOKEN)
        self.ereignisse = Ereignisse(sitzungen, uhr)
        self.lage = Lage()
        self.steuerung_wecker = asyncio.Event()
        self.status_wecker = asyncio.Event()
        schlaf = FakeSchlaf()
        tuer = Tuer(self.client, ZUORDNUNG.tuer, self.ereignisse, schlaf)
        pruefer = PinPruefer(sitzungen, uhr, ZUORDNUNG, self.ereignisse, tuer, schlaf)
        self.zuhoerer = HaZuhoerer(
            sitzungen,
            uhr,
            ZUORDNUNG,
            self.client,
            self.ereignisse,
            self.lage,
            pruefer,
            self.steuerung_wecker,
            self.status_wecker,
            backoff_start=0.01,
        )
        self.status = StatusHa(sitzungen, uhr, self.client, self.ereignisse)

    def plan(self, *buchungen: PlanBuchung) -> None:
        speichere_plan(self.sitzungen, *buchungen)

    def ereignis(self, typ: str) -> list[dict[str, object]]:
        return [
            {"feld_id": e.feld_id, "buchung_id": e.buchung_id, **e.daten}
            for e in self.ereignisse.unbestaetigt(1000)
            if e.typ == typ
        ]


@pytest.fixture
async def a(
    sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator
) -> AsyncIterator[Aufbau]:
    aufbau = Aufbau(sitzungen, uhr, ha)
    yield aufbau
    await aufbau.client.schliesse()


async def warte_bis(bedingung: Callable[[], bool], sekunden: float = 3.0) -> None:
    async def schleife() -> None:
        while not bedingung():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(schleife(), timeout=sekunden)


async def test_tastenfeld_code_als_zahl_oeffnet(a: Aufbau) -> None:
    a.plan(buchung(F1, t(19), t(21), pin=PIN))
    a.uhr.stelle(t(19))
    await a.zuhoerer.verarbeite({"event_type": "esphome.beachhub_pin", "data": {"code": int(PIN)}})
    await a.zuhoerer.warte_auf_eingaben()
    assert a.sim.zustaende["lock.eingang"]["state"] == "unlocked"
    await a.zuhoerer.verarbeite({"event_type": "esphome.beachhub_pin", "data": {}})
    await a.zuhoerer.warte_auf_eingaben()
    assert len(a.ereignis("pin_akzeptiert")) == 1 and a.ereignis("pin_abgelehnt") == []


async def test_handbetrieb_an_und_aus(a: Aufbau) -> None:
    await a.zuhoerer.verarbeite(zustandswechsel("input_boolean.beachhub_handbetrieb", "on"))
    await a.zuhoerer.verarbeite(zustandswechsel("input_boolean.beachhub_handbetrieb", "on"))
    with a.sitzungen() as db:
        assert lies(db, "handbetrieb") is True
    assert len(a.ereignis("handbetrieb_an")) == 1
    assert not a.steuerung_wecker.is_set()
    await a.zuhoerer.verarbeite(zustandswechsel("input_boolean.beachhub_handbetrieb", "off"))
    assert len(a.ereignis("handbetrieb_aus")) == 1
    assert a.steuerung_wecker.is_set()  # Rückkehr zur Automatik stellt den Sollzustand sofort her


async def test_praesenz_mit_und_ohne_buchung(a: Aufbau) -> None:
    b = buchung(F1, t(19), t(21))
    a.plan(b)
    a.uhr.stelle(t(19, 2))
    await a.zuhoerer.verarbeite(zustandswechsel("binary_sensor.praesenz_feld_1", "on"))
    await a.zuhoerer.verarbeite(zustandswechsel("binary_sensor.praesenz_feld_2", "on"))
    start = a.ereignis("praesenz_start")
    assert start[0]["feld_id"] == F1 and start[0]["buchung_id"] == b.buchung_id
    assert start[1]["feld_id"] == F2 and start[1]["buchung_id"] is None
    with a.sitzungen() as db:
        praesenz = lies(db, "praesenz")
    assert praesenz[F1]["ohne_buchung_seit"] is None
    assert praesenz[F2]["ohne_buchung_seit"] == t(19, 2).isoformat()
    a.uhr.stelle(t(19, 40))
    await a.zuhoerer.verarbeite(zustandswechsel("binary_sensor.praesenz_feld_1", "off"))
    assert a.ereignis("praesenz_ende") == [{"feld_id": F1, "buchung_id": None, "dauer_minuten": 38}]
    with a.sitzungen() as db:
        assert F1 not in lies(db, "praesenz")


async def test_tuer_offen_ausserhalb(a: Aufbau) -> None:
    a.plan(buchung(F1, t(19), t(21)))
    a.uhr.stelle(t(18, 50))  # im Zutrittsfenster
    await a.zuhoerer.verarbeite(zustandswechsel("binary_sensor.tuer", "on"))
    assert a.ereignis("tuer_offen_ausserhalb") == []
    a.uhr.stelle(t(22))
    await a.zuhoerer.verarbeite(zustandswechsel("binary_sensor.tuer", "on"))
    assert len(a.ereignis("tuer_offen_ausserhalb")) == 1
    a.uhr.stelle(t(23))
    await a.zuhoerer.verarbeite(
        {"event_type": "esphome.beachhub_pin", "data": {"code": MASTER_PIN}}
    )
    await a.zuhoerer.warte_auf_eingaben()
    a.uhr.vor(minutes=3)
    await a.zuhoerer.verarbeite(zustandswechsel("binary_sensor.tuer", "on"))
    assert len(a.ereignis("tuer_offen_ausserhalb")) == 1  # Master-PIN vor 3 min: kein Alarm


async def test_externe_aenderung_stoesst_steuerung_an(a: Aufbau) -> None:
    await a.zuhoerer.verarbeite(zustandswechsel("light.feld_1", "on"))
    assert a.steuerung_wecker.is_set()
    assert a.lage.ist_an("light.feld_1") is True


async def test_ha_ausfall_wird_nach_2_minuten_einmal_gemeldet(
    sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator
) -> None:
    aufbau = Aufbau(sitzungen, uhr, ha, url="http://127.0.0.1:9")
    aufbau.zuhoerer.getrennt()
    uhr.vor(minutes=1, seconds=59)
    aufbau.zuhoerer.pruefe_ausfall()
    assert aufbau.ereignis("ha_nicht_erreichbar") == []
    uhr.vor(seconds=1)
    aufbau.zuhoerer.pruefe_ausfall()
    aufbau.zuhoerer.pruefe_ausfall()
    assert len(aufbau.ereignis("ha_nicht_erreichbar")) == 1
    await aufbau.client.schliesse()


async def test_nach_verbindung_status_und_steuerung_angestossen(a: Aufbau) -> None:
    await a.sim.setze("input_boolean.beachhub_handbetrieb", "on")
    await a.sim.setze("binary_sensor.praesenz_feld_2", "on")
    await a.zuhoerer.verbunden()
    assert a.lage.ha_verbunden is True and a.lage.ist_an("binary_sensor.praesenz_feld_2") is True
    assert a.status_wecker.is_set() and a.steuerung_wecker.is_set()
    assert len(a.ereignis("handbetrieb_an")) == 1
    assert [e["feld_id"] for e in a.ereignis("praesenz_start")] == [F2]


async def test_wiederverbindung_ueber_websocket(a: Aufbau) -> None:
    aufgabe = asyncio.create_task(a.zuhoerer.laufen())
    try:
        await warte_bis(lambda: a.sim.abonnements() == 2)
        await a.sim.trenne_alle()
        await warte_bis(lambda: a.sim.verbindungen_gesamt == 2 and a.sim.abonnements() == 2)
        await a.sim.setze("input_boolean.beachhub_handbetrieb", "on")
        await warte_bis(lambda: len(a.ereignis("handbetrieb_an")) == 1)
    finally:
        aufgabe.cancel()
        with pytest.raises(asyncio.CancelledError):
            await aufgabe


async def test_wartende_eingabe_wird_beim_herunterfahren_abgebrochen(a: Aufbau) -> None:
    a.plan(buchung(F1, t(19), t(21), pin=PIN))
    a.uhr.stelle(t(19))
    await a.zuhoerer.verarbeite({"event_type": "esphome.beachhub_pin", "data": {"code": int(PIN)}})
    assert len(a.zuhoerer._eingaben) == 1
    await a.zuhoerer.beende_eingaben()
    assert a.zuhoerer._eingaben == set()


async def test_status_sensoren(a: Aufbau) -> None:
    a.plan()
    with a.sitzungen() as db:
        schreibe(db, "letzter_kontakt", (t(17) - timedelta(minutes=5)).isoformat())
        db.commit()
    a.ereignisse.melde("dienst_gestartet")
    await a.status.einmal()
    g = a.sim.geschrieben
    assert g["sensor.beachhub_planversion"]["state"] == "1"
    assert g["sensor.beachhub_letzter_kontakt"]["attributes"]["device_class"] == "timestamp"
    assert g["binary_sensor.beachhub_verbunden"]["state"] == "on"
    assert g["sensor.beachhub_warteschlange"]["state"] == "1"
    a.uhr.vor(minutes=6)
    await a.status.einmal()
    assert a.sim.geschrieben["binary_sensor.beachhub_verbunden"]["state"] == "off"


async def test_status_ohne_ha_kein_absturz(
    sitzungen: sessionmaker[Session],
    uhr: SimulierteUhr,
    ha: HaSimulator,
    caplog: pytest.LogCaptureFixture,
) -> None:
    aufbau = Aufbau(sitzungen, uhr, ha, url="http://127.0.0.1:9")
    caplog.set_level(logging.WARNING)
    offen_vorher = aufbau.ereignisse.offen()
    ergebnis = await aufbau.status.einmal()
    assert ergebnis is None  # kein Absturz, kein Rückgabewert
    assert "nicht geschrieben" in caplog.text
    assert ha.geschrieben == {}  # nichts landet im (echten) HA, weder teilweise noch ganz
    assert aufbau.ereignisse.offen() == offen_vorher  # kein Ereignis erzeugt
    await aufbau.client.schliesse()
