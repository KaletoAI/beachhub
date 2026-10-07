"""Vollständige Vorgangslocks vor Buchungs-Savepoints und Finanzmutationen."""

import uuid
from collections.abc import Callable, Iterable
from datetime import timedelta
from typing import TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from beachhub_core import clock
from beachhub_core.models import (
    Buchung,
    Gutschein,
    GutscheinEinloesung,
    GutscheinFehlversuch,
    Kunde,
    Zahlung,
)
from beachhub_core.services.gutschein_typen import GutscheinSperrkontext


class GutscheinVorbereitungWiederholen(Exception):  # noqa: N818
    """Interner Retry: gesamte Transaktion zurückrollen, neu entdecken und vorbereiten.

    Kein fachlicher Ablehnungsgrund und niemals nachträglich Kunden nachsperren.
    Aufrufer dürfen vor erfolgreicher Vorbereitung keine externen Effekte auslösen.
    """


T = TypeVar("T")


def fuehre_transaktion_aus(db: Session, vorgang: Callable[[], T]) -> T:
    """Bis zu drei vollständige Versuche, ausschließlich bei neuer Gutscheinzuordnung.

    Der Aufrufer lädt Modelle, Entscheidungen und die gesamte Batchmenge in `vorgang`
    frisch. Jeder Fehlversuch gibt alle Locks und Mutationen per äußerem Rollback frei.
    Externe Nachläufe gehören erst hinter die erfolgreiche Rückgabe dieser Funktion.
    Andere Ausnahmen bleiben unverändert beim bisherigen Aufrufer.
    """
    for versuch in range(3):
        try:
            ergebnis = vorgang()
            db.commit()
            return ergebnis
        except GutscheinVorbereitungWiederholen as exc:
            db.rollback()
            if versuch == 2:
                raise GutscheinVorbereitungWiederholen(
                    "Die Gutscheinzuordnung hat sich mehrfach geändert. "
                    "Bitte die Aktion erneut ausführen."
                ) from exc
    raise AssertionError("Transaktionsversuche erschöpft")


class GutscheinRateLimitFehler(Exception):  # noqa: N818
    grund = "zu_viele_versuche"


def _beziehungen(db: Session, ids: set[uuid.UUID]) -> tuple[set[uuid.UUID], set[uuid.UUID]]:
    kunden: set[uuid.UUID] = set()
    zahlungen: set[uuid.UUID] = set()
    for kaeufer, inhaber, zahlung in db.execute(
        select(Gutschein.kaeufer_kunde_id, Gutschein.inhaber_kunde_id, Gutschein.zahlung_id).where(
            Gutschein.id.in_(ids)
        )
    ):
        kunden.update(k for k in (kaeufer, inhaber) if k is not None)
        if zahlung is not None:
            zahlungen.add(zahlung)
    kunden.update(
        db.scalars(
            select(GutscheinEinloesung.kunde_id).where(GutscheinEinloesung.gutschein_id.in_(ids))
        )
    )
    return kunden, zahlungen


def bereite_vorgang_vor(
    db: Session,
    *,
    kunde_ids: Iterable[uuid.UUID],
    gutschein_ids: Iterable[uuid.UUID],
    zahlung_ids: Iterable[uuid.UUID] = (),
) -> GutscheinSperrkontext:
    """Entdeckt Beziehungen lesend, sperrt Kunde → Zahlung → Gutschein UUID-sortiert.

    Alle Rückgabemodelle sind unter Lock frisch geladen. Beziehungserweiterungen
    lösen vor jeder Mutation einen internen Transaktionsretry aus.
    """
    gids, kids, zids = set(gutschein_ids), set(kunde_ids), set(zahlung_ids)
    with db.no_autoflush:
        entdeckt_k, entdeckt_z = _beziehungen(db, gids)
        kids.update(entdeckt_k)
        zids.update(entdeckt_z)
        kids.update(db.scalars(select(Zahlung.kunde_id).where(Zahlung.id.in_(zids))))
        gesperrte_kunden = list(
            db.scalars(
                select(Kunde)
                .where(Kunde.id.in_(kids))
                .order_by(Kunde.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        if {k.id for k in gesperrte_kunden} != kids:
            raise ValueError("Unbekannter Kunde in Gutscheinvorbereitung")
        zahlungen = {
            z.id: z
            for z in db.scalars(
                select(Zahlung)
                .where(Zahlung.id.in_(zids))
                .order_by(Zahlung.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        }
        gutscheine = {
            g.id: g
            for g in db.scalars(
                select(Gutschein)
                .where(Gutschein.id.in_(gids))
                .order_by(Gutschein.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        }
        if set(zahlungen) != zids or set(gutscheine) != gids:
            raise GutscheinVorbereitungWiederholen("Vorgangsmenge inzwischen geändert")
        aktuell_k, aktuell_z = _beziehungen(db, gids)
        aktuell_k.update(z.kunde_id for z in zahlungen.values())
        if not aktuell_k <= kids or not aktuell_z <= zids:
            raise GutscheinVorbereitungWiederholen("Besitzer- oder Zahlungsbeziehung geändert")
    return GutscheinSperrkontext(frozenset(kids), gutscheine, zahlungen)


def bereite_buchungen_vor(
    db: Session,
    buchung_ids: Iterable[uuid.UUID],
    *,
    kunde_ids: Iterable[uuid.UUID] = (),
) -> GutscheinSperrkontext:
    """Bereitet die ganze Batchmenge vor ihrer ersten Buchungs-/Belegsperre vor.

    Der spätere Stornodienst kann den Kontext direkt übernehmen. Käufer, bisherige
    Inhaber, Einlöser und Kauf-/Buchungszahlungen gehören sämtlich zum Vorgang.
    """
    bids = set(buchung_ids)
    with db.no_autoflush:
        kids = set(kunde_ids) | set(
            db.scalars(select(Buchung.kunde_id).where(Buchung.id.in_(bids)))
        )
        einloesungen = list(
            db.execute(
                select(GutscheinEinloesung.gutschein_id, GutscheinEinloesung.kunde_id).where(
                    GutscheinEinloesung.buchung_id.in_(bids)
                )
            )
        )
        kids.update(k for _, k in einloesungen)
        gids = {g for g, _ in einloesungen}
        zids = set(db.scalars(select(Zahlung.id).where(Zahlung.buchung_id.in_(bids))))
        kontext = bereite_vorgang_vor(db, kunde_ids=kids, gutschein_ids=gids, zahlung_ids=zids)
        # Während des Wartens können Buchungen andere Einlösungen/Zahlungen erhalten haben.
        aktuell_k = set(db.scalars(select(Buchung.kunde_id).where(Buchung.id.in_(bids))))
        aktuell_e = list(
            db.execute(
                select(GutscheinEinloesung.gutschein_id, GutscheinEinloesung.kunde_id).where(
                    GutscheinEinloesung.buchung_id.in_(bids)
                )
            )
        )
        aktuell_k.update(k for _, k in aktuell_e)
        aktuell_z = set(db.scalars(select(Zahlung.id).where(Zahlung.buchung_id.in_(bids))))
        if (
            not aktuell_k <= kontext.kunde_ids
            or not {g for g, _ in aktuell_e} <= gids
            or not aktuell_z <= set(kontext.zahlungen)
        ):
            raise GutscheinVorbereitungWiederholen("Buchungsbeziehungen inzwischen geändert")
        return kontext


def _sperre_ratelimit_kunde(db: Session, kunde_id: uuid.UUID) -> None:
    # Auch direkte Aufrufer serialisieren Zählen und Einfügen; in einem vorbereiteten
    # Vorgang ist diese Sperre bereits gehalten. Immer außerhalb des Buchungs-Savepoints.
    with db.no_autoflush:
        if db.scalar(select(Kunde.id).where(Kunde.id == kunde_id).with_for_update()) is None:
            raise ValueError("Unbekannter Kunde im Gutschein-Versuchslimit")


def pruefe_rate_limit(db: Session, kunde_id: uuid.UUID) -> None:
    _sperre_ratelimit_kunde(db, kunde_id)
    anzahl = db.scalar(
        select(func.count())
        .select_from(GutscheinFehlversuch)
        .where(
            GutscheinFehlversuch.kunde_id == kunde_id,
            GutscheinFehlversuch.zeitpunkt > clock.now(db) - timedelta(minutes=60),
        )
    )
    if anzahl is not None and anzahl >= 10:
        raise GutscheinRateLimitFehler("zu_viele_versuche")


def merke_fehlversuch(
    db: Session, kunde_id: uuid.UUID, *, anfrage_id: uuid.UUID | None = None
) -> None:
    _sperre_ratelimit_kunde(db, kunde_id)
    if (
        anfrage_id is not None
        and db.scalar(
            select(GutscheinFehlversuch.id).where(
                GutscheinFehlversuch.anfrage_id == anfrage_id,
            )
        )
        is not None
    ):
        return
    db.add(GutscheinFehlversuch(kunde_id=kunde_id, anfrage_id=anfrage_id, zeitpunkt=clock.now(db)))
    db.flush()
