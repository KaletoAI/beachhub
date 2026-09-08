import hashlib
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_core.models import Kunde, Kundengruppe, utcnow
from beachhub_core.services import audit


class KundenFehler(Exception):  # noqa: N818
    pass


def lege_an(
    db: Session,
    *,
    name: str,
    email: str,
    kundengruppe_id: uuid.UUID,
    zahlungsart: str | None = None,
    adresse_strasse: str = "",
    adresse_plz: str = "",
    adresse_ort: str = "",
    quelle: str = "admin",
    admin_user_id: uuid.UUID | None = None,
) -> Kunde:
    email = email.strip().lower()
    if db.scalar(select(Kunde).where(Kunde.email == email)):
        raise KundenFehler("email_vergeben")
    gruppe = db.get(Kundengruppe, kundengruppe_id)
    if gruppe is None:
        raise KundenFehler("gruppe_unbekannt")
    k = Kunde(
        name=name.strip(),
        email=email,
        kundengruppe_id=kundengruppe_id,
        zahlungsart=zahlungsart or gruppe.standard_zahlungsart,
        adresse_strasse=adresse_strasse,
        adresse_plz=adresse_plz,
        adresse_ort=adresse_ort,
    )
    db.add(k)
    db.flush()
    audit.protokolliere(
        db,
        quelle=quelle,
        objekt_typ="kunde",
        objekt_id=k.id,
        vorher=None,
        nachher=audit.als_dict(k),
        admin_user_id=admin_user_id,
    )
    return k


def aendere(db: Session, kunde: Kunde, *, admin_user_id: uuid.UUID | None, **felder: Any) -> Kunde:
    vorher = audit.als_dict(kunde)
    for name, wert in felder.items():
        if name == "email":
            wert = wert.strip().lower()
        setattr(kunde, name, wert)
    db.flush()
    audit.protokolliere(
        db,
        quelle="admin",
        objekt_typ="kunde",
        objekt_id=kunde.id,
        vorher=vorher,
        nachher=audit.als_dict(kunde),
        admin_user_id=admin_user_id,
    )
    return kunde


def anonymisiere(db: Session, kunde: Kunde, admin_user_id: uuid.UUID | None = None) -> None:
    vorher = audit.als_dict(kunde)
    digest = hashlib.sha256(kunde.email.encode()).hexdigest()[:32]
    kunde.name = "Gelöschter Kunde"
    kunde.email = f"geloescht-{digest}"
    kunde.adresse_strasse = kunde.adresse_plz = kunde.adresse_ort = ""
    kunde.portal_konto_id = None
    kunde.stripe_customer_id = None
    kunde.anonymisiert_am = utcnow()
    db.flush()
    audit.protokolliere(
        db,
        quelle="admin",
        objekt_typ="kunde",
        objekt_id=kunde.id,
        vorher=vorher,
        nachher=audit.als_dict(kunde),
        admin_user_id=admin_user_id,
    )
