import uuid
from datetime import timedelta
from decimal import InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from beachhub_core import auth, clock
from beachhub_core.database import get_db
from beachhub_core.models import (
    AdminUser,
    Audit,
    Buchung,
    GuthabenBuchung,
    Kunde,
    Kundengruppe,
    Rechnung,
)
from beachhub_core.routes._form import fehlertext, pflicht, t_betrag, t_uuid
from beachhub_core.services import guthaben, kunden
from beachhub_core.services.guthaben import GuthabenFehler
from beachhub_core.services.kunden import KundenFehler
from beachhub_core.templating import mit_flash, render

router = APIRouter()

FEHLERTEXT = {
    "email_vergeben": "E-Mail-Adresse ist bereits vergeben",
    "gruppe_unbekannt": "Kundengruppe unbekannt",
    "nicht_gedeckt": "Guthaben nicht gedeckt",
    "betrag_muss_negativ_sein": "Auszahlung muss negativ sein",
    "art_unbekannt": "Art unbekannt",
    "unique": "E-Mail-Adresse ist bereits vergeben",
}

# Alle vom Admin-Formular her erwartbaren Fehler: Domänenvalidierung (KundenFehler/GuthabenFehler),
# fehlerhafte/​leere Formularwerte (ValueError aus routes._form), ungültige Decimal-Literale
# (InvalidOperation, wird von t_betrag zwar schon in ValueError gewandelt, hier zur Sicherheit
# trotzdem mitgefangen) sowie DB-Constraint-Verletzungen (IntegrityError, z. B. doppelte E-Mail
# bei einem Wettlauf zweier gleichzeitiger Anfragen).
FORM_FEHLER = (KundenFehler, GuthabenFehler, ValueError, InvalidOperation, IntegrityError)


def _liste_ctx(db: Session, q: str) -> dict:  # type: ignore[type-arg]
    stmt = select(Kunde).where(Kunde.anonymisiert_am.is_(None)).order_by(Kunde.name)
    if q:
        stmt = stmt.where(or_(Kunde.name.ilike(f"%{q}%"), Kunde.email.ilike(f"%{q}%")))
    return {
        "kunden": db.scalars(stmt.limit(200)).all(),
        "q": q,
        "gruppen": db.scalars(select(Kundengruppe).order_by(Kundengruppe.name)).all(),
    }


@router.get("/kunden", response_class=HTMLResponse)
def liste(
    request: Request,
    q: str = "",
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return render(request, "kunden/liste.html", admin=admin, **_liste_ctx(db, q))


@router.post("/kunden", response_model=None)
def anlegen(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    kundengruppe_id: str = Form(...),
    zahlungsart: str = Form(""),
    adresse_strasse: str = Form(""),
    adresse_plz: str = Form(""),
    adresse_ort: str = Form(""),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    try:
        k = kunden.lege_an(
            db,
            name=pflicht(name.strip() or None, "Name"),
            email=email,
            kundengruppe_id=pflicht(t_uuid(kundengruppe_id), "Kundengruppe"),
            zahlungsart=zahlungsart or None,
            adresse_strasse=adresse_strasse,
            adresse_plz=adresse_plz,
            adresse_ort=adresse_ort,
            admin_user_id=admin.id,
        )
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        return render(
            request,
            "kunden/liste.html",
            admin=admin,
            fehler=fehlertext(e, FEHLERTEXT),
            **_liste_ctx(db, ""),
        )
    return mit_flash(RedirectResponse(f"/admin/kunden/{k.id}", status_code=303), "Kunde angelegt")


def _detail_ctx(db: Session, k: Kunde) -> dict:  # type: ignore[type-arg]
    seit = clock.now(db) - timedelta(days=365)
    return {
        "kunde": k,
        "gruppen": db.scalars(select(Kundengruppe).order_by(Kundengruppe.name)).all(),
        "buchungen": db.scalars(
            select(Buchung)
            .where(Buchung.kunde_id == k.id, Buchung.beginn >= seit)
            .order_by(Buchung.beginn.desc())
        ).all(),
        "rechnungen": db.scalars(
            select(Rechnung).where(Rechnung.kunde_id == k.id).order_by(Rechnung.datum.desc())
        ).all(),
        "guthaben": db.scalars(
            select(GuthabenBuchung)
            .where(GuthabenBuchung.kunde_id == k.id)
            .order_by(GuthabenBuchung.created_at.desc())
        ).all(),
        "audit": db.scalars(
            select(Audit)
            .where(Audit.objekt_typ == "kunde", Audit.objekt_id == k.id)
            .order_by(Audit.zeitpunkt.desc())
            .limit(50)
        ).all(),
    }


@router.get("/kunden/{kunde_id}", response_class=HTMLResponse)
def detail(
    request: Request,
    kunde_id: uuid.UUID,
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    k = db.get(Kunde, kunde_id)
    if k is None:
        return mit_flash(
            RedirectResponse("/admin/kunden", status_code=303), "Kunde nicht gefunden", "fehler"
        )  # type: ignore[return-value]
    return render(request, "kunden/detail.html", admin=admin, **_detail_ctx(db, k))


@router.post("/kunden/{kunde_id}", response_model=None)
def aendern(
    request: Request,
    kunde_id: uuid.UUID,
    name: str = Form(...),
    email: str = Form(...),
    kundengruppe_id: str = Form(...),
    zahlungsart: str = Form(...),
    adresse_strasse: str = Form(""),
    adresse_plz: str = Form(""),
    adresse_ort: str = Form(""),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    k = db.get(Kunde, kunde_id)
    if k is None:
        return mit_flash(
            RedirectResponse("/admin/kunden", status_code=303), "Kunde nicht gefunden", "fehler"
        )
    try:
        kunden.aendere(
            db,
            k,
            admin_user_id=admin.id,
            name=pflicht(name.strip() or None, "Name"),
            email=email,
            kundengruppe_id=pflicht(t_uuid(kundengruppe_id), "Kundengruppe"),
            zahlungsart=zahlungsart,
            adresse_strasse=adresse_strasse,
            adresse_plz=adresse_plz,
            adresse_ort=adresse_ort,
        )
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        return render(
            request,
            "kunden/detail.html",
            admin=admin,
            fehler=fehlertext(e, FEHLERTEXT),
            **_detail_ctx(db, k),
        )
    return mit_flash(RedirectResponse(f"/admin/kunden/{k.id}", status_code=303), "Gespeichert")


@router.post("/kunden/{kunde_id}/guthaben", response_model=None)
def guthaben_buchen(
    request: Request,
    kunde_id: uuid.UUID,
    betrag: str = Form(...),
    art: str = Form(...),
    notiz: str = Form(""),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    k = db.get(Kunde, kunde_id)
    if k is None:
        return mit_flash(
            RedirectResponse("/admin/kunden", status_code=303), "Kunde nicht gefunden", "fehler"
        )
    try:
        if art not in ("manuell", "auszahlung"):
            raise GuthabenFehler("art_unbekannt")
        guthaben.buche(
            db,
            kunde=k,
            betrag=pflicht(t_betrag(betrag), "Betrag"),
            art=art,
            notiz=notiz,
            admin_user_id=admin.id,
        )
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        return render(
            request,
            "kunden/detail.html",
            admin=admin,
            fehler=fehlertext(e, FEHLERTEXT),
            **_detail_ctx(db, k),
        )
    return mit_flash(RedirectResponse(f"/admin/kunden/{k.id}", status_code=303), "Guthaben gebucht")


@router.post("/kunden/{kunde_id}/anonymisieren", response_model=None)
def anonymisieren(
    request: Request,
    kunde_id: uuid.UUID,
    bestaetigt: str = Form(""),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    k = db.get(Kunde, kunde_id)
    if k is None:
        return mit_flash(
            RedirectResponse("/admin/kunden", status_code=303), "Kunde nicht gefunden", "fehler"
        )
    try:
        if bestaetigt != "1":
            raise ValueError("Bitte die Anonymisierung bestätigen")
        kunden.anonymisiere(db, k, admin_user_id=admin.id)
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        return render(
            request,
            "kunden/detail.html",
            admin=admin,
            fehler=fehlertext(e, FEHLERTEXT),
            **_detail_ctx(db, k),
        )
    return mit_flash(RedirectResponse("/admin/kunden", status_code=303), "Kunde anonymisiert")
