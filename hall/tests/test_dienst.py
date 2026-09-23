import asyncio
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
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import baue_dienst
from tests.core_simulator import CoreSimulator
from tests.ha_simulator import HaSimulator
from tests.hilfen import F1, MASTER_HASH, TOML_BEISPIEL, baue_plan


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

    aufgabe = asyncio.create_task(dienst_modul._dauerhaft("test", arbeit))
    await asyncio.sleep(0.01)
    assert len(aufrufe) == 2  # nach der Ausnahme sofort neu gestartet
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
    eingaben = iter(["4711", "4712"])
    with pytest.raises(SystemExit):
        cli.main(["master-pin"])


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
