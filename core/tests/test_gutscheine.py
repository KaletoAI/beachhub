"""Persistenzverträge für Kauf-Snapshots und Einlösungsbelegbasis."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def test_freicode_hat_keine_steuer_und_keinen_kaufbeleg(db: Session) -> None:
    from beachhub_core.models import Gutschein

    g = Gutschein(
        code="FREI-TEST", art="frei", modell="einheit", steuer="keine", minuten=120, status="aktiv"
    )
    db.add(g)
    db.commit()
    db.refresh(g)
    assert g.steuer == "keine" and g.rechnung_id is None and g.kaufpreis_brutto is None


def test_gutscheinmodell_verfuegbar() -> None:
    from beachhub_core.models import Gutschein, GutscheinEinloesung, GutscheinFehlversuch

    assert Gutschein.__tablename__ == "gutschein"
    assert GutscheinEinloesung.__tablename__ == "gutschein_einloesung"
    assert GutscheinFehlversuch.__tablename__ == "gutschein_fehlversuch"


def test_offener_kauf_hat_noch_keine_gueltigkeit(db: Session, gwelt) -> None:
    from hilfen_gutschein import kaufgutschein

    g = kaufgutschein(db, gwelt, status="offen", gueltig_bis=None, kaufdatum=None)
    db.commit()
    db.refresh(g)
    assert g.gueltig_bis is None and g.kauf_reserviert_bis is not None
    assert (g.kaufpreis_brutto, g.gueltig_tage_snapshot) == (Decimal("26.00"), 730)


@pytest.mark.parametrize(
    "aenderungen",
    [
        {"status": "unbekannt"},
        {"art": "bad"},
        {"modell": "bad"},
        {"steuer": "unbekannt"},
        {"steuer": "keine"},
        {"kaufpreis_brutto": Decimal("-0.01")},
        {"kaufpreis_brutto": Decimal("NaN")},
        {"restwert": Decimal("-1")},
        {"restwert": Decimal("51"), "nennwert_brutto": Decimal("50")},
        {"max_einloesungen": 0},
        {"minuten": 0},
        {"gueltig_tage_snapshot": 0},
        {"gueltig_bis": None},
        {"kaufdatum": None},
        {"zeitraum_von": date(2027, 12, 2), "zeitraum_bis": date(2027, 12, 1)},
        {"rueckgabe_betrag": Decimal("-1")},
    ],
)
def test_gutschein_db_schuetzt_zustaende_und_geld(db: Session, gwelt, aenderungen) -> None:
    from hilfen_gutschein import kaufgutschein

    g = kaufgutschein(db, gwelt)
    for name, wert in aenderungen.items():
        setattr(g, name, wert)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


@pytest.mark.parametrize(
    "aenderungen",
    [
        {"status": "unbekannt"},
        {"betrag": Decimal("-0.01")},
        {"betrag": Decimal("NaN")},
        {"beleg_netto": Decimal("24")},
        {"beleg_ust": None},
        {"rueckabwicklung_art": "unbekannt"},
        {"beleg_brutto": Decimal("-1")},
    ],
)
def test_einloesung_db_schuetzt_belegbasis(db: Session, gwelt, aenderungen) -> None:
    from hilfen_gutschein import einloesung, kaufgutschein

    g = kaufgutschein(db, gwelt)
    e = einloesung(db, gwelt, g)
    for name, wert in aenderungen.items():
        setattr(e, name, wert)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_einloesung_belegbasis_und_snapshots_bleiben_erhalten(db: Session, gwelt) -> None:
    from hilfen_gutschein import einloesung, kaufgutschein

    g = kaufgutschein(db, gwelt)
    e = einloesung(db, gwelt, g)
    db.commit()
    db.refresh(e)
    assert e.status == "reserviert"
    assert (e.betrag, e.beleg_brutto, e.beleg_netto, e.beleg_ust) == (
        Decimal("30.00"),
        Decimal("26.00"),
        Decimal("24.30"),
        Decimal("1.70"),
    )
    assert g.kundengruppe_id == gwelt.mitglied_gruppe.id
