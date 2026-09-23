"""Gemeinsame Sicherheitsfunktionen des Portals: Hashing von Tokens und Codes für die
Datenbank (Anmeldung § 10, Rechnungs-Einmal-Link § 15). Einmal öffentlich hier, damit
beide Stellen nicht denselben Code wörtlich duplizieren (Ruling)."""

import hashlib
import hmac


def hash_token(wert: str) -> str:
    """Für hochentropische Werte (`secrets.token_urlsafe`): reines SHA-256 genügt, ein Leck der
    Datenbank verrät ohne den Rohwert nichts, offline raten ist wegen der Entropie aussichtslos."""
    return hashlib.sha256(wert.encode()).hexdigest()


def hash_code(code: str, secret_key: str) -> str:
    """Für den 6-stelligen Login-Code (nur 10**6 Möglichkeiten): HMAC-SHA256 mit dem geheimen
    Schlüssel der Anwendung statt reinem SHA-256 (Ruling Fix-Runde 1) – sonst ließe sich der Code
    bei einem Leck der Datenbank offline in Sekunden durchprobieren."""
    return hmac.new(secret_key.encode(), code.encode(), hashlib.sha256).hexdigest()
