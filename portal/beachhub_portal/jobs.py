"""Aufräumen im Portal, alle 5 Minuten: abgelaufene Einmal-Links samt Dateien, verwaiste
PDF-Dateien, Login-Codes und Sessions, alte beantwortete Anfragen und Briefkasteneinträge,
Lesestände von Konten, die es im Portal nicht mehr gibt.

Läuft nur in einem Prozess/Worker (Task 17: `--workers 1`). `pg_try_advisory_lock` schützt
zusätzlich gegen einen gleichzeitigen zweiten Lauf (z. B. ein manueller Aufruf neben dem
Scheduler) – ein Lauf, der die Sperre nicht bekommt, überspringt sich komplett, statt mit dem
laufenden Job um dieselben Zeilen zu konkurrieren (Ruling Fix-Runde 1).

Jeder Schritt läuft in seiner eigenen kleinen Transaktion und wird einzeln abgefangen (Ruling
Fix-Runde 1): ein dauerhafter Fehler in einem Schritt (kaputte Datei-Rechte, ein dediziertes
Problem in genau dieser Tabelle) darf die übrigen, unabhängigen Aufräumarbeiten nicht
blockieren. Abgelaufene Rechnungslinks werden per `DELETE … RETURNING` entfernt; die
zugehörigen Dateien werden erst gelöscht, nachdem diese Löschung committet ist – schlägt eine
einzelne Datei fehl (z. B. Berechtigungsfehler), bleiben die übrigen DB-Löschungen trotzdem
wirksam."""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from beachhub_portal import uhr
from beachhub_portal.config import settings
from beachhub_portal.database import SessionLocal, engine
from beachhub_portal.models import (
    Anfrage,
    CodeFehlversuch,
    Konto,
    Lesestand,
    LoginToken,
    RechnungLink,
    Sitzung,
    WebhookEingang,
)

logger = logging.getLogger(__name__)
AUFBEWAHRUNG = timedelta(days=30)
# Ruling (Task 10, umgesetzt in Task 15): Fehlversuche beim Code-Login werden dauerhaft in der
# DB gezählt (Sperre über 24 h, unabhängig von einzelnen Login-Tokens) und deshalb erst hier,
# nicht beim Löschen eines einzelnen Kontos, wieder entfernt.
CODE_FEHLVERSUCH_AUFBEWAHRUNG = timedelta(hours=24)
# Feste, für diesen Zweck reservierte Kennung des Postgres-Advisory-Locks (beliebiger bigint).
_LOCK_ID = 891_273_400_15
_scheduler: BackgroundScheduler | None = None


def _versuchen(db: Session, schritt: str, fn: Callable[[], int]) -> int:
    """Führt einen Aufräumschritt in seiner eigenen Transaktion aus: committet bei Erfolg, rollt
    bei einem Fehler nur diesen Schritt zurück und protokolliert ihn – die übrigen, unabhängigen
    Schritte laufen trotzdem weiter (Ruling Fix-Runde 1)."""
    try:
        ergebnis = fn()
        db.commit()
        return ergebnis
    except Exception:
        db.rollback()
        logger.exception("Aufräumen: Schritt %r fehlgeschlagen", schritt)
        return 0


def _rechnung_links(db: Session, jetzt: datetime) -> int:
    """Löscht abgelaufene Link-Zeilen per RETURNING und erst danach – nach dem Commit – die
    zugehörigen Dateien; eine einzelne kaputte Datei bricht die übrigen Löschungen nicht ab."""
    try:
        pfade = list(
            db.scalars(
                delete(RechnungLink)
                .where(RechnungLink.laeuft_ab <= jetzt)
                .returning(RechnungLink.pdf_pfad)
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Aufräumen: Schritt 'rechnung_links' fehlgeschlagen")
        return 0
    for pfad in pfade:
        try:
            Path(pfad).unlink(missing_ok=True)
        except OSError:
            logger.exception("Aufräumen: Rechnungs-PDF konnte nicht gelöscht werden: %s", pfad)
    return len(pfade)


def _waisen(db: Session, jetzt: datetime) -> int:
    """Dateien ohne (mehr gültigen) Link, etwa nach einem Absturz zwischen Schreiben und Commit.
    Läuft nach `_rechnung_links`, sodass in `RechnungLink` nur noch gültige Zeilen stehen."""
    # Derselbe aufgelöste Ordner wie in `rechnung_link.lege_an` (Ruling Fix-Runde 2) – sonst
    # stimmen die Pfad-Strings aus `ordner.glob(...)` nicht mit den in `RechnungLink.pdf_pfad`
    # gespeicherten (bereits `.resolve()`ten) Pfaden überein, und ein noch gültiger Link würde
    # fälschlich als Waise erkannt und gelöscht.
    ordner = (settings.data_dir / "rechnungen_tmp").resolve()
    if not ordner.exists():
        return 0
    bekannt = set(db.scalars(select(RechnungLink.pdf_pfad)))
    grenze = (jetzt - timedelta(hours=1)).timestamp()
    waisen = 0
    for pfad in ordner.glob("*.pdf"):
        try:
            if str(pfad) not in bekannt and pfad.stat().st_mtime < grenze:
                pfad.unlink(missing_ok=True)
                waisen += 1
        except OSError:
            logger.exception("Aufräumen: verwaiste Datei konnte nicht geprüft werden: %s", pfad)
    return waisen


def _code_fehlversuch(db: Session, jetzt: datetime) -> int:
    return db.execute(
        delete(CodeFehlversuch).where(
            CodeFehlversuch.versucht_am <= jetzt - CODE_FEHLVERSUCH_AUFBEWAHRUNG
        )
    ).rowcount


def _login_token(db: Session, jetzt: datetime) -> int:
    return db.execute(delete(LoginToken).where(LoginToken.laeuft_ab <= jetzt)).rowcount


def _sitzungen(db: Session, jetzt: datetime) -> int:
    return db.execute(delete(Sitzung).where(Sitzung.laeuft_ab <= jetzt)).rowcount


def _anfragen(db: Session, jetzt: datetime) -> int:
    # Nur beantwortete Anfragen verfallen – eine alte, aber weiterhin offene/abgeholte Anfrage
    # (das Hauptsystem hat nie geantwortet) darf nicht verloren gehen.
    return db.execute(
        delete(Anfrage).where(
            Anfrage.status == Anfrage.BEANTWORTET, Anfrage.beantwortet_am < jetzt - AUFBEWAHRUNG
        )
    ).rowcount


def _webhooks(db: Session, jetzt: datetime) -> int:
    return db.execute(
        delete(WebhookEingang).where(WebhookEingang.empfangen_am < jetzt - AUFBEWAHRUNG)
    ).rowcount


def _lesestand_verwaist(db: Session, jetzt: datetime) -> int:
    # konto:-Dokumente können vor dem Konto ankommen; erst nach einem Tag gelten sie als verwaist.
    kunden = {
        f"konto:{k}" for k in db.scalars(select(Konto.kunde_id).where(Konto.kunde_id.is_not(None)))
    }
    verwaist = [
        z
        for z in db.scalars(
            select(Lesestand).where(
                Lesestand.dokument.like("konto:%"),
                Lesestand.empfangen_am < jetzt - timedelta(days=1),
            )
        )
        if z.dokument not in kunden
    ]
    for z in verwaist:
        db.delete(z)
    return len(verwaist)


def aufraeumen(db: Session, jetzt: datetime) -> dict[str, int]:
    # Eigene, für den ganzen Aufruf offen gehaltene Verbindung nur für die Sperre: Der `db`-
    # Session-Verbindung wird nach jedem einzelnen Schritt-Commit ihre physische Verbindung vom
    # Pool wieder freigegeben (und beim nächsten Schritt ggf. eine andere zugeteilt) – ein
    # `pg_try_advisory_lock`/`pg_advisory_unlock`-Paar auf `db` selbst liefe deshalb Gefahr, auf
    # zwei verschiedenen physischen Verbindungen zu laufen und die Sperre nie wieder freizugeben.
    with engine.connect() as sperr_verbindung:
        gesperrt = bool(
            sperr_verbindung.execute(
                text("SELECT pg_try_advisory_lock(:id)"), {"id": _LOCK_ID}
            ).scalar()
        )
        sperr_verbindung.commit()
        if not gesperrt:
            logger.info("Aufräumen übersprungen: ein anderer Lauf hält die Sperre")
            return {}
        try:
            return {
                "rechnung_links": _rechnung_links(db, jetzt),
                "waisen": _versuchen(db, "waisen", lambda: _waisen(db, jetzt)),
                "code_fehlversuch": _versuchen(
                    db, "code_fehlversuch", lambda: _code_fehlversuch(db, jetzt)
                ),
                "login_token": _versuchen(db, "login_token", lambda: _login_token(db, jetzt)),
                "sitzungen": _versuchen(db, "sitzungen", lambda: _sitzungen(db, jetzt)),
                "anfragen": _versuchen(db, "anfragen", lambda: _anfragen(db, jetzt)),
                "webhooks": _versuchen(db, "webhooks", lambda: _webhooks(db, jetzt)),
                "lesestand": _versuchen(db, "lesestand", lambda: _lesestand_verwaist(db, jetzt)),
            }
        finally:
            try:
                sperr_verbindung.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": _LOCK_ID})
                sperr_verbindung.commit()
            except Exception:
                # Ruling Fix-Runde 2: Kann die Sperre nicht sauber freigegeben werden (z. B. die
                # Verbindung ist zwischenzeitlich weg), darf diese Verbindung nicht gesund an den
                # Pool zurückgehen und dort die Sperre für immer mitnehmen – `invalidate()` wirft
                # die physische Verbindung weg; Postgres löst den Advisory-Lock spätestens beim
                # Schließen der zugehörigen Backend-Sitzung.
                logger.exception("Aufräumen: Advisory-Lock konnte nicht freigegeben werden")
                sperr_verbindung.invalidate()


def _job_aufraeumen() -> None:
    with SessionLocal() as db:
        try:
            aufraeumen(db, uhr.jetzt())
        except Exception:
            logger.exception("Aufräumen fehlgeschlagen")


def starte_scheduler() -> BackgroundScheduler:
    global _scheduler
    s = BackgroundScheduler(timezone="Europe/Berlin")
    s.add_job(_job_aufraeumen, IntervalTrigger(minutes=5), id="aufraeumen", replace_existing=True)
    s.start()
    _scheduler = s
    return s


def stoppe_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
