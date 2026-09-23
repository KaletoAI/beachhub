"""Vertrag der Lesestand-Dokumente zwischen Hauptsystem (Erzeuger) und Portal (Verbraucher)."""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


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


class TarifeInhalt(BaseModel):
    regeln: list[TarifInfo]


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
    # Darf der Kunde die Buchung im Portal stornieren (nur Portal-Buchungen, keine
    # Dauerbuchungstermine)? Mit Vorgabe, damit ältere gespeicherte Dokumente gültig bleiben.
    stornierbar: bool = True


class KontoRechnung(BaseModel):
    nummer: str
    datum: date
    brutto: Decimal
    status: str


class KontoInhalt(BaseModel):
    kunde_id: str
    kundengruppe: str
    zahlungsart: str
    guthaben: Decimal
    buchungen: list[KontoBuchung]
    rechnungen: list[KontoRechnung]
