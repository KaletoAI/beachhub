"""Einmal-Links für Rechnungs-PDFs (A-RECH-5): Das Portal speichert keine Rechnungen, nur die
angeforderte Kopie für höchstens zehn Minuten; nach dem Abruf ist sie weg."""

import os
import secrets
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_portal.config import settings
from beachhub_portal.models import RechnungLink
from beachhub_portal.sicherheit import hash_token

DAUER = timedelta(minutes=10)
# Controller-Ruling Task 15: Größenlimit für angeforderte Rechnungs-PDFs; ein größerer Wert
# deutet auf ein defektes oder manipuliertes Dokument im Hauptsystem hin und soll die
# Verarbeitung nicht mit einem 500 abbrechen lassen (siehe services/anfragen.beantworte).
MAX_PDF_BYTES = 10 * 1024 * 1024


def lege_an(
    db: Session, *, konto_id: uuid.UUID, rechnung_nr: str, pdf: bytes, jetzt: datetime
) -> str:
    """Schreibt das PDF unter einem zufälligen Dateinamen (kein Nutzerinput im Pfad) nach
    `DATA_DIR/rechnungen_tmp` mit den Rechten 0600 (Ordner 0700) und legt die Link-Zeile an.
    Committet nicht selbst – der Aufrufer (`anfragen.beantworte`, Tests) entscheidet über die
    Transaktion. Liefert das Klartext-Token; gespeichert wird nur dessen Hash."""
    ordner = settings.data_dir / "rechnungen_tmp"
    ordner.mkdir(parents=True, exist_ok=True)
    os.chmod(ordner, 0o700)
    pfad = ordner / f"{uuid.uuid4()}.pdf"
    fd = os.open(pfad, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pdf)
    token = secrets.token_urlsafe(32)
    db.add(
        RechnungLink(
            konto_id=konto_id,
            rechnung_nr=rechnung_nr,
            token_hash=hash_token(token),
            pdf_pfad=str(pfad),
            laeuft_ab=jetzt + DAUER,
        )
    )
    return token


def einloesen(
    db: Session, *, token: str, konto_id: uuid.UUID, jetzt: datetime
) -> tuple[str, bytes] | None:
    """Liefert das PDF genau einmal aus: Die Zeile wird gesperrt (`with_for_update`), solange
    gelesen und danach sofort gelöscht wird – ein gleichzeitiger zweiter Abruf wartet auf die
    Sperre und findet die Zeile danach nicht mehr (Controller-Ruling: nur einer bekommt das
    PDF). Nur für das eigene Konto, nicht abgelaufen; löscht Datei und Link und committet."""
    link = db.scalar(
        select(RechnungLink).where(RechnungLink.token_hash == hash_token(token)).with_for_update()
    )
    if link is None or link.konto_id != konto_id or link.laeuft_ab <= jetzt:
        db.rollback()
        return None
    pfad = link.pdf_pfad
    nummer = link.rechnung_nr
    try:
        with open(pfad, "rb") as f:
            daten = f.read()
    except OSError:
        daten = None
    if os.path.exists(pfad):
        os.unlink(pfad)
    db.delete(link)
    db.commit()
    return (nummer, daten) if daten is not None else None
