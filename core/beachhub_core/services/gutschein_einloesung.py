"""Atomarer Gutscheinverbrauch unter vollständigen Vorgangslocks."""

import uuid
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

from beachhub_shared.zeit import lokales_datum
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from beachhub_core import clock
from beachhub_core.models import Buchung, Gutschein, GutscheinEinloesung, Kunde, Rechnung
from beachhub_core.services import (
    audit,
    gutschein_codes,
    gutschein_sperren,
    konfiguration,
    kundengruppen,
    lesestand,
    rechnungen,
    tarife,
)
from beachhub_core.services.gutschein_typen import GutscheinDeckung, GutscheinSperrkontext

NULL = Decimal("0.00")
CENT = Decimal("0.01")


class GutscheinFehler(Exception):  # noqa: N818
    def __init__(self, grund: str):
        super().__init__(grund)
        self.grund = grund


def normalisierte_codes(codes: Sequence[str]) -> list[str]:
    try:
        return list(dict.fromkeys(gutschein_codes.normalisiere(c) for c in codes))
    except gutschein_codes.GutscheincodeFehler as exc:
        raise GutscheinFehler("gutschein_ungueltig") from exc


def bereite_codes_vor(db: Session, kunde: Kunde, codes: Sequence[str]) -> GutscheinSperrkontext:
    # Auch kaputte Eingaben werden erst nach vollständigen äußeren Kundenlocks abgewiesen.
    normalisiert = []
    for code in codes:
        try:
            normalisiert.append(gutschein_codes.normalisiere(code))
        except gutschein_codes.GutscheincodeFehler:
            continue
    with db.no_autoflush:
        ids = list(db.scalars(select(Gutschein.id).where(Gutschein.code.in_(normalisiert))))
    return gutschein_sperren.bereite_vorgang_vor(db, kunde_ids=[kunde.id], gutschein_ids=ids)


def _pruefe(db: Session, g: Gutschein, b: Buchung, kunde: Kunde) -> None:
    heute, tag = clock.today(db), lokales_datum(b.beginn)
    if (
        g.status not in (Gutschein.AKTIV, Gutschein.TEILWEISE_EINGELOEST)
        or g.zurueckgegeben_am is not None
    ):
        raise GutscheinFehler("gutschein_ungueltig")
    if g.gueltig_bis is None or heute > g.gueltig_bis:
        raise GutscheinFehler("gutschein_abgelaufen")
    if (
        g.steuer == "beim_kauf"
        and g.nur_mitglieder
        and not kundengruppen.ist_mitglied_am(kunde, tag)
    ):
        raise GutscheinFehler("gutschein_nur_mitglieder")
    if (
        (g.feld_id is not None and g.feld_id != b.feld_id)
        or (g.zeitraum_von and tag < g.zeitraum_von)
        or (g.zeitraum_bis and tag > g.zeitraum_bis)
    ):
        raise GutscheinFehler("gutschein_nicht_passend")
    if g.modell == "einheit":
        nutzungen = (
            db.scalar(
                select(func.count())
                .select_from(GutscheinEinloesung)
                .where(
                    GutscheinEinloesung.gutschein_id == g.id,
                    GutscheinEinloesung.status != GutscheinEinloesung.RUECKGAENGIG,
                )
            )
            or 0
        )
        if nutzungen >= g.max_einloesungen:
            raise GutscheinFehler("gutschein_ungueltig")
    elif g.restwert is None or g.restwert <= NULL:
        raise GutscheinFehler("gutschein_ungueltig")


def _belegbasis(db: Session, g: Gutschein, e: GutscheinEinloesung, b: Buchung) -> None:
    if g.art == "frei":
        return
    r = db.get(Rechnung, g.rechnung_id) if g.rechnung_id else None
    if r is None or len(r.positionen) != 1 or r.status == "storniert":
        raise GutscheinFehler("gutschein_ungueltig")
    p = r.positionen[0]
    e.ursprung_position_id = p.id
    if g.modell == "einheit":
        e.beleg_brutto = p.brutto
        e.beleg_netto, e.beleg_ust = (
            (p.netto, p.ust)
            if g.steuer == "beim_kauf"
            else rechnungen.netto_ust(p.brutto, b.ust_satz)
        )
    elif g.steuer == "beim_kauf":
        # Kumulativer Verbrauch inklusive bereits korrigierter bestätigter Einlösungen;
        # reine Reservierungsrücknahmen verbrauchen die Kaufbasis dagegen nicht.
        alt = db.execute(
            select(
                func.coalesce(func.sum(GutscheinEinloesung.beleg_brutto), 0),
                func.coalesce(func.sum(GutscheinEinloesung.beleg_netto), 0),
            ).where(
                GutscheinEinloesung.gutschein_id == g.id,
                (GutscheinEinloesung.status != GutscheinEinloesung.RUECKGAENGIG)
                | (GutscheinEinloesung.rueckabwicklung_art == "guthaben"),
            )
        ).one()
        brutto_alt, netto_alt = Decimal(str(alt[0])), Decimal(str(alt[1]))
        e.beleg_brutto = e.betrag
        if brutto_alt + e.betrag > p.brutto or p.brutto <= NULL:
            raise GutscheinFehler("gutschein_ungueltig")
        kumulativ = ((brutto_alt + e.betrag) * p.netto / p.brutto).quantize(
            CENT, rounding=ROUND_HALF_UP
        )
        e.beleg_netto = kumulativ - netto_alt
        e.beleg_ust = e.beleg_brutto - e.beleg_netto
    else:
        e.beleg_brutto = e.betrag
        e.beleg_netto, e.beleg_ust = rechnungen.netto_ust(e.betrag, b.ust_satz)


def loese_ein(
    db: Session,
    buchung: Buchung,
    kunde: Kunde,
    codes: Sequence[str],
    *,
    sperrkontext: GutscheinSperrkontext,
) -> GutscheinDeckung:
    if kunde.id not in sperrkontext.kunde_ids:
        raise ValueError("Einlöser nicht vorbereitet")
    normalisiert = normalisierte_codes(codes)
    nach_code = {g.code: g for g in sperrkontext.gutscheine.values()}
    if any(c not in nach_code for c in normalisiert):
        raise GutscheinFehler("gutschein_ungueltig")
    gs = [nach_code[c] for c in normalisiert]
    for g in gs:
        _pruefe(db, g, buchung, kunde)
    einheiten = [g for g in gs if g.modell == "einheit"]
    dauer = int((buchung.ende - buchung.beginn).total_seconds() // 60)
    grenze = konfiguration.hole(db, "gutscheine_je_buchung")
    if (grenze != "volle_einheiten" and len(gs) > int(grenze)) or (
        len(einheiten) > 1 and sum(g.minuten or 0 for g in einheiten) > dauer
    ):
        raise GutscheinFehler("zu_viele_gutscheine")
    preise = tarife.preise_je_slot(
        db,
        feld_id=buchung.feld_id,
        beginn=buchung.beginn,
        ende=buchung.ende,
        kundengruppe_id=buchung.kundengruppe_id,
    )
    if preise is None:
        raise GutscheinFehler("kein_tarif")
    es = []
    minuten_bisher, deckung = 0, NULL
    for g in [*einheiten, *(g for g in gs if g.modell == "wert")]:
        vorher = audit.als_dict(g)
        minuten = None
        if g.modell == "einheit":
            minuten = min(g.minuten or 0, dauer - minuten_bisher)
            minuten_bisher += minuten
            verbleibend = minuten_bisher
            kumulativ = NULL
            for slot, preis in preise:
                slot_min = int((slot.ende - slot.beginn).total_seconds() // 60)
                anteil = min(verbleibend, slot_min)
                kumulativ += preis * Decimal(anteil) / Decimal(slot_min)
                verbleibend -= anteil
            betrag = kumulativ.quantize(CENT, rounding=ROUND_HALF_UP) - deckung
        else:
            betrag = min(g.restwert or NULL, buchung.preis - deckung)
            if betrag <= NULL:
                raise GutscheinFehler("zu_viele_gutscheine")
        e = GutscheinEinloesung(
            id=uuid.uuid4(),
            gutschein_id=g.id,
            buchung_id=buchung.id,
            kunde_id=kunde.id,
            status=GutscheinEinloesung.RESERVIERT,
            minuten_gedeckt=minuten,
            betrag=betrag,
            zeitpunkt=clock.now(db),
        )
        _belegbasis(db, g, e, buchung)
        db.add(e)
        db.flush()
        if g.modell == "wert":
            g.restwert = (g.restwert or NULL) - betrag
            g.status = (
                Gutschein.EINGELOEST if g.restwert == NULL else Gutschein.TEILWEISE_EINGELOEST
            )
        else:
            bisher = (
                db.scalar(
                    select(func.count())
                    .select_from(GutscheinEinloesung)
                    .where(
                        GutscheinEinloesung.gutschein_id == g.id,
                        GutscheinEinloesung.status != GutscheinEinloesung.RUECKGAENGIG,
                    )
                )
                or 0
            )
            g.status = Gutschein.EINGELOEST if bisher >= g.max_einloesungen else Gutschein.AKTIV
        g.inhaber_kunde_id = kunde.id
        db.flush()
        audit.protokolliere(
            db,
            quelle=buchung.quelle,
            objekt_typ="gutschein_einloesung",
            objekt_id=e.id,
            vorher=None,
            nachher={
                **audit.als_dict(e),
                "akteur_kunde_id": str(kunde.id),
                "beleg_id": str(g.rechnung_id) if g.rechnung_id else None,
            },
        )
        audit.protokolliere(
            db,
            quelle=buchung.quelle,
            objekt_typ="gutschein",
            objekt_id=g.id,
            vorher=vorher,
            nachher={
                **audit.als_dict(g),
                "einloesung_id": str(e.id),
                "akteur_kunde_id": str(kunde.id),
                "beleg_id": str(g.rechnung_id) if g.rechnung_id else None,
            },
        )
        lesestand.markiere_geaendert(
            db,
            *(
                f"konto:{kid}"
                for kid in {
                    kunde.id,
                    g.kaeufer_kunde_id,
                    uuid.UUID(str(vorher["inhaber_kunde_id"]))
                    if vorher.get("inhaber_kunde_id")
                    else None,
                }
                if kid is not None
            ),
        )
        es.append(e)
        deckung += betrag
    return GutscheinDeckung(deckung, es)


def bestaetige_einloesungen(db: Session, buchung: Buchung) -> None:
    for e in db.scalars(
        select(GutscheinEinloesung).where(
            GutscheinEinloesung.buchung_id == buchung.id,
            GutscheinEinloesung.status == GutscheinEinloesung.RESERVIERT,
        )
    ):
        vorher = audit.als_dict(e)
        e.status = GutscheinEinloesung.BESTAETIGT
        db.flush()
        audit.protokolliere(
            db,
            quelle=buchung.quelle,
            objekt_typ="gutschein_einloesung",
            objekt_id=e.id,
            vorher=vorher,
            nachher={**audit.als_dict(e), "akteur_kunde_id": str(buchung.kunde_id)},
        )
