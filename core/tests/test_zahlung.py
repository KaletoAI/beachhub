import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import pytest
from beachhub_core import zahlung
from beachhub_core.config import Settings, settings
from beachhub_core.zahlung.fake import FakeProvider


def test_fake_sitzung_verweist_auf_portalseite() -> None:
    s = FakeProvider().erzeuge_sitzung(
        betrag=Decimal("30.00"),
        referenz=uuid.uuid4(),
        ablauf=datetime(2027, 12, 1, tzinfo=UTC),
        rueckkehr_url="/zahlung/zurueck?anfrage=abc",
    )
    assert s.provider_ref.startswith("fake_")
    u = urlparse(s.checkout_url)
    assert u.path == f"/test-zahlung/{s.provider_ref}"
    q = parse_qs(u.query)
    assert q["betrag"] == ["30.00"]
    assert q["zurueck"] == ["/zahlung/zurueck?anfrage=abc"]


def test_fake_verifiziert_und_liefert_status() -> None:
    p = FakeProvider()
    roh = json.dumps({"ref": "fake_abc", "ergebnis": "bezahlt", "betrag": "30.00"})
    assert p.verifiziere(roh, None) == "fake_abc"
    assert p.status("fake_abc") == ("bezahlt", Decimal("30.00"))
    assert p.status("fake_unbekannt") == ("offen", Decimal("0.00"))


@pytest.mark.parametrize(
    "roh",
    [
        "kein json",
        json.dumps([1]),
        json.dumps({"ref": "x", "ergebnis": "bezahlt", "betrag": "1"}),
        json.dumps({"ref": "fake_a", "ergebnis": "gestohlen", "betrag": "1"}),
        json.dumps({"ref": "fake_a", "ergebnis": "bezahlt", "betrag": "abc"}),
        json.dumps({"ref": "fake_a", "ergebnis": "bezahlt", "betrag": "NaN"}),
    ],
)
def test_fake_verwirft_kaputte_rueckmeldungen(roh: str) -> None:
    assert FakeProvider().verifiziere(roh, None) is None


def test_fake_nur_in_entwicklung(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    with pytest.raises(zahlung.ZahlungsFehler):
        FakeProvider()


def test_anbieter_nach_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zahlung, "_instanz", None)
    assert zahlung.anbieter().name == "fake"
    assert zahlung.anbieter_fuer("fake") is zahlung.anbieter()
    assert zahlung.anbieter_fuer("stripe") is None


def test_unbekannter_anbieter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zahlung, "_instanz", None)
    monkeypatch.setattr(settings, "zahlung_provider", "gibtsnicht")
    with pytest.raises(zahlung.ZahlungsFehler):
        zahlung.anbieter()
    assert zahlung.anbieter_fuer("gibtsnicht") is None


def test_produktionsfehler() -> None:
    ohne_portal = Settings(app_env="production", portal_url="", zahlung_provider="fake")
    assert ohne_portal.produktionsfehler == []
    mit_portal = Settings(
        app_env="production",
        portal_url="https://p:8443",
        portal_oeffentliche_url="https://p.example",
        kanal_token="",
        zahlung_provider="fake",
        portal_client_cert="/data/zertifikate/portal-kanal.crt",
        portal_client_key="/data/zertifikate/portal-kanal.key",
        portal_ca="/data/zertifikate/ca.crt",
    )
    assert len(mit_portal.produktionsfehler) == 2


def test_produktionsfehler_ohne_oeffentliche_url() -> None:
    # K3: PORTAL_OEFFENTLICHE_URL muss gesetzt sein, sobald der Kanal (PORTAL_URL) es ist.
    s = Settings(
        app_env="production",
        portal_url="https://p:8443",
        portal_oeffentliche_url="",
        kanal_token="tok",
        zahlung_provider="stripe",
        portal_client_cert="/data/zertifikate/portal-kanal.crt",
        portal_client_key="/data/zertifikate/portal-kanal.key",
        portal_ca="/data/zertifikate/ca.crt",
    )
    assert s.produktionsfehler == ["PORTAL_OEFFENTLICHE_URL fehlt, obwohl PORTAL_URL gesetzt ist"]


def test_produktionsfehler_ohne_mtls_zertifikate() -> None:
    # mTLS zum Portal ist Pflicht (A-2): ohne Client-Zertifikat/-Schlüssel/CA kein Start in
    # production.
    s = Settings(
        app_env="production",
        portal_url="https://p:8443",
        portal_oeffentliche_url="https://p.example",
        kanal_token="tok",
        zahlung_provider="stripe",
        portal_client_cert="",
        portal_client_key="",
        portal_ca="",
    )
    assert s.produktionsfehler == [
        "PORTAL_CLIENT_CERT fehlt, obwohl PORTAL_URL gesetzt ist",
        "PORTAL_CLIENT_KEY fehlt, obwohl PORTAL_URL gesetzt ist",
        "PORTAL_CA fehlt, obwohl PORTAL_URL gesetzt ist",
    ]
