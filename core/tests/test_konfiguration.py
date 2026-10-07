from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from beachhub_core.models import Audit, Kunde, LesestandVersion
from beachhub_core.services import konfiguration
from sqlalchemy.orm import Session


def test_default_wird_typisiert_geliefert(db: Session) -> None:
    assert konfiguration.hole(db, "storno_frist_stunden") == 24
    assert isinstance(konfiguration.hole(db, "storno_frist_stunden"), int)
    assert konfiguration.hole(db, "spiel_temperatur") == Decimal("18.0")


def test_setzen_ueberschreibt_und_protokolliert(db: Session) -> None:
    konfiguration.setze(db, "storno_frist_stunden", 48)
    db.commit()
    assert konfiguration.hole(db, "storno_frist_stunden") == 48
    a = db.query(Audit).filter_by(objekt_typ="konfiguration").one()
    assert a.vorher_json == {"wert": "24"} and a.nachher_json["wert"] == "48"


def test_unbekannter_schluessel_wirft() -> None:
    import pytest

    with pytest.raises(KeyError):
        konfiguration.DEFAULTS["gibt_es_nicht"]


def test_dezimalwert_nimmt_komma_und_punkt(db: Session) -> None:
    """Die Konfigurationsseite zeigt Dezimalzahlen deutsch mit Komma; sie muss sie deshalb
    auch so wieder entgegennehmen."""
    konfiguration.setze(db, "spiel_temperatur", "7,5")
    db.commit()
    assert konfiguration.hole(db, "spiel_temperatur") == Decimal("7.5")
    konfiguration.setze(db, "spiel_temperatur", "19.00")
    db.commit()
    assert konfiguration.hole(db, "spiel_temperatur") == Decimal("19.00")


def test_speichern_ohne_aenderung_schreibt_nichts(db: Session) -> None:
    konfiguration.setze(db, "storno_frist_stunden", 24)  # entspricht der Vorgabe
    konfiguration.setze(db, "storno_frist_stunden", 48)
    konfiguration.setze(db, "storno_frist_stunden", "48")
    db.commit()
    assert db.query(Audit).filter_by(objekt_typ="konfiguration").count() == 1


def test_event_ust_satz_hat_vorgabe_19(db: Session) -> None:
    assert konfiguration.hole(db, "event_ust_satz") == Decimal("19.00")
    assert konfiguration.BESCHREIBUNGEN["event_ust_satz"].gruppe == "Zahlung und Rechnung"


@pytest.mark.parametrize(
    "schluessel,roh,meldung",
    [("storno_frist_stunden", "abc", "ganze Zahl"), ("event_ust_satz", "x", "Zahl angeben")],
)
def test_ungueltige_werte_mit_verstaendlicher_meldung(
    db: Session, schluessel: str, roh: str, meldung: str
) -> None:
    with pytest.raises(ValueError, match=meldung):
        konfiguration.setze(db, schluessel, roh)


@pytest.mark.parametrize(
    "roh,erwartet", [("30.04.", "30.04."), ("1.5", "01.05."), (" 31.08 ", "31.08.")]
)
def test_tagmonat_normalisiert(roh: str, erwartet: str) -> None:
    tm = konfiguration.TagMonat(roh)
    assert tm == erwartet
    assert tm.im_jahr(2028) == date(2028, tm.monat, tm.tag)


@pytest.mark.parametrize("roh", ["31.02.", "29.02.", "30-04", "", "13.13."])
def test_tagmonat_lehnt_ungueltige_tage_ab(roh: str) -> None:
    with pytest.raises(ValueError):
        konfiguration.TagMonat(roh)


def test_stichtag_speichern_und_lesen(db: Session) -> None:
    assert konfiguration.hole(db, "mitgliedschaft_ablauf") == "30.04."
    konfiguration.setze(db, "mitgliedschaft_ablauf", "1.5.")
    db.commit()
    wert = konfiguration.hole(db, "mitgliedschaft_ablauf")
    assert isinstance(wert, konfiguration.TagMonat)
    assert wert.im_jahr(2028) == date(2028, 5, 1)


def test_ja_nein_nur_eindeutig(db: Session) -> None:
    assert konfiguration.hole(db, "rechnungskunden_online_buchen") is False
    konfiguration.setze(db, "rechnungskunden_online_buchen", "ja")
    assert konfiguration.hole(db, "rechnungskunden_online_buchen") is True
    with pytest.raises(ValueError, match="ja oder nein"):
        konfiguration.setze(db, "rechnungskunden_online_buchen", "vielleicht")


@pytest.mark.parametrize("roh", ["NaN", "Infinity", "-Infinity"])
def test_decimal_lehnt_nicht_endliche_werte_ab(db: Session, roh: str) -> None:
    with pytest.raises(ValueError, match="Zahl angeben"):
        konfiguration.setze(db, "event_ust_satz", roh)


@pytest.mark.parametrize("roh", ["150", "100", "-1"])
def test_event_ust_satz_bereich(db: Session, roh: str) -> None:
    with pytest.raises(ValueError, match="Steuersatz ungültig"):
        konfiguration.setze(db, "event_ust_satz", roh)


def test_gutschein_vorgaben(db: Session) -> None:
    erwartete_werte = {
        "gutschein_modell": "einheit",
        "gutschein_minuten": 120,
        "gutscheine_je_buchung": "volle_einheiten",
        "gutschein_preis_mitglied": Decimal("0.00"),
        "gutschein_preis_nichtmitglied": Decimal("0.00"),
        "gutschein_betraege": "25,00; 50,00; 100,00",
        "mitgliedsgutschein_nur_mitglieder": True,
        "gutschein_steuer": "beim_kauf",
        "gutschein_gueltig_tage": 730,
        "freicode_gueltig_tage": 90,
        "rechnungskunden_gutscheinkauf": True,
        "gutschein_gastkauf": False,
    }
    for schluessel, wert in erwartete_werte.items():
        assert konfiguration.hole(db, schluessel) == wert
        assert konfiguration.BESCHREIBUNGEN[schluessel].gruppe == "Gutscheine"
    assert konfiguration.hole(db, "gutscheine_je_buchung").zahl is None
    assert konfiguration.hole(db, "gutschein_betraege").betraege == [
        Decimal("25.00"),
        Decimal("50.00"),
        Decimal("100.00"),
    ]


@pytest.mark.parametrize("roh,zahl", [(" volle einheiten ", None), ("01", 1), (" 3 ", 3)])
def test_obergrenze_normalisiert(roh: str, zahl: int | None) -> None:
    assert konfiguration.Obergrenze(roh).zahl == zahl


@pytest.mark.parametrize("roh", ["0", "-1", "1.5", "", "NaN"])
def test_obergrenze_nur_positive_ganze_zahlen(roh: str) -> None:
    with pytest.raises(ValueError, match="ganze Zahl ab 1"):
        konfiguration.Obergrenze(roh)


def test_betragsliste_endlich_positiv() -> None:
    liste = konfiguration.Betragsliste("100; 25,00; 50.00; 25")
    assert liste.betraege == [Decimal("25.00"), Decimal("50.00"), Decimal("100.00")]
    assert str(liste) == "25,00; 50,00; 100,00"


def test_betragsliste_bis_zur_geldspeichergrenze() -> None:
    assert konfiguration.Betragsliste("99999999,99").betraege == [Decimal("99999999.99")]


@pytest.mark.parametrize("roh", ["100000000", "99999999.999"])
def test_betragsliste_lehnt_zu_grosse_betraege_ab(roh: str) -> None:
    with pytest.raises(ValueError):
        konfiguration.Betragsliste(roh)


@pytest.mark.parametrize(
    "schluessel", ["gutschein_preis_mitglied", "gutschein_preis_nichtmitglied"]
)
def test_gutscheinpreise_bis_zur_geldspeichergrenze(db: Session, schluessel: str) -> None:
    konfiguration.setze(db, schluessel, "99999999.99")
    assert konfiguration.hole(db, schluessel) == Decimal("99999999.99")
    konfiguration.setze(db, schluessel, "0")
    assert konfiguration.hole(db, schluessel) == Decimal("0.00")


@pytest.mark.parametrize(
    "schluessel", ["gutschein_preis_mitglied", "gutschein_preis_nichtmitglied"]
)
@pytest.mark.parametrize("roh", ["100000000", "99999999.999", "0.001"])
def test_gutscheinpreise_lehnen_zu_grosse_und_unrunde_betraege_ab(
    db: Session, schluessel: str, roh: str
) -> None:
    with pytest.raises(ValueError):
        konfiguration.setze(db, schluessel, roh)
    assert db.query(Audit).count() == 0


@pytest.mark.parametrize("roh", ["NaN", "sNaN", "Infinity", "-Infinity", "0", "-1", "", "0.001"])
def test_betragsliste_lehnt_ungueltige_betraege_ab(roh: str) -> None:
    with pytest.raises(ValueError):
        konfiguration.Betragsliste(roh)


@pytest.mark.parametrize(
    "schluessel,roh",
    [("gutschein_modell", "gratis"), ("gutschein_steuer", "keine")]
    + [(k, "0") for k in ("gutschein_minuten", "gutschein_gueltig_tage", "freicode_gueltig_tage")]
    + [(k, "-1") for k in ("gutschein_minuten", "gutschein_gueltig_tage", "freicode_gueltig_tage")]
    + [
        (k, wert)
        for k in ("gutschein_preis_mitglied", "gutschein_preis_nichtmitglied")
        for wert in ("-1", "NaN", "Infinity")
    ],
)
def test_gutschein_einstellungen_validieren(db: Session, schluessel: str, roh: str) -> None:
    with pytest.raises(ValueError):
        konfiguration.setze(db, schluessel, roh)
    assert db.query(Audit).count() == 0


def test_gutschein_einstellungen_roundtrip_und_darstellung(db: Session) -> None:
    konfiguration.setze(db, "gutschein_modell", "wert")
    konfiguration.setze(db, "gutschein_preis_mitglied", "26,00")
    konfiguration.setze(db, "gutschein_betraege", "100; 50")
    konfiguration.setze(db, "gutscheine_je_buchung", "2")
    db.commit()
    assert konfiguration.hole(db, "gutschein_preis_mitglied") == Decimal("26.00")
    assert konfiguration.hole(db, "gutschein_betraege").betraege == [
        Decimal("50.00"),
        Decimal("100.00"),
    ]
    assert konfiguration.hole(db, "gutscheine_je_buchung").zahl == 2
    werte = {k: konfiguration.hole(db, k) for k in konfiguration.DEFAULTS}
    darstellung = {
        k: art for _, eintraege in konfiguration.gruppiert(werte) for k, _, _, art in eintraege
    }
    assert darstellung["mitgliedsgutschein_nur_mitglieder"] == "bool"
    assert darstellung["gutschein_modell"] == "auswahl"
    assert darstellung["gutscheine_je_buchung"] == "text"


@pytest.mark.parametrize(
    "schluessel,wert",
    [
        ("gutschein_preis_mitglied", "26"),
        ("gutschein_steuer", "bei_einloesung"),
        ("mitgliedsgutschein_nur_mitglieder", "nein"),
    ],
)
def test_gutscheinangebot_markiert_tarif_und_konten(
    db: Session, schluessel: str, wert: str
) -> None:
    kunde = Kunde(name="A", email="a@x.de")
    anonym = Kunde(
        name="Gelöscht",
        email="geloescht@x.de",
        anonymisiert_am=datetime.now(UTC),
    )
    db.add_all([kunde, anonym])
    db.flush()
    konfiguration.setze(db, schluessel, wert)
    assert db.get(LesestandVersion, "tarife").geaendert
    assert db.get(LesestandVersion, f"konto:{kunde.id}").geaendert
    assert db.get(LesestandVersion, f"konto:{anonym.id}") is None


def test_rechnungskunden_gutscheinkauf_markiert_nur_rechnungskunden(db: Session) -> None:
    normal = Kunde(name="A", email="a@x.de")
    rechnung = Kunde(name="B", email="b@x.de", rechnungskunde=True)
    db.add_all([normal, rechnung])
    db.flush()
    konfiguration.setze(db, "rechnungskunden_gutscheinkauf", "nein")
    assert db.get(LesestandVersion, f"konto:{rechnung.id}").geaendert
    assert db.get(LesestandVersion, f"konto:{normal.id}") is None
