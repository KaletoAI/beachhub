"""Mitgliedschaft im Verein (A-KUND-4): Freischalten, Verlängern, Beenden, Antrag.

Der Status ist kein gespeichertes Kennzeichen, sondern `kunde.mitglied_bis`: Bis zu diesem Tag gilt
die Gruppe der Mitglieder, danach läuft die Mitgliedschaft von selbst aus
(services/kundengruppen.effektive_gruppe). Es gibt keinen Job, der Gruppen umschreibt.
"""

import csv
import io
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

from beachhub_shared.zeit import lokales_datum
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from beachhub_core import clock
from beachhub_core.models import AppSetting, Betriebszeit, Buchung, Kunde, utcnow
from beachhub_core.services import audit, konfiguration, kundengruppen
from beachhub_core.services.rechnungen import csv_sicher

Status = Literal["mitglied", "beantragt", "nicht_mitglied"]
MITGLIED: Status = "mitglied"
BEANTRAGT: Status = "beantragt"
NICHT_MITGLIED: Status = "nicht_mitglied"


class MitgliedschaftsFehler(Exception):  # noqa: N818
    pass


def naechster_ablauf(db: Session, nach: date) -> date:
    """Der nächste Ablauftag (Einstellung `mitgliedschaft_ablauf`) echt nach `nach`."""
    stichtag = konfiguration.hole(db, "mitgliedschaft_ablauf")
    d: date = stichtag.im_jahr(nach.year)
    return d if d > nach else stichtag.im_jahr(nach.year + 1)


def letzter_ablauf(db: Session, bis: date) -> date:
    """Der letzte Ablauftag am oder vor `bis`."""
    stichtag = konfiguration.hole(db, "mitgliedschaft_ablauf")
    d: date = stichtag.im_jahr(bis.year)
    return d if d <= bis else stichtag.im_jahr(bis.year - 1)


def status(kunde: Kunde, heute: date) -> Status:
    if kundengruppen.ist_mitglied_am(kunde, heute):
        return MITGLIED
    if kunde.mitglied_antrag_am is not None:
        return BEANTRAGT
    return NICHT_MITGLIED


def _protokolliere(
    db: Session,
    kunde: Kunde,
    vorher: dict[str, Any],
    aktion: str,
    *,
    quelle: str,
    admin_user_id: uuid.UUID | None,
) -> None:
    audit.protokolliere(
        db,
        quelle=quelle,
        objekt_typ="kunde",
        objekt_id=kunde.id,
        vorher=vorher,
        nachher={**audit.als_dict(kunde), "aktion": aktion},
        admin_user_id=admin_user_id,
    )
    from beachhub_core.services import lesestand

    lesestand.markiere_geaendert(db, f"konto:{kunde.id}")


def freischalten(db: Session, kunde: Kunde, *, bis: date, admin_user_id: uuid.UUID | None) -> None:
    """Schaltet frei oder verlängert bis `bis`. Ein offener Antrag ist damit erledigt; seine
    Angaben (Mitgliedsnummer) bleiben zur Nachvollziehbarkeit stehen."""
    if kunde.anonymisiert_am is not None:
        raise MitgliedschaftsFehler("kunde_anonymisiert")
    if bis < clock.today(db):
        raise MitgliedschaftsFehler("bis_vergangen")
    vorher = audit.als_dict(kunde)
    kunde.mitglied_bis = bis
    kunde.mitglied_freigeschaltet_am = utcnow()
    kunde.mitglied_freigeschaltet_von = admin_user_id
    kunde.mitglied_antrag_am = None
    kunde.mitglied_beendet_am = None
    kunde.mitglied_beendet_grund = ""
    db.flush()
    _protokolliere(
        db, kunde, vorher, "mitglied_freigeschaltet", quelle="admin", admin_user_id=admin_user_id
    )


def beende(db: Session, kunde: Kunde, *, grund: str, admin_user_id: uuid.UUID | None) -> bool:
    """Beendet die Mitgliedschaft sofort (Abweichung A-5) und liefert, ob sie bis heute galt –
    nur dann bekommt der Kunde eine Mail. Bestätigte Buchungen behalten ihre Konditionen
    (A-TARIF-3); künftige Termine zum Mitgliedspreis zeigt die Klärungsliste."""
    if kunde.mitglied_bis is None:
        raise MitgliedschaftsFehler("kein_mitglied")
    heute = clock.today(db)
    war_mitglied = kundengruppen.ist_mitglied_am(kunde, heute)
    vorher = audit.als_dict(kunde)
    if war_mitglied:
        kunde.mitglied_bis = heute - timedelta(days=1)
    kunde.mitglied_beendet_am = utcnow()
    kunde.mitglied_beendet_grund = grund.strip()[:300]
    db.flush()
    _protokolliere(
        db, kunde, vorher, "mitglied_beendet", quelle="admin", admin_user_id=admin_user_id
    )
    return war_mitglied


def verwerfe_antrag(db: Session, kunde: Kunde, *, admin_user_id: uuid.UUID | None) -> None:
    if kunde.mitglied_antrag_am is None:
        raise MitgliedschaftsFehler("kein_antrag")
    vorher = audit.als_dict(kunde)
    kunde.mitglied_antrag_am = None
    db.flush()
    _protokolliere(
        db, kunde, vorher, "antrag_verworfen", quelle="admin", admin_user_id=admin_user_id
    )


def offene_antraege(db: Session) -> list[Kunde]:
    return list(
        db.scalars(
            select(Kunde)
            .where(Kunde.mitglied_antrag_am.is_not(None), Kunde.anonymisiert_am.is_(None))
            .order_by(Kunde.mitglied_antrag_am)
        ).all()
    )


def beantrage(db: Session, kunde: Kunde, *, hinweis: str) -> None:
    """Vermerkt einen Antrag aus dem Portal. Ein erneuter Antrag ersetzt die Angaben des
    vorigen; entschieden wird im Admin-UI."""
    vorher = audit.als_dict(kunde)
    kunde.mitglied_antrag_am = utcnow()
    kunde.mitglied_antrag_hinweis = hinweis.strip()[:500]
    db.flush()
    _protokolliere(db, kunde, vorher, "antrag_gestellt", quelle="portal", admin_user_id=None)


def klaerungsfaelle(db: Session) -> list[Buchung]:
    """Künftige Buchungen zum Mitgliedspreis, deren Kunde am Termin kein Mitglied mehr ist
    (A-KUND-4, A-DAUER-5). Sie behalten ihre Konditionen, bis der Betreiber entscheidet."""
    kandidaten = db.scalars(
        select(Buchung)
        .where(
            Buchung.kundengruppe_id == kundengruppen.mitglied(db).id,
            Buchung.status.in_((Buchung.RESERVIERT, Buchung.BESTAETIGT)),
            Buchung.beginn > clock.now(db),
            Buchung.gruppe_geklaert_am.is_(None),
        )
        .order_by(Buchung.beginn)
    ).all()
    return [
        b for b in kandidaten if not kundengruppen.ist_mitglied_am(b.kunde, lokales_datum(b.beginn))
    ]


def klaere(db: Session, buchung: Buchung, *, admin_user_id: uuid.UUID | None) -> None:
    """Der Betreiber belässt die Buchung zu ihren Konditionen."""
    if buchung.gruppe_geklaert_am is not None:
        return
    vorher = audit.als_dict(buchung)
    buchung.gruppe_geklaert_am = utcnow()
    db.flush()
    audit.protokolliere(
        db,
        quelle="admin",
        objekt_typ="buchung",
        objekt_id=buchung.id,
        vorher=vorher,
        nachher={**audit.als_dict(buchung), "aktion": "gruppe_geklaert"},
        admin_user_id=admin_user_id,
    )


ABGLEICH_MARKER = "mitglieder_abgleich_letzter"


def pruefliste(db: Session, heute: date) -> list[Kunde]:
    """Prüfliste des Jahresabgleichs (A-KUND-5, Abweichung A-10): Mitgliedschaften, die seit dem
    Jahr vor dem letzten Ablauftag geendet haben oder bis zum nächsten Ablauftag enden – ohne
    ausdrücklich beendete."""
    letzter = letzter_ablauf(db, heute)
    von = letzter.replace(year=letzter.year - 1)
    bis = naechster_ablauf(db, heute)
    return list(
        db.scalars(
            select(Kunde)
            .where(
                Kunde.anonymisiert_am.is_(None),
                Kunde.mitglied_beendet_am.is_(None),
                Kunde.mitglied_bis > von,
                Kunde.mitglied_bis <= bis,
            )
            .order_by(Kunde.name)
        ).all()
    )


def pruefliste_csv(kunden: Sequence[Kunde]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow(["name", "email", "mitglied_bis", "angaben_im_antrag"])
    for k in kunden:
        w.writerow(
            [
                csv_sicher(k.name),
                csv_sicher(k.email),
                k.mitglied_bis.isoformat() if k.mitglied_bis else "",
                csv_sicher(k.mitglied_antrag_hinweis),
            ]
        )
    return buf.getvalue()


def verlaengere_alle(
    db: Session, kunden_ids: Sequence[uuid.UUID], *, admin_user_id: uuid.UUID | None
) -> list[Kunde]:
    """Sammelaktion „bis zum nächsten Ablauftag verlängern“. Wer schon so lange freigeschaltet
    ist, bleibt unberührt und bekommt keine Mail."""
    bis = naechster_ablauf(db, clock.today(db))
    verlaengert = []
    for k in db.scalars(
        select(Kunde)
        .where(Kunde.id.in_(kunden_ids), Kunde.anonymisiert_am.is_(None))
        .order_by(Kunde.name)
    ).all():
        if k.mitglied_bis is not None and k.mitglied_bis >= bis:
            continue
        freischalten(db, k, bis=bis, admin_user_id=admin_user_id)
        verlaengert.append(k)
    return verlaengert


def beende_alle(
    db: Session,
    kunden_ids: Sequence[uuid.UUID],
    *,
    grund: str,
    admin_user_id: uuid.UUID | None,
) -> list[tuple[Kunde, bool]]:
    """Sammelaktion „beenden“. Liefert je Kunde, ob die Mitgliedschaft bis heute galt – nur
    diese Kunden bekommen eine Mail."""
    return [
        (k, beende(db, k, grund=grund, admin_user_id=admin_user_id))
        for k in db.scalars(
            select(Kunde)
            .where(
                Kunde.id.in_(kunden_ids),
                Kunde.anonymisiert_am.is_(None),
                Kunde.mitglied_bis.is_not(None),
            )
            .order_by(Kunde.name)
        ).all()
    ]


def abgleich_warnung(db: Session, heute: date) -> str | None:
    """Warnt, wenn der Stichtag des Abgleichs nach dem ersten Buchungsfenster der nächsten Saison
    liegt (A-KUND-5). Saisonstart ist das früheste künftige `betriebszeit.gueltig_von`
    (Abweichung A-12)."""
    saisonstart = db.scalar(
        select(func.min(Betriebszeit.gueltig_von)).where(Betriebszeit.gueltig_von > heute)
    )
    if saisonstart is None:
        return None
    tag_monat = konfiguration.hole(db, "mitglieder_abgleich")
    stichtag: date = tag_monat.im_jahr(saisonstart.year)
    if stichtag > saisonstart:
        stichtag = tag_monat.im_jahr(saisonstart.year - 1)
    fenster_beginn = saisonstart - timedelta(days=konfiguration.hole(db, "fenster_tage"))
    if stichtag <= fenster_beginn:
        return None
    return (
        f"Der Mitglieder-Abgleich am {stichtag:%d.%m.%Y} liegt nach dem ersten Buchungsfenster "
        f"der Saison ab {saisonstart:%d.%m.%Y} (gebucht wird ab {fenster_beginn:%d.%m.%Y}). "
        "Mitglieder würden die ersten Termine zu Preisen für Nicht-Mitglieder buchen. Bitte den "
        "Stichtag in den Einstellungen vorziehen."
    )


@dataclass
class Tageslauf:
    # Anzahl Kunden auf der Prüfliste, wenn der Stichtag in diesem Jahr erreicht und noch nicht
    # gemeldet war; sonst None.
    abgleich: int | None = None
    erinnert: list[uuid.UUID] = field(default_factory=list)


def tageslauf(db: Session) -> Tageslauf:
    """Täglich: den Abgleich einmal im Jahr melden (auch nachträglich, falls der Lauf am Stichtag
    ausfiel), vor Ablauf an die Mitgliedschaft erinnern und das Konto im Portal am Tag nach dem
    Ablauf neu veröffentlichen. Committet nicht; die Mails verschickt
    jobs.mitgliedschaft_ausfuehren nach dem Commit."""
    heute = clock.today(db)
    lauf = Tageslauf()
    stichtag = konfiguration.hole(db, "mitglieder_abgleich").im_jahr(heute.year)
    marker = db.get(AppSetting, ABGLEICH_MARKER)
    if heute >= stichtag and (marker is None or marker.value != str(heute.year)):
        lauf.abgleich = len(pruefliste(db, heute))
        if marker is None:
            db.add(AppSetting(key=ABGLEICH_MARKER, value=str(heute.year)))
        else:
            marker.value = str(heute.year)
    tage = konfiguration.hole(db, "mitglied_erinnerung_tage")
    if tage > 0:
        for k in db.scalars(
            select(Kunde).where(
                Kunde.anonymisiert_am.is_(None),
                Kunde.mitglied_bis >= heute,
                Kunde.mitglied_bis <= heute + timedelta(days=tage),
            )
        ).all():
            if k.mitglied_erinnert_fuer != k.mitglied_bis:
                k.mitglied_erinnert_fuer = k.mitglied_bis
                lauf.erinnert.append(k.id)
    # Das Konto-Dokument zeigt Mitgliedschaft und Gruppe „Stand heute“: Am Tag nach dem Ablauf
    # muss es neu entstehen, sonst zeigt das Portal weiter die abgelaufene Mitgliedschaft.
    abgelaufen = db.scalars(
        select(Kunde.id).where(
            Kunde.anonymisiert_am.is_(None), Kunde.mitglied_bis == heute - timedelta(days=1)
        )
    ).all()
    if abgelaufen:
        from beachhub_core.services import lesestand

        lesestand.markiere_geaendert(db, *(f"konto:{i}" for i in abgelaufen))
    db.flush()
    return lauf
