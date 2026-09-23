import uuid
from datetime import date, time

from beachhub_portal.models import Anfrage, Konto
from beachhub_shared.zeit import kombiniere
from fastapi.testclient import TestClient
from hilfen import KUNDE_ID, belegung, buchung, konto, speichere
from sqlalchemy import select
from sqlalchemy.orm import Session

MORGEN = date(2027, 11, 26)
UEBERMORGEN = date(2027, 11, 27)
FRUEHER = date(2027, 11, 20)


def _b(tag: date, stunde: int, **kw) -> dict:
    return buchung(kombiniere(tag, time(stunde)), kombiniere(tag, time(stunde + 1)), **kw)


def test_buchungen_ohne_kunde(angemeldet: TestClient, db: Session) -> None:
    db.get(Konto, angemeldet.konto_id).kunde_id = None
    db.commit()
    assert "wird gerade eingerichtet" in angemeldet.get("/buchungen").text


def test_liste_mit_pin_und_frueheren(angemeldet: TestClient, db: Session) -> None:
    speichere(
        db,
        f"konto:{KUNDE_ID}",
        konto(
            buchungen=[
                _b(MORGEN, 19, pin="654321"),
                _b(MORGEN, 21, status="reserviert"),
                _b(FRUEHER, 19),
                _b(MORGEN, 17, status="storniert", storno={"kostenfrei": True}),
            ]
        ),
    )
    seite = angemeldet.get("/buchungen").text
    kommend, frueher = seite.split("Frühere und stornierte")
    assert '<span class="pin">654321</span>' in kommend
    assert "Zahlung ausstehend" in kommend
    assert "20.11.2027" in frueher and "storniert (kostenfrei)" in frueher


def test_reservierung_mit_zahlungslink(angemeldet: TestClient, db: Session) -> None:
    frist = kombiniere(date(2027, 11, 25), time(10, 15))  # jetzt: 10:00 Uhr
    offen = _b(
        MORGEN, 19, status="reserviert", checkout_url="/test-zahlung/fake_x", reserviert_bis=frist
    )
    abgelaufen = _b(
        MORGEN,
        20,
        status="reserviert",
        checkout_url="/test-zahlung/fake_y",
        reserviert_bis=kombiniere(date(2027, 11, 25), time(9, 45)),
    )
    speichere(db, f"konto:{KUNDE_ID}", konto(buchungen=[offen, abgelaufen]))
    seite = angemeldet.get("/buchungen").text
    assert '<a class="knopf" href="/test-zahlung/fake_x">Jetzt bezahlen (bis 10:15)</a>' in seite
    assert "fake_y" not in seite
    assert "Zahlung ausstehend" in seite


def test_meldung(angemeldet: TestClient, db: Session) -> None:
    speichere(db, f"konto:{KUNDE_ID}", konto())
    assert "Deine Buchung ist bestätigt" in angemeldet.get("/buchungen?meldung=bestaetigt").text
    assert "gibtsnicht" not in angemeldet.get("/buchungen?meldung=gibtsnicht").text


def test_stornieren_kostenfrei_und_kostenpflichtig(angemeldet: TestClient, db: Session) -> None:
    speichere(db, "belegung", belegung())
    frueh = _b(UEBERMORGEN, 19)  # mehr als 24 h vorher
    spaet = _b(MORGEN, 9)  # 23 h vorher (jetzt: 25.11. 10:00)
    reserviert = _b(MORGEN, 10, status="reserviert")
    speichere(db, f"konto:{KUNDE_ID}", konto(buchungen=[frueh, spaet, reserviert]))
    assert (
        "<strong>kostenfrei</strong>" in angemeldet.get(f"/buchungen/{frueh['id']}/stornieren").text
    )
    assert "bleibt aber fällig" in angemeldet.get(f"/buchungen/{spaet['id']}/stornieren").text
    seite = angemeldet.get(f"/buchungen/{reserviert['id']}/stornieren").text
    assert "<strong>kostenfrei</strong>" in seite


def test_stornieren_legt_anfrage_an(angemeldet: TestClient, db: Session) -> None:
    b = _b(UEBERMORGEN, 19)
    speichere(db, f"konto:{KUNDE_ID}", konto(buchungen=[b]))
    r = angemeldet.post(
        f"/buchungen/{b['id']}/stornieren",
        data={"csrf_token": angemeldet.csrf},
        follow_redirects=False,
    )
    a = db.scalar(select(Anfrage))
    assert r.headers["location"] == f"/anfrage/{a.id}"
    assert a.typ == "buchung_stornieren" and a.nutzlast_json == {"buchung_id": b["id"]}


def test_stornieren_unbekannt_oder_vergangen(angemeldet: TestClient, db: Session) -> None:
    alt = _b(FRUEHER, 19)
    speichere(db, f"konto:{KUNDE_ID}", konto(buchungen=[alt]))
    assert angemeldet.get(f"/buchungen/{alt['id']}/stornieren").status_code == 404
    r = angemeldet.post(
        f"/buchungen/{uuid.uuid4()}/stornieren",
        data={"csrf_token": angemeldet.csrf},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/buchungen"
    assert db.scalar(select(Anfrage)) is None
