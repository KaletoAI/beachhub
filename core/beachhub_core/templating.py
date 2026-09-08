from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import jinja2
from beachhub_shared.zeit import lokal
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeTimedSerializer

from beachhub_core.config import settings
from beachhub_core.models import AdminUser

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
# Nur .html/.htm automatisch escapen – die reinen Text-Mailvorlagen (.txt) sollen Sonderzeichen
# wie "&" oder Anführungszeichen unverändert ausgeben.
templates.env.autoescape = jinja2.select_autoescape(
    enabled_extensions=("html", "htm"), default_for_string=False, default=False
)
_WT = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
_flash = URLSafeTimedSerializer(settings.secret_key, salt="flash")


def euro(v: Decimal | None) -> str:
    return (
        "–"
        if v is None
        else f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"
    )


def f_lokal(v: datetime) -> str:
    lv = lokal(v)
    return f"{_WT[lv.weekday()]} {lv:%d.%m.%Y %H:%M}"


def f_datum(v: date | datetime) -> str:
    return (lokal(v) if isinstance(v, datetime) else v).strftime("%d.%m.%Y")


def f_uhrzeit(v: time | datetime) -> str:
    return (lokal(v) if isinstance(v, datetime) else v).strftime("%H:%M")


def prozent(v: Decimal) -> str:
    """Decimal("19.00") -> "19 %", Decimal("7.50") -> "7,5 %" (ohne unnötige Nullen)."""
    text = format(v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP).normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text.replace('.', ',')} %"


templates.env.filters.update(
    {"euro": euro, "lokal": f_lokal, "datum": f_datum, "uhrzeit": f_uhrzeit, "prozent": prozent}
)
templates.env.globals["wochentage"] = _WT


def render(request: Request, name: str, admin: AdminUser | None = None, **ctx: Any) -> HTMLResponse:
    flash = None
    roh = request.cookies.get("bh_flash")
    if roh:
        try:
            flash = _flash.loads(roh, max_age=60)
        except BadSignature:
            flash = None
    resp = templates.TemplateResponse(
        request,
        name,
        {
            "admin": admin,
            "csrf_token": getattr(request.state, "csrf", ""),
            "flash": flash,
            **ctx,
        },
    )
    if roh:
        resp.delete_cookie("bh_flash", path="/")
    return resp


def mit_flash(response: Any, text: str, art: str = "ok") -> Any:
    response.set_cookie(
        "bh_flash",
        _flash.dumps({"text": text, "art": art}),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=60,
        path="/",
    )
    return response
