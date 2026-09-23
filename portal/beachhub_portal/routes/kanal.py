"""Briefkasten für das Hauptsystem. Nur das Hauptsystem ruft diese Pfade auf – über mTLS
(Caddy, eigener Port) und zusätzlich mit dem Kanal-Token."""

import asyncio
import hmac
import logging
import time
from typing import Any

from beachhub_shared import kanal
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from beachhub_portal import uhr
from beachhub_portal.config import settings
from beachhub_portal.database import SessionLocal, get_db
from beachhub_portal.services import anfragen, lesestand, wecker

logger = logging.getLogger(__name__)


def pruefe_token(request: Request) -> None:
    if not settings.kanal_token:
        raise HTTPException(status_code=404, detail="Kanal nicht eingerichtet")
    erwartet = f"Bearer {settings.kanal_token}".encode()
    if not hmac.compare_digest(request.headers.get("authorization", "").encode(), erwartet):
        raise HTTPException(status_code=401, detail="Kanal-Token ungültig")


router = APIRouter(dependencies=[Depends(pruefe_token)])


def _abholen() -> list[kanal.Anfrage]:
    with SessionLocal() as db:
        return anfragen.abholen(db, uhr.jetzt())


@router.get("/anfragen")
async def anfragen_abholen(warten: int = Query(25, ge=0, le=30)) -> dict[str, Any]:
    """Long-Poll: sofort antworten, wenn Anfragen da sind; sonst bis `warten` Sekunden warten.
    Die Datenbank wird im Threadpool abgefragt, damit der Event-Loop frei bleibt."""
    ende = time.monotonic() + warten
    while True:
        stand = wecker.stand()
        liste = await run_in_threadpool(_abholen)
        if liste or time.monotonic() >= ende:
            return kanal.AnfrageListe(anfragen=liste).model_dump(mode="json")
        naechste_pruefung = min(time.monotonic() + 1.0, ende)
        while time.monotonic() < naechste_pruefung and wecker.stand() == stand:
            await asyncio.sleep(0.05)


@router.post("/antworten")
def antworten(liste: kanal.AntwortListe, db: Session = Depends(get_db)) -> dict[str, int]:
    jetzt = uhr.jetzt()
    n = sum(anfragen.beantworte(db, e.anfrage_id, e.antwort, jetzt) for e in liste.antworten)
    db.commit()
    return {"ok": n}


@router.post("/lesestand")
def lesestand_empfangen(liste: kanal.DokumentListe, db: Session = Depends(get_db)) -> JSONResponse:
    jetzt = uhr.jetzt()
    ergebnis = kanal.LesestandErgebnis(uebernommen=[], verworfen=[])
    for dok in liste.dokumente:
        grund = lesestand.uebernehme(db, dok, jetzt)
        if grund is None:
            ergebnis.uebernommen.append(dok.dokument)
        else:
            ergebnis.verworfen.append(kanal.Verworfen(dokument=dok.dokument, grund=grund))
    db.commit()
    signaturfehler = [v.dokument for v in ergebnis.verworfen if v.grund == "signatur"]
    if signaturfehler:
        logger.error("Lesestand mit ungültiger Signatur verworfen: %s", signaturfehler)
    return JSONResponse(
        ergebnis.model_dump(mode="json"), status_code=422 if signaturfehler else 200
    )


@router.get("/lesestand/versionen")
def lesestand_versionen(db: Session = Depends(get_db)) -> dict[str, int]:
    return lesestand.versionen(db)
