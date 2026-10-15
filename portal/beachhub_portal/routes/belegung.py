from datetime import date, datetime, timedelta

from beachhub_shared.lesestand import BelegungInhalt
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
    i = tage.index(datum)
    return render(
        request,
        "belegung.html",
        konto=konto,
        belegung=b,
        wochen=_wochen(b, tage, jetzt),
        erster=tage[0],
        datum=datum,
        vortag=tage[i - 1] if i > 0 else None,
        folgetag=tage[i + 1] if i + 1 < len(tage) else None,
        ansicht=ansicht,
    )


def _wochen(
    b: BelegungInhalt, tage: list[date], jetzt: datetime
) -> list[list[tuple[date, bool] | None]]:
    """Buchbare Tage als Kalender Mo–So, damit alle ohne Scrollen sichtbar sind. Jeder Tag
    trägt mit, ob er noch freie Slots hat; Lücken vor dem ersten und nach dem letzten Tag."""
    frei = {
        t: any(
            slots.zustand(b, f.id, s, jetzt) == "frei"
            for f in slots.felder(b)
            for s in slots.tages_slots(b, f, t)
        )
        for t in tage
    }
    erster = tage[0] - timedelta(days=tage[0].weekday())
    wochen = []
    while erster <= tage[-1]:
        woche = [erster + timedelta(days=i) for i in range(7)]
        wochen.append([(d, frei[d]) if d in frei else None for d in woche])
        erster += timedelta(days=7)
    return wochen
