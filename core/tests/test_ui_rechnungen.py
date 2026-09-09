from datetime import date, time
from decimal import Decimal

import pytest
from beachhub_core import clock, mail
from beachhub_core.models import Betriebszeit, Feld, FeldRaster, Kundengruppe, Rechnung, Tarif
from beachhub_core.services import buchungen, kunden
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


@pytest.fixture
def welt(db: Session):
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    v = Kundengruppe(name="Verein", standard_zahlungsart="rechnung")
    db.add_all([f, v, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    v1 = kunden.lege_an(db, name="TSV", email="v@x.de", kundengruppe_id=v.id)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    from beachhub_shared.zeit import kombiniere

    buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=v1.id,
        beginn=kombiniere(date(2027, 12, 1), time(19)),
        ende=kombiniere(date(2027, 12, 1), time(20)),
    )
    db.commit()
    return f, v1


def test_monatslauf_liste_pdf_bezahlt_storno_export(
    eingeloggt: TestClient, db: Session, welt
) -> None:
    c = eingeloggt
    clock.set_override(db, date(2028, 1, 3))
    r = c.post(
        "/admin/rechnungen/monatslauf",
        data={"csrf_token": c.csrf, "jahr": "2027", "monat": "12"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    rechnung = db.query(Rechnung).one()
    assert rechnung.pdf_sha256 and any(
        m["betreff"] == f"Rechnung {rechnung.nummer}" for m in mail.TEST_AUSGANG
    )
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
    csv = c.get("/admin/rechnungen/export.csv?von=2028-01-01&bis=2028-01-31")
    assert (
        csv.status_code == 200
        and csv.headers["content-type"].startswith("text/csv")
        and len(csv.text.strip().splitlines()) == 3
    )


def test_rechnungen_detail_zeigt_positionen_und_integritaet(
    eingeloggt: TestClient, db: Session, welt
) -> None:
    c = eingeloggt
    clock.set_override(db, date(2028, 1, 3))
    c.post(
        "/admin/rechnungen/monatslauf",
        data={"csrf_token": c.csrf, "jahr": "2027", "monat": "12"},
        follow_redirects=False,
    )
    rechnung = db.query(Rechnung).one()
    seite = c.get(f"/admin/rechnungen/{rechnung.id}")
    assert seite.status_code == 200
    assert rechnung.nummer in seite.text
    assert "TSV" in seite.text
    assert "F1" in seite.text
    assert "PDF unverändert" in seite.text


def test_monatslauf_ungueltiger_monat_zeigt_meldung(eingeloggt: TestClient, db: Session) -> None:
    c = eingeloggt
    r = c.post(
        "/admin/rechnungen/monatslauf",
        data={"csrf_token": c.csrf, "jahr": "2027", "monat": "13"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "bh_flash" in r.cookies
    seite = c.get(r.headers["location"])
    assert "Monat muss zwischen 1 und 12" in seite.text
    assert db.query(Rechnung).count() == 0
