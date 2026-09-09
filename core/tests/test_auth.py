import re
from datetime import date, time
from decimal import Decimal

import pyotp
from beachhub_core import auth, clock
from beachhub_core.models import AdminUser, Betriebszeit, Feld, FeldRaster, Kundengruppe, Tarif
from beachhub_core.services import buchungen, kunden, rechnungen
from beachhub_shared.zeit import kombiniere
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def test_login_braucht_passwort_und_totp(client: TestClient, db: Session) -> None:
    user, secret = auth.lege_admin_an(db, name="kai", passwort="geheim-123456")
    db.commit()
    r = client.post(
        "/admin/login", data={"name": "kai", "passwort": "falsch", "code": pyotp.TOTP(secret).now()}
    )
    assert r.status_code == 200 and "Anmeldung fehlgeschlagen" in r.text
    r = client.post(
        "/admin/login", data={"name": "kai", "passwort": "geheim-123456", "code": "000000"}
    )
    assert r.status_code == 200 and "Anmeldung fehlgeschlagen" in r.text
    r = client.post(
        "/admin/login",
        data={"name": "kai", "passwort": "geheim-123456", "code": pyotp.TOTP(secret).now()},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    assert "bh_session" in r.cookies
    r = client.get("/admin")
    assert r.status_code == 200 and "Übersicht" in r.text


def test_login_unbekannter_name_gleiche_antwort(client: TestClient) -> None:
    r = client.post(
        "/admin/login", data={"name": "unbekannt", "passwort": "irgendwas", "code": "123456"}
    )
    assert r.status_code == 200 and "Anmeldung fehlgeschlagen" in r.text


def test_ohne_session_umleitung(client: TestClient) -> None:
    r = client.get("/admin", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/admin/login")


def test_csrf_pflicht(eingeloggt: TestClient) -> None:
    r = eingeloggt.post("/admin/logout", data={})
    assert r.status_code == 403
    r = eingeloggt.post(
        "/admin/logout", data={"csrf_token": eingeloggt.csrf}, follow_redirects=False
    )
    assert r.status_code == 303
    assert eingeloggt.get("/admin", follow_redirects=False).status_code == 303


def test_rate_limit(client: TestClient, db: Session) -> None:
    auth.lege_admin_an(db, name="kai", passwort="geheim-123456")
    db.commit()
    for _ in range(10):
        client.post("/admin/login", data={"name": "kai", "passwort": "x", "code": "1"})
    r = client.post("/admin/login", data={"name": "kai", "passwort": "x", "code": "1"})
    assert r.status_code == 429


def test_lesende_rolle_darf_nicht_schreiben(client: TestClient, db: Session) -> None:
    user, secret = auth.lege_admin_an(db, name="leser", passwort="geheim-123456", rolle="lesend")
    db.commit()
    client.post(
        "/admin/login",
        data={"name": "leser", "passwort": "geheim-123456", "code": pyotp.TOTP(secret).now()},
    )
    assert db.query(AdminUser).filter_by(name="leser").one().rolle == "lesend"
    seite = client.get("/admin")
    assert seite.status_code == 200
    m = re.search(r'name="csrf_token" value="([^"]+)"', seite.text)
    assert m
    csrf = m.group(1)

    vorher = db.query(Feld).count()
    r = client.post("/admin/felder", data={"csrf_token": csrf, "name": "Neu", "reihenfolge": "1"})
    assert r.status_code == 403
    assert db.query(Feld).count() == vorher

    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    p = Kundengruppe(name="Privat")
    db.add_all([f, p, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    k = kunden.lege_an(
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
        kunde_id=k.id,
        beginn=kombiniere(date(2027, 12, 1), time(19)),
        ende=kombiniere(date(2027, 12, 1), time(20)),
    )
    rechnung = rechnungen.erzeuge_einzelrechnung(db, b)
    db.commit()

    r = client.get(f"/admin/rechnungen/{rechnung.id}/pdf", follow_redirects=False)
    assert r.status_code == 303
    db.refresh(rechnung)
    assert rechnung.pdf_pfad is None
