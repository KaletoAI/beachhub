from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from beachhub_portal import auth
from beachhub_portal.config import settings
from beachhub_portal.database import get_db
from beachhub_portal.models import Konto
from beachhub_portal.services import anfragen, konten
from beachhub_portal.templating import mit_flash, render

router = APIRouter()


@router.get("/willkommen", response_model=None)
def willkommen_seite(request: Request, konto: Konto = Depends(auth.konto_pflicht)) -> Response:
    if konto.anzeigename:
        return RedirectResponse("/", status_code=303)
    return render(request, "willkommen.html", konto=konto)


@router.post("/willkommen", response_model=None)
def willkommen(
    request: Request,
    anzeigename: str = Form(..., max_length=100),
    konto: Konto = Depends(auth.konto_pflicht),
    db: Session = Depends(get_db),
) -> Response:
    if konto.anzeigename:
        return RedirectResponse("/", status_code=303)
    name = anzeigename.strip()
    if not name:
        return render(
            request,
            "willkommen.html",
            konto=konto,
            status_code=400,
            fehler="Bitte gib einen Namen an.",
        )
    # Ruling Fix-Runde 1 (Item 10): keine eigene db.commit() hier – anfragen.stelle() committet
    # gleich beides (Namensänderung und Anfrage) in einer Transaktion.
    konto.anzeigename = name
    anfragen.stelle(
        db,
        typ="konto_angelegt",
        konto_id=konto.id,
        nutzlast={"email": konto.email, "anzeigename": name},
    )
    return mit_flash(RedirectResponse("/", status_code=303), f"Willkommen, {name}!")


@router.get("/konto", response_class=HTMLResponse)
def konto_seite(request: Request, konto: Konto = Depends(auth.konto_pflicht)) -> HTMLResponse:
    return render(request, "konto.html", konto=konto, betreiber_email=settings.betreiber_email)


@router.post("/konto/name")
def name_aendern(
    anzeigename: str = Form(..., max_length=100),
    konto: Konto = Depends(auth.konto_pflicht),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    name = anzeigename.strip()
    ziel = RedirectResponse("/konto", status_code=303)
    if not name:
        return mit_flash(ziel, "Bitte gib einen Namen an.", "fehler")
    if name != konto.anzeigename:
        bisher = konto.anzeigename
        # Ruling Fix-Runde 1 (Item 10): siehe willkommen() – ein gemeinsamer Commit über
        # anfragen.stelle() statt eines eigenen db.commit() davor.
        konto.anzeigename = name
        anfragen.stelle(
            db,
            typ="konto_geaendert",
            konto_id=konto.id,
            nutzlast={"anzeigename": name, "bisher": bisher},
        )
    return mit_flash(ziel, "Name gespeichert.")


@router.get("/konto/loeschen", response_class=HTMLResponse)
def loeschen_seite(request: Request, konto: Konto = Depends(auth.konto_pflicht)) -> HTMLResponse:
    return render(request, "konto_loeschen.html", konto=konto)


@router.post("/konto/loeschen")
def loeschen(
    konto: Konto = Depends(auth.konto_pflicht), db: Session = Depends(get_db)
) -> RedirectResponse:
    # Ruling Fix-Runde 1 (Item 10): Anfrage ohne eigenen Commit anlegen (commit=False) – erst
    # konten.loesche() schließt Anfrage und Löschung in einer gemeinsamen Transaktion ab und
    # löscht die Rechnungs-PDFs erst danach von der Platte.
    anfragen.stelle(db, typ="konto_loeschen", konto_id=konto.id, nutzlast={}, commit=False)
    konten.loesche(db, konto)
    resp = RedirectResponse("/", status_code=303)
    auth.loesche_cookie(resp)
    return mit_flash(resp, "Dein Konto ist gelöscht.")
