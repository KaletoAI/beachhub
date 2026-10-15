import pytest
from beachhub_core import mail, postausgang
from beachhub_core.config import settings
from beachhub_core.models import Audit
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

PDF = b"%PDF-1.4 Demo"


@pytest.fixture
def ablage(monkeypatch: pytest.MonkeyPatch, tmp_path) -> list:
    """Echter Mailpfad statt TEST_AUSGANG; SMTP-Versand wird nur mitgeschrieben."""
    gesendet: list = []

    async def senden(m) -> None:
        gesendet.append(m)

    monkeypatch.setattr(mail, "TEST_AUSGANG", None)
    monkeypatch.setattr(mail, "_senden", senden)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.org")
    return gesendet


def test_versand_aus_haelt_mail_im_postausgang_zurueck(ablage: list) -> None:
    mail.sende("kunde@example.org", "Rechnung 2027-00001", "Hallo,\n\nanbei.", [("r.pdf", PDF)])
    assert ablage == []
    [e] = postausgang.liste()
    assert (e.an, e.betreff, e.status, e.anhaenge) == (
        "kunde@example.org",
        "Rechnung 2027-00001",
        postausgang.ZURUECKGEHALTEN,
        ["r.pdf"],
    )
    eintrag, m = postausgang.lade(e.id)  # type: ignore[misc]
    assert postausgang.text(m).strip() == "Hallo,\n\nanbei."
    assert postausgang.anhang(m, 0) == ("r.pdf", "application/pdf", PDF)


def test_versand_an_verschickt_und_protokolliert_status(ablage: list, monkeypatch) -> None:
    postausgang.setze_versand(True)
    mail.sende("kunde@example.org", "Storno", "Text")
    assert len(ablage) == 1 and postausgang.liste()[0].status == postausgang.GESENDET

    async def kaputt(m) -> None:
        raise OSError("SMTP weg")

    monkeypatch.setattr(mail, "_senden", kaputt)
    mail.sende("kunde@example.org", "Zweite", "Text")
    assert postausgang.liste()[0].status == postausgang.FEHLER
    monkeypatch.setattr(settings, "smtp_host", "")
    mail.sende("kunde@example.org", "Dritte", "Text")
    assert postausgang.liste()[0].status == postausgang.KEIN_SMTP


def test_produktion_ohne_postausgang(ablage: list, monkeypatch, eingeloggt: TestClient) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    mail.sende("kunde@example.org", "Rechnung", "Text")
    assert len(ablage) == 1 and postausgang.liste() == []
    assert eingeloggt.get("/admin/system/postausgang").status_code == 404


def test_postausgang_oberflaeche(eingeloggt: TestClient, db: Session, ablage: list) -> None:
    c = eingeloggt
    mail.sende(
        "kunde@example.org",
        "Buchung bestätigt: Feld 1",
        "Hallo <Anna>,\n\nPIN: 1234\n\nViele Grüße",
        [("2027-00001.pdf", PDF)],
    )
    [e] = postausgang.liste()
    seite = c.get("/admin/system/postausgang")
    assert seite.status_code == 200
    assert "Buchung bestätigt: Feld 1" in seite.text and "nur im Postausgang" in seite.text
    assert 'href="/admin/system/postausgang"' in seite.text  # Menüpunkt im Testsystem
    detail = c.get(f"/admin/system/postausgang/{e.id}").text
    # Text unverändert mit Zeilenumbrüchen, HTML darin maskiert.
    assert 'class="mail-text">Hallo &lt;Anna&gt;,\n\nPIN: 1234\n\nViele Grüße' in detail
    assert settings.email_from in detail and "2027-00001.pdf" in detail
    pdf = c.get(f"/admin/system/postausgang/{e.id}/anhang/0")
    assert pdf.content == PDF and pdf.headers["content-type"] == "application/pdf"
    assert pdf.headers["content-disposition"].startswith("inline")
    eml = c.get(f"/admin/system/postausgang/{e.id}/mail.eml")
    assert eml.headers["content-type"].startswith("message/rfc822")
    assert b"Subject: Buchung =?utf-8?q?best=C3=A4tigt" in eml.content
    for falsch in ("../../etc", "20270101T000000000000-zzzzzzzz"):
        assert c.get(f"/admin/system/postausgang/{falsch}").status_code == 404
    assert c.get(f"/admin/system/postausgang/{e.id}/anhang/5").status_code == 404

    r = c.post(
        "/admin/system/postausgang/versand",
        data={"csrf_token": c.csrf, "an": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and postausgang.versand_an()
    eintrag = db.scalars(select(Audit).where(Audit.objekt_typ == "postausgang")).one()
    assert eintrag.nachher_json == {"versand": True}
    mail.sende("kunde@example.org", "Jetzt raus", "Text")
    assert len(ablage) == 1
    r = c.post(
        "/admin/system/postausgang/leeren", data={"csrf_token": c.csrf}, follow_redirects=False
    )
    assert r.status_code == 303 and postausgang.liste() == []
