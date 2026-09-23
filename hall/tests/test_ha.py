import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import aiohttp
import pytest
from beachhub_hall.ha import HaClient, HaFehler, HaNichtErreichbar, HaWebSocket

from tests.ha_simulator import HaSimulator


@pytest.fixture
async def client(ha: HaSimulator) -> AsyncIterator[HaClient]:
    c = HaClient(ha.url, HaSimulator.TOKEN)
    yield c
    await c.schliesse()


async def test_rest_lesen_schreiben_schalten(ha: HaSimulator, client: HaClient) -> None:
    z = await client.zustand("light.feld_1")
    assert z is not None and z["state"] == "off"
    assert await client.zustand("light.gibt_es_nicht") is None
    assert {s["entity_id"] for s in await client.zustaende()} >= {"light.feld_1", "climate.halle"}
    await client.dienst("light", "turn_on", {"entity_id": "light.feld_1"})
    assert ha.zustaende["light.feld_1"]["state"] == "on"
    assert ha.aufrufe[-1] == ("light", "turn_on", {"entity_id": "light.feld_1"})
    await client.dienst(
        "climate", "set_temperature", {"entity_id": "climate.halle", "temperature": 18.0}
    )
    assert ha.zustaende["climate.halle"]["attributes"]["temperature"] == 18.0
    await client.setze_zustand("sensor.beachhub_planversion", "3", {"friendly_name": "Planversion"})
    assert ha.geschrieben["sensor.beachhub_planversion"]["state"] == "3"


async def test_fehler_werden_zu_hafehler(ha: HaSimulator) -> None:
    falsch = HaClient(ha.url, "falsches-token")
    with pytest.raises(HaFehler):
        await falsch.zustand("light.feld_1")
    await falsch.schliesse()
    ha.fehler_bei_diensten = True
    c = HaClient(ha.url, HaSimulator.TOKEN)
    with pytest.raises(HaFehler):
        await c.dienst("light", "turn_on", {"entity_id": "light.feld_1"})
    await c.schliesse()
    weg = HaClient("http://127.0.0.1:9", HaSimulator.TOKEN)
    # Echte Verbindungsfehler (kein Host erreichbar) werden zu HaNichtErreichbar – nicht nur
    # zur allgemeinen HaFehler-Basisklasse –, damit Aufrufer wie die Steuerung oder der
    # HA-Zuhörer sie von einer Fehlerantwort für eine einzelne Anfrage unterscheiden können.
    with pytest.raises(HaNichtErreichbar):
        await weg.zustand("light.feld_1")
    with pytest.raises(HaNichtErreichbar):
        await weg.websocket()
    await weg.schliesse()


async def test_websocket_abonnieren_und_empfangen(ha: HaSimulator, client: HaClient) -> None:
    ws = await client.websocket()
    await ws.abonniere("state_changed")
    await ws.abonniere("esphome.beachhub_pin")
    assert ha.abonnements() == 2
    await ha.setze("binary_sensor.praesenz_feld_1", "on")
    event = await asyncio.wait_for(ws.naechstes(), timeout=2)
    assert event["event_type"] == "state_changed"
    assert event["data"]["entity_id"] == "binary_sensor.praesenz_feld_1"
    assert event["data"]["new_state"]["state"] == "on"
    await ha.feuere("esphome.beachhub_pin", {"code": "482913"})
    event = await asyncio.wait_for(ws.naechstes(), timeout=2)
    assert event["event_type"] == "esphome.beachhub_pin"
    assert event["data"] == {"code": "482913"}
    await ws.schliesse()


async def test_websocket_event_waehrend_zweitem_abo_geht_nicht_verloren(
    ha: HaSimulator, client: HaClient
) -> None:
    ws = await client.websocket()
    await ws.abonniere("state_changed")
    await ha.setze("binary_sensor.praesenz_feld_1", "on")
    await ws.abonniere("esphome.beachhub_pin")
    event = await asyncio.wait_for(ws.naechstes(), timeout=2)
    assert event["event_type"] == "state_changed"
    assert event["data"]["entity_id"] == "binary_sensor.praesenz_feld_1"
    await ws.schliesse()


async def test_websocket_kaputtes_json_wird_zu_hafehler() -> None:
    """Eine WebSocket-Nachricht, die kein gültiges JSON ist, darf nicht als rohe
    JSONDecodeError durchschlagen – sie wird wie jeder andere Protokollfehler zu HaFehler, damit
    der HA-Zuhörer sie wie gewohnt fängt und neu verbindet, statt abzustürzen."""

    class FakeWs:
        def __init__(self) -> None:
            self._gesendet = False

        async def receive(self) -> Any:
            self._gesendet = True
            return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data="{kaputtes json")

        async def close(self) -> None:
            return None

    ws = HaWebSocket(FakeWs())  # type: ignore[arg-type]
    with pytest.raises(HaFehler):
        await ws.naechstes()


async def test_websocket_falsches_token_und_trennung(ha: HaSimulator) -> None:
    falsch = HaClient(ha.url, "falsches-token")
    with pytest.raises(HaFehler, match="Anmeldung"):
        await falsch.websocket()
    await falsch.schliesse()
    c = HaClient(ha.url, HaSimulator.TOKEN)
    ws = await c.websocket()
    await ws.abonniere("state_changed")
    await ha.trenne_alle()
    with pytest.raises(HaFehler):
        await asyncio.wait_for(ws.naechstes(), timeout=2)
    await c.schliesse()
