import logging
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from beachhub_core import mail
from beachhub_core.templating import templates


def test_test_ausgang_faengt_mails(monkeypatch) -> None:
    ausgang: list[dict] = []
    monkeypatch.setattr(mail, "TEST_AUSGANG", ausgang)
    mail.sende("a@x.de", "Betreff", "Hallo", anhaenge=[("r.pdf", b"%PDF")])
    assert ausgang == [
        {"an": "a@x.de", "betreff": "Betreff", "text": "Hallo", "anhaenge": ["r.pdf"]}
    ]


def test_ohne_smtp_wird_nur_geloggt(monkeypatch, caplog) -> None:
    monkeypatch.setattr(mail, "TEST_AUSGANG", None)
    caplog.set_level(logging.WARNING, logger="beachhub_core.mail")
    mail.sende("a@x.de", "B", "T")
    assert "kein SMTP" in caplog.text


def test_mail_template_escaped_nicht() -> None:
    b = SimpleNamespace(
        kunde=SimpleNamespace(name='Müller & Sohn "GbR"'),
        feld=SimpleNamespace(name="F1"),
        beginn=datetime(2027, 12, 1, 19, 0, tzinfo=UTC),
        ende=datetime(2027, 12, 1, 20, 0, tzinfo=UTC),
        preis=Decimal("30.00"),
    )
    text = templates.env.get_template("mail/buchung_bestaetigt.txt").render(
        betreiber="Halle", b=b, pin="1234", vorlauf=15
    )
    assert 'Müller & Sohn "GbR"' in text
    assert "&amp;" not in text
    assert "&quot;" not in text
    assert templates.env.autoescape("x.html") is True
    assert templates.env.autoescape("x.txt") is False


def test_sende_fehler_wird_geloggt_und_verschluckt(monkeypatch, caplog) -> None:
    monkeypatch.setattr(mail, "TEST_AUSGANG", None)
    monkeypatch.setattr(mail.settings, "smtp_host", "smtp.example")

    async def kaputt(_m: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(mail, "_senden", kaputt)
    caplog.set_level(logging.ERROR, logger="beachhub_core.mail")
    mail.sende("a@x.de", "B", "T")  # darf keine Ausnahme werfen
    assert "fehlgeschlagen" in caplog.text
