import re

from beachhub_shared import kanal
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from beachhub_portal import auth, uhr
from beachhub_portal.database import get_db
from beachhub_portal.models import Konto
from beachhub_portal.services import anfragen, lesestand, rechnung_link
from beachhub_portal.templating import mit_flash, render

router = APIRouter()


@router.get("/rechnungen", response_class=HTMLResponse)
def rechnungen_seite(
    request: Request, konto: Konto = Depends(auth.konto_pflicht), db: Session = Depends(get_db)
) -> HTMLResponse:
    inhalt = lesestand.konto(db, konto.kunde_id)
    return render(
        request, "rechnungen.html", konto=konto, rechnungen=inhalt.rechnungen if inhalt else None
    )


@router.post("/rechnungen/{nummer}/anfordern")
def anfordern(
    nummer: str, konto: Konto = Depends(auth.konto_pflicht), db: Session = Depends(get_db)
) -> RedirectResponse:
    inhalt = lesestand.konto(db, konto.kunde_id)
    # Ruling Fix-Runde 1: die Länge hier mitprüfen (Grenze aus kanal.RechnungAnfordern,
    # max_length=20) – die echten Rechnungsnummern sind immer kürzer, aber ohne diese Prüfung
    # würde ein Lesestand mit einer zu langen Nummer weiter unten eine rohe ValidationError aus
    # `model_validate` auslösen (500) statt der üblichen Fehlermeldung.
    gueltig = (
        inhalt is not None and len(nummer) <= 20 and nummer in {r.nummer for r in inhalt.rechnungen}
    )
    if not gueltig:
        return mit_flash(
            RedirectResponse("/rechnungen", status_code=303),
            "Diese Rechnung gibt es nicht.",
            "fehler",
        )
    nutzlast = {"rechnung_nr": nummer}
    validiert = kanal.RechnungAnfordern.model_validate(nutzlast).model_dump(mode="json")
    # Doppelklick-Schutz wie beim Buchen (Task 12) und Stornieren (Task 14, Controller-Ruling
    # Task 15): Konto-Zeile sperren, solange auf ein Duplikat geprüft und ggf. angelegt wird.
    # `nur_offen=True` (Ruling Fix-Runde 1): eine schon beantwortete Anfrage nie wiederverwenden
    # – ihr Einmal-Link kann bereits verbraucht sein.
    db.get(Konto, konto.id, with_for_update=True)
    bestehende = anfragen.bestehende(
        db, konto.id, "rechnung_anfordern", validiert, uhr.jetzt(), nur_offen=True
    )
    if bestehende is not None:
        db.commit()  # Sperre freigeben, auch wenn nichts geschrieben wurde.
        return RedirectResponse(f"/anfrage/{bestehende.id}", status_code=303)
    a = anfragen.stelle(db, typ="rechnung_anfordern", konto_id=konto.id, nutzlast=nutzlast)
    return RedirectResponse(f"/anfrage/{a.id}", status_code=303)


@router.get("/rechnung/{token}")
def herunterladen(
    token: str, konto: Konto = Depends(auth.konto_pflicht), db: Session = Depends(get_db)
) -> Response:
    ergebnis = rechnung_link.einloesen(db, token=token, konto_id=konto.id, jetzt=uhr.jetzt())
    if ergebnis is None:
        raise HTTPException(
            status_code=404,
            detail="Der Link ist abgelaufen oder wurde schon benutzt. "
            "Bitte fordere die Rechnung noch einmal an.",
        )
    nummer, daten = ergebnis
    # Sicherer Dateiname für Content-Disposition: nur aus der (bereits validierten)
    # Rechnungsnummer, nie aus Nutzereingabe – keine Header-Injektion möglich.
    name = re.sub(r"[^0-9A-Za-z-]", "", nummer) or "Rechnung"
    return Response(
        daten,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="Rechnung-{name}.pdf"',
            "Cache-Control": "no-store",
        },
    )
