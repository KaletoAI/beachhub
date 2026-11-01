import uuid
from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal

from beachhub_shared.zeit import BERLIN
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased

from beachhub_core import clock
from beachhub_core.models import (
    Buchung,
    GuthabenBuchung,
    GutscheinEinloesung,
    Rechnung,
    RechnungPosition,
    Storno,
)
from beachhub_core.services import (
    audit,
    buchungen,
    guthaben,
    gutschein_einloesung,
    gutschein_sperren,
    konfiguration,
    rechnungen,
    sperren,
)
from beachhub_core.services.gutschein_typen import GutscheinSperrkontext

NULL = Decimal("0.00")


class StornoFehler(Exception):  # noqa: N818
    pass


def _altgutschriften(db: Session, positionen: Sequence[RechnungPosition]) -> Decimal:
    """Vor 0014 schrieb ein kostenfreies Storno einer Onlinebuchung Guthaben ohne
    Korrekturbeleg gut; die Guthabenbuchung verweist dort auf das Storno statt auf einen Beleg.
    Holt der Betreiber den Beleg nach, ist dieser Betrag bereits erstattet."""
    buchung_ids = [p.buchung_id for p in positionen if p.buchung_id is not None]
    if not buchung_ids:
        return NULL
    summe = db.scalar(
        select(func.coalesce(func.sum(GuthabenBuchung.betrag), 0))
        .join(Storno, GuthabenBuchung.bezug_id == Storno.id)
        .where(
            GuthabenBuchung.art == "storno_gutschrift",
            Storno.buchung_id.in_(buchung_ids),
        )
    )
    return Decimal(str(summe))


def gutschreiben_positionen(
    db: Session,
    positionen: Sequence[RechnungPosition],
    *,
    grund: str,
    quelle: str,
    admin_user_id: uuid.UUID | None = None,
) -> Rechnung:
    """Korrigiert Positionen einer Rechnung und schreibt den bereits bezahlten Anteil als Guthaben
    gut. Ohne
    Korrekturbeleg bliebe Umsatzsteuer auf eine nicht erbrachte Leistung abgeführt (§ 17 UStG).
    Bei offener Rechnung sinkt zuerst die Forderung (A-RECH-7, Abweichung B-3)."""
    for position in positionen:
        rechnungen.pruefe_allgemeinen_rechnungsvorgang(position.rechnung, "teilstorno")
    if positionen:
        rechnungen.sperre(db, positionen[0].rechnung)
        rechnungen.pruefe_allgemeinen_rechnungsvorgang(positionen[0].rechnung, "teilstorno")
        rechnungen.pruefe_keine_abo_gutschrift(db, positionen)
    return _gutschreiben_positionen(
        db, positionen, grund=grund, quelle=quelle, admin_user_id=admin_user_id
    )


def _gutschreiben_positionen(
    db: Session,
    positionen: Sequence[RechnungPosition],
    *,
    grund: str,
    quelle: str,
    admin_user_id: uuid.UUID | None = None,
) -> Rechnung:
    """Korrigiert Positionen einer Rechnung und schreibt den bereits bezahlten Anteil als Guthaben
    gut. Ohne
    Korrekturbeleg bliebe Umsatzsteuer auf eine nicht erbrachte Leistung abgeführt (§ 17 UStG).
    Bei offener Rechnung sinkt zuerst die Forderung (A-RECH-7, Abweichung B-3).
    Gutschriften ohne Beleg aus der Zeit vor 0014 werden angerechnet (`_altgutschriften`).
    Interner Finanzweg für vollständig vorbereitete koordinierte Gutschein-Stornos.
    Öffentliche Aktionsguards gehören an die aufrufenden allgemeinen Dienste."""
    if not positionen:
        raise rechnungen.RechnungsFehler("keine_positionen")
    positionen = list({p.id: p for p in positionen}.values())
    rechnung = positionen[0].rechnung
    rechnungen.sperre(db, rechnung)
    offen_vorher = rechnungen.offener_betrag(db, rechnung)
    bereits_erstattet = _altgutschriften(db, positionen)
    beleg = rechnungen._korrigiere_positionen(
        db, positionen, grund=grund, quelle=quelle, admin_user_id=admin_user_id
    )
    betrag = sum((p.brutto for p in positionen), NULL)
    gutschrift = max(NULL, betrag - offen_vorher - bereits_erstattet)
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
    sperrkontext: GutscheinSperrkontext | None = None,
) -> list[Rechnung]:
    """Korrekturbelege für kostenfrei gewordene Buchungen – einer je Rechnung, etwa beim Beenden
    einer Dauerbuchung (A-RECH-7). Verknüpft jedes Storno mit seinem Beleg. Buchungen ohne
    (noch nicht korrigierte) Rechnungsposition bleiben ohne Beleg und ohne Guthaben."""
    if sperrkontext is None:
        sperrkontext = gutschein_sperren.bereite_buchungen_vor(db, (b.id for b in gebucht))
    belege = []
    gruppen: dict[uuid.UUID, list[tuple[Buchung, RechnungPosition]]] = {}
    for b in gebucht:
        # Saisonrechnung/Neuausstellung kann den zuvor geladenen Zeiger ersetzt haben.
        db.refresh(b)
        if (
            db.scalar(select(GutscheinEinloesung.id).where(GutscheinEinloesung.buchung_id == b.id))
            is not None
        ):
            rueck = gutschein_einloesung.storno_rueckgaengig(
                db,
                b,
                grund=grund,
                quelle=quelle,
                admin_user_id=admin_user_id,
                sperrkontext=sperrkontext,
            )
            belege.extend(rueck.korrekturbelege)
            continue
        pos = _offene_position(db, b)
        if pos is not None:
            gruppen.setdefault(pos.rechnung_id, []).append((b, pos))
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
                vorher = audit.als_dict(s)
                s.korrektur_rechnung_id = beleg.id
                audit.protokolliere(
                    db,
                    quelle=quelle,
                    objekt_typ="storno",
                    objekt_id=s.id,
                    vorher=vorher,
                    nachher=audit.als_dict(s),
                    admin_user_id=admin_user_id,
                )
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
    sperrkontext: GutscheinSperrkontext | None = None,
) -> Rechnung | None:
    belege = gutschreiben_alle(
        db,
        [buchung],
        grund=grund,
        quelle=quelle,
        admin_user_id=admin_user_id,
        sperrkontext=sperrkontext,
    )
    return belege[0] if belege else None


def ist_abo_termin(buchung: Buchung) -> bool:
    """Abo-Termine werden während des Abos nur gutgeschrieben, ihre Saisonrechnung bleibt."""
    return buchung.dauerbuchung_id is not None


def abo_gutschreiben(
    db: Session,
    s: Storno,
    *,
    quelle: str,
    admin_user_id: uuid.UUID | None = None,
) -> Decimal:
    """Abo-Gutschrift für einen kostenfrei gewordenen Abo-Termin: Der Positionsbetrag wird ohne
    Korrekturbeleg Guthaben, die Saisonrechnung bleibt unverändert und voll fällig. Genau einmal
    je Storno. Unberechnete Termine (etwa zwischen Vollstorno und Neuausstellung) und bereits
    korrigierte Positionen ergeben keine Gutschrift; ein kostenfreier unberechneter Termin wird
    nicht mehr berechnet (`saison_abrechenbar`)."""
    buchung = s.buchung
    # Saisonrechnung/Neuausstellung kann den zuvor geladenen Zeiger ersetzt haben.
    db.refresh(buchung)
    # Sperrreihenfolge wie bei Korrekturen: Kunde, dann Rechnung (`_offene_position`), dann Storno.
    pos = _offene_position(db, buchung)
    db.refresh(s, with_for_update=True)
    if s.abo_gutschrift is not None or pos is None:
        return NULL
    vorher = audit.als_dict(s)
    betrag = pos.brutto
    s.abo_gutschrift = betrag
    if betrag > NULL:
        guthaben.buche(
            db,
            kunde=buchung.kunde,
            betrag=betrag,
            art="abo_gutschrift",
            bezug_id=s.id,
            notiz=(
                f"Abo-Termin {buchung.beginn.astimezone(BERLIN):%d.%m.%Y} abgesagt "
                f"(Rechnung {pos.rechnung.nummer})"
            ),
            admin_user_id=admin_user_id,
            quelle=quelle,
        )
    db.flush()
    audit.protokolliere(
        db,
        quelle=quelle,
        objekt_typ="storno",
        objekt_id=s.id,
        vorher=vorher,
        nachher=audit.als_dict(s),
        admin_user_id=admin_user_id,
    )
    return betrag


def _wiedervergeben(db: Session, buchung: Buchung) -> bool:
    """Hat ein anderer Kunde den Platz des abgesagten Termins gebucht und bezahlt oder schuldet
    ihn? Zählen bestätigte, durchgeführte und nicht erschienene Buchungen sowie
    kostenpflichtig stornierte; jede Überschneidung auf demselben Feld genügt."""
    andere = aliased(Buchung)
    andere_storno = aliased(Storno)
    return (
        db.scalar(
            select(andere.id)
            .outerjoin(andere_storno, andere_storno.buchung_id == andere.id)
            .where(
                andere.id != buchung.id,
                andere.feld_id == buchung.feld_id,
                andere.kunde_id != buchung.kunde_id,
                andere.beginn < buchung.ende,
                andere.ende > buchung.beginn,
                or_(
                    andere.status.in_(
                        (Buchung.BESTAETIGT, Buchung.DURCHGEFUEHRT, Buchung.NICHT_ERSCHIENEN)
                    ),
                    and_(
                        andere.status == Buchung.STORNIERT,
                        andere_storno.kostenfrei.is_(False),
                    ),
                ),
            )
            .limit(1)
        )
        is not None
    )


def wiedervergabe_kandidaten(db: Session) -> list[uuid.UUID]:
    """Zu spät abgesagte Abo-Termine, deren Zeit vorbei ist und deren Platz ein anderer Kunde
    gebucht hat (A-DAUER-3 in der Fassung vom 01.11.2026)."""
    jetzt = clock.now(db)
    zeilen = db.execute(
        select(Storno.id, Buchung)
        .join(Buchung, Storno.buchung_id == Buchung.id)
        .where(
            Storno.durch == "kunde",
            Storno.kostenfrei.is_(False),
            Buchung.dauerbuchung_id.is_not(None),
            Buchung.status == Buchung.STORNIERT,
            Buchung.ende <= jetzt,
        )
        .order_by(Buchung.beginn)
    ).all()
    return [sid for sid, b in zeilen if _wiedervergeben(db, b)]


def wiedervergabe_gutschreiben(db: Session, storno_id: uuid.UUID) -> Storno | None:
    """Stellt eine zu spät abgesagte Abo-Absage nachträglich frei, weil der Platz wieder vergeben
    wurde, und schreibt den Termin gut. Liefert das Storno nur, wenn es jetzt frei wurde."""
    s = db.get(Storno, storno_id)
    if s is None:
        return None
    # Vollständige Kundenmenge vorbereiten wie bei jedem Storno-Finanzvorgang.
    gutschein_sperren.bereite_buchungen_vor(db, [s.buchung_id])
    db.refresh(s, with_for_update=True)
    if s.kostenfrei or not ist_abo_termin(s.buchung) or not _wiedervergeben(db, s.buchung):
        return None
    vorher = audit.als_dict(s)
    s.kostenfrei = True
    s.grund = (s.grund + " | " if s.grund else "") + "Platz wieder vergeben"
    db.flush()
    audit.protokolliere(
        db,
        quelle="system",
        objekt_typ="storno",
        objekt_id=s.id,
        vorher=vorher,
        nachher=audit.als_dict(s),
    )
    abo_gutschreiben(db, s, quelle="system")
    from beachhub_core.services import lesestand

    lesestand.markiere_geaendert(db, f"konto:{s.buchung.kunde_id}")
    return s


def storniere(
    db: Session,
    buchung: Buchung,
    *,
    durch: str,
    admin_user_id: uuid.UUID | None = None,
    grund: str = "",
    kostenfrei: bool | None = None,
    korrigieren: bool = True,
    sperrkontext: GutscheinSperrkontext | None = None,
) -> Storno:
    """Storniert eine Buchung. Ist das Storno kostenfrei, korrigiert es die Rechnung der Buchung
    (`gutschreiben`) – außer mit `korrigieren=False`: Dann bündelt der Aufrufer die Belege mit
    `gutschreiben_alle`. Abo-Termine korrigieren die Saisonrechnung während des Abos nicht; ihr
    Betrag wird Guthaben (`abo_gutschreiben`). Das Beenden eines Abos korrigiert dagegen mit
    `korrigieren=False` und `gutschreiben_alle`."""
    if sperrkontext is None:
        sperrkontext = gutschein_sperren.bereite_buchungen_vor(db, [buchung.id])
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
    if kostenfrei is None:
        frist = timedelta(hours=konfiguration.hole(db, "storno_frist_stunden"))
        kostenfrei = jetzt <= buchung.beginn - frist
        if ist_abo_termin(buchung) and not kostenfrei:
            # Ein bereits korrigierter Termin bleibt auch nach der Frist kostenfrei; es gibt
            # nichts mehr zu berechnen. Sonst bleibt er berechnet, bis der Platz wieder
            # vergeben ist (`wiedervergabe_gutschreiben`).
            kostenfrei = (
                buchung.rechnung_position_id is not None and _offene_position(db, buchung) is None
            )
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
    audit.protokolliere(
        db,
        quelle=quelle,
        objekt_typ="storno",
        objekt_id=s.id,
        vorher=None,
        nachher=audit.als_dict(s),
        admin_user_id=admin_user_id,
    )
    # Die Verknüpfung mit dem Korrekturbeleg protokolliert `gutschreiben_alle`.
    if kostenfrei and korrigieren and ist_abo_termin(buchung):
        abo_gutschreiben(db, s, quelle=quelle, admin_user_id=admin_user_id)
    elif kostenfrei and korrigieren:
        gutschreiben(
            db,
            buchung,
            sperrkontext=sperrkontext,
            grund=f"Storno: {grund}" if grund else "Storno",
            quelle=quelle,
            admin_user_id=admin_user_id,
        )
    return s


def kulanz(db: Session, s: Storno, *, admin_user_id: uuid.UUID | None, grund: str) -> None:
    """Stellt ein kostenpflichtiges Storno nachträglich frei – mit Korrekturbeleg (A-STORNO-4,
    A-STORNO-6). Ein bereits kostenfreies Storno bleibt unberührt – sonst entstünde ein zweites
    Mal Guthaben."""
    sperrkontext = gutschein_sperren.bereite_buchungen_vor(db, [s.buchung_id])
    db.refresh(s)
    if s.kostenfrei:
        return
    vorher = audit.als_dict(s)
    s.kostenfrei = True
    s.grund = (s.grund + " | " if s.grund else "") + f"Kulanz: {grund}"
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
    if ist_abo_termin(s.buchung):
        abo_gutschreiben(db, s, quelle="admin", admin_user_id=admin_user_id)
    else:
        gutschreiben(
            db,
            s.buchung,
            grund=f"Kulanz: {grund}",
            quelle="admin",
            admin_user_id=admin_user_id,
            sperrkontext=sperrkontext,
        )
    db.flush()
    from beachhub_core.services import lesestand

    lesestand.markiere_geaendert(db, f"konto:{s.buchung.kunde_id}")


# Verdrahtung der Hooks – einmalig beim Import
sperren.STORNIERE = storniere
