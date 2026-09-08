from decimal import Decimal

import pytest
from beachhub_core.models import Audit, GuthabenBuchung, Kunde, Kundengruppe
from beachhub_core.services import guthaben, kunden
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


@pytest.fixture
def gruppen(db: Session) -> tuple[Kundengruppe, Kundengruppe]:
    p, v = Kundengruppe(name="Privat"), Kundengruppe(name="Verein", standard_zahlungsart="rechnung")
    db.add_all([p, v])
    db.commit()
    return p, v


def test_anlegen_nimmt_zahlungsart_der_gruppe(db: Session, gruppen) -> None:
    _, verein = gruppen
    k = kunden.lege_an(db, name="TSV", email="Info@TSV.de", kundengruppe_id=verein.id)
    db.commit()
    assert (
        k.zahlungsart == "rechnung" and k.email == "info@tsv.de" and k.guthaben == Decimal("0.00")
    )


def test_email_doppelt_wirft(db: Session, gruppen) -> None:
    p, _ = gruppen
    kunden.lege_an(db, name="A", email="a@x.de", kundengruppe_id=p.id)
    db.commit()
    with pytest.raises(kunden.KundenFehler, match="email_vergeben"):
        kunden.lege_an(db, name="B", email="A@x.de", kundengruppe_id=p.id)


def test_guthaben_buchen_und_deckung(db: Session, gruppen) -> None:
    p, _ = gruppen
    k = kunden.lege_an(db, name="A", email="a@x.de", kundengruppe_id=p.id)
    guthaben.buche(db, kunde=k, betrag=Decimal("30.00"), art="storno_gutschrift")
    guthaben.buche(db, kunde=k, betrag=Decimal("-10.00"), art="verrechnung")
    db.commit()
    assert k.guthaben == Decimal("20.00") == guthaben.saldo(db, k.id)
    with pytest.raises(guthaben.GuthabenFehler, match="nicht_gedeckt"):
        guthaben.buche(db, kunde=k, betrag=Decimal("-25.00"), art="auszahlung")
    assert db.query(GuthabenBuchung).count() == 2
    assert db.query(Audit).filter_by(objekt_typ="guthaben").count() == 2


def test_aendern_protokolliert(db: Session, gruppen) -> None:
    p, v = gruppen
    k = kunden.lege_an(db, name="A", email="a@x.de", kundengruppe_id=p.id)
    kunden.aendere(db, k, admin_user_id=None, kundengruppe_id=v.id, zahlungsart="rechnung")
    db.commit()
    a = db.query(Audit).filter_by(objekt_typ="kunde").order_by(Audit.zeitpunkt.desc()).first()
    assert a.nachher_json["zahlungsart"] == "rechnung"


def test_anonymisieren(db: Session, gruppen) -> None:
    p, _ = gruppen
    k = kunden.lege_an(db, name="Anna Müller", email="anna@x.de", kundengruppe_id=p.id)
    kunden.anonymisiere(db, k)
    db.commit()
    assert k.name == "Gelöschter Kunde" and "@" not in k.email and k.anonymisiert_am is not None


def test_aendern_auf_vergebene_email_wirft(db: Session, gruppen) -> None:
    p, _ = gruppen
    _ = kunden.lege_an(db, name="A", email="a@x.de", kundengruppe_id=p.id)
    k2 = kunden.lege_an(db, name="B", email="b@x.de", kundengruppe_id=p.id)
    db.commit()
    with pytest.raises(kunden.KundenFehler, match="email_vergeben"):
        kunden.aendere(db, k2, admin_user_id=None, email="A@x.de")
    db.rollback()
    db.refresh(k2)
    assert k2.email == "b@x.de"


def test_check_constraint_verhindert_negatives_guthaben(db: Session, gruppen) -> None:
    p, _ = gruppen
    k = kunden.lege_an(db, name="A", email="a@x.de", kundengruppe_id=p.id)
    db.commit()
    with pytest.raises(IntegrityError):
        db.execute(update(Kunde).values(guthaben=Decimal("-1")).where(Kunde.id == k.id))
        db.commit()
    db.rollback()
