"""Plan-Abruf: alle 5 Minuten, beim Start und sofort, wenn das Hauptsystem `plan_neu` meldet."""

import asyncio
import logging
from collections.abc import Callable

from beachhub_shared.hallenplan import HallenplanInhalt
from sqlalchemy.orm import Session, sessionmaker

from beachhub_hall import plan
from beachhub_hall.clock import Uhr
from beachhub_hall.config import Zuordnung
from beachhub_hall.core import CoreClient, CoreNichtErreichbar
from beachhub_hall.db import lies, schreibe
from beachhub_hall.ereignisse import Ereignisse

logger = logging.getLogger(__name__)


class PlanAbruf:
    def __init__(
        self,
        sitzungen: sessionmaker[Session],
        uhr: Uhr,
        core: CoreClient,
        oeffentlich_hex: str,
        ereignisse: Ereignisse,
        zuordnung: Zuordnung,
        nach_neuem_plan: Callable[[], None],
    ) -> None:
        self._sitzungen = sitzungen
        self._uhr = uhr
        self._core = core
        self._oeffentlich = oeffentlich_hex
        self._ereignisse = ereignisse
        self._zuordnung = zuordnung
        self._nach_neuem_plan = nach_neuem_plan
        self.wecker = asyncio.Event()

    async def einmal(self) -> bool:
        with self._sitzungen() as db:
            aktuell = plan.version(db)
        try:
            roh = await self._core.hole_plan(aktuell)
        except CoreNichtErreichbar as e:
            # Kein Ereignis: Der Ausfall zeigt sich im Status und im Kontakt-Alarm des Hauptsystems.
            logger.warning("Plan-Abruf fehlgeschlagen: %s", e)
            return False
        jetzt = self._uhr.jetzt()
        with self._sitzungen() as db:
            schreibe(db, "letzter_abruf", jetzt.isoformat())
            db.commit()
        if roh is None:
            return False
        try:
            dok, inhalt = plan.pruefe(roh, self._oeffentlich, aktuell)
        except plan.PlanFehler as e:
            logger.error("Plan verworfen: %s", e.grund)
            self._ereignisse.melde("plan_verworfen", grund=e.grund, version=roh.get("version"))
            return False
        with self._sitzungen() as db:
            plan.speichere(db, dok, inhalt, jetzt)
        logger.info("Plan Version %s übernommen (%s Buchungen)", dok.version, len(inhalt.buchungen))
        self._melde_unzugeordnete(inhalt)
        self._nach_neuem_plan()
        return True

    def _melde_unzugeordnete(self, inhalt: HallenplanInhalt) -> None:
        with self._sitzungen() as db:
            gemeldet = set(lies(db, "unzugeordnet_gemeldet", []))
            neu = [
                f.id
                for f in inhalt.felder
                if f.aktiv and f.id not in self._zuordnung.felder and f.id not in gemeldet
            ]
            if not neu:
                return
            schreibe(db, "unzugeordnet_gemeldet", sorted(gemeldet | set(neu)))
            db.commit()
        for feld_id in neu:
            self._ereignisse.melde("aktor_fehler", feld_id=feld_id, grund="feld_nicht_zugeordnet")
