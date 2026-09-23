"""Verarbeitung der Portal-Anfragen im Hauptsystem (Spec Portal-Kern § 4).

Das Portal ist ein Briefkasten: Jede Nutzlast ist nicht vertrauenswürdig. Den Kunden ermittelt
das Hauptsystem selbst über `kunde.portal_konto_id`, nie über ein Feld der Anfrage.
"""

import base64
import logging
from collections.abc import Callable
from typing import Any

from beachhub_shared import kanal
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from beachhub_core.config import settings
from beachhub_core.models import AnfrageVerarbeitet, Kunde, Kundengruppe, Rechnung
from beachhub_core.services import (
    audit,
    konfiguration,
    kunden,
    lesestand,
    online_buchung,
    rechnung_pdf,
)
from beachhub_core.services.ergebnis import Ergebnis, Nachlauf, abgelehnt, ok
from beachhub_core.services.online_buchung import alarm

logger = logging.getLogger(__name__)

# Länge der Spalte anfrage_verarbeitet.typ. Der Typ einer unbekannten/kaputten Anfrage kommt
# unvalidiert vom Portal; ohne die Kürzung hier würde ein zu langer Wert einen DataError statt
# einer sauberen `abgelehnt/unbekannter_typ`-Antwort auslösen.
_TYP_MAXLAENGE = 40

Verarbeiter = Callable[[Session, Kunde, kanal.Anfrage, Any], Ergebnis]


def _kunde_zum_konto(db: Session, konto_id: Any) -> Kunde | None:
    if konto_id is None:
        return None
    return db.scalar(select(Kunde).where(Kunde.portal_konto_id == konto_id))


def _portal_gruppe(db: Session) -> Kundengruppe | None:
    name = str(konfiguration.hole(db, "portal_kundengruppe")).strip()
    if name:
        gruppe = db.scalar(select(Kundengruppe).where(Kundengruppe.name == name))
        if gruppe is not None:
            return gruppe
        logger.warning("Kundengruppe %r aus portal_kundengruppe fehlt – nehme die erste", name)
    return db.scalar(select(Kundengruppe).order_by(Kundengruppe.name).limit(1))


def _konto_angelegt(db: Session, anfrage: kanal.Anfrage, n: kanal.KontoAngelegt) -> Ergebnis:
    if anfrage.konto_id is None:
        return abgelehnt("ungueltig")
    k = _kunde_zum_konto(db, anfrage.konto_id)
    if k is None:
        email = n.email.strip().lower()
        # Ein anonymisierter Kunde trägt zwar eine geschwärzte E-Mail (kunden.anonymisiere), der
        # Ausschluss hier bleibt trotzdem bestehen: Ein gelöschtes Konto darf über keinen Pfad
        # wiederbelebt werden, auch nicht durch eine künftige Änderung an der Anonymisierung.
        k = db.scalar(
            select(Kunde)
            .where(Kunde.email == email, Kunde.anonymisiert_am.is_(None))
            .with_for_update()
        )
        if k is None:
            gruppe = _portal_gruppe(db)
            if gruppe is None:
                return abgelehnt("keine_kundengruppe")
            k = kunden.lege_an(
                db,
                name=n.anzeigename.strip(),
                email=email,
                kundengruppe_id=gruppe.id,
                zahlungsart="online",
                quelle="portal",
            )
        vorher = audit.als_dict(k)
        # Das Portal hat die Adresse per Login-Code bestätigt. Eine abweichende alte Verknüpfung
        # stammt aus einem gelöschten oder wiederhergestellten Portal und wird ersetzt.
        k.portal_konto_id = anfrage.konto_id
        db.flush()
        audit.protokolliere(
            db,
            quelle="portal",
            objekt_typ="kunde",
            objekt_id=k.id,
            vorher=vorher,
            nachher=audit.als_dict(k),
        )
    lesestand.markiere_geaendert(db, f"konto:{k.id}")
    return ok(kunde_id=k.id)


def _konto_geaendert(
    db: Session, kunde: Kunde, anfrage: kanal.Anfrage, n: kanal.KontoGeaendert
) -> Ergebnis:
    neu = n.anzeigename.strip()
    # Hat der Betreiber den Namen inzwischen gepflegt (etwa für die Rechnung), bleibt er.
    if kunde.name == n.bisher and neu and neu != kunde.name:
        vorher = audit.als_dict(kunde)
        kunde.name = neu
        db.flush()
        audit.protokolliere(
            db,
            quelle="portal",
            objekt_typ="kunde",
            objekt_id=kunde.id,
            vorher=vorher,
            nachher=audit.als_dict(kunde),
        )
        lesestand.markiere_geaendert(db, f"konto:{kunde.id}")
    return ok()


def _konto_loeschen(
    db: Session, kunde: Kunde, anfrage: kanal.Anfrage, n: kanal.KontoLoeschen
) -> Ergebnis:
    kunden.anonymisiere(db, kunde, quelle="portal")
    # Kein markiere_geaendert hier: Das Portal hat das Konto-Dokument bereits gelöscht; würde es
    # hier erneut als geändert markiert, käme es beim nächsten Verteilen wieder zum Vorschein.
    return ok()


def _buchung_anfragen(
    db: Session, kunde: Kunde, anfrage: kanal.Anfrage, n: kanal.BuchungAnfragen
) -> Ergebnis:
    # portal_url ist nur die mTLS-Kanaladresse (:8443, Task 1/7) und für den Kunden-Browser
    # untauglich; die Rückkehradresse nach der Zahlung kommt aus portal_oeffentliche_url.
    basis = settings.portal_oeffentliche_url.rstrip("/")
    rueckkehr = f"{basis}/zahlung/zurueck?anfrage={anfrage.anfrage_id}"
    return online_buchung.anfragen(
        db,
        kunde=kunde,
        anfrage_id=anfrage.anfrage_id,
        feld_id=n.feld_id,
        beginn=n.beginn,
        ende=n.ende,
        rueckkehr_url=rueckkehr,
    )


def _buchung_stornieren(
    db: Session, kunde: Kunde, anfrage: kanal.Anfrage, n: kanal.BuchungStornieren
) -> Ergebnis:
    return online_buchung.storniere_fuer_kunde(db, kunde=kunde, buchung_id=n.buchung_id)


def _rechnung_anfordern(
    db: Session, kunde: Kunde, anfrage: kanal.Anfrage, n: kanal.RechnungAnfordern
) -> Ergebnis:
    r = db.scalar(
        select(Rechnung).where(Rechnung.nummer == n.rechnung_nr, Rechnung.kunde_id == kunde.id)
    )
    if r is None:
        return abgelehnt("nicht_gefunden")
    if not r.pdf_pfad:
        # Die Rechnung gibt es, ihr PDF wurde aber nie erzeugt (etwa nach einem Fehler im
        # Nachlauf der Bestätigung): Der Betreiber muss es nachholen.
        erg = abgelehnt("nicht_gefunden")
        text = (
            f"Zur Rechnung {r.nummer} gibt es kein archiviertes PDF. Der Kunde konnte sie im "
            "Portal nicht abrufen."
        )
        erg.nach_commit.append(alarm("Rechnungs-PDF fehlt", text))
        return erg
    # Einmal lesen und gegen pdf_sha256 prüfen; dieselben Bytes gehen (falls intakt) auch raus –
    # kein zweiter, ungeprüfter Lesevorgang derselben Datei.
    daten = rechnung_pdf.lese_geprueft(r)
    if daten is None:
        text = (
            f"Das archivierte PDF der Rechnung {r.nummer} fehlt oder stimmt nicht mit seiner "
            "Prüfsumme überein. Der Kunde konnte es im Portal nicht abrufen."
        )
        erg = abgelehnt("nicht_gefunden")
        erg.nach_commit.append(alarm("Rechnungs-PDF beschädigt", text))
        return erg
    return ok(pdf_base64=base64.b64encode(daten).decode(), dateiname=f"Rechnung-{r.nummer}.pdf")


_MIT_KUNDE: dict[str, Verarbeiter] = {
    "konto_geaendert": _konto_geaendert,
    "konto_loeschen": _konto_loeschen,
    "buchung_anfragen": _buchung_anfragen,
    "buchung_stornieren": _buchung_stornieren,
    "rechnung_anfordern": _rechnung_anfordern,
}


def verarbeite(db: Session, anfrage: kanal.Anfrage) -> Ergebnis:
    schema = kanal.NUTZLAST.get(anfrage.typ)
    if schema is None:
        return abgelehnt("unbekannter_typ")
    try:
        n = schema.model_validate(anfrage.nutzlast)
    except ValidationError:
        return abgelehnt("ungueltig")
    if isinstance(n, kanal.KontoAngelegt):
        return _konto_angelegt(db, anfrage, n)
    if isinstance(n, kanal.ZahlungEingegangen):
        # Rückmeldungen des Anbieters kommen ohne Konto; die Zuordnung läuft über die Referenz.
        return online_buchung.zahlung_eingegangen(db, n)
    kunde = _kunde_zum_konto(db, anfrage.konto_id)
    if kunde is None:
        return abgelehnt("konto_unbekannt")
    return _MIT_KUNDE[anfrage.typ](db, kunde, anfrage, n)


def _speichere(db: Session, anfrage: kanal.Anfrage, antwort: kanal.Antwort) -> None:
    db.add(
        AnfrageVerarbeitet(
            anfrage_id=anfrage.anfrage_id,
            typ=anfrage.typ[:_TYP_MAXLAENGE],
            antwort_json=antwort.model_dump(mode="json", exclude_none=True),
        )
    )


def _bereits_verarbeitet(
    db: Session, anfrage: kanal.Anfrage
) -> tuple[kanal.Antwort, list[Nachlauf]] | None:
    vorhanden = db.get(AnfrageVerarbeitet, anfrage.anfrage_id)
    if vorhanden is None:
        return None
    return kanal.Antwort.model_validate(vorhanden.antwort_json), []


def bearbeite(db: Session, anfrage: kanal.Anfrage) -> tuple[kanal.Antwort, list[Nachlauf]]:
    """Verarbeitet eine Anfrage genau einmal und committet. Eine erneut zugestellte Anfrage
    bekommt die gespeicherte Antwort, ohne dass etwas ein zweites Mal geschieht. Committet eine
    andere Session dieselbe anfrage_id zuerst (Wettlauf zweier Zustellungen), kollidiert unser
    eigener Commit am Primärschlüssel von anfrage_verarbeitet; dann gilt die zuerst gespeicherte
    Antwort."""
    ergebnis = _bereits_verarbeitet(db, anfrage)
    if ergebnis is not None:
        return ergebnis
    try:
        erg = verarbeite(db, anfrage)
        _speichere(db, anfrage, erg.antwort)
        db.commit()
        return erg.antwort, erg.nach_commit
    except IntegrityError:
        db.rollback()
        ergebnis = _bereits_verarbeitet(db, anfrage)
        if ergebnis is not None:
            return ergebnis
        logger.exception("Anfrage %s (%s) fehlgeschlagen", anfrage.anfrage_id, anfrage.typ)
    except Exception:
        db.rollback()
        logger.exception("Anfrage %s (%s) fehlgeschlagen", anfrage.anfrage_id, anfrage.typ)

    fehler = kanal.Antwort(status="fehler")
    try:
        _speichere(db, anfrage, fehler)
        db.commit()
    except IntegrityError:
        # Auch hier kann inzwischen eine andere Session (mit einer echten, erfolgreichen
        # Antwort) gewonnen haben – deren Antwort gilt, statt unseres lokalen "fehler" darüber
        # zu schreiben.
        db.rollback()
        ergebnis = _bereits_verarbeitet(db, anfrage)
        if ergebnis is not None:
            return ergebnis
        logger.exception(
            "Anfrage %s (%s): Fehlerantwort konnte nicht gespeichert werden",
            anfrage.anfrage_id,
            anfrage.typ,
        )
        raise
    text = (
        f"Die Portal-Anfrage {anfrage.anfrage_id} vom Typ {anfrage.typ} konnte nicht "
        "verarbeitet werden. Der Kunde sieht eine Fehlermeldung. Details im Log des Hauptsystems."
    )
    return fehler, [alarm("Portal-Anfrage fehlgeschlagen", text)]
