"""Kaufbedingungen, verifizierte Zahlung, Geheimhaltung und Nachlauf mit echtem PostgreSQL."""

import json
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from beachhub_core import clock, jobs, zahlung
from beachhub_core.config import settings
from beachhub_core.models import GuthabenBuchung, Gutschein, Rechnung, Zahlung
from beachhub_core.services import (
    anfragen,
    guthaben,
    gutschein_codes,
    konfiguration,
    online_buchung,
    rechnung_pdf,
)
from beachhub_shared import kanal
from hilfen_gutschein import kaufgutschein
from sqlalchemy import select
from sqlalchemy.orm import Session


def _wunsch(db, kunde, **nutzlast):
    if kunde.portal_konto_id is None:
        kunde.portal_konto_id = uuid.uuid4()
        db.commit()
    return anfragen.bearbeite(
        db,
        kanal.Anfrage(
            anfrage_id=uuid.uuid4(),
            typ="gutschein_kaufen",
            konto_id=kunde.portal_konto_id,
            erstellt_am=clock.now(db),
            nutzlast=nutzlast,
        ),
    )


def _callback(db, g, betrag="30.00", zustand="bezahlt"):
    z = db.get(Zahlung, g.zahlung_id)
    return online_buchung.zahlung_eingegangen(
        db,
        kanal.ZahlungEingegangen(
            provider="fake",
            rohdaten=json.dumps({"ref": z.provider_ref, "ergebnis": zustand, "betrag": betrag}),
        ),
    )


def _offen(db, gwelt):
    g = kaufgutschein(
        db,
        gwelt,
        status="offen",
        kaufdatum=None,
        gueltig_bis=None,
        kaufpreis_brutto=Decimal("30.00"),
        kauf_reserviert_bis=clock.now(db) + timedelta(minutes=15),
    )
    z = db.get(Zahlung, g.zahlung_id)
    g.code = gutschein_codes.neuer_code(db)
    z.provider, z.provider_ref = "fake", "fake_" + uuid.uuid4().hex
    db.commit()
    return g


def _nachlauf(db, erg):
    db.commit()
    for lauf in erg.nach_commit:
        lauf(db)


def test_kaufwunsch_speichert_snapshot_ohne_guthaben_oder_code(db: Session, gwelt, mail_ausgang):
    k = gwelt.mitglied
    guthaben.buche(db, kunde=k, betrag=Decimal("100.00"), art="manuell")
    db.commit()
    a, nach = _wunsch(db, k, empfaenger_email="Geschenk@example.org")
    assert a.status == "reserviert"
    assert a.preis == a.zu_zahlen == Decimal("26.00")
    assert a.guthaben_verrechnet == Decimal("0.00") and nach == []
    g = db.scalar(select(Gutschein))
    z = db.get(Zahlung, g.zahlung_id)
    assert (g.status, g.modell, g.minuten, g.steuer) == ("offen", "einheit", 120, "beim_kauf")
    assert (g.kundengruppe_id, g.ust_satz, g.nur_mitglieder) == (
        gwelt.mitglied_gruppe.id,
        Decimal("7.00"),
        True,
    )
    assert g.gueltig_tage_snapshot == 730 and g.kaufdatum is None and g.gueltig_bis is None
    assert g.kauf_reserviert_bis == a.reserviert_bis and z.checkout_url == a.checkout_url
    assert (
        g.empfaenger_email == "geschenk@example.org"
        and g.kaeufer_kunde_id == g.inhaber_kunde_id == k.id
    )
    assert k.guthaben == Decimal("100.00") and db.scalar(select(Rechnung)) is None
    assert g.code not in a.model_dump_json() and mail_ausgang == []


@pytest.mark.parametrize(
    "modell,steuer,betrag,preis",
    [("einheit", "beim_kauf", None, "26.00"), ("wert", "bei_einloesung", "50.00", "50.00")],
)
def test_kaufabschluss_und_beleg_nach_commit(
    db, gwelt, mail_ausgang, modell, steuer, betrag, preis
):
    konfiguration.setze(db, "gutschein_modell", modell)
    konfiguration.setze(db, "gutschein_steuer", steuer)
    a, _ = _wunsch(
        db,
        gwelt.mitglied,
        empfaenger_email="beschenkt@example.org",
        **({"betrag": betrag} if betrag else {}),
    )
    assert a.status == "reserviert"
    g = db.scalar(select(Gutschein))
    erg = _callback(db, g, preis)
    assert g.status == "aktiv" and mail_ausgang == []
    r = db.get(Rechnung, g.rechnung_id)
    assert r.pdf_pfad is None and r.kunde_id == gwelt.mitglied.id and r.gutschein_rolle == "kauf"
    assert r.brutto == Decimal(preis) and r.art == "gutschein" and r.status == "bezahlt"
    assert r.leistung_von == r.leistung_bis == g.kaufdatum == clock.today(db)
    assert g.gueltig_bis == g.kaufdatum + timedelta(days=730)
    html = rechnung_pdf.html(r)
    if steuer == "bei_einloesung":
        assert r.nummer.startswith("GB-") and r.ust == 0 and r.netto == Decimal("50.00")
        assert "USt" not in html and "Mehrzweckgutschein" in html and "Kaufbeleg" in html
        assert g.restwert == g.nennwert_brutto == Decimal("50.00") and not g.nur_mitglieder
    else:
        assert not r.nummer.startswith("GB-") and r.positionen[0].ust_satz == Decimal("7.00")
        assert r.netto == Decimal("24.30") and r.ust == Decimal("1.70")
        assert "nur für DJK-Mitglieder einlösbar" in html
    _nachlauf(db, erg)
    assert r.pdf_pfad is not None
    code = gutschein_codes.anzeige(g.code)
    gekauft = next(m for m in mail_ausgang if m["an"] == gwelt.mitglied.email and code in m["text"])
    geschenk = next(m for m in mail_ausgang if m["an"] == "beschenkt@example.org")
    assert code in geschenk["text"] and geschenk["anhaenge"] == []
    assert (
        "nur für DJK-Mitglieder" in gekauft["text"]
        if g.nur_mitglieder
        else "für alle einlösbar" in gekauft["text"]
    )
    assert any(
        m["an"] == gwelt.mitglied.email and m["anhaenge"] == [r.nummer + ".pdf"]
        for m in mail_ausgang
    )


@pytest.mark.parametrize("verspaetung", [0, 1])
def test_callback_nach_frist_ohne_job(db, gwelt, monkeypatch, verspaetung):
    g = _offen(db, gwelt)
    frist = g.kauf_reserviert_bis
    monkeypatch.setattr(clock, "now", lambda db: frist + timedelta(seconds=verspaetung))
    erg = _callback(db, g)
    db.commit()
    assert (g.status, gwelt.mitglied.guthaben) == ("abgebrochen", Decimal("30.00"))
    assert db.scalar(select(Rechnung)) is None and len(erg.nach_commit) == 1
    assert _callback(db, g).nach_commit == []
    db.commit()
    assert len(db.scalars(select(GuthabenBuchung)).all()) == 1


def test_fristsnapshot_nach_einstellungswechsel(db, gwelt, monkeypatch):
    a, _ = _wunsch(db, gwelt.mitglied)
    assert a.status == "reserviert"
    g = db.scalar(select(Gutschein))
    frist = g.kauf_reserviert_bis
    konfiguration.setze(db, "zahlungsfrist_minuten", 120)
    konfiguration.setze(db, "gutschein_preis_mitglied", Decimal("99.00"))
    konfiguration.setze(db, "gutschein_minuten", 60)
    konfiguration.setze(db, "gutschein_gueltig_tage", 1)
    konfiguration.setze(db, "gutschein_steuer", "bei_einloesung")
    gwelt.mitglied.mitglied_bis = None
    gwelt.mitglied_gruppe.ust_satz = Decimal("19.00")
    db.commit()
    monkeypatch.setattr(clock, "now", lambda db: frist - timedelta(seconds=1))
    _callback(db, g, "26.00")
    db.commit()
    r = db.get(Rechnung, g.rechnung_id)
    assert (g.kaufpreis_brutto, g.minuten, g.gueltig_tage_snapshot, g.steuer, g.ust_satz) == (
        Decimal("26.00"),
        120,
        730,
        "beim_kauf",
        Decimal("7.00"),
    )
    assert r.ust == Decimal("1.70") and g.nur_mitglieder and g.kauf_reserviert_bis == frist


@pytest.mark.parametrize(
    "betrag,status,guthaben,belege",
    [
        ("10.00", "abgebrochen", "10.00", 0),
        ("35.00", "aktiv", "5.00", 1),
        ("30.00", "aktiv", "0.00", 1),
    ],
)
def test_doppelte_zahlung_kein_zweiter_beleg(db, gwelt, betrag, status, guthaben, belege):
    g = _offen(db, gwelt)
    erg = _callback(db, g, betrag)
    db.commit()
    assert (g.status, gwelt.mitglied.guthaben) == (status, Decimal(guthaben))
    assert len(db.scalars(select(Rechnung)).all()) == belege
    assert _callback(db, g, betrag).nach_commit == []
    db.commit()
    assert len(db.scalars(select(Rechnung)).all()) == belege
    assert len(db.scalars(select(GuthabenBuchung)).all()) == (1 if Decimal(guthaben) else 0)
    assert len(erg.nach_commit) >= 1


def test_kaufabbruch_job_und_spaetzahlung(db, gwelt, monkeypatch, mail_ausgang):
    g = _offen(db, gwelt)
    monkeypatch.setattr(clock, "now", lambda db: g.kauf_reserviert_bis)
    assert jobs.gutschein_kaufabbruch_ausfuehren(db) == 1
    assert g.status == "abgebrochen" and db.get(Zahlung, g.zahlung_id).status == "abgebrochen"
    assert jobs.gutschein_kaufabbruch_ausfuehren(db) == 0 and mail_ausgang == []
    erg = _callback(db, g)
    _nachlauf(db, erg)
    assert g.status == "abgebrochen" and gwelt.mitglied.guthaben == Decimal("30.00")
    assert db.scalar(select(Rechnung)) is None
    assert any(m["an"] == settings.email_from for m in mail_ausgang)


@pytest.mark.parametrize("betrag", ["0", "-1", "NaN", "sNaN", "Infinity", "-Infinity"])
def test_unplausibler_providerbetrag_kein_kauf_kein_guthaben(db, gwelt, monkeypatch, betrag):
    g = _offen(db, gwelt)
    monkeypatch.setattr(zahlung.anbieter(), "status", lambda ref: ("bezahlt", Decimal(betrag)))
    erg = _callback(db, g)
    db.commit()
    assert erg.antwort.status == "ignoriert" and erg.antwort.grund == "betrag_ungueltig"
    assert g.status == "offen" and db.get(Zahlung, g.zahlung_id).status == "offen"
    assert gwelt.mitglied.guthaben == 0 and db.scalar(select(Rechnung)) is None


def test_providerzuordnung_muss_stimmen(db, gwelt):
    g = _offen(db, gwelt)
    db.get(Zahlung, g.zahlung_id).provider = "anderer"
    db.commit()
    erg = _callback(db, g)
    db.commit()
    assert erg.antwort.status == "ignoriert"
    assert g.status == "offen" and gwelt.mitglied.guthaben == 0


@pytest.mark.parametrize(
    "nutzlast,grund",
    [({"empfaenger_email": "a\nBcc: x@y.org"}, "ungueltig"), ({"betrag": "30.00"}, "ungueltig")],
)
def test_ungueltiger_kaufwunsch(db, gwelt, nutzlast, grund):
    a, _ = _wunsch(db, gwelt.mitglied, **nutzlast)
    assert (a.status, a.grund) == ("abgelehnt", grund)
    assert db.scalar(select(Gutschein)) is None and db.scalar(select(Zahlung)) is None


def test_rechnungskunde_kauft_online_nach_einstellung(db, gwelt):
    gwelt.mitglied.rechnungskunde = True
    a, _ = _wunsch(db, gwelt.mitglied)
    assert a.status == "reserviert"
    konfiguration.setze(db, "rechnungskunden_gutscheinkauf", False)
    a, _ = _wunsch(db, gwelt.mitglied)
    assert (a.status, a.grund) == ("abgelehnt", "rechnungskunde")
    assert len(db.scalars(select(Gutschein)).all()) == 1


@pytest.mark.parametrize("zuerst", ["callback", "job"])
def test_callback_parallel_abbruch(db, gwelt, monkeypatch, zuerst):
    from test_gutschein_parallel import _starte, _warte_auf_lock

    g = _offen(db, gwelt)
    gid, frist = g.id, g.kauf_reserviert_bis
    db.commit()
    monkeypatch.setattr(clock, "now", lambda db: frist)

    def callback(s):
        return _callback(s, s.get(Gutschein, gid)).antwort.status

    def job(s):
        from beachhub_core.services import gutschein_kauf

        return gutschein_kauf.brich_abgelaufene_ab(s)

    erste, zweite = (callback, job) if zuerst == "callback" else (job, callback)
    erste(db)
    pid, future, thread = _starte(zweite)
    try:
        _warte_auf_lock(pid)
    finally:
        db.commit()
    future.result(timeout=5)
    thread.join(timeout=3)
    db.expire_all()
    assert g.status == "abgebrochen" and gwelt.mitglied.guthaben == Decimal("30.00")
    assert db.scalar(select(Rechnung)) is None
    assert len(db.scalars(select(GuthabenBuchung)).all()) == 1


def test_entfernte_kaufzuordnung_erfordert_vollstaendigen_retry(db, gwelt):
    from beachhub_core.services import gutschein_sperren
    from test_gutschein_parallel import _starte, _warte_auf_lock

    g = _offen(db, gwelt)
    gid, zid = g.id, g.zahlung_id
    db.commit()
    gutschein_sperren.bereite_vorgang_vor(db, kunde_ids=[gwelt.mitglied.id], gutschein_ids=[gid])
    pid, future, thread = _starte(lambda s: _callback(s, s.get(Gutschein, gid)))
    try:
        _warte_auf_lock(pid)
        g.zahlung_id = None
    finally:
        db.commit()
    with pytest.raises(gutschein_sperren.GutscheinVorbereitungWiederholen):
        future.result(timeout=5)
    thread.join(timeout=3)
    db.expire_all()
    assert db.get(Zahlung, zid).status == "offen" and gwelt.mitglied.guthaben == 0


def test_mehrzweck_kaufbeleg_mail_semantik(db, gwelt, mail_ausgang):
    g = _offen(db, gwelt)
    g.steuer = "bei_einloesung"
    db.commit()
    _nachlauf(db, _callback(db, g))
    r = db.get(Rechnung, g.rechnung_id)
    belegmail = next(m for m in mail_ausgang if m["anhaenge"])
    assert belegmail["betreff"] == f"Kaufbeleg {r.nummer}"
    assert "Kaufbeleg" in belegmail["text"] and "Mehrzweckgutschein" in belegmail["text"]


def test_nachlauf_buyer_mail_fehler_verhindert_geschenk_und_pdf_nicht(
    db, gwelt, mail_ausgang, monkeypatch
):
    from beachhub_core import mail

    g = _offen(db, gwelt)
    g.empfaenger_email = "geschenk@example.org"
    db.commit()
    sende = mail.sende

    def smtp(an, betreff, text, anhaenge=None):
        if betreff == "Ihr Gutschein":
            raise RuntimeError("SMTP gestört")
        sende(an, betreff, text, anhaenge)

    monkeypatch.setattr(mail, "sende", smtp)
    _nachlauf(db, _callback(db, g))
    assert any(m["an"] == "geschenk@example.org" and not m["anhaenge"] for m in mail_ausgang)
    assert any(m["an"] == gwelt.mitglied.email and m["anhaenge"] for m in mail_ausgang)
    assert any(m["an"] == settings.email_from for m in mail_ausgang)


def test_offener_oder_abgebrochener_kauf_niemals_code_mail(db, gwelt, mail_ausgang):
    from beachhub_core.services import benachrichtigung

    g = _offen(db, gwelt)
    benachrichtigung.gutschein_gekauft(db, g.id)
    g.status = "abgebrochen"
    db.commit()
    benachrichtigung.gutschein_gekauft(db, g.id)
    assert mail_ausgang == []


def test_tatsaechlicher_abschlusstag_startet_gueltigkeit(db, gwelt, monkeypatch):
    from datetime import date, time

    from beachhub_shared.zeit import kombiniere

    wunsch = kombiniere(date(2027, 11, 25), time(23, 55))
    bezahlt = kombiniere(date(2027, 11, 26), time(0, 5))
    monkeypatch.setattr(clock, "now", lambda db: wunsch)
    a, _ = _wunsch(db, gwelt.mitglied)
    assert a.status == "reserviert"
    g = db.scalar(select(Gutschein))
    monkeypatch.setattr(clock, "now", lambda db: bezahlt)
    _callback(db, g, "26.00")
    db.commit()
    assert g.ausgestellt_am == wunsch and g.kaufdatum == date(2027, 11, 26)
    assert g.gueltig_bis == date(2029, 11, 25)
    assert db.get(Rechnung, g.rechnung_id).datum == date(2027, 11, 26)


@pytest.mark.parametrize("providerstatus", ["offen", "unbekannt", "abgebrochen"])
def test_verifizierter_status_muss_zahlung_bestaetigen(db, gwelt, monkeypatch, providerstatus):
    g = _offen(db, gwelt)
    monkeypatch.setattr(
        zahlung.anbieter(), "status", lambda ref: (providerstatus, Decimal("30.00"))
    )
    erg = _callback(db, g)
    db.commit()
    assert g.status == ("abgebrochen" if providerstatus == "abgebrochen" else "offen")
    assert (
        gwelt.mitglied.guthaben == 0 and db.scalar(select(Rechnung)) is None and not erg.nach_commit
    )


def test_angebot_und_kundenzuordnung_bleiben_entscheidung_des_hauptsystems(db, gwelt):
    a, _ = _wunsch(db, gwelt.nichtmitglied)
    assert a.preis == Decimal("30.00")
    konfiguration.setze(db, "gutschein_preis_nichtmitglied", Decimal("0.00"))
    a, _ = _wunsch(db, gwelt.nichtmitglied)
    assert (a.status, a.grund) == ("abgelehnt", "kein_angebot")
    konfiguration.setze(db, "gutschein_modell", "wert")
    a, _ = _wunsch(db, gwelt.nichtmitglied, betrag="33.00")
    assert (a.status, a.grund) == ("abgelehnt", "kein_angebot")
    n = kanal.Anfrage(
        anfrage_id=uuid.uuid4(),
        typ="gutschein_kaufen",
        konto_id=gwelt.nichtmitglied.portal_konto_id,
        kunde_id=gwelt.mitglied.id,
        erstellt_am=clock.now(db),
        nutzlast={"betrag": "50.00"},
    )
    erste = anfragen.bearbeite(db, n)
    zweite = anfragen.bearbeite(db, n)
    assert erste[0] == zweite[0] and zweite[1] == []
    gs = db.scalars(select(Gutschein)).all()
    assert len(gs) == 2 and all(g.kaeufer_kunde_id == gwelt.nichtmitglied.id for g in gs)


def test_nicht_verifizierte_zahlung_ignoriert_kauf(db, gwelt, monkeypatch):
    g = _offen(db, gwelt)
    monkeypatch.setattr(zahlung.anbieter(), "verifiziere", lambda roh, signatur: None)
    erg = _callback(db, g)
    db.commit()
    assert erg.antwort.grund == "nicht_verifiziert" and not erg.nach_commit
    assert (
        g.status == "offen" and gwelt.mitglied.guthaben == 0 and db.scalar(select(Rechnung)) is None
    )


def test_offener_code_auch_im_globalen_audit_geheim(db, gwelt, eingeloggt):
    a, _ = _wunsch(db, gwelt.mitglied)
    assert a.status == "reserviert"
    g = db.scalar(select(Gutschein))
    for pfad in (
        "/admin/gutscheine",
        f"/admin/gutscheine/{g.id}",
        "/admin/system/audit?typ=gutschein",
        "/admin/gutscheine/serie.csv",
    ):
        seite = eingeloggt.get(pfad)
        assert g.code not in seite.text and gutschein_codes.anzeige(g.code) not in seite.text
    _callback(db, g, "10.00")
    db.commit()
    assert g.status == "abgebrochen"
    assert g.code not in eingeloggt.get("/admin/system/audit?typ=gutschein").text


def test_mehrzweck_zeigt_urspruengliche_kaufgruppe_ohne_einloesesperre(db, gwelt, mail_ausgang):
    konfiguration.setze(db, "gutschein_steuer", "bei_einloesung")
    a, _ = _wunsch(db, gwelt.mitglied)
    assert a.status == "reserviert"
    g = db.scalar(select(Gutschein))
    _nachlauf(db, _callback(db, g, "26.00"))
    assert not g.nur_mitglieder
    html = rechnung_pdf.html(db.get(Rechnung, g.rechnung_id))
    assert "Mitgliedsgutschein" in html and "für alle einlösbar" in html
    assert any(
        "Mitgliedsgutschein" in m["text"] and "für alle einlösbar" in m["text"]
        for m in mail_ausgang
    )


def test_geaenderte_zahlungsfrist_verlaengert_kaufwunsch_nicht(db, gwelt, monkeypatch):
    a, _ = _wunsch(db, gwelt.mitglied)
    assert a.status == "reserviert"
    g = db.scalar(select(Gutschein))
    frist = g.kauf_reserviert_bis
    konfiguration.setze(db, "zahlungsfrist_minuten", 120)
    db.commit()
    monkeypatch.setattr(clock, "now", lambda db: frist)
    _callback(db, g, "26.00")
    db.commit()
    assert g.status == "abgebrochen" and gwelt.mitglied.guthaben == Decimal("26.00")
    assert db.scalar(select(Rechnung)) is None


def test_pdf_fehler_verhindert_code_mails_nicht(db, gwelt, mail_ausgang, monkeypatch):
    a, _ = _wunsch(db, gwelt.mitglied, empfaenger_email="geschenk@example.org")
    assert a.status == "reserviert"
    g = db.scalar(select(Gutschein))

    def kaputt(*args):
        raise RuntimeError("PDF gestört")

    monkeypatch.setattr(rechnung_pdf, "erzeuge", kaputt)
    _nachlauf(db, _callback(db, g, "26.00"))
    for email in (gwelt.mitglied.email, "geschenk@example.org"):
        assert any(
            m["an"] == email and gutschein_codes.anzeige(g.code) in m["text"] for m in mail_ausgang
        )
    assert any("PDF nicht erzeugt" in m["betreff"] for m in mail_ausgang)
    assert g.status == "aktiv" and db.get(Rechnung, g.rechnung_id).pdf_pfad is None


def test_deadlineentscheidung_und_abschluss_nutzen_selben_zeitpunkt(db, gwelt, monkeypatch):
    g = _offen(db, gwelt)
    frist = g.kauf_reserviert_bis
    aufrufe = 0

    def fortschreitende_uhr(db):
        nonlocal aufrufe
        aufrufe += 1
        # Eingang und Fristentscheidung vor Ablauf; während Beleganlage überschritten.
        return frist - timedelta(microseconds=1) if aufrufe <= 2 else frist

    monkeypatch.setattr(clock, "now", fortschreitende_uhr)
    erg = _callback(db, g)
    db.commit()
    assert erg.antwort.status == "ok" and g.status == "aktiv" and gwelt.mitglied.guthaben == 0
    assert len(db.scalars(select(Rechnung)).all()) == 1


def test_doppelte_callbacks_parallel_aktivieren_einmal(db, gwelt):
    from test_gutschein_parallel import _starte, _warte_auf_lock

    g = _offen(db, gwelt)
    gid = g.id
    db.commit()
    _callback(db, g, "35.00")
    pid, future, thread = _starte(
        lambda s: _callback(s, s.get(Gutschein, gid), "35.00").nach_commit
    )
    try:
        _warte_auf_lock(pid)
    finally:
        db.commit()
    assert future.result(timeout=5) == []
    thread.join(timeout=3)
    db.expire_all()
    assert g.status == "aktiv" and gwelt.mitglied.guthaben == Decimal("5.00")
    assert (
        len(db.scalars(select(Rechnung)).all())
        == len(db.scalars(select(GuthabenBuchung)).all())
        == 1
    )
