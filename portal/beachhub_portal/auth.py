"""Anmeldung per Link oder Code aus der Mail (Muster SportAbo-Manager), serverseitige
Sessions, CSRF-Token und Rate-Limits (Hauptspec § 10)."""

import hmac
import secrets
import time as _time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from beachhub_portal import uhr
from beachhub_portal.config import settings
from beachhub_portal.database import get_db
from beachhub_portal.models import Konto, LoginToken, Sitzung
from beachhub_portal.sicherheit import hash_token

COOKIE = "bp_session"
SESSION_DAUER = timedelta(days=30)
TOKEN_DAUER = timedelta(minutes=15)
MAX_CODE_FEHLVERSUCHE = 5
RATE_FENSTER_SEKUNDEN = 15 * 60
_versuche: dict[str, list[float]] = defaultdict(list)


def normalisiere_email(email: str) -> str:
    return email.strip().lower()


def fordere_an(db: Session, email: str, jetzt: datetime) -> tuple[str, str]:
    """Neuer Link und Code für eine Adresse; ältere Links und Codes werden ungültig."""
    db.execute(delete(LoginToken).where(LoginToken.email == email))
    token = secrets.token_urlsafe(32)
    code = f"{secrets.randbelow(10**6):06d}"
    db.add(
        LoginToken(
            email=email,
            token_hash=hash_token(token),
            code_hash=hash_token(code),
            laeuft_ab=jetzt + TOKEN_DAUER,
        )
    )
    db.commit()
    return token, code


def pruefe_code(db: Session, email: str, code: str, jetzt: datetime) -> bool:
    offen = db.scalars(
        select(LoginToken).where(
            LoginToken.email == email,
            LoginToken.verwendet_am.is_(None),
            LoginToken.laeuft_ab > jetzt,
        )
    ).all()
    gesucht = hash_token(code.strip())
    treffer = next((t for t in offen if hmac.compare_digest(t.code_hash, gesucht)), None)
    if treffer is None:
        for t in offen:
            t.fehlversuche += 1
            if t.fehlversuche >= MAX_CODE_FEHLVERSUCHE:
                t.verwendet_am = jetzt  # verbraucht: auch der Link gilt nicht mehr
        db.commit()
        return False
    treffer.verwendet_am = jetzt
    db.commit()
    return True


def email_zum_link(db: Session, token: str, jetzt: datetime) -> str | None:
    """Nur lesen, nichts verbrauchen: Mail-Scanner öffnen Links per GET (Review Focus 3)."""
    t = db.scalar(select(LoginToken).where(LoginToken.token_hash == hash_token(token)))
    if t is None or t.verwendet_am is not None or t.laeuft_ab <= jetzt:
        return None
    return t.email


def loese_link_ein(db: Session, token: str, jetzt: datetime) -> str | None:
    t = db.scalar(
        select(LoginToken).where(LoginToken.token_hash == hash_token(token)).with_for_update()
    )
    if t is None or t.verwendet_am is not None or t.laeuft_ab <= jetzt:
        db.rollback()
        return None
    t.verwendet_am = jetzt
    db.commit()
    return t.email


def melde_an(db: Session, email: str, jetzt: datetime) -> tuple[Konto, str]:
    """Legt das Konto mit leerem Anzeigenamen an, falls nötig (A-7), und öffnet eine Session."""
    konto = db.scalar(select(Konto).where(Konto.email == email))
    if konto is None:
        konto = Konto(id=uuid.uuid4(), email=email, anzeigename="", erstellt_am=jetzt)
        db.add(konto)
        db.flush()
    token = secrets.token_urlsafe(32)
    db.add(
        Sitzung(
            konto_id=konto.id,
            token_hash=hash_token(token),
            csrf_token=secrets.token_urlsafe(32),
            laeuft_ab=jetzt + SESSION_DAUER,
        )
    )
    db.commit()
    return konto, token


def lade_sitzung(db: Session, token: str | None, jetzt: datetime) -> Sitzung | None:
    if not token:
        return None
    s = db.scalar(select(Sitzung).where(Sitzung.token_hash == hash_token(token)))
    if s is None or s.laeuft_ab <= jetzt:
        return None
    if s.laeuft_ab - jetzt < SESSION_DAUER - timedelta(days=1):
        s.laeuft_ab = jetzt + SESSION_DAUER  # gleitend, höchstens einmal am Tag geschrieben
        db.commit()
    return s


def beende(db: Session, token: str | None) -> None:
    if token:
        db.execute(delete(Sitzung).where(Sitzung.token_hash == hash_token(token)))
        db.commit()


def setze_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=int(SESSION_DAUER.total_seconds()),
        path="/",
    )


def loesche_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")


def aktuelles_konto(request: Request, db: Session) -> Konto | None:
    s = lade_sitzung(db, request.cookies.get(COOKIE), uhr.jetzt())
    if s is None:
        return None
    request.state.csrf = s.csrf_token
    return db.get(Konto, s.konto_id)


def konto_optional(request: Request, db: Session = Depends(get_db)) -> Konto | None:
    return aktuelles_konto(request, db)


def konto_pflicht(request: Request, db: Session = Depends(get_db)) -> Konto:
    konto = aktuelles_konto(request, db)
    if konto is None:
        raise HTTPException(status_code=303, headers={"Location": "/anmelden"})
    if not konto.anzeigename and request.url.path != "/willkommen":
        raise HTTPException(status_code=303, headers={"Location": "/willkommen"})
    return konto


async def verify_csrf(request: Request, db: Session = Depends(get_db)) -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    s = lade_sitzung(db, request.cookies.get(COOKIE), uhr.jetzt())
    if s is None:
        return  # Anmeldeformulare ohne Session; dort greift das Rate-Limit
    form = await request.form()
    if not secrets.compare_digest(str(form.get("csrf_token", "")), s.csrf_token):
        raise HTTPException(
            status_code=403,
            detail="Die Seite ist veraltet. Bitte lade sie neu und versuche es noch einmal.",
        )


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def pruefe_rate_limit(schluessel: str, maximum: int) -> None:
    jetzt = _time.monotonic()
    _versuche[schluessel] = [t for t in _versuche[schluessel] if jetzt - t < RATE_FENSTER_SEKUNDEN]
    if len(_versuche[schluessel]) >= maximum:
        raise HTTPException(
            status_code=429, detail="Zu viele Versuche. Bitte in einigen Minuten erneut versuchen."
        )
    _versuche[schluessel].append(jetzt)


def reset_rate_limits() -> None:
    _versuche.clear()
