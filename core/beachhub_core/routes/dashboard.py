from datetime import time, timedelta

from beachhub_shared.zeit import kombiniere
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from beachhub_core import auth, clock
from beachhub_core.database import get_db
from beachhub_core.models import AdminUser, Buchung, Storno
from beachhub_core.templating import render

router = APIRouter()


@router.get("", response_class=HTMLResponse)
def dashboard(
    request: Request,
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    heute = clock.today(db)
    von, bis = kombiniere(heute, time(0)), kombiniere(heute + timedelta(days=1), time(0))
    n_heute = (
        db.scalar(
            select(func.count())
            .select_from(Buchung)
            .where(
                Buchung.status.in_(Buchung.AKTIVE_STATUS),
                Buchung.beginn >= von,
                Buchung.beginn < bis,
            )
        )
        or 0
    )
    n_storno = (
        db.scalar(
            select(func.count()).select_from(Storno).where(Storno.nachbuchung_offen.is_(True))
        )
        or 0
    )
    rechnungen_offen = 0  # Task 15 ersetzt das durch die echte Zählung
    return render(
        request,
        "dashboard.html",
        admin=admin,
        heute=n_heute,
        stornos_offen=n_storno,
        rechnungen_offen=rechnungen_offen,
    )
