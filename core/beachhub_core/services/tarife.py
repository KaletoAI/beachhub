import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from beachhub_shared.slots import Slot, slots_im_zeitraum, zeitraum_ist_slotfolge
from beachhub_shared.zeit import lokal, lokales_datum
from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_core.models import Feld, Tarif
from beachhub_core.services import slots_db


@dataclass(frozen=True)
class Slotpreisstand:
    """Interner unveränderlicher Preisstand genau einer Buchungsanfrage."""

    feld_id: uuid.UUID
    beginn: datetime
    ende: datetime
    kundengruppe_id: uuid.UUID | None
    slotpreise: tuple[tuple[Slot, Decimal], ...]

    @property
    def summe(self) -> Decimal:
        return sum((preis for _, preis in self.slotpreise), Decimal("0.00"))

    def passt_zu(
        self,
        *,
        feld_id: uuid.UUID,
        beginn: datetime,
        ende: datetime,
        kundengruppe_id: uuid.UUID | None,
        preis: Decimal,
    ) -> bool:
        return (
            self.feld_id == feld_id
            and self.beginn == beginn
            and self.ende == ende
            and self.kundengruppe_id == kundengruppe_id
            and self.summe == preis
            and bool(self.slotpreise)
            and self.slotpreise[0][0].beginn == beginn
            and self.slotpreise[-1][0].ende == ende
            and all(
                vorher.ende == nachher.beginn
                for (vorher, _), (nachher, _) in zip(
                    self.slotpreise, self.slotpreise[1:], strict=False
                )
            )
        )


def _passt(t: Tarif, feld_id: uuid.UUID, slot: Slot, kundengruppe_id: uuid.UUID | None) -> bool:
    lok = lokal(slot.beginn)
    if t.feld_id is not None and t.feld_id != feld_id:
        return False
    if t.wochentag is not None and t.wochentag != lok.weekday():
        return False
    if t.uhrzeit_von is not None and t.uhrzeit_bis is not None:
        if not (t.uhrzeit_von <= lok.time() < t.uhrzeit_bis):
            return False
    if t.kundengruppe_id is not None and t.kundengruppe_id != kundengruppe_id:
        return False
    if t.gueltig_von is not None and lok.date() < t.gueltig_von:
        return False
    if t.gueltig_bis is not None and lok.date() > t.gueltig_bis:
        return False
    return True


def _spezifitaet(t: Tarif) -> int:
    return sum(
        [
            t.feld_id is not None,
            t.wochentag is not None,
            t.uhrzeit_von is not None and t.uhrzeit_bis is not None,
            t.kundengruppe_id is not None,
            t.gueltig_von is not None or t.gueltig_bis is not None,
        ]
    )


def _regel_aus_stand(
    regeln: Sequence[Tarif],
    *,
    feld_id: uuid.UUID,
    slot: Slot,
    kundengruppe_id: uuid.UUID | None,
) -> Tarif | None:
    kandidaten = [t for t in regeln if _passt(t, feld_id, slot, kundengruppe_id)]
    if not kandidaten:
        return None
    return max(kandidaten, key=lambda t: (_spezifitaet(t), t.created_at))


def _aktive_regeln(db: Session) -> list[Tarif]:
    return list(
        db.scalars(
            select(Tarif).where(Tarif.aktiv.is_(True)).execution_options(populate_existing=True)
        )
    )


def regel_fuer_slot(
    db: Session, *, feld_id: uuid.UUID, slot: Slot, kundengruppe_id: uuid.UUID | None
) -> Tarif | None:
    return _regel_aus_stand(
        _aktive_regeln(db), feld_id=feld_id, slot=slot, kundengruppe_id=kundengruppe_id
    )


def preise_je_slot(
    db: Session,
    *,
    feld_id: uuid.UUID,
    beginn: datetime,
    ende: datetime,
    kundengruppe_id: uuid.UUID | None,
) -> list[tuple[Slot, Decimal]] | None:
    feld = db.get(Feld, feld_id)
    if feld is None:
        return None
    tages = slots_db.tages_slots(db, feld, lokales_datum(beginn))
    if not zeitraum_ist_slotfolge(beginn, ende, tages):
        return None
    regeln = _aktive_regeln(db)
    preise = []
    for slot in slots_im_zeitraum(beginn, ende, tages):
        regel = _regel_aus_stand(
            regeln, feld_id=feld_id, slot=slot, kundengruppe_id=kundengruppe_id
        )
        if regel is None:
            return None
        preise.append((slot, regel.preis))
    return preise


def ermittle_preis(
    db: Session,
    *,
    feld_id: uuid.UUID,
    beginn: datetime,
    ende: datetime,
    kundengruppe_id: uuid.UUID | None,
) -> Decimal | None:
    preise = preise_je_slot(
        db, feld_id=feld_id, beginn=beginn, ende=ende, kundengruppe_id=kundengruppe_id
    )
    return None if preise is None else sum((preis for _, preis in preise), Decimal("0.00"))


def preisstand_fuer_buchung(
    db: Session,
    *,
    feld_id: uuid.UUID,
    beginn: datetime,
    ende: datetime,
    kundengruppe_id: uuid.UUID | None,
) -> Slotpreisstand | None:
    preise = preise_je_slot(
        db, feld_id=feld_id, beginn=beginn, ende=ende, kundengruppe_id=kundengruppe_id
    )
    return (
        None
        if preise is None
        else Slotpreisstand(feld_id, beginn, ende, kundengruppe_id, tuple(preise))
    )
