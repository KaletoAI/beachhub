"""Die zwei festen Kundengruppen (A-KUND-2) und die Gruppe eines Kunden zum Leistungsdatum
(A-KUND-6). Der Kunde trägt keine Gruppe: Mitglied ist, wessen `mitglied_bis` den Tag des Termins
noch einschließt."""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_core.models import Kunde, Kundengruppe
from beachhub_core.services import audit

# Gilt, wenn eine Gruppe fehlt (frische Datenbank ohne Migration, etwa in Tests). Im Betrieb legt
# Migration 0010 beide an.
VORGABEN: dict[bool, tuple[str, Decimal]] = {
    True: ("DJK-Mitglied", Decimal("7.00")),
    False: ("Nicht-Mitglied", Decimal("19.00")),
}


class GruppenFehler(Exception):  # noqa: N818
    pass


def _gruppe(db: Session, ist_mitglied: bool) -> Kundengruppe:
    g = db.scalar(select(Kundengruppe).where(Kundengruppe.ist_mitglied == ist_mitglied))
    if g is None:
        name, satz = VORGABEN[ist_mitglied]
        g = Kundengruppe(name=name, ust_satz=satz, ist_mitglied=ist_mitglied)
        db.add(g)
        db.flush()
    return g


def mitglied(db: Session) -> Kundengruppe:
    return _gruppe(db, True)


def nicht_mitglied(db: Session) -> Kundengruppe:
    return _gruppe(db, False)


def beide(db: Session) -> list[Kundengruppe]:
    """Beide Gruppen in fester Reihenfolge für Auswahllisten: Nicht-Mitglied zuerst."""
    return [nicht_mitglied(db), mitglied(db)]


def ist_mitglied_am(kunde: Kunde, datum: date) -> bool:
    return kunde.mitglied_bis is not None and datum <= kunde.mitglied_bis


def effektive_gruppe(db: Session, kunde: Kunde, datum: date) -> Kundengruppe:
    return mitglied(db) if ist_mitglied_am(kunde, datum) else nicht_mitglied(db)


def aendere(
    db: Session,
    gruppe: Kundengruppe,
    *,
    name: str,
    ust_satz: Decimal,
    admin_user_id: uuid.UUID | None,
) -> Kundengruppe:
    """Name und Steuersatz ändern. Der Satz gilt für danach entstehende Buchungen; bestehende
    tragen ihren eigenen (A-TARIF-3)."""
    name = name.strip()
    if not name:
        raise GruppenFehler("Name fehlt")
    if not Decimal("0") <= ust_satz < Decimal("100"):
        raise GruppenFehler("Steuersatz ungültig")
    vorher = audit.als_dict(gruppe)
    umbenannt = gruppe.name != name
    gruppe.name, gruppe.ust_satz = name, ust_satz
    db.flush()
    audit.protokolliere(
        db,
        quelle="admin",
        objekt_typ="kundengruppe",
        objekt_id=gruppe.id,
        vorher=vorher,
        nachher=audit.als_dict(gruppe),
        admin_user_id=admin_user_id,
    )
    from beachhub_core.services import lesestand

    lesestand.markiere_geaendert(db, "tarife")
    if umbenannt:
        # Das Portal ordnet Tarife über den Gruppennamen im Konto-Dokument zu.
        ids = db.scalars(
            select(Kunde.id).where(
                Kunde.portal_konto_id.is_not(None), Kunde.anonymisiert_am.is_(None)
            )
        ).all()
        if ids:
            lesestand.markiere_geaendert(db, *(f"konto:{i}" for i in ids))
    return gruppe
