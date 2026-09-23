import hashlib
import inspect
import re
import threading
from datetime import UTC, datetime, timedelta

import pytest
from beachhub_portal import auth, sicherheit, uhr
from beachhub_portal.config import settings
from beachhub_portal.database import SessionLocal
from beachhub_portal.models import (
    Anfrage,
    CodeFehlversuch,
    Konto,
    Lesestand,
    LoginToken,
    RechnungLink,
    Sitzung,
)
from beachhub_portal.services import anfragen
from beachhub_shared import kanal
from fastapi.testclient import TestClient
from hilfen import KUNDE_ID, csrf, konto, speichere
from sqlalchemy import select
from sqlalchemy.orm import Session


def _code(ausgang: list) -> str:
    return re.search(r"Anmeldeseite ein: (\d{6})", ausgang[-1]["text"]).group(1)


def _link(ausgang: list) -> str:
    return re.search(r"http://testserver(/anmelden/link/\S+)", ausgang[-1]["text"]).group(1)


def _vor_csrf(client: TestClient) -> str:
    """Vor-Session-CSRF-Token (Double-Submit-Cookie) holen/erzeugen; wiederverwendbar für jedes
    anonyme Formular, solange dasselbe Cookie im Client bleibt (Ruling Fix-Runde 1, Item 2)."""
    return csrf(client.get("/anmelden").text)


def _einloggen(client: TestClient, ausgang: list, email: str = "anna@example.org") -> None:
    token = _vor_csrf(client)
    client.post("/anmelden", data={"email": email, "csrf_token": token})
    client.post(
        "/anmelden/code", data={"email": email, "code": _code(ausgang), "csrf_token": token}
    )


def test_anfordern_gleiche_antwort_und_mail(client: TestClient, mail_ausgang) -> None:
    token = _vor_csrf(client)
    r = client.post("/anmelden", data={"email": "neu@example.org", "csrf_token": token})
    assert r.status_code == 200 and "Wenn die Adresse stimmt" in r.text
    assert mail_ausgang[-1]["an"] == "neu@example.org"
    assert re.search(r"Anmeldeseite ein: \d{6}", mail_ausgang[-1]["text"])
    assert "http://testserver/anmelden/link/" in mail_ausgang[-1]["text"]


def test_ungueltige_adresse(client: TestClient, mail_ausgang) -> None:
    token = _vor_csrf(client)
    r = client.post("/anmelden", data={"email": "kein-at", "csrf_token": token})
    assert r.status_code == 400 and mail_ausgang == []


def test_email_ungueltig_kein_500(client: TestClient, mail_ausgang) -> None:
    """Ruling Fix-Runde 1 (Item 5): genau eine Adresse, kein Leerraum/Steuerzeichen (auch nicht
    CR/LF – Header-Injection-Versuch), kein „,“/„<>“ – nie ein 500, immer 400."""
    token = _vor_csrf(client)
    for kaputt in [
        "a@b.de\r\nBcc: x@y.de",
        "a,b@c.de",
        "a b@c.de",
        "<a@c.de>",
        "a@@c.de",
        "a@c",
        "a@c. de",
    ]:
        auth.reset_rate_limits()
        r = client.post("/anmelden", data={"email": kaputt, "csrf_token": token})
        assert r.status_code == 400, kaputt
    assert mail_ausgang == []


def test_code_meldet_an_und_legt_konto_an(client: TestClient, db: Session, mail_ausgang) -> None:
    token = _vor_csrf(client)
    client.post("/anmelden", data={"email": "  Anna@Example.org ", "csrf_token": token})
    r = client.post(
        "/anmelden/code",
        data={"email": "anna@example.org", "code": _code(mail_ausgang), "csrf_token": token},
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
    token = _vor_csrf(client)
    client.post("/anmelden", data={"email": "anna@example.org", "csrf_token": token})
    r = client.post(
        "/anmelden/code",
        data={"email": "anna@example.org", "code": _code(mail_ausgang), "csrf_token": token},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/"


def test_antwort_fuer_bekannte_und_unbekannte_adresse_gleich(
    client: TestClient, db: Session, mail_ausgang
) -> None:
    """Ruling Fix-Runde 1 (Item 11): Ob die Adresse ein Konto hat, darf aus der Antwort nicht
    erkennbar sein (Status und Meldung gleich)."""
    db.add(Konto(email="bekannt@example.org", anzeigename="B"))
    db.commit()
    token1 = _vor_csrf(client)
    r1 = client.post("/anmelden", data={"email": "bekannt@example.org", "csrf_token": token1})
    client.cookies.clear()
    token2 = _vor_csrf(client)
    r2 = client.post("/anmelden", data={"email": "unbekannt@example.org", "csrf_token": token2})
    assert r1.status_code == r2.status_code == 200
    assert "Wenn die Adresse stimmt" in r1.text
    assert "Wenn die Adresse stimmt" in r2.text


def test_fuenf_fehlversuche_verbrauchen_den_code(client: TestClient, mail_ausgang) -> None:
    token = _vor_csrf(client)
    client.post("/anmelden", data={"email": "a@example.org", "csrf_token": token})
    richtig = _code(mail_ausgang)
    falsch = "000000" if richtig != "000000" else "111111"
    for _ in range(5):
        r = client.post(
            "/anmelden/code", data={"email": "a@example.org", "code": falsch, "csrf_token": token}
        )
        assert r.status_code == 401
    r = client.post(
        "/anmelden/code", data={"email": "a@example.org", "code": richtig, "csrf_token": token}
    )
    assert r.status_code == 401


def test_fuenf_fehlversuche_macht_auch_link_ungueltig(client: TestClient, mail_ausgang) -> None:
    """Ruling Fix-Runde 1 (Item 11): Fünf Fehlversuche verbrauchen auch den zugehörigen Link."""
    token = _vor_csrf(client)
    client.post("/anmelden", data={"email": "a@example.org", "csrf_token": token})
    pfad = _link(mail_ausgang)
    richtig = _code(mail_ausgang)
    falsch = "000000" if richtig != "000000" else "111111"
    for _ in range(5):
        client.post(
            "/anmelden/code", data={"email": "a@example.org", "code": falsch, "csrf_token": token}
        )
    r = client.post(pfad, data={"csrf_token": token}, follow_redirects=False)
    assert r.status_code == 400


def test_code_laeuft_nach_15_minuten_ab(client: TestClient, mail_ausgang, uhr_steht) -> None:
    token = _vor_csrf(client)
    client.post("/anmelden", data={"email": "a@example.org", "csrf_token": token})
    uhr_steht.weiter(minutes=16)
    r = client.post(
        "/anmelden/code",
        data={"email": "a@example.org", "code": _code(mail_ausgang), "csrf_token": token},
    )
    assert r.status_code == 401


def test_neue_anforderung_macht_alten_code_ungueltig(client: TestClient, mail_ausgang) -> None:
    """Ruling R9 (Fix-Runde 0): unbedingte Assertion statt `if alt != neu` – ein neuer Code wird
    notfalls mehrfach angefordert (Rate-Limit dabei zurückgesetzt), bis er sich unterscheidet."""
    token = _vor_csrf(client)
    client.post("/anmelden", data={"email": "a@example.org", "csrf_token": token})
    alt = _code(mail_ausgang)
    neu = alt
    for _ in range(20):
        auth.reset_rate_limits()
        client.post("/anmelden", data={"email": "a@example.org", "csrf_token": token})
        neu = _code(mail_ausgang)
        if neu != alt:
            break
    assert neu != alt, "Zufalls-Code hat sich in 20 Versuchen nicht geändert"
    r = client.post(
        "/anmelden/code", data={"email": "a@example.org", "code": alt, "csrf_token": token}
    )
    assert r.status_code == 401
    r = client.post(
        "/anmelden/code",
        data={"email": "a@example.org", "code": neu, "csrf_token": token},
        follow_redirects=False,
    )
    assert r.status_code == 303


def test_code_sperre_nach_20_fehlversuchen_in_24h(client: TestClient, mail_ausgang) -> None:
    """Ruling Fix-Runde 1 (Item 4): Obergrenze 20 Fehlversuche je Adresse in 24 h, unabhängig von
    einzelnen Tokens – sonst hebelt ein neu angeforderter Code das Fünf-Versuche-Limit je Token
    aus. Danach wird auch der richtige Code abgelehnt, mit Hinweis auf den Link."""
    email = "a@example.org"
    for _ in range(4):
        token = _vor_csrf(client)
        client.post("/anmelden", data={"email": email, "csrf_token": token})
        richtig = _code(mail_ausgang)
        falsch = "000000" if richtig != "000000" else "111111"
        for _ in range(5):
            client.post(
                "/anmelden/code", data={"email": email, "code": falsch, "csrf_token": token}
            )
        auth.reset_rate_limits()
    token = _vor_csrf(client)
    client.post("/anmelden", data={"email": email, "csrf_token": token})
    richtig = _code(mail_ausgang)
    r = client.post("/anmelden/code", data={"email": email, "code": richtig, "csrf_token": token})
    assert r.status_code == 401 and "über den Link" in r.text


def test_code_hash_ist_hmac_nicht_reines_sha256(db: Session) -> None:
    """Ruling Fix-Runde 1 (Item 6): HMAC-SHA256 mit dem Secret-Key statt reinem SHA-256 – der
    6-stellige Code hat nur 10**6 Möglichkeiten und wäre sonst bei einem DB-Leck offline in
    Sekunden durchprobierbar."""
    _token, code = auth.fordere_an(db, "a@example.org", datetime.now(UTC))
    zeile = db.scalar(select(LoginToken))
    assert zeile.code_hash != hashlib.sha256(code.encode()).hexdigest()
    assert zeile.code_hash == sicherheit.hash_code(code, settings.secret_key)


def test_pruefe_code_parallele_fehlversuche_verbrauchen_token(db: Session) -> None:
    """Ruling Fix-Runde 1 (Item 1): ohne Zeilensperre lesen parallele Fehlversuche denselben
    alten Stand und überschreiben sich gegenseitig – das Fünf-Versuche-Limit wäre umgehbar."""
    _token, richtig = auth.fordere_an(db, "a@example.org", datetime.now(UTC))
    falsch = "000000" if richtig != "000000" else "111111"
    ergebnisse: list[bool] = []
    sperre = threading.Lock()

    def _versuch() -> None:
        with SessionLocal() as eigene_db:
            ok = auth.pruefe_code(eigene_db, "a@example.org", falsch, datetime.now(UTC))
        with sperre:
            ergebnisse.append(ok)

    threads = [threading.Thread(target=_versuch) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert ergebnisse == [False] * 10
    # Nach zehn (statt fälschlich niedriger gezählten) Fehlversuchen ist der Code verbraucht –
    # auch der richtige Code wird jetzt abgelehnt.
    assert auth.pruefe_code(db, "a@example.org", richtig, datetime.now(UTC)) is False


def test_link_get_verbraucht_nicht(client: TestClient, mail_ausgang) -> None:
    token = _vor_csrf(client)
    client.post("/anmelden", data={"email": "a@example.org", "csrf_token": token})
    pfad = _link(mail_ausgang)
    assert "Jetzt anmelden" in client.get(pfad).text
    assert "Jetzt anmelden" in client.get(pfad).text  # Mail-Scanner hat schon einmal geöffnet
    r = client.post(pfad, data={"csrf_token": token}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/willkommen"
    client.cookies.clear()
    # Ohne jedes Cookie greift zuerst der CSRF-Schutz (Ruling Fix-Runde 1, Item 2): der bereits
    # eingelöste Link selbst würde ebenfalls ablehnen, aber ohne Vor-Session-Cookie kommt die
    # Anfrage gar nicht erst bis zu dieser Prüfung.
    assert client.post(pfad, follow_redirects=False).status_code == 403


def test_link_bei_bestehender_sitzung_kein_403_und_beendet_alte(
    client: TestClient, mail_ausgang, db: Session
) -> None:
    """Ruling R16 (Fix-Runde 0) + Ruling Fix-Runde 1 Item 8: Wer schon angemeldet ist (Sitzung im
    Browser) und einen Anmeldelink für ein anderes Konto öffnet, muss ihn ohne 403 einlösen
    können – das CSRF-Token der Link-Seite muss aus der bestehenden Sitzung stammen. Dabei wird
    die alte Sitzung beendet, statt parallel gültig zu bleiben."""
    _einloggen(client, mail_ausgang, "anna@example.org")
    alte_sitzung = db.scalar(select(Sitzung)).token_hash
    sitzung_cookie = client.cookies.get(auth.COOKIE)
    client.cookies.clear()
    vor_token = _vor_csrf(client)
    client.post("/anmelden", data={"email": "berta@example.org", "csrf_token": vor_token})
    pfad = _link(mail_ausgang)
    client.cookies.set(auth.COOKIE, sitzung_cookie)
    token = csrf(client.get(pfad).text)
    assert token
    r = client.post(pfad, data={"csrf_token": token}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/willkommen"
    sitzungen = db.scalars(select(Sitzung)).all()
    assert len(sitzungen) == 1
    assert sitzungen[0].token_hash != alte_sitzung


def test_angemeldet_abgelaufener_link_zeigt_gueltiges_token(
    client: TestClient, mail_ausgang, uhr_steht
) -> None:
    """Ruling Fix-Runde 1 (Item 2): Formular der Fehlerseite trägt bei bestehender Sitzung ein
    gültiges CSRF-Token; der nächste POST scheitert nicht an einem falschen Token."""
    vor_token = _vor_csrf(client)
    client.post("/anmelden", data={"email": "anna@example.org", "csrf_token": vor_token})
    pfad = _link(mail_ausgang)
    uhr_steht.weiter(minutes=16)
    _einloggen(client, mail_ausgang, "anna@example.org")
    seite = client.get(pfad)
    assert "abgelaufen" in seite.text
    token = csrf(seite.text)
    assert token
    r = client.post("/anmelden", data={"email": "berta@example.org", "csrf_token": token})
    assert r.status_code == 200


def test_angemeldet_ungueltiger_link_post_zeigt_gueltiges_token(
    client: TestClient, mail_ausgang, db: Session
) -> None:
    """Ruling Fix-Runde 2 (Item 2): der Fehlerfall POST /anmelden/link/{token} (ungültiger,
    abgelaufener oder schon eingelöster Link) muss bei bestehender Sitzung ebenfalls ein
    gültiges CSRF-Token rendern, nicht nur der GET-Fehlerfall – sonst 403 beim nächsten POST."""
    _einloggen(client, mail_ausgang, "anna@example.org")
    sitzung_csrf = db.scalar(select(Sitzung.csrf_token))
    r = client.post(
        "/anmelden/link/nicht-vorhanden",
        data={"csrf_token": sitzung_csrf},
        follow_redirects=False,
    )
    assert r.status_code == 400
    token = csrf(r.text)
    assert token == sitzung_csrf
    r2 = client.post("/anmelden", data={"email": "berta@example.org", "csrf_token": token})
    assert r2.status_code == 200


def test_rate_limit_je_adresse_und_ip(client: TestClient) -> None:
    token = _vor_csrf(client)
    for _ in range(3):
        r = client.post("/anmelden", data={"email": "a@example.org", "csrf_token": token})
        assert r.status_code == 200
    r = client.post("/anmelden", data={"email": "a@example.org", "csrf_token": token})
    assert r.status_code == 429
    auth.reset_rate_limits()
    for i in range(5):
        client.post("/anmelden", data={"email": f"x{i}@example.org", "csrf_token": token})
    r = client.post("/anmelden", data={"email": "y@example.org", "csrf_token": token})
    assert r.status_code == 429


def test_entwicklung_zeigt_code_ohne_mailserver(client: TestClient, mail_ausgang) -> None:
    token = _vor_csrf(client)
    r = client.post("/anmelden", data={"email": "a@example.org", "csrf_token": token})
    assert _code(mail_ausgang) in r.text


def test_dev_anzeige_nur_im_dev_modus(
    client: TestClient, mail_ausgang, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ruling Fix-Runde 1 (Item 7): nur im echten Entwicklungsmodus, nicht bei jeder
    Nicht-Produktion (z. B. Staging) ohne konfiguriertes SMTP."""
    monkeypatch.setattr(settings, "app_env", "staging")
    token = _vor_csrf(client)
    r = client.post("/anmelden", data={"email": "a@example.org", "csrf_token": token})
    assert _code(mail_ausgang) not in r.text


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


def test_konto_angelegt_nach_fehlschlag_erneut(
    angemeldet: TestClient, db: Session, uhr_steht
) -> None:
    """Beantwortet das Hauptsystem `konto_angelegt` mit fehler/abgelehnt, bliebe kunde_id sonst
    für immer leer. Das Portal stellt die Anfrage beim nächsten Seitenaufruf neu – höchstens
    alle zehn Minuten und nie, solange noch eine offen ist."""
    k = db.get(Konto, angemeldet.konto_id)
    k.kunde_id = None
    db.commit()
    erste = anfragen.stelle(
        db,
        typ="konto_angelegt",
        konto_id=k.id,
        nutzlast={"email": "anna@example.org", "anzeigename": "Anna"},
    )

    def konto_anfragen() -> list[Anfrage]:
        db.expire_all()
        return list(
            db.scalars(
                select(Anfrage)
                .where(Anfrage.typ == "konto_angelegt")
                .order_by(Anfrage.erstellt_am, Anfrage.id)
            )
        )

    angemeldet.get("/buchungen")
    assert len(konto_anfragen()) == 1  # noch offen: nichts nachlegen
    anfragen.beantworte(db, erste.id, kanal.Antwort(status="fehler"), uhr_steht.jetzt)
    db.commit()
    angemeldet.get("/buchungen")
    assert len(konto_anfragen()) == 1  # gerade erst versucht
    uhr_steht.weiter(minutes=11)
    angemeldet.get("/buchungen")
    alle = konto_anfragen()
    assert len(alle) == 2
    assert alle[1].status == Anfrage.OFFEN and alle[1].konto_id == k.id
    assert alle[1].nutzlast_json == {"email": "anna@example.org", "anzeigename": "Anna"}
    uhr_steht.weiter(minutes=11)
    angemeldet.get("/konto")
    assert len(konto_anfragen()) == 2  # die neue ist noch offen


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


def test_anonymer_post_ohne_csrf_token_403(client: TestClient) -> None:
    """Ruling Fix-Runde 1 (Item 2): keine Sitzung, kein Vor-Session-Cookie, kein csrf_token –
    verify_csrf ließ das bisher unbemerkt durch (Login-CSRF: eine fremde Seite könnte ein
    abgemeldetes Opfer in das Konto des Angreifers einloggen)."""
    r = client.post("/anmelden", data={"email": "a@example.org"})
    assert r.status_code == 403


def test_anonymer_post_falscher_csrf_token_403(client: TestClient) -> None:
    client.get("/anmelden")  # setzt das Vor-Session-Cookie
    r = client.post("/anmelden", data={"email": "a@example.org", "csrf_token": "falsch"})
    assert r.status_code == 403


def test_anonymer_post_mit_vor_session_token_ok(client: TestClient) -> None:
    token = csrf(client.get("/anmelden").text)
    r = client.post("/anmelden", data={"email": "a@example.org", "csrf_token": token})
    assert r.status_code == 200


def test_csrf_vergleich_ohne_500_bei_nicht_ascii(client: TestClient) -> None:
    """Ruling Fix-Runde 1 (Item 9): compare_digest auf bytes – sonst TypeError (500 statt 403)
    bei Nicht-ASCII-Zeichen im übermittelten Formularfeld."""
    client.get("/anmelden")
    r = client.post("/anmelden", data={"email": "a@example.org", "csrf_token": "ø§¶€…"})
    assert r.status_code == 403


def test_verify_csrf_ist_synchron() -> None:
    """Ruling Fix-Runde 2 (Item 1): verify_csrf lief zuvor als `async def` mit
    `await request.form()` und blockierender DB-Arbeit auf dem Event-Loop – bei jeder Anfrage
    beider Router, auch GET, den auch der Long-Poll /core/anfragen nutzt. Als sync-Abhängigkeit
    führt FastAPI sie stattdessen im Threadpool aus."""
    assert inspect.iscoroutinefunction(auth.verify_csrf) is False


def test_core_antworten_ohne_csrf(client: TestClient) -> None:
    """CSRF ist je Router eingehängt, nicht app-weit – /core/* darf davon nicht betroffen sein,
    sonst könnte das Hauptsystem nie antworten (kein Browser, kein Formular)."""
    kopf = {"Authorization": "Bearer test-kanal-token"}
    r = client.post("/core/antworten", json={"antworten": []}, headers=kopf)
    assert r.status_code == 200 and r.json() == {"ok": 0}


def test_sitzung_verlaengerung_erneuert_cookie(angemeldet: TestClient, uhr_steht) -> None:
    """Ruling Fix-Runde 1 (Item 3): `lade_sitzung` verlängert nur die DB; ohne erneutes
    Set-Cookie würde der Browser das Cookie trotzdem fest 30 Tage nach dem ersten Login
    verwerfen."""
    uhr_steht.weiter(days=2)
    r = angemeldet.get("/konto")
    gesetzt = r.headers.get("set-cookie", "").lower()
    assert auth.COOKIE.lower() in gesetzt and "max-age=" in gesetzt


def test_abmelden_nach_verlaengernder_anfrage_setzt_kein_altes_cookie(
    angemeldet: TestClient, uhr_steht
) -> None:
    """Ruling Fix-Runde 2 (Item 4): Abmelden verlängert und löscht die Sitzung in derselben
    Anfrage (verify_csrf verlängert zuerst, der Handler löscht danach) – die Middleware darf das
    schon im Response gesetzte Lösch-Cookie nicht mit dem alten (jetzt ungültigen) Token
    überschreiben, sonst bekäme der Browser nach dem Abmelden eine tote Sitzung zurück."""
    uhr_steht.weiter(days=2)  # nächste Anfrage verlängert die Sitzung gleitend
    r = angemeldet.post("/abmelden", data={"csrf_token": angemeldet.csrf}, follow_redirects=False)
    gesetzt = [c for c in r.headers.get_list("set-cookie") if c.startswith(f"{auth.COOKIE}=")]
    assert len(gesetzt) == 1
    assert "max-age=0" in gesetzt[0].lower()


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
    ordner = settings.data_dir / "rechnungen_tmp"
    ordner.mkdir(parents=True, exist_ok=True)
    pdf_pfad = ordner / "rechnung.pdf"
    pdf_pfad.write_bytes(b"%PDF-1.4 test")
    db.add(
        RechnungLink(
            konto_id=angemeldet.konto_id,
            rechnung_nr="R-1",
            token_hash="x" * 64,
            pdf_pfad=str(pdf_pfad),
            laeuft_ab=uhr.jetzt() + timedelta(minutes=10),
        )
    )
    auth.fordere_an(db, "anna@example.org", uhr.jetzt())
    # Ruling Fix-Runde 2 (Item 3): code_fehlversuch hat keinen Fremdschlüssel auf konto (die
    # Sperre muss auch für unbekannte Adressen gelten) und braucht deshalb einen eigenen
    # Löschpfad in konten.loesche.
    db.add(CodeFehlversuch(email="anna@example.org", versucht_am=uhr.jetzt()))
    db.commit()
    assert db.scalar(select(LoginToken)) is not None
    assert db.scalar(select(CodeFehlversuch)) is not None
    assert "endgültig" in angemeldet.get("/konto/loeschen").text
    r = angemeldet.post(
        "/konto/loeschen", data={"csrf_token": angemeldet.csrf}, follow_redirects=False
    )
    assert r.status_code == 303
    db.expire_all()
    assert db.scalar(select(Konto)) is None and db.scalar(select(Sitzung)) is None
    assert db.get(Lesestand, f"konto:{KUNDE_ID}") is None
    assert db.scalar(select(RechnungLink)) is None
    assert not pdf_pfad.exists()
    assert db.scalar(select(LoginToken)) is None
    assert db.scalar(select(CodeFehlversuch)) is None
    a = db.scalar(select(Anfrage))
    assert a.typ == "konto_loeschen" and a.konto_id == angemeldet.konto_id
    assert angemeldet.get("/konto", follow_redirects=False).headers["location"] == "/anmelden"
