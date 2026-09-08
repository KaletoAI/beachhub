from datetime import date, time
from decimal import Decimal

import pytest
from beachhub_core import clock
from beachhub_core.models import (
    Audit,
    Betriebszeit,
    Feld,
    FeldRaster,
    Kundengruppe,
    Rechnung,
    RechnungPosition,
    Tarif,
)
from beachhub_core.services import buchungen, konfiguration, kunden, rechnungen, storno
from beachhub_shared.zeit import kombiniere
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


@pytest.fixture
def welt(db: Session):
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    p = Kundengruppe(name="Privat")
    v = Kundengruppe(name="Verein", standard_zahlungsart="rechnung")
    db.add_all([f, p, v, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    a = kunden.lege_an(
        db,
        name="A",
        email="a@x.de",
        kundengruppe_id=p.id,
        adresse_strasse="Weg 1",
        adresse_plz="12345",
        adresse_ort="Ort",
    )
    v1 = kunden.lege_an(db, name="TSV", email="v@x.de", kundengruppe_id=v.id)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    konfiguration.setze(
        db, "storno_frist_stunden", 72
    )  # Storno am 14.12. für den 15.12. ist damit sicher kostenpflichtig
    db.commit()
    return f, a, v1


def test_nummern_lueckenlos_je_jahr(db: Session) -> None:
    assert rechnungen.naechste_nummer(db, 2027) == "2027-00001"
    assert rechnungen.naechste_nummer(db, 2027) == "2027-00002"
    assert rechnungen.naechste_nummer(db, 2028) == "2028-00001"


def test_einzelrechnung_mit_ust(db: Session, welt) -> None:
    f, a, _ = welt
    b = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=a.id,
        beginn=kombiniere(date(2027, 12, 1), time(19)),
        ende=kombiniere(date(2027, 12, 1), time(20)),
    )
    r = rechnungen.erzeuge_einzelrechnung(db, b)
    db.commit()
    assert (
        r.status == "bezahlt"
        and r.brutto == Decimal("30.00")
        and r.netto == Decimal("25.21")
        and r.ust == Decimal("4.79")
    )
    assert r.adresse_snapshot["name"] == "A" and b.rechnung_position_id == r.positionen[0].id
    with pytest.raises(rechnungen.RechnungsFehler, match="bereits_berechnet"):
        rechnungen.erzeuge_einzelrechnung(db, b)


def test_sammelrechnung_und_monatslauf_idempotent(db: Session, welt) -> None:
    f, a, v1 = welt
    for tag in (1, 8):
        buchungen.lege_an(
            db,
            feld_id=f.id,
            kunde_id=v1.id,
            beginn=kombiniere(date(2027, 12, tag), time(19)),
            ende=kombiniere(date(2027, 12, tag), time(21)),
        )
    b3 = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=v1.id,
        beginn=kombiniere(date(2027, 12, 15), time(19)),
        ende=kombiniere(date(2027, 12, 15), time(21)),
    )
    clock.set_override(db, date(2027, 12, 14))  # innerhalb der 72-h-Frist → kostenpflichtig
    storno.storniere(db, b3, durch="kunde")
    buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=v1.id,
        beginn=kombiniere(date(2028, 1, 5), time(19)),
        ende=kombiniere(date(2028, 1, 5), time(20)),
    )
    db.commit()
    clock.set_override(db, date(2028, 1, 3))
    erzeugt = rechnungen.monatslauf(db, 2027, 12)
    db.commit()
    assert len(erzeugt) == 1
    r = erzeugt[0]
    assert (
        r.art == "sammel"
        and r.status == "offen"
        and len(r.positionen) == 3
        and r.brutto == Decimal("180.00")
    )
    assert r.faellig_am == date(2028, 1, 17) and r.leistung_von == date(2027, 12, 1)
    assert rechnungen.monatslauf(db, 2027, 12) == []


def test_stornorechnung_gibt_buchungen_frei(db: Session, welt) -> None:
    f, _, v1 = welt
    b = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=v1.id,
        beginn=kombiniere(date(2027, 12, 1), time(19)),
        ende=kombiniere(date(2027, 12, 1), time(20)),
    )
    db.commit()
    clock.set_override(db, date(2028, 1, 3))
    r = rechnungen.erzeuge_sammelrechnung(db, v1, 2027, 12)
    db.commit()
    s = rechnungen.storniere(db, r, admin_user_id=None, grund="Falscher Preis")
    db.commit()
    assert s.art == "storno" and s.brutto == Decimal("-30.00") and s.storniert_durch_id is None
    assert (
        r.status == "storniert" and r.storniert_durch_id == s.id and b.rechnung_position_id is None
    )
    assert db.query(Rechnung).count() == 2


def test_csv_export(db: Session, welt) -> None:
    f, a, _ = welt
    b = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=a.id,
        beginn=kombiniere(date(2027, 12, 1), time(19)),
        ende=kombiniere(date(2027, 12, 1), time(20)),
    )
    rechnungen.erzeuge_einzelrechnung(db, b)
    db.commit()
    csv = rechnungen.csv_export(db, date(2027, 11, 1), date(2027, 12, 31))
    zeilen = csv.strip().splitlines()
    assert zeilen[0].startswith("nummer;datum;kunde;art;status;netto;ust;brutto")
    assert len(zeilen) == 2 and ";30,00" in zeilen[1]


def test_buchung_kann_nicht_doppelt_berechnet_werden(db: Session, welt) -> None:
    f, a, _ = welt
    b = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=a.id,
        beginn=kombiniere(date(2027, 12, 1), time(19)),
        ende=kombiniere(date(2027, 12, 1), time(20)),
    )
    r = rechnungen.erzeuge_einzelrechnung(db, b)
    db.commit()
    db.add(
        RechnungPosition(
            rechnung_id=r.id,
            reihenfolge=99,
            buchung_id=b.id,
            text="doppelt",
            menge=1,
            einzelpreis_brutto=Decimal("30.00"),
            ust_satz=Decimal("19.00"),
            brutto=Decimal("30.00"),
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_setze_bezahlt_und_nicht_offen(db: Session, welt) -> None:
    f, _, v1 = welt
    buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=v1.id,
        beginn=kombiniere(date(2027, 12, 1), time(19)),
        ende=kombiniere(date(2027, 12, 1), time(20)),
    )
    db.commit()
    clock.set_override(db, date(2028, 1, 3))
    r = rechnungen.erzeuge_sammelrechnung(db, v1, 2027, 12)
    db.commit()
    assert r is not None
    rechnungen.setze_bezahlt(db, r, admin_user_id=None)
    db.commit()
    assert r.status == "bezahlt" and r.bezahlt_am is not None
    with pytest.raises(rechnungen.RechnungsFehler, match="nicht_offen"):
        rechnungen.setze_bezahlt(db, r, admin_user_id=None)
    assert db.query(Audit).filter_by(objekt_typ="rechnung", objekt_id=r.id).count() > 0
