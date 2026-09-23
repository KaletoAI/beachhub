"""Zahlungsrückmeldung (Briefkasten für den Anbieter) und Rückkehrseite, dazu die
Fake-Zahlungsseite für die Entwicklung (A-4)."""

import json
import re
import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from beachhub_portal import auth
from beachhub_portal.config import settings
from beachhub_portal.database import SessionLocal, get_db
from beachhub_portal.models import Konto
from beachhub_portal.services import briefkasten
from beachhub_portal.templating import render

# Ohne Anmeldung und ohne CSRF: Hier schreibt der Zahlungsanbieter hinein.
briefkasten_router = APIRouter()
# Seiten für den Kunden (mit CSRF-Prüfung).
router = APIRouter()
# Fake-Zahlungsseite (nur PORTAL_FAKE_ZAHLUNG=true, sonst 404): erzeugt denselben Briefkasten-
# eintrag wie ein echter Anbieter (kein Konto, keine Sitzung nötig) – wie der Briefkasten ohne
# CSRF, damit der Test die Seite direkt anspringen kann, ohne vorher ein Vor-Session-Cookie über
# ein GET zu holen (anders als die übrigen anonymen Formulare, z. B. /anmelden).
test_router = APIRouter()

_PROVIDER = re.compile(r"[a-z]{2,20}")


def _signatur_header(request: Request) -> str | None:
    for name in settings.webhook_signatur_header.split(","):
        name = name.strip()
        if name and name in request.headers:
            return request.headers[name]
    return None


def _speichere(provider: str, rohdaten: str, kopf: str | None) -> None:
    with SessionLocal() as db:
        briefkasten.nimm_an(db, provider=provider, rohdaten=rohdaten, signatur_header=kopf)


@briefkasten_router.post("/zahlung/rueckmeldung/{provider}")
async def rueckmeldung(provider: str, request: Request) -> JSONResponse:
    if not _PROVIDER.fullmatch(provider):
        raise HTTPException(status_code=404)
    laenge = request.headers.get("content-length", "")
    if laenge.isdigit() and int(laenge) > briefkasten.MAX_BYTES:
        raise HTTPException(status_code=413, detail="Rückmeldung zu groß")
    roh = await request.body()
    if len(roh) > briefkasten.MAX_BYTES:
        raise HTTPException(status_code=413, detail="Rückmeldung zu groß")
    await run_in_threadpool(
        _speichere, provider, roh.decode("utf-8", errors="replace"), _signatur_header(request)
    )
    return JSONResponse({"ok": True})


def _sicheres_ziel(zurueck: str) -> str:
    """Nur auf die eigene Rückkehrseite weiterleiten, nie auf einen fremden Host."""
    teile = urlsplit(zurueck)
    if teile.path == "/zahlung/zurueck":
        return "/zahlung/zurueck" + (f"?{teile.query}" if teile.query else "")
    return "/buchungen"


@router.get("/zahlung/zurueck")
def zurueck(anfrage: str = "") -> RedirectResponse:
    try:
        aid = uuid.UUID(anfrage)
    except ValueError:
        return RedirectResponse("/buchungen", status_code=303)
    return RedirectResponse(f"/anfrage/{aid}", status_code=303)


def _nur_mit_fake() -> None:
    if not settings.fake_zahlung:
        raise HTTPException(status_code=404)


@test_router.get("/test-zahlung/{ref}", response_class=HTMLResponse)
def test_zahlung_seite(
    request: Request,
    ref: str,
    betrag: str = "",
    zurueck: str = "",
    konto: Konto | None = Depends(auth.konto_optional),
) -> HTMLResponse:
    _nur_mit_fake()
    return render(
        request,
        "test_zahlung.html",
        konto=konto,
        ref=ref,
        betrag=betrag,
        zurueck=_sicheres_ziel(zurueck),
    )


@test_router.post("/test-zahlung/{ref}")
def test_zahlung(
    ref: str,
    ergebnis: str = Form(...),
    betrag: str = Form(""),
    zurueck: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    _nur_mit_fake()
    if ergebnis not in ("bezahlt", "abgebrochen"):
        raise HTTPException(status_code=400, detail="Unbekanntes Ergebnis")
    rohdaten = json.dumps({"ref": ref, "ergebnis": ergebnis, "betrag": betrag})
    briefkasten.nimm_an(db, provider="fake", rohdaten=rohdaten, signatur_header=None)
    return RedirectResponse(_sicheres_ziel(zurueck), status_code=303)
