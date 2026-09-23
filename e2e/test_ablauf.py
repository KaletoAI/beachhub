"""Der ganze Weg eines Kunden: anmelden, buchen, bezahlen, PIN sehen, Rechnung laden, stornieren.

Dazu (Controller-Ruling Task 11) der Gleichlauf zwischen dem im Portal angezeigten Preis und dem
vom Hauptsystem beim Buchen reservierten Preis über mehrere Tarif-Regelkonstellationen, und ein
kurzer Vertragstest, dass der Hallenplan nie ans Portal geht.
"""

import re
import uuid
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from beachhub_core.database import SessionLocal as CoreSession
from beachhub_core.kanal import Kanal
from beachhub_core.models import (
    Betriebszeit,
    Buchung,
    Feld,
    FeldRaster,
    Kunde,
    Kundengruppe,
    Rechnung,
    Tarif,
    Zahlung,
)
from beachhub_core.services import lesestand, pin
from beachhub_portal import uhr as portal_uhr
from beachhub_shared import kanal as vertrag
from beachhub_shared.zeit import kombiniere, lokal, lokales_datum
from fastapi.testclient import TestClient
from sqlalchemy import select

EMAIL = "anna@example.org"


@pytest.fixture
def feld_id(hauptsystem: Kanal) -> uuid.UUID:
    """Ein Feld mit Stundenraster, 9–23 Uhr, 30 € – im Hauptsystem angelegt und verteilt."""
    with CoreSession() as db:
        f = Feld(name="Feld 1", reihenfolge=1)
        f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
        db.add_all([f, Kundengruppe(name="Privat"), Tarif(name="Std", preis=Decimal("30.00"))])
        for wt in range(7):
            db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
        db.commit()
        lesestand.markiere_geaendert(db, "belegung", "tarife")
        db.commit()
        ergebnis = f.id
    assert hauptsystem.verteilen() == 2
    return ergebnis


def _csrf(html: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def _anmelden(browser: TestClient, mails: dict, hauptsystem: Kanal) -> str:
    """Jeder anonyme Formular-POST braucht das Vor-Session-CSRF-Token aus einem vorherigen GET
    (Ruling Task 10: ohne dieses Token liefert /anmelden* 403 – Schutz gegen Login-CSRF). Das
    Token bleibt über beide Anmeldeschritte gültig, solange das zugehörige Cookie besteht; erst
    nach dem Login gilt das Token der echten Sitzung, das /willkommen neu abgeholt wird."""
    vor_csrf = _csrf(browser.get("/anmelden").text)
    browser.post("/anmelden", data={"email": EMAIL, "csrf_token": vor_csrf})
    code = re.search(r"Anmeldeseite ein: (\d{6})", mails["portal"][-1]["text"]).group(1)
    browser.post("/anmelden/code", data={"email": EMAIL, "code": code, "csrf_token": vor_csrf})
    csrf = _csrf(browser.get("/willkommen").text)
    browser.post("/willkommen", data={"anzeigename": "Anna", "csrf_token": csrf})
    assert hauptsystem.abholen() == 1
    return csrf


def _termin() -> tuple[datetime, datetime]:
    tag = lokales_datum(datetime.now(UTC)) + timedelta(days=2)
    return kombiniere(tag, time(19)), kombiniere(tag, time(20))


def _buchen(browser: TestClient, csrf: str, feld_id: uuid.UUID) -> str:
    beginn, ende = _termin()
    r = browser.post(
        "/buchen",
        data={
            "feld": str(feld_id),
            "beginn": beginn.isoformat(),
            "ende": ende.isoformat(),
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    return r.headers["location"]


def test_buchen_bezahlen_rechnung_stornieren(
    browser: TestClient, hauptsystem: Kanal, feld_id: uuid.UUID, mails: dict
) -> None:
    csrf = _anmelden(browser, mails, hauptsystem)
    with CoreSession() as db:
        kunde = db.scalar(select(Kunde))
        assert kunde.name == "Anna" and kunde.email == EMAIL and kunde.portal_konto_id
    beginn, _ = _termin()
    assert "30,00 €" in browser.get("/", params={"tag": lokales_datum(beginn).isoformat()}).text

    # Buchen: Hauptsystem reserviert und schickt zur (Fake-)Zahlung
    warteseite = _buchen(browser, csrf, feld_id)
    assert hauptsystem.abholen() == 1
    zahlseite = browser.get(warteseite, follow_redirects=False).headers["location"]
    assert zahlseite.startswith("/test-zahlung/fake_")
    teile = urlsplit(zahlseite)
    query = parse_qs(teile.query)
    assert query["betrag"] == ["30.00"]
    assert "Bezahlen" in browser.get(zahlseite).text
    r = browser.post(
        teile.path,
        data={
            "ergebnis": "bezahlt",
            "betrag": "30.00",
            "zurueck": query["zurueck"][0],
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    anfrage_url = browser.get(r.headers["location"], follow_redirects=False).headers["location"]
    assert "Zur Zahlung" in browser.get(anfrage_url).text  # Hauptsystem hat noch nicht geprüft
    assert hauptsystem.abholen() == 1

    # Bestätigt: PIN im Portal ist die PIN des Hauptsystems
    r = browser.get(anfrage_url, follow_redirects=False)
    assert r.headers["location"] == "/buchungen?meldung=bestaetigt"
    pin_im_portal = re.search(r'class="pin">(\d{6})<', browser.get("/buchungen").text).group(1)
    with CoreSession() as db:
        b = db.scalar(select(Buchung))
        assert b.status == "bestaetigt"
        assert pin.entschluessele(b.pin_verschluesselt) == pin_im_portal
        rechnung_nr = db.scalar(select(Rechnung)).nummer
        buchung_id = b.id
    assert any(m["betreff"].startswith("Buchung bestätigt") for m in mails["core"])

    # Rechnung über den Einmal-Link
    r = browser.post(
        f"/rechnungen/{rechnung_nr}/anfordern", data={"csrf_token": csrf}, follow_redirects=False
    )
    anfrage_url = r.headers["location"]
    assert hauptsystem.abholen() == 1
    link = browser.get(anfrage_url, follow_redirects=False).headers["location"]
    assert browser.get(link).content.startswith(b"%PDF")
    assert browser.get(link).status_code == 404

    # Storno mehr als 24 h vorher: kostenfrei, der Betrag wird Guthaben
    r = browser.post(
        f"/buchungen/{buchung_id}/stornieren", data={"csrf_token": csrf}, follow_redirects=False
    )
    anfrage_url = r.headers["location"]
    assert hauptsystem.abholen() == 1
    r = browser.get(anfrage_url, follow_redirects=False)
    assert r.headers["location"] == "/buchungen?meldung=storniert_kostenfrei"
    assert "storniert (kostenfrei)" in browser.get("/buchungen").text
    with CoreSession() as db:
        assert db.scalar(select(Kunde)).guthaben == Decimal("30.00")


def test_verlorene_antwort_wird_folgenlos_erneut_zugestellt(
    browser: TestClient,
    hauptsystem: Kanal,
    feld_id: uuid.UUID,
    mails: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    csrf = _anmelden(browser, mails, hauptsystem)
    warteseite = _buchen(browser, csrf, feld_id)

    original = hauptsystem.client.post

    def antworten_gehen_verloren(url: str, **kwargs):
        if url == "/core/antworten":
            raise httpx.ConnectError("Verbindung weg")
        return original(url, **kwargs)

    monkeypatch.setattr(hauptsystem.client, "post", antworten_gehen_verloren)
    with pytest.raises(httpx.ConnectError):
        hauptsystem.abholen()
    monkeypatch.setattr(hauptsystem.client, "post", original)

    # Nach 60 s liefert das Portal die unbeantwortete Anfrage erneut aus.
    spaeter = datetime.now(UTC) + timedelta(seconds=61)
    monkeypatch.setattr(portal_uhr, "jetzt", lambda: spaeter)
    assert hauptsystem.abholen() == 1
    with CoreSession() as db:
        assert len(db.scalars(select(Buchung)).all()) == 1
        assert len(db.scalars(select(Zahlung)).all()) == 1
    assert (
        browser.get(warteseite, follow_redirects=False)
        .headers["location"]
        .startswith("/test-zahlung/fake_")
    )


def _euro(betrag: Decimal) -> str:
    """Spiegelt den `euro`-Jinja-Filter des Portals (ohne das " €"-Suffix), um den erwarteten
    Anzeigetext für die Assertion zu bilden – siehe portal/beachhub_portal/templating.py."""
    return f"{betrag:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


@pytest.mark.parametrize(
    "szenario",
    ["feldregel", "wochentagregel", "uhrzeitregel", "gruppenregel", "gleichstand_neuere"],
)
def test_preis_gleichlauf_portal_hauptsystem(
    browser: TestClient, hauptsystem: Kanal, mails: dict, szenario: str
) -> None:
    """Controller-Ruling Task 11: Der im Portal angezeigte Preis (Buchen-Seite) muss dem vom
    Hauptsystem beim Buchen tatsächlich reservierten Preis entsprechen. Portal und Hauptsystem
    lösen den Tarif in bewusst getrennten, gleichlautenden Funktionen auf (N-1: kein
    core-Import im Portal; siehe portal/beachhub_portal/services/tarife.py) – hier wird der
    Gleichlauf über den echten Kanal geprüft, nicht nur je Seite isoliert. Jedes Szenario
    testet eine andere Art von Regel (Feld/Wochentag/Uhrzeit/Kundengruppe) bzw. den Gleichstand
    zweier gleich spezifischer Regeln, bei dem die zuletzt angelegte gewinnt."""
    beginn, ende = _termin()
    wochentag = lokales_datum(beginn).weekday()
    with CoreSession() as db:
        f = Feld(name="Feld 1", reihenfolge=1)
        f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
        privat = Kundengruppe(name="Privat")
        db.add_all([f, privat, Kundengruppe(name="Verein")])
        for wt in range(7):
            db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
        # Unspezifische Basisregel (Spezifität 0): Jedes Szenario muss stattdessen die
        # spezifischere, im Folgenden angelegte Regel wählen.
        db.add(Tarif(name="Basis", preis=Decimal("25.00")))
        if szenario == "feldregel":
            db.add(Tarif(name="Feldregel", preis=Decimal("40.00"), feld_id=f.id))
            erwartet = Decimal("40.00")
        elif szenario == "wochentagregel":
            db.add(Tarif(name="Wochentagregel", preis=Decimal("35.00"), wochentag=wochentag))
            erwartet = Decimal("35.00")
        elif szenario == "uhrzeitregel":
            db.add(
                Tarif(
                    name="Uhrzeitregel",
                    preis=Decimal("45.00"),
                    uhrzeit_von=time(18),
                    uhrzeit_bis=time(22),
                )
            )
            erwartet = Decimal("45.00")
        elif szenario == "gruppenregel":
            db.add(Tarif(name="Gruppenregel", preis=Decimal("20.00"), kundengruppe_id=privat.id))
            erwartet = Decimal("20.00")
        else:
            assert szenario == "gleichstand_neuere"
            # Zwei gleich spezifische Regeln (nur Wochentag): Die zuletzt angelegte gewinnt,
            # sowohl im Hauptsystem (sortiert nach created_at) als auch im Portal-Dokument
            # (sortiert nach Anlagereihenfolge) – siehe tarife.py auf beiden Seiten.
            db.add(
                Tarif(
                    name="Alt",
                    preis=Decimal("50.00"),
                    wochentag=wochentag,
                    created_at=datetime.now(UTC) - timedelta(minutes=1),
                )
            )
            db.add(
                Tarif(
                    name="Neu",
                    preis=Decimal("60.00"),
                    wochentag=wochentag,
                    created_at=datetime.now(UTC),
                )
            )
            erwartet = Decimal("60.00")
        db.commit()
        lesestand.markiere_geaendert(db, "belegung", "tarife")
        db.commit()
        feld_id = f.id
    assert hauptsystem.verteilen() == 2

    csrf = _anmelden(browser, mails, hauptsystem)

    # Angezeigter Preis auf der Buchen-Seite für genau den gebuchten Termin.
    seite = browser.get("/buchen", params={"feld": str(feld_id), "beginn": beginn.isoformat()}).text
    ende_uhr = lokal(ende).strftime("%H:%M")
    treffer = re.search(rf"bis {ende_uhr} Uhr – ([\d.,]+) €", seite)
    assert treffer is not None, seite
    angezeigt = Decimal(treffer.group(1).replace(".", "").replace(",", "."))
    assert angezeigt == erwartet, f"Portal zeigt {angezeigt}, erwartet {erwartet} ({szenario})"
    assert f"– {_euro(erwartet)} €" in seite

    # Vom Hauptsystem beim Buchen tatsächlich reservierter Preis (Betrag der Fake-Checkout-URL).
    warteseite = _buchen(browser, csrf, feld_id)
    assert hauptsystem.abholen() == 1
    zahlseite = browser.get(warteseite, follow_redirects=False).headers["location"]
    reserviert = Decimal(parse_qs(urlsplit(zahlseite).query)["betrag"][0])
    assert (
        reserviert == erwartet
    ), f"Hauptsystem reserviert {reserviert}, erwartet {erwartet} ({szenario})"
    assert reserviert == angezeigt


def test_hallenplan_wird_nie_ans_portal_gesendet() -> None:
    """Ledger-Vorgabe: `PORTAL_DOKUMENTE` ist eine Allowlist (belegung, tarife, konto:<id>); ein
    Hallenplan-Dokument (mit PIN-Hashes, Hallendienst-Feature) steht dort nicht und darf auch
    künftig nicht automatisch durchrutschen."""
    assert "hallenplan" not in vertrag.PORTAL_DOKUMENTE
    assert vertrag.fuer_portal("hallenplan") is False
