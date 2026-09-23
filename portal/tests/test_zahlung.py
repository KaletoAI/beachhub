import json
import uuid
from collections.abc import Iterator

import pytest
from beachhub_portal.config import settings
from beachhub_portal.models import Anfrage, WebhookEingang
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session


def test_briefkasten_speichert_ohne_pruefung(client: TestClient, db: Session) -> None:
    r = client.post(
        "/zahlung/rueckmeldung/stripe",
        content=b'{"id": "evt_1"}',
        headers={"Stripe-Signature": "t=1,v1=abc", "Content-Type": "application/json"},
    )
    assert r.status_code == 200 and r.json() == {"ok": True}
    e = db.scalar(select(WebhookEingang))
    assert e.rohdaten == '{"id": "evt_1"}' and e.signatur_header == "t=1,v1=abc"
    a = db.get(Anfrage, e.anfrage_id)
    assert a.typ == "zahlung_eingegangen" and a.konto_id is None
    assert a.nutzlast_json == {
        "provider": "stripe",
        "rohdaten": '{"id": "evt_1"}',
        "signatur_header": "t=1,v1=abc",
    }


def test_briefkasten_braucht_kein_csrf(angemeldet: TestClient) -> None:
    assert angemeldet.post("/zahlung/rueckmeldung/stripe", content=b"{}").status_code == 200


def test_briefkasten_zu_gross(client: TestClient, db: Session) -> None:
    r = client.post("/zahlung/rueckmeldung/stripe", content=b"x" * (64 * 1024 + 1))
    assert r.status_code == 413
    assert db.scalar(select(Anfrage)) is None


def test_briefkasten_zu_gross_ohne_content_length(client: TestClient, db: Session) -> None:
    """Fix-Runde 1: Ohne (oder mit falschem) Content-Length-Header darf die 64-KB-Grenze nicht
    umgangen werden – ein Generator-Body erzeugt bei httpx Chunked Transfer Encoding, also ohne
    Content-Length-Header."""

    def strom() -> Iterator[bytes]:
        for _ in range(65):
            yield b"x" * 1024

    r = client.post("/zahlung/rueckmeldung/stripe", content=strom())
    assert r.status_code == 413
    assert db.scalar(select(Anfrage)) is None
    assert db.scalar(select(WebhookEingang)) is None


def test_briefkasten_ungueltige_nutzlast_wird_nur_geloggt(client: TestClient, db: Session) -> None:
    """Fix-Runde 1: Ein Signatur-Header über 2000 Zeichen verletzt kanal.ZahlungEingegangen;
    das darf nicht mit 500 enden, sondern nur ohne Anfrage gespeichert werden – gekürzt wird
    nichts."""
    zu_lang = "a" * 2001
    r = client.post(
        "/zahlung/rueckmeldung/stripe", content=b"{}", headers={"Stripe-Signature": zu_lang}
    )
    assert r.status_code == 200 and r.json() == {"ok": True}
    e = db.scalar(select(WebhookEingang))
    assert e is not None
    assert e.anfrage_id is None
    assert e.signatur_header == zu_lang
    assert db.scalar(select(Anfrage)) is None


@pytest.mark.parametrize("provider", ["Stripe", "x", "a-b", "sehrsehrsehrlangername"])
def test_briefkasten_unbekannter_pfad(client: TestClient, provider: str) -> None:
    assert client.post(f"/zahlung/rueckmeldung/{provider}", content=b"{}").status_code == 404


def test_fake_seite_nur_wenn_eingeschaltet(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    params = {"betrag": "30.00", "zurueck": "/zahlung/zurueck?anfrage=1"}
    seite = client.get("/test-zahlung/fake_abc", params=params).text
    assert "Bezahlen" in seite and "30.00" in seite
    monkeypatch.setattr(settings, "fake_zahlung", False)
    assert client.get("/test-zahlung/fake_abc").status_code == 404
    assert client.post("/test-zahlung/fake_abc", data={"ergebnis": "bezahlt"}).status_code == 404


def test_fake_zahlung_legt_rueckmeldung_an(client: TestClient, db: Session) -> None:
    aid = uuid.uuid4()
    r = client.post(
        "/test-zahlung/fake_abc",
        data={
            "ergebnis": "bezahlt",
            "betrag": "30.00",
            "zurueck": f"https://portal.example:8443/zahlung/zurueck?anfrage={aid}",
        },
        follow_redirects=False,
    )
    assert r.headers["location"] == f"/zahlung/zurueck?anfrage={aid}"
    a = db.scalar(select(Anfrage))
    assert a.typ == "zahlung_eingegangen"
    assert json.loads(a.nutzlast_json["rohdaten"]) == {
        "ref": "fake_abc",
        "ergebnis": "bezahlt",
        "betrag": "30.00",
    }


def test_fake_zahlung_leitet_nie_nach_draussen(client: TestClient) -> None:
    r = client.post(
        "/test-zahlung/fake_abc",
        data={"ergebnis": "abgebrochen", "zurueck": "https://evil.example/anderswo"},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/buchungen"
    assert client.post("/test-zahlung/fake_abc", data={"ergebnis": "gestohlen"}).status_code == 400


def test_test_zahlung_seite_mit_kaputtem_ziel(client: TestClient) -> None:
    """Fix-Runde 1: urlsplit wirft bei einem kaputten IPv6-Literal ValueError – das darf die
    Fake-Zahlungsseite nicht mit 500 abbrechen lassen, sondern nur auf /buchungen zurückfallen."""
    r = client.get("/test-zahlung/fake_abc", params={"zurueck": "http://[::1/evil"})
    assert r.status_code == 200
    assert 'value="/buchungen"' in r.text


def test_fake_zahlung_mit_kaputtem_ziel_leitet_sicher(client: TestClient) -> None:
    r = client.post(
        "/test-zahlung/fake_abc",
        data={"ergebnis": "bezahlt", "betrag": "1.00", "zurueck": "http://[::1/evil"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/buchungen"


def test_zurueck_fuehrt_zur_warteseite(client: TestClient) -> None:
    aid = uuid.uuid4()
    r = client.get(f"/zahlung/zurueck?anfrage={aid}", follow_redirects=False)
    assert r.headers["location"] == f"/anfrage/{aid}"
    r = client.get("/zahlung/zurueck?anfrage=quatsch", follow_redirects=False)
    assert r.headers["location"] == "/buchungen"
