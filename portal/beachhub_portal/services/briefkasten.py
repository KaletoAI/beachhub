"""Briefkasten für Rückmeldungen des Zahlungsanbieters (A-ZAHL-2).

Das Portal prüft nichts und kennt keine Geheimnisse des Anbieters. Es speichert die Rohdaten und
reicht sie als Anfrage `zahlung_eingegangen` weiter; den Eingang stellt das Hauptsystem fest.
"""

from sqlalchemy.orm import Session

from beachhub_portal import uhr
from beachhub_portal.models import Anfrage, WebhookEingang
from beachhub_portal.services import anfragen

MAX_BYTES = 64 * 1024


def nimm_an(db: Session, *, provider: str, rohdaten: str, signatur_header: str | None) -> Anfrage:
    a = anfragen.stelle(
        db,
        typ="zahlung_eingegangen",
        konto_id=None,
        nutzlast={"provider": provider, "rohdaten": rohdaten, "signatur_header": signatur_header},
    )
    db.add(
        WebhookEingang(
            provider=provider,
            rohdaten=rohdaten,
            signatur_header=signatur_header,
            empfangen_am=uhr.jetzt(),
            anfrage_id=a.id,
        )
    )
    db.commit()
    return a
