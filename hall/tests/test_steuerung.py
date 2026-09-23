import logging
from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal

import pytest
from beachhub_hall.clock import SimulierteUhr
from beachhub_hall.config import FeldZuordnung, HeizungKonfig, Zuordnung
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
    # grund_temperatur (0.0) liegt unter min_temp der Entität (7.0) und wird begrenzt.
    assert a.sim.zustaende["climate.halle"]["attributes"]["temperature"] == 7.0


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
    # grund_temperatur (0.0) liegt unter min_temp der Entität (7.0) und wird begrenzt.
    assert a.sim.zustaende["climate.halle"]["attributes"]["temperature"] == 7.0


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
    # Gestörte Entitäten werden erst wieder nach RETRY_INTERVALL (5 min seit dem letzten
    # Versuch um 19:02) neu versucht (fix 3) – 19:05 wäre noch zu früh.
    await a.um(19, 7)
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


async def test_praesenz_im_zutrittsvorlauf_ist_kein_alarm(a: Aufbau) -> None:
    # Zutritt ist schon zutritt_vorlauf_minuten (15) vor Buchungsbeginn (19:00) möglich, also
    # ab 18:45 – das Alarmfenster (praesenz_alarm_minuten=10) liegt vollständig davor. Ohne den
    # Fix (nur laufende_buchung statt soll.zutritt_offen) würde praesenz_ohne_buchung um 18:55
    # fälschlich auslösen, weil die Buchung selbst erst um 19:00 beginnt. Gegenprobe: eine
    # wirklich unbebuchte Präsenz alarmiert weiterhin nach 10 Minuten
    # (test_praesenz_ohne_buchung_meldet_einmal).
    a.plan(buchung(F1, t(19), t(21)))
    with a.sitzungen() as db:
        schreibe(
            db,
            "praesenz",
            {
                F1: {
                    "seit": t(18, 45).isoformat(),
                    "ohne_buchung_seit": t(18, 45).isoformat(),
                    "alarm": False,
                }
            },
        )
        db.commit()
    for minute in range(45, 60):
        await a.um(18, minute)
        assert a.ereignis("praesenz_ohne_buchung") == []
    await a.um(19, 0)
    assert a.ereignis("praesenz_ohne_buchung") == []


async def test_heizung_wird_auf_min_temp_begrenzt(a: Aufbau) -> None:
    # grund_temperatur (0.0) liegt unter min_temp der climate-Entität (7.0). Ohne Begrenzung
    # würde HA den Aufruf ablehnen (siehe HaSimulator._dienst) und die Heizung wiche dauerhaft
    # ab; begrenzt auf 7.0 wird der Aufruf angenommen und ist danach idempotent.
    a.plan(buchung(F1, t(19), t(21)))
    await a.sim.setze("climate.halle", "heat", temperature=0.0)
    await a.um(6)
    assert a.sim.zustaende["climate.halle"]["attributes"]["temperature"] == 7.0
    assert a.ereignis("heizung_gesetzt") == [
        {"feld_id": None, "soll": "7.0", "ist_temperatur": "5.0"}
    ]
    anzahl = len(a.sim.aufrufe)
    await a.um(6, 1)
    assert len(a.sim.aufrufe) == anzahl  # keine Abweichung mehr


async def test_gestoerte_entitaet_wird_nur_alle_5_minuten_neu_versucht(a: Aufbau) -> None:
    a.plan(buchung(F1, t(19), t(21)))
    a.sim.fehler_bei_diensten = True
    for minute in (0, 1, 2):
        await a.um(19, minute)
    anzahl = len(a.sim.aufrufe)  # 3 Versuche je Entität (Licht F1 + Heizung), jetzt gestört
    assert len(a.ereignis("aktor_fehler")) == 2
    for minute in (3, 4, 5, 6):
        await a.um(19, minute)
    assert len(a.sim.aufrufe) == anzahl  # innerhalb der 5 Minuten: kein weiterer Versuch
    assert len(a.ereignis("aktor_fehler")) == 2  # keine erneute Meldung
    await a.um(19, 7)  # 5 Minuten seit dem letzten Versuch (19:02) vorbei
    assert len(a.sim.aufrufe) == anzahl + 2  # ein neuer Versuch je gestörter Entität
    assert len(a.ereignis("aktor_fehler")) == 2  # weiterhin nur je einmal gemeldet


async def test_licht_fehler_blockiert_heizung_nicht(a: Aufbau) -> None:
    a.plan(buchung(F2, t(19), t(21)))
    a.sim.fehler_bei_zustand = {"light.feld_1", "light.feld_2"}
    await a.um(18, 30)  # Heizvorlauf beginnt, beide Lichter liefern 500 auf GET
    assert a.dienste() == [("climate", "set_temperature")]
    assert a.sim.zustaende["climate.halle"]["attributes"]["temperature"] == 18.0
    assert a.ereignis("licht_geschaltet") == []
    assert a.ereignis("aktor_fehler") == []


async def test_heizung_gesetzt_trotz_fehler_beim_ist_sensor(
    sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator
) -> None:
    ha.entitaet("sensor.temp_ist", "5.0")
    z = Zuordnung(
        master_pin_hash=MASTER_HASH,
        heizung=HeizungKonfig("climate.halle", ist_sensor="sensor.temp_ist"),
    )
    aufbau = Aufbau(sitzungen, uhr, ha, z)
    aufbau.plan(buchung(F1, t(19), t(21)))
    ha.fehler_bei_zustand = {"sensor.temp_ist"}
    await aufbau.um(18, 30)  # Heizvorlauf beginnt, das Setzen selbst klappt
    assert aufbau.sim.zustaende["climate.halle"]["attributes"]["temperature"] == 18.0
    assert aufbau.ereignis("heizung_gesetzt") == [
        {"feld_id": None, "soll": "18.0", "ist_temperatur": None}
    ]
    await aufbau.client.schliesse()


async def test_kaputter_praesenz_eintrag_bricht_steuerung_nicht_ab(
    a: Aufbau, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    a.plan(buchung(F1, t(19), t(21)))
    with a.sitzungen() as db:
        schreibe(
            db,
            "praesenz",
            {
                "kaputt": {"seit": "nicht-iso", "ohne_buchung_seit": "nicht-iso", "alarm": False},
                F2: {
                    "seit": t(17).isoformat(),
                    "ohne_buchung_seit": t(17).isoformat(),
                    "alarm": False,
                },
            },
        )
        db.commit()
    await a.um(17, 10)
    assert a.ereignis("praesenz_ohne_buchung") == [{"feld_id": F2, "minuten": 10}]
    assert "kaputt" in caplog.text
    with a.sitzungen() as db:
        assert "kaputt" not in lies(db, "praesenz")


async def test_handbetrieb_setzt_stoerung_zurueck(a: Aufbau) -> None:
    a.plan(buchung(F1, t(19), t(21)))
    a.sim.fehler_bei_diensten = True
    for minute in (0, 1, 2):
        await a.um(19, minute)
    assert len(a.ereignis("aktor_fehler")) == 2  # Licht F1 und Heizung, je einmal gestört
    with a.sitzungen() as db:
        schreibe(db, "handbetrieb", True)
        db.commit()
    await a.um(19, 3)  # ein Lauf im Handbetrieb setzt Versuche/Störungen zurück
    with a.sitzungen() as db:
        schreibe(db, "handbetrieb", False)
        db.commit()
    for minute in (4, 5, 6):
        await a.um(19, minute)
    assert len(a.ereignis("aktor_fehler")) == 4  # nach dem Reset entsteht die Störung neu
    a.sim.fehler_bei_diensten = False
