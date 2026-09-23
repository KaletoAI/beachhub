"""PIN-Prüfung am Tastenfeld (A-HALLE-3).

Reihenfolge: Serie prüfen (ab 5 Fehlversuchen wird die Annahme um wenige Sekunden verzögert,
aber nie gesperrt), Master-PIN, dann PIN einer Buchung im Zutrittsfenster. Das Zutrittsfenster
(inkl. Ablaufprüfung des Plans) kommt aus `soll.zutritt_offen` – eine einzige Quelle für
A-HALLE-3, nicht hier nachgebaut. Die Eingabe selbst wird nie gespeichert oder gemeldet.
Das Hashen läuft in einem Thread, damit der Dienst währenddessen weiterarbeitet.
"""

import asyncio
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from beachhub_shared.hallenplan import PlanBuchung, pin_hash
from sqlalchemy.orm import Session, sessionmaker

from beachhub_hall import plan, soll
from beachhub_hall.clock import Uhr
from beachhub_hall.config import Zuordnung
from beachhub_hall.db import lies, schreibe
from beachhub_hall.ereignisse import Ereignisse
from beachhub_hall.tuer import Tuer

SERIE_SCHWELLE = 5
SERIE_ENDE = timedelta(minutes=15)
# Nur ASCII-Ziffern: \d ohne re.ASCII träfe auch auf andere Unicode-Ziffern (z. B. Fullwidth-
# oder Devanagari-Ziffern) zu, die argon2 anders hasht als das Hauptsystem erwartet.
_ZIFFERN = re.compile(r"[0-9]{4,12}")
_ph = PasswordHasher()
MASTER = "master"


def ist_master(klar: str, master_hash: str) -> bool:
    try:
        return _ph.verify(master_hash, klar)
    except (VerificationError, InvalidHashError):
        return False


def _leere_serie() -> dict[str, Any]:
    return {"anzahl": 0, "letzte": None, "gemeldet": False}


class PinPruefer:
    def __init__(
        self,
        sitzungen: sessionmaker[Session],
        uhr: Uhr,
        zuordnung: Zuordnung,
        ereignisse: Ereignisse,
        tuer: Tuer,
        schlafen: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._sitzungen = sitzungen
        self._uhr = uhr
        self._z = zuordnung
        self._ereignisse = ereignisse
        self._tuer = tuer
        self._schlafen = schlafen
        # Nur während einer laufenden Fehlversuchsserie ernst zu nehmen: Task 10 startet jede
        # Tastenfeld-Eingabe als eigene Aufgabe, ohne diesen Lock würden mehrere gleichzeitige
        # Eingaben parallel verzögert statt nacheinander – die Verzögerung wäre wirkungslos.
        # Außerhalb einer Serie wird nicht serialisiert.
        self._verzoegerung_lock = asyncio.Lock()

    def _serie(self, db: Session, jetzt: datetime) -> dict[str, Any]:
        serie: dict[str, Any] = lies(db, "fehlserie") or _leere_serie()
        letzte = serie.get("letzte")
        if letzte and jetzt - datetime.fromisoformat(letzte) >= SERIE_ENDE:
            return _leere_serie()
        return serie

    async def eingabe(self, code: str) -> bool:
        code = code.strip()
        with self._sitzungen() as db:
            serie = self._serie(db, self._uhr.jetzt())
        if serie["anzahl"] >= SERIE_SCHWELLE:
            async with self._verzoegerung_lock:
                await self._schlafen(self._z.tastenfeld.verzoegerung_sekunden)
                jetzt = self._uhr.jetzt()
                treffer = await self._pruefe(code, jetzt)
        else:
            jetzt = self._uhr.jetzt()
            treffer = await self._pruefe(code, jetzt)
        if treffer is None:
            self._fehlversuch(jetzt)
            return False
        with self._sitzungen() as db:
            schreibe(db, "fehlserie", _leere_serie())
            if treffer == MASTER:
                schreibe(db, "letzter_master", jetzt.isoformat())
            db.commit()
        if isinstance(treffer, PlanBuchung):
            self._ereignisse.melde(
                "pin_akzeptiert", feld_id=treffer.feld_id, buchung_id=treffer.buchung_id
            )
        else:
            self._ereignisse.melde("pin_akzeptiert", master=True)
        await self._tuer.oeffne()
        return True

    async def _pruefe(self, code: str, jetzt: datetime) -> PlanBuchung | str | None:
        if not _ZIFFERN.fullmatch(code):
            return None
        if await asyncio.to_thread(ist_master, code, self._z.master_pin_hash):
            return MASTER
        with self._sitzungen() as db:
            gespeichert = plan.lade(db)
        if gespeichert is None:
            return None
        offene = soll.zutritt_offen(gespeichert.inhalt, jetzt)
        if not offene:
            return None
        h = await asyncio.to_thread(pin_hash, code, gespeichert.inhalt.pin)
        return next((b for b in offene if b.pin_hash == h), None)

    def _fehlversuch(self, jetzt: datetime) -> None:
        # Serie neu lesen: Während des Hashens kann eine zweite Eingabe gezählt worden sein.
        with self._sitzungen() as db:
            serie = self._serie(db, jetzt)
            serie["anzahl"] += 1
            serie["letzte"] = jetzt.isoformat()
            melden = serie["anzahl"] >= SERIE_SCHWELLE and not serie["gemeldet"]
            if melden:
                serie["gemeldet"] = True
            schreibe(db, "fehlserie", serie)
            db.commit()
        self._ereignisse.melde("pin_abgelehnt", fehlversuche=serie["anzahl"])
        if melden:
            self._ereignisse.melde("tastenfeld_fehlversuche", anzahl=serie["anzahl"])
