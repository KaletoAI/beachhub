"""„Meine Buchungen“ aus dem Lesestand und Storno mit Doppelklick-Schutz (Task 14)."""

from datetime import datetime, timedelta

from beachhub_shared import kanal
from beachhub_shared.lesestand import KontoBuchung
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from beachhub_portal import auth, uhr
from beachhub_portal.database import get_db
from beachhub_portal.models import Konto
from beachhub_portal.services import anfragen, lesestand
from beachhub_portal.templating import mit_flash, render

router = APIRouter()
AKTIV = ("reserviert", "bestaetigt")


def _kommend(b: KontoBuchung, jetzt: datetime) -> bool:
    return b.status in AKTIV and b.ende > jetzt


def _zahlungslink(b: KontoBuchung, jetzt: datetime) -> str | None:
    """Nur ein Zahlungslink, der dieselbe Prüfung besteht wie das Weiterleitungsziel auf der
    Warteseite (Controller-Hinweis Task 14: keine zweite Prüflogik –
    `anfragen.gueltige_checkout_url` wiederverwenden)."""
    if (
        b.checkout_url
        and b.reserviert_bis is not None
        and b.reserviert_bis > jetzt
        and anfragen.gueltige_checkout_url(b.checkout_url)
    ):
        return b.checkout_url
    return None


@router.get("/buchungen", response_class=HTMLResponse)
def buchungen_seite(
    request: Request,
    meldung: str = "",
    konto: Konto = Depends(auth.konto_pflicht),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    text = anfragen.MELDUNGEN.get(meldung)
    inhalt = lesestand.konto(db, konto.kunde_id)
    if inhalt is None:
        return render(request, "buchungen.html", konto=konto, einrichtung=True, meldung=text)
    jetzt = uhr.jetzt()
    kommende = sorted((b for b in inhalt.buchungen if _kommend(b, jetzt)), key=lambda b: b.beginn)
    fruehere = sorted(
        (b for b in inhalt.buchungen if not _kommend(b, jetzt)),
        key=lambda b: b.beginn,
        reverse=True,
    )
    zahlung_urls = {b.id: _zahlungslink(b, jetzt) for b in kommende}
    return render(
        request,
        "buchungen.html",
        konto=konto,
        kommende=kommende,
        fruehere=fruehere,
        jetzt=jetzt,
        zahlung_urls=zahlung_urls,
        meldung=text,
    )


def _stornierbar(db: Session, konto: Konto, buchung_id: str) -> KontoBuchung | None:
    inhalt = lesestand.konto(db, konto.kunde_id)
    if inhalt is None:
        return None
    b = next((x for x in inhalt.buchungen if x.id == buchung_id), None)
    if b is None or b.status not in AKTIV or b.beginn <= uhr.jetzt():
        return None
    return b


@router.get("/buchungen/{buchung_id}/stornieren", response_class=HTMLResponse)
def stornieren_seite(
    request: Request,
    buchung_id: str,
    konto: Konto = Depends(auth.konto_pflicht),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    b = _stornierbar(db, konto, buchung_id)
    if b is None:
        raise HTTPException(
            status_code=404, detail="Diese Buchung kann nicht mehr storniert werden."
        )
    belegung = lesestand.belegung(db)
    frist = belegung.storno_frist_stunden if belegung else 24
    kostenfrei = b.status == "reserviert" or uhr.jetzt() <= b.beginn - timedelta(hours=frist)
    return render(request, "stornieren.html", konto=konto, b=b, kostenfrei=kostenfrei, frist=frist)


@router.post("/buchungen/{buchung_id}/stornieren")
def stornieren(
    buchung_id: str,
    konto: Konto = Depends(auth.konto_pflicht),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    b = _stornierbar(db, konto, buchung_id)
    if b is None:
        return mit_flash(
            RedirectResponse("/buchungen", status_code=303),
            "Diese Buchung kann nicht mehr storniert werden.",
            "fehler",
        )
    nutzlast = {"buchung_id": b.id}
    validiert = kanal.BuchungStornieren.model_validate(nutzlast).model_dump(mode="json")
    # Doppelklick-Schutz wie beim Buchen (Ruling Task 12, Controller-Hinweis Task 14): Konto-Zeile
    # sperren, solange auf ein Duplikat geprüft und ggf. angelegt wird.
    db.get(Konto, konto.id, with_for_update=True)
    bestehende = anfragen.bestehende(db, konto.id, "buchung_stornieren", validiert, uhr.jetzt())
    if bestehende is not None:
        db.commit()  # Sperre freigeben, auch wenn nichts geschrieben wurde.
        return RedirectResponse(f"/anfrage/{bestehende.id}", status_code=303)
    a = anfragen.stelle(db, typ="buchung_stornieren", konto_id=konto.id, nutzlast=nutzlast)
    return RedirectResponse(f"/anfrage/{a.id}", status_code=303)
