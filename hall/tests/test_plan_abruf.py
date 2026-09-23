from collections.abc import AsyncIterator

import httpx
import pytest
from beachhub_hall import plan
from beachhub_hall.aufgaben.plan_abruf import PlanAbruf
from beachhub_hall.clock import SimulierteUhr
from beachhub_hall.config import FeldZuordnung, Zuordnung
from beachhub_hall.db import lies
from beachhub_hall.ereignisse import Ereignisse
from beachhub_shared.signatur import erzeuge_schluesselpaar
from sqlalchemy.orm import Session, sessionmaker

from tests.core_simulator import CoreSimulator
from tests.hilfen import F1, F2, MASTER_HASH, baue_plan, buchung, t

ZUORDNUNG = Zuordnung(
    master_pin_hash=MASTER_HASH,
    felder={F1: FeldZuordnung("light.feld_1"), F2: FeldZuordnung("light.feld_2")},
)


class Aufbau:
    def __init__(
        self,
        sitzungen: sessionmaker[Session],
        uhr: SimulierteUhr,
        core: CoreSimulator,
        zuordnung: Zuordnung = ZUORDNUNG,
    ) -> None:
        self.core = core
        self.sitzungen = sitzungen
        self.ereignisse = Ereignisse(sitzungen, uhr)
        self.angestossen = 0
        self.client = core.client()
        self.abruf = PlanAbruf(
            sitzungen,
            uhr,
            self.client,
            core.oeffentlich,
            self.ereignisse,
            zuordnung,
            self._anstossen,
        )

    def _anstossen(self) -> None:
        self.angestossen += 1

    def typen(self) -> list[str]:
        return [e.typ for e in self.ereignisse.unbestaetigt()]


@pytest.fixture
async def a(sitzungen: sessionmaker[Session], uhr: SimulierteUhr) -> AsyncIterator[Aufbau]:
    aufbau = Aufbau(sitzungen, uhr, CoreSimulator())
    yield aufbau
    await aufbau.client.schliesse()


async def test_neuer_plan_wird_gespeichert_danach_304(a: Aufbau) -> None:
    a.core.veroeffentliche(baue_plan([buchung(F1, t(19), t(20))]))
    assert await a.abruf.einmal() is True
    assert a.angestossen == 1
    with a.sitzungen() as db:
        assert plan.version(db) == 1
        assert lies(db, "letzter_abruf") == t(17).isoformat()
    assert await a.abruf.einmal() is False
    assert a.angestossen == 1
    a.core.veroeffentliche(baue_plan([]))
    assert await a.abruf.einmal() is True
    with a.sitzungen() as db:
        assert plan.version(db) == 2
    assert a.typen() == []


async def test_falsche_signatur_wird_verworfen_und_gemeldet(a: Aufbau) -> None:
    a.core.veroeffentliche(baue_plan([]))
    await a.abruf.einmal()
    fremd, _ = erzeuge_schluesselpaar()
    a.core.veroeffentliche(baue_plan([buchung(F1, t(19), t(20))]), privat=fremd)
    assert await a.abruf.einmal() is False
    with a.sitzungen() as db:
        assert plan.version(db) == 1
    verworfen = [e for e in a.ereignisse.unbestaetigt() if e.typ == "plan_verworfen"]
    assert len(verworfen) == 1 and verworfen[0].daten == {"grund": "signatur", "version": 2}


async def test_offline_ist_kein_ereignis(a: Aufbau) -> None:
    a.core.offline = True
    assert await a.abruf.einmal() is False
    assert a.typen() == []
    with a.sitzungen() as db:
        assert lies(db, "letzter_abruf") is None


async def test_fehlerseite_und_kaputtes_json(a: Aufbau) -> None:
    a.core.veroeffentliche(baue_plan([]))
    await a.abruf.einmal()
    a.core.veroeffentliche(baue_plan([buchung(F1, t(19), t(20))]))
    a.core.ersatzantwort = httpx.Response(502, text="<html>Bad Gateway</html>")
    assert await a.abruf.einmal() is False
    a.core.ersatzantwort = httpx.Response(200, text="<html>kein JSON</html>")
    assert await a.abruf.einmal() is False
    a.core.ersatzantwort = httpx.Response(200, json=[1, 2, 3])
    assert await a.abruf.einmal() is False
    assert a.typen() == []
    with a.sitzungen() as db:
        assert plan.version(db) == 1


async def test_nicht_zugeordnetes_feld_wird_einmal_gemeldet(
    sitzungen: sessionmaker[Session], uhr: SimulierteUhr
) -> None:
    nur_f1 = Zuordnung(master_pin_hash=MASTER_HASH, felder={F1: FeldZuordnung("light.feld_1")})
    aufbau = Aufbau(sitzungen, uhr, CoreSimulator(), nur_f1)
    aufbau.core.veroeffentliche(baue_plan([]))
    await aufbau.abruf.einmal()
    aufbau.core.veroeffentliche(baue_plan([]))
    await aufbau.abruf.einmal()
    fehler = [e for e in aufbau.ereignisse.unbestaetigt() if e.typ == "aktor_fehler"]
    assert len(fehler) == 1
    assert fehler[0].feld_id == F2 and fehler[0].daten == {"grund": "feld_nicht_zugeordnet"}
    await aufbau.client.schliesse()
