import uuid
from datetime import date, time

import pytest
from beachhub_portal.models import Anfrage, Konto
from beachhub_portal.services import anfragen
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
                _b(
                    MORGEN,
                    21,
                    status="reserviert",
                    checkout_url="/test-zahlung/fake_z",
                    reserviert_bis=kombiniere(date(2027, 11, 25), time(9, 45)),  # abgelaufen
                ),
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


def test_zahlung_unvollstaendig_ohne_zahlungslink(angemeldet: TestClient, db: Session) -> None:
    # Unterzahlung: Die Zahlung ist bezahlt, die Buchung bleibt reserviert, das Hauptsystem
    # schickt keinen Zahlungslink mehr (keine offene Zahlung).
    unterzahlt = _b(MORGEN, 19, status="reserviert")
    speichere(db, f"konto:{KUNDE_ID}", konto(buchungen=[unterzahlt]))
    seite = angemeldet.get("/buchungen").text
    assert "Zahlung unvollständig – der Betreiber meldet sich" in seite
    assert "Jetzt bezahlen" not in seite and "Zahlung ausstehend" not in seite


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
    fruehe_seite = angemeldet.get(f"/buchungen/{frueh['id']}/stornieren").text
    assert "<strong>kostenfrei</strong>" in fruehe_seite
    assert "Der Betrag wird deinem Guthaben gutgeschrieben" in fruehe_seite
    assert "bleibt aber fällig" in angemeldet.get(f"/buchungen/{spaet['id']}/stornieren").text
    seite = angemeldet.get(f"/buchungen/{reserviert['id']}/stornieren").text
    assert "<strong>kostenfrei</strong>" in seite
    assert "Der Betrag wird deinem Guthaben gutgeschrieben" not in seite


@pytest.mark.parametrize("rest", [0, 2, None])
def test_abo_stornoseite_zeigt_kontingent_und_bedingte_korrektur(
    angemeldet: TestClient, db: Session, rest: int | None
) -> None:
    b = {**_b(UEBERMORGEN, 19), "abo": True, "freie_absagen_rest": rest}
    speichere(db, "belegung", belegung())
    speichere(db, f"konto:{KUNDE_ID}", konto(buchungen=[b]))
    seite = angemeldet.get(f"/buchungen/{b['id']}/stornieren").text
    assert "Verbindlich stornieren" in seite
    assert "Der Betrag wird deinem Guthaben gutgeschrieben" not in seite
    if rest == 0:
        assert "keine kostenfreien Absagen" in seite
        assert "bleibt berechnet" in seite
        assert "Stornofrist von 24 Stunden ist abgelaufen" not in seite
    else:
        assert "innerhalb der Stornofrist" in seite
        assert "offene Forderung" in seite and "bezahlte Anteil" in seite
        assert "entscheidet das Buchungssystem" in seite
        if rest is not None:
            assert "Kostenfreie Absagen übrig: 2" in seite


@pytest.mark.parametrize("kostenfrei", [False, True])
def test_abo_stornoerfolg_nennt_kosten_ohne_falschen_fristgrund(
    angemeldet: TestClient, db: Session, kostenfrei: bool
) -> None:
    a = anfragen.stelle(
        db,
        typ="buchung_stornieren",
        konto_id=angemeldet.konto_id,
        nutzlast={"buchung_id": str(uuid.uuid4())},
    )
    a.status = Anfrage.BEANTWORTET
    a.antwort_json = {
        "status": "ok",
        "kostenfrei": kostenfrei,
        "freie_absage": kostenfrei,
        "verbleibende_freie_absagen": 0,
    }
    db.commit()
    stand = angemeldet.get(f"/anfrage/{a.id}/stand").json()
    assert stand["zustand"] == "fertig"
    assert "freie Absagen" in stand["text"]
    seite = angemeldet.get(stand["ziel"]).text
    if kostenfrei:
        assert "offene Forderung" in seite and "bezahlte Anteil" in seite
    else:
        assert "bleibt berechnet" in seite
        assert "Da die Stornofrist abgelaufen war" not in seite


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


def test_nicht_stornierbar_ohne_link_und_abgewiesen(angemeldet: TestClient, db: Session) -> None:
    # Betreiber-Einzelbuchungen bleiben entsprechend dem Lesestand nicht stornierbar.
    dauer = _b(UEBERMORGEN, 19, stornierbar=False)
    eigen = _b(UEBERMORGEN, 21)
    speichere(db, "belegung", belegung())
    speichere(db, f"konto:{KUNDE_ID}", konto(buchungen=[dauer, eigen]))
    seite = angemeldet.get("/buchungen").text
    assert f"/buchungen/{eigen['id']}/stornieren" in seite
    assert f"/buchungen/{dauer['id']}/stornieren" not in seite
    assert angemeldet.get(f"/buchungen/{dauer['id']}/stornieren").status_code == 404
    r = angemeldet.post(
        f"/buchungen/{dauer['id']}/stornieren",
        data={"csrf_token": angemeldet.csrf},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/buchungen"
    assert db.scalar(select(Anfrage).where(Anfrage.typ == "buchung_stornieren")) is None
