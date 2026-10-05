"""Echte Einlösung, separate Steuerbasis und atomare Online-Buchung."""

import uuid
from datetime import date, time
from decimal import Decimal

import pytest
from beachhub_core.models import (
    Buchung,
    GutscheinEinloesung,
    GutscheinFehlversuch,
    Rechnung,
    Tarif,
    Zahlung,
)
from beachhub_core.services import (
    anfragen,
    guthaben,
    gutschein_codes,
    gutschein_sperren,
    gutscheine,
    konfiguration,
    online_buchung,
)
from beachhub_shared import kanal
from beachhub_shared.zeit import kombiniere
from hilfen_gutschein import D, kaufgutschein
from sqlalchemy import func, select

TERMIN = date(2027, 12, 1)


def code(db, welt, **werte):
    return kaufgutschein(db, welt, code=gutschein_codes.neuer_code(db), **werte)


def buchen(db, welt, codes, *, stunden=3, kunde=None, anfrage_id=None, tag=TERMIN):
    return online_buchung.anfragen(
        db,
        kunde=kunde or welt.mitglied,
        anfrage_id=anfrage_id or uuid.uuid4(),
        feld_id=welt.feld.id,
        beginn=kombiniere(tag, time(19)),
        ende=kombiniere(tag, time(19 + stunden)),
        rueckkehr_url="https://portal.test/",
        gutschein_codes=codes,
    )


def test_einheit_3h_26_euro_gutschein_15_euro_rest(db, gwelt):
    g = code(db, gwelt, steuer="bei_einloesung")
    db.commit()
    erg = buchen(db, gwelt, [g.code])
    assert (erg.antwort.gutschein_verrechnet, erg.antwort.zu_zahlen) == (
        Decimal("30.00"),
        Decimal("15.00"),
    )
    e = db.scalar(select(GutscheinEinloesung))
    assert e.status == "reserviert" and e.beleg_brutto == Decimal("26.00")
    assert db.scalar(
        select(Zahlung).where(Zahlung.buchung_id == erg.antwort.buchung_id)
    ).betrag == Decimal("15.00")


def test_4h_zwei_einheiten(db, gwelt):
    gs = [code(db, gwelt, steuer="bei_einloesung") for _ in range(2)]
    db.commit()
    erg = buchen(db, gwelt, [g.code for g in gs], stunden=4)
    assert erg.antwort.status == "bestaetigt"
    assert erg.antwort.gutschein_verrechnet == Decimal("60.00")
    b = db.get(Buchung, erg.antwort.buchung_id)
    assert b.rechnung_position_id is None and b.zahlungsart == "gutschein"
    belege = db.scalars(select(Rechnung).where(Rechnung.gutschein_rolle == "einloesung")).all()
    assert len(belege) == 2 and sum(r.brutto for r in belege) == Decimal("52.00")
    assert all(p.buchung_id is None for r in belege for p in r.positionen)
    assert all(e.rechnung_position_id for e in db.scalars(select(GutscheinEinloesung)))


@pytest.mark.parametrize("falsch", ["ABCDEFGHJKLMN", "---"])
def test_ungueltiger_zweiter_code_rollt_alles_zurueck(db, gwelt, falsch):
    g = code(db, gwelt)
    db.commit()
    erg = buchen(db, gwelt, [g.code, falsch])
    db.commit()
    assert erg.antwort.status == "abgelehnt"
    assert db.scalars(select(GutscheinEinloesung)).all() == []
    assert db.scalars(select(Buchung)).all() == []
    db.refresh(g)
    assert g.status == "aktiv"
    assert db.scalar(select(func.count()).select_from(GutscheinFehlversuch)) == 1


def test_unterschiedliche_kaufsteuer_snapshots(db, gwelt):
    gs = [code(db, gwelt, ust_satz=satz) for satz in (Decimal("7"), Decimal("19"))]
    db.commit()
    erg = buchen(db, gwelt, [g.code for g in gs], stunden=4)
    assert erg.antwort.status == "bestaetigt"
    assert db.scalars(select(Rechnung).where(Rechnung.gutschein_rolle == "einloesung")).all() == []
    es = db.scalars(select(GutscheinEinloesung)).all()
    assert {e.beleg_ust for e in es} == {Decimal("1.70"), Decimal("4.15")}
    assert all(e.ursprung_position_id for e in es)


def rueckmeldung(ref, betrag):
    import json

    return kanal.ZahlungEingegangen(
        provider="fake", rohdaten=json.dumps({"ref": ref, "ergebnis": "bezahlt", "betrag": betrag})
    )


def test_callback_getrennte_belege_keine_kaufzahlung_als_buchungsgeld(db, gwelt, mail_ausgang):
    g = code(db, gwelt, steuer="bei_einloesung")
    db.commit()
    erg = buchen(db, gwelt, [g.code])
    db.commit()
    z = db.scalar(select(Zahlung).where(Zahlung.buchung_id == erg.antwort.buchung_id))
    erg = online_buchung.zahlung_eingegangen(db, rueckmeldung(z.provider_ref, "15.00"))
    db.commit()
    for lauf in erg.nach_commit:
        lauf(db)
    es = db.scalars(select(GutscheinEinloesung)).all()
    assert es[0].status == "bestaetigt"
    rs = db.scalars(
        select(Rechnung).where(Rechnung.gutschein_rolle.in_(["einloesung", "geldanteil"]))
    ).all()
    assert {(r.gutschein_rolle, r.brutto) for r in rs} == {
        ("einloesung", Decimal("26")),
        ("geldanteil", Decimal("15")),
    }
    assert all(r.pdf_pfad for r in rs)
    assert len([m for m in mail_ausgang if m["betreff"].startswith("Rechnung ")]) == 2
    assert "Gutschein" in mail_ausgang[0]["text"]
    assert "30,00" in mail_ausgang[0]["text"]
    assert gwelt.mitglied.guthaben == Decimal("0")
    assert (
        online_buchung.zahlung_eingegangen(db, rueckmeldung(z.provider_ref, "15.00")).nach_commit
        == []
    )


def test_gutschein_guthaben_zahlen_nur_rest_kaeuferwallet_bleibt(db, gwelt):
    g = code(db, gwelt, steuer="bei_einloesung")
    guthaben.buche(db, kunde=gwelt.mitglied, betrag=Decimal("50"), art="manuell")
    guthaben.buche(db, kunde=gwelt.nichtmitglied, betrag=Decimal("5"), art="manuell")
    db.commit()
    erg = buchen(db, gwelt, [g.code], kunde=gwelt.nichtmitglied)
    assert (
        erg.antwort.gutschein_verrechnet,
        erg.antwort.guthaben_verrechnet,
        erg.antwort.zu_zahlen,
    ) == (Decimal("30"), Decimal("5"), Decimal("10"))
    assert gwelt.mitglied.guthaben == Decimal("50") and gwelt.nichtmitglied.guthaben == Decimal("0")


def test_90_minuten_mit_variierenden_tarifen_und_kumulativen_cent(db, gwelt):
    # Zwei 90-Minuten-Snapshots bei Vierstundenbuchung; heutige Einstellung ist 120.
    db.add(
        Tarif(
            name="teurer zweiter Slot",
            preis=Decimal("20.01"),
            uhrzeit_von=time(20),
            uhrzeit_bis=time(21),
        )
    )
    gs = [code(db, gwelt, minuten=90) for _ in range(2)]
    db.commit()
    erg = buchen(db, gwelt, [g.code for g in gs], stunden=4)
    es = db.scalars(select(GutscheinEinloesung).order_by(GutscheinEinloesung.created_at)).all()
    assert [e.minuten_gedeckt for e in es] == [90, 90]
    assert [e.betrag for e in es] == [Decimal("25.01"), Decimal("25.00")]
    assert erg.antwort.zu_zahlen == Decimal("15.00")
    assert erg.antwort.preis == erg.antwort.gutschein_verrechnet + erg.antwort.zu_zahlen


@pytest.mark.parametrize("stunden,anzahl", [(1, 1), (3, 2)])
def test_mengenregel_keine_teilzweite_einheit(db, gwelt, stunden, anzahl):
    gs = [code(db, gwelt) for _ in range(anzahl)]
    db.commit()
    erg = buchen(db, gwelt, [g.code for g in gs], stunden=stunden)
    if anzahl == 1:
        assert erg.antwort.status == "bestaetigt"
        assert db.scalar(select(GutscheinEinloesung)).minuten_gedeckt == 60
    else:
        assert erg.antwort.grund == "zu_viele_gutscheine"
        assert db.scalars(select(GutscheinEinloesung)).all() == []


def test_dubletten_nach_normalisierung_ein_verbrauch(db, gwelt):
    g = code(db, gwelt)
    db.commit()
    erg = buchen(db, gwelt, [g.code, gutschein_codes.anzeige(g.code).lower()], stunden=2)
    assert erg.antwort.status == "bestaetigt"
    assert db.scalar(select(func.count()).select_from(GutscheinEinloesung)) == 1


def test_wert_kaufbasis_kumulative_centreste(db, gwelt):
    g = code(
        db,
        gwelt,
        modell="wert",
        minuten=None,
        nennwert_brutto=Decimal("0.03"),
        restwert=Decimal("0.03"),
        kaufpreis_brutto=Decimal("0.03"),
    )
    db.scalar(select(Tarif)).preis = Decimal("0.01")
    db.commit()
    for tag in (1, 2, 3):
        erg = buchen(db, gwelt, [g.code], stunden=1, tag=date(2027, 12, tag))
        assert erg.antwort.status == "bestaetigt"
        db.commit()
    es = db.scalars(select(GutscheinEinloesung)).all()
    p = db.get(Rechnung, g.rechnung_id).positionen[0]
    assert sum(e.beleg_brutto for e in es) == p.brutto
    assert sum(e.beleg_netto for e in es) == p.netto
    assert sum(e.beleg_ust for e in es) == p.ust
    assert all(e.ursprung_position_id == p.id for e in es)


def test_nulltarif_bestaetigt_ohne_leere_geldrechnung(db, gwelt):
    db.scalar(select(Tarif)).preis = Decimal("0")
    db.commit()
    erg = buchen(db, gwelt, [], stunden=1)
    assert erg.antwort.status == "bestaetigt"
    assert db.scalars(select(Rechnung)).all() == []


def kanalanfrage(db, welt, codes, *, ident=None):
    konto = welt.mitglied.portal_konto_id
    if konto is None:
        konto = uuid.uuid4()
        welt.mitglied.portal_konto_id = konto
        db.commit()
    return kanal.Anfrage(
        anfrage_id=ident or uuid.uuid4(),
        typ="buchung_anfragen",
        konto_id=konto,
        erstellt_am=kombiniere(D, time(10)),
        nutzlast={
            "feld_id": str(welt.feld.id),
            "beginn": kombiniere(TERMIN, time(19)).isoformat(),
            "ende": kombiniere(TERMIN, time(22)).isoformat(),
            "gutschein_codes": codes,
        },
    )


def test_kanal_limit_nach_savepoint_rollback_und_idempotenz(db, gwelt):
    g = code(db, gwelt)
    db.commit()
    for _ in range(10):
        n = kanalanfrage(db, gwelt, [g.code, "---"])
        a, _ = anfragen.bearbeite(db, n)
        assert a.grund == "gutschein_ungueltig"
        assert anfragen.bearbeite(db, n)[0] == a
    assert db.scalar(select(func.count()).select_from(GutscheinFehlversuch)) == 10
    assert db.scalars(select(Buchung)).all() == []
    assert db.scalars(select(GutscheinEinloesung)).all() == []
    assert anfragen.bearbeite(db, kanalanfrage(db, gwelt, [g.code]))[0].grund == "zu_viele_versuche"
    assert db.scalar(select(func.count()).select_from(GutscheinFehlversuch)) == 10


def test_kanal_vorbereitungsretry_rollt_ganze_transaktion_zurueck(db, gwelt, monkeypatch):
    from beachhub_core.models import GutscheinFehlversuch

    g = code(db, gwelt)
    db.commit()
    n = kanalanfrage(db, gwelt, [g.code])
    echt = gutschein_sperren.bereite_vorgang_vor
    aufrufe = []

    def wechsel(session, **kwargs):
        aufrufe.append(True)
        if len(aufrufe) == 1:
            session.add(GutscheinFehlversuch(kunde_id=gwelt.mitglied.id))
            session.flush()
            raise gutschein_sperren.GutscheinVorbereitungWiederholen("Besitzerwechsel")
        return echt(session, **kwargs)

    monkeypatch.setattr(gutschein_sperren, "bereite_vorgang_vor", wechsel)
    a, _ = anfragen.bearbeite(db, n)
    assert a.status == "reserviert"
    assert db.scalar(select(func.count()).select_from(GutscheinFehlversuch)) == 0
    assert db.scalar(select(func.count()).select_from(Buchung)) == 1
    assert len(aufrufe) == 2
    assert anfragen.bearbeite(db, n)[0] == a


@pytest.mark.parametrize("umgekehrt", [False, True])
def test_zwei_sessions_verbrauch_kein_deadlock_ein_gewinner(db, gwelt, umgekehrt):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from beachhub_core.database import SessionLocal
    from hilfen_parallel import warte_auf_sperre
    from sqlalchemy import text

    # Freicodes teilen keine Käufer-/Inhaberkundensperre vor der ersten Einlösung.
    gs = gutscheine.stelle_freicodes_aus(
        db, anzahl=2 if umgekehrt else 1, gueltig_bis=None, admin_user_id=None
    )
    gwelt.nichtmitglied.portal_konto_id = uuid.uuid4()
    db.commit()
    ids = [g.code for g in gs]
    n = kanal.Anfrage(
        anfrage_id=uuid.uuid4(),
        typ="buchung_anfragen",
        konto_id=gwelt.nichtmitglied.portal_konto_id,
        erstellt_am=kombiniere(D, time(10)),
        nutzlast={
            "feld_id": str(gwelt.feld.id),
            "beginn": kombiniere(date(2027, 12, 2), time(19)).isoformat(),
            "ende": kombiniere(date(2027, 12, 2), time(23 if umgekehrt else 21)).isoformat(),
            "gutschein_codes": list(reversed(ids)),
        },
    )
    bereit = Event()
    pid = []
    # Erste Transaktion hält einen realen, noch uncommitteten Verbrauch.
    eins = buchen(db, gwelt, ids, stunden=4 if umgekehrt else 2)
    assert eins.antwort.status == "bestaetigt"

    def lauf():
        with SessionLocal() as session:
            pid.append(session.scalar(text("select pg_backend_pid()")))
            bereit.set()
            antwort, _ = anfragen.bearbeite(session, n)
            return antwort

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(lauf)
        assert bereit.wait(5)
        try:
            warte_auf_sperre(pid[0])
            db.commit()
        finally:
            db.rollback()
        zwei = future.result(timeout=5)
    assert zwei.status == "abgelehnt" and zwei.grund == "gutschein_ungueltig"
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(GutscheinEinloesung)) == len(ids)
    assert db.scalar(select(func.count()).select_from(Buchung)) == 1
    assert db.scalar(select(func.count()).select_from(GutscheinFehlversuch)) == 1


@pytest.mark.parametrize("maximal", [1, 2])
def test_nutzungsstatus_freicode_nach_verbrauch(db, gwelt, maximal):
    g = gutscheine.stelle_freicodes_aus(
        db, anzahl=1, max_einloesungen=maximal, gueltig_bis=None, admin_user_id=None
    )[0]
    db.commit()
    assert buchen(db, gwelt, [g.code], stunden=2).antwort.status == "bestaetigt"
    assert g.status == ("eingeloest" if maximal == 1 else "aktiv")


def test_abgelehnte_codes_erhalten_vollstaendige_gutscheinlocks_aussen(db, gwelt):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from beachhub_core.database import SessionLocal
    from beachhub_core.models import Gutschein
    from hilfen_parallel import warte_auf_sperre
    from sqlalchemy import text

    g = code(db, gwelt)
    db.commit()
    gid = g.id
    erg = buchen(db, gwelt, [g.code, "---"])
    assert erg.antwort.grund == "gutschein_ungueltig"
    bereit, pid = Event(), []

    def lauf():
        with SessionLocal() as session:
            pid.append(session.scalar(text("select pg_backend_pid()")))
            bereit.set()
            session.scalar(select(Gutschein).where(Gutschein.id == gid).with_for_update())
            session.commit()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(lauf)
        assert bereit.wait(5)
        try:
            warte_auf_sperre(pid[0])
        finally:
            db.rollback()
        future.result(timeout=5)


@pytest.mark.parametrize("phase", ["bestaetigung", "erstes_pdf"])
def test_nachlauf_unabhaengige_fehler_verlieren_keinen_folgebeleg(
    db, gwelt, monkeypatch, mail_ausgang, phase
):
    from beachhub_core.services import benachrichtigung, rechnung_pdf

    g = code(db, gwelt, steuer="bei_einloesung")
    guthaben.buche(db, kunde=gwelt.mitglied, betrag=Decimal("15"), art="manuell")
    db.commit()
    erg = buchen(db, gwelt, [g.code])
    db.commit()
    if phase == "bestaetigung":

        def kaputt(*args):
            raise RuntimeError("SMTP-Bestaetigung")

        monkeypatch.setattr(benachrichtigung, "buchung_bestaetigt", kaputt)
    else:
        echt = rechnung_pdf.erzeuge

        def erzeuge(session, r):
            if r.gutschein_rolle == "geldanteil":
                raise RuntimeError("PDF-Geldrechnung")
            return echt(session, r)

        monkeypatch.setattr(rechnung_pdf, "erzeuge", erzeuge)
    for lauf in erg.nach_commit:
        lauf(db)
    r = db.scalar(select(Rechnung).where(Rechnung.gutschein_rolle == "einloesung"))
    assert r.pdf_pfad is not None
    assert any(m["betreff"] == f"Rechnung {r.nummer}" for m in mail_ausgang)


@pytest.mark.parametrize("steuer,erlaubt", [("beim_kauf", False), ("bei_einloesung", True)])
def test_mitgliedssnapshot_nur_beim_kauf(db, gwelt, steuer, erlaubt):
    g = code(db, gwelt, steuer=steuer)
    konfiguration.setze(db, "mitgliedsgutschein_nur_mitglieder", False)
    db.commit()
    erg = buchen(db, gwelt, [g.code], kunde=gwelt.nichtmitglied, stunden=2)
    if erlaubt:
        assert erg.antwort.status == "bestaetigt"
        e = db.scalar(select(GutscheinEinloesung))
        from beachhub_core.models import RechnungPosition

        r = db.get(Rechnung, db.get(RechnungPosition, e.rechnung_position_id).rechnung_id)
        assert r.positionen[0].ust_satz == Decimal("19")
    else:
        assert erg.antwort.grund == "gutschein_nur_mitglieder"
        assert db.scalars(select(Buchung)).all() == []


def test_anlageaudit_belege_enthaelt_alle_einloesungsbezuege(db, gwelt):
    from beachhub_core.models import Audit

    g = code(db, gwelt, steuer="bei_einloesung")
    guthaben.buche(db, kunde=gwelt.mitglied, betrag=Decimal("15"), art="manuell")
    db.commit()
    assert buchen(db, gwelt, [g.code]).antwort.status == "bestaetigt"
    db.commit()
    e = db.scalar(select(GutscheinEinloesung))
    rs = db.scalars(
        select(Rechnung).where(Rechnung.gutschein_rolle.in_(["einloesung", "geldanteil"]))
    ).all()
    for r in rs:
        a = db.scalar(select(Audit).where(Audit.objekt_typ == "rechnung", Audit.objekt_id == r.id))
        assert a.nachher_json["gutschein_ids"] == [str(g.id)]
        assert a.nachher_json["einloesung_ids"] == [str(e.id)]
    a = db.scalars(
        select(Audit).where(Audit.objekt_typ == "gutschein_einloesung", Audit.objekt_id == e.id)
    ).all()
    bestaetigt = [eintrag for eintrag in a if eintrag.nachher_json["status"] == "bestaetigt"]
    assert len(bestaetigt) == 1
    assert bestaetigt[0].nachher_json["rechnung_position_id"] == str(e.rechnung_position_id)


def test_kanal_retry_begrenzt_mit_fehlerantwort_und_keinen_nebenwirkungen(db, gwelt, monkeypatch):
    from beachhub_core.models import AnfrageVerarbeitet

    g = code(db, gwelt)
    db.commit()
    n = kanalanfrage(db, gwelt, [g.code])
    aufrufe = []

    def wechsel(session, **kwargs):
        aufrufe.append(True)
        session.add(GutscheinFehlversuch(kunde_id=gwelt.mitglied.id))
        session.flush()
        raise gutschein_sperren.GutscheinVorbereitungWiederholen("Besitzerwechsel")

    monkeypatch.setattr(gutschein_sperren, "bereite_vorgang_vor", wechsel)
    a, nachlauf = anfragen.bearbeite(db, n)
    assert a.status == "fehler" and len(nachlauf) == 1
    assert len(aufrufe) == 3
    assert db.scalars(select(GutscheinFehlversuch)).all() == []
    assert db.scalars(select(Buchung)).all() == []
    assert db.get(AnfrageVerarbeitet, n.anfrage_id).antwort_json["status"] == "fehler"


def test_callback_bereitet_kaeufer_vor_zahlung_und_buchung_vor(db, gwelt):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from beachhub_core.database import SessionLocal
    from beachhub_core.models import Kunde
    from hilfen_parallel import warte_auf_sperre
    from sqlalchemy import text

    g = code(db, gwelt, steuer="bei_einloesung")
    db.commit()
    erg = buchen(db, gwelt, [g.code], kunde=gwelt.nichtmitglied)
    db.commit()
    b = db.get(Buchung, erg.antwort.buchung_id)
    z = db.scalar(select(Zahlung).where(Zahlung.buchung_id == b.id))
    zid, bid, ref, kaeufer = z.id, b.id, z.provider_ref, gwelt.mitglied.id
    db.scalar(select(Kunde).where(Kunde.id == kaeufer).with_for_update())
    bereit, pid = Event(), []

    def lauf():
        with SessionLocal() as session:
            pid.append(session.scalar(text("select pg_backend_pid()")))
            bereit.set()
            erg = online_buchung.zahlung_eingegangen(session, rueckmeldung(ref, "15.00"))
            session.commit()
            return erg.antwort.status

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(lauf)
        assert bereit.wait(5)
        try:
            warte_auf_sperre(pid[0])
            assert (
                db.scalar(
                    select(Zahlung).where(Zahlung.id == zid).with_for_update(nowait=True)
                ).status
                == "offen"
            )
            assert (
                db.scalar(
                    select(Buchung).where(Buchung.id == bid).with_for_update(nowait=True)
                ).status
                == "reserviert"
            )
            db.commit()
        finally:
            db.rollback()
        assert future.result(timeout=5) == "ok"
    db.expire_all()
    assert db.get(Buchung, bid).status == "bestaetigt"


@pytest.mark.parametrize("minuten", [(60, 120), (120, 60)])
def test_unterschiedliche_minutensnapshots_erlauben_zwei_volle_einheiten(db, gwelt, minuten):
    gs = [code(db, gwelt, minuten=m) for m in minuten]
    db.commit()
    erg = buchen(db, gwelt, [g.code for g in gs], stunden=3)
    assert erg.antwort.status == "bestaetigt"
    assert erg.antwort.gutschein_verrechnet == Decimal("45")
    es = db.scalars(select(GutscheinEinloesung).order_by(GutscheinEinloesung.created_at)).all()
    assert [e.minuten_gedeckt for e in es] == list(minuten)
    assert [e.betrag for e in es] == [Decimal(m) / 4 for m in minuten]


@pytest.mark.parametrize("neuer_preis", [Decimal("20"), Decimal("30")])
def test_tarifpflege_zwischen_anlage_und_einloesung_behaelt_einen_preisstand(
    db, gwelt, monkeypatch, neuer_preis
):
    from beachhub_core.database import SessionLocal
    from beachhub_core.services import buchungen

    g = code(db, gwelt)
    db.commit()

    def tarif_aendern(session, buchung):
        # Echte separate Transaktion: globaler Tarif hat keinen Feld-FK und wartet
        # weder auf die Feldsperre noch auf die Kundenmenge der Buchungsanlage.
        with SessionLocal() as admin_session:
            admin_session.add(
                Tarif(
                    name="Neuer Abendtarif",
                    preis=neuer_preis,
                    uhrzeit_von=time(19),
                    uhrzeit_bis=time(21),
                )
            )
            admin_session.commit()

    monkeypatch.setattr(buchungen, "NACH_ANLAGE", [tarif_aendern])
    erg = buchen(db, gwelt, [g.code])
    assert (erg.antwort.preis, erg.antwort.gutschein_verrechnet, erg.antwort.zu_zahlen) == (
        Decimal("45"),
        Decimal("30"),
        Decimal("15"),
    )
    assert db.scalar(select(GutscheinEinloesung)).betrag == Decimal("30")
    z = db.scalar(select(Zahlung).where(Zahlung.buchung_id == erg.antwort.buchung_id))
    assert z.betrag == Decimal("15")


@pytest.mark.parametrize("abweichung", ["feld", "beginn", "ende", "gruppe", "summe"])
def test_einloesung_weist_fremden_oder_veraenderten_preisstand_zurueck(db, gwelt, abweichung):
    from dataclasses import replace
    from datetime import timedelta

    from beachhub_core.services import buchungen, gutschein_einloesung

    g = code(db, gwelt)
    db.commit()
    kontext = gutschein_einloesung.bereite_codes_vor(db, gwelt.mitglied, [g.code])
    anlage = buchungen.lege_an_mit_preisstand(
        db,
        feld_id=gwelt.feld.id,
        kunde_id=gwelt.mitglied.id,
        beginn=kombiniere(TERMIN, time(19)),
        ende=kombiniere(TERMIN, time(22)),
        status=Buchung.RESERVIERT,
    )
    stand = anlage.preisstand
    if abweichung == "feld":
        stand = replace(stand, feld_id=uuid.uuid4())
    elif abweichung == "beginn":
        stand = replace(stand, beginn=stand.beginn + timedelta(minutes=1))
    elif abweichung == "ende":
        stand = replace(stand, ende=stand.ende + timedelta(minutes=1))
    elif abweichung == "gruppe":
        stand = replace(stand, kundengruppe_id=gwelt.nichtmitglied_gruppe.id)
    else:
        stand = replace(
            stand,
            slotpreise=tuple((slot, preis + Decimal("1")) for slot, preis in stand.slotpreise),
        )
    with pytest.raises(ValueError, match="Slotpreisstand passt nicht"):
        gutschein_einloesung.loese_ein(
            db, anlage.buchung, gwelt.mitglied, [g.code], sperrkontext=kontext, preisstand=stand
        )
    assert db.scalars(select(GutscheinEinloesung)).all() == []
    assert g.status == "aktiv"
