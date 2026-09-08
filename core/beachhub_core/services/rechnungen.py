import csv
import io
import uuid
from calendar import monthrange
from collections.abc import Sequence
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from beachhub_shared.zeit import lokal, lokales_datum
from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_core import clock
from beachhub_core.models import (
    Buchung,
    Kunde,
    Nummernkreis,
    Rechnung,
    RechnungPosition,
    Storno,
    utcnow,
)
from beachhub_core.services import audit, konfiguration

CENT = Decimal("0.01")


class RechnungsFehler(Exception):  # noqa: N818
    pass


def naechste_nummer(db: Session, jahr: int) -> str:
    kreis = db.scalar(select(Nummernkreis).where(Nummernkreis.jahr == jahr).with_for_update())
    if kreis is None:
        kreis = Nummernkreis(jahr=jahr, letzte_nummer=0)
        db.add(kreis)
        db.flush()
        kreis = db.scalar(select(Nummernkreis).where(Nummernkreis.jahr == jahr).with_for_update())
        assert kreis is not None
    kreis.letzte_nummer += 1
    db.flush()
    return f"{jahr}-{kreis.letzte_nummer:05d}"


def _netto_ust(brutto: Decimal, satz: Decimal) -> tuple[Decimal, Decimal]:
    netto = (brutto / (1 + satz / 100)).quantize(CENT, rounding=ROUND_HALF_UP)
    return netto, brutto - netto


def _snapshot(k: Kunde) -> dict[str, str]:
    return {
        "name": k.name,
        "strasse": k.adresse_strasse,
        "plz": k.adresse_plz,
        "ort": k.adresse_ort,
        "email": k.email,
    }


def _positionstext(b: Buchung, zusatz: str = "") -> str:
    lb, le = lokal(b.beginn), lokal(b.ende)
    return f"Feld {b.feld.name}, {lb:%d.%m.%Y} {lb:%H:%M}–{le:%H:%M} Uhr{zusatz}"


def _neue_rechnung(
    db: Session,
    kunde: Kunde,
    art: str,
    positionen: Sequence[tuple[Buchung | None, str, Decimal]],
    leistung_von: date,
    leistung_bis: date,
    status: str,
) -> Rechnung:
    heute = clock.today(db)
    satz = konfiguration.hole(db, "ust_satz")
    brutto = sum((p[2] for p in positionen), Decimal("0.00"))
    netto, ust = _netto_ust(brutto, satz)
    r = Rechnung(
        nummer=naechste_nummer(db, heute.year),
        kunde_id=kunde.id,
        art=art,
        datum=heute,
        leistung_von=leistung_von,
        leistung_bis=leistung_bis,
        faellig_am=heute + timedelta(days=konfiguration.hole(db, "rechnung_zahlungsziel_tage")),
        ust_satz=satz,
        netto=netto,
        ust=ust,
        brutto=brutto,
        status=status,
        bezahlt_am=utcnow() if status == "bezahlt" else None,
        adresse_snapshot=_snapshot(kunde),
    )
    db.add(r)
    db.flush()
    for i, (buchung, text, betrag) in enumerate(positionen, start=1):
        pos = RechnungPosition(
            rechnung_id=r.id,
            reihenfolge=i,
            buchung_id=buchung.id if buchung else None,
            text=text,
            menge=1,
            einzelpreis_brutto=betrag,
            ust_satz=satz,
            brutto=betrag,
        )
        db.add(pos)
        db.flush()
        if buchung is not None and art != "storno":
            buchung.rechnung_position_id = pos.id
    db.flush()
    db.refresh(r)
    audit.protokolliere(
        db,
        quelle="system",
        objekt_typ="rechnung",
        objekt_id=r.id,
        vorher=None,
        nachher=audit.als_dict(r),
    )
    return r


def erzeuge_einzelrechnung(db: Session, buchung: Buchung, *, quelle: str = "system") -> Rechnung:
    if buchung.rechnung_position_id is not None:
        raise RechnungsFehler("bereits_berechnet")
    d = lokales_datum(buchung.beginn)
    return _neue_rechnung(
        db,
        buchung.kunde,
        "einzel",
        [(buchung, _positionstext(buchung), buchung.preis)],
        d,
        d,
        "bezahlt",
    )


def abrechenbare_buchungen(
    db: Session, kunde: Kunde, jahr: int, monat: int
) -> list[tuple[Buchung, Decimal]]:
    von, bis = date(jahr, monat, 1), date(jahr, monat, monthrange(jahr, monat)[1])
    kandidaten = db.scalars(
        select(Buchung)
        .where(
            Buchung.kunde_id == kunde.id,
            Buchung.zahlungsart == "rechnung",
            Buchung.rechnung_position_id.is_(None),
            Buchung.status.in_(
                (
                    Buchung.BESTAETIGT,
                    Buchung.DURCHGEFUEHRT,
                    Buchung.NICHT_ERSCHIENEN,
                    Buchung.STORNIERT,
                )
            ),
        )
        .order_by(Buchung.beginn)
    ).all()
    out: list[tuple[Buchung, Decimal]] = []
    for b in kandidaten:
        if not (von <= lokales_datum(b.beginn) <= bis):
            continue
        if b.status == Buchung.STORNIERT:
            s = db.scalar(select(Storno).where(Storno.buchung_id == b.id))
            if s is None or s.kostenfrei:
                continue
            rest = b.preis - s.freigestellt_betrag
            if rest > 0:
                out.append((b, rest))
        else:
            out.append((b, b.preis))
    return out


def erzeuge_sammelrechnung(db: Session, kunde: Kunde, jahr: int, monat: int) -> Rechnung | None:
    posten = abrechenbare_buchungen(db, kunde, jahr, monat)
    if not posten:
        return None
    positionen = [
        (
            b,
            _positionstext(b, " (Storno nach Frist)" if b.status == Buchung.STORNIERT else ""),
            betrag,
        )
        for b, betrag in posten
    ]
    return _neue_rechnung(
        db,
        kunde,
        "sammel",
        positionen,
        date(jahr, monat, 1),
        date(jahr, monat, monthrange(jahr, monat)[1]),
        "offen",
    )


def monatslauf(db: Session, jahr: int, monat: int) -> list[Rechnung]:
    erzeugt = []
    for kunde in db.scalars(
        select(Kunde).where(Kunde.zahlungsart == "rechnung", Kunde.anonymisiert_am.is_(None))
    ).all():
        r = erzeuge_sammelrechnung(db, kunde, jahr, monat)
        if r:
            erzeugt.append(r)
    return erzeugt


def setze_bezahlt(db: Session, rechnung: Rechnung, *, admin_user_id: uuid.UUID | None) -> None:
    if rechnung.status != "offen":
        raise RechnungsFehler("nicht_offen")
    vorher = audit.als_dict(rechnung)
    rechnung.status, rechnung.bezahlt_am = "bezahlt", utcnow()
    db.flush()
    audit.protokolliere(
        db,
        quelle="admin",
        objekt_typ="rechnung",
        objekt_id=rechnung.id,
        vorher=vorher,
        nachher=audit.als_dict(rechnung),
        admin_user_id=admin_user_id,
    )


def storniere(
    db: Session, rechnung: Rechnung, *, admin_user_id: uuid.UUID | None, grund: str
) -> Rechnung:
    if rechnung.status == "storniert" or rechnung.art == "storno":
        raise RechnungsFehler("nicht_stornierbar")
    positionen = [
        (None, f"Storno zu Rechnung {rechnung.nummer}: {p.text}", -p.brutto)
        for p in rechnung.positionen
    ]
    s = _neue_rechnung(
        db,
        rechnung.kunde,
        "storno",
        positionen,
        rechnung.leistung_von,
        rechnung.leistung_bis,
        "bezahlt",
    )
    vorher = audit.als_dict(rechnung)
    rechnung.status, rechnung.storniert_durch_id = "storniert", s.id
    for p in rechnung.positionen:
        if p.buchung_id:
            b = db.get(Buchung, p.buchung_id)
            if b is not None:
                b.rechnung_position_id = None
    db.flush()
    audit.protokolliere(
        db,
        quelle="admin",
        objekt_typ="rechnung",
        objekt_id=rechnung.id,
        vorher=vorher,
        nachher={**audit.als_dict(rechnung), "grund": grund},
        admin_user_id=admin_user_id,
    )
    return s


def _de(v: Decimal) -> str:
    return f"{v:.2f}".replace(".", ",")


def csv_export(db: Session, von: date, bis: date) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow(
        [
            "nummer",
            "datum",
            "kunde",
            "art",
            "status",
            "netto",
            "ust",
            "brutto",
            "ust_satz",
            "faellig_am",
            "bezahlt_am",
            "leistung_von",
            "leistung_bis",
        ]
    )
    for r in db.scalars(
        select(Rechnung)
        .where(Rechnung.datum >= von, Rechnung.datum <= bis)
        .order_by(Rechnung.nummer)
    ):
        w.writerow(
            [
                r.nummer,
                r.datum.isoformat(),
                r.adresse_snapshot.get("name", ""),
                r.art,
                r.status,
                _de(r.netto),
                _de(r.ust),
                _de(r.brutto),
                _de(r.ust_satz),
                r.faellig_am.isoformat(),
                r.bezahlt_am.isoformat() if r.bezahlt_am else "",
                r.leistung_von.isoformat(),
                r.leistung_bis.isoformat(),
            ]
        )
    return buf.getvalue()
