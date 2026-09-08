from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_core import auth
from beachhub_core.database import get_db
from beachhub_core.models import AdminUser
from beachhub_core.templating import render

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_seite(request: Request) -> HTMLResponse:
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
    ok = (
        user is not None
        and auth.pruefe_passwort(passwort, user.passwort_hash)
        and auth.pruefe_totp(user.totp_secret, code)
    )
    if not ok or user is None:
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
