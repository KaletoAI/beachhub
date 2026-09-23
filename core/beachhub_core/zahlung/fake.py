"""Fake-Zahlungsanbieter für Entwicklung und Tests.

Die Bezahlseite liegt im Portal (`/test-zahlung/<ref>`); sie schickt eine Rückmeldung in den
Briefkasten wie ein echter Anbieter. Im Produktivbetrieb verweigert der Konstruktor den Dienst.
"""

import json
import secrets
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from beachhub_core.config import settings
from beachhub_core.zahlung import Sitzung, ZahlungsFehler


class FakeProvider:
    name = "fake"

    def __init__(self) -> None:
        if settings.app_env not in ("dev", "test"):
            raise ZahlungsFehler("Der Fake-Zahlungsanbieter ist nur in der Entwicklung erlaubt")
        self._ergebnisse: dict[str, tuple[str, Decimal]] = {}

    def erzeuge_sitzung(
        self, *, betrag: Decimal, referenz: uuid.UUID, ablauf: datetime, rueckkehr_url: str
    ) -> Sitzung:
        ref = "fake_" + secrets.token_urlsafe(16)
        query = urlencode({"betrag": str(betrag), "zurueck": rueckkehr_url})
        return Sitzung(provider_ref=ref, checkout_url=f"/test-zahlung/{ref}?{query}")

    def verifiziere(self, rohdaten: str, signatur_header: str | None) -> str | None:
        try:
            daten = json.loads(rohdaten)
        except ValueError:
            return None
        if not isinstance(daten, dict):
            return None
        ref, ergebnis = daten.get("ref"), daten.get("ergebnis")
        if not isinstance(ref, str) or not ref.startswith("fake_"):
            return None
        if ergebnis not in ("bezahlt", "abgebrochen"):
            return None
        try:
            betrag = Decimal(str(daten.get("betrag"))).quantize(Decimal("0.01"))
        except InvalidOperation:
            return None
        if not betrag.is_finite():
            return None
        self._ergebnisse[ref] = (ergebnis, betrag)
        return ref

    def status(self, provider_ref: str) -> tuple[str, Decimal]:
        return self._ergebnisse.get(provider_ref, ("offen", Decimal("0.00")))
