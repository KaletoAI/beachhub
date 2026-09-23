"""Verbindung zum Hauptsystem (Hauptspec § 8.2): HTTPS mit mTLS über WireGuard.

Jeder Fehler – Netz, HTTP-Status, kein JSON – wird zu `CoreNichtErreichbar`. Für den Dienst
ist das derselbe Fall: Er arbeitet mit dem gespeicherten Plan weiter und versucht es später.
"""

import ssl
from typing import Any

import httpx
from beachhub_shared.hallenplan import EreignisAntwort, EreignisLieferung
from pydantic import ValidationError


class CoreNichtErreichbar(Exception):  # noqa: N818
    pass


def ssl_kontext(ca: str, cert: str, key: str) -> ssl.SSLContext | bool:
    """CA des Caddy vor dem Hauptsystem und Client-Zertifikat der Halle. Ohne beides (lokale
    Entwicklung über http://) prüft httpx wie üblich."""
    if not ca and not cert:
        return True
    kontext = ssl.create_default_context(cafile=ca or None)
    if cert:
        kontext.load_cert_chain(cert, key or None)
    return kontext


class CoreClient:
    def __init__(
        self,
        basis_url: str,
        token: str,
        *,
        verify: ssl.SSLContext | bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=basis_url,
            headers={"Authorization": f"Bearer {token}"},
            verify=verify,
            transport=transport,
            timeout=httpx.Timeout(20.0),
        )

    async def hole_plan(self, ab: int) -> dict[str, Any] | None:
        try:
            r = await self._client.get("/hall/plan", params={"ab": ab})
        except httpx.HTTPError as e:
            raise CoreNichtErreichbar(str(e)) from e
        if r.status_code == 304:
            return None
        if r.status_code != 200:
            raise CoreNichtErreichbar(f"GET /hall/plan: HTTP {r.status_code}")
        try:
            daten = r.json()
        except ValueError as e:
            raise CoreNichtErreichbar("GET /hall/plan: kein JSON") from e
        if not isinstance(daten, dict):
            raise CoreNichtErreichbar("GET /hall/plan: kein JSON-Objekt")
        return daten

    async def sende_ereignisse(self, lieferung: EreignisLieferung) -> EreignisAntwort:
        try:
            r = await self._client.post("/hall/ereignisse", json=lieferung.model_dump(mode="json"))
        except httpx.HTTPError as e:
            raise CoreNichtErreichbar(str(e)) from e
        if r.status_code != 200:
            raise CoreNichtErreichbar(f"POST /hall/ereignisse: HTTP {r.status_code}")
        try:
            return EreignisAntwort.model_validate_json(r.content)
        except ValidationError as e:
            raise CoreNichtErreichbar("POST /hall/ereignisse: unerwartete Antwort") from e

    async def schliesse(self) -> None:
        await self._client.aclose()
