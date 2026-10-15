import uuid
from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from beachhub_core import clock
from beachhub_core.models import (
    Buchung,
    Dauerbuchung,
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
    sperrkontext: GutscheinSperrkontext | None = None,
) -> Storno:
    """Storniert eine Buchung. Ist das Storno kostenfrei, korrigiert es die Rechnung der Buchung
    (`gutschreiben`) – außer mit `korrigieren=False`: Dann bündelt der Aufrufer die Belege mit
    `gutschreiben_alle`."""
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
            # Ein bereits gutgeschriebener Termin bleibt kostenfrei und verbraucht kein
            # Kontingent (A-DAUER-3); es gibt nichts mehr zu berechnen. Ein unberechneter
            # verbraucht Kontingent: Er wird nach der Absage nie mehr berechnet
            # (`saison_abrechenbar`).
            gutgeschrieben = (
                buchung.rechnung_position_id is not None and _offene_position(db, buchung) is None
            )
            if gutgeschrieben:
                kostenfrei = True
            elif kostenfrei:
                kostenfrei = bool(dauer and freie_absagen_rest(db, dauer) > 0)
                freie_absage = kostenfrei
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
    if kostenfrei and korrigieren:
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
    gutschreiben(
        db,
        s.buchung,
        grund=f"Kulanz: {grund}",
        quelle="admin",
        admin_user_id=admin_user_id,
        sperrkontext=sperrkontext,
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
