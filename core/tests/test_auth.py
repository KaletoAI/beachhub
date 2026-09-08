import pyotp
from beachhub_core import auth
from beachhub_core.models import AdminUser
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
    r = client.get("/admin")
    assert r.status_code == 200
