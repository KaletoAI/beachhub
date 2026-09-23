from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from beachhub_hall.soll import laufende_buchung, sollzustand, zusammenlegen, zutritt_offen
from beachhub_shared.hallenplan import PlanSperre

from tests.hilfen import F1, F2, baue_plan, buchung, t

FELDER = [F1, F2]


def test_zusammenlegen_verbindet_ueberlappende_und_beruehrende() -> None:
    a, b, c = (t(18), t(19)), (t(19), t(20)), (t(21), t(22))
    assert zusammenlegen([c, b, a]) == [(t(18), t(20)), (t(21), t(22))]
    assert zusammenlegen([(t(18), t(20)), (t(18, 30), t(19))]) == [(t(18), t(20))]
    assert zusammenlegen([]) == []


def test_licht_mit_vorlauf_und_nachlauf() -> None:
    plan = baue_plan([buchung(F2, t(19), t(21))])
    assert sollzustand(plan, FELDER, t(18, 54), False).licht == {F1: False, F2: False}
    assert sollzustand(plan, FELDER, t(18, 55), False).licht == {F1: False, F2: True}
    assert sollzustand(plan, FELDER, t(21, 4), False).licht[F2] is True
    assert sollzustand(plan, FELDER, t(21, 5), False).licht[F2] is False


def test_licht_bleibt_zwischen_direkt_folgenden_buchungen_an() -> None:
    plan = baue_plan([buchung(F1, t(19), t(20)), buchung(F1, t(20, 8), t(21))])
    # Lücke 20:00–20:08 ist kleiner als Nachlauf (5) + Vorlauf (5): Licht bleibt an.
    for minute in range(0, 10):
        assert sollzustand(plan, FELDER, t(20, minute), False).licht[F1] is True


def test_licht_geht_aus_bei_grosser_luecke() -> None:
    plan = baue_plan([buchung(F1, t(19), t(20)), buchung(F1, t(21), t(22))])
    assert sollzustand(plan, FELDER, t(20, 30), False).licht[F1] is False


def test_heizung_hallenweit_mit_vorlauf() -> None:
    plan = baue_plan([buchung(F1, t(19), t(20)), buchung(F2, t(20), t(21))])
    assert sollzustand(plan, FELDER, t(18, 29), False).heizung == Decimal("0.0")
    assert sollzustand(plan, FELDER, t(18, 30), False).heizung == Decimal("18.0")
    assert sollzustand(plan, FELDER, t(20, 30), False).heizung == Decimal("18.0")
    assert sollzustand(plan, FELDER, t(21), False).heizung == Decimal("0.0")


def test_sperren_schalten_nichts() -> None:
    plan = baue_plan([], sperren=[PlanSperre(feld_id=None, beginn=t(18), ende=t(23))])
    soll = sollzustand(plan, FELDER, t(19), False)
    assert soll.licht == {F1: False, F2: False}
    assert soll.heizung == Decimal("0.0")
    assert zutritt_offen(plan, t(19)) == []


def test_handbetrieb_steuert_nichts() -> None:
    plan = baue_plan([buchung(F1, t(19), t(20))])
    soll = sollzustand(plan, FELDER, t(19), True)
    assert soll.steuern is False and soll.licht == {} and soll.heizung is None


def test_ohne_plan_licht_aus_und_heizung_unangetastet() -> None:
    soll = sollzustand(None, FELDER, t(19), False)
    assert soll.steuern is True
    assert soll.licht == {F1: False, F2: False}
    assert soll.heizung is None


def test_abgelaufener_plan_gibt_grundzustand() -> None:
    gb = t(0) + timedelta(days=7)  # gueltig_bis von baue_plan() ohne ab=
    b = buchung(F1, gb - timedelta(hours=1), gb + timedelta(hours=1))
    plan = baue_plan([b])
    assert plan.gueltig_bis == gb

    kurz_vor_ablauf = gb - timedelta(seconds=1)
    soll_vorher = sollzustand(plan, FELDER, kurz_vor_ablauf, False)
    assert soll_vorher.licht[F1] is True
    assert soll_vorher.heizung == Decimal("18.0")
    assert zutritt_offen(plan, kurz_vor_ablauf) == [b]
    assert laufende_buchung(plan, F1, kurz_vor_ablauf) == b

    soll_nach = sollzustand(plan, FELDER, gb, False)
    assert soll_nach.licht == {F1: False, F2: False}
    assert soll_nach.heizung == Decimal("0.0")
    assert zutritt_offen(plan, gb) == []
    assert laufende_buchung(plan, F1, gb) is None


def test_nur_zugeordnete_felder_werden_gesteuert() -> None:
    plan = baue_plan([buchung(F2, t(19), t(20))])
    assert sollzustand(plan, [F1], t(19), False).licht == {F1: False}


def test_laufende_buchung_und_zutritt() -> None:
    b = buchung(F1, t(19), t(21))
    plan = baue_plan([b])
    assert laufende_buchung(plan, F1, t(18, 59)) is None
    assert laufende_buchung(plan, F1, t(19)) == b
    assert laufende_buchung(plan, F2, t(19)) is None
    assert laufende_buchung(plan, F1, t(21)) is None
    assert zutritt_offen(plan, t(18, 44)) == []
    assert zutritt_offen(plan, t(18, 45)) == [b]
    assert zutritt_offen(plan, t(20, 59)) == [b]
    assert zutritt_offen(plan, t(21)) == []
    assert laufende_buchung(None, F1, t(19)) is None and zutritt_offen(None, t(19)) == []


def test_zeitumstellung_winterzeit() -> None:
    """31.10.2027: Die Uhr wird um 03:00 auf 02:00 zurückgestellt. Vorläufe gelten in Ortszeit
    trotzdem genau wie an jedem anderen Tag, weil intern in UTC gerechnet wird."""
    tag = date(2027, 10, 31)
    plan = baue_plan([buchung(F1, t(19, tag=tag), t(21, tag=tag))], ab=t(0, tag=tag))
    assert sollzustand(plan, FELDER, t(18, 54, tag=tag), False).licht[F1] is False
    assert sollzustand(plan, FELDER, t(18, 55, tag=tag), False).licht[F1] is True
    assert sollzustand(plan, FELDER, t(18, 29, tag=tag), False).heizung == Decimal("0.0")
    assert sollzustand(plan, FELDER, t(18, 30, tag=tag), False).heizung == Decimal("18.0")


def test_zeitumstellung_winterzeit_direkt_an_der_ruecksetzung() -> None:
    """Buchung beginnt um 03:00 MEZ (= 02:00 UTC), direkt nach der Rückstellung von 03:00 MESZ
    (= 01:00 UTC) auf 02:00 MESZ. Vor- und Nachlauf werden intern in UTC gerechnet: eine
    Ortszeit-Arithmetik würde den Lichtvorlauf fälschlich schon um 00:30 UTC statt 01:55 UTC
    beginnen lassen (eine Stunde zu früh, weil sie die verdoppelte 02:00-03:00-Stunde ignoriert)."""
    ab = datetime(2027, 10, 30, 22, 0, tzinfo=UTC)
    beginn = datetime(2027, 10, 31, 2, 0, tzinfo=UTC)
    plan = baue_plan([buchung(F1, beginn, beginn + timedelta(hours=2))], ab=ab)

    assert sollzustand(plan, FELDER, datetime(2027, 10, 31, 1, 29, tzinfo=UTC), False).heizung == (
        Decimal("0.0")
    )
    assert sollzustand(plan, FELDER, datetime(2027, 10, 31, 1, 30, tzinfo=UTC), False).heizung == (
        Decimal("18.0")
    )
    assert (
        sollzustand(plan, FELDER, datetime(2027, 10, 31, 1, 54, tzinfo=UTC), False).licht[F1]
        is False
    )
    assert (
        sollzustand(plan, FELDER, datetime(2027, 10, 31, 1, 55, tzinfo=UTC), False).licht[F1]
        is True
    )
