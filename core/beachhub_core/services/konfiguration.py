"""Konfigurationswerte: Defaults im Code, Überschreibung in der Tabelle `konfiguration`."""

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from beachhub_core.models import Konfiguration
from beachhub_core.services import audit

DEFAULTS: dict[str, tuple[type, Any]] = {
    "fenster_tage": (int, 14),
    "mindestvorlauf_minuten": (int, 60),
    "storno_frist_stunden": (int, 24),
    "zahlungsfrist_minuten": (int, 15),
    "ust_satz": (Decimal, Decimal("19.00")),
    "rechnung_tag_im_folgemonat": (int, 3),
    "rechnung_zahlungsziel_tage": (int, 14),
    "heiz_vorlauf_minuten": (int, 60),
    "licht_vorlauf_minuten": (int, 5),
    "licht_nachlauf_minuten": (int, 5),
    "zutritt_vorlauf_minuten": (int, 15),
    "spiel_temperatur": (Decimal, Decimal("16.0")),
    "grund_temperatur": (Decimal, Decimal("8.0")),
    "antwort_hinweis_sekunden": (int, 120),
    "pin_laenge": (int, 6),
}

_TYP_NAME = {int: "int", Decimal: "decimal", str: "str", bool: "bool"}


@dataclass(frozen=True)
class Beschreibung:
    """Wie ein Konfigurationswert in der Verwaltung erscheint. Ohne diese Angaben stünden
    dort die technischen Schlüssel untereinander, die niemand ohne Spezifikation deutet."""

    gruppe: str
    name: str
    einheit: str = ""
    hilfe: str = ""


BESCHREIBUNGEN: dict[str, Beschreibung] = {
    "fenster_tage": Beschreibung(
        "Buchung und Storno",
        "Buchungsfenster",
        "Tage",
        "So viele Tage im Voraus sehen Kunden freie Zeiten und können sie buchen.",
    ),
    "mindestvorlauf_minuten": Beschreibung(
        "Buchung und Storno",
        "Mindestvorlauf",
        "Minuten",
        "So kurz vor Beginn ist eine Buchung noch möglich.",
    ),
    "storno_frist_stunden": Beschreibung(
        "Buchung und Storno",
        "Stornofrist",
        "Stunden vor Beginn",
        "Bis zu dieser Frist ist die Stornierung kostenfrei.",
    ),
    "zahlungsfrist_minuten": Beschreibung(
        "Zahlung und Rechnung",
        "Zahlungsfrist",
        "Minuten",
        "So lange bleibt eine Reservierung nach der Buchung für die Online-Zahlung bestehen.",
    ),
    "ust_satz": Beschreibung(
        "Zahlung und Rechnung",
        "Umsatzsteuersatz",
        "Prozent",
        "Gilt für alle Rechnungen.",
    ),
    "rechnung_tag_im_folgemonat": Beschreibung(
        "Zahlung und Rechnung",
        "Tag des Rechnungslaufs",
        "Tag im Folgemonat",
    ),
    "rechnung_zahlungsziel_tage": Beschreibung(
        "Zahlung und Rechnung",
        "Zahlungsziel",
        "Tage",
    ),
    "heiz_vorlauf_minuten": Beschreibung(
        "Halle",
        "Heizvorlauf",
        "Minuten",
        "So lange vor der ersten Buchung eines Blocks heizt die Halle auf Spieltemperatur.",
    ),
    "spiel_temperatur": Beschreibung("Halle", "Spieltemperatur", "Grad"),
    "grund_temperatur": Beschreibung(
        "Halle", "Grundtemperatur", "Grad", "Temperatur außerhalb der Buchungen."
    ),
    "licht_vorlauf_minuten": Beschreibung("Halle", "Licht an vor Beginn", "Minuten"),
    "licht_nachlauf_minuten": Beschreibung("Halle", "Licht aus nach Ende", "Minuten"),
    "zutritt_vorlauf_minuten": Beschreibung(
        "Halle",
        "Zahlencode gültig ab",
        "Minuten vor Beginn",
        "Bis zum Ende der Buchung bleibt der Code gültig.",
    ),
    "antwort_hinweis_sekunden": Beschreibung(
        "Portal und Zugang",
        "Hinweis auf verzögerte Antwort",
        "Sekunden",
        "So lange wartet das Portal auf die Antwort des Hauptsystems, bevor es den Kunden "
        "um Geduld bittet.",
    ),
    "pin_laenge": Beschreibung("Portal und Zugang", "Länge des Zahlencodes", "Stellen"),
}

# Reihenfolge der Gruppen auf der Konfigurationsseite.
GRUPPEN: list[str] = ["Buchung und Storno", "Zahlung und Rechnung", "Halle", "Portal und Zugang"]


def gruppiert(werte: dict[str, Any]) -> list[tuple[str, list[tuple[str, Any, Beschreibung]]]]:
    """Ordnet die Werte den Gruppen zu, in der Reihenfolge von GRUPPEN."""
    return [
        (
            gruppe,
            [
                (schluessel, werte[schluessel], BESCHREIBUNGEN[schluessel])
                for schluessel in DEFAULTS
                if schluessel in werte and BESCHREIBUNGEN[schluessel].gruppe == gruppe
            ],
        )
        for gruppe in GRUPPEN
    ]


def _parse(typ: type, roh: str) -> Any:
    if typ is bool:
        return roh.lower() in ("1", "true", "ja")
    if typ is Decimal:
        # Die Oberfläche zeigt Dezimalzahlen deutsch mit Komma und bekommt sie so zurück.
        roh = roh.strip().replace(",", ".")
    return typ(roh)


def hole(db: Session, schluessel: str) -> Any:
    typ, default = DEFAULTS[schluessel]
    zeile = db.get(Konfiguration, schluessel)
    return _parse(typ, zeile.wert) if zeile else default


def setze(db: Session, schluessel: str, wert: Any, admin_user_id: uuid.UUID | None = None) -> None:
    typ, default = DEFAULTS[schluessel]
    zeile = db.get(Konfiguration, schluessel)
    vorher = zeile.wert if zeile else str(default)
    neu = str(_parse(typ, str(wert)))
    if zeile:
        zeile.wert = neu
    else:
        db.add(Konfiguration(schluessel=schluessel, wert=neu, typ=_TYP_NAME[typ]))
    audit.protokolliere(
        db,
        quelle="admin",
        objekt_typ="konfiguration",
        objekt_id=None,
        vorher={"wert": vorher},
        nachher={"wert": neu, "schluessel": schluessel},
        admin_user_id=admin_user_id,
    )
    if schluessel in ("fenster_tage", "mindestvorlauf_minuten"):
        from beachhub_core.services import lesestand

        lesestand.markiere_geaendert(db, "belegung")
