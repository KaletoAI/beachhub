import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from beachhub_core import clock
from beachhub_core.models import AppSetting, GuthabenBuchung, Kunde
from beachhub_core.services import audit, konfiguration

# storno_gutschrift entsteht nur über storno.gutschreiben_positionen mit Korrekturbeleg.
ARTEN = {
    "storno_gutschrift",
    "verrechnung",
    "auszahlung",
    "manuell",
    "ueberzahlung",
    # Verrechnetes Guthaben einer Reservierung, die ohne Zahlung endet (Storno, Verfall).
    # Keine storno_gutschrift: Es gab keine Rechnung, also braucht es keinen Korrekturbeleg.
    "rueckbuchung",
}
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
    from beachhub_core.services import lesestand

    lesestand.markiere_geaendert(db, f"konto:{kunde.id}")
    return b


def saldo(db: Session, kunde_id: uuid.UUID) -> Decimal:
    s = db.scalar(
        select(func.coalesce(func.sum(GuthabenBuchung.betrag), 0)).where(
            GuthabenBuchung.kunde_id == kunde_id
        )
    )
    return Decimal(str(s)).quantize(Decimal("0.01"))


SAISONENDE_MARKER = "guthabenliste_letzter"


def guthabenliste(db: Session) -> list[Kunde]:
    """Alle Kunden mit Guthaben – zum Saisonende zahlt der Betreiber auf Wunsch aus (A-ZAHL-4)."""
    return list(
        db.scalars(
            select(Kunde)
            .where(Kunde.guthaben > 0, Kunde.anonymisiert_am.is_(None))
            .order_by(Kunde.name)
        ).all()
    )


def saisonende_faellig(db: Session) -> tuple[int, Decimal] | None:
    """Einmal im Jahr ab dem Stichtag `saisonende_guthabenliste` (auch nachträglich, falls der Lauf
    am Stichtag ausfiel): Anzahl und Summe der Guthaben. Setzt den Marker, committet nicht."""
    heute = clock.today(db)
    stichtag = konfiguration.hole(db, "saisonende_guthabenliste").im_jahr(heute.year)
    if heute < stichtag:
        return None
    # Atomar beanspruchen: Auch parallele Läufe und alte Session-Caches dürfen
    # dieselbe Jahreserinnerung nicht zweimal freigeben. Keine Kundensperren nötig.
    jahr = str(heute.year)
    marker = db.scalar(
        insert(AppSetting)
        .values(key=SAISONENDE_MARKER, value=jahr)
        .on_conflict_do_update(
            index_elements=[AppSetting.key],
            set_={"value": jahr},
            where=AppSetting.value.is_distinct_from(jahr),
        )
        .returning(AppSetting.key)
    )
    if marker is None:
        return None
    liste = guthabenliste(db)
    return len(liste), sum((k.guthaben for k in liste), Decimal("0.00"))
