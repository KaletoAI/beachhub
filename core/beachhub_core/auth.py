"""Admin-Anmeldung: Argon2id-Passwort + TOTP, serverseitige Sessions, CSRF, Rate-Limit."""

import hashlib
import secrets
import time as _time
from collections import defaultdict
from datetime import timedelta

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_core.config import settings
from beachhub_core.database import get_db
from beachhub_core.models import AdminSession, AdminUser, utcnow

COOKIE = "bh_session"
SESSION_DAUER = timedelta(hours=12)
_ph = PasswordHasher()
_versuche: dict[str, list[float]] = defaultdict(list)
RATE_MAX, RATE_FENSTER = 10, 15 * 60


def hash_passwort(klar: str) -> str:
    return _ph.hash(klar)


def pruefe_passwort(klar: str, gespeichert: str) -> bool:
    try:
        return _ph.verify(gespeichert, klar)
    except VerifyMismatchError:
        return False


def erzeuge_totp_secret() -> str:
    return pyotp.random_base32()


def pruefe_totp(secret: str, code: str) -> bool:
    return pyotp.TOTP(secret).verify(code.strip().replace(" ", ""), valid_window=1)


_DUMMY_HASH = hash_passwort("dummy-passwort-fuer-timing")  # noqa: S105 -- kein echtes Geheimnis


def pruefe_login(user: AdminUser | None, passwort: str, code: str) -> bool:
    """Läuft für existierende und nicht-existierende Namen gleich lang durch.

    Verhindert, dass Antwortzeiten verraten, ob ein Nutzername existiert.
    """
    pw_ok = pruefe_passwort(passwort, user.passwort_hash if user else _DUMMY_HASH)
    totp_ok = pruefe_totp(user.totp_secret if user else erzeuge_totp_secret(), code)
    return user is not None and pw_ok and totp_ok


def lege_admin_an(
    db: Session, *, name: str, passwort: str, rolle: str = "admin"
) -> tuple[AdminUser, str]:
    if len(passwort) < 12:
        raise ValueError("Passwort muss mindestens 12 Zeichen haben")
    secret = erzeuge_totp_secret()
    user = AdminUser(
        name=name.strip(), passwort_hash=hash_passwort(passwort), totp_secret=secret, rolle=rolle
    )
    db.add(user)
    db.flush()
    return user, secret


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def erzeuge_session(db: Session, admin: AdminUser) -> tuple[str, str]:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    db.add(
        AdminSession(
            token_hash=_token_hash(token),
            admin_user_id=admin.id,
            csrf_token=csrf,
            laeuft_ab=utcnow() + SESSION_DAUER,
        )
    )
    db.commit()
    return token, csrf


def lade_session(db: Session, token: str | None) -> AdminSession | None:
    if not token:
        return None
    s = db.scalar(select(AdminSession).where(AdminSession.token_hash == _token_hash(token)))
    if s is None or s.laeuft_ab < utcnow():
        return None
    return s


def beende_session(db: Session, token: str | None) -> None:
    s = lade_session(db, token)
    if s:
        db.delete(s)
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


def aktueller_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    s = lade_session(db, request.cookies.get(COOKIE))
    if s is None:
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
    user = db.get(AdminUser, s.admin_user_id)
    if user is None or not user.aktiv:
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
    request.state.csrf = s.csrf_token
    return user


def nur_admin_rolle(admin: AdminUser = Depends(aktueller_admin)) -> AdminUser:
    if admin.rolle != "admin":
        raise HTTPException(status_code=403, detail="Nur lesender Zugriff")
    return admin


async def verify_csrf(request: Request, db: Session = Depends(get_db)) -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    s = lade_session(db, request.cookies.get(COOKIE))
    if s is None:
        return  # Login-Formular ohne Session; dort greift das Rate-Limit
    form = await request.form()
    if not secrets.compare_digest(str(form.get("csrf_token", "")), s.csrf_token):
        raise HTTPException(status_code=403, detail="CSRF-Token ungültig")


def pruefe_rate_limit(request: Request, scope: str) -> None:
    ip = request.client.host if request.client else "?"
    key, jetzt = f"{scope}:{ip}", _time.monotonic()
    _versuche[key] = [t for t in _versuche[key] if jetzt - t < RATE_FENSTER]
    if len(_versuche[key]) >= RATE_MAX:
        raise HTTPException(status_code=429, detail="Zu viele Versuche")
    _versuche[key].append(jetzt)


def reset_rate_limits() -> None:
    _versuche.clear()
