"""Aufbau der Verwaltungsnavigation.

Die Navigation steht an einer Stelle, weil `templating.render` sie in jede Seite gibt:
Hauptmenü und Unternavigation erscheinen dadurch überall gleich, und eine neue Seite wird
sichtbar, sobald sie hier eingetragen ist – nicht erst, wenn jemand sie von Hand verlinkt.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Punkt:
    name: str
    pfad: str


@dataclass(frozen=True)
class Bereich:
    name: str
    pfad: str
    unterpunkte: list[Punkt] = field(default_factory=list)


NAVIGATION: list[Bereich] = [
    Bereich(
        "Belegung",
        "/admin/belegung",
        [
            Punkt("Wochenplan", "/admin/belegung"),
            Punkt("Buchung anlegen", "/admin/belegung/buchung/neu"),
            Punkt("Dauerbuchung anlegen", "/admin/belegung/dauer/neu"),
            Punkt("Sperre anlegen", "/admin/belegung/sperre/neu"),
        ],
    ),
    Bereich("Kunden", "/admin/kunden"),
    Bereich("Rechnungen", "/admin/rechnungen"),
    Bereich(
        "Stammdaten",
        "/admin/felder",
        [
            Punkt("Felder", "/admin/felder"),
            Punkt("Betriebszeiten", "/admin/betriebszeiten"),
            Punkt("Ausnahmetage", "/admin/ausnahmetage"),
            Punkt("Kundengruppen", "/admin/kundengruppen"),
            Punkt("Tarife", "/admin/tarife"),
            Punkt("Konfiguration", "/admin/konfiguration"),
        ],
    ),
    Bereich(
        "System",
        "/admin/system",
        [
            Punkt("Übersicht", "/admin/system"),
            Punkt("Kostenpflichtige Stornos", "/admin/system/stornos"),
            Punkt("Änderungsprotokoll", "/admin/system/audit"),
        ],
    ),
]


def _passt(pfad: str, ziel: str) -> bool:
    """Vergleicht auf Segmentgrenzen: `/admin/kundengruppen` liegt nicht unter `/admin/kunden`."""
    return pfad == ziel or pfad.startswith(ziel + "/")


def _treffer(pfad: str, ziele: list[str]) -> str | None:
    """Das genaueste (längste) passende Ziel, damit Unterpunkte vor Bereichen gewinnen."""
    passende = [z for z in ziele if _passt(pfad, z)]
    return max(passende, key=len) if passende else None


def bereich_fuer_pfad(pfad: str) -> Bereich | None:
    """Der Bereich, zu dem eine Seite gehört – auch wenn ihr Pfad nicht mit dem Bereichspfad
    beginnt, wie es bei den Stammdaten der Fall ist (`/admin/tarife` unter `Stammdaten`)."""
    bester: Bereich | None = None
    beste_laenge = -1
    for bereich in NAVIGATION:
        ziel = _treffer(pfad, [bereich.pfad] + [u.pfad for u in bereich.unterpunkte])
        if ziel is not None and len(ziel) > beste_laenge:
            bester, beste_laenge = bereich, len(ziel)
    return bester


def unterpunkt_fuer_pfad(bereich: Bereich, pfad: str) -> Punkt | None:
    ziel = _treffer(pfad, [u.pfad for u in bereich.unterpunkte])
    return next((u for u in bereich.unterpunkte if u.pfad == ziel), None) if ziel else None
