import logging
import uuid
from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from beachhub_core import auth
from beachhub_core.database import get_db
from beachhub_core.models import AdminUser, Kunde, Rechnung
from beachhub_core.routes._form import fehlertext, t_datum, t_uuid
from beachhub_core.services import benachrichtigung, rechnung_pdf, rechnungen
from beachhub_core.services import storno as storno_dienst
from beachhub_core.services.rechnungen import RechnungsFehler
from beachhub_core.templating import mit_flash, render

router = APIRouter()
logger = logging.getLogger(__name__)

GRUND = {
    "nicht_offen": "Rechnung ist nicht offen",
    "nicht_stornierbar": "Rechnung ist bereits storniert oder selbst eine Stornorechnung",
    "pdf_vorhanden": "Rechnungs-PDF existiert bereits",
    "bereits_korrigiert": "Eine der Positionen wurde bereits korrigiert",
    "keine_positionen": "Bitte mindestens eine Position auswählen",
    "verschiedene_rechnungen": "Die Positionen gehören nicht zu dieser Rechnung",
}


def _datum_oder_none(v: str) -> date | None:
    try:
        return t_datum(v)
    except ValueError:
        return None


@router.get("/rechnungen", response_class=HTMLResponse)
def liste(
    request: Request,
    status: str = "",
    von: str = "",
    bis: str = "",
    q: str = "",
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    von_d, bis_d = _datum_oder_none(von), _datum_oder_none(bis)
    stmt = select(Rechnung).join(Kunde).order_by(Rechnung.nummer.desc())
    if status:
        stmt = stmt.where(Rechnung.status == status)
    if von_d:
        stmt = stmt.where(Rechnung.datum >= von_d)
    if bis_d:
        stmt = stmt.where(Rechnung.datum <= bis_d)
    if q:
        stmt = stmt.where(Kunde.name.ilike(f"%{q}%"))
    gefunden = db.scalars(stmt.limit(500)).all()
    return render(
        request,
        "rechnungen/liste.html",
        admin=admin,
        rechnungen=gefunden,
        offen_je_rechnung=rechnungen.offene_betraege(db, gefunden),
        status=status,
        von=von,
        bis=bis,
        q=q,
    )


@router.get("/rechnungen/export.csv")
def export(
    von: str,
    bis: str,
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> Response:
    v, b = _datum_oder_none(von) or date(2000, 1, 1), _datum_oder_none(bis) or date(2100, 1, 1)
    return Response(
        rechnungen.csv_export(db, v, b),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="rechnungen_{v}_{b}.csv"'},
    )


@router.get("/rechnungen/{rechnung_id}", response_class=HTMLResponse)
def detail(
    request: Request,
    rechnung_id: uuid.UUID,
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    r = db.get(Rechnung, rechnung_id)
    if r is None:
        return mit_flash(
            RedirectResponse("/admin/rechnungen", status_code=303), "Nicht gefunden", "fehler"
        )  # type: ignore[return-value]
    return render(
        request,
        "rechnungen/detail.html",
        admin=admin,
        r=r,
        steuer=rechnungen.steuer_je_satz(r),
        offen=rechnungen.offener_betrag(db, r),
        verrechnet=rechnungen.verrechnet(db, r),
        korrekturen=db.scalars(
            select(Rechnung)
            .where(Rechnung.korrigiert_rechnung_id == r.id)
            .order_by(Rechnung.nummer)
        ).all(),
        integritaet=rechnung_pdf.pruefe_integritaet(r),
    )


@router.get("/rechnungen/{rechnung_id}/pdf", response_model=None)
def pdf(
    rechnung_id: uuid.UUID,
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> FileResponse | RedirectResponse:
    r = db.get(Rechnung, rechnung_id)
    if r is None:
        return mit_flash(
            RedirectResponse("/admin/rechnungen", status_code=303), "Nicht gefunden", "fehler"
        )
    if not r.pdf_pfad:
        return mit_flash(
            RedirectResponse(f"/admin/rechnungen/{r.id}", status_code=303),
            "PDF noch nicht erzeugt",
            "fehler",
        )
    return FileResponse(r.pdf_pfad, media_type="application/pdf", filename=f"{r.nummer}.pdf")


@router.post("/rechnungen/{rechnung_id}/pdf", response_model=None)
def pdf_erzeugen(
    rechnung_id: uuid.UUID,
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    r = db.get(Rechnung, rechnung_id)
    if r is None:
        return mit_flash(
            RedirectResponse("/admin/rechnungen", status_code=303), "Nicht gefunden", "fehler"
        )
    try:
        rechnung_pdf.erzeuge(db, r)
        db.commit()
    except RechnungsFehler as e:
        db.rollback()
        return mit_flash(
            RedirectResponse(f"/admin/rechnungen/{r.id}", status_code=303),
            fehlertext(e, GRUND),
            "fehler",
        )
    return mit_flash(RedirectResponse(f"/admin/rechnungen/{r.id}", status_code=303), "PDF erzeugt")


@router.post("/rechnungen/{rechnung_id}/bezahlt")
def bezahlt(
    rechnung_id: uuid.UUID,
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    r = db.get(Rechnung, rechnung_id)
    if r is None:
        return mit_flash(
            RedirectResponse("/admin/rechnungen", status_code=303), "Nicht gefunden", "fehler"
        )
    try:
        rechnungen.setze_bezahlt(db, r, admin_user_id=admin.id)
        db.commit()
    except (RechnungsFehler, ValueError, IntegrityError) as e:
        db.rollback()
        return mit_flash(
            RedirectResponse(f"/admin/rechnungen/{r.id}", status_code=303),
            fehlertext(e, GRUND),
            "fehler",
        )
    return mit_flash(
        RedirectResponse(f"/admin/rechnungen/{r.id}", status_code=303), "Als bezahlt markiert"
    )


@router.post("/rechnungen/{rechnung_id}/storno")
def storno(
    rechnung_id: uuid.UUID,
    grund: str = Form(...),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    r = db.get(Rechnung, rechnung_id)
    if r is None:
        return mit_flash(
            RedirectResponse("/admin/rechnungen", status_code=303), "Nicht gefunden", "fehler"
        )
    try:
        s = rechnungen.storniere(db, r, admin_user_id=admin.id, grund=grund)
        db.commit()
    except (RechnungsFehler, ValueError, IntegrityError) as e:
        db.rollback()
        return mit_flash(
            RedirectResponse(f"/admin/rechnungen/{r.id}", status_code=303),
            fehlertext(e, GRUND),
            "fehler",
        )
    try:
        rechnung_pdf.erzeuge(db, s)
        db.commit()
    except RechnungsFehler:
        logger.exception("PDF-Erzeugung für Stornorechnung %s fehlgeschlagen", s.nummer)
        db.rollback()
    else:
        benachrichtigung.rechnung(db, s)
    return mit_flash(
        RedirectResponse(f"/admin/rechnungen/{s.id}", status_code=303),
        f"Stornorechnung {s.nummer} erzeugt",
    )


@router.post("/rechnungen/{rechnung_id}/teilstorno", response_model=None)
async def teilstorno(
    request: Request,
    rechnung_id: uuid.UUID,
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Korrigiert ausgewählte Rechnungspositionen; bezahlte Anteile werden Guthaben.

    Die zugehörigen Buchungen bleiben unverändert (A-ADM-4, Abweichung B-8).
    """
    r = db.get(Rechnung, rechnung_id)
    if r is None:
        return mit_flash(
            RedirectResponse("/admin/rechnungen", status_code=303), "Nicht gefunden", "fehler"
        )
    form = await request.form()
    try:
        ids = [t_uuid(str(v)) for v in form.getlist("positionen")]
        positionen = [p for p in r.positionen if p.id in ids]
        if len(positionen) != len(ids):
            raise RechnungsFehler("verschiedene_rechnungen")
        beleg = storno_dienst.gutschreiben_positionen(
            db,
            positionen,
            grund=str(form.get("grund", "")).strip() or "Teil-Storno",
            quelle="admin",
            admin_user_id=admin.id,
        )
        beleg_id = beleg.id
        db.commit()
    except (RechnungsFehler, ValueError, IntegrityError) as e:
        db.rollback()
        return mit_flash(
            RedirectResponse(f"/admin/rechnungen/{rechnung_id}", status_code=303),
            fehlertext(e, GRUND),
            "fehler",
        )
    benachrichtigung.belege_versenden(db, [beleg_id])
    return mit_flash(
        RedirectResponse(f"/admin/rechnungen/{beleg_id}", status_code=303),
        "Teil-Stornorechnung erzeugt",
    )
