from datetime import date, time
from decimal import Decimal
from pathlib import Path

from beachhub_core import clock
from beachhub_core.config import settings
from beachhub_core.models import Betriebszeit, Feld, FeldRaster, Kundengruppe, Tarif
from beachhub_core.services import buchungen, kunden, storno
from beachhub_shared.zeit import kombiniere
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def test_system_seite_und_uhr(eingeloggt: TestClient) -> None:
    c = eingeloggt
    assert c.get("/admin/system").status_code == 200
    r = c.post(
        "/admin/system/uhr",
        data={"csrf_token": c.csrf, "datum": "2027-12-24"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "24.12.2027" in c.get("/admin/system").text
    assert c.get("/admin/system/audit").status_code == 200
    assert c.get("/admin/system/stornos").status_code == 200


def test_lesestand_ohne_schluessel_zeigt_meldung(eingeloggt: TestClient) -> None:
    Path(settings.signatur_privatschluessel_pfad).unlink(missing_ok=True)
    r = eingeloggt.post(
        "/admin/system/lesestand",
        data={"csrf_token": eingeloggt.csrf, "alle": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    seite = eingeloggt.get(r.headers["location"])
    assert "Signaturschlüssel fehlt" in seite.text


def test_uhr_override_im_produktivbetrieb_verboten(eingeloggt: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    r = eingeloggt.post(
        "/admin/system/uhr",
        data={"csrf_token": eingeloggt.csrf, "datum": "2027-12-24"},
        follow_redirects=False,
    )
    assert r.status_code == 403


def test_stornoliste_zeigt_kostenpflichtige_stornos(eingeloggt: TestClient, db: Session) -> None:
    """Seit dem Wegfall der Nachbuchung listet die Seite alle Stornos, für die der Preis
    fällig bleibt – die Arbeitsliste des Betreibers für Kulanzentscheidungen."""
    feld = Feld(name="Feld 1", reihenfolge=1)
    feld.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    gruppe = Kundengruppe(name="Nicht-Mitglied")
    db.add_all([feld, gruppe, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    kunde = kunden.lege_an(db, name="Anna Beispiel", email="a@x.de", kundengruppe_id=gruppe.id)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    buchung = buchungen.lege_an(
        db,
        feld_id=feld.id,
        kunde_id=kunde.id,
        beginn=kombiniere(date(2027, 11, 26), time(19)),
        ende=kombiniere(date(2027, 11, 26), time(20)),
    )
    db.commit()
    storno.storniere(db, buchung, durch="kunde", kostenfrei=False, grund="zu spät")
    db.commit()

    seite = eingeloggt.get("/admin/system/stornos")
    assert seite.status_code == 200
    assert "Anna Beispiel" in seite.text
    assert "zu spät" in seite.text
    assert f'href="/admin/belegung/buchung/{buchung.id}"' in seite.text
