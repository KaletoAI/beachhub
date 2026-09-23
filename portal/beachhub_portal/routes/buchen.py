"""Buchen mit Folgeslots und die Warteseite bis zur Antwort des Hauptsystems (Task 12)."""

import uuid
from datetime import datetime, timedelta
from typing import Any

from beachhub_shared import kanal
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_portal import auth, uhr
from beachhub_portal.database import get_db
from beachhub_portal.models import Anfrage, Konto
from beachhub_portal.services import anfragen, lesestand, slots
from beachhub_portal.services import tarife as tarif_dienst
from beachhub_portal.templating import mit_flash, render

router = APIRouter()

# Ruling Task 12: Ein Doppelklick auf „Verbindlich buchen“ darf keine zweite Anfrage erzeugen –
# der Kunde sähe beim zweiten POST sonst ggf. „belegt“, obwohl die erste Anfrage längst
# reserviert wurde. Als Duplikat gilt: dieselbe noch offene/abgeholte Anfrage, oder eine erst
# vor Kurzem beantwortete (innerhalb dieses Fensters) – danach ist ein neuer Versuch ein neuer
# Buchungswunsch, kein Doppelklick mehr.
DOPPELKLICK_FENSTER = timedelta(minutes=2)


def _zeitpunkt(wert: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(wert)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else None


@router.get("/buchen", response_class=HTMLResponse)
def buchen_seite(
    request: Request,
    feld: str = "",
    beginn: str = "",
    konto: Konto | None = Depends(auth.konto_optional),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    b = lesestand.belegung(db)
    start = _zeitpunkt(beginn)
    f = slots.feld(b, feld) if b else None
    folge = slots.folge(b, feld, start, uhr.jetzt()) if (b and f and start) else []
    if not folge or b is None or f is None or start is None:
        return render(request, "buchen.html", konto=konto, status_code=409, nicht_frei=True)
    inhalt = lesestand.konto(db, konto.kunde_id) if konto else None
    t = lesestand.tarife(db)
    optionen = [
        {
            "ende": s.ende,
            "preis": (
                tarif_dienst.preis(t, feld, folge[: i + 1], inhalt.kundengruppe)
                if t and inhalt
                else None
            ),
        }
        for i, s in enumerate(folge)
    ]
    return render(
        request,
        "buchen.html",
        konto=konto,
        feld=f,
        beginn=start,
        optionen=optionen,
        storno_frist=b.storno_frist_stunden,
    )


def _bestehende_anfrage(
    db: Session, konto_id: uuid.UUID, validiert: dict[str, Any], jetzt: datetime
) -> Anfrage | None:
    kandidaten = db.scalars(
        select(Anfrage)
        .where(
            Anfrage.konto_id == konto_id,
            Anfrage.typ == "buchung_anfragen",
            Anfrage.nutzlast_json == validiert,
        )
        .order_by(Anfrage.erstellt_am.desc())
    ).all()
    for a in kandidaten:
        if a.status != Anfrage.BEANTWORTET:
            return a
        if a.beantwortet_am is not None and jetzt - a.beantwortet_am <= DOPPELKLICK_FENSTER:
            return a
    return None


@router.post("/buchen")
def buchen(
    feld: str = Form(...),
    beginn: str = Form(...),
    ende: str = Form(...),
    konto: Konto = Depends(auth.konto_pflicht),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    b = lesestand.belegung(db)
    start, schluss = _zeitpunkt(beginn), _zeitpunkt(ende)
    folge = slots.folge(b, feld, start, uhr.jetzt()) if (b and start) else []
    if start is None or schluss is None or schluss not in [s.ende for s in folge]:
        return mit_flash(
            RedirectResponse("/", status_code=303),
            "Der Termin ist nicht mehr frei. Bitte wähle einen anderen.",
            "fehler",
        )
    nutzlast = {"feld_id": feld, "beginn": start.isoformat(), "ende": schluss.isoformat()}
    validiert = kanal.BuchungAnfragen.model_validate(nutzlast).model_dump(mode="json")
    # Konto-Zeile sperren, solange auf ein Duplikat geprüft und ggf. angelegt wird: Zwei
    # gleichzeitige Doppelklick-POSTs serialisieren sich hier; der zweite wartet auf die Sperre
    # und sieht danach (READ COMMITTED) die vom ersten bereits committete neue Anfrage.
    db.get(Konto, konto.id, with_for_update=True)
    bestehende = _bestehende_anfrage(db, konto.id, validiert, uhr.jetzt())
    if bestehende is not None:
        db.commit()  # Sperre freigeben, auch wenn nichts geschrieben wurde.
        return RedirectResponse(f"/anfrage/{bestehende.id}?weiter=1", status_code=303)
    a = anfragen.stelle(db, typ="buchung_anfragen", konto_id=konto.id, nutzlast=nutzlast)
    return RedirectResponse(f"/anfrage/{a.id}?weiter=1", status_code=303)


def _eigene(db: Session, anfrage_id: uuid.UUID, konto: Konto) -> Anfrage:
    a = db.get(Anfrage, anfrage_id)
    if a is None or a.konto_id != konto.id:
        raise HTTPException(status_code=404)
    return a


def _stand(db: Session, a: Anfrage, konto: Konto, weiter: bool) -> anfragen.Stand:
    b = lesestand.belegung(db)
    return anfragen.stand(
        db,
        a,
        konto.kunde_id,
        uhr.jetzt(),
        weiter=weiter,
        hinweis_sekunden=b.antwort_hinweis_sekunden if b else 120,
    )


@router.get("/anfrage/{anfrage_id}", response_model=None)
def anfrage_seite(
    request: Request,
    anfrage_id: uuid.UUID,
    weiter: int = 0,
    konto: Konto = Depends(auth.konto_pflicht),
    db: Session = Depends(get_db),
) -> Response:
    a = _eigene(db, anfrage_id, konto)
    st = _stand(db, a, konto, bool(weiter))
    if st.ziel:
        return RedirectResponse(st.ziel, status_code=303)
    stand_url = f"/anfrage/{a.id}/stand" + ("?weiter=1" if weiter else "")
    return render(request, "anfrage.html", konto=konto, stand=st, stand_url=stand_url)


@router.get("/anfrage/{anfrage_id}/stand")
def anfrage_stand(
    anfrage_id: uuid.UUID,
    weiter: int = 0,
    konto: Konto = Depends(auth.konto_pflicht),
    db: Session = Depends(get_db),
) -> dict[str, str | None]:
    st = _stand(db, _eigene(db, anfrage_id, konto), konto, bool(weiter))
    return {"zustand": st.zustand, "text": st.text, "ziel": st.ziel}
