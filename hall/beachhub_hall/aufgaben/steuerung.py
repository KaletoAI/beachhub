"""Steuerung: vergleicht alle 30 s (und bei Anstoß) den Sollzustand mit dem Ist in HA und
schaltet nur bei Abweichung (idempotent, Hauptspec § 8.2).

Ist HA gar nicht erreichbar, bricht der Durchlauf still ab – das meldet der HA-Zuhörer als
`ha_nicht_erreichbar`. Scheitert dagegen ein einzelner Dienstaufruf oder reagiert ein Gerät
nicht, entsteht `aktor_fehler`.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from beachhub_shared.hallenplan import HallenplanInhalt
from sqlalchemy.orm import Session, sessionmaker

from beachhub_hall import plan
from beachhub_hall.clock import Uhr
from beachhub_hall.config import Zuordnung
from beachhub_hall.db import lies, schreibe
from beachhub_hall.ereignisse import Ereignisse
from beachhub_hall.ha import HaClient, HaFehler
from beachhub_hall.lage import Lage, dezimal
from beachhub_hall.soll import laufende_buchung, sollzustand

logger = logging.getLogger(__name__)
MAX_VERSUCHE = 3
PRAESENZ_ALARM_VORGABE = 10


class Steuerung:
    def __init__(
        self,
        sitzungen: sessionmaker[Session],
        uhr: Uhr,
        zuordnung: Zuordnung,
        ha: HaClient,
        ereignisse: Ereignisse,
        lage: Lage,
    ) -> None:
        self._sitzungen = sitzungen
        self._uhr = uhr
        self._z = zuordnung
        self._ha = ha
        self._ereignisse = ereignisse
        self._lage = lage
        self._versuche: dict[str, int] = {}
        self._gestoert: set[str] = set()
        self.wecker = asyncio.Event()

    async def einmal(self) -> None:
        jetzt = self._uhr.jetzt()
        with self._sitzungen() as db:
            gespeichert = plan.lade(db)
            handbetrieb = bool(lies(db, "handbetrieb", False))
        inhalt = gespeichert.inhalt if gespeichert else None
        self._praesenzalarm(inhalt, jetzt)
        soll = sollzustand(inhalt, self._z.felder.keys(), jetzt, handbetrieb)
        self._lage.soll_heizung = soll.heizung
        if not soll.steuern:
            return
        try:
            for feld_id, an in soll.licht.items():
                entity = self._z.felder[feld_id].licht
                if entity:
                    await self._licht(feld_id, entity, an)
            if soll.heizung is not None and self._z.heizung.entity:
                await self._heizung(self._z.heizung.entity, soll.heizung)
        except HaFehler as e:
            logger.warning("Steuerung übersprungen, HA nicht erreichbar: %s", e)

    async def _ist(self, entity: str) -> dict[str, Any] | None:
        zustand = await self._ha.zustand(entity)  # HaFehler: HA weg → Durchlauf abbrechen
        if zustand is not None:
            self._lage.ist[entity] = zustand
        return zustand

    def _in_ordnung(self, entity: str) -> None:
        self._versuche.pop(entity, None)
        self._gestoert.discard(entity)

    def _stoerung(self, entity: str, grund: str, feld_id: str | None) -> None:
        if entity not in self._gestoert:
            self._gestoert.add(entity)
            self._ereignisse.melde("aktor_fehler", feld_id=feld_id, entity=entity, grund=grund)

    async def _schalte(
        self, entity: str, domain: str, service: str, daten: dict[str, Any], feld_id: str | None
    ) -> bool:
        """Ein Versuch. True, wenn der Dienstaufruf angenommen wurde und gemeldet werden soll."""
        versuch = self._versuche.get(entity, 0) + 1
        self._versuche[entity] = versuch
        if versuch > MAX_VERSUCHE:
            self._stoerung(entity, "zustand_weicht_ab", feld_id)
        try:
            await self._ha.dienst(domain, service, {"entity_id": entity, **daten})
        except HaFehler as e:
            logger.error("%s.%s für %s fehlgeschlagen: %s", domain, service, entity, e)
            if versuch >= MAX_VERSUCHE:
                self._stoerung(entity, "dienst_fehlgeschlagen", feld_id)
            return False
        return versuch <= MAX_VERSUCHE

    async def _licht(self, feld_id: str, entity: str, an: bool) -> None:
        zustand = await self._ist(entity)
        ist_an = zustand is not None and zustand.get("state") == "on"
        if ist_an == an:
            self._in_ordnung(entity)
            return
        if await self._schalte(entity, "light", "turn_on" if an else "turn_off", {}, feld_id):
            self._ereignisse.melde("licht_geschaltet", feld_id=feld_id, entity=entity, an=an)

    async def _heizung(self, entity: str, soll: Decimal) -> None:
        zustand = await self._ist(entity)
        attribute = (zustand or {}).get("attributes") or {}
        eingestellt = dezimal(attribute.get("temperature"))
        if eingestellt is not None and eingestellt == soll:
            self._in_ordnung(entity)
            return
        if await self._schalte(
            entity, "climate", "set_temperature", {"temperature": float(soll)}, None
        ):
            if self._z.heizung.ist_sensor:
                await self._ist(self._z.heizung.ist_sensor)
            ist = self._lage.temperatur(self._z.heizung)
            self._ereignisse.melde(
                "heizung_gesetzt",
                soll=str(soll),
                ist_temperatur=str(ist) if ist is not None else None,
            )

    def _praesenzalarm(self, inhalt: HallenplanInhalt | None, jetzt: datetime) -> None:
        minuten = inhalt.konfig.praesenz_alarm_minuten if inhalt else PRAESENZ_ALARM_VORGABE
        zu_melden: list[str] = []
        with self._sitzungen() as db:
            praesenz: dict[str, dict[str, Any]] = lies(db, "praesenz", {})
            if not praesenz:
                return
            for feld_id, p in praesenz.items():
                if laufende_buchung(inhalt, feld_id, jetzt) is not None:
                    p["ohne_buchung_seit"] = None
                    continue
                if p.get("ohne_buchung_seit") is None:
                    p["ohne_buchung_seit"] = jetzt.isoformat()
                seit = datetime.fromisoformat(p["ohne_buchung_seit"])
                if not p.get("alarm") and jetzt - seit >= timedelta(minutes=minuten):
                    p["alarm"] = True
                    zu_melden.append(feld_id)
            schreibe(db, "praesenz", praesenz)
            db.commit()
        for feld_id in zu_melden:
            self._ereignisse.melde("praesenz_ohne_buchung", feld_id=feld_id, minuten=minuten)
