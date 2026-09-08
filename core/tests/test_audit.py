from beachhub_core.models import Audit, Feld
from beachhub_core.services import audit
from sqlalchemy.orm import Session


def test_als_dict_und_protokollieren(db: Session) -> None:
    f = Feld(name="Feld 1", reihenfolge=1)
    db.add(f)
    db.flush()
    vorher = audit.als_dict(f)
    f.name = "Feld A"
    audit.protokolliere(
        db,
        quelle="admin",
        objekt_typ="feld",
        objekt_id=f.id,
        vorher=vorher,
        nachher=audit.als_dict(f),
    )
    db.commit()
    a = db.query(Audit).one()
    assert a.vorher_json["name"] == "Feld 1"
    assert a.nachher_json["name"] == "Feld A"
    assert a.objekt_id == f.id
