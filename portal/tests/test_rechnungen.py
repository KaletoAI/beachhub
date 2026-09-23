import base64
import os
import threading
import uuid
from datetime import timedelta

import pytest
from beachhub_portal import jobs, uhr
from beachhub_portal.config import settings
from beachhub_portal.database import engine
from beachhub_portal.models import (
    Anfrage,
    CodeFehlversuch,
    Konto,
    Lesestand,
    LoginToken,
    Sitzung,
    WebhookEingang,
)
from beachhub_portal.services import anfragen, rechnung_link
from beachhub_shared import kanal
from fastapi.testclient import TestClient
from hilfen import KUNDE_ID, konto, speichere
from sqlalchemy import select, text
from sqlalchemy.orm import Session

RECHNUNG = {"nummer": "2027-00001", "datum": "2027-11-20", "brutto": "30.00", "status": "bezahlt"}


def _tmp_pdfs() -> list:
    ordner = settings.data_dir / "rechnungen_tmp"
    return list(ordner.glob("*.pdf")) if ordner.exists() else []


def _bereitstellen(db: Session, konto_id: uuid.UUID) -> Anfrage:
    speichere(db, f"konto:{KUNDE_ID}", konto(rechnungen=[RECHNUNG]))
    a = anfragen.stelle(
        db, typ="rechnung_anfordern", konto_id=konto_id, nutzlast={"rechnung_nr": "2027-00001"}
    )
    antwort = kanal.Antwort(
        status="ok",
        pdf_base64=base64.b64encode(b"%PDF-1.7 test").decode(),
        dateiname="Rechnung-2027-00001.pdf",
    )
    anfragen.beantworte(db, a.id, antwort, uhr.jetzt())
    db.commit()
    return a


def test_liste(angemeldet: TestClient, db: Session) -> None:
    speichere(db, f"konto:{KUNDE_ID}", konto(rechnungen=[RECHNUNG]))
    seite = angemeldet.get("/rechnungen").text
    assert "2027-00001" in seite and "20.11.2027" in seite and "30,00 €" in seite


def test_anfordern_nur_eigene_nummer(angemeldet: TestClient, db: Session) -> None:
    speichere(db, f"konto:{KUNDE_ID}", konto(rechnungen=[RECHNUNG]))
    r = angemeldet.post(
        "/rechnungen/2027-00001/anfordern",
        data={"csrf_token": angemeldet.csrf},
        follow_redirects=False,
    )
    a = db.scalar(select(Anfrage))
    assert r.headers["location"] == f"/anfrage/{a.id}"
    assert a.typ == "rechnung_anfordern" and a.nutzlast_json == {"rechnung_nr": "2027-00001"}
    r = angemeldet.post(
        "/rechnungen/2027-99999/anfordern",
        data={"csrf_token": angemeldet.csrf},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/rechnungen"


def test_anfordern_doppelklick_erzeugt_keine_zweite_anfrage(
    angemeldet: TestClient, db: Session
) -> None:
    """Controller-Ruling: Doppelklick beim Anfordern über `anfragen.bestehende`, wie beim Buchen
    (Task 12) und Stornieren (Task 14) – kein zweiter POST desselben Formulars darf eine zweite
    Anfrage erzeugen."""
    speichere(db, f"konto:{KUNDE_ID}", konto(rechnungen=[RECHNUNG]))
    r1 = angemeldet.post(
        "/rechnungen/2027-00001/anfordern",
        data={"csrf_token": angemeldet.csrf},
        follow_redirects=False,
    )
    r2 = angemeldet.post(
        "/rechnungen/2027-00001/anfordern",
        data={"csrf_token": angemeldet.csrf},
        follow_redirects=False,
    )
    assert r1.headers["location"] == r2.headers["location"]
    assert len(db.scalars(select(Anfrage)).all()) == 1


def test_antwort_wird_einmal_link(angemeldet: TestClient, db: Session) -> None:
    a = _bereitstellen(db, angemeldet.konto_id)
    assert "pdf_base64" not in a.antwort_json and a.antwort_json["link_token"]
    ziel = angemeldet.get(f"/anfrage/{a.id}", follow_redirects=False).headers["location"]
    assert ziel.startswith("/rechnung/")
    r = angemeldet.get(ziel)
    assert r.content == b"%PDF-1.7 test"
    assert r.headers["content-type"] == "application/pdf"
    assert 'filename="Rechnung-2027-00001.pdf"' in r.headers["content-disposition"]
    assert r.headers["cache-control"] == "no-store"
    zweiter = angemeldet.get(ziel)
    assert zweiter.status_code == 404 and "abgelaufen oder wurde schon benutzt" in zweiter.text
    assert _tmp_pdfs() == []


def test_link_token_wird_nach_download_aus_anfrage_entfernt(
    angemeldet: TestClient, db: Session
) -> None:
    """Controller-Ruling Fix-Runde 1: Nach dem Einlösen darf `antwort_json["link_token"]` nicht
    mehr auf den (jetzt gelöschten) Link zeigen – `anfragen.stand()` läse sonst weiter einen
    toten Link. Fix-Runde 2: stattdessen steht dort `rechnung_abgerufen`, damit `stand()` den
    Fall von einem echten Fehlschlag unterscheiden kann."""
    a = _bereitstellen(db, angemeldet.konto_id)
    ziel = angemeldet.get(f"/anfrage/{a.id}", follow_redirects=False).headers["location"]
    angemeldet.get(ziel)
    db.refresh(a)
    assert "link_token" not in a.antwort_json
    assert a.antwort_json["status"] == "ok"
    assert a.antwort_json["rechnung_abgerufen"] is True


def test_stand_nach_download_zeigt_abgerufen_statt_abgelehnt(
    angemeldet: TestClient, db: Session
) -> None:
    """Controller-Ruling Fix-Runde 2: `warten.js` pollt nach `location.assign` auf den
    Download-Link weiter (ein Attachment entlädt die Seite nicht) – ohne einen eigenen Zustand
    zeigte `/anfrage/<id>` (bzw. dessen `/stand`) zwei Sekunden nach einem erfolgreichen
    Download fälschlich „Das hat nicht geklappt“. Der neue Zustand leitet nicht weiter."""
    a = _bereitstellen(db, angemeldet.konto_id)
    ziel = angemeldet.get(f"/anfrage/{a.id}", follow_redirects=False).headers["location"]
    angemeldet.get(ziel)  # Download verbraucht den Link.

    seite = angemeldet.get(f"/anfrage/{a.id}", follow_redirects=False)
    assert seite.status_code == 200
    assert "heruntergeladen" in seite.text.lower()
    assert "/rechnungen" in seite.text

    stand = angemeldet.get(f"/anfrage/{a.id}/stand").json()
    assert stand["zustand"] == "abgerufen"
    assert stand["ziel"] is None


def test_erneut_anfordern_nach_download_erzeugt_neue_anfrage(
    angemeldet: TestClient, db: Session
) -> None:
    """Controller-Ruling Fix-Runde 1: `bestehende(nur_offen=True)` darf eine schon beantwortete
    (und damit ggf. schon heruntergeladene) Anfrage nie wiederverwenden – sonst würde ein
    erneutes Anfordern bis zu zwei Minuten lang auf den verbrauchten, toten Link umleiten."""
    a = _bereitstellen(db, angemeldet.konto_id)
    ziel = angemeldet.get(f"/anfrage/{a.id}", follow_redirects=False).headers["location"]
    angemeldet.get(ziel)  # Download verbraucht den Link.
    r = angemeldet.post(
        "/rechnungen/2027-00001/anfordern",
        data={"csrf_token": angemeldet.csrf},
        follow_redirects=False,
    )
    neu = db.scalar(select(Anfrage).where(Anfrage.id != a.id))
    assert neu is not None
    assert r.headers["location"] == f"/anfrage/{neu.id}"


def test_gleichzeitiger_abruf_nur_einer_bekommt_pdf(angemeldet: TestClient, db: Session) -> None:
    """Controller-Ruling Fix-Runde 1: Zwei gleichzeitige Abrufe desselben Tokens – genau einer
    bekommt das PDF (200), der andere 404 (Zeilensperre in `rechnung_link.einloesen`)."""
    a = _bereitstellen(db, angemeldet.konto_id)
    ziel = angemeldet.get(f"/anfrage/{a.id}", follow_redirects=False).headers["location"]

    ergebnisse: list[int] = []
    schranke = threading.Barrier(2)

    def _abrufen() -> None:
        schranke.wait()
        ergebnisse.append(angemeldet.get(ziel).status_code)

    threads = [threading.Thread(target=_abrufen) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(ergebnisse) == [200, 404]


def test_link_nur_fuer_eigenes_konto(angemeldet: TestClient, db: Session) -> None:
    fremd = Konto(email="b@x.de", anzeigename="B")
    db.add(fremd)
    db.commit()
    token = rechnung_link.lege_an(
        db, konto_id=fremd.id, rechnung_nr="2027-00002", pdf=b"%PDF", jetzt=uhr.jetzt()
    )
    db.commit()
    assert angemeldet.get(f"/rechnung/{token}").status_code == 404
    assert len(_tmp_pdfs()) == 1


def test_datei_und_ordner_rechte(angemeldet: TestClient, db: Session) -> None:
    """Controller-Ruling: PDF in DATA_DIR/rechnungen_tmp, Ordner 0700, Dateien 0600 – ein
    anderer Systembenutzer darf die (kurzzeitig gespeicherten) Rechnungen nicht lesen."""
    rechnung_link.lege_an(
        db, konto_id=angemeldet.konto_id, rechnung_nr="9", pdf=b"%PDF", jetzt=uhr.jetzt()
    )
    db.commit()
    ordner = settings.data_dir / "rechnungen_tmp"
    [pdf] = _tmp_pdfs()
    assert oct(ordner.stat().st_mode)[-3:] == "700"
    assert oct(pdf.stat().st_mode)[-3:] == "600"


def test_link_laeuft_nach_10_minuten_ab(angemeldet: TestClient, db: Session, uhr_steht) -> None:
    a = _bereitstellen(db, angemeldet.konto_id)
    ziel = angemeldet.get(f"/anfrage/{a.id}", follow_redirects=False).headers["location"]
    uhr_steht.weiter(minutes=11)
    assert angemeldet.get(ziel).status_code == 404


def test_pdf_zu_gross_wird_fehler_statt_absturz(
    angemeldet: TestClient, db: Session, uhr_steht
) -> None:
    """Controller-Ruling: pdf_base64 validieren, Größenlimit – sonst Zustand `fehler` statt 500;
    kein Link, keine Datei wird angelegt."""
    speichere(db, f"konto:{KUNDE_ID}", konto(rechnungen=[RECHNUNG]))
    a = anfragen.stelle(
        db,
        typ="rechnung_anfordern",
        konto_id=angemeldet.konto_id,
        nutzlast={"rechnung_nr": "2027-00001"},
    )
    zu_gross = base64.b64encode(b"x" * (rechnung_link.MAX_PDF_BYTES + 1)).decode()
    antwort = kanal.Antwort(status="ok", pdf_base64=zu_gross, dateiname="gross.pdf")
    anfragen.beantworte(db, a.id, antwort, uhr.jetzt())
    db.commit()
    db.refresh(a)
    assert a.antwort_json["status"] == "fehler"
    assert "link_token" not in a.antwort_json
    assert _tmp_pdfs() == []


def test_pdf_base64_zu_lang_wird_ohne_dekodieren_abgelehnt(
    angemeldet: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Controller-Ruling Fix-Runde 2: Die Vorprüfung vor dem Dekodieren wirklich mit einer
    Zeichenkette über `MAX_PDF_BASE64_LEN` auslösen (der bisherige Größentest landete rechnerisch
    im Post-Decode-Zweig) und belegen, dass `base64.b64decode` dabei gar nicht erst aufgerufen
    wird."""
    speichere(db, f"konto:{KUNDE_ID}", konto(rechnungen=[RECHNUNG]))
    a = anfragen.stelle(
        db,
        typ="rechnung_anfordern",
        konto_id=angemeldet.konto_id,
        nutzlast={"rechnung_nr": "2027-00001"},
    )
    aufrufe: list[int] = []
    original = base64.b64decode

    def _mitzaehlen(*args: object, **kwargs: object) -> bytes:
        aufrufe.append(1)
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(anfragen.base64, "b64decode", _mitzaehlen)
    zu_lang = "A" * (rechnung_link.MAX_PDF_BASE64_LEN + 1)
    antwort = kanal.Antwort(status="ok", pdf_base64=zu_lang, dateiname="x.pdf")
    anfragen.beantworte(db, a.id, antwort, uhr.jetzt())
    db.commit()
    db.refresh(a)
    assert a.antwort_json["status"] == "fehler"
    assert aufrufe == []
    assert _tmp_pdfs() == []


def test_pdf_ungueltiges_base64_wird_fehler(angemeldet: TestClient, db: Session) -> None:
    speichere(db, f"konto:{KUNDE_ID}", konto(rechnungen=[RECHNUNG]))
    a = anfragen.stelle(
        db,
        typ="rechnung_anfordern",
        konto_id=angemeldet.konto_id,
        nutzlast={"rechnung_nr": "2027-00001"},
    )
    antwort = kanal.Antwort(status="ok", pdf_base64="!!nicht-base64!!", dateiname="x.pdf")
    anfragen.beantworte(db, a.id, antwort, uhr.jetzt())
    db.commit()
    db.refresh(a)
    assert a.antwort_json["status"] == "fehler"
    assert _tmp_pdfs() == []


def test_pdf_speichern_schlaegt_fehl_wird_fehler_statt_absturz(
    angemeldet: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Controller-Ruling Fix-Runde 1: Ein OSError aus `rechnung_link.lege_an` (z. B. Platte voll)
    darf `anfragen.beantworte` nicht mit einer Exception abbrechen lassen – sonst würde
    `POST /core/antworten` mit 500 den ganzen Stapel an Antworten verwerfen."""
    speichere(db, f"konto:{KUNDE_ID}", konto(rechnungen=[RECHNUNG]))
    a = anfragen.stelle(
        db,
        typ="rechnung_anfordern",
        konto_id=angemeldet.konto_id,
        nutzlast={"rechnung_nr": "2027-00001"},
    )

    def _kaputt(*args: object, **kwargs: object) -> str:
        raise OSError("Platte voll")

    monkeypatch.setattr(rechnung_link, "lege_an", _kaputt)
    antwort = kanal.Antwort(
        status="ok", pdf_base64=base64.b64encode(b"%PDF").decode(), dateiname="x.pdf"
    )
    ok = anfragen.beantworte(db, a.id, antwort, uhr.jetzt())
    db.commit()
    db.refresh(a)
    assert ok is True
    assert a.antwort_json["status"] == "fehler"


def test_anfordern_zu_lange_nummer_wird_abgelehnt_statt_500(
    angemeldet: TestClient, db: Session
) -> None:
    """Controller-Ruling Fix-Runde 1: Die Mitgliedschaftsprüfung schaut auch auf die Länge (≤ 20,
    Grenze von `kanal.RechnungAnfordern`) – sonst würfe `model_validate` weiter unten eine rohe
    ValidationError (500), falls der Lesestand je eine zu lange Nummer enthielte."""
    lang = "2" * 25
    speichere(db, f"konto:{KUNDE_ID}", konto(rechnungen=[{**RECHNUNG, "nummer": lang}]))
    r = angemeldet.post(
        f"/rechnungen/{lang}/anfordern",
        data={"csrf_token": angemeldet.csrf},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/rechnungen"
    assert db.scalar(select(Anfrage)) is None


def test_aufraeumen(db: Session, uhr_steht) -> None:
    jetzt = uhr_steht.jetzt
    k = Konto(email="a@x.de", anzeigename="A", kunde_id=KUNDE_ID)
    db.add(k)
    db.commit()
    rechnung_link.lege_an(
        db, konto_id=k.id, rechnung_nr="1", pdf=b"x", jetzt=jetzt - timedelta(minutes=11)
    )
    rechnung_link.lege_an(db, konto_id=k.id, rechnung_nr="2", pdf=b"y", jetzt=jetzt)
    db.commit()
    waise = settings.data_dir / "rechnungen_tmp" / "waise.pdf"
    waise.write_bytes(b"z")
    os.utime(waise, (0, 0))
    db.add(
        Anfrage(
            typ="konto_loeschen",
            nutzlast_json={},
            erstellt_am=jetzt - timedelta(days=40),
            status="beantwortet",
            beantwortet_am=jetzt - timedelta(days=31),
        )
    )
    db.add(Anfrage(typ="konto_loeschen", nutzlast_json={}, erstellt_am=jetzt, status="offen"))
    db.commit()
    speichere(db, f"konto:{KUNDE_ID}", konto())
    speichere(db, f"konto:{uuid.uuid4()}", konto())  # Konto gibt es im Portal nicht (mehr)
    n = jobs.aufraeumen(db, jetzt)
    assert n["rechnung_links"] == 1 and n["waisen"] == 1
    assert n["anfragen"] == 1 and n["lesestand"] == 1
    assert len(_tmp_pdfs()) == 1
    assert db.get(Lesestand, f"konto:{KUNDE_ID}") is not None
    assert len(db.scalars(select(Anfrage)).all()) == 1


def test_aufraeumen_code_fehlversuch_sitzung_login_token_webhook(db: Session, uhr_steht) -> None:
    """Controller-Ruling (Task 15, für Task 10 vorgemerkt): `aufraeumen` löscht zusätzlich
    `code_fehlversuch` älter als 24 h, abgelaufene Sitzungen und Login-Tokens – je Tabelle
    ein Test mit einer zu löschenden und einer zu behaltenden Zeile."""
    jetzt = uhr_steht.jetzt
    k = Konto(email="d@x.de", anzeigename="D")
    db.add(k)
    db.commit()

    db.add(CodeFehlversuch(email="d@x.de", versucht_am=jetzt - timedelta(hours=25)))
    db.add(CodeFehlversuch(email="d@x.de", versucht_am=jetzt - timedelta(hours=1)))

    db.add(
        LoginToken(
            email="d@x.de",
            token_hash="abgelaufen-token",
            code_hash="abgelaufen-code",
            laeuft_ab=jetzt - timedelta(minutes=1),
        )
    )
    db.add(
        LoginToken(
            email="d@x.de",
            token_hash="gueltig-token",
            code_hash="gueltig-code",
            laeuft_ab=jetzt + timedelta(minutes=1),
        )
    )

    db.add(
        Sitzung(
            konto_id=k.id,
            token_hash="abgelaufene-sitzung",
            csrf_token="a",
            laeuft_ab=jetzt - timedelta(days=1),
        )
    )
    db.add(
        Sitzung(
            konto_id=k.id,
            token_hash="gueltige-sitzung",
            csrf_token="b",
            laeuft_ab=jetzt + timedelta(days=1),
        )
    )

    db.add(WebhookEingang(provider="fake", rohdaten="{}", empfangen_am=jetzt - timedelta(days=31)))
    db.add(WebhookEingang(provider="fake", rohdaten="{}", empfangen_am=jetzt))
    db.commit()

    n = jobs.aufraeumen(db, jetzt)

    assert n["code_fehlversuch"] == 1
    assert n["login_token"] == 1
    assert n["sitzungen"] == 1
    assert n["webhooks"] == 1
    assert len(db.scalars(select(CodeFehlversuch)).all()) == 1
    assert len(db.scalars(select(LoginToken)).all()) == 1
    assert len(db.scalars(select(Sitzung)).all()) == 1
    assert len(db.scalars(select(WebhookEingang)).all()) == 1


def test_aufraeumen_alte_offene_anfrage_bleibt(db: Session, uhr_steht) -> None:
    """Controller-Ruling Fix-Runde 1: Eine alte, aber weiterhin offene Anfrage (das Hauptsystem
    hat nie geantwortet) darf nicht verfallen – nur `beantwortet` löst die 30-Tage-Frist aus."""
    jetzt = uhr_steht.jetzt
    alt_offen = Anfrage(
        typ="konto_loeschen",
        nutzlast_json={},
        erstellt_am=jetzt - timedelta(days=40),
        status="offen",
    )
    db.add(alt_offen)
    db.commit()
    jobs.aufraeumen(db, jetzt)
    assert db.get(Anfrage, alt_offen.id) is not None


def test_aufraeumen_ein_schritt_scheitert_andere_trotzdem(
    db: Session, uhr_steht, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Controller-Ruling Fix-Runde 1: Jeder Schritt läuft in seiner eigenen Transaktion –
    scheitert einer dauerhaft (hier `login_token`, per Monkeypatch simuliert), löschen die
    übrigen, unabhängigen Schritte trotzdem."""
    jetzt = uhr_steht.jetzt
    db.add(
        LoginToken(
            email="e@x.de",
            token_hash="kaputter-schritt-token",
            code_hash="kaputter-schritt-code",
            laeuft_ab=jetzt - timedelta(minutes=1),
        )
    )
    db.add(CodeFehlversuch(email="e@x.de", versucht_am=jetzt - timedelta(hours=25)))
    db.commit()

    def _kaputt(*args: object, **kwargs: object) -> int:
        raise RuntimeError("defekt")

    monkeypatch.setattr(jobs, "_login_token", _kaputt)

    n = jobs.aufraeumen(db, jetzt)

    assert n["login_token"] == 0
    assert n["code_fehlversuch"] == 1
    assert len(db.scalars(select(LoginToken)).all()) == 1  # Schritt scheiterte, nichts gelöscht
    assert len(db.scalars(select(CodeFehlversuch)).all()) == 0  # anderer Schritt lief trotzdem


def test_aufraeumen_zweiter_gleichzeitiger_lauf_wird_uebersprungen(db: Session, uhr_steht) -> None:
    """Controller-Ruling Fix-Runde 1: `pg_try_advisory_lock` schützt vor einem gleichzeitigen
    zweiten Lauf – hält eine andere Verbindung die Sperre, überspringt sich dieser Aufruf.

    Fix-Runde 2: Die hier gehaltene Sperre wird per try/finally freigegeben – schlägt eine
    Assertion fehl oder wirft `jobs.aufraeumen` unerwartet, bliebe sie sonst über das Testende
    hinaus bestehen und ließe spätere Tests fälschlich überspringen (genau der Fehler, der beim
    ursprünglichen Lock-Leak in `jobs.aufraeumen` selbst auftrat)."""
    with engine.connect() as andere_verbindung:
        andere_verbindung.execute(text("SELECT pg_try_advisory_lock(:id)"), {"id": jobs._LOCK_ID})
        andere_verbindung.commit()
        try:
            n = jobs.aufraeumen(db, uhr_steht.jetzt)
        finally:
            andere_verbindung.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": jobs._LOCK_ID})
            andere_verbindung.commit()
    assert n == {}
