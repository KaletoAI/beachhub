"""Regressionen der Gesamtprüfung: Verwaltungszugang und Transaktionsgrenzen."""

import base64
import re
import uuid
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest
from beachhub_core import clock, jobs, mail
from beachhub_core.database import SessionLocal, engine
from beachhub_core.models import (
    Audit,
    Buchung,
    Dauerbuchung,
    GuthabenBuchung,
    Gutschein,
    GutscheinEinloesung,
    Kunde,
    Rechnung,
    Sperre,
    Storno,
)
from beachhub_core.services import (
    anfragen,
    dauerbuchungen,
    gutschein_codes,
    gutschein_kauf,
    gutschein_sperren,
    gutscheine,
    online_buchung,
    pin,
    rechnung_pdf,
    sperren,
    storno,
)
from beachhub_shared import kanal
from beachhub_shared.zeit import kombiniere
from hilfen_gutschein import einloesung, kaufgutschein
from sqlalchemy import event, select


def _abruf(db, kunde, nummer):
    return anfragen.bearbeite(
        db,
        kanal.Anfrage(
            anfrage_id=uuid.uuid4(),
            typ="rechnung_anfordern",
            konto_id=kunde.portal_konto_id,
            nutzlast={"rechnung_nr": nummer},
            erstellt_am=datetime.now(UTC),
        ),
    )[0]


@pytest.mark.parametrize("steuer", ["beim_kauf", "bei_einloesung"])
def test_rueckgabe_gegenbeleg_pdf_nur_kaeufer(eingeloggt, db, gwelt, mail_ausgang, steuer):
    gwelt.mitglied.portal_konto_id = uuid.uuid4()
    gwelt.nichtmitglied.portal_konto_id = uuid.uuid4()
    g = kaufgutschein(
        db,
        gwelt,
        code="ABCDEFGHJKLMN",
        steuer=steuer,
        inhaber_kunde_id=gwelt.nichtmitglied.id,
        empfaenger_email="geschenk@example.org",
    )
    gid = g.id
    db.commit()
    r = eingeloggt.post(
        f"/admin/gutscheine/{gid}/rueckgabe",
        data={"csrf_token": eingeloggt.csrf, "grund": "Rückgabe"},
    )
    assert r.status_code == 200
    gegen = db.scalar(select(Rechnung).where(Rechnung.art == "storno"))
    assert gegen.pdf_pfad and Path(gegen.pdf_pfad).is_file()
    assert gegen.kunde_id == gwelt.mitglied.id
    assert [m["an"] for m in mail_ausgang] == [gwelt.mitglied.email]
    antwort = _abruf(db, gwelt.mitglied, gegen.nummer)
    assert antwort.status == "ok"
    assert base64.b64decode(antwort.pdf_base64) == Path(gegen.pdf_pfad).read_bytes()
    assert _abruf(db, gwelt.nichtmitglied, gegen.nummer).grund == "nicht_gefunden"
    eingeloggt.post(
        f"/admin/gutscheine/{gid}/rueckgabe",
        data={"csrf_token": eingeloggt.csrf, "grund": "Wiederholung"},
    )
    assert db.query(Rechnung).count() == 2
    assert len(mail_ausgang) == 1


@pytest.mark.parametrize("phase", ["pdf", "smtp"])
def test_rueckgabe_nachlauffehler_behaelt_finanzcommit(eingeloggt, db, gwelt, monkeypatch, phase):
    g = kaufgutschein(db, gwelt, code="ABCDEFGHJKLMN")
    gid = g.id
    db.commit()

    versucht = []

    def kaputt(*args, **kwargs):
        versucht.append(phase)
        # Finanzzustand ist schon außerhalb der Route sichtbar.
        with SessionLocal() as andere:
            assert andere.get(Gutschein, gid).zurueckgegeben_am is not None
            assert andere.query(Rechnung).filter_by(art="storno").count() == 1
        raise RuntimeError("Nachlauf nicht verfügbar")

    monkeypatch.setattr(
        rechnung_pdf if phase == "pdf" else mail, "erzeuge" if phase == "pdf" else "sende", kaputt
    )
    r = eingeloggt.post(
        f"/admin/gutscheine/{gid}/rueckgabe",
        data={"csrf_token": eingeloggt.csrf, "grund": "Rückgabe"},
    )
    assert r.status_code == 200
    assert versucht
    db.expire_all()
    assert db.get(Gutschein, gid).zurueckgegeben_am is not None
    assert db.query(Rechnung).count() == 2 and db.query(GuthabenBuchung).count() == 0


def test_liste_1000_gleiche_filter_vollstaendig_stabil(eingeloggt, db, gwelt):
    codes = gutscheine.stelle_freicodes_aus(
        db, anzahl=1000, gueltig_bis=None, serie="Aktion & Sommer", admin_user_id=None
    )
    # Gleicher Zeitstempel erzwingt die UUID als eindeutigen Tie-Breaker.
    for g in codes:
        g.ausgestellt_am = kombiniere(date(2027, 11, 25), time(10))
    erwartet = {str(g.id) for g in codes}
    db.commit()
    url = "/admin/gutscheine?art=frei&status=aktiv&serie=Aktion+%26+Sommer"
    gesehen = []
    for _ in range(3):
        html = eingeloggt.get(url).text
        gesehen.extend(re.findall(r'href="/admin/gutscheine/([0-9a-f-]{36})"', html))
        weiter = re.search(r'href="([^"]+)"[^>]*rel="next"', html)
        if weiter is None:
            break
        from html import unescape

        url = unescape(weiter.group(1))
        assert "art=frei" in url and "status=aktiv" in url and "serie=Aktion" in url
    assert len(gesehen) == 1000 and set(gesehen) == erwartet
    assert len(set(gesehen)) == len(gesehen)
    assert eingeloggt.get(f"/admin/gutscheine/{gesehen[-1]}").status_code == 200


def test_liste_kaeufer_inhaber_gesammelt_und_offene_codes_alle_seiten(eingeloggt, db, gwelt):
    codes = gutscheine.stelle_freicodes_aus(db, anzahl=500, gueltig_bis=None, admin_user_id=None)
    inhaber = [
        Kunde(id=uuid.uuid4(), name=f"Kunde {i}", email=f"kunde{i}@example.org") for i in range(499)
    ]
    db.add_all(inhaber)
    db.flush()
    for g, kunde in zip(codes[1:], inhaber, strict=True):
        g.inhaber_kunde_id = kunde.id
    geheim = []
    for status in ("offen", "abgebrochen"):
        g = kaufgutschein(
            db,
            gwelt,
            code=gutschein_codes.neuer_code(db),
            status=status,
            gueltig_bis=None,
            kaufdatum=None,
            inhaber_kunde_id=gwelt.nichtmitglied.id,
        )
        g.ausgestellt_am = kombiniere(
            date(2027, 11, 24 if status == "abgebrochen" else 26), time(10)
        )
        geheim.extend([g.code, gutschein_codes.anzeige(g.code)])
    kaufgutschein(db, gwelt, code="ABCDEFGHJKLMN", inhaber_kunde_id=gwelt.nichtmitglied.id)
    db.commit()
    sqls = []

    def beobachte(conn, cursor, statement, parameters, context, executemany):
        sqls.append(statement.lower())

    event.listen(engine, "before_cursor_execute", beobachte)
    try:
        seiten = [eingeloggt.get(f"/admin/gutscheine?seite={n}").text for n in (1, 2)]
    finally:
        event.remove(engine, "before_cursor_execute", beobachte)
    assert all(code not in html for code in geheim for html in seiten)
    assert all("Kauf nicht abgeschlossen" in html for html in seiten)
    assert all("<th>Käufer</th>" in html and "<th>Inhaber</th>" in html for html in seiten)
    html = "".join(seiten)
    assert f'href="/admin/kunden/{gwelt.mitglied.id}">Mitglied</a>' in html
    assert f'href="/admin/kunden/{gwelt.nichtmitglied.id}">Gast</a>' in html
    assert sum("from kunde" in sql for sql in sqls) <= 2


def _zustand(db):
    db.expire_all()
    return (
        tuple(
            db.query(m).count()
            for m in (
                Audit,
                Buchung,
                Dauerbuchung,
                Rechnung,
                Sperre,
                Storno,
                GutscheinEinloesung,
                GuthabenBuchung,
            )
        ),
        tuple(db.execute(select(Buchung.id, Buchung.status).order_by(Buchung.id))),
        tuple(
            db.execute(
                select(Gutschein.id, Gutschein.status, Gutschein.restwert).order_by(Gutschein.id)
            )
        ),
        tuple(
            db.execute(select(Kunde.id, Kunde.guthaben, Kunde.rechnungskunde).order_by(Kunde.id))
        ),
    )


def _erzwinge_retry(monkeypatch, modul, name, dauerhaft):
    echt = getattr(modul, name)
    versuche = []

    def vorgang(db, *args, **kwargs):
        ergebnis = echt(db, *args, **kwargs)
        # Fehler nach realen Finanz-/Auditänderungen prüft den kompletten Rollback,
        # einschließlich Rechnungskundenflag bei der Daueranlage.
        versuche.append(1)
        if dauerhaft or len(versuche) == 1:
            raise gutschein_sperren.GutscheinVorbereitungWiederholen("Beziehungen geändert")
        return ergebnis

    monkeypatch.setattr(modul, name, vorgang)
    return versuche


@pytest.mark.parametrize("vorgang", ["storno", "kulanz", "sperre", "daueranlage", "beenden"])
@pytest.mark.parametrize("dauerhaft", [False, True])
def test_admin_ganzer_vorgang_retry_oder_begrenzt_ohne_teilwirkung(
    eingeloggt, db, gwelt, monkeypatch, mail_ausgang, vorgang, dauerhaft
):
    g = kaufgutschein(db, gwelt, code="ABCDEFGHJKLMN")
    e = einloesung(db, gwelt, g, status="bestaetigt")
    g.status = "eingeloest"
    b = db.get(Buchung, e.buchung_id)
    bid = b.id
    daten = {"csrf_token": eingeloggt.csrf, "grund": "Betreiber", "kostenfrei": "ja"}
    if vorgang == "kulanz":
        storno.storniere(db, b, durch="betreiber", kostenfrei=False, grund="Absage")
    if vorgang == "beenden":
        d = Dauerbuchung(
            kunde_id=b.kunde_id,
            feld_id=b.feld_id,
            wochentag=2,
            start=time(19),
            ende=time(21),
            gueltig_von=date(2027, 12, 1),
            gueltig_bis=date(2027, 12, 1),
            pin_hash=pin.hash("123456"),
            pin_verschluesselt=pin.verschluessele("123456"),
        )
        db.add(d)
        db.flush()
        b.dauerbuchung_id = d.id
        url = f"/admin/belegung/dauer/{d.id}/beenden"
        daten["ab"] = "2027-12-01"
        modul, methode = dauerbuchungen, "beende"
    elif vorgang == "sperre":
        url = "/admin/belegung/sperre"
        daten.update(feld_ids=str(b.feld_id), beginn="2027-12-01T19:00", ende="2027-12-01T21:00")
        daten[f"entscheidung_{bid}"] = "stornieren"
        modul, methode = sperren, "lege_an"
    elif vorgang == "daueranlage":
        gwelt.nichtmitglied.mitglied_bis = date(2030, 12, 31)
        assert not gwelt.nichtmitglied.rechnungskunde
        url = "/admin/belegung/dauer"
        daten.update(
            kunde_id=str(gwelt.nichtmitglied.id),
            feld_id=str(b.feld_id),
            wochentag="2",
            start="19:00",
            ende="21:00",
            gueltig_von="2027-12-01",
            gueltig_bis="2027-12-01",
            rechnungskunde_setzen="1",
        )
        daten[f"entscheidung_{bid}"] = "stornieren"
        modul, methode = dauerbuchungen, "lege_an"
    else:
        url = f"/admin/belegung/buchung/{bid}/{vorgang}"
        modul, methode = storno, "storniere" if vorgang == "storno" else "kulanz"
    db.commit()
    vorher = _zustand(db)
    db.rollback()
    versuche = _erzwinge_retry(monkeypatch, modul, methode, dauerhaft)
    r = eingeloggt.post(url, data=daten, follow_redirects=False)
    assert len(versuche) == (3 if dauerhaft else 2)
    if dauerhaft:
        assert r.status_code in (200, 303)
        html = r.text if r.status_code == 200 else eingeloggt.get(r.headers["location"]).text
        assert "erneut" in html
        assert _zustand(db) == vorher and mail_ausgang == []
    else:
        assert r.status_code == 303
        db.expire_all()
        assert db.get(Buchung, bid).status == Buchung.STORNIERT
        assert db.get(Buchung, bid).storno.kostenfrei
        assert db.get(GutscheinEinloesung, e.id).status == "rueckgaengig"
        assert db.query(Storno).count() == 1
        assert db.query(Sperre).count() == (1 if vorgang == "sperre" else 0)
        assert db.query(Dauerbuchung).count() == (1 if vorgang in ("daueranlage", "beenden") else 0)
        assert mail_ausgang
        assert len({(m["an"], m["betreff"]) for m in mail_ausgang}) == len(mail_ausgang)
        if vorgang == "daueranlage":
            assert db.get(Kunde, gwelt.nichtmitglied.id).rechnungskunde
        if vorgang == "kulanz":
            assert any("Gutschein" in m["betreff"] for m in mail_ausgang)


@pytest.mark.parametrize("vorgang", ["reservierung", "gutschein", "kauf"])
@pytest.mark.parametrize("dauerhaft", [False, True])
def test_jobs_ganze_batch_retry_oder_begrenzt_ohne_teilwirkung(
    db, gwelt, monkeypatch, mail_ausgang, vorgang, dauerhaft
):
    if vorgang == "reservierung":
        g = kaufgutschein(db, gwelt)
        e = einloesung(db, gwelt, g)
        b = db.get(Buchung, e.buchung_id)
        b.status = Buchung.RESERVIERT
        b.reserviert_bis = clock.now(db) - timedelta(minutes=1)
        modul, methode, job = online_buchung, "verfalle_abgelaufene", jobs.verfall_ausfuehren
    elif vorgang == "gutschein":
        g = kaufgutschein(db, gwelt, gueltig_bis=clock.today(db) - timedelta(days=1))
        modul, methode, job = gutscheine, "verfalle", jobs.gutschein_verfall_ausfuehren
    else:
        g = kaufgutschein(
            db,
            gwelt,
            status="offen",
            gueltig_bis=None,
            kaufdatum=None,
            kauf_reserviert_bis=clock.now(db) - timedelta(minutes=1),
        )
        modul, methode, job = (
            gutschein_kauf,
            "brich_abgelaufene_ab",
            jobs.gutschein_kaufabbruch_ausfuehren,
        )
    gid = g.id
    db.commit()
    vorher = _zustand(db)
    db.rollback()
    versuche = _erzwinge_retry(monkeypatch, modul, methode, dauerhaft)
    if dauerhaft:
        with pytest.raises(gutschein_sperren.GutscheinVorbereitungWiederholen):
            job(db)
        assert len(versuche) == 3
        assert _zustand(db) == vorher and mail_ausgang == []
    else:
        assert job(db) == 1 and len(versuche) == 2
        with SessionLocal() as andere:
            assert (
                andere.get(Gutschein, gid).status
                == {"reservierung": "aktiv", "gutschein": "verfallen", "kauf": "abgebrochen"}[
                    vorgang
                ]
            )
        assert len(mail_ausgang) == (1 if vorgang == "reservierung" else 0)
