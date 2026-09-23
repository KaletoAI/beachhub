from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from beachhub_portal import auth, uhr
from beachhub_portal.database import get_db
from beachhub_portal.models import Konto
from beachhub_portal.services import lesestand, slots
from beachhub_portal.templating import render

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def belegung_seite(
    request: Request,
    tag: str = "",
    konto: Konto | None = Depends(auth.konto_optional),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    b = lesestand.belegung(db)
    if b is None:
        return render(request, "belegung.html", konto=konto, belegung=None)
    jetzt = uhr.jetzt()
    tage = slots.tage(b, jetzt)
    try:
        datum = date.fromisoformat(tag) if tag else tage[0]
    except ValueError:
        datum = tage[0]
    if datum not in tage:
        datum = tage[0]
    inhalt = lesestand.konto(db, konto.kunde_id) if konto else None
    gruppe = inhalt.kundengruppe if inhalt else None
    ansicht = slots.tagesansicht(b, lesestand.tarife(db), gruppe, datum, jetzt)
    return render(
        request,
        "belegung.html",
        konto=konto,
        belegung=b,
        tage=tage,
        datum=datum,
        ansicht=ansicht,
    )
