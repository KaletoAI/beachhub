from datetime import date, time
from decimal import Decimal

import pytest
from beachhub_core import clock, mail
from beachhub_core.models import (
    Betriebszeit,
    Buchung,
    Dauerbuchung,
    Feld,
    FeldRaster,
    Kunde,
    Rechnung,
    Sperre,
    Storno,
    Tarif,
)
from beachhub_core.services import kunden
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


@pytest.fixture
def welt(db: Session):
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    db.add_all([f, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    a = kunden.lege_an(db, name="A", email="a@x.de")
    v1 = kunden.lege_an(db, name="TSV", email="v@x.de", rechnungskunde=True)
    v1.mitglied_bis = date(2028, 4, 30)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    return f, a, v1


def test_woche_zeigt_slots_und_buchung(eingeloggt: TestClient, db: Session, welt) -> None:
    f, a, _ = welt
    c = eingeloggt
    seite = c.get(f"/admin/belegung?feld={f.id}&woche=2027-11-29")
    assert (
        seite.status_code == 200
        and "Mi 01.12." in seite.text
        and 'class="zelle frei"' in seite.text
    )
    r = c.post(
        "/admin/belegung/buchung",
        data={
            "csrf_token": c.csrf,
            "feld_id": str(f.id),
            "kunde_id": str(a.id),
            "beginn": "2027-12-01T19:00",
            "ende": "2027-12-01T21:00",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    b = db.query(Buchung).one()
    # Betreiberbuchung: Zahlungsart manuell, Event-Satz, sofort eine offene Rechnung.
    assert b.preis == Decimal("60.00") and b.zahlungsart == "manuell"
    assert b.ust_satz == Decimal("19.00") and b.rechnung_position_id is not None
    assert db.query(Rechnung).one().status == "offen"
    assert [m["betreff"].split(":")[0] for m in mail.TEST_AUSGANG] == [
        "Buchung bestätigt",
        "Rechnung 2027-00001",
    ]
    seite = c.get(f"/admin/belegung?feld={f.id}&woche=2027-11-29")
    assert 'class="zelle belegt"' in seite.text and "A" in seite.text
    detail = c.get(f"/admin/belegung/buchung/{b.id}")
    assert detail.status_code == 200 and "PIN" in detail.text


def test_betreiberbuchung_mit_eigenem_steuersatz(eingeloggt: TestClient, db: Session, welt) -> None:
    f, a, _ = welt
    a.mitglied_bis = date(2028, 4, 30)  # auch für Mitglieder gilt der Event-Satz (A-RECH-9)
    db.commit()
    c = eingeloggt
    formular = c.get(f"/admin/belegung/buchung/neu?feld={f.id}&beginn=2027-12-01T19:00")
    assert 'name="ust_satz" value="19"' in formular.text
    r = c.post(
        "/admin/belegung/buchung",
        data={
            "csrf_token": c.csrf,
            "feld_id": str(f.id),
            "kunde_id": str(a.id),
            "beginn": "2027-12-01T19:00",
            "ende": "2027-12-01T20:00",
            "ust_satz": "7",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    b = db.query(Buchung).one()
    assert b.zahlungsart == "manuell" and b.ust_satz == Decimal("7.00")
    rechnung = db.query(Rechnung).one()
    assert rechnung.status == "offen" and rechnung.positionen[0].ust_satz == Decimal("7.00")


def test_betreiberbuchung_ungueltiger_steuersatz(eingeloggt: TestClient, db: Session, welt) -> None:
    f, a, _ = welt
    c = eingeloggt
    r = c.post(
        "/admin/belegung/buchung",
        data={
            "csrf_token": c.csrf,
            "feld_id": str(f.id),
            "kunde_id": str(a.id),
            "beginn": "2027-12-01T19:00",
            "ende": "2027-12-01T20:00",
            "ust_satz": "120",
        },
    )
    assert "Steuersatz ungültig" in r.text
    assert db.query(Buchung).count() == 0


def test_storno_ueber_ui(eingeloggt: TestClient, db: Session, welt) -> None:
    f, _, v1 = welt
    c = eingeloggt
    c.post(
        "/admin/belegung/buchung",
        data={
            "csrf_token": c.csrf,
            "feld_id": str(f.id),
            "kunde_id": str(v1.id),
            "beginn": "2027-12-01T19:00",
            "ende": "2027-12-01T20:00",
        },
    )
    b = db.query(Buchung).one()
    r = c.post(
        f"/admin/belegung/buchung/{b.id}/storno",
        data={"csrf_token": c.csrf, "grund": "Test", "kostenfrei": "nein"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(b)
    assert b.status == "storniert" and not b.storno.kostenfrei


def test_sperre_mit_entscheidung(eingeloggt: TestClient, db: Session, welt) -> None:
    f, a, _ = welt
    c = eingeloggt
    c.post(
        "/admin/belegung/buchung",
        data={
            "csrf_token": c.csrf,
            "feld_id": str(f.id),
            "kunde_id": str(a.id),
            "beginn": "2027-12-01T19:00",
            "ende": "2027-12-01T20:00",
        },
    )
    b = db.query(Buchung).one()
    daten = {
        "csrf_token": c.csrf,
        "alle_felder": "1",
        "beginn": "2027-12-01T18:00",
        "ende": "2027-12-01T22:00",
        "grund": "Turnier",
    }
    r = c.post("/admin/belegung/sperre", data=daten)
    assert r.status_code == 200 and f"entscheidung_{b.id}" in r.text
    r = c.post(
        "/admin/belegung/sperre",
        data={**daten, f"entscheidung_{b.id}": "stornieren"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert db.query(Sperre).count() == 1
    db.refresh(b)
    assert b.status == "storniert" and b.storno.kostenfrei


def test_dauerbuchung_planen_und_anlegen(eingeloggt: TestClient, db: Session, welt) -> None:
    f, _, v1 = welt
    c = eingeloggt
    daten = {
        "csrf_token": c.csrf,
        "kunde_id": str(v1.id),
        "feld_id": str(f.id),
        "wochentag": "1",
        "start": "19:00",
        "ende": "21:00",
        "gueltig_von": "2027-12-01",
        "gueltig_bis": "2027-12-31",
    }
    r = c.post("/admin/belegung/dauer/planen", data=daten)
    assert r.status_code == 200 and "07.12.2027" in r.text and "auslassen_2027-12-28" in r.text
    r = c.post(
        "/admin/belegung/dauer", data={**daten, "auslassen_2027-12-28": "1"}, follow_redirects=False
    )
    assert r.status_code == 303
    d = db.query(Dauerbuchung).one()
    assert len(d.buchungen) == 3
    assert any(m["betreff"] == "Ihre Dauerbuchung" for m in mail.TEST_AUSGANG)
    r = c.post(
        f"/admin/belegung/dauer/{d.id}/beenden",
        data={"csrf_token": c.csrf, "ab": "2027-12-14"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.expire_all()
    assert [b.status for b in db.get(Dauerbuchung, d.id).buchungen] == [
        "bestaetigt",
        "storniert",
        "storniert",
    ]


def test_woche_ohne_felder_rendert(eingeloggt: TestClient, db: Session) -> None:
    seite = eingeloggt.get("/admin/belegung")
    assert seite.status_code == 200


def test_alle_seiten_rendern(eingeloggt: TestClient, db: Session, welt) -> None:
    f, a, v1 = welt
    c = eingeloggt
    assert c.get(f"/admin/belegung?feld={f.id}&woche=2027-11-29").status_code == 200
    assert (
        c.get(f"/admin/belegung/buchung/neu?feld={f.id}&beginn=2027-12-01T19:00").status_code == 200
    )
    c.post(
        "/admin/belegung/buchung",
        data={
            "csrf_token": c.csrf,
            "feld_id": str(f.id),
            "kunde_id": str(a.id),
            "beginn": "2027-12-01T19:00",
            "ende": "2027-12-01T20:00",
        },
    )
    b = db.query(Buchung).one()
    assert c.get(f"/admin/belegung/buchung/{b.id}").status_code == 200
    assert c.get("/admin/belegung/sperre/neu").status_code == 200
    assert c.get("/admin/belegung/dauer/neu").status_code == 200
    daten = {
        "csrf_token": c.csrf,
        "kunde_id": str(v1.id),
        "feld_id": str(f.id),
        "wochentag": "3",
        "start": "10:00",
        "ende": "11:00",
        "gueltig_von": "2027-12-02",
        "gueltig_bis": "2027-12-02",
    }
    c.post("/admin/belegung/dauer/planen", data=daten)
    c.post("/admin/belegung/dauer", data=daten)
    d = db.query(Dauerbuchung).one()
    assert c.get(f"/admin/belegung/dauer/{d.id}").status_code == 200


def test_dauerbuchung_kollision_mit_entscheidung(eingeloggt: TestClient, db: Session, welt) -> None:
    f, a, v1 = welt
    c = eingeloggt
    c.post(
        "/admin/belegung/buchung",
        data={
            "csrf_token": c.csrf,
            "feld_id": str(f.id),
            "kunde_id": str(a.id),
            "beginn": "2027-12-07T19:00",
            "ende": "2027-12-07T21:00",
        },
    )
    fremd = db.query(Buchung).one()
    daten = {
        "csrf_token": c.csrf,
        "kunde_id": str(v1.id),
        "feld_id": str(f.id),
        "wochentag": "1",
        "start": "19:00",
        "ende": "21:00",
        "gueltig_von": "2027-12-01",
        "gueltig_bis": "2027-12-31",
    }
    r = c.post("/admin/belegung/dauer/planen", data=daten)
    assert r.status_code == 200 and f"entscheidung_{fremd.id}" in r.text
    r = c.post("/admin/belegung/dauer", data=daten)
    assert r.status_code == 200 and "entscheiden" in r.text
    assert db.query(Dauerbuchung).count() == 0
    r = c.post(
        "/admin/belegung/dauer",
        data={**daten, f"entscheidung_{fremd.id}": "stornieren"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(fremd)
    assert fremd.status == "storniert"
    d = db.query(Dauerbuchung).one()
    assert len(d.buchungen) == 4


def test_dauerbuchung_fehlende_pflichtfelder_zeigt_meldung(
    eingeloggt: TestClient, db: Session, welt
) -> None:
    f, _, v1 = welt
    c = eingeloggt
    daten = {
        "csrf_token": c.csrf,
        "kunde_id": "",
        "feld_id": str(f.id),
        "wochentag": "1",
        "start": "19:00",
        "ende": "21:00",
        "gueltig_von": "2027-12-01",
        "gueltig_bis": "2027-12-31",
    }
    r = c.post("/admin/belegung/dauer", data=daten)
    assert r.status_code == 200 and ("fehlt" in r.text or "Ungültige Auswahl" in r.text)
    assert db.query(Dauerbuchung).count() == 0


def test_kulanz_ueber_ui(eingeloggt: TestClient, db: Session, welt) -> None:
    f, _, v1 = welt
    c = eingeloggt
    c.post(
        "/admin/belegung/buchung",
        data={
            "csrf_token": c.csrf,
            "feld_id": str(f.id),
            "kunde_id": str(v1.id),
            "beginn": "2027-12-01T19:00",
            "ende": "2027-12-01T20:00",
        },
    )
    b = db.query(Buchung).one()
    c.post(
        f"/admin/belegung/buchung/{b.id}/storno",
        data={"csrf_token": c.csrf, "grund": "Test", "kostenfrei": "nein"},
    )
    s = db.query(Storno).filter_by(buchung_id=b.id).one()
    assert not s.kostenfrei
    r = c.post(
        f"/admin/belegung/buchung/{b.id}/kulanz",
        data={"csrf_token": c.csrf, "grund": "Kulanzgrund"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(s)
    assert s.kostenfrei is True


def test_sperre_loeschen(eingeloggt: TestClient, db: Session, welt) -> None:
    f, _, _ = welt
    c = eingeloggt
    daten = {
        "csrf_token": c.csrf,
        "alle_felder": "1",
        "beginn": "2027-12-05T08:00",
        "ende": "2027-12-05T09:00",
        "grund": "Wartung",
    }
    c.post("/admin/belegung/sperre", data=daten)
    s = db.query(Sperre).one()
    r = c.post(
        f"/admin/belegung/sperre/{s.id}/loeschen",
        data={"csrf_token": c.csrf},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert db.query(Sperre).count() == 0


def test_woche_zeigt_sperre_zelle(eingeloggt: TestClient, db: Session, welt) -> None:
    f, _, _ = welt
    c = eingeloggt
    daten = {
        "csrf_token": c.csrf,
        "alle_felder": "1",
        "beginn": "2027-12-01T09:00",
        "ende": "2027-12-01T12:00",
        "grund": "Turnier",
    }
    c.post("/admin/belegung/sperre", data=daten)
    seite = c.get(f"/admin/belegung?feld={f.id}&woche=2027-11-29")
    assert seite.status_code == 200 and 'class="zelle sperre"' in seite.text


def test_buchung_neu_ohne_slot_fuehrt_in_den_plan(eingeloggt: TestClient, welt) -> None:
    """Der Pfad wird normalerweise mit Feld und Beginn aufgerufen. Ohne diese Angaben –
    über ein Lesezeichen etwa – gehörte der Betreiber in den Belegungsplan geführt und
    nicht auf eine technische Fehlerseite (vorher: HTTP 422 mit JSON-Rumpf)."""
    antwort = eingeloggt.get("/admin/belegung/buchung/neu", follow_redirects=False)
    assert antwort.status_code == 303
    assert antwort.headers["location"] == "/admin/belegung"

    seite = eingeloggt.get("/admin/belegung/buchung/neu", follow_redirects=True)
    assert seite.status_code == 200
    assert "Belegungsplan" in seite.text


def _post_buchung(c, f, a, satz: str):
    return c.post(
        "/admin/belegung/buchung",
        data={
            "csrf_token": c.csrf,
            "feld_id": str(f.id),
            "kunde_id": str(a.id),
            "beginn": "2027-12-01T19:00",
            "ende": "2027-12-01T20:00",
            "ust_satz": satz,
        },
        follow_redirects=False,
    )


def test_betreiberbuchung_steuersatz_mit_komma(eingeloggt: TestClient, db: Session, welt) -> None:
    f, a, _ = welt
    assert _post_buchung(eingeloggt, f, a, "7,5").status_code == 303
    assert db.query(Buchung).one().ust_satz == Decimal("7.50")


def test_betreiberbuchung_steuersatz_nan(eingeloggt: TestClient, db: Session, welt) -> None:
    f, a, _ = welt
    r = _post_buchung(eingeloggt, f, a, "NaN")
    assert r.status_code == 303
    assert "Steuersatz ungültig" in eingeloggt.get(r.headers["location"]).text
    assert db.query(Buchung).count() == 0


def test_storno_mit_rechnung_verschickt_stornorechnung(
    eingeloggt: TestClient, db: Session, welt
) -> None:
    f, a, _ = welt
    c = eingeloggt
    c.post(
        "/admin/belegung/buchung",
        data={
            "csrf_token": c.csrf,
            "feld_id": str(f.id),
            "kunde_id": str(a.id),
            "beginn": "2027-12-01T19:00",
            "ende": "2027-12-01T20:00",
        },
    )
    b = db.query(Buchung).one()
    mail.TEST_AUSGANG.clear()
    r = c.post(
        f"/admin/belegung/buchung/{b.id}/storno",
        data={"csrf_token": c.csrf, "grund": "Test", "kostenfrei": "ja"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(b)
    beleg = db.get(Rechnung, b.storno.korrektur_rechnung_id)
    assert beleg.art == "storno" and beleg.pdf_pfad
    assert [m["betreff"] for m in mail.TEST_AUSGANG] == [
        "Stornierung Ihrer Buchung",
        f"Stornorechnung {beleg.nummer}",
    ]
    assert "Stornorechnung" in c.get(f"/admin/belegung/buchung/{b.id}").text
    # Betreiberbuchung mit offener Rechnung: Die Forderung sinkt, Guthaben entsteht keins.
    assert db.get(Kunde, a.id).guthaben == Decimal("0.00")


def _abo_daten(c: TestClient, f, kunde) -> dict[str, str]:
    return {
        "csrf_token": c.csrf,
        "kunde_id": str(kunde.id),
        "feld_id": str(f.id),
        "wochentag": "1",
        "start": "19:00",
        "ende": "21:00",
        "gueltig_von": "2027-12-01",
        "gueltig_bis": "2027-12-31",
    }


def test_ui_abo_ohne_mitgliedschaft_zeigt_link_zur_kundenseite(
    eingeloggt: TestClient, db: Session, welt
) -> None:
    f, _, v1 = welt
    v1.mitglied_bis = None
    db.commit()
    c = eingeloggt
    daten = _abo_daten(c, f, v1)
    r = c.post("/admin/belegung/dauer/planen", data=daten)
    assert f'href="/admin/kunden/{v1.id}"' in r.text
    r = c.post("/admin/belegung/dauer", data=daten)
    assert "reicht nicht bis zum letzten Termin" in r.text
    assert db.query(Dauerbuchung).count() == 0


def test_ui_abo_rechnungskunde_markieren(eingeloggt: TestClient, db: Session, welt) -> None:
    f, a, _ = welt
    a.mitglied_bis = date(2028, 4, 30)
    db.commit()
    c = eingeloggt
    daten = _abo_daten(c, f, a)
    assert 'name="rechnungskunde_setzen"' in c.post("/admin/belegung/dauer/planen", data=daten).text
    r = c.post("/admin/belegung/dauer", data=daten)
    assert "nur für Rechnungskunden" in r.text
    r = c.post(
        "/admin/belegung/dauer",
        data={**daten, "rechnungskunde_setzen": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(a)
    assert a.rechnungskunde is True


@pytest.mark.parametrize(
    "ausgelassen,hinweis",
    [
        ([], True),
        (["2027-12-28"], False),
        (["2027-12-07", "2027-12-14", "2027-12-21", "2027-12-28"], False),
    ],
)
def test_ui_abo_vorschau_prueft_nur_verbleibende_termine(
    eingeloggt: TestClient, db: Session, welt, ausgelassen: list[str], hinweis: bool
) -> None:
    f, _, kunde = welt
    kunde.mitglied_bis = date(2027, 12, 21)
    db.commit()
    daten = _abo_daten(eingeloggt, f, kunde)
    daten.update({f"auslassen_{datum}": "1" for datum in ausgelassen})
    r = eingeloggt.post("/admin/belegung/dauer/planen", data=daten)
    assert r.status_code == 200
    assert ("Mitgliedschaft auf der Kundenseite verlängern" in r.text) is hinweis


def _betreiberbuchung(c: TestClient, f: Feld, k: Kunde, beginn: str, ende: str) -> None:
    r = c.post(
        "/admin/belegung/buchung",
        data={
            "csrf_token": c.csrf,
            "feld_id": str(f.id),
            "kunde_id": str(k.id),
            "beginn": beginn,
            "ende": ende,
        },
        follow_redirects=False,
    )
    assert r.status_code == 303


def test_review2_6_sperre_bestaetigt_storno_und_verschickt_nur_neue_belege(
    eingeloggt: TestClient, db: Session, welt
) -> None:
    f, a, _ = welt
    c = eingeloggt
    _betreiberbuchung(c, f, a, "2027-12-01T19:00", "2027-12-01T20:00")
    _betreiberbuchung(c, f, a, "2027-12-02T19:00", "2027-12-02T20:00")
    alt, neu = sorted(db.query(Buchung).all(), key=lambda b: b.beginn)
    r = c.post(
        f"/admin/belegung/buchung/{alt.id}/storno",
        data={"csrf_token": c.csrf, "grund": "Kunde", "kostenfrei": "ja"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    mail.TEST_AUSGANG.clear()
    r = c.post(
        "/admin/belegung/sperre",
        data={
            "csrf_token": c.csrf,
            "alle_felder": "1",
            "beginn": "2027-12-01T18:00",
            "ende": "2027-12-02T22:00",
            "grund": "Turnier",
            # Veraltete Entscheidung zu einer inzwischen stornierten Buchung.
            f"entscheidung_{alt.id}": "stornieren",
            f"entscheidung_{neu.id}": "stornieren",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.expire_all()
    beleg = db.get(Storno, db.get(Buchung, neu.id).storno.id).korrektur_rechnung_id
    nummer = db.get(Rechnung, beleg).nummer
    assert [(m["an"], m["betreff"]) for m in mail.TEST_AUSGANG] == [
        ("a@x.de", "Stornierung Ihrer Buchung"),
        ("a@x.de", f"Stornorechnung {nummer}"),
    ]


def test_review2_6_abo_ende_eine_stornomail_mit_allen_terminen(
    eingeloggt: TestClient, db: Session, welt
) -> None:
    f, _, v1 = welt
    c = eingeloggt
    daten = {
        "csrf_token": c.csrf,
        "kunde_id": str(v1.id),
        "feld_id": str(f.id),
        "wochentag": "1",
        "start": "19:00",
        "ende": "21:00",
        "gueltig_von": "2027-12-01",
        "gueltig_bis": "2027-12-31",
    }
    assert c.post("/admin/belegung/dauer", data=daten, follow_redirects=False).status_code == 303
    d = db.query(Dauerbuchung).one()
    mail.TEST_AUSGANG.clear()
    r = c.post(
        f"/admin/belegung/dauer/{d.id}/beenden",
        data={"csrf_token": c.csrf, "ab": "2027-12-20"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    betreffe = [m["betreff"] for m in mail.TEST_AUSGANG]
    assert betreffe[0] == "Stornierung Ihrer Buchungen"
    assert "21.12.2027" in mail.TEST_AUSGANG[0]["text"]
    assert "28.12.2027" in mail.TEST_AUSGANG[0]["text"]
    assert len(betreffe) == 2 and betreffe[1].startswith("Stornorechnung ")


def test_review2_6_daueranlage_bestaetigt_verdraengten_kunden(
    eingeloggt: TestClient, db: Session, welt
) -> None:
    f, a, v1 = welt
    c = eingeloggt
    _betreiberbuchung(c, f, a, "2027-12-07T19:00", "2027-12-07T21:00")
    fremd = db.query(Buchung).one()
    mail.TEST_AUSGANG.clear()
    r = c.post(
        "/admin/belegung/dauer",
        data={
            "csrf_token": c.csrf,
            "kunde_id": str(v1.id),
            "feld_id": str(f.id),
            "wochentag": "1",
            "start": "19:00",
            "ende": "21:00",
            "gueltig_von": "2027-12-01",
            "gueltig_bis": "2027-12-31",
            f"entscheidung_{fremd.id}": "stornieren",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    an_a = [m["betreff"] for m in mail.TEST_AUSGANG if m["an"] == "a@x.de"]
    assert an_a[0] == "Stornierung Ihrer Buchung"
    assert len(an_a) == 2 and an_a[1].startswith("Stornorechnung ")


def test_woche_zeigt_mehrere_felder_per_haken(eingeloggt: TestClient, db: Session, welt) -> None:
    f, a, _ = welt
    weitere = []
    for i in range(2, 7):
        x = Feld(name=f"F{i}", reihenfolge=i)
        x.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
        weitere.append(x)
    ohne_raster = weitere[1]
    ohne_raster.raster.clear()
    db.add_all(weitere)
    db.commit()
    c = eingeloggt

    def ueberschriften(html: str) -> list[str]:
        return [n for n in ["F1", "F2", "F3", "F4", "F5", "F6"] if f"<h2>{n}</h2>" in html]

    # Ohne Auswahl die ersten fünf Felder, jedes mit eigenem Plan; ohne Raster „geschlossen“.
    seite = c.get("/admin/belegung?woche=2027-11-29").text
    assert ueberschriften(seite) == ["F1", "F2", "F3", "F4", "F5"]
    assert seite.count('type="checkbox" name="feld"') == 6
    assert seite.count("checked") == 5 and "ersten 5 Felder" in seite
    assert 'class="zelle leer">geschlossen' in seite
    # Explizite Auswahl bleibt beim Blättern erhalten.
    sechs = weitere[-1]
    seite = c.get(f"/admin/belegung?feld={f.id}&feld={sechs.id}&woche=2027-11-29").text
    assert ueberschriften(seite) == ["F1", "F6"]
    assert f'href="/admin/belegung?feld={f.id}&amp;feld={sechs.id}&amp;woche=2027-12-06"' in seite
    assert f"/admin/belegung/buchung/neu?feld={sechs.id}&beginn=" in seite
    # Ungültige Angaben fallen auf die Standardauswahl zurück.
    assert ueberschriften(c.get("/admin/belegung?feld=kaputt").text) == [
        "F1",
        "F2",
        "F3",
        "F4",
        "F5",
    ]
    # Nach dem Buchen geht es zur Standardansicht, solange das Feld darin enthalten ist.
    for ziel, erwartet in [(f, "/admin/belegung?woche=2027-11-29"), (sechs, f"feld={sechs.id}")]:
        r = c.post(
            "/admin/belegung/buchung",
            data={
                "csrf_token": c.csrf,
                "feld_id": str(ziel.id),
                "kunde_id": str(a.id),
                "beginn": "2027-12-01T19:00",
                "ende": "2027-12-01T20:00",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303 and erwartet in r.headers["location"]
