from datetime import date

import pytest
from beachhub_core.models import Gutschein
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def test_freicodes_anlegen_filtern_exportieren(eingeloggt: TestClient, db: Session) -> None:
    c = eingeloggt
    assert c.get("/admin/gutscheine/freicodes").status_code == 200
    r = c.post(
        "/admin/gutscheine/freicodes",
        data={"csrf_token": c.csrf, "anzahl": "2", "serie": "Aktion", "notiz": "=Test"},
    )
    assert r.status_code == 200 and "ausgestellt" in r.text
    g = db.query(Gutschein).first()
    assert g is not None
    assert len(db.query(Gutschein).all()) == 2
    assert "Aktion" in c.get("/admin/gutscheine?serie=Aktion").text
    assert "Aktion" not in c.get("/admin/gutscheine?serie=anders").text
    csv = c.get("/admin/gutscheine/serie.csv?serie=Aktion")
    assert csv.status_code == 200 and "'=Test" in csv.text
    assert c.get(f"/admin/gutscheine/{g.id}").status_code == 200
    c.post(f"/admin/gutscheine/{g.id}/sperren", data={"csrf_token": c.csrf})
    db.refresh(g)
    assert g.status == "gesperrt"
    c.post(
        f"/admin/gutscheine/{g.id}/gueltigkeit",
        data={"csrf_token": c.csrf, "gueltig_bis": "2030-12-31", "reaktivieren": "1"},
    )
    db.refresh(g)
    assert g.status == "aktiv" and g.gueltig_bis == date(2030, 12, 31)


def test_freicodes_fehler_und_csrf(eingeloggt: TestClient, db: Session) -> None:
    c = eingeloggt
    assert c.post("/admin/gutscheine/freicodes", data={"anzahl": "1"}).status_code == 403
    for extra in ({"anzahl": "0"}, {"gueltig_bis": "kaputt"}, {"max_einloesungen": "0"}):
        r = c.post(
            "/admin/gutscheine/freicodes", data={"csrf_token": c.csrf, "anzahl": "1", **extra}
        )
        assert r.status_code == 200 and 'class="flash fehler"' in r.text
    assert db.query(Gutschein).count() == 0


def test_lesende_rolle_kann_nur_ansehen(eingeloggt: TestClient, db: Session, admin) -> None:
    from beachhub_core.services import gutscheine

    g = gutscheine.stelle_freicodes_aus(db, anzahl=1, gueltig_bis=None, admin_user_id=None)[0]
    user, _ = admin
    user.rolle = "lesend"
    db.commit()
    c = eingeloggt
    for pfad in ("/admin/gutscheine", "/admin/gutscheine/freicodes", f"/admin/gutscheine/{g.id}"):
        r = c.get(pfad)
        assert r.status_code == 200
        assert 'action="/admin/gutscheine/' not in r.text
    for pfad in (
        "/admin/gutscheine/freicodes",
        f"/admin/gutscheine/{g.id}/sperren",
        f"/admin/gutscheine/{g.id}/gueltigkeit",
    ):
        assert c.post(pfad, data={"csrf_token": c.csrf, "anzahl": "1"}).status_code == 403


def test_admin_besitzwechsel_retry_rollt_zurueck(
    eingeloggt: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from beachhub_core.services import gutschein_sperren, gutscheine

    g = gutscheine.stelle_freicodes_aus(db, anzahl=1, gueltig_bis=None, admin_user_id=None)[0]
    db.commit()

    def retry(*args, **kwargs):
        raise gutschein_sperren.GutscheinVorbereitungWiederholen("Besitzwechsel")

    monkeypatch.setattr(gutschein_sperren, "bereite_vorgang_vor", retry)
    r = eingeloggt.post(f"/admin/gutscheine/{g.id}/sperren", data={"csrf_token": eingeloggt.csrf})
    assert r.status_code == 200 and "erneut" in r.text
    db.refresh(g)
    assert g.status == "aktiv"


def test_offener_kauf_veroeffentlicht_keinen_code(
    eingeloggt: TestClient, db: Session, gwelt
) -> None:
    from beachhub_core.services import gutschein_codes
    from hilfen_gutschein import kaufgutschein

    g = kaufgutschein(
        db, gwelt, code="ABCDEFGHJKLMN", status="offen", gueltig_bis=None, kaufdatum=None
    )
    db.commit()
    geheim = gutschein_codes.anzeige(g.code)
    assert geheim not in eingeloggt.get("/admin/gutscheine").text
    assert geheim not in eingeloggt.get(f"/admin/gutscheine/{g.id}").text
    assert g.code not in eingeloggt.get("/admin/gutscheine/serie.csv").text
    eingeloggt.post(f"/admin/gutscheine/{g.id}/sperren", data={"csrf_token": eingeloggt.csrf})
    db.refresh(g)
    assert g.status == "offen"


def test_rueckgabe_ui_verhindert_reaktivierung(eingeloggt: TestClient, db: Session, gwelt) -> None:
    from beachhub_core import clock
    from hilfen_gutschein import kaufgutschein

    g = kaufgutschein(
        db, gwelt, code="ABCDEFGHJKLMN", status="gesperrt", zurueckgegeben_am=clock.now(db)
    )
    db.commit()
    seite = eingeloggt.get(f"/admin/gutscheine/{g.id}")
    assert "dauerhaft" in seite.text.lower() and 'name="reaktivieren"' not in seite.text
    r = eingeloggt.post(
        f"/admin/gutscheine/{g.id}/gueltigkeit",
        data={"csrf_token": eingeloggt.csrf, "gueltig_bis": "2035-12-31", "reaktivieren": "1"},
    )
    assert "zurückgegeben" in r.text
    db.refresh(g)
    assert g.status == "gesperrt" and g.gueltig_bis != date(2035, 12, 31)
