"""Freie Zeiten aus dem Lesestand `belegung` – reine Funktionen ohne Datenbank.

Dieselben Regeln wie im Hauptsystem (`buchungen._pruefe_zeitraum`); verbindlich prüft aber
erst das Hauptsystem beim Buchen.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal

from beachhub_shared import slots as sl
from beachhub_shared.lesestand import BelegungInhalt, FeldInfo, TarifeInhalt
from beachhub_shared.zeit import lokales_datum

from beachhub_portal.services import tarife as tarif_dienst

Zustand = Literal["frei", "belegt", "vorbei"]


@dataclass(frozen=True)
class SlotAnzeige:
    beginn: datetime
    ende: datetime
    zustand: Zustand
    preis: Decimal | None


def _zeit(wert: str) -> time:
    h, m = wert.split(":")[:2]
    return time(int(h), int(m))


def _raster(feld: FeldInfo) -> list[sl.RasterKonfig]:
    return [
        sl.RasterKonfig(
            wochentag=r.wochentag,
            modus="fenster" if r.modus == "fenster" else "dauer",
            slot_minuten=r.slot_minuten,
            fenster=[(_zeit(a), _zeit(b)) for a, b in r.fenster],
        )
        for r in feld.raster
    ]


def _betriebszeiten(b: BelegungInhalt, datum: date) -> list[sl.Betriebszeit]:
    return [
        sl.Betriebszeit(z.wochentag, z.oeffnet, z.schliesst)
        for z in b.betriebszeiten
        if (z.gueltig_von is None or z.gueltig_von <= datum)
        and (z.gueltig_bis is None or z.gueltig_bis >= datum)
    ]


def _ausnahmen(b: BelegungInhalt, datum: date) -> list[sl.Ausnahme]:
    return [
        sl.Ausnahme(a.datum, a.geschlossen, a.oeffnet, a.schliesst)
        for a in b.ausnahmetage
        if a.datum == datum
    ]


def felder(b: BelegungInhalt) -> list[FeldInfo]:
    return sorted(b.felder, key=lambda f: f.reihenfolge)


def feld(b: BelegungInhalt, feld_id: str) -> FeldInfo | None:
    return next((f for f in b.felder if f.id == feld_id), None)


def tages_slots(b: BelegungInhalt, f: FeldInfo, datum: date) -> list[sl.Slot]:
    return sl.slots_fuer_tag(datum, _raster(f), _betriebszeiten(b, datum), _ausnahmen(b, datum))


def tage(b: BelegungInhalt, jetzt: datetime) -> list[date]:
    heute = lokales_datum(jetzt)
    return [heute + timedelta(days=i) for i in range(b.fenster_tage + 1)]


def zustand(b: BelegungInhalt, feld_id: str, slot: sl.Slot, jetzt: datetime) -> Zustand:
    for z in b.belegt.get(feld_id, []):
        if z.beginn < slot.ende and z.ende > slot.beginn:
            return "belegt"
    if slot.beginn < jetzt + timedelta(minutes=b.mindestvorlauf_minuten):
        return "vorbei"
    if slot.beginn > jetzt + timedelta(days=b.fenster_tage):
        return "vorbei"
    return "frei"


def tagesansicht(
    b: BelegungInhalt,
    t: TarifeInhalt | None,
    gruppe: str | None,
    datum: date,
    jetzt: datetime,
) -> list[tuple[FeldInfo, list[SlotAnzeige]]]:
    ansicht: list[tuple[FeldInfo, list[SlotAnzeige]]] = []
    for f in felder(b):
        liste: list[SlotAnzeige] = []
        for s in tages_slots(b, f, datum):
            z = zustand(b, f.id, s, jetzt)
            preis = None
            if t is not None and gruppe is not None and z == "frei":
                preis = tarif_dienst.preis(t, f.id, [s], gruppe)
            liste.append(SlotAnzeige(s.beginn, s.ende, z, preis))
        ansicht.append((f, liste))
    return ansicht


def folge(b: BelegungInhalt, feld_id: str, beginn: datetime, jetzt: datetime) -> list[sl.Slot]:
    """Der Slot ab `beginn` und alle direkt anschließenden, ebenfalls freien Slots."""
    f = feld(b, feld_id)
    if f is None:
        return []
    tages = tages_slots(b, f, lokales_datum(beginn))
    start = next((i for i, s in enumerate(tages) if s.beginn == beginn), None)
    if start is None or zustand(b, feld_id, tages[start], jetzt) != "frei":
        return []
    ergebnis = [tages[start]]
    for s in tages[start + 1 :]:
        if s.beginn != ergebnis[-1].ende or zustand(b, feld_id, s, jetzt) != "frei":
            break
        ergebnis.append(s)
    return ergebnis
