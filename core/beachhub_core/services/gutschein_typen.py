"""Interne Ergebnisverträge der Gutschein-Fachdienste; keine Kanal-Nutzlasten."""

import uuid
from dataclasses import dataclass
from decimal import Decimal

from beachhub_core.models import Gutschein, GutscheinEinloesung, Rechnung, Zahlung


@dataclass
class GutscheinSperrkontext:
    kunde_ids: frozenset[uuid.UUID]
    gutscheine: dict[uuid.UUID, Gutschein]
    zahlungen: dict[uuid.UUID, Zahlung]


@dataclass
class GutscheinDeckung:
    betrag: Decimal
    einloesungen: list[GutscheinEinloesung]


@dataclass
class GutscheinBuchungsbelege:
    geldrechnung: Rechnung | None
    gutscheinbelege: list[Rechnung]


@dataclass
class GutscheinRueckabwicklung:
    freigegebene_gutscheine: list[Gutschein]
    guthaben_betrag: Decimal
    korrekturbelege: list[Rechnung]


@dataclass
class GutscheinRueckgabe:
    korrekturbeleg: Rechnung | None
    manuell_zu_erstatten: Decimal
