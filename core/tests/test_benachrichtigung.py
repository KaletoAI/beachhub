from datetime import date, time
from decimal import Decimal

import pytest
from beachhub_core import clock
from beachhub_core.models import Betriebszeit, Feld, FeldRaster, Kundengruppe, Tarif
from beachhub_core.services import benachrichtigung, buchungen, kunden, rechnungen
from beachhub_shared.zeit import kombiniere
from sqlalchemy.orm import Session


@pytest.fixture
def buchung(db: Session) -> object:
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    p = Kundengruppe(name="Privat")
    db.add_all([f, p, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    a = kunden.lege_an(
        db,
        name="Anna Müller",
        email="a@x.de",
        kundengruppe_id=p.id,
        adresse_strasse="Weg 1",
        adresse_plz="12345",
        adresse_ort="Ort",
    )
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    b = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=a.id,
        beginn=kombiniere(date(2027, 12, 1), time(19)),
        ende=kombiniere(date(2027, 12, 1), time(20)),
    )
    db.commit()
    return b


def test_buchung_bestaetigt_enthaelt_konfigurierten_vorlauf(
    db: Session, buchung, mail_ausgang
) -> None:
    benachrichtigung.buchung_bestaetigt(db, buchung)
    assert len(mail_ausgang) == 1
    assert "15 Minuten" in mail_ausgang[0]["text"]


def test_rechnung_ohne_pdf_wird_vor_dem_versand_erzeugt(db: Session, buchung, mail_ausgang) -> None:
    r = rechnungen.erzeuge_einzelrechnung(db, buchung)
    db.commit()
    assert r.pdf_pfad is None
    benachrichtigung.rechnung(db, r)
    assert r.pdf_pfad is not None
    assert mail_ausgang[-1]["anhaenge"] == [f"{r.nummer}.pdf"]
