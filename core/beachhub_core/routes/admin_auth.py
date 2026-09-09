from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_core import auth
from beachhub_core.database import get_db
from beachhub_core.models import AdminUser
from beachhub_core.templating import render

router = APIRouter()


@router.get("/login", response_class=HTMLResponse, response_model=None)
def login_seite(request: Request, db: Session = Depends(get_db)) -> HTMLResponse | RedirectResponse:
    if auth.lade_session(db, request.cookies.get(auth.COOKIE)) is not None:
        return RedirectResponse("/admin", status_code=303)
    return render(request, "login.html", fehler=False)


@router.post("/login", response_model=None)
def login(
    request: Request,
    name: str = Form(...),
    passwort: str = Form(...),
    code: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    auth.pruefe_rate_limit(request, "login")
    user = db.scalar(
        select(AdminUser).where(AdminUser.name == name.strip(), AdminUser.aktiv.is_(True))
    )
    if not auth.pruefe_login(user, passwort, code) or user is None:
        return render(request, "login.html", fehler=True)
    token, _ = auth.erzeuge_session(db, user)
    resp = RedirectResponse("/admin", status_code=303)
    auth.setze_cookie(resp, token)
    return resp


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    auth.beende_session(db, request.cookies.get(auth.COOKIE))
    resp = RedirectResponse("/admin/login", status_code=303)
    auth.loesche_cookie(resp)
    return resp
