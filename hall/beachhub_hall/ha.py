"""Home Assistant über REST und WebSocket mit Long-Lived Access Token – keine Custom
Component (Hauptspec § 8.2).

Jeder Fehler wird zu `HaFehler`. Die Aufrufer unterscheiden nur „HA hat geantwortet“ und
„HA hat nicht geantwortet“; was daraus folgt (warten, melden), entscheiden sie selbst.
"""

import json
from typing import Any

import aiohttp


class HaFehler(Exception):  # noqa: N818
    pass


class HaWebSocket:
    def __init__(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        self._ws = ws
        self._id = 0

    async def _lies(self) -> dict[str, Any]:
        try:
            msg = await self._ws.receive()
        except (aiohttp.ClientError, TimeoutError) as e:
            raise HaFehler(f"WebSocket: {e}") from e
        if msg.type != aiohttp.WSMsgType.TEXT:
            raise HaFehler(f"WebSocket beendet ({msg.type.name})")
        daten = json.loads(msg.data)
        if not isinstance(daten, dict):
            raise HaFehler("WebSocket: unerwartete Nachricht")
        return daten

    async def _sende(self, nachricht: dict[str, Any]) -> None:
        try:
            await self._ws.send_json(nachricht)
        except (aiohttp.ClientError, ConnectionError) as e:
            raise HaFehler(f"WebSocket: {e}") from e

    async def anmelden(self, token: str) -> None:
        if (await self._lies()).get("type") != "auth_required":
            raise HaFehler("WebSocket: kein auth_required")
        await self._sende({"type": "auth", "access_token": token})
        if (await self._lies()).get("type") != "auth_ok":
            raise HaFehler("HA-Anmeldung abgelehnt – Token prüfen")

    async def abonniere(self, event_type: str) -> None:
        self._id += 1
        await self._sende({"id": self._id, "type": "subscribe_events", "event_type": event_type})
        while True:
            antwort = await self._lies()
            if antwort.get("type") == "result" and antwort.get("id") == self._id:
                if not antwort.get("success"):
                    raise HaFehler(f"Abonnement {event_type} abgelehnt")
                return

    async def naechstes(self) -> dict[str, Any]:
        while True:
            nachricht = await self._lies()
            if nachricht.get("type") == "event" and isinstance(nachricht.get("event"), dict):
                event: dict[str, Any] = nachricht["event"]
                return event

    async def schliesse(self) -> None:
        await self._ws.close()


class HaClient:
    def __init__(self, basis_url: str, token: str) -> None:
        self._basis = basis_url.rstrip("/")
        self._token = token
        self._session: aiohttp.ClientSession | None = None

    def _s(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=aiohttp.ClientTimeout(total=15),
            )
        return self._session

    async def _anfrage(self, methode: str, pfad: str, daten: dict[str, Any] | None = None) -> Any:
        try:
            async with self._s().request(methode, self._basis + pfad, json=daten) as r:
                if r.status == 404:
                    return None
                if r.status >= 400:
                    raise HaFehler(f"HA {methode} {pfad}: HTTP {r.status}")
                return await r.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as e:
            raise HaFehler(f"HA {methode} {pfad}: {e}") from e

    async def zustand(self, entity_id: str) -> dict[str, Any] | None:
        z = await self._anfrage("GET", f"/api/states/{entity_id}")
        return z if isinstance(z, dict) else None

    async def zustaende(self) -> list[dict[str, Any]]:
        z = await self._anfrage("GET", "/api/states")
        return [s for s in z if isinstance(s, dict)] if isinstance(z, list) else []

    async def dienst(self, domain: str, service: str, daten: dict[str, Any]) -> None:
        await self._anfrage("POST", f"/api/services/{domain}/{service}", daten)

    async def setze_zustand(self, entity_id: str, state: str, attribute: dict[str, Any]) -> None:
        await self._anfrage(
            "POST", f"/api/states/{entity_id}", {"state": state, "attributes": attribute}
        )

    async def websocket(self) -> HaWebSocket:
        try:
            ws = await self._s().ws_connect(self._basis + "/api/websocket", heartbeat=30)
        except (aiohttp.ClientError, TimeoutError) as e:
            raise HaFehler(f"HA-WebSocket: {e}") from e
        verbindung = HaWebSocket(ws)
        try:
            await verbindung.anmelden(self._token)
        except HaFehler:
            await verbindung.schliesse()
            raise
        return verbindung

    async def schliesse(self) -> None:
        if self._session is not None:
            await self._session.close()
