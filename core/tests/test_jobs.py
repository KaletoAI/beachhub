from datetime import date, time
from decimal import Decimal

from beachhub_core import clock, jobs, mail
from beachhub_core.models import (
    AppSetting,
    Betriebszeit,
    Feld,
    FeldRaster,
    Rechnung,
    Tarif,
)
from beachhub_core.services import buchungen, kunden
from beachhub_shared.zeit import kombiniere
from sqlalchemy.orm import Session


def test_monatslauf_faellig_nur_einmal_pro_monat(db: Session) -> None:
    clock.set_override(db, date(2028, 1, 2))
    assert jobs.monatslauf_faellig(db) is None  # Tag 2 < 3
    clock.set_override(db, date(2028, 1, 3))
    assert jobs.monatslauf_faellig(db) == (2027, 12)
    assert (
        jobs.monatslauf_ausfuehren(db) == 0
    )  # keine Rechnungskunden → 0 Rechnungen, Marker trotzdem
    db.commit()
    assert db.get(AppSetting, "monatslauf_letzter").value == "2027-12"
    assert jobs.monatslauf_faellig(db) is None
    clock.set_override(db, date(2028, 2, 5))
    assert jobs.monatslauf_faellig(db) == (2028, 1)


def test_monatslauf_fuer_erzeugt_pdf_und_mail(db: Session) -> None:
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    db.add_all([f, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    k = kunden.lege_an(db, name="TSV", email="v@x.de", rechnungskunde=True)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k.id,
        beginn=kombiniere(date(2027, 12, 1), time(19)),
        ende=kombiniere(date(2027, 12, 1), time(20)),
        zahlungsart="saison",
    )
    db.commit()

    anzahl = jobs.monatslauf_fuer(db, 2027, 12)

    assert anzahl == 1
    rechnung = db.query(Rechnung).one()
    assert rechnung.pdf_pfad
    assert any(m["betreff"] == f"Rechnung {rechnung.nummer}" for m in mail.TEST_AUSGANG)


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
