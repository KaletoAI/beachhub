from datetime import date, time
from decimal import Decimal
from pathlib import Path

import pytest
from beachhub_core import clock
from beachhub_core.config import settings
from beachhub_core.models import Betriebszeit, Feld, FeldRaster, Kundengruppe, Tarif
from beachhub_core.services import buchungen, kunden, rechnung_pdf, rechnungen
from beachhub_shared.zeit import kombiniere
from pypdf import PdfReader
from sqlalchemy.orm import Session


@pytest.fixture
def rechnung(db: Session):
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
    r = rechnungen.erzeuge_einzelrechnung(db, b)
    db.commit()
    return r


def test_pdf_wird_erzeugt_und_gehasht(db: Session, rechnung) -> None:
    pfad = rechnung_pdf.erzeuge(db, rechnung)
    db.commit()
    assert Path(pfad).exists() and rechnung.pdf_sha256 and len(rechnung.pdf_sha256) == 64
    text = "".join(p.extract_text() for p in PdfReader(str(pfad)).pages)
    assert rechnung.nummer in text and "Anna Müller" in text and "30,00" in text and "19 %" in text
    assert rechnung_pdf.pruefe_integritaet(rechnung)
    with pytest.raises(rechnungen.RechnungsFehler, match="pdf_vorhanden"):
        rechnung_pdf.erzeuge(db, rechnung)


def test_registriertes_pdf_wird_nie_ueberschrieben(db: Session, rechnung) -> None:
    pfad = rechnung_pdf.erzeuge(db, rechnung)
    db.commit()
    inhalt = Path(pfad).read_bytes()
    with pytest.raises(rechnungen.RechnungsFehler, match="pdf_vorhanden"):
        rechnung_pdf.erzeuge(db, rechnung)
    assert Path(pfad).read_bytes() == inhalt


def test_verwaiste_pdf_datei_wird_ersetzt(db: Session, rechnung) -> None:
    ordner = settings.data_dir / "rechnungen"
    ordner.mkdir(parents=True, exist_ok=True)
    pfad = ordner / f"{rechnung.nummer}.pdf"
    pfad.write_bytes(b"schon-da")
    ergebnis = rechnung_pdf.erzeuge(db, rechnung)
    db.commit()
    assert ergebnis == pfad
    assert pfad.read_bytes().startswith(b"%PDF")


def test_pruefe_integritaet_fehlt_datei(db: Session, rechnung) -> None:
    pfad = rechnung_pdf.erzeuge(db, rechnung)
    db.commit()
    Path(pfad).unlink()
    assert rechnung_pdf.pruefe_integritaet(rechnung) is False
