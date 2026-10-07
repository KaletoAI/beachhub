from datetime import UTC, date, datetime
from decimal import Decimal

from beachhub_shared.lesestand import Dokument, KontoInhalt, TarifeInhalt, TarifInfo


def test_dokument_roundtrip_json() -> None:
    inhalt = TarifeInhalt(
        regeln=[
            TarifInfo(
                name="Std",
                preis=Decimal("30.00"),
                feld_id=None,
                wochentag=None,
                uhrzeit_von=None,
                uhrzeit_bis=None,
                kundengruppe="Privat",
                gueltig_von=None,
                gueltig_bis=None,
            )
        ]
    )
    d = Dokument(
        dokument="tarife",
        version=1,
        erzeugt_am=datetime(2027, 11, 1, tzinfo=UTC),
        inhalt=inhalt.model_dump(mode="json"),
        signatur="00",
    )
    wieder = Dokument.model_validate_json(d.model_dump_json())
    assert wieder.inhalt["regeln"][0]["preis"] == "30.00"


def test_konto_neue_felder_mit_vorgabe_und_ohne_zahlungsart() -> None:
    alt = {
        "kunde_id": "k",
        "kundengruppe": "Nicht-Mitglied",
        "zahlungsart": "online",
        "guthaben": "0.00",
        "buchungen": [],
        "rechnungen": [],
    }
    k = KontoInhalt.model_validate(alt)
    assert k.rechnungskunde is False and k.online_buchen is True
    assert k.mitgliedschaft == "nicht_mitglied" and k.mitglied_bis is None and k.antrag_am is None
    assert "zahlungsart" not in k.model_dump()
    neu = KontoInhalt.model_validate(
        {**alt, "mitgliedschaft": "mitglied", "mitglied_bis": "2028-04-30"}
    )
    assert neu.mitglied_bis == date(2028, 4, 30)


def test_tarife_gruppennamen_optional() -> None:
    t = TarifeInhalt(regeln=[])
    assert t.gruppe_mitglied is None and t.gruppe_nichtmitglied is None


def test_altdokument_aktiviert_keinen_gutscheinverkauf() -> None:
    k = KontoInhalt.model_validate(
        dict(kunde_id="k", kundengruppe="g", guthaben="0", buchungen=[], rechnungen=[])
    )
    assert k.gutschein_kauf is False
    assert k.gutscheine == [] and k.gutschein_kaeufe == []
    assert TarifeInhalt(regeln=[]).gutschein_angebot is None


def test_offener_kauf_roundtrip_veroeffentlicht_keinen_code() -> None:
    from beachhub_shared.lesestand import KontoGutscheinKauf

    k = KontoGutscheinKauf.model_validate(
        dict(
            id="k",
            status="offen",
            preis="26.00",
            reserviert_bis="2026-10-06T00:15:00Z",
            checkout_url="/bezahlen",
            code="GEHEIM",
        )
    )
    assert "code" not in k.model_dump()
    assert KontoGutscheinKauf.model_validate_json(k.model_dump_json()).preis == Decimal("26.00")
