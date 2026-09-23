import re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from beachhub_portal import auth, mail, uhr
from beachhub_portal.config import settings
from beachhub_portal.database import get_db
from beachhub_portal.models import Konto
from beachhub_portal.templating import mit_flash, render, templates

router = APIRouter()

NEUTRAL = (
    "Wenn die Adresse stimmt, ist eine Mail mit Anmeldelink und Code unterwegs. "
    "Bitte schau in dein Postfach."
)
FALSCHER_CODE = (
    "Der Code ist ungültig oder abgelaufen. "
    "Nach mehreren Fehlversuchen bitte einen neuen anfordern."
)
CODE_GESPERRT = "Bitte melde dich über den Link in der Mail an."
UNGUELTIGER_LINK = (
    "Der Anmeldelink ist ungültig, abgelaufen oder wurde schon benutzt. "
    "Bitte fordere einen neuen an."
)
UNGUELTIGE_ADRESSE = "Bitte gib eine gültige E-Mail-Adresse an."
# Ruling Fix-Runde 1 (Item 5): genau eine Adresse, kein Leerraum/Steuerzeichen (auch nicht
# CR/LF), kein „,“/„<>“ – sonst nie ein 500, sondern immer die neutrale 400-Antwort.
EMAIL_MUSTER = re.compile(
    r"^[^\s,<>\x00-\x1f\x7f@]+@[^\s,<>\x00-\x1f\x7f@]+\.[^\s,<>\x00-\x1f\x7f@]+$"
)


@router.get("/anmelden", response_class=HTMLResponse)
def anmelden_seite(
    request: Request, konto: Konto | None = Depends(auth.konto_optional)
) -> Response:
    if konto is not None:
        return RedirectResponse("/", status_code=303)
    return render(request, "anmelden.html")


@router.post("/anmelden", response_class=HTMLResponse)
def anmelden(
    request: Request,
    email: str = Form(..., max_length=200),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    auth.pruefe_rate_limit(f"anfordern-ip:{auth.client_ip(request)}", 5)
    adresse = auth.normalisiere_email(email)
    if not EMAIL_MUSTER.match(adresse):
        return render(request, "anmelden.html", status_code=400, fehler=UNGUELTIGE_ADRESSE)
    auth.pruefe_rate_limit(f"anfordern-mail:{adresse}", 3)
    token, code = auth.fordere_an(db, adresse, uhr.jetzt())
    link = f"{settings.base_url.rstrip('/')}/anmelden/link/{token}"
    # Ruling Fix-Runde 1 (Item 7): Nur im Entwicklungsmodus, nicht bei jeder Nicht-Produktion
    # (z. B. Staging) ohne konfiguriertes SMTP.
    zeigen = not settings.smtp_host and settings.app_env == "dev"
    resp = render(
        request,
        "anmelden.html",
        meldung=NEUTRAL,
        code_email=adresse,
        dev_link=link if zeigen else None,
        dev_code=code if zeigen else None,
    )
    text = templates.env.get_template("mail/login.txt").render(
        link=link, code=code, betreiber=settings.betreiber_name
    )
    # Erst nach der Antwort senden: gleiche Antwortzeit für bekannte und unbekannte Adressen.
    resp.background = BackgroundTask(
        mail.sende, adresse, f"Dein Anmeldelink – {settings.betreiber_name}", text
    )
    return resp


def _angemeldet(db: Session, request: Request, adresse: str) -> RedirectResponse:
    # Ruling Fix-Runde 1 (Item 8): eine im Browser noch bestehende Sitzung zuerst beenden, sonst
    # bleiben nach einem Kontowechsel mehrere Sitzungen parallel gültig.
    auth.beende(db, request.cookies.get(auth.COOKIE))
    konto, token = auth.melde_an(db, adresse, uhr.jetzt())
    resp = RedirectResponse("/" if konto.anzeigename else "/willkommen", status_code=303)
    auth.setze_cookie(resp, token)
    return resp


@router.post("/anmelden/code", response_model=None)
def anmelden_mit_code(
    request: Request,
    email: str = Form(..., max_length=200),
    code: str = Form(..., max_length=12),
    db: Session = Depends(get_db),
) -> Response:
    auth.pruefe_rate_limit(f"code-ip:{auth.client_ip(request)}", 10)
    adresse = auth.normalisiere_email(email)
    jetzt = uhr.jetzt()
    if auth.code_gesperrt(db, adresse, jetzt):
        return render(
            request, "anmelden.html", status_code=401, fehler=CODE_GESPERRT, code_email=adresse
        )
    if not auth.pruefe_code(db, adresse, code, jetzt):
        return render(
            request, "anmelden.html", status_code=401, fehler=FALSCHER_CODE, code_email=adresse
        )
    return _angemeldet(db, request, adresse)


@router.get("/anmelden/link/{token}", response_class=HTMLResponse)
def link_seite(request: Request, token: str, db: Session = Depends(get_db)) -> HTMLResponse:
    """Nur ein Knopf: Mail-Scanner öffnen Links per GET und würden ihn sonst verbrauchen."""
    if auth.email_zum_link(db, token, uhr.jetzt()) is None:
        return render(request, "anmelden.html", status_code=400, fehler=UNGUELTIGER_LINK)
    return render(request, "anmelden_link.html", token=token)


@router.post("/anmelden/link/{token}", response_model=None)
def link_einloesen(request: Request, token: str, db: Session = Depends(get_db)) -> Response:
    adresse = auth.loese_link_ein(db, token, uhr.jetzt())
    if adresse is None:
        return render(request, "anmelden.html", status_code=400, fehler=UNGUELTIGER_LINK)
    return _angemeldet(db, request, adresse)


@router.post("/abmelden")
def abmelden(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    auth.beende(db, request.cookies.get(auth.COOKIE))
    resp = RedirectResponse("/", status_code=303)
    auth.loesche_cookie(resp)
    return mit_flash(resp, "Du bist abgemeldet.")
