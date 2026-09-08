"""PIN je Buchung: Klartext verschlüsselt (für Mails), Hash für die Halle."""

import base64
import hashlib
import secrets
from datetime import datetime, timedelta

from argon2.low_level import Type, hash_secret_raw
from cryptography.fernet import Fernet
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from beachhub_core.config import settings
from beachhub_core.models import Buchung, Dauerbuchung


def _schluessel() -> bytes:
    return hashlib.sha256(settings.pin_schluessel.encode()).digest()


def erzeuge(laenge: int) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(laenge))


def hash(klar: str) -> str:  # noqa: A001 – bewusst so benannt, wird als pin.hash() gelesen
    """Argon2id mit hallenweitem Salt: deterministisch, damit die Halle lokal prüfen kann."""
    salt = hashlib.sha256(b"beachhub-pin-salt" + _schluessel()).digest()[:16]
    raw = hash_secret_raw(
        klar.encode(),
        salt,
        time_cost=2,
        memory_cost=65536,
        parallelism=1,
        hash_len=32,
        type=Type.ID,
    )
    return "argon2id$" + base64.b64encode(raw).decode()


def _fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(_schluessel()))


def verschluessele(klar: str) -> str:
    return _fernet().encrypt(klar.encode()).decode()


def entschluessele(chiffre: str) -> str:
    return _fernet().decrypt(chiffre.encode()).decode()


def finde_freien(
    db: Session, beginn: datetime, ende: datetime, laenge: int, vorlauf_minuten: int
) -> str:
    """PIN, der in keiner aktiven Buchung mit überlappendem Fenster (± Vorlauf) vorkommt."""
    von, bis = (
        beginn - timedelta(minutes=vorlauf_minuten),
        ende + timedelta(minutes=vorlauf_minuten),
    )
    belegt = set(
        db.scalars(
            select(Buchung.pin_hash).where(
                Buchung.status.in_(Buchung.AKTIVE_STATUS),
                Buchung.beginn < bis,
                Buchung.ende > von,
                Buchung.pin_hash.is_not(None),
            )
        ).all()
    )
    belegt |= set(
        db.scalars(
            select(Dauerbuchung.pin_hash).where(
                or_(Dauerbuchung.beendet_am.is_(None), Dauerbuchung.beendet_am > beginn)
            )
        ).all()
    )
    for _ in range(100):
        kandidat = erzeuge(laenge)
        if hash(kandidat) not in belegt:
            return kandidat
    raise RuntimeError("Kein freier PIN gefunden")
