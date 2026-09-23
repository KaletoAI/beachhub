from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal

import pytest
from beachhub_hall.clock import SimulierteUhr
from beachhub_hall.config import FeldZuordnung, Zuordnung
from beachhub_hall.db import lies, schreibe
from beachhub_shared.hallenplan import PlanSperre
from sqlalchemy.orm import Session, sessionmaker

from tests.ha_simulator import HaSimulator
from tests.hilfen import F1, F2, MASTER_HASH, buchung, t
from tests.hilfen import SteuerungsAufbau as Aufbau


@pytest.fixture
async def a(
    sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator
) -> AsyncIterator[Aufbau]:
    aufbau = Aufbau(sitzungen, uhr, ha)
    yield aufbau
    await aufbau.client.schliesse()


async def test_hallenabend_schaltet_heizung_und_licht(a: Aufbau) -> None:
    a.plan(buchung(F2, t(19), t(21)))
    await a.um(18, 29)
    assert a.dienste() == []
    await a.um(18, 30)
    assert a.sim.zustaende["climate.halle"]["attributes"]["temperature"] == 18.0
    assert a.ereignis("heizung_gesetzt") == [
        {"feld_id": None, "soll": "18.0", "ist_temperatur": "5.0"}
    ]
    assert a.lage.soll_heizung == Decimal("18.0")
    await a.um(18, 55)
    assert a.sim.zustaende["light.feld_2"]["state"] == "on"
    assert a.sim.zustaende["light.feld_1"]["state"] == "off"
    assert a.ereignis("licht_geschaltet") == [{"feld_id": F2, "entity": "light.feld_2", "an": True}]
    anzahl = len(a.sim.aufrufe)
    await a.um(19, 30)
    assert len(a.sim.aufrufe) == anzahl  # nichts zu tun: idempotent
    await a.um(21, 5)
    assert a.sim.zustaende["light.feld_2"]["state"] == "off"
    assert a.sim.zustaende["climate.halle"]["attributes"]["temperature"] == 0.0


async def test_handbetrieb_schaltet_nichts(a: Aufbau) -> None:
    a.plan(buchung(F1, t(19), t(21)))
    with a.sitzungen() as db:
        schreibe(db, "handbetrieb", True)
        db.commit()
    await a.um(19)
    assert a.dienste() == []


async def test_ohne_plan_licht_aus_heizung_unangetastet(a: Aufbau) -> None:
    await a.sim.setze("light.feld_1", "on")
    await a.um(19)
    assert a.dienste() == [("light", "turn_off")]


async def test_abgelaufener_plan_grundzustand(a: Aufbau) -> None:
    # Die Buchung reicht über gueltig_bis hinaus: ihr eigenes Zutrittsfenster (beginn..ende)
    # wäre zur Testzeit noch offen, der Plan als Ganzes aber schon abgelaufen. Nur so prüft
    # der Fall wirklich die Ablaufprüfung und nicht nur, dass die Buchung längst vorbei ist
    # (Review Focus 5, Gegenprobe wie test_abgelaufener_plan_nur_master in test_pin.py).
    gueltig_bis = t(0) + timedelta(days=7)
    a.plan(buchung(F1, gueltig_bis - timedelta(hours=1), gueltig_bis + timedelta(hours=1)))
    await a.sim.setze("climate.halle", "heat", temperature=18.0)
    await a.sim.setze("light.feld_1", "on")
    a.uhr.stelle(gueltig_bis + timedelta(minutes=30))
    await a.steuerung.einmal()
    assert a.sim.zustaende["light.feld_1"]["state"] == "off"
    assert a.sim.zustaende["climate.halle"]["attributes"]["temperature"] == 0.0


async def test_sperre_schaltet_nichts(a: Aufbau) -> None:
    a.plan(sperren=(PlanSperre(feld_id=None, beginn=t(18), ende=t(23)),))
    await a.um(19)
    assert a.dienste() == []


async def test_dienstfehler_ergibt_einmal_aktorfehler(a: Aufbau) -> None:
    a.plan(buchung(F1, t(19), t(21)))
    a.sim.fehler_bei_diensten = True
    for minute in (0, 1, 2, 3, 4):
        await a.um(19, minute)
    fehler = a.ereignis("aktor_fehler")
    assert len(fehler) == 2  # Licht Feld 1 und Heizung, je einmal
    assert {f["entity"] for f in fehler} == {"light.feld_1", "climate.halle"}
    assert {f["grund"] for f in fehler} == {"dienst_fehlgeschlagen"}
    assert a.ereignis("licht_geschaltet") == []
    a.sim.fehler_bei_diensten = False
    await a.um(19, 5)
    assert a.sim.zustaende["light.feld_1"]["state"] == "on"


async def test_unbekannte_entitaet_weicht_dauerhaft_ab(
    sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator
) -> None:
    z = Zuordnung(master_pin_hash=MASTER_HASH, felder={F1: FeldZuordnung("light.gibt_es_nicht")})
    aufbau = Aufbau(sitzungen, uhr, ha, z)
    aufbau.plan(buchung(F1, t(19), t(21)))
    for minute in range(6):
        await aufbau.um(19, minute)
    assert len(aufbau.ereignis("licht_geschaltet")) == 3
    fehler = aufbau.ereignis("aktor_fehler")
    assert fehler == [
        {"feld_id": F1, "entity": "light.gibt_es_nicht", "grund": "zustand_weicht_ab"}
    ]
    await aufbau.client.schliesse()


async def test_ha_nicht_erreichbar_bricht_still_ab(
    sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator
) -> None:
    aufbau = Aufbau(sitzungen, uhr, ha, url="http://127.0.0.1:9")
    aufbau.plan(buchung(F1, t(19), t(21)))
    await aufbau.um(19)
    assert aufbau.ereignisse.unbestaetigt() == []
    await aufbau.client.schliesse()


async def test_praesenz_ohne_buchung_meldet_einmal(a: Aufbau) -> None:
    a.plan(buchung(F1, t(19), t(21)))
    with a.sitzungen() as db:
        schreibe(
            db,
            "praesenz",
            {
                F2: {
                    "seit": t(17).isoformat(),
                    "ohne_buchung_seit": t(17).isoformat(),
                    "alarm": False,
                }
            },
        )
        db.commit()
    await a.um(17, 9)
    assert a.ereignis("praesenz_ohne_buchung") == []
    await a.um(17, 10)
    assert a.ereignis("praesenz_ohne_buchung") == [{"feld_id": F2, "minuten": 10}]
    await a.um(17, 30)
    assert len(a.ereignis("praesenz_ohne_buchung")) == 1


async def test_praesenz_waehrend_buchung_ist_kein_alarm(a: Aufbau) -> None:
    a.plan(buchung(F1, t(19), t(21)))
    with a.sitzungen() as db:
        schreibe(
            db,
            "praesenz",
            {
                F1: {
                    "seit": t(18, 50).isoformat(),
                    "ohne_buchung_seit": t(18, 50).isoformat(),
                    "alarm": False,
                }
            },
        )
        db.commit()
    await a.um(19)
    with a.sitzungen() as db:
        assert lies(db, "praesenz")[F1]["ohne_buchung_seit"] is None
    await a.um(20, 30)
    assert a.ereignis("praesenz_ohne_buchung") == []
    await a.um(21)  # Buchung vorbei: ab jetzt zählt die Zeit ohne Buchung
    await a.um(21, 9)
    assert a.ereignis("praesenz_ohne_buchung") == []
    await a.um(21, 10)
    assert len(a.ereignis("praesenz_ohne_buchung")) == 1
