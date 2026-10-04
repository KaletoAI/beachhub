from datetime import date

from beachhub_core import clock, jobs
from beachhub_core.services import kunden
from sqlalchemy.orm import Session


def test_mitgliedschaft_tageslauf_schickt_mails_nach_commit(
    db: Session, mail_ausgang: list
) -> None:
    k = kunden.lege_an(db, name="Anna", email="anna@x.de")
    k.mitglied_bis = date(2028, 4, 30)
    db.commit()
    clock.set_override(db, date(2028, 4, 20))
    jobs.mitgliedschaft_ausfuehren(db)
    assert [m["betreff"] for m in mail_ausgang] == ["Ihre Mitgliedschaft läuft bald ab"]
    db.refresh(k)
    assert k.mitglied_erinnert_fuer == date(2028, 4, 30)


def test_mitgliedschaft_tageslauf_meldet_abgleich(db: Session, mail_ausgang: list) -> None:
    clock.set_override(db, date(2027, 8, 31))
    jobs.mitgliedschaft_ausfuehren(db)
    assert [m["betreff"] for m in mail_ausgang] == ["[Beachhub] Jahresabgleich der Mitglieder"]
