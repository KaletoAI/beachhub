"""Gemeinsame Sicherheitsfunktionen des Portals: Hashing von Tokens und Codes für die
Datenbank (Anmeldung § 10, Rechnungs-Einmal-Link § 15). Einmal öffentlich hier, damit
beide Stellen nicht denselben Code wörtlich duplizieren (Ruling)."""

import hashlib


def hash_token(wert: str) -> str:
    return hashlib.sha256(wert.encode()).hexdigest()
