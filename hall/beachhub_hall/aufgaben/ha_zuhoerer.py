"""HA-Zuhörer: abonniert über den HA-WebSocket Zustandsänderungen und das Tastenfeld-Ereignis.

Bei Verbindungsverlust verbindet er sich mit Backoff (1 s bis 60 s) neu; nach 2 min ohne
Verbindung meldet er einmal `ha_nicht_erreichbar`. Nach jeder (Wieder-)Verbindung liest er alle
Zustände neu ein – so gehen weder Handbetrieb noch Präsenz während eines Ausfalls verloren –
und stößt Steuerung und Status an (HA hat die Status-Sensoren bei einem Neustart vergessen).

Ein einzelnes kaputtes HA-Ereignis (unerwartete Struktur, kaputter eigener Zustand) darf den
Zuhörer und damit den Dienst nicht mitreißen – `laufen()` fängt Ausnahmen je Ereignis und in der
äußeren Schleife und verbindet danach neu, statt sich zu beenden.

Jede Tastenfeld-Eingabe läuft als eigene Aufgabe (parallel zum Verarbeiten weiterer Ereignisse);
`beende_eingaben()` bricht sie beim Herunterfahren des Dienstes ab und wartet, bis sie beendet
sind – Task 11 ruft das vor `tuer.schliesse()` und `ha.schliesse()` auf.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from beachhub_hall import plan
from beachhub_hall.clock import Uhr
from beachhub_hall.config import Zuordnung
from beachhub_hall.db import lies, schreibe
from beachhub_hall.ereignisse import Ereignisse
from beachhub_hall.ha import HaClient, HaFehler, HaKeineRechte, HaWebSocket
from beachhub_hall.lage import Lage
from beachhub_hall.pin import PinPruefer
from beachhub_hall.soll import laufende_buchung, tuer_erwartet

logger = logging.getLogger(__name__)
AUSFALL_MELDEN_NACH = timedelta(minutes=2)
MASTER_TUER_KULANZ = timedelta(minutes=5)
MAX_BACKOFF = 60.0
# HA meldet zwischendurch "unavailable"/"unknown" (z. B. beim eigenen Neustart eines Sensors).
# Nur "on"/"off" sind echte Zustandswechsel; alles andere behält den zuletzt bekannten Zustand.
_ECHTE_ZUSTAENDE = ("on", "off")


class HaZuhoerer:
    def __init__(
        self,
        sitzungen: sessionmaker[Session],
        uhr: Uhr,
        zuordnung: Zuordnung,
        ha: HaClient,
        ereignisse: Ereignisse,
        lage: Lage,
        pruefer: PinPruefer,
        steuerung_wecker: asyncio.Event,
        status_wecker: asyncio.Event,
        backoff_start: float = 1.0,
    ) -> None:
        self._sitzungen = sitzungen
        self._uhr = uhr
        self._z = zuordnung
        self._ha = ha
        self._ereignisse = ereignisse
        self._lage = lage
        self._pruefer = pruefer
        self._steuerung_wecker = steuerung_wecker
        self._status_wecker = status_wecker
        self._backoff_start = backoff_start
        self._eingaben: set[asyncio.Task[bool]] = set()
        # Bis zur ersten Verbindung gilt HA als ausgefallen – seit dem Start des Dienstes.
        self._ausfall_seit: datetime | None = uhr.jetzt()
        self._ausfall_gemeldet = False
        # Zuletzt bekannter on/off-Zustand des Türkontakts (nicht nur das old_state des
        # jeweiligen Ereignisses) – "unavailable"/"unknown" dürfen ihn nicht auf None
        # zurücksetzen, sonst löst ein Aussetzer (on → unavailable → on) erneut einen Alarm aus.
        self._tuer_zustand: bool | None = None

    async def laufen(self) -> None:
        backoff = self._backoff_start
        while True:
            try:
                ws = await self._ha.websocket()
                try:
                    await ws.abonniere("state_changed")
                    await self._abonniere_tastenfeld(ws)
                    await self.verbunden()
                    backoff = self._backoff_start
                    while True:
                        ereignis = await ws.naechstes()
                        try:
                            await self.verarbeite(ereignis)
                        except Exception:
                            # Ein kaputtes einzelnes Ereignis darf die Verbindung nicht
                            # abreißen lassen – nie `ereignis`/`daten` loggen (könnte PINs
                            # enthalten), nur den Ereignistyp.
                            logger.exception(
                                "HA-Ereignis %s nicht verarbeitet", ereignis.get("event_type")
                            )
                finally:
                    await ws.schliesse()
            except HaFehler as e:
                logger.warning("HA-WebSocket getrennt: %s", e)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("HA-Zuhörer: unerwarteter Fehler, verbinde neu")
            self.getrennt()
            self.pruefe_ausfall()
            await asyncio.sleep(self._naechster_schlaf(backoff))
            backoff = min(MAX_BACKOFF, backoff * 2)
            # Direkt nach dem (ggf. verkürzten) Schlaf erneut prüfen, statt erst nach dem
            # nächsten Verbindungsversuch – sonst könnte `ha_nicht_erreichbar` bei großem
            # Backoff verspätet gemeldet werden.
            self.pruefe_ausfall()

    async def _abonniere_tastenfeld(self, ws: HaWebSocket) -> None:
        """Abonniert den Tastenfeld-Ereignistyp. Lehnt HA das wegen fehlender Rechte ab (der
        HA-Benutzer ist kein Administrator, eigene Ereignistypen stehen nicht in HAs
        SUBSCRIBE_ALLOWLIST), ist das kein HA-Ausfall: laut als Fehler loggen, aber mit
        state_changed weiterarbeiten – Licht, Heizung, Handbetrieb und Präsenz laufen weiter,
        nur das Tastenfeld bleibt bis zur Korrektur des Benutzers taub. Ein Neuverbinden
        änderte daran nichts und ließe nach 2 min fälschlich `ha_nicht_erreichbar` melden. Ein
        Ereignis dafür gibt es bewusst nicht (neuer Typ wäre eine Vertragsänderung); beim
        nächsten Verbindungsaufbau wird es erneut versucht."""
        try:
            await ws.abonniere(self._z.tastenfeld.ereignis)
        except HaKeineRechte as e:
            logger.error("Tastenfeld abgeschaltet: %s", e)

    def _naechster_schlaf(self, backoff: float) -> float:
        """Begrenzt die Backoff-Schlafzeit auf die Restzeit bis zur 2-min-Ausfallmeldung, damit
        `ha_nicht_erreichbar` pünktlich gemeldet wird, auch wenn der Backoff (bis 60 s) sonst
        über die 2-min-Grenze hinaus schliefe."""
        if self._ausfall_seit is None or self._ausfall_gemeldet:
            return backoff
        rest = (self._ausfall_seit + AUSFALL_MELDEN_NACH - self._uhr.jetzt()).total_seconds()
        return backoff if rest <= 0 else min(backoff, rest)

    async def verbunden(self) -> None:
        # Erst nach dem erfolgreichen Lesen aller Zustände als verbunden gelten – sonst würde
        # ein Fehler mitten in verbunden() (z. B. REST nach erfolgreichem WebSocket-Handshake
        # nicht erreichbar) den Ausfall fälschlich zurücksetzen.
        zustaende = await self._ha.zustaende()
        self._lage.ha_verbunden = True
        self._ausfall_seit = None
        self._ausfall_gemeldet = False
        for zustand in zustaende:
            entity = zustand.get("entity_id")
            if isinstance(entity, str):
                self._lage.ist[entity] = zustand
        if self._z.handbetrieb:
            an = self._als_bool(self._lage.state(self._z.handbetrieb))
            if an is not None:
                self._handbetrieb(an)
        for feld_id, z in self._z.felder.items():
            if z.praesenz:
                an = self._als_bool(self._lage.state(z.praesenz))
                if an is not None:
                    self._praesenz(feld_id, an)
        if self._z.tuer.kontakt:
            # Nur die Ausgangslage übernehmen (kein Alarm) – nur echte on/off-Werte, damit ein
            # "unavailable" beim Reconnect nicht den zuletzt bekannten Zustand verwirft.
            tuer_an = self._als_bool(self._lage.state(self._z.tuer.kontakt))
            if tuer_an is not None:
                self._tuer_zustand = tuer_an
        self._status_wecker.set()
        self._steuerung_wecker.set()

    def getrennt(self) -> None:
        self._lage.ha_verbunden = False
        if self._ausfall_seit is None:
            self._ausfall_seit = self._uhr.jetzt()

    def pruefe_ausfall(self) -> None:
        if self._ausfall_seit is None or self._ausfall_gemeldet:
            return
        if self._uhr.jetzt() - self._ausfall_seit >= AUSFALL_MELDEN_NACH:
            self._ausfall_gemeldet = True
            self._ereignisse.melde("ha_nicht_erreichbar", seit=self._ausfall_seit.isoformat())

    @staticmethod
    def _als_bool(zustand: str | None) -> bool | None:
        """None, wenn `zustand` kein echter Zustandswechsel ist (z. B. "unavailable",
        "unknown", fehlend) – der Aufrufer lässt den zuletzt bekannten Zustand dann
        unangetastet, statt ihn als "aus" zu werten."""
        return zustand == "on" if zustand in _ECHTE_ZUSTAENDE else None

    async def verarbeite(self, event: dict[str, Any]) -> None:
        typ = event.get("event_type")
        daten = event.get("data") or {}
        if typ == self._z.tastenfeld.ereignis:
            # Die Eingabe (Zahl oder Text, ggf. mit Leerzeichen) wird nie geloggt oder
            # gespeichert – nur str(code) geht an PinPruefer.eingabe(), das selbst trimmt.
            code = daten.get(self._z.tastenfeld.feld)
            if code is not None:
                aufgabe = asyncio.create_task(self._pruefer.eingabe(str(code)))
                self._eingaben.add(aufgabe)
                aufgabe.add_done_callback(self._eingabe_beendet)
            return
        if typ != "state_changed":
            return
        entity = daten.get("entity_id")
        neu = daten.get("new_state")
        if not isinstance(entity, str):
            return
        if not isinstance(neu, dict):
            self._lage.ist.pop(entity, None)
            return
        self._lage.ist[entity] = neu
        an = self._als_bool(neu.get("state"))
        if entity == self._z.handbetrieb:
            if an is not None:
                self._handbetrieb(an)
        elif (feld_id := self._z.feld_fuer_praesenz(entity)) is not None:
            if an is not None:
                self._praesenz(feld_id, an)
        elif entity == self._z.tuer.kontakt:
            # Nur ein echter Übergang von bekanntem "off" (oder unbekannt beim allerersten
            # Mal) auf "on" ist ein Öffnen. Wir vergleichen mit dem selbst gemerkten, zuletzt
            # bekannten on/off-Zustand – nicht mit old_state des Ereignisses –, damit weder ein
            # Attribut-Update (state blieb "on") noch ein Aussetzer (on → unavailable → on)
            # erneut alarmiert.
            vorher = self._tuer_zustand
            if an is not None:
                self._tuer_zustand = an
            if an and vorher is not True:
                self._tuer_geoeffnet()
        elif entity in self._z.gesteuerte():
            self._steuerung_wecker.set()

    def _eingabe_beendet(self, aufgabe: asyncio.Task[bool]) -> None:
        self._eingaben.discard(aufgabe)
        if not aufgabe.cancelled() and (fehler := aufgabe.exception()) is not None:
            logger.error("Tastenfeld-Eingabe fehlgeschlagen", exc_info=fehler)

    async def warte_auf_eingaben(self) -> None:
        """Wartet, bis alle gerade laufenden Tastenfeld-Eingaben natürlich beendet sind –
        nur für Tests."""
        if self._eingaben:
            await asyncio.gather(*list(self._eingaben))

    async def beende_eingaben(self) -> None:
        """Bricht laufende Tastenfeld-Eingaben ab und wartet, bis sie beendet sind – fürs
        Herunterfahren des Dienstes. Task 11 ruft dies vor `tuer.schliesse()` und
        `ha.schliesse()` auf, damit keine Eingabe mehr auf die (dann geschlossene) Tür oder
        HA-Verbindung zugreift. Eine Ausnahme wird bereits vom Done-Callback (`_eingabe_beendet`)
        geloggt, sobald die Aufgabe beendet ist – hier nicht erneut, sonst doppelt."""
        aufgaben = list(self._eingaben)
        for aufgabe in aufgaben:
            aufgabe.cancel()
        if aufgaben:
            await asyncio.gather(*aufgaben, return_exceptions=True)

    def _handbetrieb(self, an: bool) -> None:
        with self._sitzungen() as db:
            if bool(lies(db, "handbetrieb", False)) == an:
                return
            schreibe(db, "handbetrieb", an)
            db.commit()
        self._ereignisse.melde("handbetrieb_an" if an else "handbetrieb_aus")
        if not an:
            self._steuerung_wecker.set()
        self._status_wecker.set()

    def _praesenz(self, feld_id: str, an: bool) -> None:
        jetzt = self._uhr.jetzt()
        with self._sitzungen() as db:
            gespeichert = plan.lade(db)
            roh = lies(db, "praesenz", {})
            praesenz: dict[str, dict[str, Any]] = roh if isinstance(roh, dict) else {}
            if an == (feld_id in praesenz):
                return
            laufend = laufende_buchung(gespeichert.inhalt if gespeichert else None, feld_id, jetzt)
            eintrag: Any = None
            if an:
                praesenz[feld_id] = {
                    "seit": jetzt.isoformat(),
                    "ohne_buchung_seit": None if laufend else jetzt.isoformat(),
                    "alarm": False,
                }
            else:
                eintrag = praesenz.pop(feld_id, None)
            schreibe(db, "praesenz", praesenz)
            db.commit()
        if an:
            self._ereignisse.melde(
                "praesenz_start",
                feld_id=feld_id,
                buchung_id=laufend.buchung_id if laufend else None,
            )
            return
        try:
            if not isinstance(eintrag, dict):
                raise TypeError("Präsenz-Eintrag ist kein Objekt")
            seit = datetime.fromisoformat(eintrag["seit"])
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(
                "Ungültiger Präsenz-Eintrag für %s beim Verlassen verworfen: %s", feld_id, e
            )
            return
        dauer = jetzt - seit
        self._ereignisse.melde(
            "praesenz_ende", feld_id=feld_id, dauer_minuten=int(dauer.total_seconds() // 60)
        )

    def _tuer_geoeffnet(self) -> None:
        """`tuer_offen_ausserhalb` nur, wenn kein Buchungsfenster die Tür erwartet
        (`soll.tuer_erwartet`, bis ende + licht_nachlauf – Verlassen nach dem Spiel), kein
        Master-PIN in den letzten 5 min akzeptiert wurde und kein Feld gerade Präsenz meldet
        (dann ist jemand in der Halle, etwa beim späten Verlassen)."""
        jetzt = self._uhr.jetzt()
        with self._sitzungen() as db:
            gespeichert = plan.lade(db)
            letzter_master = lies(db, "letzter_master")
            praesenz = lies(db, "praesenz", {})
        if tuer_erwartet(gespeichert.inhalt if gespeichert else None, jetzt):
            return
        if praesenz:
            return
        if letzter_master and jetzt - datetime.fromisoformat(letzter_master) < MASTER_TUER_KULANZ:
            return
        self._ereignisse.melde("tuer_offen_ausserhalb")
