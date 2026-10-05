from datetime import date, time
from decimal import Decimal
from pathlib import Path

import pytest
from beachhub_core import clock
from beachhub_core.config import settings
from beachhub_core.models import Betriebszeit, Feld, FeldRaster, Tarif
from beachhub_core.services import buchungen, kunden, rechnung_pdf, rechnungen
from beachhub_shared.zeit import kombiniere
from pypdf import PdfReader
from sqlalchemy.orm import Session


@pytest.fixture
def rechnung(db: Session):
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    db.add_all([f, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    a = kunden.lege_an(
        db,
        name="Anna Müller",
        email="a@x.de",
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
    from beachhub_core.services import storno

    pfad = rechnung_pdf.erzeuge(db, rechnung)
    db.commit()
    inhalt = Path(pfad).read_bytes()
    pruefsumme = rechnung.pdf_sha256
    storno.gutschreiben_positionen(db, rechnung.positionen, grund="Ausfall", quelle="admin")
    db.commit()
    with pytest.raises(rechnungen.RechnungsFehler, match="pdf_vorhanden"):
        rechnung_pdf.erzeuge(db, rechnung)
    assert Path(pfad).read_bytes() == inhalt
    assert rechnung.pdf_sha256 == pruefsumme and rechnung_pdf.pruefe_integritaet(rechnung)


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


def test_review_5_stornierte_originalrechnung_behauptet_keine_zahlung(
    db: Session, rechnung
) -> None:
    rechnungen.storniere(db, rechnung, admin_user_id=None, grund="Fehler")
    db.commit()
    pfad = rechnung_pdf.erzeuge(db, rechnung)
    db.commit()
    text = "".join(p.extract_text() for p in PdfReader(str(pfad)).pages)
    assert "storniert" in text.lower()
    assert "bereits beglichen" not in text and "Bitte überweisen" not in text


@pytest.mark.parametrize("zahlung,offen", [("30", "60,00"), ("0", "90,00")])
def test_review_5_pdf_forderung_zeigt_korrekturen_separat(
    db: Session, rechnung, zahlung: str, offen: str
) -> None:
    import re

    from beachhub_core.services import guthaben, storno

    k = rechnung.kunde
    tag = date(2027, 12, 1)
    r = rechnungen._neue_rechnung(
        db,
        k,
        "einzel",
        [rechnungen.Posten(None, f"Termin {i}", Decimal("30"), Decimal("19")) for i in range(4)],
        tag,
        tag,
        "offen",
        quelle="admin",
    )
    if Decimal(zahlung):
        guthaben.buche(db, kunde=k, betrag=Decimal(zahlung), art="manuell")
        rechnungen.verrechne_guthaben(db, r, quelle="admin")
    storno.gutschreiben_positionen(db, [r.positionen[0]], grund="Ausfall", quelle="admin")
    db.commit()
    html = rechnung_pdf.html(r)
    zeilen = {
        re.sub("<[^>]+>", "", zeile).strip()
        for zeile in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)
    }
    assert "Gesamtbetrag120,00 €" in zeilen
    assert "abzüglich Korrekturen-30,00 €" in zeilen
    assert f"Offener Betrag{offen} €" in zeilen
    if Decimal(zahlung):
        assert "abzüglich verrechnetes Guthaben-30,00 €" in zeilen
