from datetime import UTC, datetime
from decimal import Decimal

from beachhub_shared.lesestand import Dokument, TarifeInhalt, TarifInfo


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
