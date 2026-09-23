import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from beachhub_shared import kanal
from beachhub_shared.lesestand import BelegungInhalt, KontoBuchung
from pydantic import ValidationError


def test_fuer_portal_ist_allowlist() -> None:
    assert kanal.fuer_portal("belegung")
    assert kanal.fuer_portal("tarife")
    assert kanal.fuer_portal(f"konto:{uuid.uuid4()}")
    # Neue Dokumente des Hauptsystems (etwa der Hallenplan) dürfen nie ans Portal gehen.
    assert not kanal.fuer_portal("hallenplan")
    assert not kanal.fuer_portal("konto:kein-uuid")
    assert not kanal.fuer_portal("konto:")
    assert not kanal.fuer_portal("")


def test_jeder_anfragetyp_hat_ein_nutzlastschema() -> None:
    assert set(kanal.NUTZLAST) == set(kanal.ANFRAGETYPEN)


def test_buchung_anfragen_validiert() -> None:
    n = kanal.NUTZLAST["buchung_anfragen"].model_validate(
        {
            "feld_id": str(uuid.uuid4()),
            "beginn": "2027-12-01T18:00:00Z",
            "ende": "2027-12-01T19:00:00Z",
        }
    )
    assert isinstance(n, kanal.BuchungAnfragen)
    assert n.beginn.tzinfo is not None
    with pytest.raises(ValidationError):
        kanal.NUTZLAST["buchung_anfragen"].model_validate({"feld_id": "x"})


def test_zeiten_ohne_zeitzone_werden_abgelehnt() -> None:
    with pytest.raises(ValidationError):
        kanal.BuchungAnfragen.model_validate(
            {
                "feld_id": str(uuid.uuid4()),
                "beginn": "2027-12-01T18:00:00",
                "ende": "2027-12-01T19:00:00",
            }
        )


def test_konto_angelegt_begrenzt_laengen() -> None:
    with pytest.raises(ValidationError):
        kanal.KontoAngelegt.model_validate({"email": "a@x.de", "anzeigename": ""})
    with pytest.raises(ValidationError):
        kanal.KontoAngelegt.model_validate({"email": "a@x.de", "anzeigename": "x" * 101})


def test_antwort_ohne_leere_felder_und_roundtrip() -> None:
    a = kanal.Antwort(
        status="reserviert",
        buchung_id=uuid.uuid4(),
        preis=Decimal("30.00"),
        checkout_url="/test-zahlung/fake_x",
    )
    d = a.model_dump(mode="json", exclude_none=True)
    assert d["preis"] == "30.00"
    assert "pdf_base64" not in d
    assert kanal.Antwort.model_validate(d) == a


def test_anfrage_liste_roundtrip() -> None:
    a = kanal.Anfrage(
        anfrage_id=uuid.uuid4(),
        typ="konto_angelegt",
        konto_id=uuid.uuid4(),
        nutzlast={"email": "a@x.de", "anzeigename": "A"},
        erstellt_am=datetime(2027, 11, 1, tzinfo=UTC),
    )
    liste = kanal.AnfrageListe(anfragen=[a])
    assert kanal.AnfrageListe.model_validate_json(liste.model_dump_json()).anfragen[0] == a


def test_belegung_neue_felder_mit_vorgabe() -> None:
    b = BelegungInhalt(
        felder=[],
        betriebszeiten=[],
        ausnahmetage=[],
        fenster_tage=14,
        mindestvorlauf_minuten=60,
        belegt={},
    )
    assert b.storno_frist_stunden == 24
    assert b.antwort_hinweis_sekunden == 120


def test_konto_buchung_zahlungslink_optional() -> None:
    alt = {
        "id": str(uuid.uuid4()),
        "feld_id": str(uuid.uuid4()),
        "feld_name": "F1",
        "beginn": "2027-12-01T18:00:00Z",
        "ende": "2027-12-01T19:00:00Z",
        "status": "reserviert",
        "preis": "30.00",
        "pin": None,
        "storno": None,
    }
    b = KontoBuchung.model_validate(alt)  # Dokument aus der Zeit vor Stufe 2
    assert b.checkout_url is None and b.reserviert_bis is None
    neu = KontoBuchung.model_validate(
        {**alt, "checkout_url": "/test-zahlung/fake_x", "reserviert_bis": "2027-11-25T09:15:00Z"}
    )
    assert neu.checkout_url == "/test-zahlung/fake_x" and neu.reserviert_bis is not None
