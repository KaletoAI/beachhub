"""Fake-Home-Assistant für Tests und lokale Probeläufe.

REST: GET /api/states, GET/POST /api/states/<entity_id>, POST /api/services/<domain>/<service>.
WebSocket /api/websocket nach dem echten Protokoll: auth_required → auth → auth_ok,
subscribe_events → result, danach Nachrichten vom Typ "event".

Lokal starten (z. B. für einen Probelauf ohne echtes HA): `cd hall && python -m tests.ha_simulator`
"""

import json
from datetime import UTC, datetime
from typing import Any

from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer


def _jetzt() -> str:
    return datetime.now(UTC).isoformat()


class HaSimulator:
    TOKEN = "ha-test-token"

    def __init__(self) -> None:
        self.zustaende: dict[str, dict[str, Any]] = {}
        self.aufrufe: list[tuple[str, str, dict[str, Any]]] = []
        self.geschrieben: dict[str, dict[str, Any]] = {}
        self.fehler_bei_diensten = False
        # Lässt genau die nächsten N Dienstaufrufe scheitern (unabhängig von
        # fehler_bei_diensten), danach läuft der Simulator wieder normal – für Tests von
        # Wiederholungslogik (z. B. „scheitert zweimal, dritter Versuch klappt“).
        self.fehler_verbleibend = 0
        # Entity-IDs, für die GET /api/states/<id> mit 500 statt dem echten Zustand antwortet –
        # für Tests, dass der Fehler einer Entität die übrigen nicht blockiert.
        self.fehler_bei_zustand: set[str] = set()
        self.verbindungen_gesamt = 0
        self._abos: dict[web.WebSocketResponse, dict[int, str | None]] = {}
        self._server: TestServer | None = None
        self.url = ""
        self.app = web.Application(middlewares=[self._auth])
        self.app.router.add_get("/api/states", self._alle)
        self.app.router.add_get("/api/states/{entity_id}", self._einer)
        self.app.router.add_post("/api/states/{entity_id}", self._schreiben)
        self.app.router.add_post("/api/services/{domain}/{service}", self._dienst)
        self.app.router.add_get("/api/websocket", self._websocket)

    # --- Steuerung durch Tests -------------------------------------------------------

    def entitaet(self, entity_id: str, state: str, **attribute: Any) -> None:
        self.zustaende[entity_id] = {
            "entity_id": entity_id,
            "state": state,
            "attributes": attribute,
            "last_changed": _jetzt(),
        }

    async def setze(self, entity_id: str, state: str, **attribute: Any) -> None:
        alt = self.zustaende.get(entity_id)
        attr = {**(alt or {}).get("attributes", {}), **attribute}
        self.entitaet(entity_id, state, **attr)
        await self._state_changed(entity_id, alt)

    async def feuere(self, event_type: str, daten: dict[str, Any]) -> None:
        await self._sende_event(event_type, daten)

    def abonnements(self) -> int:
        return sum(len(a) for a in self._abos.values())

    async def trenne_alle(self) -> None:
        for ws in list(self._abos):
            await ws.close()

    async def start(self) -> None:
        self._server = TestServer(self.app, host="127.0.0.1")
        await self._server.start_server()
        self.url = str(self._server.make_url("")).rstrip("/")

    async def stop(self) -> None:
        await self.trenne_alle()
        if self._server is not None:
            await self._server.close()

    # --- intern ------------------------------------------------------------------------

    @web.middleware
    async def _auth(self, request: web.Request, handler: Any) -> web.StreamResponse:
        if (
            request.path != "/api/websocket"
            and request.headers.get("Authorization") != f"Bearer {self.TOKEN}"
        ):
            return web.json_response({"message": "401: Unauthorized"}, status=401)
        antwort: web.StreamResponse = await handler(request)
        return antwort

    async def _alle(self, request: web.Request) -> web.Response:
        return web.json_response(list(self.zustaende.values()))

    async def _einer(self, request: web.Request) -> web.Response:
        entity_id = request.match_info["entity_id"]
        if entity_id in self.fehler_bei_zustand:
            return web.json_response({"message": "Internal error"}, status=500)
        z = self.zustaende.get(entity_id)
        if z is None:
            return web.json_response({"message": "Entity not found."}, status=404)
        return web.json_response(z)

    async def _schreiben(self, request: web.Request) -> web.Response:
        entity_id = request.match_info["entity_id"]
        daten = await request.json()
        self.geschrieben[entity_id] = daten
        neu = entity_id not in self.zustaende
        await self.setze(entity_id, str(daten["state"]), **daten.get("attributes", {}))
        return web.json_response(self.zustaende[entity_id], status=201 if neu else 200)

    async def _dienst(self, request: web.Request) -> web.Response:
        domain, service = request.match_info["domain"], request.match_info["service"]
        daten = await request.json()
        self.aufrufe.append((domain, service, daten))
        fehlschlagen = self.fehler_bei_diensten
        if self.fehler_verbleibend > 0:
            fehlschlagen = True
            self.fehler_verbleibend -= 1
        if fehlschlagen:
            return web.json_response({"message": "Service call failed"}, status=500)
        ziele = daten.get("entity_id", [])
        entity_ids = [ziele] if isinstance(ziele, str) else ziele
        if (domain, service) == ("climate", "set_temperature"):
            # Wie ein echtes climate-Gerät: eine Solltemperatur außerhalb von min_temp/max_temp
            # wird abgelehnt (400), nicht stillschweigend übernommen oder gekappt.
            temperatur = daten["temperature"]
            for entity_id in entity_ids:
                attribute = self.zustaende.get(entity_id, {}).get("attributes", {})
                minimum, maximum = attribute.get("min_temp"), attribute.get("max_temp")
                if (minimum is not None and temperatur < minimum) or (
                    maximum is not None and temperatur > maximum
                ):
                    return web.json_response(
                        {"message": f"{temperatur} liegt außerhalb {minimum}..{maximum}"},
                        status=400,
                    )
        for entity_id in entity_ids:
            if entity_id not in self.zustaende:
                continue  # wie echtes HA: unbekannte Entität, keine Wirkung
            if (domain, service) in (("light", "turn_on"), ("switch", "turn_on")):
                await self.setze(entity_id, "on")
            elif (domain, service) in (("light", "turn_off"), ("switch", "turn_off")):
                await self.setze(entity_id, "off")
            elif (domain, service) == ("lock", "unlock"):
                await self.setze(entity_id, "unlocked")
            elif (domain, service) == ("lock", "lock"):
                await self.setze(entity_id, "locked")
            elif (domain, service) == ("climate", "set_temperature"):
                await self.setze(
                    entity_id, self.zustaende[entity_id]["state"], temperature=daten["temperature"]
                )
        return web.json_response([])

    async def _state_changed(self, entity_id: str, alt: dict[str, Any] | None) -> None:
        await self._sende_event(
            "state_changed",
            {"entity_id": entity_id, "old_state": alt, "new_state": self.zustaende.get(entity_id)},
        )

    async def _sende_event(self, event_type: str, daten: dict[str, Any]) -> None:
        for ws, abos in list(self._abos.items()):
            for sub_id, typ in abos.items():
                if typ is None or typ == event_type:
                    nachricht = {
                        "id": sub_id,
                        "type": "event",
                        "event": {
                            "event_type": event_type,
                            "data": daten,
                            "origin": "LOCAL",
                            "time_fired": _jetzt(),
                        },
                    }
                    if not ws.closed:
                        await ws.send_json(nachricht)

    async def _websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "auth_required", "ha_version": "2027.11.0"})
        auth = await ws.receive()
        daten = json.loads(auth.data) if auth.type == WSMsgType.TEXT else {}
        if daten.get("type") != "auth" or daten.get("access_token") != self.TOKEN:
            await ws.send_json({"type": "auth_invalid", "message": "Invalid access token"})
            await ws.close()
            return ws
        await ws.send_json({"type": "auth_ok", "ha_version": "2027.11.0"})
        self.verbindungen_gesamt += 1
        self._abos[ws] = {}
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                befehl = json.loads(msg.data)
                if befehl.get("type") == "subscribe_events":
                    self._abos[ws][befehl["id"]] = befehl.get("event_type")
                    await ws.send_json(
                        {"id": befehl["id"], "type": "result", "success": True, "result": None}
                    )
                else:
                    await ws.send_json(
                        {
                            "id": befehl.get("id"),
                            "type": "result",
                            "success": False,
                            "error": {"code": "unknown_command", "message": "Unknown command."},
                        }
                    )
        finally:
            self._abos.pop(ws, None)
        return ws


def standard_entitaeten(sim: HaSimulator) -> None:
    for e in ("light.feld_1", "light.feld_2"):
        sim.entitaet(e, "off")
    for e in (
        "binary_sensor.praesenz_feld_1",
        "binary_sensor.praesenz_feld_2",
        "binary_sensor.tuer",
    ):
        sim.entitaet(e, "off")
    sim.entitaet(
        "climate.halle",
        "heat",
        # 7.0 (min_temp) statt 0.0: ein echtes climate-Gerät hätte nie einen Sollwert unter
        # seiner eigenen Grenze – 0.0 wird gezielt in einzelnen Tests gesetzt, die die
        # Begrenzung selbst prüfen.
        temperature=7.0,
        current_temperature=5.0,
        min_temp=7.0,
        max_temp=35.0,
    )
    sim.entitaet("lock.eingang", "locked")
    sim.entitaet("input_boolean.beachhub_handbetrieb", "off")


if __name__ == "__main__":
    simulator = HaSimulator()
    standard_entitaeten(simulator)
    print(f"HA-Simulator auf http://127.0.0.1:8123, Token: {HaSimulator.TOKEN}")  # noqa: T201
    web.run_app(simulator.app, host="127.0.0.1", port=8123)
