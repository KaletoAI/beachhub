from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_core import auth, clock
from beachhub_core.config import settings
from beachhub_core.database import get_db
from beachhub_core.models import AdminUser, Audit, Buchung, LesestandVersion, Storno
from beachhub_core.routes._form import fehlertext, t_datum
from beachhub_core.services import lesestand
from beachhub_core.templating import mit_flash, render

router = APIRouter()

SCHLUESSEL_FEHLT = "Signaturschlüssel fehlt: beachhub-core keygen ausführen"


@router.get("/system", response_class=HTMLResponse)
def index(
    request: Request,
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    schluessel = (
        lesestand.oeffentlicher_schluessel()
        if settings.signatur_privatschluessel_pfad.exists()
        else None
    )
    return render(
        request,
        "system/index.html",
        admin=admin,
        schluessel=schluessel,
        heute=clock.today(db),
        override=clock.override(db),
        versionen=db.scalars(select(LesestandVersion).order_by(LesestandVersion.dokument)).all(),
    )


@router.post("/system/lesestand")
def lesestand_erzeugen(
    alle: str = Form(""),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    try:
        if alle == "1":
            lesestand.markiere_geaendert(db, "belegung", "tarife")
        namen = lesestand.verarbeite_geaenderte(db)
    except FileNotFoundError:
        db.rollback()
        return mit_flash(
            RedirectResponse("/admin/system", status_code=303), SCHLUESSEL_FEHLT, "fehler"
        )
    except (ValueError, KeyError) as e:
        db.rollback()
        return mit_flash(
            RedirectResponse("/admin/system", status_code=303), fehlertext(e), "fehler"
        )
    return mit_flash(
        RedirectResponse("/admin/system", status_code=303), f"{len(namen)} Dokumente erzeugt"
    )


@router.post("/system/uhr")
def uhr(
    datum: str = Form(""),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    try:
        neu = t_datum(datum)
        clock.set_override(db, neu)
    except ValueError as e:
        db.rollback()
        return mit_flash(
            RedirectResponse("/admin/system", status_code=303), fehlertext(e), "fehler"
        )
    return mit_flash(
        RedirectResponse("/admin/system", status_code=303),
        "Uhr gesetzt" if neu else "Uhr zurückgesetzt",
    )


@router.get("/system/audit", response_class=HTMLResponse)
def audit_liste(
    request: Request,
    typ: str = "",
    seite: int = 1,
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    seite = max(seite, 1)
    stmt = select(Audit).order_by(Audit.zeitpunkt.desc())
    if typ:
        stmt = stmt.where(Audit.objekt_typ == typ)
    eintraege = db.scalars(stmt.offset((seite - 1) * 100).limit(100)).all()
    typen = db.scalars(select(Audit.objekt_typ).distinct().order_by(Audit.objekt_typ)).all()
    admin_namen = {a.id: a.name for a in db.scalars(select(AdminUser))}
    return render(
        request,
        "system/audit.html",
        admin=admin,
        eintraege=eintraege,
        typ=typ,
        seite=seite,
        typen=typen,
        admin_namen=admin_namen,
    )


@router.get("/system/stornos", response_class=HTMLResponse)
def stornos(
    request: Request,
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    offene = db.scalars(
        select(Storno)
        .join(Buchung, Storno.buchung_id == Buchung.id)
        .where(Storno.nachbuchung_offen.is_(True))
        .order_by(Buchung.beginn)
    ).all()
    return render(request, "system/stornos.html", admin=admin, stornos=offene)
