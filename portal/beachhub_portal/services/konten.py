"""Konto löschen: alles, was das Portal zu einem Konto hält (Hauptspec § 10, Löschkonzept)."""

from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from beachhub_portal.models import Konto, LoginToken, RechnungLink
from beachhub_portal.services import lesestand, wecker


def loesche(db: Session, konto: Konto) -> None:
    """Löscht Konto, Sessions (CASCADE), Rechnungslinks (CASCADE), Login-Tokens und den
    Lesestand. Eine Transaktion (Ruling Fix-Runde 1, Item 10): Die Rechnungs-PDFs werden erst
    von der Platte gelöscht, nachdem die DB-Änderungen committet sind – schlägt der Commit fehl,
    bleiben die Dateien erhalten und passen weiter zu den (dann nicht gelöschten) DB-Zeilen."""
    pfade = [
        link.pdf_pfad
        for link in db.scalars(select(RechnungLink).where(RechnungLink.konto_id == konto.id))
    ]
    if konto.kunde_id is not None:
        lesestand.loesche(db, f"konto:{konto.kunde_id}")
    db.execute(delete(LoginToken).where(LoginToken.email == konto.email))
    db.delete(konto)  # Sitzungen und Rechnungslinks per ON DELETE CASCADE
    db.commit()
    wecker.wecke()  # falls der Aufrufer zuvor eine Anfrage mit commit=False vorgemerkt hat
    for pfad in pfade:
        Path(pfad).unlink(missing_ok=True)
