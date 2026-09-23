"""Sollzustand der Halle als reine Funktionen (A-HALLE-1, A-HALLE-2, A-HALLE-7).

Nur Buchungen schalten. Sperren stehen im Plan, schalten aber weder Licht noch Heizung und
öffnen keine Tür (Hallendienst-Spec § 1, Ⓞ-16).
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from beachhub_shared.hallenplan import HallenplanInhalt, PlanBuchung

Intervall = tuple[datetime, datetime]


@dataclass(frozen=True)
class Soll:
    steuern: bool
    licht: dict[str, bool] = field(default_factory=dict)
    heizung: Decimal | None = None


def zusammenlegen(intervalle: Iterable[Intervall]) -> list[Intervall]:
    ergebnis: list[Intervall] = []
    for von, bis in sorted(intervalle):
        if ergebnis and von <= ergebnis[-1][1]:
            ergebnis[-1] = (ergebnis[-1][0], max(ergebnis[-1][1], bis))
        else:
            ergebnis.append((von, bis))
    return ergebnis


def _enthaelt(intervalle: Iterable[Intervall], jetzt: datetime) -> bool:
    return any(von <= jetzt < bis for von, bis in intervalle)


def _gueltig(plan: HallenplanInhalt | None, jetzt: datetime) -> HallenplanInhalt | None:
    return plan if plan is not None and jetzt < plan.gueltig_bis else None


def sollzustand(
    plan: HallenplanInhalt | None, felder: Iterable[str], jetzt: datetime, handbetrieb: bool
) -> Soll:
    if handbetrieb:
        return Soll(steuern=False)
    felder = list(felder)
    if plan is None:
        # Noch nie ein Plan: Licht aus. Die Grundtemperatur kennt nur der Plan, also
        # bleibt die Heizung unangetastet.
        return Soll(steuern=True, licht=dict.fromkeys(felder, False), heizung=None)
    k = plan.konfig
    gueltig = _gueltig(plan, jetzt)
    if gueltig is None:
        return Soll(steuern=True, licht=dict.fromkeys(felder, False), heizung=k.grund_temperatur)
    vor = timedelta(minutes=k.licht_vorlauf_minuten)
    nach = timedelta(minutes=k.licht_nachlauf_minuten)
    licht = {
        f: _enthaelt(
            zusammenlegen(
                (b.beginn - vor, b.ende + nach) for b in gueltig.buchungen if b.feld_id == f
            ),
            jetzt,
        )
        for f in felder
    }
    heiz_vor = timedelta(minutes=k.heiz_vorlauf_minuten)
    heizen = _enthaelt(
        zusammenlegen((b.beginn - heiz_vor, b.ende) for b in gueltig.buchungen), jetzt
    )
    return Soll(
        steuern=True, licht=licht, heizung=k.spiel_temperatur if heizen else k.grund_temperatur
    )


def laufende_buchung(
    plan: HallenplanInhalt | None, feld_id: str, jetzt: datetime
) -> PlanBuchung | None:
    gueltig = _gueltig(plan, jetzt)
    if gueltig is None:
        return None
    return next(
        (b for b in gueltig.buchungen if b.feld_id == feld_id and b.beginn <= jetzt < b.ende), None
    )


def zutritt_offen(plan: HallenplanInhalt | None, jetzt: datetime) -> list[PlanBuchung]:
    gueltig = _gueltig(plan, jetzt)
    if gueltig is None:
        return []
    vorlauf = timedelta(minutes=gueltig.konfig.zutritt_vorlauf_minuten)
    return [b for b in gueltig.buchungen if b.beginn - vorlauf <= jetzt < b.ende]


def tuer_erwartet(plan: HallenplanInhalt | None, jetzt: datetime) -> bool:
    """Ob ein Öffnen der Tür gerade zu einer Buchung passt – für den Alarm
    `tuer_offen_ausserhalb`, nicht für die PIN-Prüfung. Das Fenster ist weiter als das
    Zutrittsfenster (`zutritt_offen`): `[beginn − zutritt_vorlauf, ende + licht_nachlauf)`, weil
    die Spieler die Halle erst nach dem Ende ihrer Buchung verlassen und dabei die Tür öffnen.
    Eine PIN öffnet nach `ende` trotzdem nicht mehr."""
    gueltig = _gueltig(plan, jetzt)
    if gueltig is None:
        return False
    vorlauf = timedelta(minutes=gueltig.konfig.zutritt_vorlauf_minuten)
    nachlauf = timedelta(minutes=gueltig.konfig.licht_nachlauf_minuten)
    return any(b.beginn - vorlauf <= jetzt < b.ende + nachlauf for b in gueltig.buchungen)
