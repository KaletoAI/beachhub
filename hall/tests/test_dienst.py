import asyncio
import os
import signal
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer
from argon2 import PasswordHasher
from beachhub_hall import __main__ as cli
from beachhub_hall import dienst as dienst_modul
from beachhub_hall.aufgaben import takt
from beachhub_hall.clock import SimulierteUhr
from beachhub_hall.config import KonfigFehler, Umgebung, lade_zuordnung
from beachhub_hall.dienst import Dienst
from beachhub_hall.health import health_app
from beachhub_hall.pin import ist_master
from sqlalchemy.orm import Session, sessionmaker

from tests.core_simulator import CoreSimulator
from tests.dienst_hilfen import baue_dienst
from tests.ha_simulator import HaSimulator
from tests.hilfen import F1, MASTER_HASH, TOML_BEISPIEL, baue_plan, buchung, speichere_plan, t


async def test_takt_laeuft_weiter_nach_fehler_und_wacht_auf() -> None:
    aufrufe: list[int] = []
    wecker = asyncio.Event()

    async def einmal() -> None:
        aufrufe.append(len(aufrufe))
        if len(aufrufe) == 1:
            raise RuntimeError("erster Lauf scheitert")

    aufgabe = asyncio.create_task(takt("test", einmal, intervall=3600, wecker=wecker))
    await asyncio.sleep(0.01)
    assert len(aufrufe) == 1
    wecker.set()
    await asyncio.sleep(0.01)
    assert len(aufrufe) == 2 and not wecker.is_set()
    aufgabe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await aufgabe


class _SchrittweiserSchlaf:
    """Ersatz für `schlafen()`: kehrt erst zurück, wenn der Test `freigeben()` ruft – für
    deterministische Schritt-für-Schritt-Kontrolle über wiederholte Neustarts, ohne dass
    `_dauerhaft` dabei (mangels jedes Suspend-Punkts) in einer heißen Schleife hängt."""

    def __init__(self) -> None:
        self.aufrufe: list[float] = []
        self._tor = asyncio.Event()

    async def __call__(self, sekunden: float) -> None:
        self.aufrufe.append(sekunden)
        self._tor.clear()
        await self._tor.wait()

    def freigeben(self) -> None:
        self._tor.set()


async def test_dauerhaft_startet_nach_ausnahme_neu() -> None:
    """`_dauerhaft` sichert Aufgaben wie `zuhoerer.laufen()` ab, die selbst schon eine
    Dauerschleife sind (kein `einmal()`-Takt) – eine unerwartete Ausnahme darf den Dienst nicht
    mitreißen (Ruling Task 8/11)."""
    aufrufe: list[int] = []
    weiterlaufen = asyncio.Event()

    async def arbeit() -> None:
        aufrufe.append(len(aufrufe))
        if len(aufrufe) == 1:
            raise RuntimeError("unerwarteter Fehler")
        await weiterlaufen.wait()

    aufgabe = asyncio.create_task(
        dienst_modul._dauerhaft("test", arbeit, schlafen=lambda _s: asyncio.sleep(0))
    )
    await asyncio.sleep(0.01)
    assert len(aufrufe) == 2  # nach der Ausnahme neu gestartet (nach kurzer Pause)
    aufgabe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await aufgabe


async def test_dauerhaft_erhoeht_backoff_bei_wiederholtem_fehler() -> None:
    """Kein sofortiger Neustart ohne Pause (sonst heiße Schleife/Log-Flut) – die Pause wächst
    nach jedem Fehlschlag (1 s → 2 s → 4 s → 8 s), die Anzahl der Neustarts ist dabei begrenzt
    auf das, was der Test tatsächlich freigibt (Ruling Task 11 Fix-Runde 1)."""
    schlaf = _SchrittweiserSchlaf()
    aufrufe = 0

    async def arbeit() -> None:
        nonlocal aufrufe
        aufrufe += 1
        raise RuntimeError("immer kaputt")

    aufgabe = asyncio.create_task(
        dienst_modul._dauerhaft("test", arbeit, schlafen=schlaf, jetzt=lambda: 0.0)
    )
    for schritt, erwarteter_backoff in enumerate((1.0, 2.0, 4.0, 8.0), start=1):
        while len(schlaf.aufrufe) < schritt:
            await asyncio.sleep(0)
        assert schlaf.aufrufe[-1] == erwarteter_backoff
        schlaf.freigeben()
    assert aufrufe == 4  # genau vier Neustarts – nicht mehr, als der Test freigegeben hat
    aufgabe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await aufgabe


async def test_dauerhaft_setzt_backoff_nach_langem_lauf_zurueck() -> None:
    """Lief `arbeit()` vor dem nächsten Fehler mindestens so lange wie der maximale Backoff,
    gilt sie als erholt: die Pause beginnt wieder bei 1 s (Ruling Task 11 Fix-Runde 1)."""
    schlaf = _SchrittweiserSchlaf()
    # Je Fehlschlag zwei jetzt()-Aufrufe (Start, Ende): kurz/kurz, kurz/kurz, kurz/lang (Reset).
    zeiten = iter([0.0, 0.0, 0.0, 0.0, 0.0, 100.0])

    async def arbeit() -> None:
        raise RuntimeError("kaputt")

    aufgabe = asyncio.create_task(
        dienst_modul._dauerhaft("test", arbeit, schlafen=schlaf, jetzt=lambda: next(zeiten))
    )
    for schritt, erwarteter_backoff in enumerate((1.0, 2.0, 1.0), start=1):
        while len(schlaf.aufrufe) < schritt:
            await asyncio.sleep(0)
        assert schlaf.aufrufe[-1] == erwarteter_backoff
        schlaf.freigeben()
    aufgabe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await aufgabe


def test_master_pin_befehl(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    eingaben = iter(["4711", "4711"])
    monkeypatch.setattr(cli, "getpass", lambda _prompt: next(eingaben))
    cli.main(["master-pin"])
    ausgabe = capsys.readouterr().out.strip()
    assert PasswordHasher().verify(ausgabe, "4711")
    assert ist_master("4711", ausgabe)  # derselbe Hash, den hall.toml als master_pin_hash bekommt
    eingaben = iter(["4711", "4712"])
    with pytest.raises(SystemExit):
        cli.main(["master-pin"])


@pytest.mark.parametrize("ungueltig", ["12a4", "123", "４７１１"])
def test_master_pin_befehl_lehnt_ungueltige_formate_ab(
    monkeypatch: pytest.MonkeyPatch, ungueltig: str
) -> None:
    # Dieselbe Prüfung wie am Tastenfeld (pin.ZIFFERN, jetzt wiederverwendet statt einer eigenen
    # \d-Regel): nur ASCII-Ziffern, 4–12 Stellen – sonst erzeugt die CLI einen Hash für eine PIN,
    # die argon2 später anders liest als das Hauptsystem (Ruling Task 11 Fix-Runde 1).
    eingaben = iter([ungueltig, ungueltig])
    monkeypatch.setattr(cli, "getpass", lambda _prompt: next(eingaben))
    with pytest.raises(SystemExit):
        cli.main(["master-pin"])


def test_main_start_meldet_konfigfehler_ohne_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    """`KonfigFehler` (z. B. fehlendes/kaputtes hall.toml) beendet die CLI mit einer lesbaren
    Meldung statt einem rohen Traceback (Ruling Task 11 Fix-Runde 1)."""
    monkeypatch.setattr(cli, "Umgebung", lambda: Umgebung(core_public_key="00" * 32))

    def kaputt(_u: Umgebung) -> Dienst:
        raise KonfigFehler("hall.toml fehlt")

    monkeypatch.setattr(cli.Dienst, "aus_umgebung", kaputt)
    with pytest.raises(SystemExit, match="hall.toml fehlt"):
        cli.main(["start"])


def test_aus_umgebung(tmp_path: Path) -> None:
    toml = tmp_path / "hall.toml"
    toml.write_text(TOML_BEISPIEL, encoding="utf-8")
    u = Umgebung(data_dir=tmp_path / "daten", hall_toml=toml, core_public_key="00" * 32)
    d = Dienst.aus_umgebung(u)
    assert (tmp_path / "daten" / "hall.sqlite").exists()
    # Konkrete gelesene Werte statt einer immer wahren Aussage (Ruling Task 10/11).
    assert d.zuordnung.master_pin_hash == MASTER_HASH
    assert d.zuordnung.felder[F1].licht == "light.feld_1"
    assert d.zuordnung.heizung.entity == "climate.halle"
    assert d.zuordnung.tuer.entity == "lock.eingang"
    with pytest.raises(KonfigFehler):
        Dienst.aus_umgebung(Umgebung(data_dir=tmp_path, hall_toml=tmp_path / "fehlt.toml"))
    assert lade_zuordnung(toml).felder


async def test_health(dienst: tuple[Dienst, CoreSimulator]) -> None:
    d, _ = dienst
    async with TestClient(TestServer(health_app(d))) as client:
        r = await client.get("/health")
        assert r.status == 200
        assert await r.json() == {
            "status": "ok",
            "planversion": 0,
            "ha_verbunden": False,
            "warteschlange": 0,
        }


async def test_laufen_bis_signal_faehrt_bei_sigterm_geordnet_herunter() -> None:
    """Als PID 1 im Container ignoriert Python SIGTERM ohne eigenen Handler – `docker stop`
    würde das geordnete Herunterfahren (u. a. `tuer.schliesse()`) sonst umgehen, ein
    eingeschalteter switch.*-Türöffner könnte eingeschaltet bleiben (Ruling Task 11
    Fix-Runde 1)."""
    geschlossen = asyncio.Event()

    class FakeDienst:
        async def laufen(self) -> None:
            await asyncio.Event().wait()  # läuft, bis von außen abgebrochen wird

        async def schliesse(self) -> None:
            geschlossen.set()

    aufgabe = asyncio.create_task(
        cli._laufen_bis_signal(FakeDienst(), "127.0.0.1", 0)  # type: ignore[arg-type]
    )
    await asyncio.sleep(0.05)
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(aufgabe, timeout=5)
    assert geschlossen.is_set()


async def test_start_aus_gespeichertem_plan_ohne_core(
    dienst: tuple[Dienst, CoreSimulator], ha: HaSimulator, uhr: SimulierteUhr
) -> None:
    """Ein gespeicherter Plan gilt auch, wenn das Hauptsystem beim Start nicht erreichbar ist
    (Hallendienst-Spec § 5 „Start“): der Dienst schaltet trotzdem, und `dienst_gestartet` bleibt
    bis zur Rückkehr des Hauptsystems in der Warteschlange (Ruling Task 11 Fix-Runde 1)."""
    d, core = dienst
    speichere_plan(d.sitzungen, buchung(F1, t(19), t(21)))
    core.offline = True
    uhr.stelle(t(18, 56))  # im Lichtvorlauf (5 min vor Buchungsbeginn)
    aufgabe = asyncio.create_task(d.laufen())
    try:

        async def licht_an() -> None:
            while ha.zustaende["light.feld_1"]["state"] != "on":
                await asyncio.sleep(0.01)

        await asyncio.wait_for(licht_an(), timeout=5)
    finally:
        aufgabe.cancel()
        with pytest.raises(asyncio.CancelledError):
            await aufgabe
    assert core.empfangen == []
    assert "dienst_gestartet" in [e.typ for e in d.ereignisse.unbestaetigt(1000)]


async def test_laufen_holt_plan_verbindet_ha_und_meldet_start(
    dienst: tuple[Dienst, CoreSimulator], ha: HaSimulator
) -> None:
    d, core = dienst
    core.veroeffentliche(baue_plan([]))
    aufgabe = asyncio.create_task(d.laufen())
    try:

        async def bereit() -> None:
            while not (
                core.typen()
                and ha.abonnements() == 2
                and "sensor.beachhub_planversion" in ha.geschrieben
                and d.status().planversion == 1
            ):
                await asyncio.sleep(0.01)

        await asyncio.wait_for(bereit(), timeout=5)
        assert core.typen()[0] == "dienst_gestartet"
    finally:
        aufgabe.cancel()
        with pytest.raises(asyncio.CancelledError):
            await aufgabe


async def test_schliesse_reihenfolge(
    sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator, tmp_path: Path
) -> None:
    """Herunterfahren in der vom Controller vorgegebenen Reihenfolge: zuerst laufende
    Tastenfeld-Eingaben, dann die Tür, dann HA- und Hauptsystem-Verbindung (Ruling Task 8/11)."""
    d, _core = await baue_dienst(sitzungen, uhr, ha, tmp_path)
    reihenfolge: list[str] = []

    async def merke_zuhoerer() -> None:
        reihenfolge.append("zuhoerer")

    async def merke_tuer() -> None:
        reihenfolge.append("tuer")

    async def merke_ha() -> None:
        reihenfolge.append("ha")

    async def merke_core() -> None:
        reihenfolge.append("core")

    d.zuhoerer.beende_eingaben = merke_zuhoerer  # type: ignore[method-assign]
    d.tuer.schliesse = merke_tuer  # type: ignore[method-assign]
    d.ha.schliesse = merke_ha  # type: ignore[method-assign]
    d.core.schliesse = merke_core  # type: ignore[method-assign]

    await d.schliesse()
    assert reihenfolge == ["zuhoerer", "tuer", "ha", "core"]
