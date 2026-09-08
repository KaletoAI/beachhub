import uuid
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
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
    if buchung.beginn <= jetzt:
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
        nachbuchung_offen=not kostenfrei,
        freigestellt_betrag=buchung.preis if kostenfrei else Decimal("0.00"),
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


def _ueberlappung_minuten(a: Buchung, b: Buchung) -> int:
    von, bis = max(a.beginn, b.beginn), min(a.ende, b.ende)
    return max(0, int((bis - von).total_seconds() // 60))


def pruefe_nachbuchung(db: Session, neue: Buchung) -> None:
    offene = db.scalars(
        select(Storno)
        .join(Buchung, Storno.buchung_id == Buchung.id)
        .where(
            Storno.nachbuchung_offen.is_(True),
            Buchung.feld_id == neue.feld_id,
            Buchung.kunde_id != neue.kunde_id,
            Buchung.beginn < neue.ende,
            Buchung.ende > neue.beginn,
        )
    ).all()
    for s in offene:
        alt = s.buchung
        gesamt = int((alt.ende - alt.beginn).total_seconds() // 60)
        anteil = (alt.preis * Decimal(_ueberlappung_minuten(alt, neue)) / Decimal(gesamt)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        neu_frei = min(alt.preis, s.freigestellt_betrag + anteil)
        zusatz = neu_frei - s.freigestellt_betrag
        if zusatz <= 0:
            continue
        vorher = audit.als_dict(s)
        s.freigestellt_betrag = neu_frei
        s.nachbuchung_buchung_id = neue.id
        if neu_frei >= alt.preis:
            s.kostenfrei = True
            s.nachbuchung_offen = False
        _gutschrift(db, alt, zusatz, s.id, "system")
        db.flush()
        audit.protokolliere(
            db,
            quelle="system",
            objekt_typ="storno",
            objekt_id=s.id,
            vorher=vorher,
            nachher=audit.als_dict(s),
        )


def kulanz(db: Session, s: Storno, *, admin_user_id: uuid.UUID | None, grund: str) -> None:
    vorher = audit.als_dict(s)
    rest = s.buchung.preis - s.freigestellt_betrag
    s.kostenfrei = True
    s.nachbuchung_offen = False
    s.freigestellt_betrag = s.buchung.preis
    s.grund = (s.grund + " | " if s.grund else "") + f"Kulanz: {grund}"
    _gutschrift(db, s.buchung, rest, s.id, "admin")
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


# Verdrahtung der Hooks aus Task 8/9 – einmalig beim Import
sperren.STORNIERE = storniere
if pruefe_nachbuchung not in buchungen.NACH_ANLAGE:
    buchungen.NACH_ANLAGE.append(pruefe_nachbuchung)
