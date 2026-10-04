from datetime import date, time
from decimal import Decimal

import pytest
from beachhub_core import clock, mail
from beachhub_core.models import Betriebszeit, Feld, FeldRaster, Kunde, Rechnung, Tarif
from beachhub_core.services import buchungen, kunden, rechnungen
from beachhub_shared.zeit import kombiniere
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


@pytest.fixture
def welt(db: Session) -> tuple[Feld, Kunde]:
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    db.add_all([f, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    v1 = kunden.lege_an(db, name="TSV", email="v@x.de", rechnungskunde=True)
    v1.mitglied_bis = date(2028, 4, 30)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    return f, v1


def _abo(c: TestClient, f: Feld, v1: Kunde) -> None:
    r = c.post(
        "/admin/belegung/dauer",
        data={
            "csrf_token": c.csrf,
            "kunde_id": str(v1.id),
            "feld_id": str(f.id),
            "wochentag": "2",
            "start": "19:00",
            "ende": "20:00",
            "gueltig_von": "2027-12-01",
            "gueltig_bis": "2027-12-08",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303


def test_saisonrechnung_liste_pdf_bezahlt_storno_export(
    eingeloggt: TestClient, db: Session, welt
) -> None:
    c = eingeloggt
    _abo(c, *welt)
    rechnung = db.query(Rechnung).one()
    assert rechnung.art == "saison" and rechnung.pdf_sha256
    assert any(m["betreff"] == f"Rechnung {rechnung.nummer}" for m in mail.TEST_AUSGANG)
    seite = c.get("/admin/rechnungen?status=offen")
    assert rechnung.nummer in seite.text and "TSV" in seite.text
    pdf = c.get(f"/admin/rechnungen/{rechnung.id}/pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    r = c.post(
        f"/admin/rechnungen/{rechnung.id}/bezahlt",
        data={"csrf_token": c.csrf},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(rechnung)
    assert rechnung.status == "bezahlt"
    r = c.post(
        f"/admin/rechnungen/{rechnung.id}/storno",
        data={"csrf_token": c.csrf, "grund": "Fehler"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.expire_all()
    assert db.query(Rechnung).count() == 2 and db.get(Rechnung, rechnung.id).status == "storniert"
    csv = c.get("/admin/rechnungen/export.csv?von=2027-11-01&bis=2027-11-30")
    assert (
        csv.status_code == 200
        and csv.headers["content-type"].startswith("text/csv")
        and len(csv.text.strip().splitlines()) == 3
    )


def test_rechnungen_detail_zeigt_positionen_und_integritaet(
    eingeloggt: TestClient, db: Session, welt
) -> None:
    c = eingeloggt
    _abo(c, *welt)
    rechnung = db.query(Rechnung).one()
    seite = c.get(f"/admin/rechnungen/{rechnung.id}")
    assert seite.status_code == 200
    assert rechnung.nummer in seite.text and "TSV" in seite.text and "F1" in seite.text
    assert "PDF unverändert" in seite.text and "Offener Betrag" in seite.text


def test_pdf_get_ohne_pdf_redirect_und_post_erzeugt(
    eingeloggt: TestClient, db: Session, welt
) -> None:
    c = eingeloggt
    f, v1 = welt
    b = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=v1.id,
        beginn=kombiniere(date(2027, 12, 1), time(19)),
        ende=kombiniere(date(2027, 12, 1), time(20)),
    )
    r = rechnungen.erzeuge_einzelrechnung(db, b, status="offen")
    db.commit()
    ohne_pdf = c.get(f"/admin/rechnungen/{r.id}/pdf", follow_redirects=False)
    assert ohne_pdf.status_code == 303
    seite = c.get(ohne_pdf.headers["location"])
    assert "PDF noch nicht erzeugt" in seite.text
    erzeugen = c.post(
        f"/admin/rechnungen/{r.id}/pdf", data={"csrf_token": c.csrf}, follow_redirects=False
    )
    assert erzeugen.status_code == 303
    db.refresh(r)
    assert r.pdf_pfad
    pdf = c.get(f"/admin/rechnungen/{r.id}/pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"


def test_kein_monatslauf_mehr(eingeloggt: TestClient) -> None:
    c = eingeloggt
    assert "Monatslauf" not in c.get("/admin/rechnungen").text
    r = c.post(
        "/admin/rechnungen/monatslauf",
        data={"csrf_token": c.csrf, "jahr": "2027", "monat": "12"},
    )
    assert r.status_code == 405
