import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from beachhub_shared.zeit import kombiniere
from sqlalchemy.orm import Session

from beachhub_core.models import Buchung, Dauerbuchung, Kunde, Sperre, utcnow
from beachhub_core.services import audit, buchungen, konfiguration, pin, sperren, tarife


class DauerbuchungsFehler(Exception):  # noqa: N818
    pass


@dataclass
class Termin:
    datum: date
    beginn: datetime
    ende: datetime
    kollisionen: list[Buchung | Sperre]
    preis: Decimal | None


def _termine(wochentag: int, von: date, bis: date) -> list[date]:
    d = von + timedelta(days=(wochentag - von.weekday()) % 7)
    out = []
    while d <= bis:
        out.append(d)
        d += timedelta(days=7)
    return out


def plane(
    db: Session,
    *,
    kunde_id: uuid.UUID,
    feld_id: uuid.UUID,
    wochentag: int,
    start: time,
    ende: time,
    gueltig_von: date,
    gueltig_bis: date,
) -> list[Termin]:
    kunde = db.get(Kunde, kunde_id)
    if kunde is None:
        raise DauerbuchungsFehler("kunde_unbekannt")
    out: list[Termin] = []
    for d in _termine(wochentag, gueltig_von, gueltig_bis):
        b, e = kombiniere(d, start), kombiniere(d, ende)
        out.append(
            Termin(
                datum=d,
                beginn=b,
                ende=e,
                kollisionen=buchungen.finde_kollisionen(db, feld_id=feld_id, beginn=b, ende=e),
                preis=tarife.ermittle_preis(
                    db, feld_id=feld_id, beginn=b, ende=e, kundengruppe_id=kunde.kundengruppe_id
                ),
            )
        )
    return out


def lege_an(
    db: Session,
    *,
    kunde_id: uuid.UUID,
    feld_id: uuid.UUID,
    wochentag: int,
    start: time,
    ende: time,
    gueltig_von: date,
    gueltig_bis: date,
    admin_user_id: uuid.UUID | None,
    auslassen: set[date],
    entscheidungen: dict[uuid.UUID, str],
) -> Dauerbuchung:
    termine = [
        t
        for t in plane(
            db,
            kunde_id=kunde_id,
            feld_id=feld_id,
            wochentag=wochentag,
            start=start,
            ende=ende,
            gueltig_von=gueltig_von,
            gueltig_bis=gueltig_bis,
        )
        if t.datum not in auslassen
    ]
    for t in termine:
        if t.preis is None:
            raise DauerbuchungsFehler("kein_tarif")
        for k in t.kollisionen:
            if isinstance(k, Sperre):
                raise DauerbuchungsFehler("sperre")
            if entscheidungen.get(k.id) != "stornieren":
                raise DauerbuchungsFehler("entscheidung_fehlt")
    if not termine:
        raise DauerbuchungsFehler("keine_termine")
    pin_klar = pin.finde_freien(
        db,
        termine[0].beginn,
        termine[-1].ende,
        konfiguration.hole(db, "pin_laenge"),
        konfiguration.hole(db, "zutritt_vorlauf_minuten"),
    )
    dauer = Dauerbuchung(
        kunde_id=kunde_id,
        feld_id=feld_id,
        wochentag=wochentag,
        start=start,
        ende=ende,
        gueltig_von=gueltig_von,
        gueltig_bis=gueltig_bis,
        pin_hash=pin.hash(pin_klar),
        pin_verschluesselt=pin.verschluessele(pin_klar),
    )
    db.add(dauer)
    db.flush()
    for t in termine:
        for k in t.kollisionen:
            sperren.STORNIERE(
                db,
                k,
                durch="betreiber",
                kostenfrei=True,
                grund="Dauerbuchung",
                admin_user_id=admin_user_id,
            )
        buchungen.lege_an(
            db,
            feld_id=feld_id,
            kunde_id=kunde_id,
            beginn=t.beginn,
            ende=t.ende,
            quelle="dauer",
            admin_user_id=admin_user_id,
            dauerbuchung_id=dauer.id,
            pin_klar=pin_klar,
        )
    db.flush()
    db.refresh(dauer)
    audit.protokolliere(
        db,
        quelle="admin",
        objekt_typ="dauerbuchung",
        objekt_id=dauer.id,
        vorher=None,
        nachher=audit.als_dict(dauer),
        admin_user_id=admin_user_id,
    )
    return dauer


def beende(db: Session, dauer: Dauerbuchung, *, ab: date, admin_user_id: uuid.UUID | None) -> None:
    grenze = kombiniere(ab, time(0, 0))
    vorher = audit.als_dict(dauer)
    for b in dauer.buchungen:
        if b.beginn >= grenze and b.aktiv:
            sperren.STORNIERE(
                db,
                b,
                durch="betreiber",
                kostenfrei=True,
                grund="Dauerbuchung beendet",
                admin_user_id=admin_user_id,
            )
    dauer.beendet_am = utcnow()
    dauer.beendet_ab = ab
    db.flush()
    audit.protokolliere(
        db,
        quelle="admin",
        objekt_typ="dauerbuchung",
        objekt_id=dauer.id,
        vorher=vorher,
        nachher=audit.als_dict(dauer),
        admin_user_id=admin_user_id,
    )
