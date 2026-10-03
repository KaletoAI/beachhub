from datetime import date, time
from decimal import Decimal

import pytest
from beachhub_core import clock
from beachhub_core.models import (
    Audit,
    Betriebszeit,
    Feld,
    FeldRaster,
    LesestandVersion,
    Tarif,
    utcnow,
)
from beachhub_core.services import buchungen, konfiguration, kunden, kundengruppen, mitgliedschaft
from beachhub_shared.zeit import kombiniere
from sqlalchemy.orm import Session

HEUTE = date(2027, 11, 25)


@pytest.fixture
def welt(db: Session):
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    mitglied = kundengruppen.mitglied(db)
    db.add_all(
        [
            f,
            Tarif(name="Std", preis=Decimal("30.00")),
            Tarif(name="Mitglieder", preis=Decimal("20.00"), kundengruppe_id=mitglied.id),
        ]
    )
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    k = kunden.lege_an(db, name="Anna", email="anna@x.de")
    db.commit()
    clock.set_override(db, HEUTE)
    return f, k


@pytest.mark.parametrize(
    "tag,naechster,letzter",
    [
        (date(2027, 11, 25), date(2028, 4, 30), date(2027, 4, 30)),
        (date(2028, 4, 30), date(2029, 4, 30), date(2028, 4, 30)),
        (date(2028, 1, 1), date(2028, 4, 30), date(2027, 4, 30)),
    ],
)
def test_ablauftage(db: Session, tag: date, naechster: date, letzter: date) -> None:
    assert mitgliedschaft.naechster_ablauf(db, tag) == naechster
    assert mitgliedschaft.letzter_ablauf(db, tag) == letzter


def test_ablauftag_folgt_der_einstellung(db: Session) -> None:
    konfiguration.setze(db, "mitgliedschaft_ablauf", "31.03.")
    assert mitgliedschaft.naechster_ablauf(db, date(2027, 11, 25)) == date(2028, 3, 31)


def test_freischalten_setzt_status_und_preis(db: Session, welt) -> None:
    f, k = welt
    k.mitglied_antrag_am = utcnow()
    k.mitglied_antrag_hinweis = "Nr. 4711"
    db.commit()
    assert mitgliedschaft.status(k, HEUTE) == "beantragt"
    mitgliedschaft.freischalten(db, k, bis=date(2028, 4, 30), admin_user_id=None)
    db.commit()
    assert mitgliedschaft.status(k, HEUTE) == "mitglied"
    # Der Antrag ist erledigt, die Angaben daraus (Mitgliedsnummer) bleiben stehen.
    assert k.mitglied_antrag_am is None and k.mitglied_antrag_hinweis == "Nr. 4711"
    assert k.mitglied_freigeschaltet_am is not None
    a = (
        db.query(Audit)
        .filter_by(objekt_typ="kunde", objekt_id=k.id)
        .order_by(Audit.zeitpunkt.desc())
        .first()
    )
    assert a.nachher_json["aktion"] == "mitglied_freigeschaltet"
    assert a.nachher_json["mitglied_bis"] == "2028-04-30"
    assert db.get(LesestandVersion, f"konto:{k.id}").geaendert
    b = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k.id,
        beginn=kombiniere(date(2027, 12, 1), time(19)),
        ende=kombiniere(date(2027, 12, 1), time(20)),
        zahlungsart="online",
    )
    assert b.preis == Decimal("20.00") and b.ust_satz == Decimal("7.00")


def test_freischalten_lehnt_vergangenes_datum_ab(db: Session, welt) -> None:
    _, k = welt
    with pytest.raises(mitgliedschaft.MitgliedschaftsFehler, match="bis_vergangen"):
        mitgliedschaft.freischalten(db, k, bis=date(2027, 11, 24), admin_user_id=None)


def test_beenden_wirkt_sofort(db: Session, welt) -> None:
    _, k = welt
    mitgliedschaft.freischalten(db, k, bis=date(2028, 4, 30), admin_user_id=None)
    db.commit()
    assert mitgliedschaft.beende(db, k, grund="ausgetreten", admin_user_id=None) is True
    db.commit()
    assert k.mitglied_bis == date(2027, 11, 24) and k.mitglied_beendet_grund == "ausgetreten"
    assert mitgliedschaft.status(k, HEUTE) == "nicht_mitglied"


def test_beenden_einer_abgelaufenen_mitgliedschaft_vermerkt_nur(db: Session, welt) -> None:
    _, k = welt
    k.mitglied_bis = date(2027, 4, 30)
    db.commit()
    assert mitgliedschaft.beende(db, k, grund="nicht verlängert", admin_user_id=None) is False
    assert k.mitglied_bis == date(2027, 4, 30) and k.mitglied_beendet_am is not None


def test_beenden_ohne_mitgliedschaft_wirft(db: Session, welt) -> None:
    _, k = welt
    with pytest.raises(mitgliedschaft.MitgliedschaftsFehler, match="kein_mitglied"):
        mitgliedschaft.beende(db, k, grund="x", admin_user_id=None)


def test_antrag_verwerfen(db: Session, welt) -> None:
    _, k = welt
    with pytest.raises(mitgliedschaft.MitgliedschaftsFehler, match="kein_antrag"):
        mitgliedschaft.verwerfe_antrag(db, k, admin_user_id=None)
    k.mitglied_antrag_am = utcnow()
    db.commit()
    assert mitgliedschaft.offene_antraege(db) == [k]
    mitgliedschaft.verwerfe_antrag(db, k, admin_user_id=None)
    db.commit()
    assert mitgliedschaft.offene_antraege(db) == []
    assert mitgliedschaft.status(k, HEUTE) == "nicht_mitglied"
