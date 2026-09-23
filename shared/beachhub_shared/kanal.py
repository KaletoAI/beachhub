"""Vertrag des Kanals zwischen Portal (Briefkasten) und Hauptsystem (Verarbeiter).

Das Hauptsystem holt Anfragen per Long-Polling ab (`GET /core/anfragen`), schickt Antworten
(`POST /core/antworten`) und signierte Lesestände (`POST /core/lesestand`). Beide Seiten
validieren gegen diese Schemata; eine Änderung hier ist eine Änderung des Vertrags.
"""

import uuid
from decimal import Decimal
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, Field

from beachhub_shared.lesestand import Dokument

ANFRAGETYPEN: tuple[str, ...] = (
    "konto_angelegt",
    "konto_geaendert",
    "konto_loeschen",
    "buchung_anfragen",
    "buchung_stornieren",
    "zahlung_eingegangen",
    "rechnung_anfordern",
)

# Allowlist: Ein Dokument geht nur ans Portal, wenn es hier ausdrücklich steht. Neue Dokumente
# des Hauptsystems (etwa der Hallenplan mit PIN-Hashes) bleiben so automatisch draußen.
PORTAL_DOKUMENTE: tuple[str, ...] = ("belegung", "tarife")


def fuer_portal(name: str) -> bool:
    if name in PORTAL_DOKUMENTE:
        return True
    praefix, _, rest = name.partition(":")
    if praefix != "konto" or not rest:
        return False
    try:
        uuid.UUID(rest)
    except ValueError:
        return False
    return True


# ---------- Nutzlasten je Anfragetyp ----------


class KontoAngelegt(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    anzeigename: str = Field(min_length=1, max_length=100)


class KontoGeaendert(BaseModel):
    anzeigename: str = Field(min_length=1, max_length=100)
    bisher: str = Field(max_length=100)


class KontoLoeschen(BaseModel):
    pass


class BuchungAnfragen(BaseModel):
    feld_id: uuid.UUID
    beginn: AwareDatetime
    ende: AwareDatetime


class BuchungStornieren(BaseModel):
    buchung_id: uuid.UUID


class ZahlungEingegangen(BaseModel):
    provider: str = Field(min_length=1, max_length=40)
    rohdaten: str = Field(max_length=65536)
    signatur_header: str | None = Field(default=None, max_length=2000)


class RechnungAnfordern(BaseModel):
    rechnung_nr: str = Field(min_length=1, max_length=20)


NUTZLAST: dict[str, type[BaseModel]] = {
    "konto_angelegt": KontoAngelegt,
    "konto_geaendert": KontoGeaendert,
    "konto_loeschen": KontoLoeschen,
    "buchung_anfragen": BuchungAnfragen,
    "buchung_stornieren": BuchungStornieren,
    "zahlung_eingegangen": ZahlungEingegangen,
    "rechnung_anfordern": RechnungAnfordern,
}


# ---------- Anfragen, Antworten, Lesestand ----------


class Anfrage(BaseModel):
    anfrage_id: uuid.UUID
    typ: str
    konto_id: uuid.UUID | None = None
    kunde_id: uuid.UUID | None = None
    nutzlast: dict[str, Any] = Field(default_factory=dict)
    # Zeitzonenbewusst: Zeiten im Kanal-Schema sind durchgehend AwareDatetime (Global Constraint).
    erstellt_am: AwareDatetime


class AnfrageListe(BaseModel):
    anfragen: list[Anfrage]


class Antwort(BaseModel):
    status: Literal["ok", "reserviert", "bestaetigt", "abgelehnt", "ignoriert", "fehler"]
    grund: str | None = None
    kunde_id: uuid.UUID | None = None
    buchung_id: uuid.UUID | None = None
    preis: Decimal | None = None
    guthaben_verrechnet: Decimal | None = None
    zu_zahlen: Decimal | None = None
    checkout_url: str | None = None
    # Zeitzonenbewusst: Zeiten im Kanal-Schema sind durchgehend AwareDatetime (Global Constraint).
    reserviert_bis: AwareDatetime | None = None
    kostenfrei: bool | None = None
    pdf_base64: str | None = None
    dateiname: str | None = None


class AntwortEintrag(BaseModel):
    anfrage_id: uuid.UUID
    antwort: Antwort


class AntwortListe(BaseModel):
    antworten: list[AntwortEintrag]


class DokumentListe(BaseModel):
    dokumente: list[Dokument]


class Verworfen(BaseModel):
    dokument: str
    grund: Literal["signatur", "version_alt", "unbekannt"]


class LesestandErgebnis(BaseModel):
    uebernommen: list[str]
    verworfen: list[Verworfen]
