"""Ganze Abläufe gegen HA- und Core-Simulator mit simulierter Uhr (Hallendienst-Spec § 7)."""

from datetime import timedelta

from beachhub_hall.clock import SimulierteUhr
from beachhub_hall.dienst import Dienst

from tests.core_simulator import CoreSimulator
from tests.ha_simulator import HaSimulator
from tests.hilfen import F1, F2, TAG, baue_plan, buchung, t

# F1: Abo-Abende im Offline-Test, F2: Buchung im Hallentag.


def state_changed(entity_id: str, state: str) -> dict[str, object]:
    return {
        "event_type": "state_changed",
        "data": {"entity_id": entity_id, "new_state": {"entity_id": entity_id, "state": state}},
    }


async def test_hallentag(
    aufbau: tuple[Dienst, CoreSimulator], ha: HaSimulator, uhr: SimulierteUhr
) -> None:
    """Hauptspec § 9 „Hallentag“ mit den Werten des Betreibers (Heizvorlauf 30 min)."""
    d, core = aufbau
    # Ein reales climate-Gerät der Halle hat min_temp 0 °C (Frostschutz = grund_temperatur,
    # Ruling Task 9) und steht schon auf Grundtemperatur – anders als der Standardwert 7.0 des
    # Simulators, der gezielt die Begrenzung selbst prüfende Unit-Tests der Steuerung bedient
    # (test_steuerung.py).
    ha.entitaet("climate.halle", "heat", temperature=0.0, min_temp=0.0, max_temp=35.0)
    b = buchung(F2, t(19), t(21), pin="482913")
    core.veroeffentliche(baue_plan([b]))
    assert await d.plan_abruf.einmal()

    uhr.stelle(t(18, 29))
    await d.steuerung.einmal()
    assert ha.zustaende["climate.halle"]["attributes"]["temperature"] == 0.0
    uhr.stelle(t(18, 30))
    await d.steuerung.einmal()
    assert ha.zustaende["climate.halle"]["attributes"]["temperature"] == 18.0
    uhr.stelle(t(18, 55))
    await d.steuerung.einmal()
    assert ha.zustaende["light.feld_2"]["state"] == "on"
    assert ha.zustaende["light.feld_1"]["state"] == "off"

    uhr.stelle(t(19, 2))
    await d.zuhoerer.verarbeite({"event_type": "esphome.beachhub_pin", "data": {"code": "482913"}})
    await d.zuhoerer.warte_auf_eingaben()
    assert ha.zustaende["lock.eingang"]["state"] == "unlocked"
    await d.zuhoerer.verarbeite(state_changed("binary_sensor.praesenz_feld_2", "on"))

    uhr.stelle(t(21, 4))
    await d.steuerung.einmal()
    assert ha.zustaende["light.feld_2"]["state"] == "on"
    uhr.stelle(t(21, 5))
    await d.steuerung.einmal()
    assert ha.zustaende["light.feld_2"]["state"] == "off"
    assert ha.zustaende["climate.halle"]["attributes"]["temperature"] == 0.0

    await d.melder.leeren()
    assert core.typen() == [
        "heizung_gesetzt",
        "licht_geschaltet",
        "pin_akzeptiert",
        "praesenz_start",
        "heizung_gesetzt",  # 21:04 – Heizen endet mit der letzten Buchung
        "licht_geschaltet",  # 21:05 – Licht nach 5 min Nachlauf aus
    ]
    pin, praesenz = core.empfangen[2], core.empfangen[3]
    assert pin.buchung_id == b.buchung_id and praesenz.buchung_id == b.buchung_id
    assert core.status[-1].planversion == 1
    assert d.ereignisse.offen() == 0


async def test_72_stunden_ohne_hauptsystem(
    aufbau: tuple[Dienst, CoreSimulator], ha: HaSimulator, uhr: SimulierteUhr
) -> None:
    """Hauptspec § 9 „Internetausfall Halle“: Der Plan gilt weiter, Ereignisse sammeln sich
    und werden nach der Rückkehr lückenlos in seq-Reihenfolge nachgeliefert."""
    d, core = aufbau
    tage = [TAG + timedelta(days=i) for i in range(4)]
    core.veroeffentliche(baue_plan([buchung(F1, t(19, tag=tag), t(20, tag=tag)) for tag in tage]))
    assert await d.plan_abruf.einmal()
    core.offline = True

    start = t(12)
    uhr.stelle(start)
    while uhr.jetzt() < start + timedelta(hours=72):
        await d.steuerung.einmal()
        await d.plan_abruf.einmal()
        await d.melder.einmal()
        uhr.vor(minutes=30)

    einschalten = [a for a in ha.aufrufe if a[:2] == ("light", "turn_on")]
    assert len(einschalten) == 3  # an drei Abenden geschaltet, ganz ohne Hauptsystem
    assert core.empfangen == []
    offen = d.ereignisse.offen()
    assert offen == 12  # je Abend: Heizung an/aus, Licht an/aus

    core.offline = False
    await d.melder.leeren()
    seqs = [e.seq for e in core.empfangen]
    assert seqs == list(range(1, offen + 1))
    # Je Abend abwechselnd Heizung/Licht (an, dann aus – 18:30 Heizvorlauf, 19:00 Buchungsbeginn,
    # 20:00 Buchungsende, 20:30 nach Lichtnachlauf), dreimal identisch, in seq-Reihenfolge
    # nachgeliefert (Ruling Task 11 Fix-Runde 1).
    assert core.typen() == ["heizung_gesetzt", "licht_geschaltet"] * 6
    assert d.ereignisse.offen() == 0
