"""Kanal zum Portal (Spec Portal-Kern § 2-3).

Das Hauptsystem holt Anfragen per Long-Polling ab und liefert Antworten und Lesestände aus.
Alle Verbindungen gehen von hier aus; das Portal kann das Hauptsystem nicht erreichen (N-1).
Zwei Threads: der Abholer (Long-Poll, Verarbeitung, Antworten) und der Verteiler (geänderte
Lesestände alle 2 s, Abgleich der Versionen beim Start, nach Fehlern und stündlich).
"""

import logging
import ssl
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx
from beachhub_shared import kanal as vertrag
from beachhub_shared.lesestand import Dokument
from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_core.config import settings
from beachhub_core.database import SessionLocal
from beachhub_core.models import AppSetting, Kunde, LesestandVersion
from beachhub_core.services import anfragen, benachrichtigung, lesestand
from beachhub_core.services.ergebnis import Nachlauf

logger = logging.getLogger(__name__)

AUSFALL_ALARM_SEKUNDEN = 30 * 60
ABGLEICH_SEKUNDEN = 60 * 60
VERTEIL_TAKT_SEKUNDEN = 2.0
MAX_BACKOFF_SEKUNDEN = 60.0
# Drosselung des Alarms bei einer vom Portal abgelehnten Sendung (422): höchstens einmal je
# (Dokument, Version) und insgesamt höchstens einmal pro Stunde, sonst würde eine dauerhaft
# falsche Portal-Konfiguration eine Mailflut auslösen (jede Runde versucht dieselbe, abgelehnte
# Version erneut, bis eine neue Version veröffentlicht wird).
LESESTAND_ALARM_DROSSEL_SEKUNDEN = 60 * 60

# Schlüsselpräfix in app_setting für die zuletzt erfolgreich ans Portal gesendete Version je
# Dokument (K1): Der Kanal merkt sich das dauerhaft, unabhängig davon, ob er selbst, der
# 5-Minuten-Job (jobs._job_lesestand) oder der Admin-Knopf "Lesestand erzeugen" veröffentlicht
# hat. app_setting.key ist String(50); der längste Dokumentname ist "konto:<uuid>" (42 Zeichen),
# das Präfix bleibt also mit deutlichem Abstand darunter.
_GESENDET_PRAEFIX = "kv:"


def baue_client() -> httpx.Client:
    """HTTP-Client mit Kanal-Token und, falls konfiguriert, Client-Zertifikat (mTLS)."""
    optionen: dict[str, Any] = {}
    if settings.portal_ca or settings.portal_client_cert:
        ctx = ssl.create_default_context(cafile=settings.portal_ca or None)
        if settings.portal_client_cert:
            ctx.load_cert_chain(settings.portal_client_cert, settings.portal_client_key or None)
        optionen["verify"] = ctx
    return httpx.Client(
        base_url=settings.portal_url,
        headers={"Authorization": f"Bearer {settings.kanal_token}"},
        timeout=httpx.Timeout(10.0, read=40.0),
        **optionen,
    )


class Kanal:
    def __init__(
        self,
        client: httpx.Client,
        *,
        sitzung: Callable[[], Session] = SessionLocal,
        warten: int = 25,
        uhr: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.sitzung = sitzung
        self.warten = warten
        self.uhr = uhr
        self._backoff = 1.0
        self._letzter_erfolg = uhr()
        self._ausfall_gemeldet = False
        self._abgleich_noetig = True
        self._letzter_abgleich = float("-inf")
        self._422_gemeldet: set[tuple[str, int]] = set()
        self._letzter_422_alarm = float("-inf")
        # Schützt den gesamten Zyklus "fällige Dokumente lesen -> senden -> Sendestand merken"
        # in verteilen()/abgleichen(): Ohne die Sperre könnten der Abholer- und der
        # Verteiler-Thread für dasselbe neue Dokument gleichzeitig die app_setting-Merkzeile
        # anlegen wollen (IntegrityError auf dem Primärschlüssel), wodurch abholen() die
        # Antworten des laufenden Long-Polls nie sendet.
        self._verteil_sperre = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    # ---------- eine Runde ----------

    def abholen(self) -> int:
        r = self.client.get(
            "/core/anfragen",
            params={"warten": self.warten},
            timeout=httpx.Timeout(10.0, read=self.warten + 15.0),
        )
        r.raise_for_status()
        liste = vertrag.AnfrageListe.model_validate(r.json())
        self._erfolg()
        if not liste.anfragen:
            return 0
        eintraege: list[vertrag.AntwortEintrag] = []
        nachlauf: list[Nachlauf] = []
        try:
            # Die Verarbeitungsschleife steht mit im try: Wirft bearbeite() bei Anfrage k+1 (der
            # schmale, nicht abgefangene Fehlerpfad in anfragen.bearbeite), liefen die Nachläufe
            # der bereits committeten Anfragen 1..k sonst nie – bei erneuter Zustellung liefert
            # _bereits_verarbeitet für sie [] zurück, der Verlust (Mails, PDFs, Alarme) wäre
            # endgültig.
            with self.sitzung() as db:
                for a in liste.anfragen:
                    antwort, nach = anfragen.bearbeite(db, a)
                    eintraege.append(
                        vertrag.AntwortEintrag(anfrage_id=a.anfrage_id, antwort=antwort)
                    )
                    nachlauf.extend(nach)
            # Erst die Lesestände, dann die Antworten: Sobald das Portal eine Antwort sieht,
            # soll es die Buchung auch schon anzeigen können.
            self.verteilen()
            body = vertrag.AntwortListe(antworten=eintraege).model_dump(
                mode="json", exclude_none=True
            )
            self.client.post("/core/antworten", json=body).raise_for_status()
        finally:
            # Mails und PDFs hängen nicht am Portal: Sie laufen auch, wenn das Senden scheitert.
            # Die Antworten holt sich das Portal bei der erneuten Zustellung (idempotent).
            self._nachlauf(nachlauf)
        return len(eintraege)

    def verteilen(self) -> int:
        try:
            with self.sitzung() as db:
                lesestand.verarbeite_geaenderte(db)
        except FileNotFoundError:
            logger.error("Signaturschlüssel fehlt – Lesestand kann nicht verteilt werden")
            return 0
        # Der ganze Zyklus "fällige lesen -> senden -> merken" liegt hinter einer Sperre: sonst
        # könnten der Abholer- (via abholen -> verteilen) und der Verteiler-Thread gleichzeitig
        # dasselbe neue Dokument für fällig halten und beide die Merkzeile anlegen wollen.
        with self._verteil_sperre:
            with self.sitzung() as db:
                namen = self._faellige_dokumente(db)
            dokumente = [dok for name in namen if (dok := lesestand.lade(name)) is not None]
            try:
                angenommen = self._sende(dokumente)
            except httpx.HTTPError:
                # Die Dokumente sind veröffentlicht, aber nicht angekommen: nachholen per
                # Abgleich.
                self._abgleich_noetig = True
                raise
            if dokumente and angenommen:
                with self.sitzung() as db:
                    self._merke_gesendete_versionen(db, dokumente)
        return len(dokumente)

    def abgleichen(self) -> int:
        r = self.client.get("/core/lesestand/versionen")
        r.raise_for_status()
        im_portal: dict[str, int] = r.json()
        ordner = settings.data_dir / "lesestand"
        kandidaten: list[Dokument] = []
        for pfad in sorted(ordner.glob("*.json")) if ordner.exists() else []:
            dok = Dokument.model_validate_json(pfad.read_text(encoding="utf-8"))
            if vertrag.fuer_portal(dok.dokument) and im_portal.get(dok.dokument, 0) < dok.version:
                kandidaten.append(dok)
        with self._verteil_sperre:
            with self.sitzung() as db:
                kunden_mit_portal_konto = self._kunden_mit_portal_konto(db)
            fehlend = [
                d
                for d in kandidaten
                if self._kunde_hat_portal_konto(d.dokument, kunden_mit_portal_konto)
            ]
            angenommen = self._sende(fehlend)
            if fehlend and angenommen:
                with self.sitzung() as db:
                    self._merke_gesendete_versionen(db, fehlend)
        self._abgleich_noetig = False
        self._letzter_abgleich = self.uhr()
        return len(fehlend)

    def runde(self) -> float:
        """Eine Runde der Abholschleife. Liefert die Wartezeit bis zur nächsten Runde."""
        try:
            self.abholen()
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("Portal nicht erreichbar oder Antwort unbrauchbar: %s", e)
            self._fehlschlag()
            wartezeit = self._backoff
            self._backoff = min(self._backoff * 2, MAX_BACKOFF_SEKUNDEN)
            return wartezeit
        except Exception:
            logger.exception("Unerwarteter Fehler im Kanal-Abholer")
            return 5.0
        self._backoff = 1.0
        return 0.0

    def verteiler_runde(self) -> None:
        try:
            if self._abgleich_noetig or self.uhr() - self._letzter_abgleich >= ABGLEICH_SEKUNDEN:
                self.abgleichen()
            self.verteilen()
        except (httpx.HTTPError, ValueError) as e:
            self._abgleich_noetig = True
            logger.warning("Lesestand-Verteilung fehlgeschlagen: %s", e)
        except Exception:
            self._abgleich_noetig = True
            logger.exception("Unerwarteter Fehler im Kanal-Verteiler")

    # ---------- Threads ----------

    def starte(self) -> None:
        self._stop.clear()
        for ziel, name in ((self._abholer, "kanal-abholer"), (self._verteiler, "kanal-verteiler")):
            t = threading.Thread(target=ziel, name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def stoppe(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=5)
            if t.is_alive():
                logger.warning("Kanal-Thread %s reagiert nicht auf stoppe()", t.name)
        self._threads.clear()
        self.client.close()

    def _abholer(self) -> None:
        while not self._stop.is_set():
            wartezeit = self.runde()
            if wartezeit == 0.0 and self.warten == 0:
                wartezeit = 0.2  # ohne Long-Poll (Tests) nicht im Kreis drehen
            if wartezeit:
                self._stop.wait(wartezeit)

    def _verteiler(self) -> None:
        while not self._stop.wait(VERTEIL_TAKT_SEKUNDEN):
            self.verteiler_runde()

    # ---------- Lesestand-Auswahl (K1/K2) ----------

    def _faellige_dokumente(self, db: Session) -> list[str]:
        """Alle für das Portal erlaubten Dokumente, deren aktuelle Version über der zuletzt
        gesendeten liegt – unabhängig davon, wer sie veröffentlicht hat (K1)."""
        gesendet = self._gesendete_versionen(db)
        kunden_mit_portal_konto = self._kunden_mit_portal_konto(db)
        zeilen = db.scalars(select(LesestandVersion)).all()
        return [
            z.dokument
            for z in zeilen
            if vertrag.fuer_portal(z.dokument)
            and z.version > gesendet.get(z.dokument, 0)
            and self._kunde_hat_portal_konto(z.dokument, kunden_mit_portal_konto)
        ]

    def _kunden_mit_portal_konto(self, db: Session) -> set[uuid.UUID]:
        """Ein Join in einer einzigen Abfrage statt eines db.get je konto:-Dokument (sonst ein
        Roundtrip pro Kunde bei jeder Runde)."""
        return set(db.scalars(select(Kunde.id).where(Kunde.portal_konto_id.is_not(None))))

    def _kunde_hat_portal_konto(
        self, dokument: str, kunden_mit_portal_konto: set[uuid.UUID]
    ) -> bool:
        """K2: konto:<id> geht nur ans Portal, wenn der Kunde per portal_konto_id verknüpft ist
        (Datensparsamkeit N-2) – sonst blieben Konto-Dokumente von Vereins-/Abo-/Admin-Kunden
        oder gerade gelöschten Konten dauerhaft im Portal liegen."""
        praefix, _, rest = dokument.partition(":")
        if praefix != "konto":
            return True
        try:
            kunde_id = uuid.UUID(rest)
        except ValueError:
            return False
        return kunde_id in kunden_mit_portal_konto

    def _gesendete_versionen(self, db: Session) -> dict[str, int]:
        zeilen = db.scalars(
            select(AppSetting).where(AppSetting.key.like(f"{_GESENDET_PRAEFIX}%"))
        ).all()
        return {
            z.key[len(_GESENDET_PRAEFIX) :]: int(z.value) for z in zeilen if z.value is not None
        }

    def _merke_gesendete_versionen(self, db: Session, dokumente: list[Dokument]) -> None:
        for dok in dokumente:
            schluessel = f"{_GESENDET_PRAEFIX}{dok.dokument}"
            zeile = db.get(AppSetting, schluessel)
            if zeile is None:
                db.add(AppSetting(key=schluessel, value=str(dok.version)))
            else:
                zeile.value = str(dok.version)
        db.commit()

    # ---------- Hilfen ----------

    def _sende(self, dokumente: list[Dokument]) -> bool:
        """Sendet die Dokumente ans Portal. Liefert, ob das Portal sie angenommen hat (False bei
        abgelehnter Signatur/Version); wirft httpx.HTTPError bei sonstigen Netz-/Serverfehlern.
        Wird ausschließlich innerhalb von self._verteil_sperre aufgerufen (verteilen/abgleichen),
        braucht also keine eigene Sperre um den POST."""
        if not dokumente:
            return True
        body = vertrag.DokumentListe(dokumente=dokumente).model_dump(mode="json")
        r = self.client.post("/core/lesestand", json=body)
        if r.status_code == 422:
            logger.error("Portal hat Lesestand abgelehnt: %s", r.text[:500])
            self._alarm_bei_ablehnung(dokumente, r.text)
            return False
        r.raise_for_status()
        return True

    def _alarm_bei_ablehnung(self, dokumente: list[Dokument], antwort_text: str) -> None:
        """Drosselt den Alarm bei einer vom Portal abgelehnten Sendung: höchstens einmal je
        (Dokument, Version) und insgesamt höchstens einmal pro Stunde. Eine (Dokument, Version)
        gilt erst als gemeldet, wenn tatsächlich eine Mail rausging – sonst bliebe eine Ablehnung,
        die nur an der Stunden-Drosselung scheiterte, für immer unbemerkt."""
        schluessel = {(d.dokument, d.version) for d in dokumente}
        neu = schluessel - self._422_gemeldet
        if not neu:
            return
        if self.uhr() - self._letzter_422_alarm < LESESTAND_ALARM_DROSSEL_SEKUNDEN:
            return
        self._422_gemeldet |= neu
        self._letzter_422_alarm = self.uhr()
        benachrichtigung.betreiber_alarm(
            "Lesestand vom Portal abgelehnt",
            "Das Portal hat mindestens ein Lesestand-Dokument wegen einer ungültigen Signatur "
            "verworfen. Prüfen Sie, ob im Portal der aktuelle öffentliche Schlüssel des "
            "Hauptsystems hinterlegt ist (beachhub-core keygen gibt ihn nicht erneut aus; "
            "er steht auf der System-Seite).\n\n" + antwort_text[:2000],
        )

    def _nachlauf(self, schritte: list[Nachlauf]) -> None:
        if not schritte:
            return
        with self.sitzung() as db:
            for schritt in schritte:
                try:
                    schritt(db)
                except Exception:
                    db.rollback()
                    logger.exception("Nachlauf nach Portal-Anfrage fehlgeschlagen")

    def _erfolg(self) -> None:
        if self._ausfall_gemeldet:
            benachrichtigung.betreiber_alarm(
                "Portal wieder erreichbar", "Das Hauptsystem erreicht das Portal wieder."
            )
        self._ausfall_gemeldet = False
        self._letzter_erfolg = self.uhr()

    def _fehlschlag(self) -> None:
        if self._ausfall_gemeldet:
            return
        if self.uhr() - self._letzter_erfolg >= AUSFALL_ALARM_SEKUNDEN:
            self._ausfall_gemeldet = True
            benachrichtigung.betreiber_alarm(
                "Portal nicht erreichbar",
                f"Das Hauptsystem erreicht das Portal seit mehr als "
                f"{AUSFALL_ALARM_SEKUNDEN // 60} Minuten nicht. Anfragen der Kunden bleiben im "
                "Portal liegen, bis die Verbindung zurück ist.",
            )
