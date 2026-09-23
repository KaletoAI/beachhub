import json
import uuid

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


def test_zurueck_fuehrt_zur_warteseite(client: TestClient) -> None:
    aid = uuid.uuid4()
    r = client.get(f"/zahlung/zurueck?anfrage={aid}", follow_redirects=False)
    assert r.headers["location"] == f"/anfrage/{aid}"
    r = client.get("/zahlung/zurueck?anfrage=quatsch", follow_redirects=False)
    assert r.headers["location"] == "/buchungen"
