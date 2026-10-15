import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from beachhub_shared.zeit import kombiniere
from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_core.models import (
    Buchung,
    Dauerbuchung,
    Kunde,
    Rechnung,
    Sperre,
    Storno,
    utcnow,
)
from beachhub_core.services import (
    audit,
    buchungen,
    gutschein_sperren,
    konfiguration,
    kunden,
    kundengruppen,
    pin,
    rechnungen,
    sperren,
    tarife,
)


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
                    db,
                    feld_id=feld_id,
                    beginn=b,
                    ende=e,
                    kundengruppe_id=kundengruppen.effektive_gruppe(db, kunde, d).id,
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
    rechnungskunde_setzen: bool = False,
    stornos: list[Storno] | None = None,
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
    # Neue Saisonrechnung und verdrängte Buchungen teilen den Nummernkreis:
    # alle beteiligten Kunden gemeinsam vor der ersten Korrektur sperren.
    sperrkontext = gutschein_sperren.bereite_buchungen_vor(
        db,
        {k.id for t in termine for k in t.kollisionen if isinstance(k, Buchung)},
        kunde_ids=[kunde_id],
    )
    kunde = db.get(Kunde, kunde_id)
    assert kunde is not None  # plane() hat den Kunden geprüft
    # Die Identity Map kann einen alten Stand halten: unter der vollständigen
    # Kundensperre frisch prüfen, bevor Kunden, Buchungen oder Rechnungen entstehen.
    db.refresh(kunde)
    if not kunde.rechnungskunde and not rechnungskunde_setzen:
        raise DauerbuchungsFehler("kein_rechnungskunde")
    if konfiguration.hole(db, "abo_nur_mitglieder") and not kundengruppen.ist_mitglied_am(
        kunde, termine[-1].datum
    ):
        raise DauerbuchungsFehler("mitgliedschaft_zu_kurz")
    if not kunde.rechnungskunde:
        kunden.aendere(db, kunde, admin_user_id=admin_user_id, rechnungskunde=True)
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
            s = sperren.STORNIERE(
                db,
                k,
                durch="betreiber",
                kostenfrei=True,
                grund="Dauerbuchung",
                sperrkontext=sperrkontext,
                admin_user_id=admin_user_id,
            )
            if stornos is not None:
                stornos.append(s)
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
            zahlungsart="saison",
        )
    db.flush()
    db.refresh(dauer)
    rechnungen.erzeuge_saisonrechnung(db, dauer, quelle="admin", admin_user_id=admin_user_id)
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


def beende(
    db: Session,
    dauer: Dauerbuchung,
    *,
    ab: date,
    admin_user_id: uuid.UUID | None,
    stornos: list[Storno] | None = None,
) -> list[Rechnung]:
    """Beendet eine Dauerbuchung ab einem Datum: künftige Termine werden kostenfrei storniert
    (A-DAUER-4) und die Saisonrechnung mit einem Korrekturbeleg über alle betroffenen Termine
    korrigiert (A-RECH-7). Liefert die Belege für den Versand nach dem Commit; `stornos`
    sammelt die Stornos für die Stornobestätigung."""
    from beachhub_core.services import storno

    grenze = kombiniere(ab, time(0, 0))
    kandidaten_abfrage = select(Buchung.id).where(
        Buchung.dauerbuchung_id == dauer.id,
        Buchung.beginn >= grenze,
        Buchung.status.in_(Buchung.AKTIVE_STATUS),
    )
    kandidaten = set(db.scalars(kandidaten_abfrage))
    sperrkontext = gutschein_sperren.bereite_buchungen_vor(
        db, kandidaten, kunde_ids=[dauer.kunde_id]
    )
    # Nach Warten auf Kunden dürfen frisch hinzugekommene Termine mit anderen
    # Gutscheinen nicht nach der ersten Dauer-/Belegsperre vorbereitet werden.
    if not set(db.scalars(kandidaten_abfrage)) <= kandidaten:
        raise gutschein_sperren.GutscheinVorbereitungWiederholen("Dauertermine inzwischen ergänzt")
    db.execute(
        select(Dauerbuchung)
        .where(Dauerbuchung.id == dauer.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    db.scalars(
        select(Buchung)
        .where(Buchung.dauerbuchung_id == dauer.id)
        .execution_options(populate_existing=True)
    ).all()
    db.expire(dauer, ["buchungen"])
    vorher = audit.als_dict(dauer)
    betroffen = [b for b in dauer.buchungen if b.beginn >= grenze and b.aktiv]
    for b in betroffen:
        s = storno.storniere(
            db,
            b,
            durch="betreiber",
            kostenfrei=True,
            grund="Dauerbuchung beendet",
            admin_user_id=admin_user_id,
            korrigieren=False,
            sperrkontext=sperrkontext,
        )
        if stornos is not None:
            stornos.append(s)
    belege = storno.gutschreiben_alle(
        db,
        betroffen,
        grund="Dauerbuchung beendet",
        quelle="admin",
        admin_user_id=admin_user_id,
        sperrkontext=sperrkontext,
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
    return belege
