from decimal import Decimal

from beachhub_core.models import Buchung, Feld, GuthabenBuchung, Kunde, Rechnung
from beachhub_core.services import kundengruppen
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def _neu(c: TestClient, **extra: str) -> dict[str, str]:
    return {
        "csrf_token": c.csrf,
        "name": "Anna Müller",
        "email": "Anna@X.de",
        "adresse_strasse": "Weg 1",
        "adresse_plz": "12345",
        "adresse_ort": "Ort",
        **extra,
    }


def test_kunde_anlegen_suchen_guthaben(eingeloggt: TestClient, db: Session) -> None:
    c = eingeloggt
    r = c.post("/admin/kunden", data=_neu(c), follow_redirects=False)
    assert r.status_code == 303
    k = db.query(Kunde).one()
    assert k.email == "anna@x.de" and k.rechnungskunde is False
    assert "Anna Müller" in c.get("/admin/kunden?q=müll").text
    assert "Anna Müller" not in c.get("/admin/kunden?q=zzz").text
    r = c.post(
        f"/admin/kunden/{k.id}/guthaben",
        data={"csrf_token": c.csrf, "betrag": "25,00", "art": "manuell", "notiz": "Gutschein"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(k)
    assert k.guthaben == Decimal("25.00")
    r = c.post(
        f"/admin/kunden/{k.id}/guthaben",
        data={"csrf_token": c.csrf, "betrag": "-30,00", "art": "auszahlung", "notiz": ""},
    )
    assert r.status_code == 200 and "nicht gedeckt" in r.text
    seite = c.get(f"/admin/kunden/{k.id}")
    assert "25,00 €" in seite.text and "Gutschein" in seite.text


def test_doppelte_email_zeigt_fehler(eingeloggt: TestClient, db: Session) -> None:
    c = eingeloggt
    daten = _neu(c, name="A", email="a@x.de", adresse_strasse="", adresse_plz="", adresse_ort="")
    c.post("/admin/kunden", data=daten)
    r = c.post("/admin/kunden", data=daten)
    assert r.status_code == 200 and "bereits vergeben" in r.text


def test_kunde_aendern_und_anonymisieren(eingeloggt: TestClient, db: Session) -> None:
    c = eingeloggt
    c.post(
        "/admin/kunden",
        data={
            "csrf_token": c.csrf,
            "name": "Bea Schmidt",
            "email": "bea@x.de",
            "adresse_strasse": "",
            "adresse_plz": "",
            "adresse_ort": "",
        },
    )
    k = db.query(Kunde).one()
    r = c.post(
        f"/admin/kunden/{k.id}",
        data={
            "csrf_token": c.csrf,
            "name": "Bea Schmidt-Neu",
            "email": "bea-neu@x.de",
            "adresse_strasse": "",
            "adresse_plz": "",
            "adresse_ort": "",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(k)
    assert k.name == "Bea Schmidt-Neu" and k.email == "bea-neu@x.de"

    r = c.post(f"/admin/kunden/{k.id}/anonymisieren", data={"csrf_token": c.csrf})
    assert r.status_code == 200 and "bestätig" in r.text.lower()
    db.refresh(k)
    assert k.anonymisiert_am is None

    r = c.post(
        f"/admin/kunden/{k.id}/anonymisieren",
        data={"csrf_token": c.csrf, "bestaetigt": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(k)
    assert k.anonymisiert_am is not None and k.name == "Gelöschter Kunde"


def test_detail_seite_mit_buchungen_rechnungen_guthaben(
    eingeloggt: TestClient, db: Session
) -> None:
    from datetime import UTC, date, datetime, timedelta

    c = eingeloggt
    feld = Feld(name="Feld 1")
    db.add(feld)
    db.commit()
    k = Kunde(name="Carla Voss", email="carla@x.de")
    db.add(k)
    db.commit()
    jetzt = datetime.now(UTC)
    db.add(
        Buchung(
            feld_id=feld.id,
            kunde_id=k.id,
            beginn=jetzt + timedelta(days=1),
            ende=jetzt + timedelta(days=1, hours=1),
            status=Buchung.BESTAETIGT,
            preis=Decimal("30.00"),
            ust_satz=Decimal("19.00"),
            kundengruppe_id=kundengruppen.nicht_mitglied(db).id,
            zahlungsart="manuell",
            quelle="admin",
        )
    )
    db.add(
        Rechnung(
            nummer="2026-000001",
            kunde_id=k.id,
            art="einzel",
            datum=date.today(),
            leistung_von=date.today(),
            leistung_bis=date.today(),
            faellig_am=date.today(),
            ust_satz=Decimal("19.00"),
            netto=Decimal("25.21"),
            ust=Decimal("4.79"),
            brutto=Decimal("30.00"),
            status="offen",
            adresse_snapshot={},
        )
    )
    db.add(GuthabenBuchung(kunde_id=k.id, betrag=Decimal("10.00"), art="manuell", notiz="Test"))
    db.commit()
    seite = c.get(f"/admin/kunden/{k.id}")
    assert seite.status_code == 200
    assert "Carla Voss" in seite.text
    assert "30,00 €" in seite.text
    assert "2026-000001" in seite.text
    assert f"/admin/rechnungen/{db.query(Rechnung).one().id}/pdf" in seite.text


def test_rechnungskunde_anlegen_und_aendern(eingeloggt: TestClient, db: Session) -> None:
    c = eingeloggt
    c.post("/admin/kunden", data=_neu(c, rechnungskunde="1"))
    k = db.query(Kunde).one()
    assert k.rechnungskunde is True
    seite = c.get(f"/admin/kunden/{k.id}")
    assert 'name="rechnungskunde" value="1" checked' in seite.text
    assert "Gruppe heute: <strong>Nicht-Mitglied</strong>" in seite.text
    r = c.post(
        f"/admin/kunden/{k.id}",
        data={
            "csrf_token": c.csrf,
            "name": k.name,
            "email": k.email,
            "adresse_strasse": "",
            "adresse_plz": "",
            "adresse_ort": "",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(k)
    assert k.rechnungskunde is False
