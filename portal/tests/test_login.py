import re

from beachhub_portal import auth
from beachhub_portal.models import Anfrage, Konto, Lesestand, Sitzung
from fastapi.testclient import TestClient
from hilfen import KUNDE_ID, csrf, konto, speichere
from sqlalchemy import select
from sqlalchemy.orm import Session


def _code(ausgang: list) -> str:
    return re.search(r"Anmeldeseite ein: (\d{6})", ausgang[-1]["text"]).group(1)


def _link(ausgang: list) -> str:
    return re.search(r"http://testserver(/anmelden/link/\S+)", ausgang[-1]["text"]).group(1)


def _einloggen(client: TestClient, ausgang: list, email: str = "anna@example.org") -> None:
    client.post("/anmelden", data={"email": email})
    client.post("/anmelden/code", data={"email": email, "code": _code(ausgang)})


def test_anfordern_gleiche_antwort_und_mail(client: TestClient, mail_ausgang) -> None:
    r = client.post("/anmelden", data={"email": "neu@example.org"})
    assert r.status_code == 200 and "Wenn die Adresse stimmt" in r.text
    assert mail_ausgang[-1]["an"] == "neu@example.org"
    assert re.search(r"Anmeldeseite ein: \d{6}", mail_ausgang[-1]["text"])
    assert "http://testserver/anmelden/link/" in mail_ausgang[-1]["text"]


def test_ungueltige_adresse(client: TestClient, mail_ausgang) -> None:
    r = client.post("/anmelden", data={"email": "kein-at"})
    assert r.status_code == 400 and mail_ausgang == []


def test_code_meldet_an_und_legt_konto_an(client: TestClient, db: Session, mail_ausgang) -> None:
    client.post("/anmelden", data={"email": "  Anna@Example.org "})
    r = client.post(
        "/anmelden/code",
        data={"email": "anna@example.org", "code": _code(mail_ausgang)},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == "/willkommen"
    k = db.scalar(select(Konto))
    assert k.email == "anna@example.org" and k.anzeigename == ""
    assert client.get("/willkommen").status_code == 200


def test_bekanntes_konto_geht_direkt_zur_belegung(
    client: TestClient, db: Session, mail_ausgang
) -> None:
    db.add(Konto(email="anna@example.org", anzeigename="Anna"))
    db.commit()
    client.post("/anmelden", data={"email": "anna@example.org"})
    r = client.post(
        "/anmelden/code",
        data={"email": "anna@example.org", "code": _code(mail_ausgang)},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/"


def test_fuenf_fehlversuche_verbrauchen_den_code(client: TestClient, mail_ausgang) -> None:
    client.post("/anmelden", data={"email": "a@example.org"})
    richtig = _code(mail_ausgang)
    falsch = "000000" if richtig != "000000" else "111111"
    for _ in range(5):
        r = client.post("/anmelden/code", data={"email": "a@example.org", "code": falsch})
        assert r.status_code == 401
    r = client.post("/anmelden/code", data={"email": "a@example.org", "code": richtig})
    assert r.status_code == 401


def test_code_laeuft_nach_15_minuten_ab(client: TestClient, mail_ausgang, uhr_steht) -> None:
    client.post("/anmelden", data={"email": "a@example.org"})
    uhr_steht.weiter(minutes=16)
    r = client.post("/anmelden/code", data={"email": "a@example.org", "code": _code(mail_ausgang)})
    assert r.status_code == 401


def test_neue_anforderung_macht_alten_code_ungueltig(client: TestClient, mail_ausgang) -> None:
    """Ruling R9: unbedingte Assertion statt `if alt != neu` – ein neuer Code wird notfalls
    mehrfach angefordert (Rate-Limit dabei zurückgesetzt), bis er sich vom alten unterscheidet."""
    client.post("/anmelden", data={"email": "a@example.org"})
    alt = _code(mail_ausgang)
    neu = alt
    for _ in range(20):
        auth.reset_rate_limits()
        client.post("/anmelden", data={"email": "a@example.org"})
        neu = _code(mail_ausgang)
        if neu != alt:
            break
    assert neu != alt, "Zufalls-Code hat sich in 20 Versuchen nicht geändert"
    r = client.post("/anmelden/code", data={"email": "a@example.org", "code": alt})
    assert r.status_code == 401
    r = client.post(
        "/anmelden/code", data={"email": "a@example.org", "code": neu}, follow_redirects=False
    )
    assert r.status_code == 303


def test_link_get_verbraucht_nicht(client: TestClient, mail_ausgang) -> None:
    client.post("/anmelden", data={"email": "a@example.org"})
    pfad = _link(mail_ausgang)
    assert "Jetzt anmelden" in client.get(pfad).text
    assert "Jetzt anmelden" in client.get(pfad).text  # Mail-Scanner hat schon einmal geöffnet
    r = client.post(pfad, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/willkommen"
    client.cookies.clear()
    assert client.post(pfad, follow_redirects=False).status_code == 400


def test_link_bei_bestehender_sitzung_kein_403(client: TestClient, mail_ausgang) -> None:
    """Ruling R16: Wer schon angemeldet ist (Sitzung im Browser) und einen Anmeldelink für ein
    anderes Konto öffnet, muss ihn ohne 403 einlösen können; das CSRF-Token der Link-Seite muss
    dafür aus der bestehenden Sitzung stammen, nicht leer sein."""
    _einloggen(client, mail_ausgang, "anna@example.org")
    sitzung = client.cookies.get(auth.COOKIE)
    client.cookies.clear()
    client.post("/anmelden", data={"email": "berta@example.org"})
    pfad = _link(mail_ausgang)
    client.cookies.set(auth.COOKIE, sitzung)
    token = csrf(client.get(pfad).text)
    assert token
    r = client.post(pfad, data={"csrf_token": token}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/willkommen"


def test_rate_limit_je_adresse_und_ip(client: TestClient) -> None:
    for _ in range(3):
        assert client.post("/anmelden", data={"email": "a@example.org"}).status_code == 200
    assert client.post("/anmelden", data={"email": "a@example.org"}).status_code == 429
    auth.reset_rate_limits()
    for i in range(5):
        client.post("/anmelden", data={"email": f"x{i}@example.org"})
    assert client.post("/anmelden", data={"email": "y@example.org"}).status_code == 429


def test_entwicklung_zeigt_code_ohne_mailserver(client: TestClient, mail_ausgang) -> None:
    r = client.post("/anmelden", data={"email": "a@example.org"})
    assert _code(mail_ausgang) in r.text


def test_willkommen_legt_konto_angelegt_an(client: TestClient, db: Session, mail_ausgang) -> None:
    _einloggen(client, mail_ausgang)
    token = csrf(client.get("/willkommen").text)
    r = client.post(
        "/willkommen",
        data={"anzeigename": " Anna ", "csrf_token": token},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == "/"
    a = db.scalar(select(Anfrage))
    assert a.typ == "konto_angelegt"
    assert a.nutzlast_json == {"email": "anna@example.org", "anzeigename": "Anna"}
    assert client.get("/willkommen", follow_redirects=False).headers["location"] == "/"


def test_geschuetzte_seiten(client: TestClient, mail_ausgang) -> None:
    assert client.get("/konto", follow_redirects=False).headers["location"] == "/anmelden"
    _einloggen(client, mail_ausgang)
    assert client.get("/konto", follow_redirects=False).headers["location"] == "/willkommen"


def test_csrf_pflicht(angemeldet: TestClient) -> None:
    assert angemeldet.post("/konto/name", data={"anzeigename": "X"}).status_code == 403
    r = angemeldet.post(
        "/konto/name",
        data={"anzeigename": "X", "csrf_token": angemeldet.csrf},
        follow_redirects=False,
    )
    assert r.status_code == 303


def test_core_antworten_ohne_csrf(client: TestClient) -> None:
    """Ruling: CSRF ist je Router eingehängt, nicht app-weit – /core/* darf davon nicht
    betroffen sein, sonst könnte das Hauptsystem nie antworten (kein Browser, kein Formular)."""
    kopf = {"Authorization": "Bearer test-kanal-token"}
    r = client.post("/core/antworten", json={"antworten": []}, headers=kopf)
    assert r.status_code == 200 and r.json() == {"ok": 0}


def test_name_aendern_legt_anfrage_an(angemeldet: TestClient, db: Session) -> None:
    angemeldet.post("/konto/name", data={"anzeigename": "Anni", "csrf_token": angemeldet.csrf})
    a = db.scalar(select(Anfrage))
    assert a.typ == "konto_geaendert"
    assert a.nutzlast_json == {"anzeigename": "Anni", "bisher": "Anna"}


def test_konto_seite_zeigt_adresse_und_abo_hinweis(angemeldet: TestClient) -> None:
    seite = angemeldet.get("/konto").text
    assert "anna@example.org" in seite and "halle@example.org" in seite


def test_session_gleitet_und_abmelden(angemeldet: TestClient, db: Session, uhr_steht) -> None:
    ablauf = db.scalar(select(Sitzung)).laeuft_ab
    uhr_steht.weiter(days=2)
    assert angemeldet.get("/konto").status_code == 200
    db.expire_all()
    assert db.scalar(select(Sitzung)).laeuft_ab > ablauf
    angemeldet.post("/abmelden", data={"csrf_token": angemeldet.csrf})
    assert db.scalar(select(Sitzung)) is None


def test_konto_loeschen(angemeldet: TestClient, db: Session) -> None:
    speichere(db, f"konto:{KUNDE_ID}", konto())
    assert "endgültig" in angemeldet.get("/konto/loeschen").text
    r = angemeldet.post(
        "/konto/loeschen", data={"csrf_token": angemeldet.csrf}, follow_redirects=False
    )
    assert r.status_code == 303
    db.expire_all()
    assert db.scalar(select(Konto)) is None and db.scalar(select(Sitzung)) is None
    assert db.get(Lesestand, f"konto:{KUNDE_ID}") is None
    a = db.scalar(select(Anfrage))
    assert a.typ == "konto_loeschen" and a.konto_id == angemeldet.konto_id
    assert angemeldet.get("/konto", follow_redirects=False).headers["location"] == "/anmelden"
