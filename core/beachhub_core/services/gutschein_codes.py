"""Geheime Codes: 13 Zeichen aus 32 Symbolen ergeben 65 Bit Entropie."""

import secrets

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from beachhub_core.models import Gutschein

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
LAENGE = 13


class GutscheincodeFehler(Exception):  # noqa: N818
    grund = "gutschein_ungueltig"


def normalisiere(roh: str) -> str:
    # Ausschließlich die dokumentierten Trenner ignorieren. Auch leere Eingaben sind Fehler.
    code = "".join(c for c in roh if c != "-" and not c.isspace()).upper()
    if len(code) != LAENGE or not set(code) <= set(ALPHABET):
        raise GutscheincodeFehler("gutschein_ungueltig")
    return code


def anzeige(code: str) -> str:
    code = normalisiere(code)
    return f"{code[:4]}-{code[4:8]}-{code[8:]}"


def neuer_code(db: Session) -> str:
    """Liefert einen freien Kandidaten; erst der Insert reserviert ihn atomar.

    Schreibende Aufrufer verwenden fuege_mit_neuem_code_ein, damit auch eine
    konkurrierende Kollision nur den Insert-Savepoint zurückrollt.
    """
    while True:
        code = "".join(secrets.choice(ALPHABET) for _ in range(LAENGE))
        with db.no_autoflush:
            if db.scalar(select(Gutschein.id).where(Gutschein.code == code)) is None:
                return code


def fuege_mit_neuem_code_ein(db: Session, gutschein: Gutschein) -> Gutschein:
    """Fügt ein noch nicht hinzugefügtes Modell ein; hält äußere Vorgangslocks.

    Nur die Unique-Verletzung des Codes wird wiederholt, andere Persistenzfehler
    bleiben Fehler. Vorab entstandene Änderungen werden von begin_nested geflusht.
    """
    if gutschein in db:
        raise ValueError("Gutschein vor der Codevergabe noch nicht zur Session hinzufügen")
    while True:
        gutschein.code = neuer_code(db)
        try:
            with db.begin_nested():
                db.add(gutschein)
                db.flush()
            return gutschein
        except IntegrityError as exc:
            if (
                getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
                != "gutschein_code_key"
            ):
                raise
