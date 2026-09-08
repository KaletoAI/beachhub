import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

from beachhub_shared.slots import zeitraum_ist_slotfolge
from beachhub_shared.zeit import lokales_datum
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from beachhub_core import clock
from beachhub_core.models import Buchung, Feld, Kunde, Sperre
from beachhub_core.services import audit, konfiguration, pin, slots_db, tarife

NACH_ANLAGE: list[Callable[[Session, Buchung], None]] = []


class BuchungsFehler(Exception):  # noqa: N818 – Name wird von Task 9-11 als BuchungsFehler erwartet
    def __init__(self, grund: str) -> None:
        super().__init__(grund)
        self.grund = grund


def finde_kollisionen(
    db: Session,
    *,
    feld_id: uuid.UUID,
    beginn: datetime,
    ende: datetime,
    ausser_buchung_id: uuid.UUID | None = None,
) -> list[Buchung | Sperre]:
    q = select(Buchung).where(
        Buchung.feld_id == feld_id,
        Buchung.status.in_(Buchung.AKTIVE_STATUS),
        Buchung.beginn < ende,
        Buchung.ende > beginn,
    )
    if ausser_buchung_id:
        q = q.where(Buchung.id != ausser_buchung_id)
    b: list[Buchung | Sperre] = list(db.scalars(q).all())
    s = db.scalars(
        select(Sperre).where(
            or_(Sperre.feld_id == feld_id, Sperre.feld_id.is_(None)),
            Sperre.beginn < ende,
            Sperre.ende > beginn,
        )
    ).all()
    return b + list(s)


def _pruefe_zeitraum(
    db: Session, feld: Feld, beginn: datetime, ende: datetime, pruefe_fenster: bool
) -> None:
    jetzt = clock.now(db)
    if beginn <= jetzt:
        raise BuchungsFehler("vergangenheit")
    if ende <= beginn or lokales_datum(beginn) != lokales_datum(ende - timedelta(seconds=1)):
        raise BuchungsFehler("ausserhalb_betriebszeit")
    if not zeitraum_ist_slotfolge(
        beginn, ende, slots_db.tages_slots(db, feld, lokales_datum(beginn))
    ):
        raise BuchungsFehler("ausserhalb_betriebszeit")
    if pruefe_fenster:
        fenster = timedelta(days=konfiguration.hole(db, "fenster_tage"))
        vorlauf = timedelta(minutes=konfiguration.hole(db, "mindestvorlauf_minuten"))
        if beginn > jetzt + fenster or beginn < jetzt + vorlauf:
            raise BuchungsFehler("ausserhalb_fenster")


def lege_an(
    db: Session,
    *,
    feld_id: uuid.UUID,
    kunde_id: uuid.UUID,
    beginn: datetime,
    ende: datetime,
    quelle: str = "admin",
    admin_user_id: uuid.UUID | None = None,
    pruefe_fenster: bool = False,
    status: str = Buchung.BESTAETIGT,
    anfrage_id: uuid.UUID | None = None,
    dauerbuchung_id: uuid.UUID | None = None,
    pin_klar: str | None = None,
) -> Buchung:
    feld = db.scalar(select(Feld).where(Feld.id == feld_id).with_for_update())
    if feld is None or not feld.aktiv:
        raise BuchungsFehler("feld_inaktiv")
    kunde = db.get(Kunde, kunde_id)
    if kunde is None or kunde.anonymisiert_am is not None:
        raise BuchungsFehler("kunde_unbekannt")
    _pruefe_zeitraum(db, feld, beginn, ende, pruefe_fenster)
    if finde_kollisionen(db, feld_id=feld_id, beginn=beginn, ende=ende):
        raise BuchungsFehler("belegt")
    preis = tarife.ermittle_preis(
        db, feld_id=feld_id, beginn=beginn, ende=ende, kundengruppe_id=kunde.kundengruppe_id
    )
    if preis is None:
        raise BuchungsFehler("kein_tarif")
    if pin_klar is None:
        pin_klar = pin.finde_freien(
            db,
            beginn,
            ende,
            konfiguration.hole(db, "pin_laenge"),
            konfiguration.hole(db, "zutritt_vorlauf_minuten"),
        )
    b = Buchung(
        feld_id=feld_id,
        kunde_id=kunde_id,
        beginn=beginn,
        ende=ende,
        status=status,
        preis=preis,
        zahlungsart=kunde.zahlungsart,
        pin_hash=pin.hash(pin_klar),
        pin_verschluesselt=pin.verschluessele(pin_klar),
        anfrage_id=anfrage_id,
        dauerbuchung_id=dauerbuchung_id,
        quelle=quelle,
    )
    db.add(b)
    db.flush()
    audit.protokolliere(
        db,
        quelle=quelle,
        objekt_typ="buchung",
        objekt_id=b.id,
        vorher=None,
        nachher=audit.als_dict(b),
        admin_user_id=admin_user_id,
    )
    for hook in NACH_ANLAGE:
        hook(db, b)
    return b


def setze_status(
    db: Session, buchung: Buchung, neu: str, *, quelle: str, admin_user_id: uuid.UUID | None = None
) -> None:
    vorher = audit.als_dict(buchung)
    buchung.status = neu
    db.flush()
    audit.protokolliere(
        db,
        quelle=quelle,
        objekt_typ="buchung",
        objekt_id=buchung.id,
        vorher=vorher,
        nachher=audit.als_dict(buchung),
        admin_user_id=admin_user_id,
    )
