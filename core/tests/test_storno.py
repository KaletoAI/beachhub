from datetime import date, time
from decimal import Decimal

import pytest
from beachhub_core import clock
from beachhub_core.models import Betriebszeit, Feld, FeldRaster, Kundengruppe, Tarif
from beachhub_core.services import buchungen, konfiguration, kunden, storno
from beachhub_shared.zeit import kombiniere
from sqlalchemy.orm import Session

D = date(2027, 12, 1)


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
    a = kunden.lege_an(db, name="A", email="a@x.de", kundengruppe_id=p.id)
    b = kunden.lege_an(db, name="B", email="b@x.de", kundengruppe_id=p.id)
    v1 = kunden.lege_an(db, name="V", email="v@x.de", kundengruppe_id=v.id)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    # Frist 48 h: Override auf 30.11. liegt damit immer innerhalb der Frist,
    # egal zu welcher Tageszeit der Test läuft
    konfiguration.setze(db, "storno_frist_stunden", 48)
    db.commit()
    return f, a, b, v1


def test_vor_frist_kostenfrei_mit_gutschrift(db: Session, welt) -> None:
    f, a, _, _ = welt
    bu = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=a.id,
        beginn=kombiniere(D, time(19)),
        ende=kombiniere(D, time(21)),
    )
    db.commit()
    s = storno.storniere(db, bu, durch="kunde")
    db.commit()
    assert s.kostenfrei and not s.nachbuchung_offen and bu.status == "storniert"
    assert a.guthaben == Decimal("60.00")


def test_nach_frist_kostenpflichtig_dann_nachbuchung(db: Session, welt) -> None:
    f, a, b, _ = welt
    bu = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=a.id,
        beginn=kombiniere(D, time(19)),
        ende=kombiniere(D, time(21)),
    )
    db.commit()
    clock.set_override(db, date(2027, 11, 30))  # innerhalb der 48-h-Frist
    s = storno.storniere(db, bu, durch="kunde")
    db.commit()
    assert not s.kostenfrei and s.nachbuchung_offen and a.guthaben == Decimal("0.00")
    # anderer Kunde bucht 1 von 2 Stunden nach → anteilig
    _ = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=b.id,
        beginn=kombiniere(D, time(19)),
        ende=kombiniere(D, time(20)),
    )
    db.commit()
    db.refresh(s)
    assert s.freigestellt_betrag == Decimal("30.00") and s.nachbuchung_offen and not s.kostenfrei
    assert a.guthaben == Decimal("30.00")
    n2 = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=b.id,
        beginn=kombiniere(D, time(20)),
        ende=kombiniere(D, time(21)),
    )
    db.commit()
    db.refresh(s)
    assert s.freigestellt_betrag == Decimal("60.00") and s.kostenfrei and not s.nachbuchung_offen
    assert s.nachbuchung_buchung_id == n2.id and a.guthaben == Decimal("60.00")


def test_eigene_nachbuchung_zaehlt_nicht(db: Session, welt) -> None:
    f, a, _, _ = welt
    bu = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=a.id,
        beginn=kombiniere(D, time(19)),
        ende=kombiniere(D, time(20)),
    )
    db.commit()
    clock.set_override(db, date(2027, 11, 30))
    s = storno.storniere(db, bu, durch="kunde")
    buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=a.id,
        beginn=kombiniere(D, time(19)),
        ende=kombiniere(D, time(20)),
    )
    db.commit()
    db.refresh(s)
    assert s.nachbuchung_offen and s.freigestellt_betrag == Decimal("0.00")


def test_rechnungskunde_ohne_gutschrift_und_kulanz(db: Session, welt) -> None:
    f, _, _, v1 = welt
    bu = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=v1.id,
        beginn=kombiniere(D, time(19)),
        ende=kombiniere(D, time(20)),
    )
    clock.set_override(db, date(2027, 11, 30))
    s = storno.storniere(db, bu, durch="kunde")
    db.commit()
    assert not s.kostenfrei and v1.guthaben == Decimal("0.00")
    storno.kulanz(db, s, admin_user_id=None, grund="Krankheit")
    db.commit()
    assert (
        s.kostenfrei
        and s.freigestellt_betrag == Decimal("30.00")
        and v1.guthaben == Decimal("0.00")
    )


def test_zu_spaet_und_nicht_aktiv(db: Session, welt) -> None:
    f, a, _, _ = welt
    bu = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=a.id,
        beginn=kombiniere(D, time(19)),
        ende=kombiniere(D, time(20)),
    )
    db.commit()
    clock.set_override(db, date(2027, 12, 2))
    with pytest.raises(storno.StornoFehler, match="zu_spaet"):
        storno.storniere(db, bu, durch="kunde")
    clock.set_override(db, date(2027, 11, 25))
    storno.storniere(db, bu, durch="kunde")
    with pytest.raises(storno.StornoFehler, match="nicht_aktiv"):
        storno.storniere(db, bu, durch="kunde")


def test_betreiber_darf_laufende_buchung_stornieren(db: Session, welt, monkeypatch) -> None:
    f, a, _, _ = welt
    bu = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=a.id,
        beginn=kombiniere(D, time(9)),
        ende=kombiniere(D, time(10)),
    )
    db.commit()
    monkeypatch.setattr(storno.clock, "now", lambda db: kombiniere(D, time(9, 30)))
    s = storno.storniere(db, bu, durch="betreiber", kostenfrei=True, grund="Sperre")
    db.commit()
    assert s.kostenfrei and bu.status == "storniert"
    # Slot ist wieder frei (Storno zählt nicht als aktiv) – neue Buchung im selben Slot,
    # dazu die Uhr kurz zurückstellen, damit lege_an sie nicht als "vergangenheit" ablehnt.
    monkeypatch.undo()
    bu2 = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=a.id,
        beginn=kombiniere(D, time(9)),
        ende=kombiniere(D, time(10)),
    )
    db.commit()
    monkeypatch.setattr(storno.clock, "now", lambda db: kombiniere(D, time(9, 30)))
    with pytest.raises(storno.StornoFehler, match="zu_spaet"):
        storno.storniere(db, bu2, durch="kunde")


def test_beendete_buchung_kann_niemand_stornieren(db: Session, welt, monkeypatch) -> None:
    f, a, _, _ = welt
    bu = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=a.id,
        beginn=kombiniere(D, time(9)),
        ende=kombiniere(D, time(10)),
    )
    db.commit()
    monkeypatch.setattr(storno.clock, "now", lambda db: kombiniere(D, time(11)))
    with pytest.raises(storno.StornoFehler, match="zu_spaet"):
        storno.storniere(db, bu, durch="betreiber", kostenfrei=True, grund="Test")
