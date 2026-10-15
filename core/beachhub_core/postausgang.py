"""Postausgang für Entwicklung und Test (nicht bei `APP_ENV=production`).

Jede Mail des Hauptsystems wird als Originalnachricht (`mail.eml`) mit Versandstatus unter
`DATA_DIR/postausgang/` abgelegt und ist in der Verwaltung unter System → Postausgang lesbar.
Ein Schalter entscheidet, ob Mails zusätzlich per SMTP verschickt werden. Vorgabe ist aus: Mails
landen dann nur im Postausgang, damit ein Testsystem niemanden versehentlich anschreibt.

Die Ablage liegt bewusst im Dateisystem statt in der Datenbank: Mails entstehen nach dem Commit
ohne eigene Sitzung, und der Postausgang gehört nicht zum fachlichen Datenbestand.
"""

import json
import logging
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from email import message_from_bytes, policy
from email.message import EmailMessage
from pathlib import Path

from beachhub_core.config import settings

logger = logging.getLogger(__name__)

# Älteste Mails werden darüber hinaus gelöscht, damit der Ordner nicht unbegrenzt wächst.
BEHALTEN = 500
ZURUECKGEHALTEN = "zurueckgehalten"
KEIN_SMTP = "kein_smtp"
WIRD_GESENDET = "wird_gesendet"
GESENDET = "gesendet"
FEHLER = "fehler"
STATUSTEXT = {
    ZURUECKGEHALTEN: "nur im Postausgang",
    KEIN_SMTP: "nicht gesendet (kein SMTP)",
    WIRD_GESENDET: "wird gesendet",
    GESENDET: "gesendet",
    FEHLER: "Versand fehlgeschlagen",
}
_ID = re.compile(r"^\d{8}T\d{12}-[0-9a-f]{8}$")


@dataclass
class Eintrag:
    id: str
    zeitpunkt: datetime
    an: str
    betreff: str
    status: str
    anhaenge: list[str]

    @property
    def statustext(self) -> str:
        return STATUSTEXT.get(self.status, self.status)


def aktiv() -> bool:
    return settings.app_env != "production"


def ordner() -> Path:
    return settings.data_dir / "postausgang"


def _schalter() -> Path:
    return ordner() / "versand.json"


def versand_an() -> bool:
    """Ob Mails zusätzlich per SMTP verschickt werden. Fehlt die Einstellung: nein."""
    try:
        return bool(json.loads(_schalter().read_text())["versand"])
    except (OSError, ValueError, KeyError, TypeError):
        return False


def setze_versand(an: bool) -> None:
    ordner().mkdir(parents=True, exist_ok=True)
    _schalter().write_text(json.dumps({"versand": an}))


def _pfad(mail_id: str) -> Path | None:
    """Nur Kennungen im eigenen Format, damit keine fremden Pfade gelesen werden."""
    if not _ID.match(mail_id):
        return None
    pfad = ordner() / mail_id
    return pfad if (pfad / "mail.eml").is_file() else None


def ablegen(m: EmailMessage, status: str) -> str | None:
    """Legt die Mail ab. Ein Fehler hier darf den Versand und den Vorgang nie stören."""
    jetzt = datetime.now(UTC)
    mail_id = f"{jetzt:%Y%m%dT%H%M%S%f}-{uuid.uuid4().hex[:8]}"
    try:
        pfad = ordner() / mail_id
        pfad.mkdir(parents=True)
        (pfad / "mail.eml").write_bytes(m.as_bytes())
        meta = {
            "zeitpunkt": jetzt.isoformat(),
            "an": str(m["To"]),
            "betreff": str(m["Subject"]),
            "status": status,
            "anhaenge": [a.get_filename() or "Anhang" for a in m.iter_attachments()],
        }
        (pfad / "meta.json").write_text(json.dumps(meta, ensure_ascii=False))
        _aufraeumen()
    except Exception:  # noqa: BLE001 -- Postausgang ist Beiwerk, der Vorgang geht vor
        logger.exception("Mail an %s konnte nicht im Postausgang abgelegt werden", m["To"])
        return None
    return mail_id


def setze_status(mail_id: str | None, status: str) -> None:
    if mail_id is None or (pfad := _pfad(mail_id)) is None:
        return
    try:
        meta = json.loads((pfad / "meta.json").read_text())
        meta["status"] = status
        (pfad / "meta.json").write_text(json.dumps(meta, ensure_ascii=False))
    except (OSError, ValueError):
        logger.exception("Status der Mail %s nicht gespeichert", mail_id)


def _eintrag(pfad: Path) -> Eintrag | None:
    try:
        meta = json.loads((pfad / "meta.json").read_text())
        return Eintrag(
            id=pfad.name,
            zeitpunkt=datetime.fromisoformat(meta["zeitpunkt"]),
            an=meta["an"],
            betreff=meta["betreff"],
            status=meta["status"],
            anhaenge=list(meta.get("anhaenge", [])),
        )
    except (OSError, ValueError, KeyError):
        return None


def _ordner_neueste_zuerst() -> list[Path]:
    if not ordner().is_dir():
        return []
    return sorted((p for p in ordner().iterdir() if _ID.match(p.name)), reverse=True)


def liste() -> list[Eintrag]:
    return [e for p in _ordner_neueste_zuerst() if (e := _eintrag(p)) is not None]


def lade(mail_id: str) -> tuple[Eintrag, EmailMessage] | None:
    pfad = _pfad(mail_id)
    eintrag = _eintrag(pfad) if pfad else None
    if pfad is None or eintrag is None:
        return None
    m = message_from_bytes((pfad / "mail.eml").read_bytes(), policy=policy.default)
    assert isinstance(m, EmailMessage)
    return eintrag, m


def roh(mail_id: str) -> bytes | None:
    pfad = _pfad(mail_id)
    return (pfad / "mail.eml").read_bytes() if pfad else None


def text(m: EmailMessage) -> str:
    teil = m.get_body(preferencelist=("plain",))
    return teil.get_content() if isinstance(teil, EmailMessage) else ""


def anhang(m: EmailMessage, nummer: int) -> tuple[str, str, bytes] | None:
    """(Dateiname, Medientyp, Inhalt) des Anhangs Nr. `nummer` (ab 0)."""
    teile = list(m.iter_attachments())
    if not 0 <= nummer < len(teile):
        return None
    teil = teile[nummer]
    daten = teil.get_content()
    if isinstance(daten, str):
        daten = daten.encode()
    return teil.get_filename() or f"anhang-{nummer + 1}", teil.get_content_type(), daten


def leeren() -> int:
    pfade = _ordner_neueste_zuerst()
    for p in pfade:
        shutil.rmtree(p, ignore_errors=True)
    return len(pfade)


def _aufraeumen() -> None:
    for p in _ordner_neueste_zuerst()[BEHALTEN:]:
        shutil.rmtree(p, ignore_errors=True)
