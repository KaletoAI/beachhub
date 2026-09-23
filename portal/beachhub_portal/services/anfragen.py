"""Anfragetabelle des Portals: der Briefkasten für das Hauptsystem (Spec Portal-Kern § 2).

Das Portal entscheidet nichts. Es legt Anfragen ab, liefert sie aus und merkt sich die Antwort.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlparse

from beachhub_shared import kanal
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from beachhub_portal import uhr
from beachhub_portal.models import Anfrage, KanalKontakt, Konto
from beachhub_portal.services import lesestand, wecker

ERNEUT_NACH = timedelta(seconds=60)
HOECHSTENS = 50
# Ruling Task 12: Ein Doppelklick (beim Buchen wie beim Stornieren, Task 14) darf keine zweite
# Anfrage erzeugen – der Kunde sähe beim zweiten POST sonst ggf. eine überholte Ablehnung, obwohl
# die erste Anfrage längst bearbeitet wurde. Als Duplikat gilt: dieselbe noch offene/abgeholte
# Anfrage, oder eine erst vor Kurzem beantwortete (innerhalb dieses Fensters) – danach ist ein
# neuer Versuch ein neuer Wunsch, kein Doppelklick mehr.
DOPPELKLICK_FENSTER = timedelta(minutes=2)


def bestehende(
    db: Session, konto_id: uuid.UUID, typ: str, nutzlast: dict[str, Any], jetzt: datetime
) -> Anfrage | None:
    """Findet eine wiederverwendbare Anfrage desselben Kontos/Typs mit identischer (validierter)
    Nutzlast für den Doppelklick-Schutz (Ruling Task 12). Gemeinsam genutzt von
    `routes/buchen.py` (Buchen) und `routes/buchungen.py` (Storno, Task 14, Review-Minor aus
    Task 12: die Duplikatlogik gehört als Service-Funktion hierher statt in eine einzelne Route)."""
    kandidaten = db.scalars(
        select(Anfrage)
        .where(
            Anfrage.konto_id == konto_id,
            Anfrage.typ == typ,
            Anfrage.nutzlast_json == nutzlast,
        )
        .order_by(Anfrage.erstellt_am.desc())
    ).all()
    for a in kandidaten:
        if a.status != Anfrage.BEANTWORTET:
            return a
        if a.beantwortet_am is not None and jetzt - a.beantwortet_am <= DOPPELKLICK_FENSTER:
            return a
    return None


def stelle(
    db: Session,
    *,
    typ: str,
    konto_id: uuid.UUID | None,
    nutzlast: dict[str, Any],
    commit: bool = True,
) -> Anfrage:
    """Legt eine Anfrage an, committet und weckt wartende Long-Polls.

    `commit=False` (Ruling Fix-Runde 1, Item 10): Anfrage nur vormerken, wenn der Aufrufer sie
    zusammen mit weiteren Änderungen (z. B. Konto löschen) in einer Transaktion abschließen will –
    committet und weckt dann selbst, nachdem auch die übrigen Änderungen angewendet sind.
    """
    schema = kanal.NUTZLAST.get(typ)
    if schema is None:
        raise ValueError(f"Unbekannter Anfragetyp: {typ}")
    # Validiert UND das validierte, JSON-taugliche Ergebnis speichern (nicht die rohe Nutzlast):
    # rohe Werte können UUID/Decimal/datetime enthalten, die die JSONB-Spalte nicht serialisieren
    # kann; model_dump(mode="json") wandelt sie in Strings.
    validiert = schema.model_validate(nutzlast).model_dump(mode="json")
    a = Anfrage(
        id=uuid.uuid4(),
        typ=typ,
        konto_id=konto_id,
        nutzlast_json=validiert,
        erstellt_am=uhr.jetzt(),
        status=Anfrage.OFFEN,
    )
    db.add(a)
    if commit:
        db.commit()
        wecker.wecke()
    return a


def markiere_kontakt(db: Session, jetzt: datetime) -> None:
    """Merkt sich, wann das Hauptsystem zuletzt einen Long-Poll gestartet hat. Wird genau einmal
    je HTTP-Aufruf von `GET /core/anfragen` gerufen (nicht in jedem Sekunden-Durchlauf der
    Warteschleife), sonst würde ein 25-Sekunden-Poll die Zeile bis zu 25-mal schreiben."""
    db.merge(KanalKontakt(id=1, letzter_abruf=jetzt))
    db.commit()


def abholen(db: Session, jetzt: datetime) -> list[kanal.Anfrage]:
    zeilen = list(
        db.scalars(
            select(Anfrage)
            .where(
                or_(
                    Anfrage.status == Anfrage.OFFEN,
                    and_(
                        Anfrage.status == Anfrage.ABGEHOLT,
                        Anfrage.abgeholt_am < jetzt - ERNEUT_NACH,
                    ),
                )
            )
            # Zweiter Schlüssel `id`, damit Anfragen mit identischem erstellt_am (eingefrorene
            # Uhr, Tests) trotzdem deterministisch sortiert werden.
            .order_by(Anfrage.erstellt_am, Anfrage.id)
            .limit(HOECHSTENS)
            .with_for_update(skip_locked=True)
        ).all()
    )
    konto_ids = {z.konto_id for z in zeilen if z.konto_id is not None}
    kunden: dict[uuid.UUID, uuid.UUID | None] = {}
    if konto_ids:
        for konto_id, kunde_id in db.execute(
            select(Konto.id, Konto.kunde_id).where(Konto.id.in_(konto_ids))
        ).tuples():
            kunden[konto_id] = kunde_id
    liste: list[kanal.Anfrage] = []
    for z in zeilen:
        z.status = Anfrage.ABGEHOLT
        z.abgeholt_am = jetzt
        liste.append(
            kanal.Anfrage(
                anfrage_id=z.id,
                typ=z.typ,
                konto_id=z.konto_id,
                kunde_id=kunden.get(z.konto_id) if z.konto_id else None,
                nutzlast=z.nutzlast_json,
                erstellt_am=z.erstellt_am,
            )
        )
    db.commit()
    return liste


def beantworte(db: Session, anfrage_id: uuid.UUID, antwort: kanal.Antwort, jetzt: datetime) -> bool:
    a = db.get(Anfrage, anfrage_id, with_for_update=True)
    if a is None or a.status == Anfrage.BEANTWORTET:
        return False
    daten = antwort.model_dump(mode="json", exclude_none=True)
    if a.typ == "konto_angelegt" and antwort.status == "ok" and antwort.kunde_id and a.konto_id:
        konto = db.get(Konto, a.konto_id)
        if konto is not None:
            konto.kunde_id = antwort.kunde_id
    a.status = Anfrage.BEANTWORTET
    a.antwort_json = daten
    a.beantwortet_am = jetzt
    return True


WARTET = "Deine Anfrage wird bearbeitet …"
HINWEIS = (
    "Deine Anfrage ist gespeichert. Das Buchungssystem ist gerade nicht erreichbar; "
    "du bekommst die Bestätigung per E-Mail."
)
FEHLER_TEXT = (
    "Bei der Verarbeitung ist ein Fehler aufgetreten. Bitte versuche es später noch einmal."
)
GRUENDE: dict[str, str] = {
    "belegt": "Der Termin ist inzwischen vergeben. Bitte wähle einen anderen.",
    "ausserhalb_fenster": "Der Termin liegt außerhalb des Buchungsfensters.",
    "ausserhalb_betriebszeit": "Zu dieser Zeit ist die Halle nicht geöffnet.",
    "kein_tarif": "Für diesen Termin gibt es keinen Preis. Bitte wende dich an die Halle.",
    "feld_inaktiv": "Dieses Feld ist derzeit nicht buchbar.",
    "konto_gesperrt": "Dein Konto ist für Buchungen gesperrt. Bitte wende dich an die Halle.",
    "konto_unbekannt": "Dein Konto wird noch eingerichtet. Bitte versuche es gleich noch einmal.",
    "nicht_gefunden": "Das haben wir nicht gefunden.",
    "zu_spaet": "Der Termin hat schon begonnen und kann nicht mehr storniert werden.",
    # Controller-Hinweis Task 12: core/services/anfragen.py meldet ungültige/unvollständige
    # Anfragen (z. B. fehlende konto_id) mit diesem Grund; der Fallbacktext wäre sonst zu
    # unspezifisch für einen tatsächlich vom Hauptsystem gesendeten Ablehnungsgrund.
    "ungueltig": "Die Anfrage konnte nicht verarbeitet werden. Bitte versuche es erneut.",
}
MELDUNGEN: dict[str, str] = {
    "bestaetigt": "Deine Buchung ist bestätigt.",
    "storniert_kostenfrei": "Deine Buchung ist storniert. Die Stornierung ist kostenfrei.",
    "storniert_kostenpflichtig": (
        "Deine Buchung ist storniert. Da die Stornofrist abgelaufen war, bleibt der Betrag fällig."
    ),
}


@dataclass(frozen=True)
class Stand:
    zustand: Literal["wartet", "zahlung", "fertig", "abgelehnt", "fehler"]
    text: str
    ziel: str | None = None
    zahlung_url: str | None = None


# ASCII-Steuerzeichen (inkl. Tab/Zeilenumbruch/CR) und Leerraum – auch am Rand verboten, sonst
# ließe sich z. B. ein führendes Leerzeichen oder ein eingebetteter Zeilenumbruch missbrauchen.
_STEUERZEICHEN_ODER_LEERRAUM = re.compile(r"[\x00-\x20\x7f]")


def gueltige_checkout_url(url: str | None) -> bool:
    """Verteidigung gegen eine offene Weiterleitung (N-1: Das Hauptsystem ist zwar
    vertrauenswürdig, das Portal erzeugt aber selbst kein Ziel aus dieser fremden Angabe, ohne
    es zu prüfen). Fix-Runde 2: Browser behandeln bei http(s)-URLs einen Backslash wie einen
    Schrägstrich – `/\\evil` und `/\\/evil` würden wie `//evil` als protokollrelative, absolute
    URL auf einen fremden Host gelesen. Backslashes sind deshalb überall verboten, nicht nur am
    Anfang.

    Akzeptiert:
    - einen Pfad, der mit genau einem `/` beginnt (zweites Zeichen weder `/` noch `\\`),
    - eine absolute `https://…`-URL mit nicht leerem Host (per `urlparse`; das Schema wird dabei
      klein geschrieben – case-insensitiv wie im Web üblich, `HTTPS://…` ist also gültig).
    Abgelehnt wird jede URL mit Backslash, ASCII-Steuerzeichen oder Leerraum (auch am Rand).

    Öffentlich (Controller-Hinweis Task 14): `routes/buchungen.py` zeigt den Zahlungslink auf
    „Meine Buchungen“ nur, wenn dieselbe Prüfung ihn akzeptiert – keine zweite Prüflogik."""
    if url is None or "\\" in url or _STEUERZEICHEN_ODER_LEERRAUM.search(url):
        return False
    if url.startswith("/"):
        return len(url) > 1 and url[1] not in ("/", "\\")
    teile = urlparse(url)
    return teile.scheme == "https" and bool(teile.netloc)


def _nach_zahlung(
    db: Session, antwort: dict[str, Any], kunde_id: uuid.UUID | None, weiter: bool
) -> Stand:
    inhalt = lesestand.konto(db, kunde_id)
    buchung_id = antwort.get("buchung_id")
    kb = next((b for b in inhalt.buchungen if b.id == buchung_id), None) if inhalt else None
    if kb is not None and kb.status == "bestaetigt":
        return Stand("fertig", MELDUNGEN["bestaetigt"], ziel="/buchungen?meldung=bestaetigt")
    if kb is not None and kb.status in ("verfallen", "storniert"):
        return Stand(
            "abgelehnt", "Die Zahlungsfrist ist abgelaufen; der Termin wurde wieder freigegeben."
        )
    url = antwort.get("checkout_url")
    if not gueltige_checkout_url(url):
        return Stand("fehler", FEHLER_TEXT)
    return Stand(
        "zahlung",
        "Bitte schließe die Zahlung ab. Sobald sie bestätigt ist, geht es hier automatisch weiter.",
        ziel=url if weiter else None,
        zahlung_url=url,
    )


def stand(
    db: Session,
    a: Anfrage,
    kunde_id: uuid.UUID | None,
    jetzt: datetime,
    *,
    weiter: bool,
    hinweis_sekunden: int,
) -> Stand:
    if a.status != Anfrage.BEANTWORTET:
        alter = (jetzt - a.erstellt_am).total_seconds()
        kontakt = db.get(KanalKontakt, 1)
        still = (
            kontakt is not None
            and (jetzt - kontakt.letzter_abruf).total_seconds() > hinweis_sekunden
        )
        return Stand("wartet", HINWEIS if alter > hinweis_sekunden or still else WARTET)
    antwort = a.antwort_json or {}
    status = antwort.get("status")
    if status == "fehler":
        return Stand("fehler", FEHLER_TEXT)
    if status in ("abgelehnt", "ignoriert"):
        return Stand(
            "abgelehnt", GRUENDE.get(str(antwort.get("grund")), "Die Anfrage wurde abgelehnt.")
        )
    if a.typ == "buchung_anfragen":
        if status == "bestaetigt":
            return Stand("fertig", MELDUNGEN["bestaetigt"], ziel="/buchungen?meldung=bestaetigt")
        return _nach_zahlung(db, antwort, kunde_id, weiter)
    if a.typ == "buchung_stornieren":
        art = "storniert_kostenfrei" if antwort.get("kostenfrei") else "storniert_kostenpflichtig"
        return Stand("fertig", MELDUNGEN[art], ziel=f"/buchungen?meldung={art}")
    if a.typ == "rechnung_anfordern":
        token = antwort.get("link_token")
        if token:
            return Stand("fertig", "Deine Rechnung steht bereit.", ziel=f"/rechnung/{token}")
        return Stand("abgelehnt", "Die Rechnung konnte nicht bereitgestellt werden.")
    return Stand("fertig", "Erledigt.", ziel="/konto")
