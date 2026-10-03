from datetime import date

from beachhub_core import clock
from beachhub_core.models import Kunde, utcnow
from beachhub_core.services import kunden, mitgliedschaft
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def _kunde(db: Session) -> Kunde:
    clock.set_override(db, date(2027, 11, 25))
    k = kunden.lege_an(db, name="Anna", email="anna@x.de")
    db.commit()
    return k


def test_freischalten_ueber_kundenseite(
    eingeloggt: TestClient, db: Session, mail_ausgang: list
) -> None:
    c = eingeloggt
    k = _kunde(db)
    seite = c.get(f"/admin/kunden/{k.id}")
    assert 'name="bis" value="2028-04-30"' in seite.text and "Freischalten" in seite.text
    r = c.post(
        f"/admin/kunden/{k.id}/mitgliedschaft",
        data={"csrf_token": c.csrf, "bis": "2028-04-30"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(k)
    assert k.mitglied_bis == date(2028, 4, 30)
    assert [m["betreff"] for m in mail_ausgang] == ["Ihre Mitgliedschaft ist freigeschaltet"]
    assert "30.04.2028" in mail_ausgang[0]["text"]
    assert "Mitglied bis <strong>30.04.2028</strong>" in c.get(f"/admin/kunden/{k.id}").text


def test_freischalten_mit_vergangenem_datum_zeigt_meldung(
    eingeloggt: TestClient, db: Session
) -> None:
    c = eingeloggt
    k = _kunde(db)
    r = c.post(
        f"/admin/kunden/{k.id}/mitgliedschaft", data={"csrf_token": c.csrf, "bis": "2027-01-01"}
    )
    assert r.status_code == 200 and "Vergangenheit" in r.text
    db.refresh(k)
    assert k.mitglied_bis is None


def test_beenden_ueber_kundenseite(eingeloggt: TestClient, db: Session, mail_ausgang: list) -> None:
    c = eingeloggt
    k = _kunde(db)
    mitgliedschaft.freischalten(db, k, bis=date(2028, 4, 30), admin_user_id=None)
    db.commit()
    r = c.post(
        f"/admin/kunden/{k.id}/mitgliedschaft/beenden",
        data={"csrf_token": c.csrf, "grund": "ausgetreten"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(k)
    assert k.mitglied_bis == date(2027, 11, 24)
    assert [m["betreff"] for m in mail_ausgang] == ["Ihre Mitgliedschaft wurde beendet"]


def test_antraege_seite_und_verwerfen(eingeloggt: TestClient, db: Session) -> None:
    c = eingeloggt
    k = _kunde(db)
    k.mitglied_antrag_am = utcnow()
    k.mitglied_antrag_hinweis = "Mitgliedsnummer 4711"
    db.commit()
    seite = c.get("/admin/kunden/antraege")
    assert "Anna" in seite.text and "4711" in seite.text
    assert "Antrag offen" in c.get(f"/admin/kunden/{k.id}").text
    r = c.post(
        f"/admin/kunden/{k.id}/mitgliedschaft/antrag-verwerfen",
        data={"csrf_token": c.csrf},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "Keine offenen Anträge" in c.get("/admin/kunden/antraege").text
