"""Persistenzverträge für Kauf-Snapshots und Einlösungsbelegbasis."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def test_codes_normalisierung_anzeige_und_entropie(db: Session) -> None:
    from math import log2

    from beachhub_core.services import gutschein_codes as codes

    assert codes.normalisiere(" abcd-efgh jklmn ") == "ABCDEFGHJKLMN"
    assert codes.anzeige("ABCDEFGHJKLMN") == "ABCD-EFGH-JKLMN"
    assert 13 * log2(len(codes.ALPHABET)) >= 60
    assert set("0O1I").isdisjoint(codes.ALPHABET)
    neu = codes.neuer_code(db)
    assert len(neu) == 13 and set(neu) <= set(codes.ALPHABET)
    for roh in ("---", "OOOOOOOOOOOOO", "ABCD", "é" * 13):
        with pytest.raises(codes.GutscheincodeFehler):
            codes.normalisiere(roh)


def test_codekollision_beim_insert_behaelt_aussentransaktion(db: Session, monkeypatch) -> None:
    from beachhub_core.models import Gutschein, Kunde
    from beachhub_core.services import gutschein_codes as codes

    erster = Gutschein(
        code="A" * 13, art="frei", modell="einheit", steuer="keine", minuten=60, status="aktiv"
    )
    db.add(erster)
    db.commit()
    kunde = Kunde(name="Außentransaktion", email="aussen@example.org")
    db.add(kunde)
    db.flush()
    folge = iter(["A" * 13, "B" * 13])
    monkeypatch.setattr(codes, "neuer_code", lambda db: next(folge))
    zweiter = Gutschein(art="frei", modell="einheit", steuer="keine", minuten=60, status="aktiv")
    codes.fuege_mit_neuem_code_ein(db, zweiter)
    db.commit()
    assert zweiter.code == "B" * 13
    assert db.get(Kunde, kunde.id) is not None


def test_savepoint_ablehnung_zaehlt_fehlversuch(db: Session, gwelt) -> None:
    import uuid
    from datetime import timedelta

    from beachhub_core import clock
    from beachhub_core.models import GutscheinFehlversuch
    from beachhub_core.services import gutschein_sperren as sperren

    sperren.bereite_vorgang_vor(db, kunde_ids=[gwelt.mitglied.id], gutschein_ids=[])
    for _ in range(10):
        anfrage_id = uuid.uuid4()
        with pytest.raises(ValueError), db.begin_nested():
            raise ValueError("Buchung abgelehnt")
        sperren.merke_fehlversuch(db, gwelt.mitglied.id, anfrage_id=anfrage_id)
        sperren.merke_fehlversuch(db, gwelt.mitglied.id, anfrage_id=anfrage_id)
    db.commit()
    with pytest.raises(sperren.GutscheinRateLimitFehler) as fehler:
        sperren.pruefe_rate_limit(db, gwelt.mitglied.id)
    assert fehler.value.grund == "zu_viele_versuche"
    assert len(db.query(GutscheinFehlversuch).all()) == 10
    alt = db.query(GutscheinFehlversuch).first()
    alt.zeitpunkt = clock.now(db) - timedelta(minutes=61)
    db.flush()
    sperren.pruefe_rate_limit(db, gwelt.mitglied.id)


def test_buchungsmenge_vorbereitung_enthaelt_kaeufer_einloeser_und_zahlung(
    db: Session, gwelt
) -> None:
    from beachhub_core.services import gutschein_sperren as sperren
    from hilfen_gutschein import einloesung, kaufgutschein

    g = kaufgutschein(db, gwelt, inhaber_kunde_id=gwelt.nichtmitglied.id)
    e = einloesung(db, gwelt, g)
    db.commit()
    kontext = sperren.bereite_buchungen_vor(db, [e.buchung_id])
    assert kontext.kunde_ids == frozenset([gwelt.mitglied.id, gwelt.nichtmitglied.id])
    assert set(kontext.gutscheine) == {g.id}
    assert set(kontext.zahlungen) == {g.zahlung_id}


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
