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


@pytest.mark.parametrize("anzahl", [0, -1, 1001])
def test_freicodes_ausgabegrenzen(db: Session, anzahl: int) -> None:
    from beachhub_core.services import gutscheine

    with pytest.raises(ValueError):
        gutscheine.stelle_freicodes_aus(db, anzahl=anzahl, gueltig_bis=None, admin_user_id=None)


def test_freicodes_snapshot_ohne_geld_und_steuer(db: Session, admin) -> None:
    from datetime import timedelta

    from beachhub_core import clock
    from beachhub_core.models import Audit
    from beachhub_core.services import gutscheine, konfiguration

    clock.set_override(db, date(2027, 11, 25))
    konfiguration.setze(db, "gutschein_modell", "wert")
    konfiguration.setze(db, "gutschein_minuten", 180)
    codes = gutscheine.stelle_freicodes_aus(
        db, anzahl=2, gueltig_bis=None, admin_user_id=admin[0].id
    )
    assert len({g.code for g in codes}) == 2
    for g in codes:
        assert (g.art, g.modell, g.steuer, g.max_einloesungen) == ("frei", "einheit", "keine", 1)
        assert g.minuten == 180 and g.gueltig_bis == clock.today(db) + timedelta(days=90)
        assert (g.kaufpreis_brutto, g.nennwert_brutto, g.restwert, g.ust_satz, g.rechnung_id) == (
            None,
        ) * 5
        a = db.query(Audit).filter_by(objekt_typ="gutschein", objekt_id=g.id).one()
        assert a.admin_user_id == admin[0].id and a.nachher_json["status"] == "aktiv"


def test_freicodes_obere_grenze_und_optionen(db: Session, gwelt) -> None:
    from beachhub_core.services import gutscheine

    codes = gutscheine.stelle_freicodes_aus(
        db,
        anzahl=1000,
        gueltig_bis=date(2029, 1, 1),
        max_einloesungen=3,
        feld_id=gwelt.feld.id,
        zeitraum_von=date(2028, 1, 1),
        zeitraum_bis=date(2028, 2, 1),
        serie="Aktion",
        admin_user_id=None,
    )
    assert len(codes) == 1000
    assert all(g.max_einloesungen == 3 and g.feld_id == gwelt.feld.id for g in codes)


@pytest.mark.parametrize(
    "werte",
    [
        {"max_einloesungen": 0},
        {"zeitraum_von": date(2029, 1, 1), "zeitraum_bis": date(2028, 1, 1)},
        {"gueltig_bis": date(2020, 1, 1)},
        {"serie": "x" * 101},
        {"notiz": "x" * 1001},
    ],
)
def test_freicodes_ungueltige_vorgaben(db: Session, werte) -> None:
    from beachhub_core.services import gutscheine

    with pytest.raises(ValueError):
        gutscheine.stelle_freicodes_aus(
            db, anzahl=1, admin_user_id=None, **{"gueltig_bis": None, **werte}
        )


def test_gueltigkeit_erhaelt_verbrauch_und_rueckgabesperre(db: Session, gwelt) -> None:
    from beachhub_core import clock
    from beachhub_core.services import gutscheine
    from hilfen_gutschein import einloesung, kaufgutschein

    g = kaufgutschein(
        db,
        gwelt,
        modell="wert",
        nennwert_brutto=Decimal("50"),
        restwert=Decimal("20"),
        status="teilweise_eingeloest",
    )
    e = einloesung(db, gwelt, g, status="bestaetigt")
    gutscheine.sperre(db, g)
    gutscheine.setze_gueltigkeit(db, g, date(2030, 1, 1), reaktivieren=True)
    assert (
        g.status == "teilweise_eingeloest"
        and g.restwert == Decimal("20")
        and e.status == "bestaetigt"
    )
    g.restwert = Decimal("0")
    g.status = "eingeloest"
    db.flush()
    gutscheine.sperre(db, g)
    gutscheine.setze_gueltigkeit(db, g, date(2031, 1, 1), reaktivieren=True)
    assert g.status == "eingeloest" and g.restwert == Decimal("0")
    g.zurueckgegeben_am = clock.now(db)
    g.status = "gesperrt"
    db.flush()
    with pytest.raises(ValueError, match="zurückgegeben"):
        gutscheine.setze_gueltigkeit(db, g, date(2032, 1, 1), reaktivieren=True)
    assert g.gueltig_bis == date(2031, 1, 1) and g.status == "gesperrt"


def test_freicode_verbrauch_reaktivierung_keine_neue_nutzung(db: Session, gwelt) -> None:
    from beachhub_core.services import gutscheine
    from hilfen_gutschein import einloesung

    g = gutscheine.stelle_freicodes_aus(db, anzahl=1, gueltig_bis=None, admin_user_id=None)[0]
    e = einloesung(db, gwelt, g, status="bestaetigt")
    g.status = "eingeloest"
    db.flush()
    gutscheine.sperre(db, g)
    gutscheine.setze_gueltigkeit(db, g, date(2030, 1, 1), reaktivieren=True)
    assert g.status == "eingeloest" and e.status == "bestaetigt" and g.max_einloesungen == 1


def test_verfall_gueltig_bis_inklusive_und_audit_invalidierung(db: Session, gwelt) -> None:
    from datetime import timedelta

    from beachhub_core import clock
    from beachhub_core.models import Audit, LesestandVersion
    from beachhub_core.services import gutscheine
    from hilfen_gutschein import kaufgutschein

    heute = clock.today(db)
    g = kaufgutschein(
        db, gwelt, inhaber_kunde_id=gwelt.nichtmitglied.id, gueltig_bis=heute - timedelta(days=1)
    )
    h = kaufgutschein(db, gwelt, gueltig_bis=heute)
    assert gutscheine.verfalle(db) == 1
    assert g.status == "verfallen" and h.status == "aktiv"
    assert gutscheine.verfalle(db) == 0
    a = db.query(Audit).filter_by(objekt_typ="gutschein", objekt_id=g.id).one()
    assert a.quelle == "system" and a.vorher_json["status"] == "aktiv"
    for k in (gwelt.mitglied, gwelt.nichtmitglied):
        assert db.get(LesestandVersion, f"konto:{k.id}").geaendert


def test_serie_csv_entschaerft_formeln(db: Session) -> None:
    import csv
    import io

    from beachhub_core.services import gutscheine

    gutscheine.stelle_freicodes_aus(
        db, anzahl=1, gueltig_bis=None, serie="=Serie", notiz="@Formel", admin_user_id=None
    )
    rows = list(csv.DictReader(io.StringIO(gutscheine.serie_csv(db, "=Serie")), delimiter=";"))
    assert len(rows) == 1 and rows[0]["serie"] == "'=Serie" and rows[0]["notiz"] == "'@Formel"


def test_datumsaenderung_reaktiviert_nie_automatisch(db: Session, gwelt) -> None:
    from beachhub_core import clock
    from beachhub_core.services import gutscheine
    from hilfen_gutschein import kaufgutschein

    g = kaufgutschein(
        db,
        gwelt,
        status="verfallen",
        modell="wert",
        nennwert_brutto=Decimal("50"),
        restwert=Decimal("20"),
    )
    gutscheine.setze_gueltigkeit(db, g, date(2030, 1, 1))
    assert g.status == "verfallen" and g.restwert == Decimal("20")
    g.zurueckgegeben_am = clock.now(db)
    g.status = "gesperrt"
    db.flush()
    gutscheine.setze_gueltigkeit(db, g, date(2031, 1, 1))
    assert g.status == "gesperrt" and g.zurueckgegeben_am is not None
    with pytest.raises(ValueError, match="zurückgegeben"):
        gutscheine.setze_gueltigkeit(db, g, date(2032, 1, 1), reaktivieren=True)


def test_mehrfachfreicode_erhaelt_nutzungen(db: Session, gwelt) -> None:
    from beachhub_core.services import gutscheine
    from hilfen_gutschein import einloesung

    g = gutscheine.stelle_freicodes_aus(
        db, anzahl=1, gueltig_bis=None, max_einloesungen=3, admin_user_id=None
    )[0]
    e = einloesung(db, gwelt, g, status="bestaetigt")
    gutscheine.sperre(db, g)
    gutscheine.setze_gueltigkeit(db, g, date(2030, 1, 1), reaktivieren=True)
    assert g.status == "aktiv" and g.max_einloesungen == 3 and e.status == "bestaetigt"


def test_verwaltung_unveraendert_kein_zusaetzliches_audit(db: Session) -> None:
    from beachhub_core.models import Audit
    from beachhub_core.services import gutscheine

    g = gutscheine.stelle_freicodes_aus(db, anzahl=1, gueltig_bis=None, admin_user_id=None)[0]
    gutscheine.setze_gueltigkeit(db, g, g.gueltig_bis)
    gutscheine.sperre(db, g)
    gutscheine.sperre(db, g)
    assert db.query(Audit).filter_by(objekt_typ="gutschein", objekt_id=g.id).count() == 2
