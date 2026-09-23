import threading
import uuid
from datetime import date, time, timedelta

import pytest
from beachhub_portal.models import Anfrage, KanalKontakt
from beachhub_portal.services import anfragen
from beachhub_shared import kanal
from beachhub_shared.zeit import kombiniere
from fastapi.testclient import TestClient
from hilfen import FELD_ID, JETZT, KUNDE_ID, belegung, buchung, konto, speichere, tarife
from sqlalchemy import select
from sqlalchemy.orm import Session

TAG = date(2027, 11, 26)
B17, B18 = kombiniere(TAG, time(17)), kombiniere(TAG, time(18))


@pytest.fixture
def welt(db: Session) -> None:
    belegt = {
        "beginn": kombiniere(TAG, time(19)).isoformat(),
        "ende": kombiniere(TAG, time(21)).isoformat(),
    }
    speichere(db, "belegung", belegung(belegt={FELD_ID: [belegt]}))
    speichere(db, "tarife", tarife())
    speichere(db, f"konto:{KUNDE_ID}", konto())


def _anfrage(db: Session, konto_id: uuid.UUID) -> Anfrage:
    return anfragen.stelle(
        db,
        typ="buchung_anfragen",
        konto_id=konto_id,
        nutzlast={"feld_id": FELD_ID, "beginn": B17.isoformat(), "ende": B18.isoformat()},
    )


def _antworte(db: Session, a: Anfrage, **antwort) -> None:
    anfragen.beantworte(db, a.id, kanal.Antwort(**antwort), JETZT)
    db.commit()


def test_buchen_seite_bietet_folgeslots(angemeldet: TestClient, welt) -> None:
    seite = angemeldet.get("/buchen", params={"feld": FELD_ID, "beginn": B17.isoformat()}).text
    assert "bis 18:00 Uhr – 30,00 €" in seite
    assert "bis 19:00 Uhr – 60,00 €" in seite
    assert "bis 20:00" not in seite
    assert "24 Stunden" in seite


def test_buchen_seite_anonym(client: TestClient, welt, uhr_steht) -> None:
    seite = client.get("/buchen", params={"feld": FELD_ID, "beginn": B17.isoformat()}).text
    assert "Melde dich an" in seite and "Verbindlich buchen" not in seite


def test_belegter_oder_kaputter_termin(angemeldet: TestClient, welt) -> None:
    belegt = kombiniere(TAG, time(19)).isoformat()
    r = angemeldet.get("/buchen", params={"feld": FELD_ID, "beginn": belegt})
    assert r.status_code == 409 and "nicht mehr frei" in r.text
    assert (
        angemeldet.get("/buchen", params={"feld": FELD_ID, "beginn": "quatsch"}).status_code == 409
    )
    ohne_zone = "2027-11-26T17:00:00"
    assert (
        angemeldet.get("/buchen", params={"feld": FELD_ID, "beginn": ohne_zone}).status_code == 409
    )


def test_buchen_seite_zeitpunkt_ueberlauf_kein_500(angemeldet: TestClient, welt) -> None:
    # Umrechnung auf die lokale Zeitzone würde für Jahre nahe der Grenze des datetime-Bereichs
    # mit OverflowError scheitern (z. B. Jahr 9999 mit negativem Offset landet in UTC im Jahr
    # 10000) – muss wie ein sonstiges kaputtes Datum als 409 behandelt werden, nicht als 500.
    r = angemeldet.get("/buchen", params={"feld": FELD_ID, "beginn": "9999-12-31T23:00:00-05:00"})
    assert r.status_code == 409 and "nicht mehr frei" in r.text


def test_buchen_legt_anfrage_an(angemeldet: TestClient, welt, db: Session) -> None:
    ende = kombiniere(TAG, time(19))
    r = angemeldet.post(
        "/buchen",
        data={
            "feld": FELD_ID,
            "beginn": B17.isoformat(),
            "ende": ende.isoformat(),
            "csrf_token": angemeldet.csrf,
        },
        follow_redirects=False,
    )
    a = db.scalar(select(Anfrage))
    assert r.headers["location"] == f"/anfrage/{a.id}?weiter=1"
    assert a.typ == "buchung_anfragen" and a.konto_id == angemeldet.konto_id
    # kanal.BuchungAnfragen.model_dump(mode="json") serialisiert AwareDatetime mit "Z" (pydantic
    # 2.10.3), nicht wie datetime.isoformat() mit "+00:00" – derselbe Validierungs-/Dump-Schritt
    # wie in anfragen.stelle(), daher hier ebenso erzeugt statt roh mit isoformat() verglichen.
    erwartet = kanal.BuchungAnfragen(feld_id=FELD_ID, beginn=B17, ende=ende).model_dump(mode="json")
    assert a.nutzlast_json == erwartet


def test_buchen_doppelklick_erzeugt_nur_eine_anfrage(
    angemeldet: TestClient, welt, db: Session
) -> None:
    daten = {
        "feld": FELD_ID,
        "beginn": B17.isoformat(),
        "ende": B18.isoformat(),
        "csrf_token": angemeldet.csrf,
    }
    r1 = angemeldet.post("/buchen", data=daten, follow_redirects=False)
    r2 = angemeldet.post("/buchen", data=daten, follow_redirects=False)
    assert r1.headers["location"] == r2.headers["location"]
    assert len(db.scalars(select(Anfrage)).all()) == 1


def test_buchen_doppelklick_gleichzeitig_erzeugt_nur_eine_anfrage(
    angemeldet: TestClient, welt, db: Session
) -> None:
    # Echte Gleichzeitigkeit statt nacheinander: zwei Threads, jeder mit eigener DB-Session
    # (get_db erzeugt je Anfrage eine neue SessionLocal) – prüft, dass die Konto-Sperre zwei
    # tatsächlich parallele POSTs serialisiert statt beide anlegen zu lassen.
    daten = {
        "feld": FELD_ID,
        "beginn": B17.isoformat(),
        "ende": B18.isoformat(),
        "csrf_token": angemeldet.csrf,
    }
    ergebnisse: list = []

    def posten() -> None:
        ergebnisse.append(angemeldet.post("/buchen", data=daten, follow_redirects=False))

    t1 = threading.Thread(target=posten)
    t2 = threading.Thread(target=posten)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(ergebnisse) == 2
    orte = {r.headers["location"] for r in ergebnisse}
    assert len(orte) == 1
    assert len(db.scalars(select(Anfrage)).all()) == 1


def test_buchen_kurz_nach_beantwortung_dieselbe_anfrage(
    angemeldet: TestClient, welt, db: Session, uhr_steht
) -> None:
    daten = {
        "feld": FELD_ID,
        "beginn": B17.isoformat(),
        "ende": B18.isoformat(),
        "csrf_token": angemeldet.csrf,
    }
    r1 = angemeldet.post("/buchen", data=daten, follow_redirects=False)
    a = db.scalar(select(Anfrage))
    _antworte(db, a, status="abgelehnt", grund="belegt")
    uhr_steht.weiter(seconds=90)  # unter dem Zwei-Minuten-Fenster
    r2 = angemeldet.post("/buchen", data=daten, follow_redirects=False)
    assert r1.headers["location"] == r2.headers["location"]
    assert len(db.scalars(select(Anfrage)).all()) == 1


def test_buchen_nach_beantwortung_und_wartezeit_neue_anfrage(
    angemeldet: TestClient, welt, db: Session, uhr_steht
) -> None:
    daten = {
        "feld": FELD_ID,
        "beginn": B17.isoformat(),
        "ende": B18.isoformat(),
        "csrf_token": angemeldet.csrf,
    }
    r1 = angemeldet.post("/buchen", data=daten, follow_redirects=False)
    a = db.scalar(select(Anfrage))
    _antworte(db, a, status="abgelehnt", grund="belegt")
    uhr_steht.weiter(seconds=121)
    r2 = angemeldet.post("/buchen", data=daten, follow_redirects=False)
    assert r1.headers["location"] != r2.headers["location"]
    assert len(db.scalars(select(Anfrage)).all()) == 2


def test_buchen_ueber_belegung_hinaus_abgewiesen(angemeldet: TestClient, welt, db: Session) -> None:
    r = angemeldet.post(
        "/buchen",
        data={
            "feld": FELD_ID,
            "beginn": B17.isoformat(),
            "ende": kombiniere(TAG, time(20)).isoformat(),
            "csrf_token": angemeldet.csrf,
        },
        follow_redirects=False,
    )
    assert r.headers["location"] == "/"
    assert db.scalar(select(Anfrage)) is None


def test_buchen_post_ueberlauf_kein_500(angemeldet: TestClient, welt, db: Session) -> None:
    r = angemeldet.post(
        "/buchen",
        data={
            "feld": FELD_ID,
            "beginn": "0001-01-01T00:30:00+05:00",
            "ende": B18.isoformat(),
            "csrf_token": angemeldet.csrf,
        },
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert db.scalar(select(Anfrage)) is None


def test_buchen_post_fehlerhafte_eingaben_erzeugen_keine_anfrage(
    angemeldet: TestClient, welt, db: Session
) -> None:
    faelle = [
        {"feld": "unbekannt", "beginn": B17.isoformat(), "ende": B18.isoformat()},
        {"feld": FELD_ID, "beginn": B18.isoformat(), "ende": B17.isoformat()},  # ende vor beginn
        {  # nicht am Raster ausgerichteter beginn
            "feld": FELD_ID,
            "beginn": kombiniere(TAG, time(17, 15)).isoformat(),
            "ende": B18.isoformat(),
        },
        {  # ende über die (durch die Belegung um 19 Uhr endende) Folge hinaus
            "feld": FELD_ID,
            "beginn": B17.isoformat(),
            "ende": kombiniere(TAG, time(22)).isoformat(),
        },
    ]
    for daten in faelle:
        r = angemeldet.post(
            "/buchen", data={**daten, "csrf_token": angemeldet.csrf}, follow_redirects=False
        )
        assert r.status_code == 303 and r.headers["location"] == "/"
    assert db.scalar(select(Anfrage)) is None


def test_buchen_ohne_csrf_verboten(angemeldet: TestClient, welt) -> None:
    daten = {"feld": FELD_ID, "beginn": B17.isoformat(), "ende": B18.isoformat()}
    assert angemeldet.post("/buchen", data=daten).status_code == 403


def test_stand_wartet(angemeldet: TestClient, welt, db: Session) -> None:
    a = _anfrage(db, angemeldet.konto_id)
    r = angemeldet.get(f"/anfrage/{a.id}")
    assert r.status_code == 200 and "wird bearbeitet" in r.text and "/static/warten.js" in r.text
    stand = angemeldet.get(f"/anfrage/{a.id}/stand").json()
    assert stand["zustand"] == "wartet" and stand["ziel"] is None


def test_stand_zeigt_hinweis_nach_wartezeit(
    angemeldet: TestClient, welt, db: Session, uhr_steht
) -> None:
    a = _anfrage(db, angemeldet.konto_id)
    uhr_steht.weiter(seconds=121)
    assert "nicht erreichbar" in angemeldet.get(f"/anfrage/{a.id}/stand").json()["text"]


def test_stand_hinweis_wenn_hauptsystem_lange_still(
    angemeldet: TestClient, welt, db: Session
) -> None:
    db.merge(KanalKontakt(id=1, letzter_abruf=JETZT - timedelta(minutes=5)))
    db.commit()
    a = _anfrage(db, angemeldet.konto_id)
    assert "nicht erreichbar" in angemeldet.get(f"/anfrage/{a.id}/stand").json()["text"]


def test_reserviert_leitet_einmal_zur_zahlung(angemeldet: TestClient, welt, db: Session) -> None:
    a = _anfrage(db, angemeldet.konto_id)
    _antworte(
        db,
        a,
        status="reserviert",
        buchung_id=uuid.uuid4(),
        checkout_url="/test-zahlung/fake_x?betrag=30.00",
    )
    r = angemeldet.get(f"/anfrage/{a.id}?weiter=1", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/test-zahlung/fake_x?betrag=30.00"
    # Zurück von der Zahlung (ohne weiter): nicht erneut weiterleiten, sondern warten
    r = angemeldet.get(f"/anfrage/{a.id}")
    assert r.status_code == 200 and "Zur Zahlung" in r.text


def test_checkout_url_https_wird_akzeptiert(angemeldet: TestClient, welt, db: Session) -> None:
    a = _anfrage(db, angemeldet.konto_id)
    ziel = "https://zahlungsanbieter.example/checkout/abc123"
    _antworte(db, a, status="reserviert", buchung_id=uuid.uuid4(), checkout_url=ziel)
    r = angemeldet.get(f"/anfrage/{a.id}?weiter=1", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == ziel


def test_checkout_url_offene_weiterleitung_wird_abgelehnt(
    angemeldet: TestClient, welt, db: Session
) -> None:
    # Protokollrelative URL ("//host/pfad") wird vom Browser als absolutes Ziel auf einem
    # fremden Host interpretiert – auch wenn das Hauptsystem an sich vertrauenswürdig ist, darf
    # eine kaputte/manipulierte checkout_url nicht kommentarlos weiterleiten oder angezeigt
    # werden.
    a = _anfrage(db, angemeldet.konto_id)
    _antworte(
        db,
        a,
        status="reserviert",
        buchung_id=uuid.uuid4(),
        checkout_url="//boese.example/phish",
    )
    r = angemeldet.get(f"/anfrage/{a.id}?weiter=1", follow_redirects=False)
    assert r.status_code == 200 and "Fehler aufgetreten" in r.text
    stand = angemeldet.get(f"/anfrage/{a.id}/stand").json()
    assert stand["zustand"] == "fehler" and stand["ziel"] is None


@pytest.mark.parametrize(
    ("url", "erwartet"),
    [
        ("//evil", False),  # protokollrelativ – Browser lesen das als fremden Host
        ("/\\evil", False),  # Backslash direkt nach "/" – Browser lesen \ wie / bei http(s)
        ("/\\/evil", False),  # dito, zweites Zeichen ist der Backslash
        ("https:evil", False),  # kein "//", also kein Host (urlparse: netloc == "")
        ("HTTPS://x", True),  # Schema ist laut RFC 3986 case-insensitiv, wie im Browser
        (" /x", False),  # führender Leerraum
        ("/x\ny", False),  # eingebettetes Steuerzeichen (Zeilenumbruch)
        ("javascript:alert(1)", False),  # kein https, kein relativer Pfad
        ("/test-zahlung/abc?betrag=1", True),  # gültiger relativer Pfad (Fake-Zahlung)
        ("https://zahlung.example/x", True),  # gültige absolute https-URL
    ],
)
def test_gueltige_checkout_url(url: str, erwartet: bool) -> None:
    assert anfragen._gueltige_checkout_url(url) is erwartet


def test_nach_zahlung_bestaetigt(angemeldet: TestClient, welt, db: Session) -> None:
    a = _anfrage(db, angemeldet.konto_id)
    bid = uuid.uuid4()
    _antworte(db, a, status="reserviert", buchung_id=bid, checkout_url="/x")
    speichere(db, f"konto:{KUNDE_ID}", konto(buchungen=[buchung(B17, B18, id=str(bid))]), version=2)
    r = angemeldet.get(f"/anfrage/{a.id}", follow_redirects=False)
    assert r.headers["location"] == "/buchungen?meldung=bestaetigt"


def test_verfallene_reservierung(angemeldet: TestClient, welt, db: Session) -> None:
    a = _anfrage(db, angemeldet.konto_id)
    bid = uuid.uuid4()
    _antworte(db, a, status="reserviert", buchung_id=bid, checkout_url="/x")
    verfallen = buchung(B17, B18, status="verfallen", id=str(bid))
    speichere(db, f"konto:{KUNDE_ID}", konto(buchungen=[verfallen]), version=2)
    assert "Zahlungsfrist ist abgelaufen" in angemeldet.get(f"/anfrage/{a.id}").text


def test_bestaetigt_abgelehnt_fehler(angemeldet: TestClient, welt, db: Session) -> None:
    a = _anfrage(db, angemeldet.konto_id)
    _antworte(db, a, status="bestaetigt", buchung_id=uuid.uuid4())
    r = angemeldet.get(f"/anfrage/{a.id}", follow_redirects=False)
    assert r.headers["location"] == "/buchungen?meldung=bestaetigt"
    b = _anfrage(db, angemeldet.konto_id)
    _antworte(db, b, status="abgelehnt", grund="belegt")
    assert "inzwischen vergeben" in angemeldet.get(f"/anfrage/{b.id}").text
    c = _anfrage(db, angemeldet.konto_id)
    _antworte(db, c, status="fehler")
    assert "Fehler aufgetreten" in angemeldet.get(f"/anfrage/{c.id}").text


def test_ablehnungsgrund_ungueltig_text(angemeldet: TestClient, welt, db: Session) -> None:
    a = _anfrage(db, angemeldet.konto_id)
    _antworte(db, a, status="abgelehnt", grund="ungueltig")
    assert "nicht verarbeitet werden" in angemeldet.get(f"/anfrage/{a.id}").text


def test_fremde_anfrage_404(angemeldet: TestClient, welt, db: Session) -> None:
    a = _anfrage(db, uuid.uuid4())
    assert angemeldet.get(f"/anfrage/{a.id}").status_code == 404
    assert angemeldet.get(f"/anfrage/{a.id}/stand").status_code == 404


def test_warten_js_wird_ausgeliefert(client: TestClient) -> None:
    assert client.get("/static/warten.js").status_code == 200
