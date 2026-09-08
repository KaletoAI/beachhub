from datetime import date, time
from decimal import Decimal

from beachhub_core.models import Buchung, Feld, Kunde, Kundengruppe
from beachhub_core.services import pin
from beachhub_shared.zeit import kombiniere
from sqlalchemy.orm import Session


def test_erzeugen_hash_verschluesseln() -> None:
    p = pin.erzeuge(6)
    assert len(p) == 6 and p.isdigit()
    assert pin.hash(p) == pin.hash(p)
    assert pin.hash(p) != pin.hash("000000")
    assert pin.entschluessele(pin.verschluessele(p)) == p


def test_finde_freien_vermeidet_kollision(db: Session, monkeypatch) -> None:
    g = Kundengruppe(name="Privat")
    f = Feld(name="F1", reihenfolge=1)
    db.add_all([g, f])
    db.flush()
    k = Kunde(name="A", email="a@x.de", kundengruppe_id=g.id, zahlungsart="online")
    db.add(k)
    db.flush()
    d = date(2027, 12, 1)
    db.add(
        Buchung(
            feld_id=f.id,
            kunde_id=k.id,
            beginn=kombiniere(d, time(19)),
            ende=kombiniere(d, time(20)),
            status="bestaetigt",
            preis=Decimal("30"),
            zahlungsart="online",
            pin_hash=pin.hash("123456"),
            quelle="admin",
        )
    )
    db.commit()
    folge = iter(["123456", "654321"])
    monkeypatch.setattr(pin, "erzeuge", lambda laenge: next(folge))
    assert pin.finde_freien(db, kombiniere(d, time(20)), kombiniere(d, time(21)), 6, 15) == "654321"
