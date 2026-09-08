import asyncio
import logging
import threading
from email.message import EmailMessage
from typing import Any

import aiosmtplib

from beachhub_core.config import settings

logger = logging.getLogger(__name__)
TEST_AUSGANG: list[dict[str, Any]] | None = None


def _nachricht(an: str, betreff: str, text: str, anhaenge: list[tuple[str, bytes]]) -> EmailMessage:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = settings.email_from, an, betreff
    m.set_content(text)
    for name, daten in anhaenge:
        m.add_attachment(daten, maintype="application", subtype="pdf", filename=name)
    return m


async def _senden(m: EmailMessage) -> None:
    await aiosmtplib.send(
        m,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user or None,
        password=settings.smtp_password or None,
        start_tls=True,
    )


def sende(
    an: str, betreff: str, text: str, anhaenge: list[tuple[str, bytes]] | None = None
) -> None:
    anhaenge = anhaenge or []
    if TEST_AUSGANG is not None:
        TEST_AUSGANG.append(
            {"an": an, "betreff": betreff, "text": text, "anhaenge": [n for n, _ in anhaenge]}
        )
        return
    if not settings.smtp_host:
        logger.warning("kein SMTP konfiguriert – Mail an %s (%s) nicht gesendet", an, betreff)
        return
    m = _nachricht(an, betreff, text, anhaenge)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_senden(m))
        return
    threading.Thread(target=lambda: asyncio.run(_senden(m)), daemon=True).start()
