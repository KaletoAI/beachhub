import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from beachhub_shared.canonical_json import dumps


def test_schluessel_sortiert_und_kompakt() -> None:
    assert dumps({"b": 1, "a": [1, 2]}) == b'{"a":[1,2],"b":1}'


def test_umlaute_bleiben_utf8() -> None:
    assert dumps({"n": "Müller"}) == '{"n":"Müller"}'.encode()


def test_decimal_datum_uuid_werden_strings() -> None:
    u = uuid.UUID("12345678-1234-5678-1234-567812345678")
    dt = datetime(2027, 11, 3, 18, 0, tzinfo=UTC)
    out = dumps({"p": Decimal("12.50"), "d": date(2027, 11, 3), "t": dt, "u": u})
    assert out == (
        b'{"d":"2027-11-03","p":"12.50","t":"2027-11-03T18:00:00+00:00",'
        b'"u":"12345678-1234-5678-1234-567812345678"}'
    )


def test_gleicher_inhalt_gleiche_bytes() -> None:
    assert dumps({"x": 1, "y": 2}) == dumps({"y": 2, "x": 1})
