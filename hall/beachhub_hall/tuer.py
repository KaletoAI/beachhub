"""Türöffner: lock.* wird entriegelt (das Schloss bzw. eine HA-Automation verriegelt wieder),
switch.* bekommt einen Impuls von `impuls_sekunden`."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from beachhub_hall.config import TuerKonfig
from beachhub_hall.ereignisse import Ereignisse
from beachhub_hall.ha import HaClient, HaFehler

logger = logging.getLogger(__name__)


class Tuer:
    def __init__(
        self,
        ha: HaClient,
        konfig: TuerKonfig,
        ereignisse: Ereignisse,
        schlafen: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._ha = ha
        self._k = konfig
        self._ereignisse = ereignisse
        self._schlafen = schlafen
        self._impulse: set[asyncio.Task[None]] = set()

    async def oeffne(self) -> bool:
        entity = self._k.entity
        if not entity:
            self._ereignisse.melde("aktor_fehler", entity="tuer", grund="nicht_zugeordnet")
            return False
        try:
            if entity.startswith("lock."):
                await self._ha.dienst("lock", "unlock", {"entity_id": entity})
            else:
                await self._ha.dienst("switch", "turn_on", {"entity_id": entity})
                aufgabe = asyncio.create_task(self._impuls_ende(entity))
                self._impulse.add(aufgabe)
                aufgabe.add_done_callback(self._impulse.discard)
        except HaFehler as e:
            logger.error("Tür lässt sich nicht öffnen: %s", e)
            self._ereignisse.melde(
                "aktor_fehler", entity=entity, grund="tuer_oeffnen_fehlgeschlagen"
            )
            return False
        return True

    async def _impuls_ende(self, entity: str) -> None:
        await self._schlafen(self._k.impuls_sekunden)
        try:
            await self._ha.dienst("switch", "turn_off", {"entity_id": entity})
        except HaFehler:
            self._ereignisse.melde("aktor_fehler", entity=entity, grund="tuer_impuls_nicht_beendet")

    async def warte(self) -> None:
        if self._impulse:
            await asyncio.gather(*list(self._impulse))
