import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from beachhub_core.models import GuthabenBuchung, Kunde
from beachhub_core.services import audit

ARTEN = {"storno_gutschrift", "verrechnung", "auszahlung", "manuell", "ueberzahlung"}
ABGEHEND = {"verrechnung", "auszahlung"}


class GuthabenFehler(Exception):  # noqa: N818
    pass


def buche(
    db: Session,
    *,
    kunde: Kunde,
    betrag: Decimal,
    art: str,
    bezug_id: uuid.UUID | None = None,
    notiz: str = "",
    admin_user_id: uuid.UUID | None = None,
    quelle: str = "admin",
) -> GuthabenBuchung:
    if art not in ARTEN:
        raise GuthabenFehler("art_unbekannt")
    if art in ABGEHEND and betrag >= 0:
        raise GuthabenFehler("betrag_muss_negativ_sein")

    # Lock the customer row to prevent concurrent balance modifications
    db.execute(select(Kunde).where(Kunde.id == kunde.id).with_for_update())
    db.refresh(kunde)

    if kunde.guthaben + betrag < 0:
        raise GuthabenFehler("nicht_gedeckt")
    vorher = kunde.guthaben
    kunde.guthaben = kunde.guthaben + betrag
    b = GuthabenBuchung(
        kunde_id=kunde.id,
        betrag=betrag,
        art=art,
        bezug_id=bezug_id,
        notiz=notiz,
        admin_user_id=admin_user_id,
    )
    db.add(b)
    db.flush()
    audit.protokolliere(
        db,
        quelle=quelle,
        objekt_typ="guthaben",
        objekt_id=b.id,
        vorher={"guthaben": str(vorher)},
        nachher={
            "guthaben": str(kunde.guthaben),
            "art": art,
            "betrag": str(betrag),
        },
        admin_user_id=admin_user_id,
    )
    return b


def saldo(db: Session, kunde_id: uuid.UUID) -> Decimal:
    s = db.scalar(
        select(func.coalesce(func.sum(GuthabenBuchung.betrag), 0)).where(
            GuthabenBuchung.kunde_id == kunde_id
        )
    )
    return Decimal(str(s)).quantize(Decimal("0.01"))
