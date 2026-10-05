"""Getrennte Buchungsbelege; Gutschein-Tarifdeckung ist kein neuer Geldzufluss."""

from decimal import Decimal

from beachhub_shared.zeit import lokales_datum
from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_core.models import Buchung, Gutschein, GutscheinEinloesung, Zahlung
from beachhub_core.services import rechnungen
from beachhub_core.services.gutschein_typen import GutscheinBuchungsbelege

NULL = Decimal("0.00")


def rechnung_fuer_buchung(db: Session, buchung: Buchung, *, quelle: str) -> GutscheinBuchungsbelege:
    es = list(
        db.scalars(
            select(GutscheinEinloesung)
            .where(
                GutscheinEinloesung.buchung_id == buchung.id,
                GutscheinEinloesung.status != GutscheinEinloesung.RUECKGAENGIG,
            )
            .order_by(GutscheinEinloesung.id)
        )
    )
    if buchung.rechnung_position_id is not None or any(e.rechnung_position_id for e in es):
        raise rechnungen.RechnungsFehler("bereits_berechnet")
    tag = lokales_datum(buchung.beginn)
    deckung = sum((e.betrag for e in es), NULL)
    rest = buchung.preis - deckung
    geld = None
    if rest > NULL:
        geld = rechnungen._neue_rechnung(
            db,
            buchung.kunde,
            "einzel",
            [
                rechnungen.Posten(
                    buchung, rechnungen._positionstext(buchung), rest, buchung.ust_satz
                )
            ],
            tag,
            tag,
            "bezahlt",
            quelle=quelle,
            gutschein_rolle="geldanteil" if es else None,
            gutschein_ids=[e.gutschein_id for e in es],
            einloesung_ids=[e.id for e in es],
        )
        for z in db.scalars(select(Zahlung).where(Zahlung.buchung_id == buchung.id)):
            z.rechnung_id = geld.id
    belege = []
    for e in es:
        g = db.get(Gutschein, e.gutschein_id)
        assert g is not None
        if g.steuer != "bei_einloesung":
            continue
        if e.beleg_brutto is None or e.beleg_netto is None or e.beleg_ust is None:
            raise rechnungen.RechnungsFehler("ungueltige_belegbasis")
        r = rechnungen._neue_rechnung(
            db,
            buchung.kunde,
            "einzel",
            [
                rechnungen.Posten(
                    None,
                    f"Gutschein-Einlösung {e.id}: " + rechnungen._positionstext(buchung),
                    e.beleg_brutto,
                    buchung.ust_satz,
                    netto_vorgegeben=e.beleg_netto,
                    ust_vorgegeben=e.beleg_ust,
                )
            ],
            tag,
            tag,
            "bezahlt",
            quelle=quelle,
            gutschein_rolle="einloesung",
            gutschein_ids=[g.id],
            einloesung_ids=[e.id],
        )
        e.rechnung_position_id = r.positionen[0].id
        belege.append(r)
    db.flush()
    return GutscheinBuchungsbelege(geld, belege)
