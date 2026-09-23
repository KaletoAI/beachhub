"""Mailversand des Portals: nur Login-Codes (A-MAIL-1; Gruppen-Mails folgen mit Stufe 4)."""

import logging
import smtplib
from email.message import EmailMessage
from typing import Any

from beachhub_portal.config import settings

logger = logging.getLogger(__name__)
TEST_AUSGANG: list[dict[str, Any]] | None = None


def sende(an: str, betreff: str, text: str) -> None:
    if TEST_AUSGANG is not None:
        TEST_AUSGANG.append({"an": an, "betreff": betreff, "text": text})
        return
    if not settings.smtp_host:
        logger.warning("kein SMTP konfiguriert – Mail an %s (%s) nicht gesendet", an, betreff)
        return
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = settings.email_from, an, betreff
    m.set_content(text)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
            smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(m)
    except (smtplib.SMTPException, OSError):
        logger.exception("Mailversand an %s fehlgeschlagen", an)
