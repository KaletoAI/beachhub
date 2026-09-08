from decimal import Decimal

from beachhub_core.templating import prozent


def test_prozent_ganzzahl() -> None:
    assert prozent(Decimal("19.00")) == "19 %"


def test_prozent_mit_nachkommastelle() -> None:
    assert prozent(Decimal("7.50")) == "7,5 %"
