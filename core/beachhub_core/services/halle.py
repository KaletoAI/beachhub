"""Ereignisse und Status der Halle, Alarme an den Betreiber (Hallendienst-Spec § 2)."""

import uuid
from datetime import datetime, timedelta

from beachhub_shared.hallenplan import ALARM_TYPEN, DOKUMENT, EreignisLieferung, HallenStatus
from beachhub_shared.zeit import lokal
from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_core.models import AppSetting, Ereignis, Feld, HallenStatusZeile, LesestandVersion

KONTAKT_MARKER = "halle_ohne_kontakt_seit"
KONTAKT_GRENZE = timedelta(minutes=60)
NACHLIEFERUNG = timedelta(hours=6)

ALARM_BETREFF: dict[str, str] = {
    "tastenfeld_fehlversuche": "Tastenfeld: mehrere falsche Codes",
    "praesenz_ohne_buchung": "Anwesenheit ohne Buchung",
    "aktor_fehler": "Gerät in der Halle reagiert nicht",
    "ha_nicht_erreichbar": "Home Assistant nicht erreichbar",
    "plan_verworfen": "Halle hat den Plan verworfen",
    "tuer_offen_ausserhalb": "Tür außerhalb der Buchungszeiten geöffnet",
}


def _uuid(wert: str | None) -> uuid.UUID | None:
    if not wert:
        return None
    try:
        return uuid.UUID(wert)
    except ValueError:
        return None


def _zeit(t: datetime) -> str:
    return lokal(t).strftime("%d.%m.%Y %H:%M")


def speichere_ereignisse(
    db: Session, lieferung: EreignisLieferung, jetzt: datetime
) -> tuple[int, list[Ereignis]]:
    seqs = [e.seq for e in lieferung.ereignisse]
    vorhanden = (
        set(
            db.scalars(
                select(Ereignis.halle_seq).where(
                    Ereignis.halle_dienst_id == lieferung.dienst_id, Ereignis.halle_seq.in_(seqs)
                )
            ).all()
        )
        if seqs
        else set()
    )
    neu: list[Ereignis] = []
    for e in sorted(lieferung.ereignisse, key=lambda x: x.seq):
        if e.seq in vorhanden:
            continue
        vorhanden.add(e.seq)
        daten = dict(e.daten)
        if e.feld_id and _uuid(e.feld_id) is None:
            daten.setdefault("feld_id_unbekannt", e.feld_id)
        zeile = Ereignis(
            quelle="halle",
            typ=e.typ,
            zeitpunkt=e.zeitpunkt,
            feld_id=_uuid(e.feld_id),
            buchung_id=_uuid(e.buchung_id),
            daten_json=daten,
            halle_dienst_id=lieferung.dienst_id,
            halle_seq=e.seq,
        )
        db.add(zeile)
        neu.append(zeile)
    db.flush()
    # bestaetigt_bis ist die höchste LÜCKENLOS gespeicherte seq ab 1 (die Halle startet ihre
    # Zählung je Dienst-ID immer bei 1, siehe hall/beachhub_hall/db.py:dienst_id). Ein einfaches
    # max(halle_seq) würde bei einer Lücke (z. B. 1, 2, 4 – seq 3 fehlt) fälschlich bis 4
    # bestätigen; die Halle löscht dann seq 3 nie erneut aus ihrer Warteschlange, weil ihr
    # bestaetige_bis() alles bis zur bestätigten seq als zugestellt markiert.
    bestaetigt_bis = 0
    erwartet = 1
    for s in db.scalars(
        select(Ereignis.halle_seq)
        .where(Ereignis.halle_dienst_id == lieferung.dienst_id)
        .order_by(Ereignis.halle_seq)
    ):
        if s != erwartet:
            break
        bestaetigt_bis = s
        erwartet += 1
    return bestaetigt_bis, neu


def kontakt(db: Session, jetzt: datetime, status: HallenStatus | None) -> str | None:
    zeile = db.get(HallenStatusZeile, 1)
    if zeile is None:
        zeile = HallenStatusZeile(id=1, daten_json=None, empfangen_am=jetzt)
        db.add(zeile)
    if status is not None:
        zeile.daten_json = status.model_dump(mode="json")
    zeile.empfangen_am = jetzt
    marker = db.get(AppSetting, KONTAKT_MARKER)
    if marker is None or not marker.value:
        return None
    seit = datetime.fromisoformat(marker.value)
    db.delete(marker)
    dauer = jetzt - seit
    stunden, rest = divmod(int(dauer.total_seconds()) // 60, 60)
    return (
        f"Die Halle meldet sich wieder. Letzter Kontakt davor: {_zeit(seit)} Uhr "
        f"(ohne Kontakt: {stunden} h {rest} min). Ereignisse aus dieser Zeit werden nachgeliefert."
    )


def plan_neu(db: Session, planversion: int | None) -> bool:
    if planversion is None:
        return False
    zeile = db.get(LesestandVersion, DOKUMENT)
    return zeile is None or zeile.geaendert or planversion < zeile.version


def _zeile(e: Ereignis, felder: dict[uuid.UUID, str]) -> str:
    teile = [f"{_zeit(e.zeitpunkt)} Uhr", ALARM_BETREFF[e.typ]]
    if e.feld_id is not None and e.feld_id in felder:
        teile.append(f"Feld {felder[e.feld_id]}")
    if e.daten_json:
        teile.append(", ".join(f"{k}: {v}" for k, v in sorted(e.daten_json.items())))
    return " – ".join(teile)


def alarm_mails(db: Session, neu: list[Ereignis], jetzt: datetime) -> list[tuple[str, str]]:
    alarme = [e for e in neu if e.typ in ALARM_TYPEN]
    if not alarme:
        return []
    felder = {f.id: f.name for f in db.scalars(select(Feld))}
    hinweis = "\n\nDetails im Verwaltungsbereich unter System → Halle."
    frisch = [e for e in alarme if jetzt - e.zeitpunkt <= NACHLIEFERUNG]
    alt = [e for e in alarme if jetzt - e.zeitpunkt > NACHLIEFERUNG]
    mails = [(ALARM_BETREFF[e.typ], _zeile(e, felder) + hinweis) for e in frisch]
    if alt:
        mails.append(
            (
                f"{len(alt)} nachgelieferte Meldungen der Halle",
                "Nach einer Unterbrechung hat die Halle diese Meldungen nachgeliefert:\n\n"
                + "\n".join(_zeile(e, felder) for e in alt)
                + hinweis,
            )
        )
    return mails


def pruefe_kontakt(db: Session, jetzt: datetime) -> bool:
    """Job alle 5 min: meldet einmal, wenn die Halle seit 60 min schweigt (A-HALLE-8).
    Hat sich die Halle noch nie gemeldet, ist sie noch nicht eingerichtet – kein Alarm."""
    zeile = db.get(HallenStatusZeile, 1)
    if zeile is None or jetzt - zeile.empfangen_am <= KONTAKT_GRENZE:
        return False
    if db.get(AppSetting, KONTAKT_MARKER) is not None:
        return False
    db.add(AppSetting(key=KONTAKT_MARKER, value=zeile.empfangen_am.isoformat()))
    db.commit()

    from beachhub_core.services import benachrichtigung

    benachrichtigung.betreiber_alarm(
        "Halle ohne Kontakt",
        f"Die Halle hat sich seit {_zeit(zeile.empfangen_am)} Uhr nicht mehr gemeldet. "
        "Sie arbeitet mit ihrem gespeicherten Plan weiter; neue Buchungen kennt sie erst nach "
        "der Rückkehr. Bitte WireGuard-Verbindung und Hallenrechner prüfen.",
    )
    return True
