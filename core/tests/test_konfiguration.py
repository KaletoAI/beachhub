from decimal import Decimal

from beachhub_core.models import Audit
from beachhub_core.services import konfiguration
from sqlalchemy.orm import Session


def test_default_wird_typisiert_geliefert(db: Session) -> None:
    assert konfiguration.hole(db, "storno_frist_stunden") == 24
    assert isinstance(konfiguration.hole(db, "storno_frist_stunden"), int)
    assert konfiguration.hole(db, "ust_satz") == Decimal("19.00")


def test_setzen_ueberschreibt_und_protokolliert(db: Session) -> None:
    konfiguration.setze(db, "storno_frist_stunden", 48)
    db.commit()
    assert konfiguration.hole(db, "storno_frist_stunden") == 48
    a = db.query(Audit).filter_by(objekt_typ="konfiguration").one()
    assert a.vorher_json == {"wert": "24"} and a.nachher_json["wert"] == "48"


def test_unbekannter_schluessel_wirft() -> None:
    import pytest

    with pytest.raises(KeyError):
        konfiguration.DEFAULTS["gibt_es_nicht"]
