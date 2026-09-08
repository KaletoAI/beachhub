import logging

from beachhub_core import mail


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
