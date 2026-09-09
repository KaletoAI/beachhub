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
