import base64
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from beachhub_shared.hallenplan import (
    ALARM_TYPEN,
    EREIGNISTYPEN,
    EreignisLieferung,
    HallenEreignis,
    HallenplanInhalt,
    PinParameter,
    PlanBuchung,
    PlanFeld,
    PlanKonfig,
    pin_hash,
)
from beachhub_shared.lesestand import Dokument
from pydantic import ValidationError

P = PinParameter(
    salt_b64=base64.b64encode(b"0123456789abcdef").decode(),
    time_cost=1,
    memory_cost=1024,
    parallelism=1,
    hash_len=32,
)
T0 = datetime(2027, 12, 1, 17, 0, tzinfo=UTC)


def test_pin_hash_ist_deterministisch_und_haengt_von_salt_und_pin_ab() -> None:
    assert pin_hash("482913", P) == pin_hash("482913", P)
    assert pin_hash("482913", P).startswith("argon2id$")
    anderes_salt = P.model_copy(update={"salt_b64": base64.b64encode(b"fedcba9876543210").decode()})
    assert pin_hash("482913", anderes_salt) != pin_hash("482913", P)
    assert pin_hash("482914", P) != pin_hash("482913", P)


def test_ereignistypen() -> None:
    assert len(EREIGNISTYPEN) == 15
    assert "tastenfeld_fehlversuche" in EREIGNISTYPEN
    assert "tastenfeld_gesperrt" not in EREIGNISTYPEN
    assert ALARM_TYPEN <= EREIGNISTYPEN
    assert len(ALARM_TYPEN) == 6


def test_ereignis_prueft_typ_zeitzone_und_seq() -> None:
    HallenEreignis(seq=1, typ="pin_akzeptiert", zeitpunkt=T0)
    with pytest.raises(ValidationError):
        HallenEreignis(seq=1, typ="gibt_es_nicht", zeitpunkt=T0)
    with pytest.raises(ValidationError):
        HallenEreignis(seq=1, typ="pin_akzeptiert", zeitpunkt=datetime(2027, 12, 1, 17, 0))
    with pytest.raises(ValidationError):
        HallenEreignis(seq=0, typ="pin_akzeptiert", zeitpunkt=T0)


def test_plan_ueberlebt_den_weg_durch_ein_dokument() -> None:
    inhalt = HallenplanInhalt(
        gueltig_ab=T0,
        gueltig_bis=T0 + timedelta(days=7),
        felder=[PlanFeld(id="f1", name="Feld 1", aktiv=True)],
        buchungen=[
            PlanBuchung(
                buchung_id="b1",
                feld_id="f1",
                beginn=T0 + timedelta(hours=2),
                ende=T0 + timedelta(hours=4),
                pin_hash=pin_hash("482913", P),
            )
        ],
        sperren=[],
        konfig=PlanKonfig(
            heiz_vorlauf_minuten=30,
            spiel_temperatur=Decimal("18.0"),
            grund_temperatur=Decimal("0.0"),
            licht_vorlauf_minuten=5,
            licht_nachlauf_minuten=5,
            zutritt_vorlauf_minuten=15,
            praesenz_alarm_minuten=10,
        ),
        pin=P,
    )
    d = Dokument(
        dokument="hallenplan",
        version=1,
        erzeugt_am=T0,
        inhalt=inhalt.model_dump(mode="json"),
        signatur="00",
    )
    assert d.inhalt["konfig"]["spiel_temperatur"] == "18.0"
    wieder = HallenplanInhalt.model_validate(
        Dokument.model_validate_json(d.model_dump_json()).inhalt
    )
    assert wieder == inhalt


def test_lieferung_ohne_status_ist_erlaubt() -> None:
    lieferung = EreignisLieferung.model_validate({"dienst_id": str(uuid.uuid4()), "ereignisse": []})
    assert lieferung.status is None
