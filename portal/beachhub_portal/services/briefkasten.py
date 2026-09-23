"""Briefkasten für Rückmeldungen des Zahlungsanbieters (A-ZAHL-2).

Das Portal prüft nichts und kennt keine Geheimnisse des Anbieters. Es speichert die Rohdaten und
reicht sie als Anfrage `zahlung_eingegangen` weiter; den Eingang stellt das Hauptsystem fest.
"""

import logging

from pydantic import ValidationError
from sqlalchemy.orm import Session

from beachhub_portal import uhr
from beachhub_portal.models import Anfrage, WebhookEingang
from beachhub_portal.services import anfragen

MAX_BYTES = 64 * 1024

logger = logging.getLogger(__name__)


def nimm_an(
    db: Session, *, provider: str, rohdaten: str, signatur_header: str | None
) -> Anfrage | None:
    """Legt die Anfrage `zahlung_eingegangen` an und speichert den Briefkasteneintrag.

    Verletzt die Nutzlast das Kanal-Schema (z. B. ein überlanger Signatur-Header, Ruling
    Fix-Runde 1), wird **keine** Anfrage erzeugt – nur der `webhook_eingang` bleibt erhalten,
    ohne `anfrage_id`, und eine Warnung wird geloggt (ohne die Rohdaten, die Zahlungsdaten des
    Anbieters enthalten können). Der Aufrufer antwortet trotzdem mit 200, wie bei jeder anderen
    Rückmeldung: Das Portal prüft/kürzt nichts, es verwirft nur, was es nicht weiterreichen kann.
    """
    try:
        a: Anfrage | None = anfragen.stelle(
            db,
            typ="zahlung_eingegangen",
            konto_id=None,
            nutzlast={
                "provider": provider,
                "rohdaten": rohdaten,
                "signatur_header": signatur_header,
            },
        )
    except ValidationError:
        logger.warning(
            "Zahlungsrückmeldung von Provider %r verworfen: Nutzlast verletzt das Kanal-Schema "
            "(z. B. Signatur-Header zu lang)",
            provider,
        )
        a = None
    db.add(
        WebhookEingang(
            provider=provider,
            rohdaten=rohdaten,
            signatur_header=signatur_header,
            empfangen_am=uhr.jetzt(),
            anfrage_id=a.id if a is not None else None,
        )
    )
    db.commit()
    return a
