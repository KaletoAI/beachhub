"""Steuerung: vergleicht alle 30 s (und bei Anstoß) den Sollzustand mit dem Ist in HA und
schaltet nur bei Abweichung (idempotent, Hauptspec § 8.2).

Ist HA gar nicht erreichbar (Verbindung/Timeout), bricht der Durchlauf für die restlichen
Entitäten still ab – das meldet der HA-Zuhörer separat als `ha_nicht_erreichbar`. Der Fehler
einer einzelnen Entität (HTTP-Fehlerantwort, gescheiterter Dienstaufruf) blockiert dagegen nur
diese eine Entität; die übrigen werden im selben Lauf trotzdem verarbeitet.
"""

import asyncio
import logging
from collections.abc import Coroutine
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
from beachhub_hall.ha import HaClient, HaFehler, HaNichtErreichbar
from beachhub_hall.lage import Lage, dezimal
from beachhub_hall.soll import sollzustand, zutritt_offen

logger = logging.getLogger(__name__)
MAX_VERSUCHE = 3
PRAESENZ_ALARM_VORGABE = 10
RETRY_INTERVALL = timedelta(minutes=5)


def _begrenzt(soll: Decimal, attribute: dict[str, Any]) -> Decimal:
    """Begrenzt den Sollwert auf [min_temp, max_temp] der climate-Entität, falls HA diese
    Attribute meldet. Ohne Begrenzung lehnt HA einen Wert außerhalb ab oder kappt ihn selbst –
    dann weicht Soll dauerhaft von Ist ab (z. B. Grundtemperatur 0 °C unter min_temp 7 °C)."""
    minimum, maximum = dezimal(attribute.get("min_temp")), dezimal(attribute.get("max_temp"))
    if minimum is not None and soll < minimum:
        return minimum
    if maximum is not None and soll > maximum:
        return maximum
    return soll


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
        # Wann eine gestörte Entität frühestens wieder versucht wird (siehe _bereit).
        self._naechster_versuch: dict[str, datetime] = {}
        # Entitäten, für die das Lesen des Ist-Zustands (GET) gerade fehlschlägt – für die
        # „einmal je Störung“-Logregel in _verarbeite.
        self._get_gewarnt: set[str] = set()
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
            # Handbetrieb: der Dienst schaltet nichts mehr. Versuche/Störungen aus der Zeit
            # davor gelten nicht mehr – nach der Rückkehr aus dem Handbetrieb wird eine neue
            # Störung wieder frisch (mit eigenem aktor_fehler) erkannt.
            self._versuche.clear()
            self._gestoert.clear()
            self._naechster_versuch.clear()
            self._get_gewarnt.clear()
            return
        try:
            for feld_id, an in soll.licht.items():
                entity = self._z.felder[feld_id].licht
                if entity:
                    await self._verarbeite(entity, self._licht(feld_id, entity, an, jetzt))
            if soll.heizung is not None and self._z.heizung.entity:
                await self._verarbeite(
                    self._z.heizung.entity,
                    self._heizung(self._z.heizung.entity, soll.heizung, jetzt),
                )
        except HaNichtErreichbar as e:
            logger.warning("Steuerung übersprungen, HA nicht erreichbar: %s", e)

    async def _verarbeite(self, entity: str, lauf: Coroutine[Any, Any, None]) -> None:
        """Ein Fehler bei genau dieser Entität (HTTP-Fehlerantwort, gescheiterter
        Dienstaufruf) darf die übrigen Entitäten desselben Laufs nicht blockieren. Nur eine
        echte Nichterreichbarkeit von HA (Verbindung, Timeout) wird weitergereicht und bricht
        den ganzen Lauf ab (Fang in `einmal()`)."""
        try:
            await lauf
        except HaNichtErreichbar:
            raise
        except HaFehler as e:
            # Wie bei _stoerung: bei einem dauerhaften Fehler nur einmal loggen, nicht jeden
            # Lauf (30 s) erneut – zurückgesetzt, sobald das Lesen wieder klappt.
            if entity not in self._get_gewarnt:
                self._get_gewarnt.add(entity)
                logger.warning("Steuerung für %s übersprungen: %s", entity, e)
        else:
            self._get_gewarnt.discard(entity)

    async def _ist(self, entity: str) -> dict[str, Any] | None:
        zustand = await self._ha.zustand(entity)  # HaFehler/HaNichtErreichbar: siehe _verarbeite
        if zustand is not None:
            self._lage.ist[entity] = zustand
        return zustand

    def _in_ordnung(self, entity: str) -> None:
        self._versuche.pop(entity, None)
        self._gestoert.discard(entity)
        self._naechster_versuch.pop(entity, None)

    def _bereit(self, entity: str, jetzt: datetime) -> bool:
        """Solange eine Entität nicht gestört ist, wird jeder Lauf versucht (damit ein neuer
        Aktorfehler zügig erkannt wird). Gestörte Entitäten werden danach nur noch alle
        RETRY_INTERVALL neu versucht – sonst würde eine dauerhafte Störung jede
        Steuerungsrunde (30 s) einen weiteren Dienstaufruf auslösen."""
        if entity not in self._gestoert:
            return True
        naechster = self._naechster_versuch.get(entity)
        return naechster is None or jetzt >= naechster

    def _stoerung(self, entity: str, grund: str, feld_id: str | None) -> None:
        if entity not in self._gestoert:
            self._gestoert.add(entity)
            logger.error("Aktorfehler %s: %s (Feld %s)", entity, grund, feld_id)
            self._ereignisse.melde("aktor_fehler", feld_id=feld_id, entity=entity, grund=grund)

    async def _schalte(
        self,
        entity: str,
        domain: str,
        service: str,
        daten: dict[str, Any],
        feld_id: str | None,
        jetzt: datetime,
    ) -> bool:
        """Ein Versuch. True, wenn der Dienstaufruf angenommen wurde und gemeldet werden soll."""
        versuch = self._versuche.get(entity, 0) + 1
        self._versuche[entity] = versuch
        self._naechster_versuch[entity] = jetzt + RETRY_INTERVALL
        if versuch > MAX_VERSUCHE:
            self._stoerung(entity, "zustand_weicht_ab", feld_id)
        try:
            await self._ha.dienst(domain, service, {"entity_id": entity, **daten})
        except HaNichtErreichbar:
            raise
        except HaFehler:
            if versuch >= MAX_VERSUCHE:
                self._stoerung(entity, "dienst_fehlgeschlagen", feld_id)
            return False
        return versuch <= MAX_VERSUCHE

    async def _licht(self, feld_id: str, entity: str, an: bool, jetzt: datetime) -> None:
        zustand = await self._ist(entity)
        ist_an = zustand is not None and zustand.get("state") == "on"
        if ist_an == an:
            self._in_ordnung(entity)
            return
        if not self._bereit(entity, jetzt):
            return
        if await self._schalte(
            entity, "light", "turn_on" if an else "turn_off", {}, feld_id, jetzt
        ):
            self._ereignisse.melde("licht_geschaltet", feld_id=feld_id, entity=entity, an=an)

    async def _heizung(self, entity: str, soll: Decimal, jetzt: datetime) -> None:
        zustand = await self._ist(entity)
        attribute = (zustand or {}).get("attributes") or {}
        soll = _begrenzt(soll, attribute)
        # lage.soll_heizung zeigt denselben (begrenzten) Wert, der auch an HA geschickt wird
        # bzw. mit dem Ist verglichen wurde – sonst widersprechen sich Status und Ereignis.
        self._lage.soll_heizung = soll
        eingestellt = dezimal(attribute.get("temperature"))
        if eingestellt is not None and eingestellt == soll:
            self._in_ordnung(entity)
            return
        if not self._bereit(entity, jetzt):
            return
        if await self._schalte(
            entity, "climate", "set_temperature", {"temperature": float(soll)}, None, jetzt
        ):
            if self._z.heizung.ist_sensor:
                try:
                    await self._ist(self._z.heizung.ist_sensor)
                except HaNichtErreichbar:
                    raise
                except HaFehler as e:
                    # Das Setzen selbst hat geklappt – nur das Nachlesen der Bestätigung
                    # scheitert. Das darf heizung_gesetzt nicht unterdrücken (Ist bleibt None).
                    logger.warning(
                        "Ist-Temperatur (%s) nach dem Setzen nicht lesbar: %s",
                        self._z.heizung.ist_sensor,
                        e,
                    )
            ist = self._lage.temperatur(self._z.heizung)
            self._ereignisse.melde(
                "heizung_gesetzt",
                soll=str(soll),
                ist_temperatur=str(ist) if ist is not None else None,
            )

    def _praesenzalarm(self, inhalt: HallenplanInhalt | None, jetzt: datetime) -> None:
        minuten = inhalt.konfig.praesenz_alarm_minuten if inhalt else PRAESENZ_ALARM_VORGABE
        # Zutrittsfenster statt nur laufende Buchung: Zutritt ist schon zutritt_vorlauf_minuten
        # vor Buchungsbeginn möglich, und Präsenz in diesem Fenster ist erwartet – kein Alarm.
        offene_felder = {b.feld_id for b in zutritt_offen(inhalt, jetzt)}
        zu_melden: list[str] = []
        with self._sitzungen() as db:
            praesenz: dict[str, dict[str, Any]] = lies(db, "praesenz", {})
            if not isinstance(praesenz, dict):
                # Kaputter Zustand insgesamt (z. B. ein älteres/anderes Format) – verwerfen
                # statt mit .items() abzustürzen, und selbst heilen (leeres Objekt speichern).
                logger.warning("Ungültiger Präsenz-Zustand verworfen (kein Objekt): %r", praesenz)
                schreibe(db, "praesenz", {})
                db.commit()
                return
            if not praesenz:
                return
            for feld_id, p in list(praesenz.items()):
                try:
                    if not isinstance(p, dict):
                        raise TypeError("Präsenz-Eintrag ist kein Objekt")
                    if feld_id in offene_felder:
                        p["ohne_buchung_seit"] = None
                        continue
                    if p.get("ohne_buchung_seit") is None:
                        p["ohne_buchung_seit"] = jetzt.isoformat()
                    seit = datetime.fromisoformat(p["ohne_buchung_seit"])
                    if not p.get("alarm") and jetzt - seit >= timedelta(minutes=minuten):
                        p["alarm"] = True
                        zu_melden.append(feld_id)
                except (KeyError, ValueError, TypeError) as e:
                    # Ein kaputter Eintrag (z. B. vom HA-Zuhörer fehlerhaft angelegt) darf die
                    # Steuerung nicht für alle anderen Felder zum Absturz bringen.
                    logger.warning("Ungültiger Präsenz-Eintrag für %s verworfen: %s", feld_id, e)
                    del praesenz[feld_id]
            schreibe(db, "praesenz", praesenz)
            db.commit()
        for feld_id in zu_melden:
            self._ereignisse.melde("praesenz_ohne_buchung", feld_id=feld_id, minuten=minuten)
