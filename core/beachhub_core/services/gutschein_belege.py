"""Getrennte Buchungsbelege; Gutschein-Tarifdeckung ist kein neuer Geldzufluss."""

import uuid
from decimal import ROUND_HALF_UP, Decimal

from beachhub_shared.zeit import lokales_datum
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from beachhub_core.models import (
    Buchung,
    Gutschein,
    GutscheinEinloesung,
    Rechnung,
    RechnungPosition,
    Zahlung,
)
from beachhub_core.services import audit, rechnungen
from beachhub_core.services.gutschein_typen import GutscheinBuchungsbelege

NULL = Decimal("0.00")
CENT = Decimal("0.01")


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


def korrigiere_wertanteil(
    db: Session,
    einloesung: GutscheinEinloesung,
    *,
    grund: str,
    quelle: str,
    admin_user_id: uuid.UUID | None = None,
) -> Rechnung:
    """Gegenbeleg unter den vollständigen Vorgangslocks, ohne Walletmutation.

    Rundung folgt der bereits korrigierten Kaufbasis, unabhängig von der Reihenfolge
    der Einlösungen. Der Einlöser erhält niemals den fremden Käuferbeleg.
    """
    e = einloesung
    if e.korrektur_rechnung_id is not None:
        r = db.get(Rechnung, e.korrektur_rechnung_id)
        assert r is not None
        return r
    g = db.get(Gutschein, e.gutschein_id)
    assert g is not None
    pid = e.ursprung_position_id if g.steuer == "beim_kauf" else e.rechnung_position_id
    p = db.get(RechnungPosition, pid) if pid else None
    if p is None or e.beleg_brutto is None or p.brutto <= NULL:
        raise rechnungen.RechnungsFehler("ungueltige_belegbasis")
    rechnungen.sperre(db, p.rechnung)
    alt_ids = select(GutscheinEinloesung.korrektur_rechnung_id).where(
        (GutscheinEinloesung.ursprung_position_id == p.id)
        if g.steuer == "beim_kauf"
        else (GutscheinEinloesung.rechnung_position_id == p.id)
    )
    alt = db.execute(
        select(
            func.coalesce(func.sum(RechnungPosition.brutto), 0),
            func.coalesce(func.sum(RechnungPosition.netto), 0),
        ).where(RechnungPosition.rechnung_id.in_(alt_ids))
    ).one()
    brutto_alt, netto_alt = -Decimal(str(alt[0])), -Decimal(str(alt[1]))
    brutto = e.beleg_brutto
    gesamt = brutto_alt + brutto
    if gesamt > p.brutto or p.korrigiert_durch_id is not None:
        raise rechnungen.RechnungsFehler("bereits_korrigiert")
    netto = (gesamt * p.netto / p.brutto).quantize(CENT, rounding=ROUND_HALF_UP) - netto_alt
    ust = brutto - netto
    if netto < NULL or ust < NULL:
        raise rechnungen.RechnungsFehler("ungueltige_belegbasis")
    vorher = audit.als_dict(p.rechnung)
    beleg = rechnungen._neue_rechnung(
        db,
        p.rechnung.kunde,
        "storno",
        [
            rechnungen.Posten(
                None,
                f"Storno zu Rechnung {p.rechnung.nummer}: {p.text}",
                -brutto,
                p.ust_satz,
                netto_vorgegeben=-netto,
                ust_vorgegeben=-ust,
            )
        ],
        p.rechnung.leistung_von,
        p.rechnung.leistung_bis,
        "bezahlt",
        quelle=quelle,
        korrigiert_rechnung_id=p.rechnung_id,
        admin_user_id=admin_user_id,
        gutschein_rolle=p.rechnung.gutschein_rolle,
        gutschein_ids=[g.id],
        einloesung_ids=[e.id],
    )
    e.korrektur_rechnung_id = beleg.id
    if gesamt == p.brutto:
        p.korrigiert_durch_id = beleg.positionen[0].id
        if all(pos.korrigiert_durch_id is not None for pos in p.rechnung.positionen):
            p.rechnung.status = "storniert"
            p.rechnung.storniert_durch_id = beleg.id
    db.flush()
    rechnungen._aktualisiere_bezahlt(db, p.rechnung)
    audit.protokolliere(
        db,
        quelle=quelle,
        objekt_typ="rechnung",
        objekt_id=p.rechnung_id,
        vorher=vorher,
        nachher={
            **audit.als_dict(p.rechnung),
            "grund": grund,
            "korrekturbeleg": beleg.nummer,
            "einloesung_id": str(e.id),
        },
        admin_user_id=admin_user_id,
    )
    return beleg
