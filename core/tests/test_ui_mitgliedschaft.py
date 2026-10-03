from datetime import date, time
from decimal import Decimal

from beachhub_core import clock
from beachhub_core.models import Betriebszeit, Feld, FeldRaster, Kunde, Tarif, utcnow
from beachhub_core.services import buchungen, kunden, mitgliedschaft
from beachhub_shared.zeit import kombiniere
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


def _feld(db: Session) -> Feld:
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    db.add_all([f, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.commit()
    return f


def test_klaerungsliste(eingeloggt: TestClient, db: Session) -> None:
    c = eingeloggt
    f = _feld(db)
    k = _kunde(db)
    mitgliedschaft.freischalten(db, k, bis=date(2028, 4, 30), admin_user_id=None)
    b = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k.id,
        beginn=kombiniere(date(2027, 12, 10), time(19)),
        ende=kombiniere(date(2027, 12, 10), time(20)),
    )
    db.commit()
    seite = c.post(
        f"/admin/kunden/{k.id}/mitgliedschaft/beenden",
        data={"csrf_token": c.csrf, "grund": "ausgetreten"},
    )
    assert "1 künftige Buchung zum Mitgliedspreis" in seite.text
    liste = c.get("/admin/system/klaerung")
    assert "Anna" in liste.text and f"/admin/belegung/buchung/{b.id}" in liste.text
    r = c.post(
        f"/admin/system/klaerung/{b.id}", data={"csrf_token": c.csrf}, follow_redirects=False
    )
    assert r.status_code == 303
    assert "Nichts zu klären" in c.get("/admin/system/klaerung").text


def test_abgleich_seite_csv_und_verlaengern(
    eingeloggt: TestClient, db: Session, mail_ausgang: list
) -> None:
    c = eingeloggt
    clock.set_override(db, date(2027, 8, 31))
    k = kunden.lege_an(db, name="Anna", email="anna@x.de")
    k.mitglied_bis = date(2027, 4, 30)
    k.mitglied_antrag_hinweis = "Nr. 4711"
    db.commit()
    seite = c.get("/admin/kunden/abgleich")
    assert "Anna" in seite.text and "30.04.2027" in seite.text
    assert "bis 30.04.2028 verlängern" in seite.text
    csv = c.get("/admin/kunden/abgleich.csv")
    assert csv.headers["content-type"].startswith("text/csv")
    assert "Anna;anna@x.de;2027-04-30;Nr. 4711" in csv.text
    r = c.post(
        "/admin/kunden/abgleich",
        data={"csrf_token": c.csrf, "aktion": "verlaengern", "kunde_ids": [str(k.id)]},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(k)
    assert k.mitglied_bis == date(2028, 4, 30)
    assert [m["betreff"] for m in mail_ausgang] == ["Ihre Mitgliedschaft ist freigeschaltet"]


def test_abgleich_ohne_auswahl_meldet_fehler(eingeloggt: TestClient) -> None:
    c = eingeloggt
    seite = c.post("/admin/kunden/abgleich", data={"csrf_token": c.csrf, "aktion": "beenden"})
    assert "mindestens einen Kunden" in seite.text
