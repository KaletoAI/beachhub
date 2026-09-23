"""Setzt den Hallendienst aus seinen Teilen zusammen und startet die Aufgaben.

Nach dem Start arbeitet der Dienst ab der ersten Sekunde aus dem gespeicherten Plan – noch
bevor Hauptsystem oder HA erreichbar sind (Hallendienst-Spec § 5 „Start“).
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from beachhub_shared.hallenplan import HallenStatus
from sqlalchemy.orm import Session, sessionmaker

from beachhub_hall import __version__
from beachhub_hall.aufgaben import takt
from beachhub_hall.aufgaben.ha_zuhoerer import HaZuhoerer
from beachhub_hall.aufgaben.melder import Melder, baue_status
from beachhub_hall.aufgaben.plan_abruf import PlanAbruf
from beachhub_hall.aufgaben.status_ha import StatusHa
from beachhub_hall.aufgaben.steuerung import Steuerung
from beachhub_hall.clock import EchteUhr, Uhr
from beachhub_hall.config import Umgebung, Zuordnung, lade_zuordnung
from beachhub_hall.core import CoreClient, ssl_kontext
from beachhub_hall.db import oeffne
from beachhub_hall.ereignisse import Ereignisse
from beachhub_hall.ha import HaClient
from beachhub_hall.lage import Lage
from beachhub_hall.pin import PinPruefer
from beachhub_hall.tuer import Tuer

logger = logging.getLogger(__name__)

_BACKOFF_START = 1.0
_BACKOFF_MAX = 60.0


async def _dauerhaft(
    name: str,
    arbeit: Callable[[], Awaitable[None]],
    *,
    schlafen: Callable[[float], Awaitable[None]] = asyncio.sleep,
    jetzt: Callable[[], float] = time.monotonic,
) -> None:
    """Startet `arbeit()` neu, wenn sie mit einer unerwarteten Ausnahme endet, statt den Dienst
    mitzureißen. Für Aufgaben, die – anders als die `takt()`-Aufgaben mit ihrem `einmal()` –
    selbst schon eine Dauerschleife sind, wie `HaZuhoerer.laufen()`. `laufen()` fängt bereits
    jede Ausnahme in seiner eigenen Schleife (je Ereignis und in der äußeren Schleife); dieser
    Wächter ist die zweite Sicherung, falls trotzdem einmal eine unerwartete Ausnahme bis
    hierher durchreicht – ohne ihn würde `asyncio.gather` in `laufen()` sonst alle anderen
    Aufgaben mit abbrechen.

    Ein sofortiger Neustart ohne Pause wäre bei einer dauerhaften Ursache eine heiße Schleife
    (Log-Flut, blockierte Ereignisschleife) – deshalb wächst die Pause nach jedem Fehlschlag
    von 1 s auf bis zu 60 s. Lief `arbeit()` vor dem Fehler mindestens so lange wie die
    maximale Pause, gilt die Aufgabe als erholt: die Pause beginnt wieder bei 1 s."""
    backoff = _BACKOFF_START
    while True:
        start = jetzt()
        try:
            await arbeit()
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            if jetzt() - start >= _BACKOFF_MAX:
                backoff = _BACKOFF_START
            logger.exception("Aufgabe %s unerwartet beendet, Neustart in %.0f s", name, backoff)
            await schlafen(backoff)
            backoff = min(_BACKOFF_MAX, backoff * 2)


class Dienst:
    def __init__(
        self,
        *,
        oeffentlich_hex: str,
        zuordnung: Zuordnung,
        sitzungen: sessionmaker[Session],
        uhr: Uhr,
        ha: HaClient,
        core: CoreClient,
        schlafen: Callable[[float], Awaitable[None]] = asyncio.sleep,
        zuhoerer_backoff: float = 1.0,
    ) -> None:
        self.zuordnung = zuordnung
        self.sitzungen = sitzungen
        self.uhr = uhr
        self.ha = ha
        self.core = core
        self.lage = Lage()
        self.ereignisse = Ereignisse(sitzungen, uhr)
        self.steuerung = Steuerung(sitzungen, uhr, zuordnung, ha, self.ereignisse, self.lage)
        self.plan_abruf = PlanAbruf(
            sitzungen,
            uhr,
            core,
            oeffentlich_hex,
            self.ereignisse,
            zuordnung,
            self.steuerung.wecker.set,
        )
        self.status_ha = StatusHa(sitzungen, uhr, ha, self.ereignisse)
        self.melder = Melder(
            sitzungen, uhr, core, self.ereignisse, self.status, self.plan_abruf.wecker
        )
        self.tuer = Tuer(ha, zuordnung.tuer, self.ereignisse, schlafen)
        self.pruefer = PinPruefer(sitzungen, uhr, zuordnung, self.ereignisse, self.tuer, schlafen)
        self.zuhoerer = HaZuhoerer(
            sitzungen,
            uhr,
            zuordnung,
            ha,
            self.ereignisse,
            self.lage,
            self.pruefer,
            self.steuerung.wecker,
            self.status_ha.wecker,
            backoff_start=zuhoerer_backoff,
        )

    @classmethod
    def aus_umgebung(cls, u: Umgebung) -> "Dienst":
        zuordnung = lade_zuordnung(u.hall_toml)
        return cls(
            oeffentlich_hex=u.core_public_key,
            zuordnung=zuordnung,
            sitzungen=oeffne(u.data_dir / "hall.sqlite"),
            uhr=EchteUhr(),
            ha=HaClient(u.ha_url, u.ha_token),
            core=CoreClient(
                u.core_url,
                u.hall_token,
                verify=ssl_kontext(u.core_ca, u.core_client_cert, u.core_client_key),
            ),
        )

    def status(self) -> HallenStatus:
        return baue_status(self.sitzungen, self.zuordnung, self.lage, self.ereignisse)

    def gestartet(self) -> None:
        self.ereignisse.melde("dienst_gestartet", version=__version__)

    async def _aufraeumen(self) -> None:
        geloescht = self.ereignisse.raeume_auf()
        if geloescht:
            logger.info("%s bestätigte Ereignisse älter als 90 Tage gelöscht", geloescht)

    async def laufen(self) -> None:
        self.gestartet()
        aufgaben = [
            asyncio.create_task(
                takt("steuerung", self.steuerung.einmal, 30, self.steuerung.wecker)
            ),
            asyncio.create_task(takt("plan", self.plan_abruf.einmal, 300, self.plan_abruf.wecker)),
            asyncio.create_task(_dauerhaft("zuhoerer", self.zuhoerer.laufen)),
            asyncio.create_task(takt("melder", self.melder.einmal, 60, self.ereignisse.neu)),
            asyncio.create_task(
                takt("status_ha", self.status_ha.einmal, 60, self.status_ha.wecker)
            ),
            asyncio.create_task(takt("aufraeumen", self._aufraeumen, 24 * 3600)),
        ]
        try:
            await asyncio.gather(*aufgaben)
        finally:
            for aufgabe in aufgaben:
                aufgabe.cancel()
            await asyncio.gather(*aufgaben, return_exceptions=True)

    async def schliesse(self) -> None:
        """Fährt in der vom Controller vorgegebenen Reihenfolge herunter (die laufenden
        `takt()`-Aufgaben sind zu diesem Zeitpunkt bereits über `laufen()`s eigenes `finally`
        gestoppt): erst laufende Tastenfeld-Eingaben beenden – sonst griffe eine Eingabe noch
        auf die gleich geschlossene Tür oder HA-Verbindung zu –, dann die Tür (schaltet einen
        `switch.*`-Türöffner sicherheitshalber aus), dann HA- und Hauptsystem-Verbindung. Die
        Datenbank wird hier nirgends aktiv geschlossen – jede Sitzung öffnet und schließt sich
        selbst (`with sitzungen() as db`) –, sie bleibt also bis zuletzt nutzbar, u. a. falls
        `tuer.schliesse()` noch einen `aktor_fehler` meldet."""
        await self.zuhoerer.beende_eingaben()
        await self.tuer.schliesse()
        await self.ha.schliesse()
        await self.core.schliesse()
