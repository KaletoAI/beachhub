import csv
import io
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from beachhub_shared.zeit import lokal, lokales_datum
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload

from beachhub_core import clock
from beachhub_core.models import (
    Buchung,
    Dauerbuchung,
    Kunde,
    Nummernkreis,
    Rechnung,
    RechnungPosition,
    Zahlung,
    utcnow,
)
from beachhub_core.services import audit, guthaben, konfiguration, kunden

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


def verrechnet(db: Session, r: Rechnung) -> Decimal:
    """Auf die Rechnung verbuchte Zahlungen, etwa verrechnetes Guthaben (A-ZAHL-4)."""
    summe = db.scalar(
        select(func.coalesce(func.sum(Zahlung.betrag), 0)).where(
            Zahlung.rechnung_id == r.id, Zahlung.status == Zahlung.BEZAHLT
        )
    )
    return Decimal(str(summe)).quantize(CENT)


def korrektursumme(db: Session, r: Rechnung) -> Decimal:
    """Negative Bruttosumme der zur Rechnung gehörenden Korrekturbelege."""
    summe = db.scalar(
        select(func.coalesce(func.sum(Rechnung.brutto), 0)).where(
            Rechnung.korrigiert_rechnung_id == r.id
        )
    )
    return Decimal(str(summe)).quantize(CENT)


def offener_betrag(db: Session, r: Rechnung) -> Decimal:
    """Was der Kunde auf eine offene Rechnung noch zahlen muss: Brutto abzüglich Korrekturen und
    verrechneter Zahlungen. Bezahlte und stornierte Rechnungen sowie Stornorechnungen sind nie
    offen; den Zahlungseingang per Überweisung hakt der Betreiber ab (A-ZAHL-7)."""
    if r.art == "storno" or r.status != "offen":
        return NULL
    offen = r.brutto + korrektursumme(db, r) - verrechnet(db, r)
    return max(NULL, offen.quantize(CENT))


def offene_betraege(db: Session, rechnungen: Sequence[Rechnung]) -> dict[uuid.UUID, Decimal]:
    """Restforderungen einer Liste mit zwei Aggregaten statt Abfragen je Zeile."""
    ergebnis = {r.id: NULL for r in rechnungen}
    ids = {r.id for r in rechnungen if r.status == "offen" and r.art != "storno"}
    if not ids:
        return ergebnis
    korrekturen = {
        rechnung_id: betrag
        for rechnung_id, betrag in db.execute(
            select(Rechnung.korrigiert_rechnung_id, func.sum(Rechnung.brutto))
            .where(Rechnung.korrigiert_rechnung_id.in_(ids))
            .group_by(Rechnung.korrigiert_rechnung_id)
        )
    }
    zahlungen = {
        rechnung_id: betrag
        for rechnung_id, betrag in db.execute(
            select(Zahlung.rechnung_id, func.sum(Zahlung.betrag))
            .where(Zahlung.rechnung_id.in_(ids), Zahlung.status == Zahlung.BEZAHLT)
            .group_by(Zahlung.rechnung_id)
        )
    }
    for r in rechnungen:
        if r.id in ids:
            rest = (
                r.brutto
                + Decimal(str(korrekturen.get(r.id, NULL)))
                - Decimal(str(zahlungen.get(r.id, NULL)))
            )
            ergebnis[r.id] = max(NULL, rest.quantize(CENT))
    return ergebnis


def sperre(db: Session, rechnung: Rechnung) -> None:
    """Schreibzugriffe: zuerst Kunde, dann Rechnung; Salden und Marker frisch lesen.

    Dieselbe Reihenfolge gilt für Guthabenverrechnung und Korrekturen. Die Sperren
    bleiben bis zum Commit/Rollback bestehen, auch während der Guthabenberechnung.
    """
    kunden.sperre_mehrere(db, [rechnung.kunde_id])
    db.execute(
        select(Rechnung)
        .where(Rechnung.id == rechnung.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    db.scalars(
        select(RechnungPosition)
        .where(RechnungPosition.rechnung_id == rechnung.id)
        .execution_options(populate_existing=True)
    ).all()
    db.expire(rechnung, ["positionen"])


def _aktualisiere_bezahlt(db: Session, rechnung: Rechnung) -> bool:
    """Deckung nach Zahlung/Korrektur synchronisieren; der Aufrufer auditiert den Wechsel."""
    if rechnung.status != "offen" or rechnung.art == "storno":
        return False
    if offener_betrag(db, rechnung) != NULL:
        return False
    rechnung.status = "bezahlt"
    rechnung.bezahlt_am = rechnung.bezahlt_am or utcnow()
    return True


def korrigiere(
    db: Session,
    positionen: Sequence[RechnungPosition],
    *,
    grund: str,
    quelle: str,
    admin_user_id: uuid.UUID | None = None,
) -> Rechnung:
    """(Teil-)Stornorechnung über genau diese Positionen einer Rechnung (A-RECH-7). Die
    ursprüngliche Rechnung bleibt unverändert (A-RECH-4); sind danach alle ihre Positionen
    korrigiert, gilt sie als storniert (Abweichung B-4). Guthaben entsteht hier nicht – das
    entscheidet `storno.gutschreiben_positionen`."""
    if not positionen:
        raise RechnungsFehler("keine_positionen")
    positionen = list({p.id: p for p in positionen}.values())
    rechnung = positionen[0].rechnung
    sperre(db, rechnung)
    if rechnung.art == "storno" or rechnung.status == "storniert":
        raise RechnungsFehler("nicht_stornierbar")
    if any(p.rechnung_id != rechnung.id for p in positionen):
        raise RechnungsFehler("verschiedene_rechnungen")
    if any(p.korrigiert_durch_id is not None for p in positionen):
        raise RechnungsFehler("bereits_korrigiert")
    tage = [
        lokales_datum(b.beginn)
        for p in positionen
        if p.buchung_id is not None and (b := db.get(Buchung, p.buchung_id)) is not None
    ]
    von, bis = (min(tage), max(tage)) if tage else (rechnung.leistung_von, rechnung.leistung_bis)
    vorher = audit.als_dict(rechnung)
    beleg = _neue_rechnung(
        db,
        rechnung.kunde,
        "storno",
        [
            Posten(None, f"Storno zu Rechnung {rechnung.nummer}: {p.text}", -p.brutto, p.ust_satz)
            for p in positionen
        ],
        von,
        bis,
        "bezahlt",
        quelle=quelle,
    )
    beleg.korrigiert_rechnung_id = rechnung.id
    for p, gegen in zip(positionen, beleg.positionen, strict=True):
        p.korrigiert_durch_id = gegen.id
    if all(p.korrigiert_durch_id is not None for p in rechnung.positionen):
        rechnung.status = "storniert"
        rechnung.storniert_durch_id = beleg.id
    db.flush()
    _aktualisiere_bezahlt(db, rechnung)
    audit.protokolliere(
        db,
        quelle=quelle,
        objekt_typ="rechnung",
        objekt_id=rechnung.id,
        vorher=vorher,
        nachher={**audit.als_dict(rechnung), "korrekturbeleg": beleg.nummer, "grund": grund},
        admin_user_id=admin_user_id,
    )
    return beleg


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
    zahlungsziel_tage: int | None = None,
    dauerbuchung_id: uuid.UUID | None = None,
) -> Rechnung:
    heute = clock.today(db)
    ziel = (
        konfiguration.hole(db, "rechnung_zahlungsziel_tage")
        if zahlungsziel_tage is None
        else zahlungsziel_tage
    )
    zeilen = [(p, *netto_ust(p.brutto, p.ust_satz)) for p in posten]
    r = Rechnung(
        nummer=naechste_nummer(db, heute.year),
        kunde_id=kunde.id,
        dauerbuchung_id=dauerbuchung_id,
        art=art,
        datum=heute,
        leistung_von=leistung_von,
        leistung_bis=leistung_bis,
        faellig_am=heute + timedelta(days=ziel),
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


def erzeuge_einzelrechnung(
    db: Session, buchung: Buchung, *, quelle: str = "system", status: str = "bezahlt"
) -> Rechnung:
    """Online bezahlte Buchungen bekommen eine bezahlte Rechnung (A-RECH-2), Betreiberbuchungen
    eine offene mit Zahlungsziel (A-ZAHL-1)."""
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
        status,
        quelle=quelle,
    )


def verrechne_guthaben(db: Session, r: Rechnung, *, quelle: str) -> Decimal:
    """Verrechnet Guthaben des Kunden mit dem offenen Betrag – als Zahlung, nicht als
    Preisminderung (A-ZAHL-4). Deckt es alles, ist die Rechnung bezahlt. Einmal je Rechnung
    (provider_ref ist eindeutig)."""
    sperre(db, r)
    kunde = r.kunde
    db.refresh(kunde)
    if db.scalar(select(Zahlung.id).where(Zahlung.provider_ref == f"guthaben:{r.id}")):
        return NULL
    betrag = min(kunde.guthaben, offener_betrag(db, r))
    if betrag <= NULL:
        return NULL
    guthaben.buche(
        db,
        kunde=kunde,
        betrag=-betrag,
        art="verrechnung",
        bezug_id=r.id,
        notiz=f"Verrechnung mit Rechnung {r.nummer}",
        quelle=quelle,
    )
    db.add(
        Zahlung(
            kunde_id=kunde.id,
            rechnung_id=r.id,
            provider="guthaben",
            provider_ref=f"guthaben:{r.id}",
            betrag=betrag,
            status=Zahlung.BEZAHLT,
            empfangen_am=utcnow(),
        )
    )
    db.flush()
    vorher = audit.als_dict(r)
    if _aktualisiere_bezahlt(db, r):
        db.flush()
        audit.protokolliere(
            db,
            quelle=quelle,
            objekt_typ="rechnung",
            objekt_id=r.id,
            vorher=vorher,
            nachher={**audit.als_dict(r), "bezahlt_durch": "guthaben"},
        )
    return betrag


def saison_abrechenbar(buchung: Buchung) -> bool:
    """Unberechnet und aktiv oder nachweisbar kostenpflichtig abgesagt."""
    return buchung.rechnung_position_id is None and (
        buchung.aktiv
        or (
            buchung.status == Buchung.STORNIERT
            and buchung.storno is not None
            and not buchung.storno.kostenfrei
        )
    )


def erzeuge_saisonrechnung(db: Session, dauer: Dauerbuchung, *, quelle: str = "admin") -> Rechnung:
    """Vorausrechnung über die abrechenbaren unberechneten Termine einer Dauerbuchung
    (A-RECH-3): Leistungszeitraum erster bis letzter Termin, Zahlungsziel
    `saison_zahlungsziel_tage`. Ist es eingestellt, wird Guthaben sofort verrechnet (A-ZAHL-4).
    Neuausstellung nach Vollstorno erfolgt ausdrücklich, nie automatisch.
    Kommen später Termine hinzu, ist das eine neue Dauerbuchung mit eigener Saisonrechnung."""
    # Kundensperre vor Dauerbuchung und Nummernkreis; danach keine alten ORM-Marker nutzen.
    kunden.sperre_mehrere(db, [dauer.kunde_id])
    db.execute(
        select(Dauerbuchung)
        .where(Dauerbuchung.id == dauer.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    if (
        db.scalar(
            select(Rechnung.id).where(
                Rechnung.dauerbuchung_id == dauer.id,
                Rechnung.art == "saison",
                Rechnung.status != "storniert",
            )
        )
        is not None
    ):
        raise RechnungsFehler("bereits_berechnet")
    termine = db.scalars(
        select(Buchung)
        .where(Buchung.dauerbuchung_id == dauer.id)
        .options(selectinload(Buchung.storno))
        .order_by(Buchung.beginn)
        .execution_options(populate_existing=True)
    ).all()
    termine = [b for b in termine if saison_abrechenbar(b)]
    if not termine:
        raise RechnungsFehler("keine_termine")
    r = _neue_rechnung(
        db,
        dauer.kunde,
        "saison",
        [
            Posten(
                b,
                _positionstext(b, " (kostenpflichtig storniert)" if not b.aktiv else ""),
                b.preis,
                b.ust_satz,
            )
            for b in termine
        ],
        lokales_datum(termine[0].beginn),
        lokales_datum(termine[-1].beginn),
        "offen",
        quelle=quelle,
        zahlungsziel_tage=konfiguration.hole(db, "saison_zahlungsziel_tage"),
        dauerbuchung_id=dauer.id,
    )
    if konfiguration.hole(db, "guthaben_auf_saisonrechnung"):
        verrechne_guthaben(db, r, quelle=quelle)
    return r


def setze_bezahlt(db: Session, rechnung: Rechnung, *, admin_user_id: uuid.UUID | None) -> None:
    sperre(db, rechnung)
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
    """Voll-Storno zur Korrektur mit Neuausstellung (A-RECH-4): korrigiert alle noch nicht
    korrigierten Positionen und gibt die Buchungen zur Neuberechnung frei. Der verbleibende
    bezahlte Anteil wird mit Korrekturbeleg als Guthaben zurückgegeben."""
    sperre(db, rechnung)
    if rechnung.status == "storniert" or rechnung.art == "storno":
        raise RechnungsFehler("nicht_stornierbar")
    offen = [p for p in rechnung.positionen if p.korrigiert_durch_id is None]
    if not offen:
        raise RechnungsFehler("nicht_stornierbar")
    from beachhub_core.services import storno

    beleg = storno.gutschreiben_positionen(
        db, offen, grund=grund, quelle="admin", admin_user_id=admin_user_id
    )
    # Nur die vor diesem Vollstorno unberichtigten Positionen werden freigegeben.
    # Frühere Teilkorrekturen bleiben am aktiven Termin als Abrechnungsausschluss erhalten.
    for p in offen:
        if p.buchung_id:
            b = db.get(Buchung, p.buchung_id)
            if b is not None and b.rechnung_position_id == p.id:
                b.rechnung_position_id = None
            p.buchung_id = None
    db.flush()
    from beachhub_core.services import lesestand

    lesestand.markiere_geaendert(db, f"konto:{rechnung.kunde_id}")
    return beleg


def _de(v: Decimal) -> str:
    return f"{v:.2f}".replace(".", ",")


def csv_sicher(wert: str) -> str:
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
        .options(selectinload(Rechnung.positionen))
        .order_by(Rechnung.nummer)
    ):
        for z in steuer_je_satz(r):
            w.writerow(
                [
                    r.nummer,
                    r.datum.isoformat(),
                    csv_sicher(r.adresse_snapshot.get("name", "")),
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
