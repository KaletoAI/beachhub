"""Status nach HA: vier Sensoren, die der Betreiber im HA-Dashboard sieht – ganz ohne Custom
Component. Per `POST /api/states` geschriebene Zustände überleben keinen HA-Neustart; der
HA-Zuhörer stößt deshalb nach jeder Wiederverbindung ein neues Schreiben an."""

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker

from beachhub_hall import plan
from beachhub_hall.clock import Uhr
from beachhub_hall.db import lies
from beachhub_hall.ereignisse import Ereignisse
from beachhub_hall.ha import HaClient, HaFehler

logger = logging.getLogger(__name__)
VERBUNDEN_FENSTER = timedelta(minutes=10)


class StatusHa:
    def __init__(
        self, sitzungen: sessionmaker[Session], uhr: Uhr, ha: HaClient, ereignisse: Ereignisse
    ) -> None:
        self._sitzungen = sitzungen
        self._uhr = uhr
        self._ha = ha
        self._ereignisse = ereignisse
        self.wecker = asyncio.Event()

    async def einmal(self) -> None:
        with self._sitzungen() as db:
            gespeichert = plan.lade(db)
            kontakt = lies(db, "letzter_kontakt")
        verbunden = (
            bool(kontakt)
            and self._uhr.jetzt() - datetime.fromisoformat(kontakt) <= VERBUNDEN_FENSTER
        )
        try:
            await self._ha.setze_zustand(
                "sensor.beachhub_planversion",
                str(gespeichert.version if gespeichert else 0),
                {
                    "friendly_name": "Beachhub Planversion",
                    "gueltig_bis": gespeichert.inhalt.gueltig_bis.isoformat()
                    if gespeichert
                    else None,
                    "empfangen_am": gespeichert.empfangen_am.isoformat() if gespeichert else None,
                },
            )
            await self._ha.setze_zustand(
                "sensor.beachhub_letzter_kontakt",
                kontakt or "unknown",
                {
                    "friendly_name": "Beachhub letzter Kontakt zum Hauptsystem",
                    "device_class": "timestamp",
                },
            )
            await self._ha.setze_zustand(
                "binary_sensor.beachhub_verbunden",
                "on" if verbunden else "off",
                {
                    "friendly_name": "Beachhub mit Hauptsystem verbunden",
                    "device_class": "connectivity",
                },
            )
            await self._ha.setze_zustand(
                "sensor.beachhub_warteschlange",
                str(self._ereignisse.offen()),
                {
                    "friendly_name": "Beachhub nicht zugestellte Ereignisse",
                    "unit_of_measurement": "Ereignisse",
                },
            )
        except HaFehler as e:
            logger.warning("Status nach HA nicht geschrieben: %s", e)
