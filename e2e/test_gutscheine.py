"""Gutscheine über Portal-Briefkasten, HTTP-Zahlung und signierten Kontolesestand.

Verkaufs- und Codeformulare folgen in Stufe 2. Bis dahin stellt der Test deren
validierte Wünsche mit dem vorhandenen Portal-Anfragedienst in den Briefkasten.
Fachliche Kauf-, Buchungs- und Stornodienste werden niemals direkt aufgerufen.
"""

import re
from datetime import date, time
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import pytest
from beachhub_core import clock
from beachhub_core.database import SessionLocal as CoreSession
from beachhub_core.kanal import Kanal
from beachhub_core.models import (
    Betriebszeit,
    Feld,
    FeldRaster,
    Gutschein,
    GutscheinEinloesung,
    Rechnung,
    Tarif,
)
from beachhub_core.services import konfiguration
from beachhub_core.services import lesestand as core_lesestand
from beachhub_portal import uhr
from beachhub_portal.config import settings as portal_settings
from beachhub_portal.database import SessionLocal as PortalSession
from beachhub_portal.main import app
from beachhub_portal.models import Anfrage, Konto, Lesestand
from beachhub_portal.services import anfragen, lesestand
from beachhub_shared import kanal, signatur
from beachhub_shared.lesestand import KontoInhalt
from beachhub_shared.zeit import kombiniere
from fastapi.testclient import TestClient
from sqlalchemy import select

JETZT = kombiniere(date(2026, 10, 6), time(12))
BEGINN = kombiniere(date(2026, 10, 8), time(19))
ENDE = kombiniere(date(2026, 10, 8), time(20))


@pytest.fixture(autouse=True)
def feste_uhr(monkeypatch):
    monkeypatch.setattr(clock, "now", lambda db: JETZT)
    monkeypatch.setattr(uhr, "jetzt", lambda: JETZT)


def _csrf(browser, pfad):
    return re.search(r'name="csrf_token" value="([^"]+)"', browser.get(pfad).text).group(1)


def _konto(browser, hauptsystem, mails, email, name):
    csrf = _csrf(browser, "/anmelden")
    assert browser.post("/anmelden", data={"email": email, "csrf_token": csrf}).status_code == 200
    code = re.search(r"Anmeldeseite ein: (\d{6})", mails["portal"][-1]["text"]).group(1)
    browser.post("/anmelden/code", data={"email": email, "code": code, "csrf_token": csrf})
    csrf = _csrf(browser, "/willkommen")
    browser.post("/willkommen", data={"anzeigename": name, "csrf_token": csrf})
    assert hauptsystem.abholen() == 1
    with PortalSession() as db:
        k = db.scalar(select(Konto).where(Konto.email == email))
        assert k.kunde_id is not None
        return k.id, k.kunde_id, csrf


def _wunsch(hauptsystem, konto_id, typ, nutzlast):
    with PortalSession() as db:
        aid = anfragen.stelle(db, typ=typ, konto_id=konto_id, nutzlast=nutzlast).id
    assert hauptsystem.abholen() == 1
    with PortalSession() as db:
        a = db.get(Anfrage, aid)
        assert a.status == Anfrage.BEANTWORTET
        return kanal.Antwort.model_validate(a.antwort_json)


def _bestand(kunde_id) -> KontoInhalt:
    """Prüft das wirklich übertragene Dokument, einschließlich Signatur und Version."""
    dok = core_lesestand.lade(f"konto:{kunde_id}")
    assert dok is not None
    assert signatur.pruefe(
        dok.model_dump(mode="json", exclude={"signatur"}),
        dok.signatur,
        portal_settings.core_public_key,
    )
    with PortalSession() as db:
        gespeichert = db.get(Lesestand, dok.dokument)
        assert gespeichert.version == dok.version and gespeichert.signatur == dok.signatur
        assert gespeichert.inhalt_json == dok.inhalt
        return lesestand.konto(db, kunde_id)


def _angebot(hauptsystem, modell, steuer):
    with CoreSession() as db:
        f = Feld(name="Feld 1", reihenfolge=1)
        f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
        db.add_all([f, Tarif(name="Stunde", preis=Decimal("30.00"))])
        for wt in range(7):
            db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
        for key, value in {
            "gutschein_modell": modell,
            "gutschein_steuer": steuer,
            "gutschein_minuten": 60,
            "gutschein_preis_nichtmitglied": Decimal("26.00"),
        }.items():
            konfiguration.setze(db, key, value)
        core_lesestand.markiere_geaendert(db, "belegung", "tarife")
        db.commit()
        fid = f.id
    hauptsystem.verteilen()
    return fid


def _kauf(browser, hauptsystem, mails, buyer, other, modell, steuer):
    konto_id, kid, _ = buyer
    antwort = _wunsch(
        hauptsystem, konto_id, "gutschein_kaufen", {"betrag": "50.00"} if modell == "wert" else {}
    )
    assert antwort.status == "reserviert"
    assert antwort.preis == Decimal("50.00" if modell == "wert" else "26.00")
    offen = _bestand(kid)
    assert offen.gutschein_kauf
    assert offen.gutscheine == [] and len(offen.gutschein_kaeufe) == 1
    assert offen.gutschein_kaeufe[0].checkout_url == antwort.checkout_url
    fremd = _bestand(other[1])
    assert fremd.gutscheine == fremd.gutschein_kaeufe == []
    with CoreSession() as db:
        g = db.scalar(select(Gutschein))
        geheim = g.code
        assert g.gueltig_bis is None and g.rechnung_id is None
    for daten in (
        antwort.model_dump_json(),
        offen.model_dump_json(),
        fremd.model_dump_json(),
        str(mails["core"]),
    ):
        assert geheim not in daten
        assert f"{geheim[:4]}-{geheim[4:8]}-{geheim[8:]}" not in daten
    # HTTP-Testzahlung erzeugt die Provider-Rückmeldung; der Kanal verifiziert sie.
    teile = urlsplit(antwort.checkout_url)
    query = parse_qs(teile.query)
    assert (
        browser.post(
            teile.path,
            data={
                "ergebnis": "bezahlt",
                "betrag": query["betrag"][0],
                "zurueck": query["zurueck"][0],
            },
            follow_redirects=False,
        ).status_code
        == 303
    )
    assert hauptsystem.abholen() == 1
    bezahlt = _bestand(kid)
    assert bezahlt.gutschein_kaeufe == [] and len(bezahlt.gutscheine) == 1
    code = bezahlt.gutscheine[0].code
    assert bezahlt.gutscheine[0].gueltig_bis == date(2028, 10, 5)
    with CoreSession() as db:
        g = db.scalar(select(Gutschein))
        r = db.get(Rechnung, g.rechnung_id)
        assert (g.modell, g.steuer) == (modell, steuer)
        assert r.kunde_id == kid and r.status == "bezahlt"
        assert r.brutto == Decimal("50.00" if modell == "wert" else "26.00")
        assert r.ust == (
            Decimal("0.00")
            if steuer == "bei_einloesung"
            else Decimal("7.98" if modell == "wert" else "4.15")
        )
        assert any(
            m["an"] == "anna@example.org" and m["anhaenge"] == [r.nummer + ".pdf"]
            for m in mails["core"]
        )
        return code, g.id, r.nummer


def _pdf(hauptsystem, browser, konto_id, nr):
    antwort = _wunsch(hauptsystem, konto_id, "rechnung_anfordern", {"rechnung_nr": nr})
    assert antwort.status == "ok"
    with PortalSession() as db:
        a = db.scalar(
            select(Anfrage)
            .where(Anfrage.typ == "rechnung_anfordern", Anfrage.konto_id == konto_id)
            .order_by(Anfrage.beantwortet_am.desc(), Anfrage.id.desc())
        )
        token = a.antwort_json["link_token"]
    assert browser.get(f"/rechnung/{token}").content.startswith(b"%PDF")


def _buchung(hauptsystem, konto_id, fid, code):
    antwort = _wunsch(
        hauptsystem,
        konto_id,
        "buchung_anfragen",
        {
            "feld_id": str(fid),
            "beginn": BEGINN.isoformat(),
            "ende": ENDE.isoformat(),
            "gutschein_codes": [code],
        },
    )
    assert antwort.status == "bestaetigt" and antwort.gutschein_verrechnet == Decimal("30.00")
    with CoreSession() as db:
        einloesung = db.scalar(
            select(GutscheinEinloesung).where(GutscheinEinloesung.buchung_id == antwort.buchung_id)
        )
        assert einloesung.status == "bestaetigt"
    return antwort.buchung_id


def _storno(hauptsystem, browser, csrf, bid):
    response = browser.post(
        f"/buchungen/{bid}/stornieren", data={"csrf_token": csrf}, follow_redirects=False
    )
    assert response.status_code == 303
    assert hauptsystem.abholen() == 1
    assert (
        browser.get(response.headers["location"], follow_redirects=False).headers["location"]
        == "/buchungen?meldung=storniert_kostenfrei"
    )


@pytest.mark.parametrize("modell", ["einheit", "wert"])
@pytest.mark.parametrize("steuer", ["beim_kauf", "bei_einloesung"])
def test_konto_kauf_zahlung_bestand_buchung_storno(
    browser: TestClient, hauptsystem: Kanal, mails, modell, steuer
):
    """Fängt fehlende Kanalabwicklung, falsche Belegbasis und doppelte Wertrückgabe ab."""
    fid = _angebot(hauptsystem, modell, steuer)
    buyer = _konto(browser, hauptsystem, mails, "anna@example.org", "Anna")
    with TestClient(app) as anderer_browser:
        other = _konto(anderer_browser, hauptsystem, mails, "bert@example.org", "Bert")
    code, gid, kauf_nr = _kauf(browser, hauptsystem, mails, buyer, other, modell, steuer)
    _pdf(hauptsystem, browser, buyer[0], kauf_nr)
    bid = _buchung(hauptsystem, buyer[0], fid, code)
    _storno(hauptsystem, browser, buyer[2], bid)
    stand = _bestand(buyer[1])
    assert stand.guthaben == Decimal("30.00" if modell == "wert" else "0.00")
    assert len(stand.gutscheine) == 1 and stand.gutscheine[0].gueltig_bis == date(2028, 10, 5)
    assert stand.gutscheine[0].restwert == (Decimal("20.00") if modell == "wert" else None)
    assert stand.gutscheine[0].verbleibende_einloesungen == (1 if modell == "einheit" else None)
    with CoreSession() as db:
        e = db.scalar(select(GutscheinEinloesung).where(GutscheinEinloesung.gutschein_id == gid))
        assert e.status == "rueckgaengig"
        gegen = db.get(Rechnung, e.korrektur_rechnung_id) if e.korrektur_rechnung_id else None
        assert (gegen is not None) == (modell == "wert" or steuer == "bei_einloesung")
        if gegen:
            assert gegen.brutto == Decimal("-30.00" if modell == "wert" else "-26.00")


@pytest.mark.parametrize("steuer", ["beim_kauf", "bei_einloesung"])
def test_geschenkter_wert_einloeserguthaben_und_fremde_belegrechte(
    browser, hauptsystem, mails, steuer
):
    """Fängt Käuferguthaben, erhöhten Restwert und fremde Kauf-PDF-Freigabe ab."""
    fid = _angebot(hauptsystem, "wert", steuer)
    buyer = _konto(browser, hauptsystem, mails, "anna@example.org", "Anna")
    with TestClient(app) as redeemer_browser:
        redeemer = _konto(redeemer_browser, hauptsystem, mails, "bert@example.org", "Bert")
        code, gid, kauf_nr = _kauf(browser, hauptsystem, mails, buyer, redeemer, "wert", steuer)
        bid = _buchung(hauptsystem, redeemer[0], fid, code)
        assert _bestand(buyer[1]).gutscheine == []
        assert _bestand(redeemer[1]).gutscheine[0].restwert == Decimal("20.00")
        assert (
            _wunsch(hauptsystem, redeemer[0], "rechnung_anfordern", {"rechnung_nr": kauf_nr}).status
            == "abgelehnt"
        )
        _storno(hauptsystem, redeemer_browser, redeemer[2], bid)
        b = _bestand(redeemer[1])
        a = _bestand(buyer[1])
        assert (a.guthaben, b.guthaben) == (Decimal("0.00"), Decimal("30.00"))
        assert a.gutscheine == []
        assert len(b.gutscheine) == 1 and b.gutscheine[0].restwert == Decimal("20.00")
        assert b.gutscheine[0].gueltig_bis == date(2028, 10, 5)
        with CoreSession() as db:
            g = db.get(Gutschein, gid)
            assert g.restwert == Decimal("20.00")
            e = db.scalar(
                select(GutscheinEinloesung).where(GutscheinEinloesung.gutschein_id == gid)
            )
            gegen = db.get(Rechnung, e.korrektur_rechnung_id)
            assert gegen.kunde_id == (buyer[1] if steuer == "beim_kauf" else redeemer[1])
            gegen_nr = gegen.nummer
        if steuer == "beim_kauf":
            assert (
                _wunsch(
                    hauptsystem, redeemer[0], "rechnung_anfordern", {"rechnung_nr": gegen_nr}
                ).status
                == "abgelehnt"
            )
            assert all(
                gegen_nr + ".pdf" not in m["anhaenge"]
                for m in mails["core"]
                if m["an"] == "bert@example.org"
            )
