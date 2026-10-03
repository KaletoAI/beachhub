from datetime import date, time
from decimal import Decimal

import pytest
from beachhub_core.models import Buchung, Feld, Kunde
from beachhub_core.services import kundengruppen
from beachhub_shared.zeit import kombiniere
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def _basis(db: Session) -> tuple[Feld, Kunde]:
    f = Feld(name="F1", reihenfolge=1)
    k = Kunde(name="A", email="a@x.de")
    db.add_all([f, k])
    db.flush()
    return f, k


def _buchung(
    db: Session, f: Feld, k: Kunde, von: int, bis: int, status: str = "bestaetigt"
) -> Buchung:
    d = date(2027, 12, 1)
    return Buchung(
        feld_id=f.id,
        kunde_id=k.id,
        beginn=kombiniere(d, time(von)),
        ende=kombiniere(d, time(bis)),
        status=status,
        preis=Decimal("30"),
        ust_satz=Decimal("19.00"),
        kundengruppe_id=kundengruppen.nicht_mitglied(db).id,
        zahlungsart="manuell",
        quelle="admin",
    )


def test_ueberlappung_wird_von_datenbank_abgelehnt(db: Session) -> None:
    f, k = _basis(db)
    db.add(_buchung(db, f, k, 19, 21))
    db.commit()
    db.add(_buchung(db, f, k, 20, 22))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_angrenzend_und_storniert_erlaubt(db: Session) -> None:
    f, k = _basis(db)
    db.add(_buchung(db, f, k, 19, 21))
    db.add(_buchung(db, f, k, 21, 23))
    db.add(_buchung(db, f, k, 20, 22, status="storniert"))
    db.commit()
    assert db.query(Buchung).count() == 3
