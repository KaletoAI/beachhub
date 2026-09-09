from pathlib import Path

from beachhub_core.config import settings
from fastapi.testclient import TestClient


def test_system_seite_und_uhr(eingeloggt: TestClient) -> None:
    c = eingeloggt
    assert c.get("/admin/system").status_code == 200
    r = c.post(
        "/admin/system/uhr",
        data={"csrf_token": c.csrf, "datum": "2027-12-24"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "24.12.2027" in c.get("/admin/system").text
    assert c.get("/admin/system/audit").status_code == 200
    assert c.get("/admin/system/stornos").status_code == 200


def test_lesestand_ohne_schluessel_zeigt_meldung(eingeloggt: TestClient) -> None:
    Path(settings.signatur_privatschluessel_pfad).unlink(missing_ok=True)
    r = eingeloggt.post(
        "/admin/system/lesestand",
        data={"csrf_token": eingeloggt.csrf, "alle": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    seite = eingeloggt.get(r.headers["location"])
    assert "Signaturschlüssel fehlt" in seite.text


def test_uhr_override_im_produktivbetrieb_verboten(eingeloggt: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    r = eingeloggt.post(
        "/admin/system/uhr",
        data={"csrf_token": eingeloggt.csrf, "datum": "2027-12-24"},
        follow_redirects=False,
    )
    assert r.status_code == 403
