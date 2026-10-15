"""System → Postausgang: verschickte und zurückgehaltene Mails im Testsystem (siehe
`beachhub_core.postausgang`). Im Produktivbetrieb gibt es die Seiten nicht."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from beachhub_core import auth, postausgang
from beachhub_core.config import settings
from beachhub_core.database import get_db
from beachhub_core.models import AdminUser
from beachhub_core.services import audit
from beachhub_core.templating import mit_flash, render

router = APIRouter()
LISTE = "/admin/system/postausgang"


def _nur_testsystem() -> None:
    if not postausgang.aktiv():
        raise HTTPException(status_code=404)


@router.get("/system/postausgang", response_class=HTMLResponse)
def liste(request: Request, admin: AdminUser = Depends(auth.aktueller_admin)) -> HTMLResponse:
    _nur_testsystem()
    return render(
        request,
        "system/postausgang.html",
        admin=admin,
        mails=postausgang.liste(),
        versand=postausgang.versand_an(),
        smtp=bool(settings.smtp_host),
    )


@router.post("/system/postausgang/versand")
def versand(
    an: str = Form(""),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    _nur_testsystem()
    vorher, neu = postausgang.versand_an(), an == "1"
    postausgang.setze_versand(neu)
    audit.protokolliere(
        db,
        quelle="admin",
        objekt_typ="postausgang",
        objekt_id=None,
        vorher={"versand": vorher},
        nachher={"versand": neu},
        admin_user_id=admin.id,
    )
    db.commit()
    text = "Mails werden verschickt" if neu else "Mails landen nur noch im Postausgang"
    return mit_flash(RedirectResponse(LISTE, status_code=303), text)


@router.post("/system/postausgang/leeren")
def leeren(admin: AdminUser = Depends(auth.nur_admin_rolle)) -> RedirectResponse:
    _nur_testsystem()
    anzahl = postausgang.leeren()
    return mit_flash(RedirectResponse(LISTE, status_code=303), f"{anzahl} Mails gelöscht")


@router.get("/system/postausgang/{mail_id}", response_class=HTMLResponse)
def detail(
    request: Request, mail_id: str, admin: AdminUser = Depends(auth.aktueller_admin)
) -> HTMLResponse:
    _nur_testsystem()
    geladen = postausgang.lade(mail_id)
    if geladen is None:
        raise HTTPException(status_code=404)
    eintrag, m = geladen
    return render(
        request,
        "system/postausgang_mail.html",
        admin=admin,
        e=eintrag,
        von=str(m["From"]),
        text=postausgang.text(m),
    )


@router.get("/system/postausgang/{mail_id}/anhang/{nummer}")
def anhang(mail_id: str, nummer: int, admin: AdminUser = Depends(auth.aktueller_admin)) -> Response:
    _nur_testsystem()
    geladen = postausgang.lade(mail_id)
    teil = postausgang.anhang(geladen[1], nummer) if geladen else None
    if teil is None:
        raise HTTPException(status_code=404)
    name, typ, daten = teil
    name = "".join(z for z in name if z.isalnum() or z in "-_.") or "anhang"
    # Inline, damit das PDF im Browser so erscheint, wie der Kunde es öffnet.
    return Response(
        daten, media_type=typ, headers={"Content-Disposition": f'inline; filename="{name}"'}
    )


@router.get("/system/postausgang/{mail_id}/mail.eml")
def original(mail_id: str, admin: AdminUser = Depends(auth.aktueller_admin)) -> Response:
    _nur_testsystem()
    daten = postausgang.roh(mail_id)
    if daten is None:
        raise HTTPException(status_code=404)
    return Response(
        daten,
        media_type="message/rfc822",
        headers={"Content-Disposition": f'attachment; filename="{mail_id}.eml"'},
    )
