"""Fake-Hauptsystem für Tests: signiert Pläne wie das echte und nimmt Ereignisse an.

Läuft als httpx.MockTransport im selben Prozess – ohne Netz, ohne Wartezeit. `offline = True`
simuliert den Internetausfall, `ersatzantwort` eine Fehlerseite des Reverse Proxys.
"""

import httpx
from beachhub_hall.core import CoreClient
from beachhub_shared.hallenplan import (
    EreignisLieferung,
    HallenEreignis,
    HallenplanInhalt,
    HallenStatus,
)
from beachhub_shared.lesestand import Dokument
from beachhub_shared.signatur import erzeuge_schluesselpaar

from tests.hilfen import signiertes_dokument


class CoreSimulator:
    TOKEN = "hall-test-token"
    BASIS = "http://core.test"

    def __init__(self) -> None:
        self.privat, self.oeffentlich = erzeuge_schluesselpaar()
        self.dokument: Dokument | None = None
        self.empfangen: list[HallenEreignis] = []
        self.status: list[HallenStatus] = []
        self.offline = False
        self.plan_neu = False
        self.ersatzantwort: httpx.Response | None = None
        self.anfragen = 0

    def veroeffentliche(
        self,
        inhalt: HallenplanInhalt,
        *,
        version: int | None = None,
        privat: str | None = None,
        dokument: str = "hallenplan",
    ) -> Dokument:
        if version is None:
            version = self.dokument.version + 1 if self.dokument else 1
        self.dokument = signiertes_dokument(inhalt, version, privat or self.privat, dokument)
        return self.dokument

    def _antwort(self, request: httpx.Request) -> httpx.Response:
        self.anfragen += 1
        if self.offline:
            raise httpx.ConnectError("Hauptsystem nicht erreichbar", request=request)
        if self.ersatzantwort is not None:
            return self.ersatzantwort
        if request.headers.get("authorization") != f"Bearer {self.TOKEN}":
            return httpx.Response(401)
        if request.method == "GET" and request.url.path == "/hall/plan":
            if self.dokument is None:
                return httpx.Response(404)
            if int(request.url.params.get("ab", "0")) == self.dokument.version:
                return httpx.Response(304)
            return httpx.Response(200, json=self.dokument.model_dump(mode="json"))
        if request.method == "POST" and request.url.path == "/hall/ereignisse":
            lieferung = EreignisLieferung.model_validate_json(request.content)
            bekannt = {e.seq for e in self.empfangen}
            self.empfangen += [e for e in lieferung.ereignisse if e.seq not in bekannt]
            if lieferung.status is not None:
                self.status.append(lieferung.status)
            neu, self.plan_neu = self.plan_neu, False
            hoechste = max((e.seq for e in self.empfangen), default=0)
            return httpx.Response(200, json={"bestaetigt_bis": hoechste, "plan_neu": neu})
        return httpx.Response(404)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._antwort)

    def client(self) -> CoreClient:
        return CoreClient(self.BASIS, self.TOKEN, transport=self.transport())

    def typen(self) -> list[str]:
        return [e.typ for e in self.empfangen]
