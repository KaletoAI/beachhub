"""Anmeldung per Link oder Code aus der Mail (Muster SportAbo-Manager), serverseitige
Sessions, CSRF-Token und Rate-Limits (Hauptspec § 10)."""

import secrets
import threading
import time as _time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import Depends, Form, HTTPException, Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from beachhub_portal import uhr
from beachhub_portal.config import settings
from beachhub_portal.database import get_db
from beachhub_portal.models import CodeFehlversuch, Konto, LoginToken, Sitzung
from beachhub_portal.sicherheit import hash_code, hash_token

COOKIE = "bp_session"
SESSION_DAUER = timedelta(days=30)
TOKEN_DAUER = timedelta(minutes=15)
MAX_CODE_FEHLVERSUCHE = 5
RATE_FENSTER_SEKUNDEN = 15 * 60
# Ruling Fix-Runde 1 (Item 4): 20 Fehlversuche je Adresse in 24 h, unabhängig von einzelnen
# Tokens – die bei jeder neuen Anforderung verworfen werden und den Zähler sonst mitnehmen.
CODE_SPERRE_FENSTER = timedelta(hours=24)
CODE_SPERRE_MAXIMUM = 20
# Ruling Fix-Runde 1 (Item 2): Double-Submit-CSRF-Cookie für Formulare ohne Sitzung (Anmelden).
VOR_CSRF_COOKIE = "bp_vor_csrf"
VOR_CSRF_DAUER = timedelta(hours=1)

_versuche: dict[str, list[float]] = defaultdict(list)
_versuche_sperre = threading.Lock()  # Ruling Fix-Runde 1 (Item 1): verlorene Updates verhindern
_vor_csrf_signer = URLSafeTimedSerializer(settings.secret_key, salt="vor-csrf")


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
            code_hash=hash_code(code, settings.secret_key),
            laeuft_ab=jetzt + TOKEN_DAUER,
        )
    )
    db.commit()
    return token, code


def _code_fehlversuche_24h(db: Session, email: str, jetzt: datetime) -> int:
    n = db.scalar(
        select(func.count(CodeFehlversuch.id)).where(
            CodeFehlversuch.email == email,
            CodeFehlversuch.versucht_am > jetzt - CODE_SPERRE_FENSTER,
        )
    )
    return int(n or 0)


def code_gesperrt(db: Session, email: str, jetzt: datetime) -> bool:
    """Obergrenze 20 Code-Fehlversuche je Adresse in 24 Stunden (Ruling Fix-Runde 1, Item 4):
    dauerhaft in der DB und unabhängig von einzelnen Tokens. Der Anmeldelink bleibt davon
    unberührt – nur die Codeeingabe wird für die Adresse vorübergehend gesperrt."""
    return _code_fehlversuche_24h(db, email, jetzt) >= CODE_SPERRE_MAXIMUM


def pruefe_code(db: Session, email: str, code: str, jetzt: datetime) -> bool:
    if code_gesperrt(db, email, jetzt):
        return False
    # Ruling Fix-Runde 1 (Item 1): Zeilen sperren, sonst lesen parallele Fehlversuche denselben
    # alten Stand und überschreiben sich gegenseitig – das Fünf-Versuche-Limit wäre umgehbar.
    offen = db.scalars(
        select(LoginToken)
        .where(
            LoginToken.email == email,
            LoginToken.verwendet_am.is_(None),
            LoginToken.laeuft_ab > jetzt,
        )
        .with_for_update()
    ).all()
    gesucht = hash_code(code.strip(), settings.secret_key)
    treffer = next((t for t in offen if secrets.compare_digest(t.code_hash, gesucht)), None)
    if treffer is None:
        db.add(CodeFehlversuch(email=email, versucht_am=jetzt))
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


def lade_sitzung(
    db: Session, token: str | None, jetzt: datetime, request: Request | None = None
) -> Sitzung | None:
    """`request`, falls übergeben: Verlängert `lade_sitzung` die Sitzung, wird das an
    `request.state` vermerkt, damit `SessionCookieMiddleware` das Cookie mit neuem `max_age`
    erneut setzt (Ruling Fix-Runde 1, Item 3) – die DB verlängert sonst gleitend, das Cookie im
    Browser verfällt aber weiter fest nach 30 Tagen ab dem ersten Login."""
    if not token:
        return None
    s = db.scalar(select(Sitzung).where(Sitzung.token_hash == hash_token(token)))
    if s is None or s.laeuft_ab <= jetzt:
        return None
    if s.laeuft_ab - jetzt < SESSION_DAUER - timedelta(days=1):
        s.laeuft_ab = jetzt + SESSION_DAUER  # gleitend, höchstens einmal am Tag geschrieben
        db.commit()
        if request is not None:
            request.state.sitzung_verlaengert = token
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
    s = lade_sitzung(db, request.cookies.get(COOKIE), uhr.jetzt(), request)
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


def vor_csrf_token(request: Request) -> tuple[str, str | None]:
    """CSRF-Token für Formulare ohne Sitzung (Double-Submit-Cookie, Ruling Fix-Runde 1, Item 2):
    aus dem signierten Cookie lesen oder neu erzeugen. Gibt (Token, neuer Cookie-Rohwert) zurück;
    Zweites ist nur gesetzt, wenn ein neues Cookie geschrieben werden muss."""
    roh = request.cookies.get(VOR_CSRF_COOKIE)
    if roh:
        try:
            wert: str = _vor_csrf_signer.loads(roh, max_age=int(VOR_CSRF_DAUER.total_seconds()))
            return wert, None
        except BadSignature:
            pass
    wert = secrets.token_urlsafe(32)
    return wert, wert


def setze_vor_csrf_cookie(response: Response, wert: str) -> None:
    response.set_cookie(
        VOR_CSRF_COOKIE,
        _vor_csrf_signer.dumps(wert),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=int(VOR_CSRF_DAUER.total_seconds()),
        path="/",
    )


def loesche_vor_csrf_cookie(response: Response) -> None:
    """Ruling Fix-Runde 2 (Item 4): nach erfolgreichem Login wird das Vor-Session-Cookie nicht
    mehr gebraucht (es gilt nur für Formulare ohne Sitzung) und wird entfernt."""
    response.delete_cookie(VOR_CSRF_COOKIE, path="/")


def pruefe_vor_csrf(request: Request, eingereicht: str) -> bool:
    roh = request.cookies.get(VOR_CSRF_COOKIE)
    if not roh:
        return False
    try:
        erwartet: str = _vor_csrf_signer.loads(roh, max_age=int(VOR_CSRF_DAUER.total_seconds()))
    except BadSignature:
        return False
    # Ruling Fix-Runde 1 (Item 9): auf bytes vergleichen – compare_digest lehnt zwei Strings mit
    # Nicht-ASCII-Zeichen mit TypeError ab (500 statt 403 bei einem manipulierten Formularfeld).
    return secrets.compare_digest(erwartet.encode(), eingereicht.encode())


def verify_csrf(
    request: Request, csrf_token: str | None = Form(default=None), db: Session = Depends(get_db)
) -> None:
    """Läuft für jede Route der geschützten Router (main.py), auch für GET: setzt
    `request.state.csrf` aus einer bestehenden Sitzung, egal welche Methode – so tragen auch
    reine Lese-Seiten (z. B. der Anmeldelink) immer das echte Token, statt es über einen
    Umweg-Parameter je Route nachzurüsten (Ruling Fix-Runde 1, Item 2).

    Bei änderenden Methoden ohne Sitzung wird nicht mehr stillschweigend durchgelassen: Das
    ermöglichte bisher Login-CSRF (eine fremde Seite loggt ein abgemeldetes Opfer unbemerkt in
    das Konto des Angreifers ein). Ohne Sitzung gilt stattdessen das Double-Submit-Vor-Session-
    Cookie, das dieselbe render()-Funktion für jedes anonyme Formular ausstellt.

    Bewusst `def` statt `async def` (Ruling Fix-Runde 2, Item 1): Die Funktion macht blockierende
    DB-Arbeit (SQLAlchemy, synchroner psycopg-Treiber) für jede Anfrage beider Router, auch GET –
    als async-Funktion liefe das auf dem Event-Loop und blockierte ihn, den auch der Long-Poll
    `/core/anfragen` nutzt. FastAPI führt eine synchrone Abhängigkeit stattdessen im Threadpool
    aus; `csrf_token` kommt über `Form(...)`, damit kein eigenes `await request.form()` nötig
    ist (für GET ohne Formular-Body liefert das schlicht `None`)."""
    s = lade_sitzung(db, request.cookies.get(COOKIE), uhr.jetzt(), request)
    if s is not None:
        request.state.csrf = s.csrf_token
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    eingereicht = csrf_token or ""
    if s is not None:
        gueltig = secrets.compare_digest(eingereicht.encode(), s.csrf_token.encode())
    else:
        gueltig = pruefe_vor_csrf(request, eingereicht)
    if not gueltig:
        raise HTTPException(
            status_code=403,
            detail="Die Seite ist veraltet. Bitte lade sie neu und versuche es noch einmal.",
        )


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def pruefe_rate_limit(schluessel: str, maximum: int) -> None:
    jetzt = _time.monotonic()
    with _versuche_sperre:
        rest = [t for t in _versuche[schluessel] if jetzt - t < RATE_FENSTER_SEKUNDEN]
        if len(rest) >= maximum:
            _versuche[schluessel] = rest
            raise HTTPException(
                status_code=429,
                detail="Zu viele Versuche. Bitte in einigen Minuten erneut versuchen.",
            )
        rest.append(jetzt)
        _versuche[schluessel] = rest


def reset_rate_limits() -> None:
    with _versuche_sperre:
        _versuche.clear()
