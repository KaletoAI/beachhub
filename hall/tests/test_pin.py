import asyncio
import logging
import sqlite3
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest
from beachhub_hall.clock import SimulierteUhr
from beachhub_hall.config import TuerKonfig
from beachhub_hall.db import lies, schreibe
from beachhub_hall.pin import ist_master
from sqlalchemy import text
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


class ReihenfolgeSchlaf:
    """Wie FakeSchlaf, merkt sich aber, ob zwei Schlafphasen sich überlappen – für den Test des
    Verzögerungs-Locks (mehrere gleichzeitige Eingaben in einer laufenden Fehlversuchsserie)."""

    def __init__(self) -> None:
        self.aufrufe = 0
        self.max_gleichzeitig = 0
        self._aktiv = 0

    async def __call__(self, sekunden: float) -> None:
        self.aufrufe += 1
        self._aktiv += 1
        self.max_gleichzeitig = max(self.max_gleichzeitig, self._aktiv)
        await asyncio.sleep(0)
        self._aktiv -= 1


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


async def test_pin_wird_nie_gespeichert(
    a: Aufbau, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    # DEBUG statt INFO: die Prüfung soll nicht davon abhängen, auf welchem Level irgendwo
    # geloggt wird – der Code darf die PIN auf keinem Level ausgeben.
    caplog.set_level(logging.DEBUG)
    a.plan(buchung(F1, t(19), t(21), pin=PIN))
    a.uhr.stelle(t(19))
    assert await a.pruefer.eingabe(PIN) is True
    assert await a.pruefer.eingabe("135790") is False
    # Nicht nur die Ereignis-Tabelle, sondern die komplette SQLite-Datei: jede Tabelle dumpen.
    with a.sitzungen() as db:
        db.execute(text("PRAGMA wal_checkpoint(FULL)"))
    with sqlite3.connect(tmp_path / "hall.sqlite") as roh:
        tabellen = [r[0] for r in roh.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        # Tabellennamen kommen aus sqlite_master (unser eigenes, bekanntes Schema), nicht von
        # außen – kein Injection-Risiko, nur die generische Query lässt sich nicht parametrisieren.
        gesamt = "\n".join(
            str(roh.execute(f"SELECT * FROM {tbl}").fetchall())  # noqa: S608
            for tbl in tabellen
        )
    assert PIN not in gesamt and "135790" not in gesamt
    assert PIN not in caplog.text and "135790" not in caplog.text


async def test_code_mit_leerzeichen_wird_akzeptiert(a: Aufbau) -> None:
    a.plan(buchung(F1, t(19), t(21), pin=PIN))
    a.uhr.stelle(t(19))
    assert await a.pruefer.eingabe(f" {PIN} ") is True


async def test_fullwidth_ziffern_sind_kein_gueltiger_code(a: Aufbau) -> None:
    # ESPHome/HA könnten theoretisch Unicode-Ziffern liefern; \d ohne re.ASCII träfe auch
    # darauf zu und argon2 würde sie anders hashen als das Hauptsystem erwartet.
    a.plan(buchung(F1, t(19), t(21), pin=PIN))
    a.uhr.stelle(t(19))
    fullwidth = "".join(chr(ord(c) - ord("0") + 0xFF10) for c in PIN)
    assert await a.pruefer.eingabe(fullwidth) is False
    assert a.typen().count("pin_abgelehnt") == 1


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
    # Die Buchung reicht über gueltig_bis (Planbeginn + 7 Tage) hinaus: ihr eigenes
    # Zutrittsfenster (beginn−vorlauf .. ende) wäre zur Testzeit noch offen, der Plan als
    # Ganzes aber schon abgelaufen. Nur so testet der Fall wirklich die Ablaufprüfung und
    # nicht nur, dass die Buchung selbst längst vorbei ist.
    gueltig_bis = t(0) + timedelta(days=7)
    a.plan(buchung(F1, gueltig_bis - timedelta(hours=1), gueltig_bis + timedelta(hours=1), pin=PIN))
    a.uhr.stelle(gueltig_bis + timedelta(minutes=30))
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


async def test_gleichzeitige_eingaben_in_serie_verzoegern_nacheinander(a: Aufbau) -> None:
    for _ in range(5):
        await a.pruefer.eingabe("111111")
    schlaf = ReihenfolgeSchlaf()
    a.pruefer._schlafen = schlaf
    ergebnisse = await asyncio.gather(
        a.pruefer.eingabe("111111"), a.pruefer.eingabe("222222"), a.pruefer.eingabe("333333")
    )
    assert ergebnisse == [False, False, False]
    assert schlaf.aufrufe == 3
    assert schlaf.max_gleichzeitig == 1  # nacheinander, nie überlappend


async def test_tuerfehler_bei_akzeptierter_pin_bricht_eingabe_nicht_ab(a: Aufbau) -> None:
    a.plan(buchung(F1, t(19), t(21), pin=PIN))
    a.uhr.stelle(t(19))
    a.sim.fehler_bei_diensten = True
    assert await a.pruefer.eingabe(PIN) is True  # Zutritt gilt, auch wenn die Tür klemmt
    typen = a.typen()
    assert "pin_akzeptiert" in typen and "aktor_fehler" in typen


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


async def test_tuer_turn_on_fehler_schaltet_trotzdem_wieder_aus(
    sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator
) -> None:
    ha.entitaet("switch.tueroeffner", "off")
    ha.fehler_verbleibend = 1  # nur der turn_on-Aufruf schlägt fehl
    aufbau = Aufbau(sitzungen, uhr, ha, TuerKonfig("switch.tueroeffner", impuls_sekunden=5))
    assert await aufbau.tuer.oeffne() is False
    await aufbau.tuer.warte()
    assert [x[:2] for x in ha.aufrufe] == [("switch", "turn_on"), ("switch", "turn_off")]
    assert ha.zustaende["switch.tueroeffner"]["state"] == "off"
    fehler = [e for e in aufbau.ereignisse.unbestaetigt() if e.typ == "aktor_fehler"]
    assert [f.daten["grund"] for f in fehler] == ["tuer_oeffnen_fehlgeschlagen"]
    await aufbau.client.schliesse()


async def test_tuer_turn_off_mit_wiederholung_erfolgreich(
    sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator
) -> None:
    ha.entitaet("switch.tueroeffner", "off")
    aufbau = Aufbau(sitzungen, uhr, ha, TuerKonfig("switch.tueroeffner", impuls_sekunden=5))
    assert await aufbau.tuer.oeffne() is True
    ha.fehler_verbleibend = 2  # die ersten zwei turn_off-Versuche schlagen fehl
    await aufbau.tuer.warte()
    aufrufe = [x[:2] for x in ha.aufrufe]
    assert aufrufe == [
        ("switch", "turn_on"),
        ("switch", "turn_off"),
        ("switch", "turn_off"),
        ("switch", "turn_off"),
    ]
    assert ha.zustaende["switch.tueroeffner"]["state"] == "off"
    assert not [e for e in aufbau.ereignisse.unbestaetigt() if e.typ == "aktor_fehler"]
    await aufbau.client.schliesse()


async def test_tuer_turn_off_alle_versuche_scheitern_meldet_aktor_fehler(
    sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator
) -> None:
    ha.entitaet("switch.tueroeffner", "off")
    aufbau = Aufbau(sitzungen, uhr, ha, TuerKonfig("switch.tueroeffner", impuls_sekunden=5))
    assert await aufbau.tuer.oeffne() is True
    ha.fehler_verbleibend = 3  # alle drei turn_off-Versuche schlagen fehl
    await aufbau.tuer.warte()
    assert [x[:2] for x in ha.aufrufe].count(("switch", "turn_off")) == 3
    fehler = [e for e in aufbau.ereignisse.unbestaetigt() if e.typ == "aktor_fehler"]
    assert [f.daten["grund"] for f in fehler] == ["tuer_impuls_nicht_beendet"]
    await aufbau.client.schliesse()


async def test_schliesse_beendet_laufenden_impuls_sofort(
    sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator
) -> None:
    ha.entitaet("switch.tueroeffner", "off")
    aufbau = Aufbau(sitzungen, uhr, ha, TuerKonfig("switch.tueroeffner", impuls_sekunden=5))
    assert await aufbau.tuer.oeffne() is True
    await aufbau.tuer.schliesse()
    assert [x[:2] for x in ha.aufrufe] == [("switch", "turn_on"), ("switch", "turn_off")]
    assert aufbau.schlaf.aufrufe == []  # schliesse() wartet nicht die impuls_sekunden ab
    assert ha.zustaende["switch.tueroeffner"]["state"] == "off"
    await aufbau.client.schliesse()


async def test_schliesse_schaltet_switch_auch_ohne_laufenden_impuls_aus(
    sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator
) -> None:
    # Der Schalter hängt (z. B. nach einem früheren Fehler oder von außen geschaltet) auf "on",
    # ohne dass gerade ein Impuls läuft – schliesse() muss ihn trotzdem ausschalten (Ruling
    # Task 8/11).
    ha.entitaet("switch.tueroeffner", "on")
    aufbau = Aufbau(sitzungen, uhr, ha, TuerKonfig("switch.tueroeffner", impuls_sekunden=5))
    await aufbau.tuer.schliesse()
    assert [x[:2] for x in ha.aufrufe] == [("switch", "turn_off")]
    assert ha.zustaende["switch.tueroeffner"]["state"] == "off"
    await aufbau.client.schliesse()


async def test_schliesse_tut_bei_lock_ohne_laufenden_impuls_nichts(
    sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator
) -> None:
    aufbau = Aufbau(sitzungen, uhr, ha, TuerKonfig("lock.eingang"))
    await aufbau.tuer.schliesse()
    assert ha.aufrufe == []
    await aufbau.client.schliesse()


async def test_abbruch_waehrend_turn_on_hinterlaesst_impuls_zum_ausschalten(
    sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator
) -> None:
    # Wird oeffne() genau während des turn_on-Aufrufs abgebrochen, muss trotzdem ein Impuls
    # angelegt werden (Task 11 legt ihn in finally an) – sonst plant niemand mehr ein turn_off
    # und der Schalter bliebe im Zweifel dauerhaft eingeschaltet (Ruling Task 8/11).
    ha.entitaet("switch.tueroeffner", "off")
    aufbau = Aufbau(sitzungen, uhr, ha, TuerKonfig("switch.tueroeffner", impuls_sekunden=5))
    haengt = asyncio.Event()
    echt = aufbau.tuer._ha.dienst

    async def hemmend(domain: str, service: str, daten: dict[str, object]) -> None:
        if (domain, service) == ("switch", "turn_on"):
            await haengt.wait()
            return
        await echt(domain, service, daten)

    aufbau.tuer._ha.dienst = hemmend  # type: ignore[method-assign]
    aufgabe = asyncio.create_task(aufbau.tuer.oeffne())
    await asyncio.sleep(0)
    aufgabe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await aufgabe
    assert aufbau.tuer._impuls is not None
    await aufbau.tuer.schliesse()
    assert [x[:2] for x in ha.aufrufe] == [("switch", "turn_off")]
    await aufbau.client.schliesse()


async def test_ueberlappende_oeffnung_verlaengert_impuls(
    sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator
) -> None:
    ha.entitaet("switch.tueroeffner", "off")
    aufbau = Aufbau(sitzungen, uhr, ha, TuerKonfig("switch.tueroeffner", impuls_sekunden=5))
    assert await aufbau.tuer.oeffne() is True
    assert await aufbau.tuer.oeffne() is True  # erster Impuls wird abgebrochen, neuer startet
    await aufbau.tuer.warte()
    assert [x[:2] for x in ha.aufrufe] == [
        ("switch", "turn_on"),
        ("switch", "turn_on"),
        ("switch", "turn_off"),
    ]
    assert aufbau.schlaf.aufrufe == [5.0]  # nur der zweite, überlebende Impuls schläft
    await aufbau.client.schliesse()
