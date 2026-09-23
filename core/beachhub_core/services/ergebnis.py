"""Ergebnis einer Anfrageverarbeitung: die Antwort ans Portal und was nach dem Commit geschieht.

Mails, Rechnungs-PDFs und Betreiber-Alarme dürfen erst laufen, wenn die Transaktion steht –
sonst ginge eine Bestätigung hinaus, die ein Rollback wieder zurücknimmt.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from beachhub_shared import kanal
from sqlalchemy.orm import Session

Nachlauf = Callable[[Session], None]


@dataclass
class Ergebnis:
    antwort: kanal.Antwort
    nach_commit: list[Nachlauf] = field(default_factory=list)


def ok(**felder: Any) -> Ergebnis:
    return Ergebnis(kanal.Antwort(status="ok", **felder))


def abgelehnt(grund: str) -> Ergebnis:
    return Ergebnis(kanal.Antwort(status="abgelehnt", grund=grund))


def ignoriert(grund: str) -> Ergebnis:
    return Ergebnis(kanal.Antwort(status="ignoriert", grund=grund))
