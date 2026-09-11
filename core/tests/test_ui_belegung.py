from datetime import date, time
from decimal import Decimal

import pytest
from beachhub_core import clock, mail
from beachhub_core.models import (
    Betriebszeit,
    Buchung,
    Dauerbuchung,
    Feld,
    FeldRaster,
    Kundengruppe,
    Sperre,
    Storno,
    Tarif,
)
from beachhub_core.services import kunden
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


@pytest.fixture
def welt(db: Session):
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    p = Kundengruppe(name="Privat")
    v = Kundengruppe(name="Verein", standard_zahlungsart="rechnung")
    db.add_all([f, p, v, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    a = kunden.lege_an(db, name="A", email="a@x.de", kundengruppe_id=p.id)
    v1 = kunden.lege_an(db, name="TSV", email="v@x.de", kundengruppe_id=v.id)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    return f, a, v1


def test_woche_zeigt_slots_und_buchung(eingeloggt: TestClient, db: Session, welt) -> None:
    f, a, _ = welt
    c = eingeloggt
    seite = c.get(f"/admin/belegung?feld={f.id}&woche=2027-11-29")
    assert (
        seite.status_code == 200
        and "Mi 01.12." in seite.text
        and 'class="zelle frei"' in seite.text
    )
    r = c.post(
        "/admin/belegung/buchung",
        data={
            "csrf_token": c.csrf,
            "feld_id": str(f.id),
            "kunde_id": str(a.id),
            "beginn": "2027-12-01T19:00",
            "ende": "2027-12-01T21:00",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    b = db.query(Buchung).one()
    assert (
        b.preis == Decimal("60.00") and b.rechnung_position_id is not None
    )  # Onlinekunde: sofort Einzelrechnung
    assert [m["betreff"].split(":")[0] for m in mail.TEST_AUSGANG] == [
        "Buchung bestätigt",
        "Rechnung 2027-00001",
    ]
    seite = c.get(f"/admin/belegung?feld={f.id}&woche=2027-11-29")
    assert 'class="zelle belegt"' in seite.text and "A" in seite.text
    detail = c.get(f"/admin/belegung/buchung/{b.id}")
    assert detail.status_code == 200 and "PIN" in detail.text


def test_storno_ueber_ui(eingeloggt: TestClient, db: Session, welt) -> None:
    f, _, v1 = welt
    c = eingeloggt
    c.post(
        "/admin/belegung/buchung",
        data={
            "csrf_token": c.csrf,
            "feld_id": str(f.id),
            "kunde_id": str(v1.id),
            "beginn": "2027-12-01T19:00",
            "ende": "2027-12-01T20:00",
        },
    )
    b = db.query(Buchung).one()
    r = c.post(
        f"/admin/belegung/buchung/{b.id}/storno",
        data={"csrf_token": c.csrf, "grund": "Test", "kostenfrei": "nein"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(b)
    assert b.status == "storniert" and not b.storno.kostenfrei


def test_sperre_mit_entscheidung(eingeloggt: TestClient, db: Session, welt) -> None:
    f, a, _ = welt
    c = eingeloggt
    c.post(
        "/admin/belegung/buchung",
        data={
            "csrf_token": c.csrf,
            "feld_id": str(f.id),
            "kunde_id": str(a.id),
            "beginn": "2027-12-01T19:00",
            "ende": "2027-12-01T20:00",
        },
    )
    b = db.query(Buchung).one()
    daten = {
        "csrf_token": c.csrf,
        "alle_felder": "1",
        "beginn": "2027-12-01T18:00",
        "ende": "2027-12-01T22:00",
        "grund": "Turnier",
    }
    r = c.post("/admin/belegung/sperre", data=daten)
    assert r.status_code == 200 and f"entscheidung_{b.id}" in r.text
    r = c.post(
        "/admin/belegung/sperre",
        data={**daten, f"entscheidung_{b.id}": "stornieren"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert db.query(Sperre).count() == 1
    db.refresh(b)
    assert b.status == "storniert" and b.storno.kostenfrei


def test_dauerbuchung_planen_und_anlegen(eingeloggt: TestClient, db: Session, welt) -> None:
    f, _, v1 = welt
    c = eingeloggt
    daten = {
        "csrf_token": c.csrf,
        "kunde_id": str(v1.id),
        "feld_id": str(f.id),
        "wochentag": "1",
        "start": "19:00",
        "ende": "21:00",
        "gueltig_von": "2027-12-01",
        "gueltig_bis": "2027-12-31",
    }
    r = c.post("/admin/belegung/dauer/planen", data=daten)
    assert r.status_code == 200 and "07.12.2027" in r.text and "auslassen_2027-12-28" in r.text
    r = c.post(
        "/admin/belegung/dauer", data={**daten, "auslassen_2027-12-28": "1"}, follow_redirects=False
    )
    assert r.status_code == 303
    d = db.query(Dauerbuchung).one()
    assert len(d.buchungen) == 3
    assert any(m["betreff"] == "Ihre Dauerbuchung" for m in mail.TEST_AUSGANG)
    r = c.post(
        f"/admin/belegung/dauer/{d.id}/beenden",
        data={"csrf_token": c.csrf, "ab": "2027-12-14"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.expire_all()
    assert [b.status for b in db.get(Dauerbuchung, d.id).buchungen] == [
        "bestaetigt",
        "storniert",
        "storniert",
    ]


def test_woche_ohne_felder_rendert(eingeloggt: TestClient, db: Session) -> None:
    seite = eingeloggt.get("/admin/belegung")
    assert seite.status_code == 200


def test_alle_seiten_rendern(eingeloggt: TestClient, db: Session, welt) -> None:
    f, a, v1 = welt
    c = eingeloggt
    assert c.get(f"/admin/belegung?feld={f.id}&woche=2027-11-29").status_code == 200
    assert (
        c.get(f"/admin/belegung/buchung/neu?feld={f.id}&beginn=2027-12-01T19:00").status_code == 200
    )
    c.post(
        "/admin/belegung/buchung",
        data={
            "csrf_token": c.csrf,
            "feld_id": str(f.id),
            "kunde_id": str(a.id),
            "beginn": "2027-12-01T19:00",
            "ende": "2027-12-01T20:00",
        },
    )
    b = db.query(Buchung).one()
    assert c.get(f"/admin/belegung/buchung/{b.id}").status_code == 200
    assert c.get("/admin/belegung/sperre/neu").status_code == 200
    assert c.get("/admin/belegung/dauer/neu").status_code == 200
    daten = {
        "csrf_token": c.csrf,
        "kunde_id": str(v1.id),
        "feld_id": str(f.id),
        "wochentag": "3",
        "start": "10:00",
        "ende": "11:00",
        "gueltig_von": "2027-12-02",
        "gueltig_bis": "2027-12-02",
    }
    c.post("/admin/belegung/dauer/planen", data=daten)
    c.post("/admin/belegung/dauer", data=daten)
    d = db.query(Dauerbuchung).one()
    assert c.get(f"/admin/belegung/dauer/{d.id}").status_code == 200


def test_dauerbuchung_kollision_mit_entscheidung(eingeloggt: TestClient, db: Session, welt) -> None:
    f, a, v1 = welt
    c = eingeloggt
    c.post(
        "/admin/belegung/buchung",
        data={
            "csrf_token": c.csrf,
            "feld_id": str(f.id),
            "kunde_id": str(a.id),
            "beginn": "2027-12-07T19:00",
            "ende": "2027-12-07T21:00",
        },
    )
    fremd = db.query(Buchung).one()
    daten = {
        "csrf_token": c.csrf,
        "kunde_id": str(v1.id),
        "feld_id": str(f.id),
        "wochentag": "1",
        "start": "19:00",
        "ende": "21:00",
        "gueltig_von": "2027-12-01",
        "gueltig_bis": "2027-12-31",
    }
    r = c.post("/admin/belegung/dauer/planen", data=daten)
    assert r.status_code == 200 and f"entscheidung_{fremd.id}" in r.text
    r = c.post("/admin/belegung/dauer", data=daten)
    assert r.status_code == 200 and "entscheiden" in r.text
    assert db.query(Dauerbuchung).count() == 0
    r = c.post(
        "/admin/belegung/dauer",
        data={**daten, f"entscheidung_{fremd.id}": "stornieren"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(fremd)
    assert fremd.status == "storniert"
    d = db.query(Dauerbuchung).one()
    assert len(d.buchungen) == 4


def test_dauerbuchung_fehlende_pflichtfelder_zeigt_meldung(
    eingeloggt: TestClient, db: Session, welt
) -> None:
    f, _, v1 = welt
    c = eingeloggt
    daten = {
        "csrf_token": c.csrf,
        "kunde_id": "",
        "feld_id": str(f.id),
        "wochentag": "1",
        "start": "19:00",
        "ende": "21:00",
        "gueltig_von": "2027-12-01",
        "gueltig_bis": "2027-12-31",
    }
    r = c.post("/admin/belegung/dauer", data=daten)
    assert r.status_code == 200 and ("fehlt" in r.text or "Ungültige Auswahl" in r.text)
    assert db.query(Dauerbuchung).count() == 0


def test_kulanz_ueber_ui(eingeloggt: TestClient, db: Session, welt) -> None:
    f, _, v1 = welt
    c = eingeloggt
    c.post(
        "/admin/belegung/buchung",
        data={
            "csrf_token": c.csrf,
            "feld_id": str(f.id),
            "kunde_id": str(v1.id),
            "beginn": "2027-12-01T19:00",
            "ende": "2027-12-01T20:00",
        },
    )
    b = db.query(Buchung).one()
    c.post(
        f"/admin/belegung/buchung/{b.id}/storno",
        data={"csrf_token": c.csrf, "grund": "Test", "kostenfrei": "nein"},
    )
    s = db.query(Storno).filter_by(buchung_id=b.id).one()
    assert not s.kostenfrei
    r = c.post(
        f"/admin/belegung/buchung/{b.id}/kulanz",
        data={"csrf_token": c.csrf, "grund": "Kulanzgrund"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(s)
    assert s.kostenfrei is True


def test_sperre_loeschen(eingeloggt: TestClient, db: Session, welt) -> None:
    f, _, _ = welt
    c = eingeloggt
    daten = {
        "csrf_token": c.csrf,
        "alle_felder": "1",
        "beginn": "2027-12-05T08:00",
        "ende": "2027-12-05T09:00",
        "grund": "Wartung",
    }
    c.post("/admin/belegung/sperre", data=daten)
    s = db.query(Sperre).one()
    r = c.post(
        f"/admin/belegung/sperre/{s.id}/loeschen",
        data={"csrf_token": c.csrf},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert db.query(Sperre).count() == 0


def test_woche_zeigt_sperre_zelle(eingeloggt: TestClient, db: Session, welt) -> None:
    f, _, _ = welt
    c = eingeloggt
    daten = {
        "csrf_token": c.csrf,
        "alle_felder": "1",
        "beginn": "2027-12-01T09:00",
        "ende": "2027-12-01T12:00",
        "grund": "Turnier",
    }
    c.post("/admin/belegung/sperre", data=daten)
    seite = c.get(f"/admin/belegung?feld={f.id}&woche=2027-11-29")
    assert seite.status_code == 200 and 'class="zelle sperre"' in seite.text


def test_buchung_neu_ohne_slot_fuehrt_in_den_plan(eingeloggt: TestClient, welt) -> None:
    """Der Pfad wird normalerweise mit Feld und Beginn aufgerufen. Ohne diese Angaben –
    über ein Lesezeichen etwa – gehörte der Betreiber in den Belegungsplan geführt und
    nicht auf eine technische Fehlerseite (vorher: HTTP 422 mit JSON-Rumpf)."""
    antwort = eingeloggt.get("/admin/belegung/buchung/neu", follow_redirects=False)
    assert antwort.status_code == 303
    assert antwort.headers["location"] == "/admin/belegung"

    seite = eingeloggt.get("/admin/belegung/buchung/neu", follow_redirects=True)
    assert seite.status_code == 200
    assert "Belegungsplan" in seite.text
