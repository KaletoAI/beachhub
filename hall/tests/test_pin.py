import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from beachhub_hall.clock import SimulierteUhr
from beachhub_hall.config import TuerKonfig
from beachhub_hall.db import EreignisZeile, lies, schreibe
from beachhub_hall.pin import ist_master
from sqlalchemy.orm import Session, sessionmaker

from tests.ha_simulator import HaSimulator
from tests.hilfen import F1, MASTER_HASH, MASTER_PIN, Aufbau, buchung, t

PIN = "482913"


@pytest.fixture
async def a(
    sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator
) -> AsyncIterator[Aufbau]:
    aufbau = Aufbau(sitzungen, uhr, ha, TuerKonfig("lock.eingang"))
    yield aufbau
    await aufbau.client.schliesse()


async def test_pin_oeffnet_im_zutrittsfenster(a: Aufbau) -> None:
    b = buchung(F1, t(19), t(21), pin=PIN)
    a.plan(b)
    a.uhr.stelle(t(18, 44))
    assert await a.pruefer.eingabe(PIN) is False
    a.uhr.stelle(t(18, 45))
    assert await a.pruefer.eingabe(PIN) is True
    assert a.sim.zustaende["lock.eingang"]["state"] == "unlocked"
    a.uhr.stelle(t(21))
    assert await a.pruefer.eingabe(PIN) is False
    akzeptiert = [e for e in a.ereignisse.unbestaetigt() if e.typ == "pin_akzeptiert"]
    assert len(akzeptiert) == 1
    assert akzeptiert[0].buchung_id == b.buchung_id and akzeptiert[0].feld_id == F1


async def test_pin_wird_nie_gespeichert(a: Aufbau, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    a.plan(buchung(F1, t(19), t(21), pin=PIN))
    a.uhr.stelle(t(19))
    assert await a.pruefer.eingabe(PIN) is True
    assert await a.pruefer.eingabe("135790") is False
    with a.sitzungen() as db:
        alles = json.dumps([(z.typ, z.daten_json) for z in db.query(EreignisZeile)])
    assert PIN not in alles and "135790" not in alles
    assert PIN not in caplog.text and "135790" not in caplog.text


async def test_code_mit_leerzeichen_wird_akzeptiert(a: Aufbau) -> None:
    a.plan(buchung(F1, t(19), t(21), pin=PIN))
    a.uhr.stelle(t(19))
    assert await a.pruefer.eingabe(f" {PIN} ") is True


async def test_master_oeffnet_immer(a: Aufbau) -> None:
    assert ist_master(MASTER_PIN, MASTER_HASH) and not ist_master("1234", MASTER_HASH)
    assert not ist_master(MASTER_PIN, "$argon2id$kaputt")
    assert await a.pruefer.eingabe(MASTER_PIN) is True  # ganz ohne Plan
    e = [x for x in a.ereignisse.unbestaetigt() if x.typ == "pin_akzeptiert"][0]
    assert e.daten == {"master": True} and e.buchung_id is None
    with a.sitzungen() as db:
        assert lies(db, "letzter_master") == a.uhr.jetzt().isoformat()


async def test_handbetrieb_aendert_nichts_am_zutritt(a: Aufbau) -> None:
    a.plan(buchung(F1, t(19), t(21), pin=PIN))
    with a.sitzungen() as db:
        schreibe(db, "handbetrieb", True)
        db.commit()
    a.uhr.stelle(t(19))
    assert await a.pruefer.eingabe(PIN) is True


async def test_abgelaufener_plan_nur_master(a: Aufbau) -> None:
    a.plan(buchung(F1, t(19), t(21), pin=PIN))
    a.uhr.stelle(t(19) + timedelta(days=8))
    assert await a.pruefer.eingabe(PIN) is False
    assert await a.pruefer.eingabe(MASTER_PIN) is True


async def test_ungueltige_eingaben_sind_fehlversuche(a: Aufbau) -> None:
    for code in ("", "12", "abcdef", "1234567890123", "12 34"):
        assert await a.pruefer.eingabe(code) is False
    assert a.typen().count("pin_abgelehnt") == 5
    assert a.geoeffnet() == 0


async def test_fehlversuchsserie_meldet_einmal_und_verzoegert(a: Aufbau) -> None:
    a.plan(buchung(F1, t(19), t(21), pin=PIN))
    a.uhr.stelle(t(19))
    for _ in range(5):
        assert await a.pruefer.eingabe("111111") is False
    assert a.typen().count("tastenfeld_fehlversuche") == 1
    assert a.schlaf.aufrufe == []  # die ersten fünf Eingaben werden sofort geprüft
    assert await a.pruefer.eingabe("111111") is False
    assert a.schlaf.aufrufe == [3.0]
    assert a.typen().count("tastenfeld_fehlversuche") == 1
    # Das Tastenfeld bleibt aktiv: die richtige PIN öffnet trotzdem (nur verzögert) ...
    assert await a.pruefer.eingabe(PIN) is True
    assert a.schlaf.aufrufe == [3.0, 3.0]
    # ... und beendet die Serie.
    assert await a.pruefer.eingabe("111111") is False
    assert a.schlaf.aufrufe == [3.0, 3.0]


async def test_serie_endet_nach_15_minuten_ruhe(a: Aufbau) -> None:
    for _ in range(5):
        await a.pruefer.eingabe("111111")
    a.uhr.vor(minutes=15)
    await a.pruefer.eingabe("111111")
    assert a.schlaf.aufrufe == []
    for _ in range(4):
        await a.pruefer.eingabe("111111")
    assert a.typen().count("tastenfeld_fehlversuche") == 2


async def test_gleichzeitige_fehleingaben_zaehlen_beide(a: Aufbau) -> None:
    await asyncio.gather(a.pruefer.eingabe("111111"), a.pruefer.eingabe("222222"))
    with a.sitzungen() as db:
        assert lies(db, "fehlserie")["anzahl"] == 2


async def test_tuer_als_switch_mit_impuls(
    sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator
) -> None:
    ha.entitaet("switch.tueroeffner", "off")
    aufbau = Aufbau(sitzungen, uhr, ha, TuerKonfig("switch.tueroeffner", impuls_sekunden=5))
    assert await aufbau.tuer.oeffne() is True
    await aufbau.tuer.warte()
    assert [x[:2] for x in ha.aufrufe] == [("switch", "turn_on"), ("switch", "turn_off")]
    assert aufbau.schlaf.aufrufe == [5.0]
    await aufbau.client.schliesse()


async def test_tuer_fehler_werden_gemeldet(
    sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator
) -> None:
    ohne = Aufbau(sitzungen, uhr, ha, TuerKonfig(None))
    assert await ohne.tuer.oeffne() is False
    ha.fehler_bei_diensten = True
    mit = Aufbau(sitzungen, uhr, ha, TuerKonfig("lock.eingang"))
    assert await mit.tuer.oeffne() is False
    fehler = [e for e in mit.ereignisse.unbestaetigt() if e.typ == "aktor_fehler"]
    assert [f.daten["entity"] for f in fehler] == ["tuer", "lock.eingang"]
    await ohne.client.schliesse()
    await mit.client.schliesse()
