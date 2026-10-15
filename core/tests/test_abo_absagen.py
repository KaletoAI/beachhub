from datetime import date, time
from decimal import Decimal

import pytest
from beachhub_core import clock
from beachhub_core.models import (
    Betriebszeit,
    Dauerbuchung,
    Feld,
    FeldRaster,
    Kunde,
    Rechnung,
    Tarif,
)
from beachhub_core.services import (
    dauerbuchungen,
    konfiguration,
    kunden,
    lesestand,
    online_buchung,
    rechnungen,
    storno,
)
from beachhub_shared.zeit import kombiniere
from sqlalchemy import select
from sqlalchemy.orm import Session


@pytest.fixture
def welt(db: Session) -> tuple[Feld, Kunde]:
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    db.add_all([f, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    k = kunden.lege_an(db, name="TSV", email="tsv@x.de", rechnungskunde=True)
    k.mitglied_bis = date(2028, 4, 30)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    return f, k


def _abo(db: Session, f: Feld, k: Kunde) -> tuple[Dauerbuchung, Rechnung]:
    """Mittwochs 19–20 Uhr, 01.12. bis 29.12.2027: fünf Termine zu 30 €."""
    d = dauerbuchungen.lege_an(
        db,
        kunde_id=k.id,
        feld_id=f.id,
        wochentag=2,
        start=time(19),
        ende=time(20),
        gueltig_von=date(2027, 12, 1),
        gueltig_bis=date(2027, 12, 29),
        admin_user_id=None,
        auslassen=set(),
        entscheidungen={},
    )
    db.commit()
    return d, db.scalars(select(Rechnung).where(Rechnung.dauerbuchung_id == d.id)).one()


def _absagen(db: Session, k: Kunde, b) -> online_buchung.Ergebnis:
    erg = online_buchung.storniere_fuer_kunde(db, kunde=k, buchung_id=b.id)
    db.commit()
    return erg


def test_review_7_bereits_korrigierter_termin_verbraucht_kein_kontingent(db: Session, welt) -> None:
    f, k = welt
    d, r = _abo(db, f, k)
    rechnungen.setze_bezahlt(db, r, admin_user_id=None)
    storno.gutschreiben_positionen(db, [r.positionen[0]], grund="Ausfall", quelle="admin")
    db.commit()
    vorher = k.guthaben
    belege = db.query(Rechnung).count()
    a = _absagen(db, k, d.buchungen[0]).antwort
    assert a.kostenfrei is True and a.freie_absage is False
    assert a.verbleibende_freie_absagen == 3
    assert k.guthaben == vorher and db.query(Rechnung).count() == belege


def test_review_7a_unberechneter_termin_verbraucht_kontingent(db: Session, welt) -> None:
    """Ein unberechneter Termin wird nach der Absage nie berechnet – das kostet Kontingent."""
    from test_saisonrechnung import _altabo

    f, k = welt
    d = _altabo(db, f, k)
    antworten = [_absagen(db, k, b).antwort for b in d.buchungen]
    assert [a.kostenfrei for a in antworten] == [True, True, True, False]
    assert [a.freie_absage for a in antworten] == [True, True, True, False]
    assert storno.freie_absagen_rest(db, d) == 0
    neu = rechnungen.erzeuge_saisonrechnung(db, d)
    db.commit()
    assert neu.brutto == Decimal("30")
    assert len(neu.positionen) == 1


def test_review_7a_absagen_zwischen_vollstorno_und_neuausstellung(db: Session, welt) -> None:
    f, k = welt
    d, r = _abo(db, f, k)
    rechnungen.storniere(db, r, admin_user_id=None, grund="Adresse")
    db.commit()
    antworten = [_absagen(db, k, b).antwort for b in d.buchungen]
    assert [a.freie_absage for a in antworten] == [True, True, True, False, False]
    assert storno.freie_absagen_rest(db, d) == 0
    neu = rechnungen.erzeuge_saisonrechnung(db, d)
    db.commit()
    assert neu.brutto == Decimal("60")


@pytest.mark.parametrize("abo", [False, True])
def test_review_6_betreiberentscheidung_in_stornomail(
    db: Session, welt, mail_ausgang: list, abo: bool
) -> None:
    from beachhub_core.services import benachrichtigung, buchungen

    f, k = welt
    if abo:
        d, _ = _abo(db, f, k)
        b = d.buchungen[0]
    else:
        b = buchungen.lege_an(
            db,
            feld_id=f.id,
            kunde_id=k.id,
            beginn=kombiniere(date(2027, 12, 1), time(19)),
            ende=kombiniere(date(2027, 12, 1), time(20)),
        )
    s = storno.storniere(db, b, durch="betreiber", kostenfrei=False)
    db.commit()
    benachrichtigung.storno(db, s)
    text = mail_ausgang[-1]["text"]
    assert "Betreiber" in text and "kostenpflichtig" in text
    assert "Frist" not in text and "frist" not in text
    if abo:
        assert "übrig: 3" in text


def test_freie_absagen_werden_gezaehlt(db: Session, welt) -> None:
    f, k = welt
    d, r = _abo(db, f, k)
    termine = list(d.buchungen)
    antworten = [_absagen(db, k, b).antwort for b in termine[:4]]
    assert [(a.kostenfrei, a.freie_absage, a.verbleibende_freie_absagen) for a in antworten] == [
        (True, True, 2),
        (True, True, 1),
        (True, True, 0),
        (False, False, 0),
    ]
    # Auch die vierte Absage gibt den Platz frei (A-DAUER-3).
    assert all(b.status == "storniert" for b in termine[:4])
    # Offene Saisonrechnung: drei Teil-Stornos senken die Forderung um 3 × 30 €.
    assert rechnungen.offener_betrag(db, r) == Decimal("60.00")
    db.refresh(k)
    assert k.guthaben == Decimal("0.00")
    assert storno.freie_absagen_rest(db, d) == 0


def test_absage_nach_frist_kostet_und_zaehlt_nicht(db: Session, welt, monkeypatch) -> None:
    f, k = welt
    d, r = _abo(db, f, k)
    erster = d.buchungen[0]  # 01.12. 19:00
    monkeypatch.setattr(storno.clock, "now", lambda db: kombiniere(date(2027, 12, 1), time(10)))
    a = _absagen(db, k, erster).antwort
    assert a.kostenfrei is False and a.freie_absage is False
    assert a.verbleibende_freie_absagen == 3
    assert erster.status == "storniert"
    assert rechnungen.offener_betrag(db, r) == Decimal("150.00")


def test_null_freie_absagen_heisst_saison_fest_bezahlt(db: Session, welt) -> None:
    f, k = welt
    konfiguration.setze(db, "abo_freie_absagen", 0)
    db.commit()
    d, r = _abo(db, f, k)
    a = _absagen(db, k, d.buchungen[0]).antwort
    assert a.kostenfrei is False and a.verbleibende_freie_absagen == 0
    assert rechnungen.offener_betrag(db, r) == Decimal("150.00")


def test_kulanz_zaehlt_nicht_als_freie_absage(db: Session, welt, monkeypatch) -> None:
    f, k = welt
    d, r = _abo(db, f, k)
    monkeypatch.setattr(storno.clock, "now", lambda db: kombiniere(date(2027, 12, 1), time(10)))
    _absagen(db, k, d.buchungen[0])
    monkeypatch.undo()
    s = d.buchungen[0].storno
    storno.kulanz(db, s, admin_user_id=None, grund="Krankheit")
    # Ein Betreiber-Storno innerhalb der Frist ist kostenfrei, zählt aber ebenfalls nicht.
    storno.storniere(db, d.buchungen[1], durch="betreiber", grund="Turnier")
    db.commit()
    assert s.kostenfrei is True and s.freie_absage is False
    assert storno.freie_absagen_rest(db, d) == 3
    assert rechnungen.offener_betrag(db, r) == Decimal("90.00")


def test_freie_absage_bei_bezahlter_saisonrechnung_wird_guthaben(
    db: Session, welt, mail_ausgang: list
) -> None:
    f, k = welt
    d, r = _abo(db, f, k)
    rechnungen.setze_bezahlt(db, r, admin_user_id=None)
    db.commit()
    erg = _absagen(db, k, d.buchungen[0])
    for schritt in erg.nach_commit:
        schritt(db)
    db.refresh(k)
    assert k.guthaben == Decimal("30.00")
    betreffs = [m["betreff"] for m in mail_ausgang]
    assert "Stornierung Ihrer Buchung" in betreffs
    assert any(b.startswith("Stornorechnung ") for b in betreffs)
    storno_mail = next(m for m in mail_ausgang if m["betreff"] == "Stornierung Ihrer Buchung")
    assert "es bleiben 2" in storno_mail["text"]


def test_konto_zeigt_abo_und_restliche_absagen(db: Session, welt) -> None:
    f, k = welt
    d, _ = _abo(db, f, k)
    _absagen(db, k, d.buchungen[0])
    je_id = {b.id: b for b in lesestand.baue_konto(db, k).buchungen}
    zweiter = je_id[str(d.buchungen[1].id)]
    assert zweiter.abo is True and zweiter.freie_absagen_rest == 2 and zweiter.stornierbar is True


def test_aenderung_der_freien_absagen_markiert_konten(db: Session, welt) -> None:
    f, k = welt
    _abo(db, f, k)
    lesestand.markiere_geaendert(db, f"konto:{k.id}")
    db.commit()
    from beachhub_core.models import LesestandVersion

    db.get(LesestandVersion, f"konto:{k.id}").geaendert = False
    db.commit()
    konfiguration.setze(db, "abo_freie_absagen", 5)
    db.commit()
    assert db.get(LesestandVersion, f"konto:{k.id}").geaendert


def test_negative_freie_absagen_werden_abgelehnt(db: Session) -> None:
    with pytest.raises(ValueError, match="nicht negativ"):
        konfiguration.setze(db, "abo_freie_absagen", -1)


def test_einstellung_markiert_abo_konto_auch_ohne_rechnungskundenflag(db: Session, welt) -> None:
    from beachhub_core.models import LesestandVersion

    f, k = welt
    _abo(db, f, k)
    kunden.aendere(db, k, admin_user_id=None, rechnungskunde=False)
    db.commit()
    version = db.get(LesestandVersion, f"konto:{k.id}")
    version.geaendert = False
    db.commit()
    konfiguration.setze(db, "abo_freie_absagen", 5)
    db.commit()
    assert version.geaendert
    assert lesestand.baue_konto(db, k).buchungen[0].freie_absagen_rest == 5


def test_parallele_absagen_verbrauchen_die_letzte_freie_nur_einmal(db: Session, welt) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from beachhub_core.database import SessionLocal
    from beachhub_core.models import Buchung, Storno
    from hilfen_parallel import warte_auf_sperre
    from sqlalchemy import text

    f, k = welt
    d, r = _abo(db, f, k)
    konfiguration.setze(db, "abo_freie_absagen", 1)
    db.commit()
    kid, bid = k.id, d.buchungen[1].id
    geladen, starten = Event(), Event()
    pid: list[int] = []

    def zweiter():
        with SessionLocal() as session:
            kunde = session.get(Kunde, kid)
            session.get(Buchung, bid)
            pid.append(session.scalar(text("select pg_backend_pid()")))
            geladen.set()
            assert starten.wait(5)
            antwort = online_buchung.storniere_fuer_kunde(
                session, kunde=kunde, buchung_id=bid
            ).antwort
            session.commit()
            return antwort

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(zweiter)
        assert geladen.wait(5)
        erster = online_buchung.storniere_fuer_kunde(db, kunde=k, buchung_id=d.buchungen[0].id)
        starten.set()
        try:
            warte_auf_sperre(pid[0])
            db.commit()
        finally:
            db.rollback()
        zweite = future.result(timeout=10)
    assert erster.antwort.freie_absage is True
    assert zweite.status == "ok" and zweite.kostenfrei is False and zweite.freie_absage is False
    assert zweite.verbleibende_freie_absagen == 0
    assert len(db.scalars(select(Storno).where(Storno.freie_absage.is_(True))).all()) == 1
    assert rechnungen.offener_betrag(db, r) == Decimal("120.00")


@pytest.mark.parametrize("nach_frist", [False, True])
def test_review2_7_gutgeschriebener_termin_bleibt_ohne_kontingent_kostenfrei(
    db: Session, welt, nach_frist: bool
) -> None:
    """A-DAUER-3: Ein bereits gutgeschriebener Termin bleibt kostenfrei, auch ohne Kontingent
    und nach der Frist – es gibt nichts mehr zu berechnen."""
    from beachhub_core import mail

    konfiguration.setze(db, "abo_freie_absagen", 0)
    f, k = welt
    d, r = _abo(db, f, k)
    if nach_frist:
        konfiguration.setze(db, "storno_frist_stunden", 240)
    storno.gutschreiben_positionen(db, [r.positionen[0]], grund="Ausfall", quelle="admin")
    db.commit()
    belege = db.query(Rechnung).count()
    erg = _absagen(db, k, d.buchungen[0])
    assert erg.antwort.kostenfrei is True and erg.antwort.freie_absage is False
    assert erg.antwort.korrekturbeleg is False
    assert db.query(Rechnung).count() == belege
    mail.TEST_AUSGANG.clear()
    for nachlauf in erg.nach_commit:
        nachlauf(db)
    (m,) = mail.TEST_AUSGANG
    assert "kostenfrei" in m["text"] and "keine kostenfreien Absagen" not in m["text"]


def _anlage(db: Session, objekt_id):
    """Anlage-Audit eines Objekts (vorher ist JSON-null, nicht SQL-NULL)."""
    from beachhub_core.models import Audit

    (eintrag,) = [
        a
        for a in db.scalars(select(Audit).where(Audit.objekt_id == objekt_id))
        if a.vorher_json is None
    ]
    return eintrag


def test_review2_9_saisonrechnung_verrechnung_und_beenden_mit_admin(db: Session, welt) -> None:
    import uuid

    from beachhub_core.models import Audit, Storno
    from beachhub_core.services import guthaben

    f, k = welt
    admin = uuid.uuid4()
    guthaben.buche(db, kunde=k, betrag=Decimal("10"), art="manuell")
    d = dauerbuchungen.lege_an(
        db,
        kunde_id=k.id,
        feld_id=f.id,
        wochentag=2,
        start=time(19),
        ende=time(20),
        gueltig_von=date(2027, 12, 1),
        gueltig_bis=date(2027, 12, 29),
        admin_user_id=admin,
        auslassen=set(),
        entscheidungen={},
    )
    db.commit()
    r = db.scalars(select(Rechnung).where(Rechnung.dauerbuchung_id == d.id)).one()
    anlage = _anlage(db, r.id)
    assert anlage.admin_user_id == admin
    assert anlage.nachher_json["dauerbuchung_id"] == str(d.id)
    verrechnung = [
        a
        for a in db.scalars(select(Audit).where(Audit.objekt_typ == "guthaben"))
        if a.nachher_json["art"] == "verrechnung"
    ]
    assert [a.admin_user_id for a in verrechnung] == [admin]
    belege = dauerbuchungen.beende(db, d, ab=date(2027, 12, 20), admin_user_id=admin)
    db.commit()
    (beleg,) = belege
    for s in db.scalars(select(Storno)):
        letzter = db.scalars(
            select(Audit)
            .where(Audit.objekt_typ == "storno", Audit.objekt_id == s.id)
            .order_by(Audit.zeitpunkt.desc())
        ).first()
        assert letzter.nachher_json["korrektur_rechnung_id"] == str(beleg.id)


@pytest.mark.parametrize("heute,marker", [(date(2027, 11, 25), "2027"), (date(2027, 3, 1), None)])
def test_review2_3_5_migration_0016_markiert_konten_und_saisonende(
    db: Session, welt, heute: date, marker: str | None
) -> None:
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from beachhub_core.models import AppSetting, LesestandVersion

    f, k = welt
    _abo(db, f, k)
    for zeile in db.scalars(select(LesestandVersion)):
        zeile.geaendert = False
    clock.set_override(db, heute)
    db.commit()
    cfg = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
    cfg.config_file_name = None
    command.stamp(cfg, "head")
    command.downgrade(cfg, "0015")
    command.upgrade(cfg, "0016")
    db.expire_all()
    assert db.get(LesestandVersion, f"konto:{k.id}").geaendert
    zeile = db.get(AppSetting, "guthabenliste_letzter")
    assert (zeile.value if zeile else None) == marker


def test_review2_8_antwort_meldet_korrekturbeleg(db: Session, welt) -> None:
    f, k = welt
    d, _ = _abo(db, f, k)
    a = _absagen(db, k, d.buchungen[0]).antwort
    assert a.freie_absage is True and a.korrekturbeleg is True
