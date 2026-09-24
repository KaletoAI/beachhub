"""Melder: liefert Ereignisse und Status ans Hauptsystem (Hauptspec § 8.2).

Neue Ereignisse wecken ihn sofort, sonst läuft er alle 60 s. Ist das Hauptsystem nicht
erreichbar oder bestätigt es von einer Lieferung nichts, wartet er nach der Uhr 1, 2, 4 …
höchstens 60 s, bevor er es wieder versucht – neue Ereignisse wecken ihn in dieser Zeit nicht.
Nichts geht verloren: Erst die Bestätigung des Hauptsystems markiert ein Ereignis als zugestellt.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from beachhub_shared.hallenplan import (
    EreignisAntwort,
    EreignisLieferung,
    FeldStatus,
    HallenStatus,
    HeizungStatus,
    TuerStatus,
)
from sqlalchemy.orm import Session, sessionmaker

from beachhub_hall import __version__, plan
from beachhub_hall.clock import Uhr
from beachhub_hall.config import Zuordnung
from beachhub_hall.core import CoreClient, CoreNichtErreichbar
from beachhub_hall.db import dienst_id, lies, schreibe
from beachhub_hall.ereignisse import Ereignisse
from beachhub_hall.lage import Lage

logger = logging.getLogger(__name__)
MAX_JE_LIEFERUNG = 200
MAX_BACKOFF = 60.0


def baue_status(
    sitzungen: sessionmaker[Session], zuordnung: Zuordnung, lage: Lage, ereignisse: Ereignisse
) -> HallenStatus:
    with sitzungen() as db:
        version = plan.version(db)
        abruf = lies(db, "letzter_abruf")
        handbetrieb = bool(lies(db, "handbetrieb", False))
        praesenz = lies(db, "praesenz", {})
    tuer = zuordnung.tuer
    tuer_state = lage.state(tuer.entity)
    return HallenStatus(
        planversion=version,
        letzter_abruf=datetime.fromisoformat(abruf) if abruf else None,
        ha_erreichbar=lage.ha_verbunden,
        handbetrieb=handbetrieb,
        felder=[
            FeldStatus(
                feld_id=feld_id,
                licht_ist=lage.ist_an(z.licht) if z.licht else None,
                praesenz=(feld_id in praesenz) if z.praesenz else None,
            )
            for feld_id, z in zuordnung.felder.items()
        ],
        heizung=HeizungStatus(soll=lage.soll_heizung, ist=lage.temperatur(zuordnung.heizung)),
        tuer=TuerStatus(
            verriegelt=(tuer_state == "locked")
            if tuer.entity and tuer.entity.startswith("lock.") and tuer_state is not None
            else None,
            offen=lage.ist_an(tuer.kontakt) if tuer.kontakt else None,
        ),
        warteschlange=ereignisse.offen(),
        version_dienst=__version__,
    )


class Melder:
    def __init__(
        self,
        sitzungen: sessionmaker[Session],
        uhr: Uhr,
        core: CoreClient,
        ereignisse: Ereignisse,
        status: Callable[[], HallenStatus],
        plan_wecker: asyncio.Event,
    ) -> None:
        self._sitzungen = sitzungen
        self._uhr = uhr
        self._core = core
        self._ereignisse = ereignisse
        self._status = status
        self._plan_wecker = plan_wecker
        self._backoff = 0.0
        self._naechster_versuch: datetime | None = None

    async def einmal(self) -> EreignisAntwort | None:
        jetzt = self._uhr.jetzt()
        if self._naechster_versuch is not None and jetzt < self._naechster_versuch:
            return None
        lieferung = EreignisLieferung(
            dienst_id=dienst_id(self._sitzungen),
            ereignisse=self._ereignisse.unbestaetigt(MAX_JE_LIEFERUNG),
            status=self._status(),
        )
        try:
            antwort = await self._core.sende_ereignisse(lieferung)
        except CoreNichtErreichbar as e:
            self._verschiebe(jetzt)
            logger.warning(
                "Hauptsystem nicht erreichbar (%s), nächster Versuch in %.0f s", e, self._backoff
            )
            return None
        neu_bestaetigt = self._ereignisse.bestaetige_bis(antwort.bestaetigt_bis)
        with self._sitzungen() as db:
            schreibe(db, "letzter_kontakt", jetzt.isoformat())
            db.commit()
        if antwort.plan_neu:
            self._plan_wecker.set()
        if lieferung.ereignisse and neu_bestaetigt == 0:
            # Geliefert, aber nichts davon bestätigt – z. B. steht die Marke des Hauptsystems
            # nach einer Wiederherstellung aus einem Backup hinter dem Stand der Halle. Sofort
            # erneut zu liefern änderte nichts und liefe ohne Pause im Kreis (die Dauerschleife
            # wartet auf `ereignisse.neu`); daher wie bei einem Fehler mit Backoff warten.
            self._verschiebe(jetzt)
            logger.warning(
                "Hauptsystem bestätigt nur bis seq %s, nächster Versuch in %.0f s",
                antwort.bestaetigt_bis,
                self._backoff,
            )
            return antwort
        self._backoff = 0.0
        self._naechster_versuch = None
        if neu_bestaetigt > 0 and self._ereignisse.offen() > 0:
            self._ereignisse.neu.set()
        return antwort

    def _verschiebe(self, jetzt: datetime) -> None:
        self._backoff = min(MAX_BACKOFF, max(1.0, self._backoff * 2))
        self._naechster_versuch = jetzt + timedelta(seconds=self._backoff)

    async def leeren(self) -> None:
        vorher = -1
        while self._ereignisse.offen() > 0:
            antwort = await self.einmal()
            if antwort is None or antwort.bestaetigt_bis == vorher:
                return
            vorher = antwort.bestaetigt_bis
