from decimal import Decimal

import pytest
from beachhub_core.models import Audit
from beachhub_core.services import konfiguration
from sqlalchemy.orm import Session


def test_default_wird_typisiert_geliefert(db: Session) -> None:
    assert konfiguration.hole(db, "storno_frist_stunden") == 24
    assert isinstance(konfiguration.hole(db, "storno_frist_stunden"), int)
    assert konfiguration.hole(db, "spiel_temperatur") == Decimal("18.0")


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


def test_dezimalwert_nimmt_komma_und_punkt(db: Session) -> None:
    """Die Konfigurationsseite zeigt Dezimalzahlen deutsch mit Komma; sie muss sie deshalb
    auch so wieder entgegennehmen."""
    konfiguration.setze(db, "spiel_temperatur", "7,5")
    db.commit()
    assert konfiguration.hole(db, "spiel_temperatur") == Decimal("7.5")
    konfiguration.setze(db, "spiel_temperatur", "19.00")
    db.commit()
    assert konfiguration.hole(db, "spiel_temperatur") == Decimal("19.00")


def test_speichern_ohne_aenderung_schreibt_nichts(db: Session) -> None:
    konfiguration.setze(db, "storno_frist_stunden", 24)  # entspricht der Vorgabe
    konfiguration.setze(db, "storno_frist_stunden", 48)
    konfiguration.setze(db, "storno_frist_stunden", "48")
    db.commit()
    assert db.query(Audit).filter_by(objekt_typ="konfiguration").count() == 1


def test_event_ust_satz_hat_vorgabe_19(db: Session) -> None:
    assert konfiguration.hole(db, "event_ust_satz") == Decimal("19.00")
    assert konfiguration.BESCHREIBUNGEN["event_ust_satz"].gruppe == "Zahlung und Rechnung"


@pytest.mark.parametrize(
    "schluessel,roh,meldung",
    [("storno_frist_stunden", "abc", "ganze Zahl"), ("event_ust_satz", "x", "Zahl angeben")],
)
def test_ungueltige_werte_mit_verstaendlicher_meldung(
    db: Session, schluessel: str, roh: str, meldung: str
) -> None:
    with pytest.raises(ValueError, match=meldung):
        konfiguration.setze(db, schluessel, roh)
