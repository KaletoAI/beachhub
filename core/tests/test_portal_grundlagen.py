import uuid
from datetime import date, time
from decimal import Decimal
from pathlib import Path

import pytest
from beachhub_core import clock
from beachhub_core.config import settings
from beachhub_core.models import (
    AnfrageVerarbeitet,
    Betriebszeit,
    Buchung,
    Feld,
    FeldRaster,
    Kundengruppe,
    LesestandVersion,
    Tarif,
    Zahlung,
)
from beachhub_core.services import buchungen, guthaben, konfiguration, kunden, lesestand
from beachhub_shared.zeit import kombiniere
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

D = date(2027, 12, 1)


@pytest.fixture
def welt(db: Session):
    Path(settings.signatur_privatschluessel_pfad).unlink(missing_ok=True)
    lesestand.erzeuge_schluessel()
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    v = Kundengruppe(name="Verein", standard_zahlungsart="rechnung")
    db.add_all([f, v, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    k = kunden.lege_an(db, name="V", email="v@x.de", kundengruppe_id=v.id)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    return f, k


def test_lege_an_mit_abweichender_zahlungsart(db: Session, welt) -> None:
    f, k = welt
    b = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k.id,
        beginn=kombiniere(D, time(19)),
        ende=kombiniere(D, time(20)),
        zahlungsart="online",
    )
    assert k.zahlungsart == "rechnung"
    assert b.zahlungsart == "online"


def test_rueckbuchung_ist_zugehende_guthabenart(db: Session, welt) -> None:
    _, k = welt
    guthaben.buche(db, kunde=k, betrag=Decimal("5.00"), art="rueckbuchung")
    assert k.guthaben == Decimal("5.00")


def test_anfrage_verarbeitet_und_zahlung_eindeutig(db: Session, welt) -> None:
    _, k = welt
    db.add(
        AnfrageVerarbeitet(
            anfrage_id=uuid.uuid4(), typ="konto_angelegt", antwort_json={"status": "ok"}
        )
    )
    z = Zahlung(kunde_id=k.id, provider="fake", provider_ref="fake_1", betrag=Decimal("30.00"))
    db.add(z)
    db.commit()
    assert z.status == Zahlung.OFFEN
    db.add(Zahlung(kunde_id=k.id, provider="fake", provider_ref="fake_1", betrag=Decimal("1.00")))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_portal_kundengruppe_ist_konfigurierbar(db: Session) -> None:
    assert konfiguration.hole(db, "portal_kundengruppe") == ""
    konfiguration.setze(db, "portal_kundengruppe", "Privat")
    db.commit()
    assert konfiguration.hole(db, "portal_kundengruppe") == "Privat"
    assert konfiguration.BESCHREIBUNGEN["portal_kundengruppe"].gruppe == "Portal und Zugang"


def test_belegung_enthaelt_storno_frist_und_hinweis(db: Session, welt) -> None:
    konfiguration.setze(db, "storno_frist_stunden", 48)
    db.commit()
    b = lesestand.baue_belegung(db)
    assert b.storno_frist_stunden == 48
    assert b.antwort_hinweis_sekunden == 120


@pytest.mark.parametrize("schluessel", ["storno_frist_stunden", "antwort_hinweis_sekunden"])
def test_aenderung_markiert_belegung(db: Session, welt, schluessel: str) -> None:
    lesestand.verarbeite_geaenderte(db)
    konfiguration.setze(db, schluessel, 36)
    db.commit()
    assert db.get(LesestandVersion, "belegung").geaendert


def test_pin_nur_fuer_bestaetigte_buchung(db: Session, welt) -> None:
    f, k = welt
    buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k.id,
        beginn=kombiniere(D, time(19)),
        ende=kombiniere(D, time(20)),
        status=Buchung.RESERVIERT,
        zahlungsart="online",
    )
    buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k.id,
        beginn=kombiniere(D, time(20)),
        ende=kombiniere(D, time(21)),
    )
    db.commit()
    pins = {b.status: b.pin for b in lesestand.baue_konto(db, k).buchungen}
    assert pins["reserviert"] is None
    assert pins["bestaetigt"] is not None and len(pins["bestaetigt"]) == 6


def test_zahlungslink_nur_bei_offener_reservierung(db: Session, welt) -> None:
    f, k = welt
    offen = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k.id,
        beginn=kombiniere(D, time(19)),
        ende=kombiniere(D, time(20)),
        status=Buchung.RESERVIERT,
        zahlungsart="online",
    )
    offen.reserviert_bis = kombiniere(date(2027, 11, 25), time(10, 15))
    bezahlt = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k.id,
        beginn=kombiniere(D, time(20)),
        ende=kombiniere(D, time(21)),
        status=Buchung.RESERVIERT,
        zahlungsart="online",
    )
    db.add_all(
        [
            Zahlung(
                kunde_id=k.id,
                buchung_id=offen.id,
                provider="fake",
                provider_ref="fake_offen",
                betrag=Decimal("30.00"),
                checkout_url="/test-zahlung/fake_offen",
            ),
            Zahlung(
                kunde_id=k.id,
                buchung_id=bezahlt.id,
                provider="fake",
                provider_ref="fake_bezahlt",
                betrag=Decimal("30.00"),
                status=Zahlung.BEZAHLT,
                checkout_url="/test-zahlung/fake_bezahlt",
            ),
        ]
    )
    db.commit()
    je_id = {b.id: b for b in lesestand.baue_konto(db, k).buchungen}
    assert je_id[str(offen.id)].checkout_url == "/test-zahlung/fake_offen"
    assert je_id[str(offen.id)].reserviert_bis == offen.reserviert_bis
    assert je_id[str(bezahlt.id)].checkout_url is None
    assert je_id[str(bezahlt.id)].reserviert_bis is None


def test_tarife_aelteste_zuerst(db: Session, welt) -> None:
    db.add(Tarif(name="Neu", preis=Decimal("40.00")))
    db.commit()
    assert [r.name for r in lesestand.baue_tarife(db).regeln] == ["Std", "Neu"]
