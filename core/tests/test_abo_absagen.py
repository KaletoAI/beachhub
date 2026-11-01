from datetime import date, time
from decimal import Decimal

import pytest
from beachhub_core import clock
from beachhub_core.models import (
    Betriebszeit,
    Dauerbuchung,
    Feld,
    FeldRaster,
    GuthabenBuchung,
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


def _jetzt(monkeypatch, tag: int, stunde: int) -> None:
    monkeypatch.setattr(clock, "now", lambda db: kombiniere(date(2027, 12, tag), time(stunde)))


def _abo_gutschriften(db: Session) -> list[GuthabenBuchung]:
    return list(
        db.scalars(select(GuthabenBuchung).where(GuthabenBuchung.art == "abo_gutschrift")).all()
    )


def test_ausgelassene_termine_werden_weder_gebucht_noch_berechnet(db: Session, welt) -> None:
    """Termine, die der Kunde nicht nimmt, stehen schon beim Anlegen fest (Auslassen)."""
    f, k = welt
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
        auslassen={date(2027, 12, 15), date(2027, 12, 22), date(2027, 12, 29)},
        entscheidungen={},
    )
    db.commit()
    r = db.scalars(select(Rechnung).where(Rechnung.dauerbuchung_id == d.id)).one()
    assert [b.beginn.date() for b in d.buchungen] == [date(2027, 12, 1), date(2027, 12, 8)]
    assert r.brutto == Decimal("60.00") and len(r.positionen) == 2


def test_einstellung_abo_freie_absagen_entfaellt() -> None:
    assert "abo_freie_absagen" not in konfiguration.DEFAULTS
    assert "abo_freie_absagen" not in konfiguration.BESCHREIBUNGEN


def test_rechtzeitige_absagen_werden_guthaben_ohne_beleg(db: Session, welt) -> None:
    """Kein Kontingent: Jede rechtzeitige Absage wird Guthaben, die Saisonrechnung bleibt."""
    f, k = welt
    d, r = _abo(db, f, k)
    belege = db.query(Rechnung).count()
    termine = list(d.buchungen)
    antworten = [_absagen(db, k, b).antwort for b in termine[:4]]
    assert [(a.kostenfrei, a.abo_gutschrift, a.korrekturbeleg) for a in antworten] == [
        (True, Decimal("30.00"), False)
    ] * 4
    assert all(a.freie_absage is None and a.verbleibende_freie_absagen is None for a in antworten)
    assert all(b.status == "storniert" for b in termine[:4])
    # Keine Stornorechnung: Die Saisonrechnung bleibt unverändert und voll offen.
    assert db.query(Rechnung).count() == belege
    assert rechnungen.offener_betrag(db, r) == Decimal("150.00")
    assert all(p.korrigiert_durch_id is None for p in r.positionen)
    db.refresh(k)
    assert k.guthaben == Decimal("120.00")
    stornos = [b.storno for b in termine[:4]]
    assert all(s.korrektur_rechnung_id is None and s.freie_absage is False for s in stornos)
    assert {g.bezug_id for g in _abo_gutschriften(db)} == {s.id for s in stornos}


def test_absage_bei_bezahlter_saisonrechnung_mailt_gutschrift(
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
    assert r.status == "bezahlt"
    betreffs = [m["betreff"] for m in mail_ausgang]
    assert betreffs == ["Stornierung Ihrer Buchung"]
    text = mail_ausgang[0]["text"]
    assert "30,00" in text and "gutgeschrieben" in text and "Saisonrechnung bleibt" in text
    assert "Absagen" not in text


def test_absage_nach_frist_bleibt_berechnet(
    db: Session, welt, monkeypatch, mail_ausgang: list
) -> None:
    f, k = welt
    d, r = _abo(db, f, k)
    erster = d.buchungen[0]  # 01.12. 19:00
    _jetzt(monkeypatch, 1, 10)
    erg = _absagen(db, k, erster)
    a = erg.antwort
    assert a.kostenfrei is False and a.abo_gutschrift == Decimal("0.00")
    assert erster.status == "storniert" and erster.storno.abo_gutschrift is None
    assert rechnungen.offener_betrag(db, r) == Decimal("150.00")
    db.refresh(k)
    assert k.guthaben == Decimal("0.00")
    for schritt in erg.nach_commit:
        schritt(db)
    text = mail_ausgang[-1]["text"]
    assert "bleibt berechnet" in text and "anderer Kunde" in text


def _fremdbuchung(db: Session, f: Feld, kunde: Kunde, tag: int = 1):
    from beachhub_core.services import buchungen

    b = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=kunde.id,
        beginn=kombiniere(date(2027, 12, tag), time(19)),
        ende=kombiniere(date(2027, 12, tag), time(20)),
    )
    db.commit()
    return b


def test_spaete_absage_wird_bei_wiedervergabe_gutgeschrieben(
    db: Session, welt, monkeypatch, mail_ausgang: list
) -> None:
    from beachhub_core import jobs

    f, k = welt
    d, r = _abo(db, f, k)
    erster = d.buchungen[0]
    _jetzt(monkeypatch, 1, 10)
    _absagen(db, k, erster)
    andere = kunden.lege_an(db, name="Gast", email="gast@x.de")
    _fremdbuchung(db, f, andere)
    # Vor dem Ende des Termins wird nichts gutgeschrieben.
    assert storno.wiedervergabe_kandidaten(db) == []
    _jetzt(monkeypatch, 1, 21)
    assert jobs.abo_wiedervergabe_ausfuehren(db) == 1
    db.expire_all()
    s = erster.storno
    assert s.kostenfrei is True and s.abo_gutschrift == Decimal("30.00")
    assert "Platz wieder vergeben" in s.grund
    assert k.guthaben == Decimal("30.00")
    assert rechnungen.offener_betrag(db, r) == Decimal("150.00")
    (m,) = mail_ausgang
    assert m["betreff"] == "Gutschrift für Ihren abgesagten Abo-Termin"
    assert "30,00" in m["text"]
    # Genau einmal.
    assert jobs.abo_wiedervergabe_ausfuehren(db) == 0
    assert len(_abo_gutschriften(db)) == 1


@pytest.mark.parametrize("fall", ["ohne", "eigene", "kostenfrei_storniert", "anderes_feld"])
def test_spaete_absage_ohne_wiedervergabe_bleibt_berechnet(
    db: Session, welt, monkeypatch, fall: str
) -> None:
    from beachhub_core import jobs

    f, k = welt
    d, r = _abo(db, f, k)
    _jetzt(monkeypatch, 1, 10)
    _absagen(db, k, d.buchungen[0])
    if fall == "eigene":
        _fremdbuchung(db, f, k)
    elif fall == "kostenfrei_storniert":
        andere = kunden.lege_an(db, name="Gast", email="gast@x.de")
        b = _fremdbuchung(db, f, andere)
        storno.storniere(db, b, durch="betreiber", kostenfrei=True)
        db.commit()
    elif fall == "anderes_feld":
        f2 = Feld(name="F2", reihenfolge=2)
        f2.raster.append(
            FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[])
        )
        db.add(f2)
        db.commit()
        andere = kunden.lege_an(db, name="Gast", email="gast@x.de")
        _fremdbuchung(db, f2, andere)
    _jetzt(monkeypatch, 1, 21)
    assert jobs.abo_wiedervergabe_ausfuehren(db) == 0
    db.expire_all()
    assert d.buchungen[0].storno.kostenfrei is False
    assert _abo_gutschriften(db) == []
    assert rechnungen.offener_betrag(db, r) == Decimal("150.00")


def test_kulanz_betreiber_und_sperre_werden_abo_gutschrift(db: Session, welt, monkeypatch) -> None:
    from beachhub_core.services import sperren

    f, k = welt
    d, r = _abo(db, f, k)
    belege = db.query(Rechnung).count()
    _jetzt(monkeypatch, 1, 10)
    _absagen(db, k, d.buchungen[0])
    monkeypatch.undo()
    s = d.buchungen[0].storno
    storno.kulanz(db, s, admin_user_id=None, grund="Krankheit")
    storno.storniere(db, d.buchungen[1], durch="betreiber", grund="Turnier")
    termin = d.buchungen[2]
    sperren.lege_an(
        db,
        feld_ids=[f.id],
        beginn=termin.beginn,
        ende=termin.ende,
        grund="Wartung",
        admin_user_id=None,
        entscheidungen={termin.id: "stornieren"},
    )
    db.commit()
    db.expire_all()
    assert [b.storno.abo_gutschrift for b in d.buchungen[:3]] == [Decimal("30.00")] * 3
    assert all(b.storno.korrektur_rechnung_id is None for b in d.buchungen[:3])
    assert db.query(Rechnung).count() == belege
    assert rechnungen.offener_betrag(db, r) == Decimal("150.00")
    assert k.guthaben == Decimal("90.00")


def test_abo_gutschrift_genau_einmal(db: Session, welt) -> None:
    f, k = welt
    d, _ = _abo(db, f, k)
    _absagen(db, k, d.buchungen[0])
    s = d.buchungen[0].storno
    assert storno.abo_gutschreiben(db, s, quelle="admin") == Decimal("0.00")
    storno.kulanz(db, s, admin_user_id=None, grund="doppelt")
    db.commit()
    assert len(_abo_gutschriften(db)) == 1
    assert k.guthaben == Decimal("30.00")


def test_beenden_bleibt_stornorechnung(db: Session, welt) -> None:
    f, k = welt
    d, r = _abo(db, f, k)
    _absagen(db, k, d.buchungen[0])
    belege = dauerbuchungen.beende(db, d, ab=date(2027, 12, 20), admin_user_id=None)
    db.commit()
    (beleg,) = belege
    # Beleg nur über die beiden noch aktiven Termine 22.12. und 29.12.
    assert beleg.brutto == Decimal("-60.00")
    assert rechnungen.offener_betrag(db, r) == Decimal("90.00")
    assert d.buchungen[0].storno.korrektur_rechnung_id is None
    assert k.guthaben == Decimal("30.00")


def test_teilstorno_gutgeschriebener_position_wird_abgelehnt(db: Session, welt) -> None:
    f, k = welt
    d, r = _abo(db, f, k)
    _absagen(db, k, d.buchungen[0])
    pos = next(p for p in r.positionen if p.buchung_id == d.buchungen[0].id)
    with pytest.raises(rechnungen.RechnungsFehler, match="abo_gutgeschrieben"):
        storno.gutschreiben_positionen(db, [pos], grund="Kulanz", quelle="admin")
    db.rollback()
    with pytest.raises(rechnungen.RechnungsFehler, match="abo_gutgeschrieben"):
        rechnungen.korrigiere(db, [pos], grund="Kulanz", quelle="admin")
    db.rollback()


def test_vollstorno_und_neuausstellung_berechnen_gutgeschriebenen_termin_wieder(
    db: Session, welt
) -> None:
    """Die Gutschrift ersetzt die Korrektur: Bei Neuausstellung bleibt der Termin Teil der Saison,
    sonst erhielte der Kunde ihn doppelt erstattet."""
    f, k = welt
    d, r = _abo(db, f, k)
    rechnungen.setze_bezahlt(db, r, admin_user_id=None)
    db.commit()
    _absagen(db, k, d.buchungen[0])
    assert k.guthaben == Decimal("30.00")
    rechnungen.storniere(db, r, admin_user_id=None, grund="Adresse")
    db.commit()
    db.refresh(k)
    assert k.guthaben == Decimal("180.00")
    neu = rechnungen.erzeuge_saisonrechnung(db, d)
    db.commit()
    assert neu.brutto == Decimal("150.00") and len(neu.positionen) == 5
    assert "als Guthaben gutgeschrieben" in neu.positionen[0].text
    assert neu.status == "bezahlt"
    db.refresh(k)
    assert k.guthaben == Decimal("30.00")


@pytest.mark.parametrize("nach_frist", [False, True])
def test_bereits_korrigierter_termin_bleibt_kostenfrei_ohne_gutschrift(
    db: Session, welt, nach_frist: bool, mail_ausgang: list
) -> None:
    """Ein per Teil-Storno bereits korrigierter Termin bleibt kostenfrei, auch nach der Frist –
    es gibt nichts mehr zu berechnen oder gutzuschreiben."""
    f, k = welt
    d, r = _abo(db, f, k)
    rechnungen.setze_bezahlt(db, r, admin_user_id=None)
    if nach_frist:
        konfiguration.setze(db, "storno_frist_stunden", 240)
    storno.gutschreiben_positionen(db, [r.positionen[0]], grund="Ausfall", quelle="admin")
    db.commit()
    vorher = k.guthaben
    belege = db.query(Rechnung).count()
    erg = _absagen(db, k, d.buchungen[0])
    assert erg.antwort.kostenfrei is True and erg.antwort.abo_gutschrift == Decimal("0.00")
    assert erg.antwort.korrekturbeleg is False
    assert k.guthaben == vorher and db.query(Rechnung).count() == belege
    for nachlauf in erg.nach_commit:
        nachlauf(db)
    (m,) = mail_ausgang
    assert "kostenfrei" in m["text"] and "gutgeschrieben" not in m["text"]


def test_unberechneter_termin_wird_nach_absage_nicht_mehr_berechnet(db: Session, welt) -> None:
    from test_saisonrechnung import _altabo

    f, k = welt
    d = _altabo(db, f, k)
    antworten = [_absagen(db, k, b).antwort for b in d.buchungen[:3]]
    assert [(a.kostenfrei, a.abo_gutschrift) for a in antworten] == [(True, Decimal("0.00"))] * 3
    neu = rechnungen.erzeuge_saisonrechnung(db, d)
    db.commit()
    assert neu.brutto == Decimal("30") and len(neu.positionen) == 1
    assert k.guthaben == Decimal("0.00")


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
    assert "Absagen" not in text and "anderer Kunde" not in text


def test_konto_zeigt_abo_ohne_kontingent(db: Session, welt) -> None:
    f, k = welt
    d, _ = _abo(db, f, k)
    _absagen(db, k, d.buchungen[0])
    je_id = {b.id: b for b in lesestand.baue_konto(db, k).buchungen}
    zweiter = je_id[str(d.buchungen[1].id)]
    assert zweiter.abo is True and zweiter.stornierbar is True
    assert zweiter.freie_absagen_rest is None


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


def test_antwort_meldet_abo_gutschrift_ohne_korrekturbeleg(db: Session, welt) -> None:
    f, k = welt
    d, _ = _abo(db, f, k)
    a = _absagen(db, k, d.buchungen[0]).antwort
    assert a.korrekturbeleg is False and a.abo_gutschrift == Decimal("30.00")


def _alembic():
    from pathlib import Path

    from alembic.config import Config

    cfg = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
    cfg.config_file_name = None
    return cfg


def test_migration_0017_entfernt_kontingent_und_markiert_konten(db: Session, welt) -> None:
    from alembic import command
    from beachhub_core.database import engine
    from beachhub_core.models import Konfiguration, LesestandVersion
    from sqlalchemy import inspect, text

    f, k = welt
    _abo(db, f, k)
    cfg = _alembic()
    command.stamp(cfg, "head")
    command.downgrade(cfg, "0016")
    assert "abo_gutschrift" not in {c["name"] for c in inspect(engine).get_columns("storno")}
    db.execute(
        text(
            "INSERT INTO konfiguration (schluessel, wert, typ) "
            "VALUES ('abo_freie_absagen', '3', 'int')"
        )
    )
    for zeile in db.scalars(select(LesestandVersion)):
        zeile.geaendert = False
    db.commit()
    command.upgrade(cfg, "0017")
    db.expire_all()
    assert "abo_gutschrift" in {c["name"] for c in inspect(engine).get_columns("storno")}
    assert db.get(Konfiguration, "abo_freie_absagen") is None
    assert db.get(LesestandVersion, f"konto:{k.id}").geaendert


def test_migration_0017_downgrade_schuetzt_abo_gutschriften(db: Session, welt) -> None:
    from alembic import command

    f, k = welt
    d, _ = _abo(db, f, k)
    _absagen(db, k, d.buchungen[0])
    cfg = _alembic()
    command.stamp(cfg, "head")
    with pytest.raises(RuntimeError, match="Downgrade 0017 verweigert"):
        command.downgrade(cfg, "0016")
    db.expire_all()
    assert d.buchungen[0].storno.abo_gutschrift == Decimal("30.00")
