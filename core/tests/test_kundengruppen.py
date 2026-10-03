from datetime import date
from decimal import Decimal

import pytest
from beachhub_core.models import Audit, Kunde, Kundengruppe, LesestandVersion
from beachhub_core.services import kundengruppen
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def test_zwei_feste_gruppen_mit_vorgaben(db: Session) -> None:
    mitglied = kundengruppen.mitglied(db)
    nicht = kundengruppen.nicht_mitglied(db)
    db.commit()
    assert mitglied.ist_mitglied and mitglied.ust_satz == Decimal("7.00")
    assert not nicht.ist_mitglied and nicht.ust_satz == Decimal("19.00")
    assert kundengruppen.mitglied(db).id == mitglied.id  # kein zweites Anlegen
    assert db.query(Kundengruppe).count() == 2
    assert [g.id for g in kundengruppen.beide(db)] == [nicht.id, mitglied.id]


def test_dritte_gruppe_scheitert_am_eindeutigen_index(db: Session) -> None:
    kundengruppen.beide(db)
    db.commit()
    db.add(Kundengruppe(name="Verein", ust_satz=Decimal("19.00"), ist_mitglied=False))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


@pytest.mark.parametrize(
    "mitglied_bis,datum,erwartet",
    [
        (date(2028, 3, 31), date(2028, 3, 31), True),
        (date(2028, 3, 31), date(2028, 4, 1), False),
        (None, date(2028, 3, 1), False),
    ],
)
def test_effektive_gruppe_folgt_mitglied_bis(
    db: Session, mitglied_bis: date | None, datum: date, erwartet: bool
) -> None:
    k = Kunde(name="A", email="a@x.de", mitglied_bis=mitglied_bis)
    assert kundengruppen.ist_mitglied_am(k, datum) is erwartet
    assert kundengruppen.effektive_gruppe(db, k, datum).ist_mitglied is erwartet


def test_aendere_name_und_satz_protokolliert(db: Session) -> None:
    g = kundengruppen.mitglied(db)
    db.commit()
    kundengruppen.aendere(
        db, g, name=" DJK Augsburg ", ust_satz=Decimal("7.50"), admin_user_id=None
    )
    db.commit()
    assert g.name == "DJK Augsburg" and g.ust_satz == Decimal("7.50")
    a = db.query(Audit).filter_by(objekt_typ="kundengruppe", objekt_id=g.id).one()
    assert a.vorher_json["ust_satz"] == "7.00" and a.nachher_json["ust_satz"] == "7.50"
    # Gruppennamen stehen im Tarif-Dokument des Portals.
    assert db.get(LesestandVersion, "tarife").geaendert


@pytest.mark.parametrize(
    "name,satz", [("", Decimal("7")), ("X", Decimal("100")), ("X", Decimal("-1"))]
)
def test_aendere_lehnt_ungueltiges_ab(db: Session, name: str, satz: Decimal) -> None:
    g = kundengruppen.nicht_mitglied(db)
    with pytest.raises(kundengruppen.GruppenFehler):
        kundengruppen.aendere(db, g, name=name, ust_satz=satz, admin_user_id=None)


def test_umbenennen_markiert_konto_dokumente_nicht_der_satz(db: Session) -> None:
    import uuid

    k = Kunde(name="A", email="a@x.de", portal_konto_id=uuid.uuid4())
    ohne = Kunde(name="B", email="b@x.de")
    db.add_all([k, ohne])
    g = kundengruppen.mitglied(db)
    db.commit()
    kundengruppen.aendere(db, g, name=g.name, ust_satz=Decimal("5.00"), admin_user_id=None)
    db.commit()
    assert db.get(LesestandVersion, f"konto:{k.id}") is None
    kundengruppen.aendere(db, g, name="Verein", ust_satz=Decimal("5.00"), admin_user_id=None)
    db.commit()
    assert db.get(LesestandVersion, f"konto:{k.id}").geaendert
    assert db.get(LesestandVersion, f"konto:{ohne.id}") is None
