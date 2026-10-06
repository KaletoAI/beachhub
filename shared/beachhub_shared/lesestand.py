"""Vertrag der Lesestand-Dokumente zwischen Hauptsystem (Erzeuger) und Portal (Verbraucher)."""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field


class Dokument(BaseModel):
    dokument: str
    version: int
    erzeugt_am: datetime
    inhalt: dict[str, Any]
    signatur: str


class RasterInfo(BaseModel):
    wochentag: int | None
    modus: str
    slot_minuten: int | None
    fenster: list[list[str]]


class FeldInfo(BaseModel):
    id: str
    name: str
    reihenfolge: int
    raster: list[RasterInfo]


class BetriebszeitInfo(BaseModel):
    wochentag: int
    oeffnet: time
    schliesst: time
    gueltig_von: date | None
    gueltig_bis: date | None


class AusnahmeInfo(BaseModel):
    datum: date
    geschlossen: bool
    oeffnet: time | None
    schliesst: time | None


class Zeitraum(BaseModel):
    beginn: datetime
    ende: datetime


class BelegungInhalt(BaseModel):
    felder: list[FeldInfo]
    betriebszeiten: list[BetriebszeitInfo]
    ausnahmetage: list[AusnahmeInfo]
    fenster_tage: int
    mindestvorlauf_minuten: int
    belegt: dict[str, list[Zeitraum]]
    # Mit Vorgabe, damit vor Stufe 2 gespeicherte Dokumente gültig bleiben.
    storno_frist_stunden: int = 24
    antwort_hinweis_sekunden: int = 120


class TarifInfo(BaseModel):
    name: str
    preis: Decimal
    feld_id: str | None
    wochentag: int | None
    uhrzeit_von: time | None
    uhrzeit_bis: time | None
    kundengruppe: str | None
    gueltig_von: date | None
    gueltig_bis: date | None


class GutscheinAngebot(BaseModel):
    modell: Literal["einheit", "wert"]
    steuer: Literal["beim_kauf", "bei_einloesung"]
    minuten: int | None
    preis_mitglied: Decimal
    preis_nichtmitglied: Decimal
    betraege: list[Decimal]
    nur_mitglieder: bool
    gueltig_tage: int


class TarifeInhalt(BaseModel):
    regeln: list[TarifInfo]
    gutschein_angebot: GutscheinAngebot | None = None
    # Namen der beiden festen Gruppen (A-KUND-2), damit das Portal die Gruppe eines Termins aus
    # mitglied_bis ableiten kann. Mit Vorgabe, damit ältere gespeicherte Dokumente gültig bleiben.
    gruppe_mitglied: str | None = None
    gruppe_nichtmitglied: str | None = None


class StornoInfo(BaseModel):
    kostenfrei: bool


class KontoBuchung(BaseModel):
    id: str
    feld_id: str
    feld_name: str
    beginn: datetime
    ende: datetime
    status: str
    preis: Decimal
    pin: str | None
    storno: StornoInfo | None
    # Nur bei einer offenen Reservierung: Link zur Bezahlseite und Ende der Zahlungsfrist
    # (Hauptspec § 8.1). Mit Vorgabe, damit ältere gespeicherte Dokumente gültig bleiben.
    checkout_url: str | None = None
    reserviert_bis: datetime | None = None
    # Portal-Buchungen und Dauerbuchungstermine können Kunden im Portal absagen.
    # Mit Vorgabe, damit ältere gespeicherte Dokumente gültig bleiben.
    stornierbar: bool = True
    # Termin einer Dauerbuchung und verbleibende freie Absagen dieses Abos (A-DAUER-3).
    abo: bool = False
    freie_absagen_rest: int | None = None
    # Modelle der tatsächlichen Deckung, ohne fremde Codes oder Kaufbelegrechte.
    gutschein_modelle: list[Literal["einheit", "wert"]] = Field(default_factory=list)


class KontoRechnung(BaseModel):
    nummer: str
    datum: date
    brutto: Decimal
    status: str


class KontoGutschein(BaseModel):
    code: str
    art: Literal["kauf", "frei"]
    modell: Literal["einheit", "wert"]
    status: Literal["aktiv", "teilweise_eingeloest"]
    gueltig_bis: date
    minuten: int | None
    restwert: Decimal | None
    verbleibende_einloesungen: int | None
    gruppenhinweis: str


class KontoGutscheinKauf(BaseModel):
    # Vor Abschluss gibt es ausdrücklich keinen Code im öffentlichen Vertrag.
    id: str
    status: Literal["offen"]
    preis: Decimal
    reserviert_bis: datetime
    checkout_url: str | None = None


class KontoInhalt(BaseModel):
    kunde_id: str
    # Gruppe am heutigen Tag. Für künftige Termine gilt die Gruppe an deren Tag (A-KUND-6):
    # Mitglied, solange mitglied_bis den Tag einschließt.
    kundengruppe: str
    guthaben: Decimal
    buchungen: list[KontoBuchung]
    rechnungen: list[KontoRechnung]
    # Seit Stufe 1a; mit Vorgaben, damit ältere gespeicherte Dokumente gültig bleiben. Das frühere
    # Feld zahlungsart entfällt (die Zahlungsart steht an der Buchung, A-ZAHL-1).
    rechnungskunde: bool = False
    online_buchen: bool = True
    mitgliedschaft: Literal["mitglied", "beantragt", "nicht_mitglied"] = "nicht_mitglied"
    mitglied_bis: date | None = None
    antrag_am: datetime | None = None

    # Alt-Dokumente dürfen keinen Verkauf freischalten.
    gutscheine: list[KontoGutschein] = Field(default_factory=list)
    gutschein_kaeufe: list[KontoGutscheinKauf] = Field(default_factory=list)
    gutschein_kauf: bool = False
