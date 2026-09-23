"""Konto löschen: alles, was das Portal zu einem Konto hält (Hauptspec § 10, Löschkonzept)."""

from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from beachhub_portal.models import Konto, LoginToken, RechnungLink
from beachhub_portal.services import lesestand


def loesche(db: Session, konto: Konto) -> None:
    for link in db.scalars(select(RechnungLink).where(RechnungLink.konto_id == konto.id)):
        Path(link.pdf_pfad).unlink(missing_ok=True)
    if konto.kunde_id is not None:
        lesestand.loesche(db, f"konto:{konto.kunde_id}")
    db.execute(delete(LoginToken).where(LoginToken.email == konto.email))
    db.delete(konto)  # Sitzungen und Rechnungslinks per ON DELETE CASCADE
    db.commit()
