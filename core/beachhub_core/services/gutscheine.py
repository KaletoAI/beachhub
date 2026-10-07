"""Freischaltcodes, Verwaltung und manuelle Rückgabe ohne Walletmutation."""

import csv
import io
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from beachhub_core import clock
from beachhub_core.models import Feld, Gutschein, GutscheinEinloesung, Rechnung
from beachhub_core.services import (
    audit,
    gutschein_belege,
    gutschein_codes,
    gutschein_sperren,
    konfiguration,
    lesestand,
)
from beachhub_core.services.gutschein_typen import GutscheinRueckgabe
from beachhub_core.services.rechnungen import csv_sicher


def _auditdaten(daten: dict[str, Any] | None) -> dict[str, Any] | None:
    if daten is not None and daten.get("status") in (Gutschein.OFFEN, Gutschein.ABGEBROCHEN):
        # Auch das globale Audit zeigt JSON: noch nicht ausgegebene Codes bleiben geheim.
        return {key: value for key, value in daten.items() if key != "code"}
    return daten


def _aenderung(
    db: Session,
    g: Gutschein,
    vorher: dict[str, Any] | None,
    *,
    admin_user_id: uuid.UUID | None,
    quelle: str = "admin",
) -> None:
    nachher = audit.als_dict(g)
    if vorher == nachher:
        return
    audit.protokolliere(
        db,
        quelle=quelle,
        objekt_typ="gutschein",
        objekt_id=g.id,
        vorher=_auditdaten(vorher),
        nachher=_auditdaten(nachher),
        admin_user_id=admin_user_id,
    )
    kunden = {
        str(daten[key])
        for daten in (vorher or {}, nachher)
        for key in ("kaeufer_kunde_id", "inhaber_kunde_id")
        if daten.get(key) is not None
    }
    lesestand.markiere_geaendert(db, *(f"konto:{k}" for k in sorted(kunden)))


def stelle_freicodes_aus(
    db: Session,
    *,
    anzahl: int,
    gueltig_bis: date | None,
    max_einloesungen: int = 1,
    feld_id: uuid.UUID | None = None,
    zeitraum_von: date | None = None,
    zeitraum_bis: date | None = None,
    serie: str = "",
    notiz: str = "",
    admin_user_id: uuid.UUID | None,
) -> list[Gutschein]:
    if not 1 <= anzahl <= 1000:
        raise ValueError("Bitte zwischen 1 und 1000 Codes ausstellen")
    if not 1 <= max_einloesungen <= 2147483647:
        raise ValueError("Bitte zwischen 1 und 2147483647 Einlösungen wählen")
    if len(serie) > 100 or len(notiz) > 1000:
        raise ValueError("Serie höchstens 100, Notiz höchstens 1000 Zeichen")
    if zeitraum_von and zeitraum_bis and zeitraum_von > zeitraum_bis:
        raise ValueError("Der Zeitraum endet vor seinem Beginn")
    heute = clock.today(db)
    bis = gueltig_bis or heute + timedelta(days=konfiguration.hole(db, "freicode_gueltig_tage"))
    if bis < heute:
        raise ValueError("Die Gültigkeit darf nicht in der Vergangenheit enden")
    gutschein_sperren.bereite_vorgang_vor(db, kunde_ids=(), gutschein_ids=())
    if feld_id is not None and db.get(Feld, feld_id) is None:
        raise ValueError("Unbekanntes Feld")
    minuten = konfiguration.hole(db, "gutschein_minuten")
    ausgestellt = clock.now(db)
    codes = []
    for _ in range(anzahl):
        g = Gutschein(
            art="frei",
            modell="einheit",
            steuer="keine",
            minuten=minuten,
            max_einloesungen=max_einloesungen,
            gueltig_bis=bis,
            feld_id=feld_id,
            zeitraum_von=zeitraum_von,
            zeitraum_bis=zeitraum_bis,
            serie=serie,
            notiz=notiz,
            status=Gutschein.AKTIV,
            ausgestellt_von_admin_id=admin_user_id,
            ausgestellt_am=ausgestellt,
        )
        gutschein_codes.fuege_mit_neuem_code_ein(db, g)
        _aenderung(db, g, None, admin_user_id=admin_user_id)
        codes.append(g)
    db.flush()
    return codes


def _vorbereiten(db: Session, g: Gutschein) -> Gutschein:
    return gutschein_sperren.bereite_vorgang_vor(
        db, kunde_ids=(), gutschein_ids=(g.id,)
    ).gutscheine[g.id]


def sperre(db: Session, gutschein: Gutschein, *, admin_user_id: uuid.UUID | None = None) -> None:
    g = _vorbereiten(db, gutschein)
    if g.status in (Gutschein.OFFEN, Gutschein.ABGEBROCHEN):
        raise ValueError("Ein nicht abgeschlossener Kauf kann hier nicht geändert werden")
    vorher = audit.als_dict(g)
    g.status = Gutschein.GESPERRT
    _aenderung(db, g, vorher, admin_user_id=admin_user_id)
    db.flush()


def _verbrauchsstatus(db: Session, g: Gutschein) -> str:
    if g.modell == "wert":
        if g.restwert == 0:
            return Gutschein.EINGELOEST
        return (
            Gutschein.TEILWEISE_EINGELOEST if g.restwert != g.nennwert_brutto else Gutschein.AKTIV
        )
    nutzungen = (
        db.scalar(
            select(func.count())
            .select_from(GutscheinEinloesung)
            .where(
                GutscheinEinloesung.gutschein_id == g.id,
                GutscheinEinloesung.status != GutscheinEinloesung.RUECKGAENGIG,
            )
        )
        or 0
    )
    return Gutschein.EINGELOEST if nutzungen >= g.max_einloesungen else Gutschein.AKTIV


def setze_gueltigkeit(
    db: Session,
    gutschein: Gutschein,
    gueltig_bis: date,
    *,
    reaktivieren: bool = False,
    admin_user_id: uuid.UUID | None = None,
) -> None:
    g = _vorbereiten(db, gutschein)
    if g.status in (Gutschein.OFFEN, Gutschein.ABGEBROCHEN):
        raise ValueError("Ein nicht abgeschlossener Kauf kann hier nicht geändert werden")
    if reaktivieren and g.zurueckgegeben_am is not None:
        raise ValueError("Ein zurückgegebener Gutschein darf nicht reaktiviert werden")
    if reaktivieren and g.status not in (Gutschein.GESPERRT, Gutschein.VERFALLEN):
        raise ValueError("Nur gesperrte oder verfallene Gutscheine können reaktiviert werden")
    if reaktivieren and gueltig_bis < clock.today(db):
        raise ValueError("Zum Reaktivieren ein gültiges Ablaufdatum wählen")
    vorher = audit.als_dict(g)
    g.gueltig_bis = gueltig_bis
    if reaktivieren:
        g.status = _verbrauchsstatus(db, g)
    _aenderung(db, g, vorher, admin_user_id=admin_user_id)
    db.flush()


def verfalle(db: Session) -> int:
    heute = clock.today(db)
    zustaende = (Gutschein.AKTIV, Gutschein.TEILWEISE_EINGELOEST)
    ids = list(
        db.scalars(
            select(Gutschein.id).where(
                Gutschein.status.in_(zustaende), Gutschein.gueltig_bis < heute
            )
        )
    )
    kontext = gutschein_sperren.bereite_vorgang_vor(db, kunde_ids=(), gutschein_ids=ids)
    anzahl = 0
    for g in kontext.gutscheine.values():
        if g.status not in zustaende or g.gueltig_bis is None or g.gueltig_bis >= heute:
            continue
        vorher = audit.als_dict(g)
        g.status = Gutschein.VERFALLEN
        _aenderung(db, g, vorher, admin_user_id=None, quelle="system")
        anzahl += 1
    db.flush()
    return anzahl


def serie_csv(db: Session, serie: str) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\n")
    writer.writerow(
        [
            "code",
            "serie",
            "gueltig_bis",
            "minuten",
            "max_einloesungen",
            "feld_id",
            "zeitraum_von",
            "zeitraum_bis",
            "status",
            "notiz",
        ]
    )
    for g in db.scalars(
        select(Gutschein)
        .where(Gutschein.art == "frei", Gutschein.serie == serie)
        .order_by(Gutschein.ausgestellt_am, Gutschein.id)
    ):
        writer.writerow(
            [
                csv_sicher(str(v) if v is not None else "")
                for v in (
                    gutschein_codes.anzeige(g.code),
                    g.serie,
                    g.gueltig_bis,
                    g.minuten,
                    g.max_einloesungen,
                    g.feld_id,
                    g.zeitraum_von,
                    g.zeitraum_bis,
                    g.status,
                    g.notiz,
                )
            ]
        )
    return buf.getvalue()


def gib_zurueck(
    db: Session,
    g: Gutschein,
    *,
    grund: str,
    admin_user_id: uuid.UUID | None,
) -> GutscheinRueckgabe:
    """Ungenutzten Kauf dauerhaft sperren; Erstattung erfolgt außerhalb des Systems.

    Beziehungserweiterungen brauchen einen vollständigen Transaktionsretry, bevor
    irgendein Beleg oder Audit entsteht. Ein wiederholter Aufruf erstattet nichts.
    """
    grund = grund.strip()
    if not grund or len(grund) > 1000:
        raise ValueError("Bitte einen Grund mit höchstens 1000 Zeichen angeben")
    gid = g.id
    for versuch in range(3):
        try:
            g = gutschein_sperren.bereite_vorgang_vor(
                db, kunde_ids=(), gutschein_ids=(gid,)
            ).gutscheine[gid]
            break
        except gutschein_sperren.GutscheinVorbereitungWiederholen:
            db.rollback()
            if versuch == 2:
                raise
    if g.zurueckgegeben_am is not None:
        return GutscheinRueckgabe(None, Decimal("0.00"))
    if g.art != "kauf" or g.status in (Gutschein.OFFEN, Gutschein.ABGEBROCHEN):
        raise ValueError("Nur ein abgeschlossener Kaufgutschein kann zurückgegeben werden")
    genutzt = db.scalar(
        select(GutscheinEinloesung.id)
        .where(
            GutscheinEinloesung.gutschein_id == gid,
            (GutscheinEinloesung.status != GutscheinEinloesung.RUECKGAENGIG)
            | (GutscheinEinloesung.rueckabwicklung_art == "guthaben"),
        )
        .limit(1)
    )
    if genutzt is not None or (g.modell == "wert" and g.restwert != g.nennwert_brutto):
        raise ValueError("Nur vollständig ungenutzte Gutscheine können zurückgegeben werden")
    r = db.get(Rechnung, g.rechnung_id) if g.rechnung_id else None
    if r is None or r.kunde_id != g.kaeufer_kunde_id or r.gutschein_rolle != "kauf":
        raise ValueError("Der ursprüngliche Kaufbeleg fehlt oder ist falsch zugeordnet")
    vorher = audit.als_dict(g)
    beleg = gutschein_belege.korrigiere_kauf_rueckgabe(
        db, g, r, grund=grund, admin_user_id=admin_user_id
    )
    betrag = g.kaufpreis_brutto if beleg is not None else Decimal("0.00")
    assert betrag is not None
    g.status = Gutschein.GESPERRT
    g.zurueckgegeben_am = clock.now(db)
    g.rueckgabe_betrag, g.rueckgabe_grund = betrag, grund
    _aenderung(db, g, vorher, admin_user_id=admin_user_id)
    db.flush()
    return GutscheinRueckgabe(beleg, betrag)
