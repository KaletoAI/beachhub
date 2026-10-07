"""Gemeinsame Gutscheinwelt und vollständige, historisch eingefrorene Testdaten."""

import uuid
from dataclasses import dataclass
from datetime import date, time, timedelta
from decimal import Decimal
from typing import Any

import pytest
from beachhub_core import clock
from beachhub_core.models import (
    Betriebszeit,
    Buchung,
    Feld,
    FeldRaster,
    Gutschein,
    GutscheinEinloesung,
    Kunde,
    Kundengruppe,
    Rechnung,
    Tarif,
    Zahlung,
)
from beachhub_core.services import buchungen, konfiguration, kunden, rechnungen
from beachhub_shared.zeit import kombiniere
from sqlalchemy.orm import Session

D = date(2027, 11, 25)


@dataclass
class GutscheinWelt:
    feld: Feld
    mitglied: Kunde
    nichtmitglied: Kunde
    mitglied_gruppe: Kundengruppe
    nichtmitglied_gruppe: Kundengruppe


@pytest.fixture
def gwelt(db: Session) -> GutscheinWelt:
    gruppen = [
        Kundengruppe(name="DJK-Mitglied", ust_satz=Decimal("7.00"), ist_mitglied=True),
        Kundengruppe(name="Nicht-Mitglied", ust_satz=Decimal("19.00"), ist_mitglied=False),
    ]
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    db.add_all([*gruppen, f, Tarif(name="Stunde", preis=Decimal("15.00"))])
    db.add_all(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)) for wt in range(7))
    db.flush()
    m = kunden.lege_an(db, name="Mitglied", email="mitglied@example.org")
    m.mitglied_bis = date(2030, 12, 31)
    n = kunden.lege_an(db, name="Gast", email="gast@example.org")
    konfiguration.setze(db, "gutschein_preis_mitglied", Decimal("26.00"))
    konfiguration.setze(db, "gutschein_preis_nichtmitglied", Decimal("30.00"))
    clock.set_override(db, D)
    db.commit()
    return GutscheinWelt(f, m, n, gruppen[0], gruppen[1])


def kaufgutschein(db: Session, welt: GutscheinWelt, **aenderungen: Any) -> Gutschein:
    werte: dict[str, Any] = dict(
        id=uuid.uuid4(),
        code=uuid.uuid4().hex.upper(),
        art="kauf",
        modell="einheit",
        steuer="beim_kauf",
        minuten=120,
        nennwert_brutto=None,
        restwert=None,
        kaufpreis_brutto=Decimal("26.00"),
        kundengruppe_id=welt.mitglied_gruppe.id,
        ust_satz=Decimal("7.00"),
        nur_mitglieder=True,
        kaeufer_kunde_id=welt.mitglied.id,
        inhaber_kunde_id=welt.mitglied.id,
        kaufdatum=D,
        kauf_reserviert_bis=kombiniere(D, time(12)),
        gueltig_tage_snapshot=730,
        gueltig_bis=D + timedelta(days=730),
        ausgestellt_am=kombiniere(D, time(10)),
        status="aktiv",
        max_einloesungen=1,
        serie="",
        notiz="Kauf-Snapshot",
        empfaenger_email=None,
    )
    werte.update(aenderungen)
    if "zahlung_id" not in werte:
        z = Zahlung(
            kunde_id=werte["kaeufer_kunde_id"],
            provider="test",
            provider_ref=uuid.uuid4().hex,
            betrag=werte["kaufpreis_brutto"],
            status="offen" if werte["status"] == "offen" else "bezahlt",
        )
        db.add(z)
        db.flush()
        werte["zahlung_id"] = z.id
    if "rechnung_id" not in werte and werte["status"] not in ("offen", "abgebrochen"):
        kaeufer = db.get(Kunde, werte["kaeufer_kunde_id"])
        assert kaeufer is not None
        r = rechnungen._neue_rechnung(
            db,
            kaeufer,
            "gutschein",
            [
                rechnungen.Posten(
                    None,
                    "Gutscheinkauf",
                    werte["kaufpreis_brutto"],
                    werte["ust_satz"] if werte["steuer"] == "beim_kauf" else Decimal("0"),
                )
            ],
            D,
            D,
            "bezahlt",
            quelle="system",
            gutschein_rolle="kauf",
            kreis="gutschein" if werte["steuer"] == "bei_einloesung" else "rechnung",
            gutschein_ids=[werte["id"]],
        )
        werte["rechnung_id"] = r.id
    g = Gutschein(**werte)
    db.add(g)
    db.flush()
    return g


def einloesung(
    db: Session,
    welt: GutscheinWelt,
    gutschein: Gutschein,
    *,
    buchung: Buchung | None = None,
    **aenderungen: Any,
) -> GutscheinEinloesung:
    if buchung is None:
        buchung = buchungen.lege_an(
            db,
            feld_id=welt.feld.id,
            kunde_id=welt.mitglied.id,
            beginn=kombiniere(date(2027, 12, 1), time(19)),
            ende=kombiniere(date(2027, 12, 1), time(21)),
        )
    r = db.get(Rechnung, gutschein.rechnung_id) if gutschein.rechnung_id else None
    brutto = gutschein.kaufpreis_brutto or Decimal("0.00")
    netto, ust = rechnungen.netto_ust(brutto, buchung.ust_satz)
    if r:
        brutto, netto, ust = r.positionen[0].brutto, r.positionen[0].netto, r.positionen[0].ust
    werte = dict(
        gutschein_id=gutschein.id,
        buchung_id=buchung.id,
        kunde_id=buchung.kunde_id,
        status="reserviert",
        minuten_gedeckt=120,
        betrag=buchung.preis,
        ursprung_position_id=r.positionen[0].id if r else None,
        beleg_brutto=brutto,
        beleg_netto=netto,
        beleg_ust=ust,
        zeitpunkt=kombiniere(D, time(11)),
    )
    werte.update(aenderungen)
    e = GutscheinEinloesung(**werte)
    db.add(e)
    db.flush()
    return e
