import csv
import io
import uuid
from calendar import monthrange
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from beachhub_shared.zeit import lokal, lokales_datum
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
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
NULL = Decimal("0.00")


@dataclass(frozen=True)
class Posten:
    """Eine Rechnungsposition vor dem Anlegen. Netto und Steuer rechnet `_neue_rechnung` je
    Position (A-RECH-8)."""

    buchung: Buchung | None
    text: str
    brutto: Decimal
    ust_satz: Decimal


@dataclass(frozen=True)
class SteuerZeile:
    ust_satz: Decimal
    netto: Decimal
    ust: Decimal
    brutto: Decimal


class RechnungsFehler(Exception):  # noqa: N818
    pass


def naechste_nummer(db: Session, jahr: int) -> str:
    db.execute(insert(Nummernkreis).values(jahr=jahr, letzte_nummer=0).on_conflict_do_nothing())
    kreis = db.scalar(select(Nummernkreis).where(Nummernkreis.jahr == jahr).with_for_update())
    if kreis is None:
        raise RuntimeError("Nummernkreis konnte nicht angelegt werden")
    kreis.letzte_nummer += 1
    db.flush()
    return f"{jahr}-{kreis.letzte_nummer:05d}"


def netto_ust(brutto: Decimal, satz: Decimal) -> tuple[Decimal, Decimal]:
    netto = (brutto / (1 + satz / 100)).quantize(CENT, rounding=ROUND_HALF_UP)
    return netto, brutto - netto


def steuer_je_satz(r: Rechnung) -> list[SteuerZeile]:
    """Netto, Steuer und Brutto je Steuersatz, aufsteigend nach Satz – für PDF, Detailseite und
    CSV-Export. Summiert die je Position gerundeten Beträge."""
    summen: dict[Decimal, tuple[Decimal, Decimal, Decimal]] = {}
    for p in r.positionen:
        netto, ust, brutto = summen.get(p.ust_satz, (NULL, NULL, NULL))
        summen[p.ust_satz] = (netto + p.netto, ust + p.ust, brutto + p.brutto)
    return [SteuerZeile(satz, *werte) for satz, werte in sorted(summen.items())]


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
    posten: Sequence[Posten],
    leistung_von: date,
    leistung_bis: date,
    status: str,
    *,
    quelle: str,
) -> Rechnung:
    heute = clock.today(db)
    zeilen = [(p, *netto_ust(p.brutto, p.ust_satz)) for p in posten]
    r = Rechnung(
        nummer=naechste_nummer(db, heute.year),
        kunde_id=kunde.id,
        art=art,
        datum=heute,
        leistung_von=leistung_von,
        leistung_bis=leistung_bis,
        faellig_am=heute + timedelta(days=konfiguration.hole(db, "rechnung_zahlungsziel_tage")),
        netto=sum((netto for _, netto, _ in zeilen), NULL),
        ust=sum((ust for _, _, ust in zeilen), NULL),
        brutto=sum((p.brutto for p in posten), NULL),
        status=status,
        bezahlt_am=utcnow() if status == "bezahlt" else None,
        adresse_snapshot=_snapshot(kunde),
    )
    db.add(r)
    db.flush()
    for i, (p, netto, ust) in enumerate(zeilen, start=1):
        pos = RechnungPosition(
            rechnung_id=r.id,
            reihenfolge=i,
            buchung_id=p.buchung.id if p.buchung else None,
            text=p.text,
            menge=1,
            einzelpreis_brutto=p.brutto,
            ust_satz=p.ust_satz,
            netto=netto,
            ust=ust,
            brutto=p.brutto,
        )
        db.add(pos)
        db.flush()
        if p.buchung is not None and art != "storno":
            p.buchung.rechnung_position_id = pos.id
    db.flush()
    db.refresh(r)
    audit.protokolliere(
        db,
        quelle=quelle,
        objekt_typ="rechnung",
        objekt_id=r.id,
        vorher=None,
        nachher=audit.als_dict(r),
    )
    from beachhub_core.services import lesestand

    lesestand.markiere_geaendert(db, f"konto:{kunde.id}")
    return r


def erzeuge_einzelrechnung(db: Session, buchung: Buchung, *, quelle: str = "system") -> Rechnung:
    if buchung.rechnung_position_id is not None:
        raise RechnungsFehler("bereits_berechnet")
    d = lokales_datum(buchung.beginn)
    return _neue_rechnung(
        db,
        buchung.kunde,
        "einzel",
        [Posten(buchung, _positionstext(buchung), buchung.preis, buchung.ust_satz)],
        d,
        d,
        "bezahlt",
        quelle=quelle,
    )


# Zahlungsarten, die der Monatslauf sammelt. Plan 1a-II ersetzt den Monatslauf durch die
# Saisonrechnung (A-RECH-3).
MONATSLAUF_ZAHLUNGSARTEN: tuple[str, ...] = ("saison", "manuell")


def abrechenbare_buchungen(
    db: Session, kunde: Kunde, jahr: int, monat: int
) -> list[tuple[Buchung, Decimal]]:
    von, bis = date(jahr, monat, 1), date(jahr, monat, monthrange(jahr, monat)[1])
    kandidaten = db.scalars(
        select(Buchung)
        .where(
            Buchung.kunde_id == kunde.id,
            Buchung.zahlungsart.in_(MONATSLAUF_ZAHLUNGSARTEN),
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
            out.append((b, b.preis))
        else:
            out.append((b, b.preis))
    return out


def erzeuge_sammelrechnung(db: Session, kunde: Kunde, jahr: int, monat: int) -> Rechnung | None:
    db.execute(select(Kunde).where(Kunde.id == kunde.id).with_for_update())
    posten = abrechenbare_buchungen(db, kunde, jahr, monat)
    if not posten:
        return None
    positionen = [
        Posten(
            b,
            _positionstext(b, " (Storno nach Frist)" if b.status == Buchung.STORNIERT else ""),
            betrag,
            b.ust_satz,
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
        quelle="system",
    )


def monatslauf(db: Session, jahr: int, monat: int) -> list[Rechnung]:
    offen = select(Buchung.kunde_id).where(
        Buchung.zahlungsart.in_(MONATSLAUF_ZAHLUNGSARTEN),
        Buchung.rechnung_position_id.is_(None),
    )
    erzeugt = []
    for kunde in db.scalars(
        select(Kunde)
        .where(Kunde.id.in_(offen), Kunde.anonymisiert_am.is_(None))
        .order_by(Kunde.name)
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
    from beachhub_core.services import lesestand

    lesestand.markiere_geaendert(db, f"konto:{rechnung.kunde_id}")


def storniere(
    db: Session, rechnung: Rechnung, *, admin_user_id: uuid.UUID | None, grund: str
) -> Rechnung:
    if rechnung.status == "storniert" or rechnung.art == "storno":
        raise RechnungsFehler("nicht_stornierbar")
    positionen = [
        Posten(None, f"Storno zu Rechnung {rechnung.nummer}: {p.text}", -p.brutto, p.ust_satz)
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
        quelle="admin",
    )
    vorher = audit.als_dict(rechnung)
    rechnung.status, rechnung.storniert_durch_id = "storniert", s.id
    for p in rechnung.positionen:
        if p.buchung_id:
            b = db.get(Buchung, p.buchung_id)
            if b is not None:
                b.rechnung_position_id = None
            p.buchung_id = None
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
    from beachhub_core.services import lesestand

    lesestand.markiere_geaendert(db, f"konto:{rechnung.kunde_id}")
    return s


def _de(v: Decimal) -> str:
    return f"{v:.2f}".replace(".", ",")


def _csv_sicher(wert: str) -> str:
    """Verhindert CSV-/Formel-Injection (Excel & Co. interpretieren Zellen, die mit
    =, +, - oder @ beginnen, als Formel): eine führende einzelne Anführung entschärft das,
    ohne den sichtbaren Wert zu verändern."""
    return f"'{wert}" if wert.startswith(("=", "+", "-", "@")) else wert


def csv_export(db: Session, von: date, bis: date) -> str:
    """Eine Zeile je Rechnung und Steuersatz, damit die Buchhaltung beide Sätze getrennt erhält."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow(
        [
            "nummer",
            "datum",
            "kunde",
            "art",
            "status",
            "ust_satz",
            "netto",
            "ust",
            "brutto",
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
        for z in steuer_je_satz(r):
            w.writerow(
                [
                    r.nummer,
                    r.datum.isoformat(),
                    _csv_sicher(r.adresse_snapshot.get("name", "")),
                    r.art,
                    r.status,
                    _de(z.ust_satz),
                    _de(z.netto),
                    _de(z.ust),
                    _de(z.brutto),
                    r.faellig_am.isoformat(),
                    r.bezahlt_am.isoformat() if r.bezahlt_am else "",
                    r.leistung_von.isoformat(),
                    r.leistung_bis.isoformat(),
                ]
            )
    return buf.getvalue()
