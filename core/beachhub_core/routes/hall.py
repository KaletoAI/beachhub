"""Schnittstelle für den Hallendienst (Hauptspec § 8.2). Der Hallendienst ruft, das
Hauptsystem antwortet – nie umgekehrt. Caddy erzwingt mTLS, hier zusätzlich ein Token."""

import hmac
from typing import Annotated

from beachhub_shared.hallenplan import (
    DOKUMENT,
    EreignisAntwort,
    EreignisLieferung,
    HallenStatus,
    StatusAntwort,
)
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from beachhub_core import clock
from beachhub_core.config import settings
from beachhub_core.database import get_db
from beachhub_core.models import LesestandVersion
from beachhub_core.services import benachrichtigung, halle, lesestand


def pruefe_token(authorization: Annotated[str, Header()] = "") -> None:
    if not settings.hall_token:
        raise HTTPException(status_code=404)
    erwartet = f"Bearer {settings.hall_token}"
    if not hmac.compare_digest(authorization.encode(), erwartet.encode()):
        raise HTTPException(status_code=401, detail="Token ungültig")


router = APIRouter(prefix="/hall", dependencies=[Depends(pruefe_token)])


@router.get("/plan")
def plan(ab: int = 0, db: Session = Depends(get_db)) -> Response:
    zeile = db.get(LesestandVersion, DOKUMENT)
    dok = lesestand.lade(DOKUMENT)
    # `ab` größer als die gespeicherte Version: Die Halle kennt einen neueren Plan als das
    # Hauptsystem – typischerweise nach einer Wiederherstellung aus einem Backup. Die alte Version
    # zu schicken hieße, dass die Halle sie als version_alt verwirft (mit Alarm-Mail) und bei
    # ihrem Stand bleibt. Stattdessen neu veröffentlichen: publiziere() vergibt
    # max(alt + 1, Unixzeit in ms), und da die Halle ihre Version früher von hier bekommen hat,
    # liegt die neue darüber.
    if (
        zeile is None
        or zeile.geaendert
        or dok is None
        or dok.version != zeile.version
        or ab > zeile.version
    ):
        try:
            dok = lesestand.publiziere(db, DOKUMENT)
            db.commit()
        except FileNotFoundError as e:
            db.rollback()
            raise HTTPException(status_code=503, detail="Signaturschlüssel fehlt") from e
    if ab == dok.version:
        return Response(status_code=304)
    return JSONResponse(dok.model_dump(mode="json"))


@router.post("/ereignisse")
def ereignisse(
    lieferung: EreignisLieferung, background_tasks: BackgroundTasks, db: Session = Depends(get_db)
) -> EreignisAntwort:
    jetzt = clock.now(db)
    bis, neu = halle.speichere_ereignisse(db, lieferung)
    entwarnung = halle.kontakt(db, jetzt, lieferung.status)
    mails = halle.alarm_mails(db, neu, jetzt)
    antwort = EreignisAntwort(
        bestaetigt_bis=bis,
        plan_neu=halle.plan_neu(db, lieferung.status.planversion if lieferung.status else None),
    )
    db.commit()
    # Mailversand erst nach dem commit(), aber als BackgroundTask, damit ein langsamer
    # SMTP-Server nicht die Antwort blockiert und den 20-s-Timeout des Hallendienst-Clients
    # reißt (Fix-Runde 1, Punkt 5); BackgroundTasks laufen ohnehin erst nach dem Response.
    for betreff, text in mails:
        background_tasks.add_task(benachrichtigung.betreiber_alarm, betreff, text)
    if entwarnung:
        background_tasks.add_task(
            benachrichtigung.betreiber_alarm, "Halle wieder verbunden", entwarnung
        )
    return antwort


@router.post("/status")
def status(
    daten: HallenStatus, background_tasks: BackgroundTasks, db: Session = Depends(get_db)
) -> StatusAntwort:
    jetzt = clock.now(db)
    entwarnung = halle.kontakt(db, jetzt, daten)
    antwort = StatusAntwort(plan_neu=halle.plan_neu(db, daten.planversion))
    db.commit()
    if entwarnung:
        background_tasks.add_task(
            benachrichtigung.betreiber_alarm, "Halle wieder verbunden", entwarnung
        )
    return antwort
