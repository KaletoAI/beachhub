"""Ereignisse und Status der Halle, Alarme an den Betreiber (Hallendienst-Spec § 2)."""

import uuid
from datetime import datetime, timedelta
from typing import Any

from beachhub_shared.hallenplan import ALARM_TYPEN, DOKUMENT, EreignisLieferung, HallenStatus
from beachhub_shared.zeit import lokal
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from beachhub_core.models import (
    AppSetting,
    Ereignis,
    Feld,
    HalleDienst,
    HallenStatusZeile,
    LesestandVersion,
    utcnow,
)

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

# Nur diese Schlüssel aus `daten_json` landen in der Alarm-Mail (Fix-Runde 1, Punkt 4): Die
# Halle meldet nach Vertrag keine PIN, aber `daten` ist ein freies dict – ohne Allowlist würde
# jeder künftige, unbedachte Zusatzschlüssel ungefiltert in einer Mail landen. Felder je Typ
# nach hall/beachhub_hall (pin.py, tuer.py, aufgaben/steuerung.py, aufgaben/ha_zuhoerer.py,
# aufgaben/plan_abruf.py).
ALARM_FELDER: dict[str, tuple[str, ...]] = {
    "tastenfeld_fehlversuche": ("anzahl",),
    "praesenz_ohne_buchung": ("minuten",),
    "aktor_fehler": ("entity", "grund"),
    "ha_nicht_erreichbar": ("seit",),
    "plan_verworfen": ("grund", "version"),
    "tuer_offen_ausserhalb": (),
}
WERT_MAX_LAENGE = 200

# Dieselbe Idee wie ALARM_FELDER, aber für alle 15 Ereignistypen (EREIGNISTYPEN): Die
# Admin-Seite „Halle“ zeigt `daten_json` nie ungefiltert, sondern nur die für den jeweiligen
# Typ erwarteten Schlüssel, Werte gekürzt. Enthält ALARM_FELDER als Teilmenge.
EREIGNIS_FELDER: dict[str, tuple[str, ...]] = {
    "pin_akzeptiert": ("feld_id", "buchung_id", "master"),
    "pin_abgelehnt": ("fehlversuche",),
    "tastenfeld_fehlversuche": ("anzahl",),
    "praesenz_start": ("feld_id", "buchung_id"),
    "praesenz_ende": ("feld_id", "dauer_minuten"),
    "praesenz_ohne_buchung": ("feld_id", "minuten"),
    "tuer_offen_ausserhalb": (),
    "licht_geschaltet": ("feld_id", "entity", "an"),
    "heizung_gesetzt": ("soll", "ist_temperatur"),
    "ha_nicht_erreichbar": ("seit",),
    "aktor_fehler": ("feld_id", "entity", "grund"),
    "plan_verworfen": ("grund", "version"),
    "handbetrieb_an": (),
    "handbetrieb_aus": (),
    "dienst_gestartet": ("version",),
}


def _kuerze(wert: Any) -> str:
    text = str(wert)
    return text if len(text) <= WERT_MAX_LAENGE else text[:WERT_MAX_LAENGE] + "…"


def anzeige_daten(e: Ereignis) -> dict[str, str]:
    """Gefilterte, gekürzte Ereignisdaten für die Admin-Seite „Halle“ (nie das rohe
    `daten_json` anzeigen, siehe EREIGNIS_FELDER)."""
    erlaubt = EREIGNIS_FELDER.get(e.typ, ())
    return {k: _kuerze(v) for k, v in e.daten_json.items() if k in erlaubt}


def _uuid(wert: str | None) -> uuid.UUID | None:
    if not wert:
        return None
    try:
        return uuid.UUID(wert)
    except ValueError:
        return None


def _zeit(t: datetime) -> str:
    return lokal(t).strftime("%d.%m.%Y %H:%M")


def speichere_ereignisse(db: Session, lieferung: EreignisLieferung) -> tuple[int, list[Ereignis]]:
    # Je Dienst-ID genau eine Zeile mit der zuletzt bestätigten seq (Upsert, falls neu) – dann
    # sperren (SELECT … FOR UPDATE). Das serialisiert parallele Lieferungen derselben Dienst-ID:
    # eine zweite, gleichzeitige Lieferung wartet hier, bis die erste committet hat, und sieht
    # danach deren bereits gespeicherte Ereignisse – kein IntegrityError durch einen doppelten
    # Insert-Versuch für dieselbe (Dienst-ID, seq) (Fix-Runde 1, Punkt 2).
    db.execute(
        pg_insert(HalleDienst)
        .values(dienst_id=lieferung.dienst_id, bestaetigt_bis=0)
        .on_conflict_do_nothing(index_elements=["dienst_id"])
    )
    dienst = db.execute(
        select(HalleDienst).where(HalleDienst.dienst_id == lieferung.dienst_id).with_for_update()
    ).scalar_one()

    # Die Halle liefert immer ab ihrer niedrigsten unbestätigten seq (Melder: unbestaetigt() in
    # seq-Reihenfolge); alles davor hat das Hauptsystem ihr also schon einmal bestätigt. Liegt die
    # gespeicherte Marke darunter – etwa nach einer Wiederherstellung aus einem Backup, das älter
    # ist als der Stand der Halle –, fehlen die Ereignisse dazwischen hier für immer: Die
    # Lückenprüfung unten bliebe dann dauerhaft bei der alten Marke stehen, die Halle bekäme nie
    # eine Bestätigung und lieferte endlos. Deshalb die Marke auf „kleinste gelieferte seq − 1“
    # nachziehen (nur anheben, nie senken) und fortschreiben.
    if lieferung.ereignisse:
        untergrenze = min(e.seq for e in lieferung.ereignisse) - 1
        if untergrenze > dienst.bestaetigt_bis:
            dienst.bestaetigt_bis = untergrenze

    # created_at/updated_at mit der echten Uhr, nicht mit `jetzt` (clock.now, vom Admin
    # überschreibbar für Tests/Abnahme): Ein Datums-Override darf nicht verfälschen, wann ein
    # Ereignis tatsächlich empfangen wurde.
    empfangen = utcnow()
    zeilen: list[dict[str, Any]] = []
    gesehen: set[int] = set()
    for e in sorted(lieferung.ereignisse, key=lambda x: x.seq):
        # Bereits bestätigt oder innerhalb dieser Lieferung doppelt gesendet: gar nicht erst für
        # den Insert vormerken (Duplikate werden zusätzlich unten per ON CONFLICT abgefangen).
        if e.seq <= dienst.bestaetigt_bis or e.seq in gesehen:
            continue
        gesehen.add(e.seq)
        daten = dict(e.daten)
        if e.feld_id and _uuid(e.feld_id) is None:
            daten.setdefault("feld_id_unbekannt", e.feld_id)
        zeilen.append(
            {
                "id": uuid.uuid4(),
                "quelle": "halle",
                "typ": e.typ,
                "zeitpunkt": e.zeitpunkt,
                "feld_id": _uuid(e.feld_id),
                "buchung_id": _uuid(e.buchung_id),
                "daten_json": daten,
                "halle_dienst_id": lieferung.dienst_id,
                "halle_seq": e.seq,
                "created_at": empfangen,
                "updated_at": empfangen,
            }
        )

    neu: list[Ereignis] = []
    if zeilen:
        # ON CONFLICT DO NOTHING statt „erst prüfen, dann einfügen“: Auch falls zwei Prozesse
        # (z. B. nach einem Neustart mit alter und neuer Dienst-ID-Kombination) doch einmal
        # gleichzeitig dieselbe (Dienst-ID, seq) einfügen wollen, entsteht kein IntegrityError,
        # sondern die zweite Zeile wird stillschweigend übersprungen (Fix-Runde 1, Punkt 2).
        eingefuegte_seqs = list(
            db.scalars(
                pg_insert(Ereignis)
                .values(zeilen)
                .on_conflict_do_nothing(constraint="ereignis_halle_seq_eindeutig")
                .returning(Ereignis.halle_seq)
            )
        )
        if eingefuegte_seqs:
            neu = list(
                db.scalars(
                    select(Ereignis).where(
                        Ereignis.halle_dienst_id == lieferung.dienst_id,
                        Ereignis.halle_seq.in_(eingefuegte_seqs),
                    )
                )
            )

    # Lückenlos ab der gespeicherten Marke weiterzählen (nicht ab 1 über die ganze Tabelle
    # scannen): Ereignisse werden nach 90 Tagen gelöscht (Spec § 10); ein Scan ab 1 würde nach
    # dem Aufräumen dauerhaft bei 0 hängen bleiben und mit wachsender Historie zusätzlich immer
    # langsamer werden (Fix-Runde 1, Punkt 1). Ein einfaches max(halle_seq) wäre zudem bei einer
    # Lücke (z. B. 1, 2, 4 – seq 3 fehlt) falsch: Die Halle markiert mit ihrem bestaetige_bis()
    # alles bis zur bestätigten seq als zugestellt und würde seq 3 nie nachliefern.
    marke = dienst.bestaetigt_bis
    erwartet = marke + 1
    for s in db.scalars(
        select(Ereignis.halle_seq)
        .where(Ereignis.halle_dienst_id == lieferung.dienst_id, Ereignis.halle_seq > marke)
        .order_by(Ereignis.halle_seq)
    ):
        if s != erwartet:
            break
        marke = s
        erwartet += 1
    dienst.bestaetigt_bis = marke
    db.flush()
    return marke, neu


def kontakt(db: Session, jetzt: datetime, status: HallenStatus | None) -> str | None:
    # Upsert + Sperre der Singleton-Zeile (id=1): serialisiert parallele Aufrufe aus /hall/status
    # und /hall/ereignisse, damit die Kontakt-Marker-Entwarnung nie doppelt ausgelöst wird – ohne
    # die Sperre könnten zwei gleichzeitige Aufrufe den Marker beide lesen, bevor der eine ihn
    # löscht, und beide eine „wieder verbunden“-Mail auslösen (Fix-Runde 1, Punkt 2).
    db.execute(
        pg_insert(HallenStatusZeile)
        .values(id=1, daten_json=None, empfangen_am=jetzt)
        .on_conflict_do_nothing(index_elements=["id"])
    )
    zeile = db.execute(
        select(HallenStatusZeile).where(HallenStatusZeile.id == 1).with_for_update()
    ).scalar_one()
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
    # Nur die für diesen Typ erwarteten Schlüssel, Werte gekürzt (Fix-Runde 1, Punkt 4): daten
    # ist ein freies dict der Halle; ohne Allowlist würde jeder unerwartete Zusatzschlüssel
    # (z. B. das defensive feld_id_unbekannt) ungefiltert in der Mail landen.
    erlaubt = ALARM_FELDER.get(e.typ, ())
    eintraege = {k: v for k, v in e.daten_json.items() if k in erlaubt}
    if eintraege:
        teile.append(", ".join(f"{k}: {_kuerze(v)}" for k, v in sorted(eintraege.items())))
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
    # Mit FOR UPDATE wie kontakt(): serialisiert Job und Zustellung (/hall/ereignisse,
    # /hall/status), damit nie beide gleichzeitig den Marker setzen bzw. löschen.
    zeile = db.execute(
        select(HallenStatusZeile).where(HallenStatusZeile.id == 1).with_for_update()
    ).scalar_one_or_none()
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
