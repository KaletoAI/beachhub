"""Jinja2-Templates, Filter und Flash-Meldungen des Portals."""

from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

import jinja2
from beachhub_shared.zeit import lokal
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeTimedSerializer

from beachhub_portal.config import settings
from beachhub_portal.models import Konto

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
# Nur .html automatisch escapen – die Mailvorlagen (.txt) sollen Sonderzeichen unverändert zeigen.
templates.env.autoescape = jinja2.select_autoescape(
    enabled_extensions=("html", "htm"), default_for_string=False, default=False
)
_WT = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
_flash = URLSafeTimedSerializer(settings.secret_key, salt="flash")
FLASH_COOKIE = "bp_flash"


def euro(v: Decimal | str | None) -> str:
    if v is None:
        return "–"
    d = Decimal(str(v))
    return f"{d:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"


def f_lokal(v: datetime) -> str:
    lv = lokal(v)
    return f"{_WT[lv.weekday()]} {lv:%d.%m.%Y %H:%M}"


def f_datum(v: date | datetime) -> str:
    return (lokal(v) if isinstance(v, datetime) else v).strftime("%d.%m.%Y")


def f_uhrzeit(v: time | datetime) -> str:
    return (lokal(v) if isinstance(v, datetime) else v).strftime("%H:%M")


def f_tag(v: date) -> str:
    return f"{_WT[v.weekday()]} {v:%d.%m.}"


templates.env.filters.update(
    {"euro": euro, "lokal": f_lokal, "datum": f_datum, "uhrzeit": f_uhrzeit, "tag": f_tag}
)


def render(
    request: Request,
    name: str,
    konto: Konto | None = None,
    status_code: int = 200,
    **ctx: Any,
) -> HTMLResponse:
    flash = None
    roh = request.cookies.get(FLASH_COOKIE)
    if roh:
        try:
            flash = _flash.loads(roh, max_age=60)
        except BadSignature:
            flash = None
    resp = templates.TemplateResponse(
        request,
        name,
        {
            "konto": konto,
            "csrf_token": getattr(request.state, "csrf", ""),
            "flash": flash,
            "betreiber": settings.betreiber_name,
            **ctx,
        },
        status_code=status_code,
    )
    if roh:
        resp.delete_cookie(FLASH_COOKIE, path="/")
    return resp


def mit_flash(response: Any, text: str, art: str = "ok") -> Any:
    response.set_cookie(
        FLASH_COOKIE,
        _flash.dumps({"text": text, "art": art}),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=60,
        path="/",
    )
    return response
