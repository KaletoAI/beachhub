from decimal import Decimal

from beachhub_core.models import Feld, Konfiguration, Tarif
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def test_feld_anlegen_und_raster(eingeloggt: TestClient, db: Session) -> None:
    c = eingeloggt
    r = c.post(
        "/admin/felder",
        data={"csrf_token": c.csrf, "name": "Feld 1", "reihenfolge": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    feld = db.query(Feld).one()
    r = c.post(
        f"/admin/felder/{feld.id}/raster",
        data={
            "csrf_token": c.csrf,
            "wochentag": "",
            "modus": "dauer",
            "slot_minuten": "60",
            "fenster": "",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    r = c.post(
        f"/admin/felder/{feld.id}/raster",
        data={
            "csrf_token": c.csrf,
            "wochentag": "2",
            "modus": "fenster",
            "slot_minuten": "",
            "fenster": "19:00-21:00, 21:00-23:00",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.expire_all()
    assert len(db.get(Feld, feld.id).raster) == 2
    seite = c.get(f"/admin/felder/{feld.id}")
    assert "19:00-21:00" in seite.text and "Feld 1" in seite.text


def test_validierung_zeigt_fehler(eingeloggt: TestClient) -> None:
    c = eingeloggt
    r = c.post(
        "/admin/betriebszeiten",
        data={
            "csrf_token": c.csrf,
            "wochentag": "0",
            "oeffnet": "20:00",
            "schliesst": "18:00",
            "gueltig_von": "",
            "gueltig_bis": "",
        },
    )
    assert r.status_code == 200 and "Schließzeit" in r.text


def test_tarif_und_konfiguration(eingeloggt: TestClient, db: Session) -> None:
    c = eingeloggt
    r = c.post(
        "/admin/tarife",
        data={
            "csrf_token": c.csrf,
            "name": "Abend",
            "preis": "40,00",
            "feld_id": "",
            "wochentag": "",
            "uhrzeit_von": "18:00",
            "uhrzeit_bis": "23:00",
            "kundengruppe_id": "",
            "gueltig_von": "",
            "gueltig_bis": "",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    t = db.query(Tarif).one()
    assert t.preis == Decimal("40.00") and t.uhrzeit_von.hour == 18
    r = c.post(
        "/admin/konfiguration",
        data={
            "csrf_token": c.csrf,
            "storno_frist_stunden": "48",
            "ust_satz": "19.00",
            **{k: "" for k in ("fenster_tage",)},
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert db.query(Konfiguration).filter_by(schluessel="storno_frist_stunden").one().wert == "48"
    assert (
        db.query(Konfiguration).filter_by(schluessel="fenster_tage").first() is None
    )  # leer = Default behalten


def test_doppelter_feldname_zeigt_meldung(eingeloggt: TestClient, db: Session) -> None:
    c = eingeloggt
    c.post(
        "/admin/felder",
        data={"csrf_token": c.csrf, "name": "Feld X", "reihenfolge": "1"},
        follow_redirects=False,
    )
    r = c.post("/admin/felder", data={"csrf_token": c.csrf, "name": "Feld X", "reihenfolge": "2"})
    assert r.status_code == 200
    assert "bereits vergeben" in r.text
    assert db.query(Feld).count() == 1


def test_leere_pflichtzeit_zeigt_meldung(eingeloggt: TestClient) -> None:
    c = eingeloggt
    r = c.post(
        "/admin/betriebszeiten",
        data={
            "csrf_token": c.csrf,
            "wochentag": "0",
            "oeffnet": "",
            "schliesst": "20:00",
            "gueltig_von": "",
            "gueltig_bis": "",
        },
    )
    assert r.status_code == 200
    assert "fehlt" in r.text


def test_tarif_aendern_validiert(eingeloggt: TestClient, db: Session) -> None:
    c = eingeloggt
    c.post(
        "/admin/tarife",
        data={
            "csrf_token": c.csrf,
            "name": "Standard",
            "preis": "20,00",
            "feld_id": "",
            "wochentag": "",
            "uhrzeit_von": "",
            "uhrzeit_bis": "",
            "kundengruppe_id": "",
            "gueltig_von": "",
            "gueltig_bis": "",
        },
        follow_redirects=False,
    )
    t = db.query(Tarif).one()
    r = c.post(
        f"/admin/tarife/{t.id}",
        data={
            "csrf_token": c.csrf,
            "name": "Standard",
            "preis": "-5",
            "feld_id": "",
            "wochentag": "",
            "uhrzeit_von": "",
            "uhrzeit_bis": "",
            "kundengruppe_id": "",
            "gueltig_von": "",
            "gueltig_bis": "",
            "aktiv": "1",
        },
    )
    assert r.status_code == 200
    assert "negativ" in r.text
    db.expire_all()
    assert db.get(Tarif, t.id).preis == Decimal("20.00")


def test_konfiguration_ungueltiger_wert(eingeloggt: TestClient, db: Session) -> None:
    c = eingeloggt
    r = c.post("/admin/konfiguration", data={"csrf_token": c.csrf, "storno_frist_stunden": "abc"})
    assert r.status_code == 200
    assert "storno_frist_stunden" in r.text
    assert db.query(Konfiguration).filter_by(schluessel="storno_frist_stunden").first() is None


def test_raster_gleicher_wochentag_wird_ersetzt(eingeloggt: TestClient, db: Session) -> None:
    c = eingeloggt
    c.post(
        "/admin/felder",
        data={"csrf_token": c.csrf, "name": "Feld R", "reihenfolge": "1"},
        follow_redirects=False,
    )
    feld = db.query(Feld).one()
    c.post(
        f"/admin/felder/{feld.id}/raster",
        data={
            "csrf_token": c.csrf,
            "wochentag": "",
            "modus": "dauer",
            "slot_minuten": "60",
            "fenster": "",
        },
        follow_redirects=False,
    )
    c.post(
        f"/admin/felder/{feld.id}/raster",
        data={
            "csrf_token": c.csrf,
            "wochentag": "",
            "modus": "dauer",
            "slot_minuten": "90",
            "fenster": "",
        },
        follow_redirects=False,
    )
    db.expire_all()
    raster = db.get(Feld, feld.id).raster
    assert len(raster) == 1
    assert raster[0].slot_minuten == 90


def test_lesende_rolle_bekommt_403(client: TestClient, db: Session) -> None:
    import pyotp
    from beachhub_core import auth

    _, secret = auth.lege_admin_an(db, name="leser", passwort="test-passwort-1234", rolle="lesend")
    db.commit()
    client.post(
        "/admin/login",
        data={"name": "leser", "passwort": "test-passwort-1234", "code": pyotp.TOTP(secret).now()},
    )
    assert client.get("/admin/felder").status_code == 200
    r = client.post("/admin/felder", data={"csrf_token": "x", "name": "F", "reihenfolge": "1"})
    assert r.status_code == 403


STAMMDATEN_SEITEN = [
    "/admin/felder",
    "/admin/betriebszeiten",
    "/admin/ausnahmetage",
    "/admin/kundengruppen",
    "/admin/tarife",
    "/admin/konfiguration",
]


def test_stammdaten_unternavigation_auf_jeder_seite(eingeloggt: TestClient) -> None:
    for seite in STAMMDATEN_SEITEN:
        text = eingeloggt.get(seite).text
        for ziel in STAMMDATEN_SEITEN:
            assert f'href="{ziel}"' in text, f"{seite} verlinkt {ziel} nicht"


def test_kundenseite_verweist_ohne_gruppen_auf_kundengruppen(eingeloggt: TestClient) -> None:
    text = eingeloggt.get("/admin/kunden").text
    assert 'href="/admin/kundengruppen"' in text
    assert "Kundengruppe" in text
