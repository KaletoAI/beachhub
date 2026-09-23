"""Türöffner: lock.* wird entriegelt (das Schloss bzw. eine HA-Automation verriegelt wieder),
switch.* bekommt einen Impuls von `impuls_sekunden`.

Ein zweites Öffnen während eines laufenden Impulses bricht den alten Impuls ab und startet
ihn neu – ein überlappendes Öffnen verlängert die offene Zeit, statt zwei Impulse zu verschachteln.
`turn_off` wird bis zu drei Mal versucht, bevor ein `aktor_fehler` gemeldet wird; auch wenn
schon `turn_on` mit einem Fehler antwortet, planen wir sicherheitshalber trotzdem ein `turn_off`
– HA könnte den Schalter trotz Fehlerantwort geschaltet haben, sonst bliebe die Tür dauerhaft
offen. `schliesse()` beendet einen laufenden Impuls sofort (für das Herunterfahren des Dienstes).
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from beachhub_hall.config import TuerKonfig
from beachhub_hall.ereignisse import Ereignisse
from beachhub_hall.ha import HaClient, HaFehler

logger = logging.getLogger(__name__)

TURN_OFF_VERSUCHE = 3


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
        self._impuls: asyncio.Task[None] | None = None

    async def oeffne(self) -> bool:
        entity = self._k.entity
        if not entity:
            self._ereignisse.melde("aktor_fehler", entity="tuer", grund="nicht_zugeordnet")
            return False
        if entity.startswith("lock."):
            try:
                await self._ha.dienst("lock", "unlock", {"entity_id": entity})
            except HaFehler as e:
                logger.error("Tür lässt sich nicht öffnen: %s", e)
                self._ereignisse.melde(
                    "aktor_fehler", entity=entity, grund="tuer_oeffnen_fehlgeschlagen"
                )
                return False
            return True
        # Einen laufenden Impuls zuerst abbrechen (überlappendes Öffnen verlängert nur), bevor
        # der neue turn_on-Aufruf läuft – sonst könnte der alte Impuls währenddessen nebenläufig
        # zu Ende laufen und die Tür sofort wieder schließen.
        self._breche_impuls_ab()
        erfolg = True
        try:
            await self._ha.dienst("switch", "turn_on", {"entity_id": entity})
        except HaFehler as e:
            logger.error("Tür lässt sich nicht öffnen: %s", e)
            self._ereignisse.melde(
                "aktor_fehler", entity=entity, grund="tuer_oeffnen_fehlgeschlagen"
            )
            erfolg = False
        finally:
            # Auch anlegen, wenn genau hier von außen abgebrochen wird (z. B. während des
            # turn_on-Aufrufs): sonst plant niemand mehr ein turn_off, und der Schalter bliebe
            # im Zweifel dauerhaft eingeschaltet.
            self._impuls = asyncio.create_task(self._impuls_ende(entity))
        return erfolg

    def _breche_impuls_ab(self) -> None:
        if self._impuls is not None and not self._impuls.done():
            self._impuls.cancel()

    async def _impuls_ende(self, entity: str) -> None:
        await self._schlafen(self._k.impuls_sekunden)
        await self._turn_off_mit_wiederholung(entity)

    async def _turn_off_mit_wiederholung(self, entity: str) -> None:
        for versuch in range(1, TURN_OFF_VERSUCHE + 1):
            try:
                await self._ha.dienst("switch", "turn_off", {"entity_id": entity})
                return
            except HaFehler as e:
                logger.error(
                    "Türimpuls beenden (Versuch %s/%s) fehlgeschlagen: %s",
                    versuch,
                    TURN_OFF_VERSUCHE,
                    e,
                )
        self._ereignisse.melde("aktor_fehler", entity=entity, grund="tuer_impuls_nicht_beendet")

    async def schliesse(self) -> None:
        """Beendet einen laufenden Impuls sofort, ohne `impuls_sekunden` abzuwarten, und
        schaltet einen `switch.*`-Türöffner danach in jedem Fall aus – auch ohne laufenden
        Impuls (z. B. wenn der Schalter von außen oder durch einen früheren Fehler eingeschaltet
        blieb). Für das Herunterfahren des Dienstes. Ein `lock.*`-Türöffner braucht das nicht:
        er verriegelt sich selbst wieder (Schloss bzw. eine HA-Automation)."""
        entity = self._k.entity
        if self._impuls is not None and not self._impuls.done():
            self._impuls.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._impuls
        if entity and entity.startswith("switch."):
            await self._turn_off_mit_wiederholung(entity)

    async def warte(self) -> None:
        if self._impuls is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._impuls
