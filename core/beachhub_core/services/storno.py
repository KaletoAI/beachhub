import uuid
from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from beachhub_core import clock
from beachhub_core.models import Buchung, Dauerbuchung, Rechnung, RechnungPosition, Storno
from beachhub_core.services import (
    audit,
    buchungen,
    guthaben,
    konfiguration,
    kunden,
    rechnungen,
    sperren,
)

NULL = Decimal("0.00")


class StornoFehler(Exception):  # noqa: N818
    pass


def gutschreiben_positionen(
    db: Session,
    positionen: Sequence[RechnungPosition],
    *,
    grund: str,
    quelle: str,
    admin_user_id: uuid.UUID | None = None,
) -> Rechnung:
    """Korrigiert Positionen einer Rechnung und schreibt den bereits bezahlten Anteil als Guthaben
    gut. Der einzige Weg zu Guthaben der Art `storno_gutschrift` (A-STORNO-6): Ohne
    Korrekturbeleg bliebe Umsatzsteuer auf eine nicht erbrachte Leistung abgeführt (§ 17 UStG).
    Bei offener Rechnung sinkt zuerst die Forderung (A-RECH-7, Abweichung B-3)."""
    if not positionen:
        raise rechnungen.RechnungsFehler("keine_positionen")
    positionen = list({p.id: p for p in positionen}.values())
    rechnung = positionen[0].rechnung
    rechnungen.sperre(db, rechnung)
    offen_vorher = rechnungen.offener_betrag(db, rechnung)
    beleg = rechnungen.korrigiere(
        db, positionen, grund=grund, quelle=quelle, admin_user_id=admin_user_id
    )
    betrag = sum((p.brutto for p in positionen), NULL)
    gutschrift = max(NULL, betrag - offen_vorher)
    if gutschrift > NULL:
        guthaben.buche(
            db,
            kunde=rechnung.kunde,
            betrag=gutschrift,
            art="storno_gutschrift",
            bezug_id=beleg.id,
            notiz=f"Stornorechnung {beleg.nummer}",
            admin_user_id=admin_user_id,
            quelle=quelle,
        )
    return beleg


def _offene_position(db: Session, buchung: Buchung) -> RechnungPosition | None:
    if buchung.rechnung_position_id is None:
        return None
    pos = db.get(RechnungPosition, buchung.rechnung_position_id)
    if pos is None:
        return None
    rechnungen.sperre(db, pos.rechnung)
    return pos if pos.korrigiert_durch_id is None and pos.rechnung.status != "storniert" else None


def gutschreiben_alle(
    db: Session,
    gebucht: Sequence[Buchung],
    *,
    grund: str,
    quelle: str,
    admin_user_id: uuid.UUID | None = None,
) -> list[Rechnung]:
    """Korrekturbelege für kostenfrei gewordene Buchungen – einer je Rechnung, etwa beim Beenden
    einer Dauerbuchung (A-RECH-7). Verknüpft jedes Storno mit seinem Beleg. Buchungen ohne
    (noch nicht korrigierte) Rechnungsposition bleiben ohne Beleg und ohne Guthaben."""
    kunden.sperre_mehrere(db, (b.kunde_id for b in gebucht))
    gruppen: dict[uuid.UUID, list[tuple[Buchung, RechnungPosition]]] = {}
    for b in gebucht:
        # Saisonrechnung/Neuausstellung kann den zuvor geladenen Zeiger ersetzt haben.
        db.refresh(b)
        pos = _offene_position(db, b)
        if pos is not None:
            gruppen.setdefault(pos.rechnung_id, []).append((b, pos))
    belege = []
    for paare in gruppen.values():
        beleg = gutschreiben_positionen(
            db,
            [pos for _, pos in paare],
            grund=grund,
            quelle=quelle,
            admin_user_id=admin_user_id,
        )
        for b, _ in paare:
            s = db.scalar(select(Storno).where(Storno.buchung_id == b.id))
            if s is not None:
                s.korrektur_rechnung_id = beleg.id
        belege.append(beleg)
    db.flush()
    return belege


def gutschreiben(
    db: Session,
    buchung: Buchung,
    *,
    grund: str,
    quelle: str,
    admin_user_id: uuid.UUID | None = None,
) -> Rechnung | None:
    belege = gutschreiben_alle(
        db, [buchung], grund=grund, quelle=quelle, admin_user_id=admin_user_id
    )
    return belege[0] if belege else None


def freie_absagen_rest(db: Session, dauer: Dauerbuchung) -> int:
    """Kontingent je Abo abzüglich freier Kundenabsagen; Kulanz und Betreiber zählen nicht."""
    genutzt = db.scalar(
        select(func.count(Storno.id))
        .join(Buchung, Storno.buchung_id == Buchung.id)
        .where(Buchung.dauerbuchung_id == dauer.id, Storno.freie_absage.is_(True))
    )
    return max(0, int(konfiguration.hole(db, "abo_freie_absagen")) - int(genutzt or 0))


def storniere(
    db: Session,
    buchung: Buchung,
    *,
    durch: str,
    admin_user_id: uuid.UUID | None = None,
    grund: str = "",
    kostenfrei: bool | None = None,
    korrigieren: bool = True,
) -> Storno:
    """Storniert eine Buchung. Ist das Storno kostenfrei, korrigiert es die Rechnung der Buchung
    (`gutschreiben`) – außer mit `korrigieren=False`: Dann bündelt der Aufrufer die Belege mit
    `gutschreiben_alle`."""
    kunden.sperre_mehrere(db, [buchung.kunde_id])
    db.refresh(buchung, with_for_update=True)
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
    freie_absage = False
    if kostenfrei is None:
        frist = timedelta(hours=konfiguration.hole(db, "storno_frist_stunden"))
        kostenfrei = jetzt <= buchung.beginn - frist
        if buchung.dauerbuchung_id is not None and durch == "kunde":
            # Kunde ist bereits gesperrt. Prüfung und Verbrauch erfolgen in derselben
            # Transaktion unter der Abo-Sperre, auch bei gleichzeitig letzter freier Absage.
            dauer = db.scalar(
                select(Dauerbuchung)
                .where(Dauerbuchung.id == buchung.dauerbuchung_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            kostenfrei = bool(kostenfrei and dauer and freie_absagen_rest(db, dauer) > 0)
            # Ein bereits gutgeschriebener Termin verbraucht kein Kontingent. Ein unberechneter
            # schon: Er wird nach der Absage nie mehr berechnet (`saison_abrechenbar`).
            freie_absage = bool(
                kostenfrei
                and (
                    buchung.rechnung_position_id is None
                    or _offene_position(db, buchung) is not None
                )
            )
    s = Storno(
        buchung_id=buchung.id,
        durch=durch,
        kostenfrei=kostenfrei,
        freie_absage=freie_absage,
        grund=grund,
    )
    db.add(s)
    quelle = "admin" if durch == "betreiber" else ("portal" if durch == "kunde" else "system")
    buchungen.setze_status(
        db, buchung, Buchung.STORNIERT, quelle=quelle, admin_user_id=admin_user_id
    )
    db.flush()
    if kostenfrei and korrigieren:
        gutschreiben(
            db,
            buchung,
            grund=f"Storno: {grund}" if grund else "Storno",
            quelle=quelle,
            admin_user_id=admin_user_id,
        )
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
    """Stellt ein kostenpflichtiges Storno nachträglich frei – mit Korrekturbeleg (A-STORNO-4,
    A-STORNO-6). Ein bereits kostenfreies Storno bleibt unberührt – sonst entstünde ein zweites
    Mal Guthaben."""
    kunden.sperre_mehrere(db, [s.buchung.kunde_id])
    db.refresh(s)
    if s.kostenfrei:
        return
    vorher = audit.als_dict(s)
    s.kostenfrei = True
    s.grund = (s.grund + " | " if s.grund else "") + f"Kulanz: {grund}"
    db.flush()
    gutschreiben(
        db, s.buchung, grund=f"Kulanz: {grund}", quelle="admin", admin_user_id=admin_user_id
    )
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
