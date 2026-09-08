from datetime import date, time

from beachhub_shared.zeit import kombiniere, lokal, lokales_datum


def test_kombiniere_winterzeit() -> None:
    dt = kombiniere(date(2027, 12, 1), time(19, 0))
    assert dt.isoformat() == "2027-12-01T18:00:00+00:00"


def test_kombiniere_sommerzeit() -> None:
    dt = kombiniere(date(2027, 10, 1), time(19, 0))
    assert dt.isoformat() == "2027-10-01T17:00:00+00:00"


def test_lokal_und_datum() -> None:
    dt = kombiniere(date(2027, 12, 1), time(23, 30))
    assert lokal(dt).hour == 23
    assert lokales_datum(dt) == date(2027, 12, 1)
