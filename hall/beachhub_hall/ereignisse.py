"""Ereignis-Warteschlange: alles, was in der Halle passiert, wartet hier auf das Hauptsystem.

Ein Ereignis gilt erst als zugestellt, wenn das Hauptsystem seine seq bestätigt hat. Bis dahin
bleibt es liegen – auch über Tage ohne Verbindung (A-HALLE-6).
"""

import asyncio
import json
import logging
from datetime import timedelta
from typing import Any

from beachhub_shared.hallenplan import EREIGNISTYPEN, HallenEreignis
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from beachhub_hall.clock import Uhr
from beachhub_hall.db import EreignisZeile

logger = logging.getLogger(__name__)
AUFBEWAHRUNG = timedelta(days=90)


class Ereignisse:
    def __init__(self, sitzungen: sessionmaker[Session], uhr: Uhr) -> None:
        self._sitzungen = sitzungen
        self._uhr = uhr
        self.neu = asyncio.Event()

    def melde(
        self, typ: str, *, feld_id: str | None = None, buchung_id: str | None = None, **daten: Any
    ) -> int:
        if typ not in EREIGNISTYPEN:
            raise ValueError(f"unbekannter Ereignistyp: {typ}")
        with self._sitzungen() as db:
            z = EreignisZeile(
                typ=typ,
                zeitpunkt=self._uhr.jetzt(),
                feld_id=feld_id,
                buchung_id=buchung_id,
                daten_json=json.dumps(daten, default=str, sort_keys=True),
            )
            db.add(z)
            db.commit()
            seq = z.seq
        logger.info("Ereignis %s %s feld=%s buchung=%s %s", seq, typ, feld_id, buchung_id, daten)
        self.neu.set()
        return seq

    def unbestaetigt(self, limit: int = 200) -> list[HallenEreignis]:
        with self._sitzungen() as db:
            zeilen = db.scalars(
                select(EreignisZeile)
                .where(EreignisZeile.gesendet_am.is_(None))
                .order_by(EreignisZeile.seq)
                .limit(limit)
            ).all()
            return [
                HallenEreignis(
                    seq=z.seq,
                    typ=z.typ,
                    zeitpunkt=z.zeitpunkt,
                    feld_id=z.feld_id,
                    buchung_id=z.buchung_id,
                    daten=json.loads(z.daten_json),
                )
                for z in zeilen
            ]

    def bestaetige_bis(self, seq: int) -> None:
        with self._sitzungen() as db:
            db.execute(
                update(EreignisZeile)
                .where(EreignisZeile.seq <= seq, EreignisZeile.gesendet_am.is_(None))
                .values(gesendet_am=self._uhr.jetzt())
            )
            db.commit()

    def offen(self) -> int:
        with self._sitzungen() as db:
            anzahl = db.scalar(
                select(func.count())
                .select_from(EreignisZeile)
                .where(EreignisZeile.gesendet_am.is_(None))
            )
            return int(anzahl or 0)

    def raeume_auf(self) -> int:
        grenze = self._uhr.jetzt() - AUFBEWAHRUNG
        with self._sitzungen() as db:
            ergebnis = db.execute(
                delete(EreignisZeile).where(
                    EreignisZeile.gesendet_am.is_not(None), EreignisZeile.gesendet_am < grenze
                )
            )
            db.commit()
            return int(ergebnis.rowcount)
