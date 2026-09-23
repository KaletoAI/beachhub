"""Zahlungsschnittstelle (A-ZAHL-5).

Der Anbieter ist austauschbar und noch nicht entschieden (Ⓞ-13). Bis dahin gibt es nur den
Fake-Anbieter für Entwicklung und Tests; Stripe oder Mollie kommen als weitere Klasse dazu.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from beachhub_core.config import settings


class ZahlungsFehler(Exception):  # noqa: N818 – Fachfehler heißen im Projekt ...Fehler
    pass


@dataclass(frozen=True)
class Sitzung:
    provider_ref: str
    checkout_url: str


class PaymentProvider(Protocol):
    name: str

    def erzeuge_sitzung(
        self, *, betrag: Decimal, referenz: uuid.UUID, ablauf: datetime, rueckkehr_url: str
    ) -> Sitzung: ...

    def verifiziere(self, rohdaten: str, signatur_header: str | None) -> str | None:
        """Prüft eine Rückmeldung des Anbieters und liefert die Zahlungsreferenz – oder None."""
        ...

    def status(self, provider_ref: str) -> tuple[str, Decimal]:
        """Fragt den Anbieter nach dem Stand: ("bezahlt" | "offen" | "abgebrochen", Betrag)."""
        ...


_instanz: PaymentProvider | None = None


def anbieter() -> PaymentProvider:
    global _instanz
    if _instanz is None:
        if settings.zahlung_provider == "fake":
            from beachhub_core.zahlung.fake import FakeProvider

            _instanz = FakeProvider()
        else:
            raise ZahlungsFehler(f"Unbekannter Zahlungsanbieter: {settings.zahlung_provider}")
    return _instanz


def anbieter_fuer(name: str) -> PaymentProvider | None:
    """Der aktive Anbieter, wenn er so heißt. Rückmeldungen anderer Anbieter werden ignoriert."""
    try:
        aktiv = anbieter()
    except ZahlungsFehler:
        return None
    return aktiv if aktiv.name == name else None
