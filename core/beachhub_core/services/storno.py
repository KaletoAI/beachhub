import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from beachhub_core import clock
from beachhub_core.models import Buchung, Storno
from beachhub_core.services import audit, buchungen, guthaben, konfiguration, sperren


class StornoFehler(Exception):  # noqa: N818
    pass


def _gutschrift(
    db: Session, buchung: Buchung, betrag: Decimal, storno_id: uuid.UUID, quelle: str
) -> None:
    """Onlinezahler, die bereits bestätigt (= bezahlt) waren, bekommen Guthaben.

    Rechnungskunden nicht.

    Hinweis: Solange eine Gutschrift ohne zugehörige Stornorechnung entsteht, bleibt
    Umsatzsteuer auf eine nicht erbrachte Leistung abgeführt (A-STORNO-6 der Spezifikation).
    Das ist mit der Umstellung auf zwei Steuersätze zu beheben.
    """
    if buchung.zahlungsart == "online" and betrag > 0:
        guthaben.buche(
            db,
            kunde=buchung.kunde,
            betrag=betrag,
            art="storno_gutschrift",
            bezug_id=storno_id,
            notiz="Storno kostenfrei",
            quelle=quelle,
        )


def storniere(
    db: Session,
    buchung: Buchung,
    *,
    durch: str,
    admin_user_id: uuid.UUID | None = None,
    grund: str = "",
    kostenfrei: bool | None = None,
) -> Storno:
    if not buchung.aktiv:
        raise StornoFehler("nicht_aktiv")
    jetzt = clock.now(db)
    # A-STORNO-5: Kunden dürfen nur vor Beginn stornieren. Betreiber/System dürfen
    # auch eine bereits laufende Buchung stornieren (z. B. Notfall-Sperre), aber
    # niemand eine bereits beendete Buchung.
    if durch == "kunde" and buchung.beginn <= jetzt:
        raise StornoFehler("zu_spaet")
    if buchung.ende <= jetzt:
        raise StornoFehler("zu_spaet")
    war_bezahlt = buchung.status in (
        Buchung.BESTAETIGT,
        Buchung.DURCHGEFUEHRT,
        Buchung.NICHT_ERSCHIENEN,
    )
    if kostenfrei is None:
        frist = timedelta(hours=konfiguration.hole(db, "storno_frist_stunden"))
        kostenfrei = jetzt <= buchung.beginn - frist
    s = Storno(
        buchung_id=buchung.id,
        durch=durch,
        kostenfrei=kostenfrei,
        grund=grund,
    )
    db.add(s)
    quelle = "admin" if durch == "betreiber" else ("portal" if durch == "kunde" else "system")
    buchungen.setze_status(
        db, buchung, Buchung.STORNIERT, quelle=quelle, admin_user_id=admin_user_id
    )
    db.flush()
    if kostenfrei and war_bezahlt:
        _gutschrift(db, buchung, buchung.preis, s.id, quelle)
    audit.protokolliere(
        db,
        quelle=quelle,
        objekt_typ="storno",
        objekt_id=s.id,
        vorher=None,
        nachher=audit.als_dict(s),
        admin_user_id=admin_user_id,
    )
    return s


def kulanz(db: Session, s: Storno, *, admin_user_id: uuid.UUID | None, grund: str) -> None:
    """Stellt ein kostenpflichtiges Storno nachträglich frei. Ein bereits kostenfreies
    Storno bleibt unberührt – sonst entstünde ein zweites Mal Guthaben."""
    if s.kostenfrei:
        return
    vorher = audit.als_dict(s)
    s.kostenfrei = True
    s.grund = (s.grund + " | " if s.grund else "") + f"Kulanz: {grund}"
    _gutschrift(db, s.buchung, s.buchung.preis, s.id, "admin")
    db.flush()
    audit.protokolliere(
        db,
        quelle="admin",
        objekt_typ="storno",
        objekt_id=s.id,
        vorher=vorher,
        nachher=audit.als_dict(s),
        admin_user_id=admin_user_id,
    )
    from beachhub_core.services import lesestand

    lesestand.markiere_geaendert(db, f"konto:{s.buchung.kunde_id}")


# Verdrahtung der Hooks – einmalig beim Import
sperren.STORNIERE = storniere
