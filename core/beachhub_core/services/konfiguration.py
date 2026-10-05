"""Konfigurationswerte: Defaults im Code, Überschreibung in der Tabelle `konfiguration`."""

import re
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from beachhub_core.models import Konfiguration
from beachhub_core.services import audit


class TagMonat(str):
    """Ein jährlicher Stichtag ohne Jahr, gespeichert als "TT.MM." (etwa "30.04.")."""

    __slots__ = ()

    def __new__(cls, roh: str) -> "TagMonat":
        m = re.fullmatch(r"\s*(\d{1,2})\.(\d{1,2})\.?\s*", roh)
        if m is None:
            raise ValueError("Bitte als Tag und Monat angeben, etwa 30.04.")
        tag, monat = int(m.group(1)), int(m.group(2))
        try:
            # 2027 ist kein Schaltjahr: Der 29.02. taugt nicht als jährlicher Stichtag.
            date(2027, monat, tag)
        except ValueError as e:
            raise ValueError("Diesen Tag gibt es nicht in jedem Jahr") from e
        return super().__new__(cls, f"{tag:02d}.{monat:02d}.")

    @property
    def tag(self) -> int:
        return int(self[:2])

    @property
    def monat(self) -> int:
        return int(self[3:5])

    def im_jahr(self, jahr: int) -> date:
        return date(jahr, self.monat, self.tag)


DEFAULTS: dict[str, tuple[type, Any]] = {
    "fenster_tage": (int, 14),
    "mindestvorlauf_minuten": (int, 60),
    "storno_frist_stunden": (int, 24),
    "abo_nur_mitglieder": (bool, True),
    "abo_freie_absagen": (int, 3),
    "zahlungsfrist_minuten": (int, 15),
    "saison_zahlungsziel_tage": (int, 14),
    "guthaben_auf_saisonrechnung": (bool, True),
    "rechnung_zahlungsziel_tage": (int, 14),
    "event_ust_satz": (Decimal, Decimal("19.00")),
    "mitgliedschaft_ablauf": (TagMonat, TagMonat("30.04.")),
    "mitglieder_abgleich": (TagMonat, TagMonat("31.08.")),
    "mitglied_erinnerung_tage": (int, 14),
    "heiz_vorlauf_minuten": (int, 30),
    "licht_vorlauf_minuten": (int, 5),
    "licht_nachlauf_minuten": (int, 5),
    "zutritt_vorlauf_minuten": (int, 15),
    "praesenz_alarm_minuten": (int, 10),
    "spiel_temperatur": (Decimal, Decimal("18.0")),
    "grund_temperatur": (Decimal, Decimal("0.0")),
    "antwort_hinweis_sekunden": (int, 120),
    "pin_laenge": (int, 6),
    "rechnungskunden_online_buchen": (bool, False),
}

# Werte, die in den Plan der Halle eingehen (shared.hallenplan.PlanKonfig).
HALLEN_KONFIG: tuple[str, ...] = (
    "heiz_vorlauf_minuten",
    "spiel_temperatur",
    "grund_temperatur",
    "licht_vorlauf_minuten",
    "licht_nachlauf_minuten",
    "zutritt_vorlauf_minuten",
    "praesenz_alarm_minuten",
)

_TYP_NAME = {int: "int", Decimal: "decimal", str: "str", bool: "bool", TagMonat: "tagmonat"}


@dataclass(frozen=True)
class Beschreibung:
    """Wie ein Konfigurationswert in der Verwaltung erscheint. Ohne diese Angaben stünden
    dort die technischen Schlüssel untereinander, die niemand ohne Spezifikation deutet."""

    gruppe: str
    name: str
    einheit: str = ""
    hilfe: str = ""


BESCHREIBUNGEN: dict[str, Beschreibung] = {
    "abo_freie_absagen": Beschreibung(
        "Buchung und Storno",
        "Kostenfreie Absagen im Abo",
        "je Abo",
        "So viele Termine eines Saisonabos kann der Kunde innerhalb der Stornofrist kostenfrei "
        "absagen; der bezahlte Anteil wird Guthaben, sonst sinkt die Forderung. 0 heißt: Die "
        "Saison ist fest bezahlt.",
    ),
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
    "abo_nur_mitglieder": Beschreibung(
        "Buchung und Storno",
        "Saisonabo nur für Mitglieder",
        "",
        "Ja: Ein Abo lässt sich nur anlegen, wenn die Mitgliedschaft des Kunden bis zum letzten "
        "Termin reicht; alle Termine laufen dann zum Satz der Mitglieder. Nein: Auch "
        "Nicht-Mitglieder bekommen Abos zu ihren Konditionen.",
    ),
    "zahlungsfrist_minuten": Beschreibung(
        "Zahlung und Rechnung",
        "Zahlungsfrist",
        "Minuten",
        "So lange bleibt eine Reservierung nach der Buchung für die Online-Zahlung bestehen.",
    ),
    "rechnung_zahlungsziel_tage": Beschreibung(
        "Zahlung und Rechnung",
        "Zahlungsziel von Einzelrechnungen",
        "Tage",
        "Für Buchungen und Events, die Sie selbst anlegen.",
    ),
    "saison_zahlungsziel_tage": Beschreibung(
        "Zahlung und Rechnung",
        "Zahlungsziel der Saisonrechnung",
        "Tage",
        "Die Saisonrechnung geht bei der Anlage eines Abos sofort an den Kunden; so lange hat er "
        "Zeit zu überweisen.",
    ),
    "guthaben_auf_saisonrechnung": Beschreibung(
        "Zahlung und Rechnung",
        "Guthaben mit der Saisonrechnung verrechnen",
        "",
        "Ja: Vorhandenes Guthaben des Kunden wird beim Erstellen der Saisonrechnung als Zahlung "
        "verrechnet; die Rechnung weist es aus. Nein: Sie verrechnen von Hand.",
    ),
    "event_ust_satz": Beschreibung(
        "Zahlung und Rechnung",
        "Steuersatz für Betreiberbuchungen",
        "Prozent",
        "Vorbelegung, wenn Sie eine Buchung oder ein Event selbst anlegen – auch wenn ein "
        "Mitglied bucht. Im Buchungsformular können Sie ihn im Einzelfall ändern.",
    ),
    "mitgliedschaft_ablauf": Beschreibung(
        "Mitgliedschaft",
        "Ablauf der Mitgliedschaft",
        "Tag und Monat",
        "Bis zu diesem Tag gilt eine Freischaltung als Mitglied (Ende der Wintermitgliedschaft). "
        "Beim Freischalten ist der nächste dieser Tage vorbelegt.",
    ),
    "mitglieder_abgleich": Beschreibung(
        "Mitgliedschaft",
        "Jährlicher Abgleich",
        "Tag und Monat",
        "An diesem Tag bekommen Sie eine E-Mail mit der Prüfliste (Kunden → Mitglieder-Abgleich). "
        "Er muss vor dem ersten Buchungsfenster der Saison liegen, sonst buchen Mitglieder die "
        "ersten Termine zu Preisen für Nicht-Mitglieder.",
    ),
    "mitglied_erinnerung_tage": Beschreibung(
        "Mitgliedschaft",
        "Erinnerung vor Ablauf",
        "Tage",
        "So viele Tage vor Ablauf der Mitgliedschaft bekommt der Kunde eine Erinnerung. "
        "0 schaltet die Erinnerung ab.",
    ),
    "heiz_vorlauf_minuten": Beschreibung(
        "Halle",
        "Heizvorlauf",
        "Minuten",
        "So lange vor der ersten Buchung eines Blocks heizt die Halle auf Spieltemperatur.",
    ),
    "spiel_temperatur": Beschreibung("Halle", "Spieltemperatur", "Grad"),
    "grund_temperatur": Beschreibung(
        "Halle",
        "Grundtemperatur",
        "Grad",
        "Temperatur außerhalb der Buchungen. 0 °C muss am Heizgerät dem Frostschutz entsprechen.",
    ),
    "licht_vorlauf_minuten": Beschreibung("Halle", "Licht an vor Beginn", "Minuten"),
    "licht_nachlauf_minuten": Beschreibung("Halle", "Licht aus nach Ende", "Minuten"),
    "zutritt_vorlauf_minuten": Beschreibung(
        "Halle",
        "Zahlencode gültig ab",
        "Minuten vor Beginn",
        "Bis zum Ende der Buchung bleibt der Code gültig.",
    ),
    "praesenz_alarm_minuten": Beschreibung(
        "Halle",
        "Alarm bei Anwesenheit ohne Buchung",
        "Minuten",
        "Meldet ein Sensor so lange Anwesenheit auf einem Feld ohne laufende Buchung, "
        "bekommen Sie eine E-Mail.",
    ),
    "antwort_hinweis_sekunden": Beschreibung(
        "Portal und Zugang",
        "Hinweis auf verzögerte Antwort",
        "Sekunden",
        "So lange wartet das Portal auf die Antwort des Hauptsystems, bevor es den Kunden "
        "um Geduld bittet.",
    ),
    "pin_laenge": Beschreibung("Portal und Zugang", "Länge des Zahlencodes", "Stellen"),
    "rechnungskunden_online_buchen": Beschreibung(
        "Portal und Zugang",
        "Rechnungskunden buchen online",
        "",
        "Nein: Rechnungskunden sehen im Portal ihre Termine, Zahlencodes und Rechnungen und "
        "sagen Abo-Termine ab, buchen aber nicht selbst. Ihre Buchungen legen Sie an.",
    ),
}

# Reihenfolge der Gruppen auf der Konfigurationsseite.
GRUPPEN: list[str] = [
    "Buchung und Storno",
    "Zahlung und Rechnung",
    "Mitgliedschaft",
    "Halle",
    "Portal und Zugang",
]


def gruppiert(
    werte: dict[str, Any],
) -> list[tuple[str, list[tuple[str, Any, Beschreibung, bool]]]]:
    """Ordnet die Werte den Gruppen zu, in der Reihenfolge von GRUPPEN. Das vierte Element sagt,
    ob der Wert ja/nein ist – die Seite zeigt dafür eine Auswahl statt eines Textfelds."""
    return [
        (
            gruppe,
            [
                (
                    schluessel,
                    werte[schluessel],
                    BESCHREIBUNGEN[schluessel],
                    DEFAULTS[schluessel][0] is bool,
                )
                for schluessel in DEFAULTS
                if schluessel in werte and BESCHREIBUNGEN[schluessel].gruppe == gruppe
            ],
        )
        for gruppe in GRUPPEN
    ]


def _parse(typ: type, roh: str) -> Any:
    if typ is bool:
        wert = roh.strip().lower()
        if wert in ("1", "true", "ja"):
            return True
        if wert in ("0", "false", "nein"):
            return False
        raise ValueError("Bitte ja oder nein wählen")
    if typ is Decimal:
        # Die Oberfläche zeigt Dezimalzahlen deutsch mit Komma und bekommt sie so zurück.
        try:
            zahl = Decimal(roh.strip().replace(",", "."))
        except InvalidOperation as e:
            raise ValueError("Bitte eine Zahl angeben") from e
        if not zahl.is_finite():
            raise ValueError("Bitte eine Zahl angeben")
        return zahl
    if typ is int:
        try:
            return int(roh.strip())
        except ValueError as e:
            raise ValueError("Bitte eine ganze Zahl angeben") from e
    return typ(roh)


def hole(db: Session, schluessel: str) -> Any:
    typ, default = DEFAULTS[schluessel]
    zeile = db.get(Konfiguration, schluessel)
    return _parse(typ, zeile.wert) if zeile else default


def setze(db: Session, schluessel: str, wert: Any, admin_user_id: uuid.UUID | None = None) -> None:
    typ, default = DEFAULTS[schluessel]
    zeile = db.get(Konfiguration, schluessel)
    vorher = zeile.wert if zeile else str(default)
    geparst = _parse(typ, str(wert))
    if schluessel == "abo_freie_absagen" and geparst < 0:
        raise ValueError("Kostenfreie Absagen dürfen nicht negativ sein")
    if schluessel == "event_ust_satz" and not Decimal("0") <= geparst < Decimal("100"):
        raise ValueError("Steuersatz ungültig")
    neu = str(geparst)
    aktuell = zeile.wert if zeile else str(_parse(typ, str(default)))
    if neu == aktuell:
        # Unverändert: keine Zeile, kein Audit-Eintrag. Ein leeres Formularfeld und der
        # Vorgabewert bleiben dadurch gleichwertig.
        return
    if zeile:
        zeile.wert = neu
    else:
        db.add(Konfiguration(schluessel=schluessel, wert=neu, typ=_TYP_NAME[typ]))
        db.flush()  # damit `hole` den Wert noch vor dem Commit sieht
    audit.protokolliere(
        db,
        quelle="admin",
        objekt_typ="konfiguration",
        objekt_id=None,
        vorher={"wert": vorher},
        nachher={"wert": neu, "schluessel": schluessel},
        admin_user_id=admin_user_id,
    )
    if schluessel in (
        "fenster_tage",
        "mindestvorlauf_minuten",
        "storno_frist_stunden",
        "antwort_hinweis_sekunden",
    ):
        from beachhub_core.services import lesestand

        lesestand.markiere_geaendert(db, "belegung")
    if schluessel in HALLEN_KONFIG:
        from beachhub_core.services import lesestand

        lesestand.markiere_geaendert(db, "hallenplan")
    if schluessel == "rechnungskunden_online_buchen":
        from beachhub_core.services import lesestand

        # Der Wert steht als online_buchen im Konto-Dokument jedes Rechnungskunden.
        lesestand.markiere_rechnungskunden(db)
    if schluessel == "abo_freie_absagen":
        from beachhub_core.services import lesestand

        lesestand.markiere_rechnungskunden(db)
        lesestand.markiere_abo_kunden(db)
