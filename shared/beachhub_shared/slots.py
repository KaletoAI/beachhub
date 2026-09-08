"""Slot-Berechnung: reine Funktionen ohne Datenbankbezug."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal

from beachhub_shared.zeit import kombiniere


@dataclass
class RasterKonfig:
    wochentag: int | None
    modus: Literal["dauer", "fenster"]
    slot_minuten: int | None
    fenster: list[tuple[time, time]]


@dataclass
class Betriebszeit:
    wochentag: int
    oeffnet: time
    schliesst: time


@dataclass
class Ausnahme:
    datum: date
    geschlossen: bool
    oeffnet: time | None
    schliesst: time | None


@dataclass(frozen=True)
class Slot:
    beginn: datetime
    ende: datetime


def _oeffnungszeit(
    datum: date, betriebszeiten: list[Betriebszeit], ausnahmen: list[Ausnahme]
) -> tuple[datetime, datetime] | None:
    for a in ausnahmen:
        if a.datum == datum:
            if a.geschlossen or a.oeffnet is None or a.schliesst is None:
                return None
            return kombiniere(datum, a.oeffnet), _ende(datum, a.schliesst)
    for b in betriebszeiten:
        if b.wochentag == datum.weekday():
            return kombiniere(datum, b.oeffnet), _ende(datum, b.schliesst)
    return None


def _ende(datum: date, uhrzeit: time) -> datetime:
    """Schließzeit 00:00 bedeutet Mitternacht am Folgetag."""
    if uhrzeit == time(0, 0):
        return kombiniere(datum + timedelta(days=1), uhrzeit)
    return kombiniere(datum, uhrzeit)


def _raster_fuer(datum: date, raster: list[RasterKonfig]) -> RasterKonfig | None:
    spezifisch = [r for r in raster if r.wochentag == datum.weekday()]
    if spezifisch:
        return spezifisch[0]
    allgemein = [r for r in raster if r.wochentag is None]
    return allgemein[0] if allgemein else None


def slots_fuer_tag(
    datum: date,
    raster: list[RasterKonfig],
    betriebszeiten: list[Betriebszeit],
    ausnahmen: list[Ausnahme],
) -> list[Slot]:
    zeit = _oeffnungszeit(datum, betriebszeiten, ausnahmen)
    r = _raster_fuer(datum, raster)
    if zeit is None or r is None:
        return []
    oeffnet, schliesst = zeit
    ergebnis: list[Slot] = []
    if r.modus == "dauer":
        if not r.slot_minuten or r.slot_minuten <= 0:
            return []
        schritt = timedelta(minutes=r.slot_minuten)
        t = oeffnet
        while t + schritt <= schliesst:
            ergebnis.append(Slot(t, t + schritt))
            t += schritt
    else:
        for von, bis in r.fenster:
            b, e = kombiniere(datum, von), _ende(datum, bis)
            if e <= b:
                continue
            if b >= oeffnet and e <= schliesst:
                ergebnis.append(Slot(b, e))
    return sorted(ergebnis, key=lambda s: s.beginn)


def slots_im_zeitraum(beginn: datetime, ende: datetime, tages_slots: list[Slot]) -> list[Slot]:
    return [s for s in tages_slots if s.beginn >= beginn and s.ende <= ende]


def zeitraum_ist_slotfolge(beginn: datetime, ende: datetime, tages_slots: list[Slot]) -> bool:
    teil = slots_im_zeitraum(beginn, ende, tages_slots)
    if not teil or teil[0].beginn != beginn or teil[-1].ende != ende:
        return False
    return all(teil[i].ende == teil[i + 1].beginn for i in range(len(teil) - 1))
