import uuid
from datetime import date, time
from decimal import Decimal
from typing import Any, TypeVar

from sqlalchemy.orm import Session

from beachhub_core.models import Ausnahmetag, Betriebszeit, Feld, FeldRaster, Kundengruppe, Tarif
from beachhub_core.services import audit

T = TypeVar("T")


class StammdatenFehler(Exception):  # noqa: N818
    pass


def _pruefe_zeiten(oeffnet: time, schliesst: time) -> None:
    if schliesst != time(0, 0) and schliesst <= oeffnet:
        raise StammdatenFehler("Schließzeit muss nach der Öffnungszeit liegen")


def _log(
    db: Session,
    typ: str,
    obj: Any,
    vorher: dict[str, Any] | None,
    admin_user_id: uuid.UUID | None,
    geloescht: bool = False,
) -> None:
    audit.protokolliere(
        db,
        quelle="admin",
        objekt_typ=typ,
        objekt_id=obj.id,
        vorher=vorher,
        nachher=None if geloescht else audit.als_dict(obj),
        admin_user_id=admin_user_id,
    )


def _anlegen(db: Session, typ: str, obj: T, admin_user_id: uuid.UUID | None) -> T:
    db.add(obj)
    db.flush()
    _log(db, typ, obj, None, admin_user_id)
    return obj


def _aendern(db: Session, typ: str, obj: T, admin_user_id: uuid.UUID | None, **felder: Any) -> T:
    vorher = audit.als_dict(obj)
    for k, v in felder.items():
        setattr(obj, k, v)
    db.flush()
    _log(db, typ, obj, vorher, admin_user_id)
    return obj


def _loeschen(db: Session, typ: str, obj: Any, admin_user_id: uuid.UUID | None) -> None:
    _log(db, typ, obj, audit.als_dict(obj), admin_user_id, geloescht=True)
    db.delete(obj)
    db.flush()


def feld_anlegen(
    db: Session, *, admin_user_id: uuid.UUID | None, name: str, reihenfolge: int
) -> Feld:
    if not name.strip():
        raise StammdatenFehler("Name fehlt")
    return _anlegen(db, "feld", Feld(name=name.strip(), reihenfolge=reihenfolge), admin_user_id)


def feld_aendern(
    db: Session, feld: Feld, *, admin_user_id: uuid.UUID | None, **felder: Any
) -> Feld:
    return _aendern(db, "feld", feld, admin_user_id, **felder)


def raster_setzen(
    db: Session,
    feld: Feld,
    *,
    admin_user_id: uuid.UUID | None,
    wochentag: int | None,
    modus: str,
    slot_minuten: int | None,
    fenster: list[tuple[str, str]],
) -> FeldRaster:
    if wochentag is not None and not 0 <= wochentag <= 6:
        raise StammdatenFehler("Wochentag ungültig")
    if modus == "dauer" and (not slot_minuten or slot_minuten <= 0):
        raise StammdatenFehler("Slot-Dauer in Minuten fehlt")
    if modus == "fenster" and not fenster:
        raise StammdatenFehler("Mindestens ein Zeitfenster angeben")
    for r in list(feld.raster):
        if r.wochentag == wochentag:
            _loeschen(db, "feld_raster", r, admin_user_id)
    neu = FeldRaster(
        feld_id=feld.id,
        wochentag=wochentag,
        modus=modus,
        slot_minuten=slot_minuten if modus == "dauer" else None,
        fenster_json=[[a, b] for a, b in fenster] if modus == "fenster" else [],
    )
    return _anlegen(db, "feld_raster", neu, admin_user_id)


def raster_loeschen(db: Session, raster: FeldRaster, *, admin_user_id: uuid.UUID | None) -> None:
    _loeschen(db, "feld_raster", raster, admin_user_id)


def betriebszeit_anlegen(
    db: Session,
    *,
    admin_user_id: uuid.UUID | None,
    wochentag: int,
    oeffnet: time,
    schliesst: time,
    gueltig_von: date | None,
    gueltig_bis: date | None,
) -> Betriebszeit:
    _pruefe_zeiten(oeffnet, schliesst)
    return _anlegen(
        db,
        "betriebszeit",
        Betriebszeit(
            wochentag=wochentag,
            oeffnet=oeffnet,
            schliesst=schliesst,
            gueltig_von=gueltig_von,
            gueltig_bis=gueltig_bis,
        ),
        admin_user_id,
    )


def betriebszeit_loeschen(
    db: Session, bz: Betriebszeit, *, admin_user_id: uuid.UUID | None
) -> None:
    _loeschen(db, "betriebszeit", bz, admin_user_id)


def ausnahmetag_anlegen(
    db: Session,
    *,
    admin_user_id: uuid.UUID | None,
    datum: date,
    geschlossen: bool,
    oeffnet: time | None,
    schliesst: time | None,
    grund: str,
) -> Ausnahmetag:
    if not geschlossen:
        if oeffnet is None or schliesst is None:
            raise StammdatenFehler("Sonderöffnung braucht Öffnungs- und Schließzeit")
        _pruefe_zeiten(oeffnet, schliesst)
    return _anlegen(
        db,
        "ausnahmetag",
        Ausnahmetag(
            datum=datum, geschlossen=geschlossen, oeffnet=oeffnet, schliesst=schliesst, grund=grund
        ),
        admin_user_id,
    )


def ausnahmetag_loeschen(db: Session, a: Ausnahmetag, *, admin_user_id: uuid.UUID | None) -> None:
    _loeschen(db, "ausnahmetag", a, admin_user_id)


def kundengruppe_anlegen(
    db: Session, *, admin_user_id: uuid.UUID | None, name: str, standard_zahlungsart: str
) -> Kundengruppe:
    if standard_zahlungsart not in ("online", "rechnung"):
        raise StammdatenFehler("Zahlungsart ungültig")
    return _anlegen(
        db,
        "kundengruppe",
        Kundengruppe(name=name.strip(), standard_zahlungsart=standard_zahlungsart),
        admin_user_id,
    )


def kundengruppe_aendern(
    db: Session, g: Kundengruppe, *, admin_user_id: uuid.UUID | None, **felder: Any
) -> Kundengruppe:
    return _aendern(db, "kundengruppe", g, admin_user_id, **felder)


def tarif_anlegen(
    db: Session, *, admin_user_id: uuid.UUID | None, name: str, preis: Decimal, **kriterien: Any
) -> Tarif:
    if preis < 0:
        raise StammdatenFehler("Preis darf nicht negativ sein")
    if (kriterien.get("uhrzeit_von") is None) != (kriterien.get("uhrzeit_bis") is None):
        raise StammdatenFehler("Uhrzeit von und bis gemeinsam angeben")
    return _anlegen(db, "tarif", Tarif(name=name.strip(), preis=preis, **kriterien), admin_user_id)


def tarif_aendern(
    db: Session, t: Tarif, *, admin_user_id: uuid.UUID | None, **felder: Any
) -> Tarif:
    return _aendern(db, "tarif", t, admin_user_id, **felder)


def tarif_deaktivieren(db: Session, t: Tarif, *, admin_user_id: uuid.UUID | None) -> Tarif:
    return _aendern(db, "tarif", t, admin_user_id, aktiv=False)
