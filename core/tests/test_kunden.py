from decimal import Decimal

import pytest
from beachhub_core.models import Audit, GuthabenBuchung, Kunde, LesestandVersion
from beachhub_core.services import guthaben, kunden
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def test_anlegen_normalisiert_und_ist_kein_rechnungskunde(db: Session) -> None:
    k = kunden.lege_an(db, name=" TSV ", email="Info@TSV.de")
    db.commit()
    assert k.name == "TSV" and k.email == "info@tsv.de" and k.guthaben == Decimal("0.00")
    assert k.rechnungskunde is False and k.mitglied_bis is None


def test_anlegen_als_rechnungskunde(db: Session) -> None:
    k = kunden.lege_an(db, name="TSV", email="v@x.de", rechnungskunde=True)
    db.commit()
    assert k.rechnungskunde is True


def test_email_doppelt_wirft(db: Session) -> None:
    kunden.lege_an(db, name="A", email="a@x.de")
    db.commit()
    with pytest.raises(kunden.KundenFehler, match="email_vergeben"):
        kunden.lege_an(db, name="B", email="A@x.de")


def test_guthaben_buchen_und_deckung(db: Session) -> None:
    k = kunden.lege_an(db, name="A", email="a@x.de")
    guthaben.buche(db, kunde=k, betrag=Decimal("30.00"), art="storno_gutschrift")
    guthaben.buche(db, kunde=k, betrag=Decimal("-10.00"), art="verrechnung")
    db.commit()
    assert k.guthaben == Decimal("20.00") == guthaben.saldo(db, k.id)
    with pytest.raises(guthaben.GuthabenFehler, match="nicht_gedeckt"):
        guthaben.buche(db, kunde=k, betrag=Decimal("-25.00"), art="auszahlung")
    assert db.query(GuthabenBuchung).count() == 2
    assert db.query(Audit).filter_by(objekt_typ="guthaben").count() == 2


def test_aendern_protokolliert_und_markiert_konto(db: Session) -> None:
    k = kunden.lege_an(db, name="A", email="a@x.de")
    db.commit()
    kunden.aendere(db, k, admin_user_id=None, rechnungskunde=True)
    db.commit()
    a = db.query(Audit).filter_by(objekt_typ="kunde").order_by(Audit.zeitpunkt.desc()).first()
    assert a.vorher_json["rechnungskunde"] is False and a.nachher_json["rechnungskunde"] is True
    assert db.get(LesestandVersion, f"konto:{k.id}").geaendert


def test_anonymisieren(db: Session) -> None:
    k = kunden.lege_an(db, name="Anna Müller", email="anna@x.de")
    kunden.anonymisiere(db, k)
    db.commit()
    assert k.name == "Gelöschter Kunde" and "@" not in k.email and k.anonymisiert_am is not None


def test_aendern_auf_vergebene_email_wirft(db: Session) -> None:
    _ = kunden.lege_an(db, name="A", email="a@x.de")
    k2 = kunden.lege_an(db, name="B", email="b@x.de")
    db.commit()
    with pytest.raises(kunden.KundenFehler, match="email_vergeben"):
        kunden.aendere(db, k2, admin_user_id=None, email="A@x.de")
    db.rollback()
    db.refresh(k2)
    assert k2.email == "b@x.de"


def test_check_constraint_verhindert_negatives_guthaben(db: Session) -> None:
    k = kunden.lege_an(db, name="A", email="a@x.de")
    db.commit()
    with pytest.raises(IntegrityError):
        db.execute(update(Kunde).values(guthaben=Decimal("-1")).where(Kunde.id == k.id))
        db.commit()
    db.rollback()
