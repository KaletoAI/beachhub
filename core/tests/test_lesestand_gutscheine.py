"""Berechtigter signierter Gutscheinbestand, unabhängig von Ablaufjobs."""

import uuid
from datetime import date, time, timedelta
from decimal import Decimal

import pytest
from beachhub_core import clock
from beachhub_core.config import settings
from beachhub_core.models import LesestandVersion, Zahlung
from beachhub_core.services import (
    gutschein_codes,
    konfiguration,
    kunden,
    lesestand,
    online_buchung,
)
from beachhub_shared.signatur import pruefe
from beachhub_shared.zeit import kombiniere
from hilfen_gutschein import einloesung, kaufgutschein

HEUTE = date(2026, 10, 6)


@pytest.fixture
def bestand(db, gwelt, monkeypatch):
    settings.signatur_privatschluessel_pfad.unlink(missing_ok=True)
    monkeypatch.setattr(clock, "now", lambda db: kombiniere(HEUTE, time(0)))
    for k in (gwelt.mitglied, gwelt.nichtmitglied):
        k.portal_konto_id = uuid.uuid4()
    db.commit()
    return gwelt


def _g(db, welt, **werte):
    return kaufgutschein(
        db, welt, code=gutschein_codes.neuer_code(db), kaufdatum=HEUTE, gueltig_bis=HEUTE, **werte
    )


def test_nur_inhaber_sieht_code_und_gruppensnapshot(db, bestand):
    g = _g(db, bestand)
    db.commit()
    konto = lesestand.baue_konto(db, bestand.mitglied)
    assert [x.code for x in konto.gutscheine] == [gutschein_codes.anzeige(g.code)]
    assert konto.gutscheine[0].minuten == 120
    assert (
        konto.gutscheine[0].gruppenhinweis
        == "Mitgliedsgutschein – nur für DJK-Mitglieder einlösbar"
    )
    assert lesestand.baue_konto(db, bestand.nichtmitglied).gutscheine == []
    g.steuer, g.nur_mitglieder = "bei_einloesung", False
    bestand.mitglied.mitglied_bis = None
    db.commit()
    assert (
        "für alle einlösbar"
        in lesestand.baue_konto(db, bestand.mitglied).gutscheine[0].gruppenhinweis
    )


@pytest.mark.parametrize(
    "zustand",
    [
        "gesperrt",
        "verfallen",
        "eingeloest",
        "offen",
        "abgebrochen",
        "abgelaufen",
        "kein_datum",
        "wert_leer",
        "einheit_verbraucht",
        "zurueckgegeben",
    ],
)
def test_unsichtbarer_code_auch_ohne_job(db, bestand, zustand):
    g = _g(db, bestand)
    if zustand == "abgelaufen":
        g.gueltig_bis = HEUTE - timedelta(days=1)
    elif zustand == "kein_datum":
        g.art, g.steuer, g.gueltig_bis = "frei", "keine", None
    elif zustand == "wert_leer":
        g.modell, g.restwert, g.nennwert_brutto = "wert", Decimal("0"), Decimal("50")
    elif zustand == "einheit_verbraucht":
        einloesung(db, bestand, g)
    elif zustand == "zurueckgegeben":
        g.zurueckgegeben_am = clock.now(db)
    else:
        g.status = zustand
    db.commit()
    assert lesestand.baue_konto(db, bestand.mitglied).gutscheine == []


def test_wertrest_und_verbleibende_freicode_nutzung(db, bestand):
    wert = _g(
        db,
        bestand,
        modell="wert",
        nennwert_brutto=Decimal("50"),
        restwert=Decimal("20"),
        status="teilweise_eingeloest",
    )
    frei = _g(db, bestand, art="frei", steuer="keine", max_einloesungen=3)
    einloesung(db, bestand, frei)
    db.commit()
    sicht = {x.code: x for x in lesestand.baue_konto(db, bestand.mitglied).gutscheine}
    assert sicht[gutschein_codes.anzeige(wert.code)].restwert == Decimal("20")
    assert sicht[gutschein_codes.anzeige(frei.code)].verbleibende_einloesungen == 2


@pytest.mark.parametrize("sekunden,sichtbar", [(-1, True), (0, False), (1, False)])
def test_offene_kaeufe_nur_kaeufer_vor_frozen_frist_ohne_code(db, bestand, sekunden, sichtbar):
    g = _g(db, bestand, status="offen")
    g.kauf_reserviert_bis = clock.now(db) - timedelta(seconds=sekunden)
    g.inhaber_kunde_id = bestand.nichtmitglied.id
    z = db.get(Zahlung, g.zahlung_id)
    z.checkout_url = "https://zahlung.test/persistiert"
    db.commit()
    konfiguration.setze(db, "zahlungsfrist_minuten", 999)
    konto = lesestand.baue_konto(db, bestand.mitglied)
    assert len(konto.gutschein_kaeufe) == int(sichtbar)
    assert konto.gutscheine == []
    assert lesestand.baue_konto(db, bestand.nichtmitglied).gutschein_kaeufe == []
    if sichtbar:
        kauf = konto.gutschein_kaeufe[0]
        assert kauf.checkout_url == "https://zahlung.test/persistiert"
        assert kauf.reserviert_bis == g.kauf_reserviert_bis and kauf.preis == Decimal("26.00")
        assert "code" not in kauf.model_dump() and g.code not in konto.model_dump_json()


def test_besitzwechsel_invalidiert_alte_neue_und_kaeufer(db, bestand):
    alt = bestand.mitglied
    neu = bestand.nichtmitglied
    kaeufer = kunden.lege_an(db, name="Käufer", email="k@example.org")
    g = _g(
        db,
        bestand,
        kaeufer_kunde_id=kaeufer.id,
        nur_mitglieder=False,
        modell="wert",
        nennwert_brutto=Decimal("50"),
        restwert=Decimal("50"),
    )
    db.commit()
    lesestand.erzeuge_schluessel()
    for k in (alt, neu, kaeufer):
        lesestand.publiziere(db, f"konto:{k.id}")
    db.commit()
    erg = online_buchung.anfragen(
        db,
        kunde=neu,
        anfrage_id=uuid.uuid4(),
        feld_id=bestand.feld.id,
        beginn=kombiniere(HEUTE + timedelta(days=2), time(19)),
        ende=kombiniere(HEUTE + timedelta(days=2), time(20)),
        rueckkehr_url="https://portal.test/",
        gutschein_codes=[g.code],
    )
    assert erg.antwort.status == "bestaetigt"
    for k in (alt, neu, kaeufer):
        assert db.get(LesestandVersion, f"konto:{k.id}").geaendert
    assert lesestand.baue_konto(db, alt).gutscheine == []
    assert lesestand.baue_konto(db, kaeufer).gutscheine == []
    assert lesestand.baue_konto(db, neu).gutscheine[0].restwert == Decimal("35")
    assert lesestand.baue_konto(db, neu).buchungen[0].gutschein_modelle == ["wert"]
    for k in (alt, neu, kaeufer):
        dok = lesestand.publiziere(db, f"konto:{k.id}")
        assert pruefe(
            dok.model_dump(mode="json", exclude={"signatur"}),
            dok.signatur,
            lesestand.oeffentlicher_schluessel(),
        )
        assert len(dok.inhalt["gutscheine"]) == (1 if k is neu else 0)
        if k is not neu:
            assert gutschein_codes.anzeige(g.code) not in dok.model_dump_json()


@pytest.mark.parametrize(
    "modell,mitglied,preis,berechtigt",
    [
        ("einheit", True, "0", False),
        ("einheit", True, "26", True),
        ("einheit", False, "0", False),
        ("einheit", False, "30", True),
        ("wert", True, "0", True),
    ],
)
def test_angebot_und_kaufberechtigung(db, bestand, modell, mitglied, preis, berechtigt):
    konfiguration.setze(db, "gutschein_modell", modell)
    key = "gutschein_preis_mitglied" if mitglied else "gutschein_preis_nichtmitglied"
    konfiguration.setze(db, key, preis)
    k = bestand.mitglied if mitglied else bestand.nichtmitglied
    assert lesestand.baue_konto(db, k).gutschein_kauf is berechtigt
    angebot = lesestand.baue_tarife(db).gutschein_angebot
    assert angebot.modell == modell and angebot.minuten == (120 if modell == "einheit" else None)
    k.rechnungskunde = True
    konfiguration.setze(db, "rechnungskunden_gutscheinkauf", False)
    assert lesestand.baue_konto(db, k).gutschein_kauf is False
    k.rechnungskunde, k.portal_konto_id = False, None
    assert lesestand.baue_konto(db, k).gutschein_kauf is False


def test_angebotswechsel_aktualisiert_alle_konten_und_tarife(db, bestand):
    lesestand.erzeuge_schluessel()
    for name in ("tarife", f"konto:{bestand.mitglied.id}", f"konto:{bestand.nichtmitglied.id}"):
        lesestand.publiziere(db, name)
    db.commit()
    konfiguration.setze(db, "gutschein_preis_mitglied", "0")
    assert lesestand.baue_konto(db, bestand.mitglied).gutschein_kauf is False
    assert lesestand.baue_tarife(db).gutschein_angebot.preis_mitglied == Decimal("0")
    for name in ("tarife", f"konto:{bestand.mitglied.id}", f"konto:{bestand.nichtmitglied.id}"):
        assert db.get(LesestandVersion, name).geaendert


def test_kaufberechtigungswechsel_markiert_tarifdokument(db, bestand):
    bestand.mitglied.rechnungskunde = True
    lesestand.erzeuge_schluessel()
    lesestand.publiziere(db, "tarife")
    lesestand.publiziere(db, f"konto:{bestand.mitglied.id}")
    db.commit()
    konfiguration.setze(db, "rechnungskunden_gutscheinkauf", False)
    assert db.get(LesestandVersion, f"konto:{bestand.mitglied.id}").geaendert
    assert db.get(LesestandVersion, "tarife").geaendert


@pytest.mark.parametrize("gesperrt", ["anonymisiert", "ohne_portal"])
def test_konto_ohne_berechtigung_enthaelt_keinen_bestand_oder_kauf(db, bestand, gesperrt):
    _g(db, bestand)
    _g(db, bestand, status="offen", kauf_reserviert_bis=clock.now(db) + timedelta(minutes=15))
    if gesperrt == "anonymisiert":
        bestand.mitglied.anonymisiert_am = clock.now(db)
    else:
        bestand.mitglied.portal_konto_id = None
    db.commit()
    konto = lesestand.baue_konto(db, bestand.mitglied)
    assert konto.gutscheine == [] and konto.gutschein_kaeufe == []
    assert konto.gutschein_kauf is False
