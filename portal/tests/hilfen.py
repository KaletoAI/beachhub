"""Testhilfen: Schlüsselpaar des „Hauptsystems“, signierte Dokumente, Beispielinhalte."""

import uuid
from datetime import UTC, datetime
from typing import Any

from beachhub_shared import signatur
from beachhub_shared.lesestand import Dokument
from sqlalchemy.orm import Session

PRIVAT, OEFFENTLICH = signatur.erzeuge_schluesselpaar()
FELD_ID = "11111111-1111-1111-1111-111111111111"
KUNDE_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
# Donnerstag, 25.11.2027, 10:00 Uhr in Berlin
JETZT = datetime(2027, 11, 25, 9, 0, tzinfo=UTC)


def signiert(name: str, version: int, inhalt: dict[str, Any]) -> dict[str, Any]:
    entwurf = Dokument(
        dokument=name, version=version, erzeugt_am=datetime.now(UTC), inhalt=inhalt, signatur=""
    )
    sig = signatur.signiere(entwurf.model_dump(mode="json", exclude={"signatur"}), PRIVAT)
    return entwurf.model_copy(update={"signatur": sig}).model_dump(mode="json")


def belegung(**abweichend: Any) -> dict[str, Any]:
    inhalt: dict[str, Any] = {
        "felder": [
            {
                "id": FELD_ID,
                "name": "Feld 1",
                "reihenfolge": 1,
                "raster": [
                    {"wochentag": None, "modus": "dauer", "slot_minuten": 60, "fenster": []}
                ],
            }
        ],
        "betriebszeiten": [
            {
                "wochentag": wt,
                "oeffnet": "09:00:00",
                "schliesst": "23:00:00",
                "gueltig_von": None,
                "gueltig_bis": None,
            }
            for wt in range(7)
        ],
        "ausnahmetage": [],
        "fenster_tage": 14,
        "mindestvorlauf_minuten": 60,
        "storno_frist_stunden": 24,
        "antwort_hinweis_sekunden": 120,
        "belegt": {FELD_ID: []},
    }
    inhalt.update(abweichend)
    return inhalt


def tarif(name: str, preis: str, **kriterien: Any) -> dict[str, Any]:
    regel: dict[str, Any] = {
        "name": name,
        "preis": preis,
        "feld_id": None,
        "wochentag": None,
        "uhrzeit_von": None,
        "uhrzeit_bis": None,
        "kundengruppe": None,
        "gueltig_von": None,
        "gueltig_bis": None,
    }
    regel.update(kriterien)
    return regel


def tarife(*regeln: dict[str, Any]) -> dict[str, Any]:
    return {"regeln": list(regeln) or [tarif("Std", "30.00")]}


def buchung(
    beginn: datetime,
    ende: datetime,
    status: str = "bestaetigt",
    pin: str | None = "123456",
    storno: dict[str, Any] | None = None,
    id: str | None = None,  # noqa: A002 – Feldname des Lesestands
    checkout_url: str | None = None,
    reserviert_bis: datetime | None = None,
) -> dict[str, Any]:
    return {
        "id": id or str(uuid.uuid4()),
        "feld_id": FELD_ID,
        "feld_name": "Feld 1",
        "beginn": beginn.isoformat(),
        "ende": ende.isoformat(),
        "status": status,
        "preis": "30.00",
        "pin": pin if status == "bestaetigt" else None,
        "storno": storno,
        "checkout_url": checkout_url,
        "reserviert_bis": reserviert_bis.isoformat() if reserviert_bis else None,
    }


def konto(
    buchungen: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
    rechnungen: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
    gruppe: str = "Privat",
    guthaben: str = "0.00",
) -> dict[str, Any]:
    return {
        "kunde_id": str(KUNDE_ID),
        "kundengruppe": gruppe,
        "zahlungsart": "online",
        "guthaben": guthaben,
        "buchungen": list(buchungen),
        "rechnungen": list(rechnungen),
    }


def speichere(db: Session, name: str, inhalt: dict[str, Any], version: int = 1) -> None:
    """Legt einen Lesestand direkt in der Datenbank ab (ohne Kanal, ohne Signaturprüfung)."""
    from beachhub_portal.models import Lesestand

    db.merge(
        Lesestand(
            dokument=name,
            version=version,
            erzeugt_am=datetime.now(UTC),
            signatur="00",
            inhalt_json=inhalt,
            empfangen_am=datetime.now(UTC),
        )
    )
    db.commit()
