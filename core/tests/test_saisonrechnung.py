from datetime import date, time
from decimal import Decimal

import pytest
from beachhub_core import auth, clock, jobs, mail
from beachhub_core.models import (
    Betriebszeit,
    Buchung,
    Dauerbuchung,
    Feld,
    FeldRaster,
    GuthabenBuchung,
    Kunde,
    Rechnung,
    Storno,
    Tarif,
    Zahlung,
)
from beachhub_core.services import (
    buchungen,
    dauerbuchungen,
    guthaben,
    konfiguration,
    kunden,
    pin,
    rechnungen,
    storno,
)
from beachhub_shared.zeit import kombiniere
from fastapi.testclient import TestClient
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
    k.mitglied_bis = date(2028, 4, 30)  # Saisonabo nur für Mitglieder (ab Task 3)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    return f, k


def _abo(db: Session, f: Feld, k: Kunde, bis: date = date(2027, 12, 22)) -> Dauerbuchung:
    """Mittwochs 19–20 Uhr ab dem 01.12.2027 (bis zum 22.12. vier Termine)."""
    d = dauerbuchungen.lege_an(
        db,
        kunde_id=k.id,
        feld_id=f.id,
        wochentag=2,
        start=time(19),
        ende=time(20),
        gueltig_von=date(2027, 12, 1),
        gueltig_bis=bis,
        admin_user_id=None,
        auslassen=set(),
        entscheidungen={},
    )
    db.commit()
    return d


def _saison(db: Session, d: Dauerbuchung) -> Rechnung:
    return db.scalars(select(Rechnung).where(Rechnung.dauerbuchung_id == d.id)).one()


def test_saisonrechnung_bei_anlage(db: Session, welt) -> None:
    f, k = welt
    d = _abo(db, f, k)
    r = _saison(db, d)
    assert r.art == "saison" and r.status == "offen" and r.brutto == Decimal("120.00")
    assert (r.leistung_von, r.leistung_bis) == (date(2027, 12, 1), date(2027, 12, 22))
    assert r.faellig_am == date(2027, 12, 9)  # 14 Tage nach dem 25.11.
    assert len(r.positionen) == 4 and {p.ust_satz for p in r.positionen} == {Decimal("7.00")}
    assert all(b.rechnung_position_id is not None for b in d.buchungen)
    with pytest.raises(rechnungen.RechnungsFehler, match="bereits_berechnet"):
        rechnungen.erzeuge_saisonrechnung(db, d)


def test_saison_zahlungsziel_einstellbar(db: Session, welt) -> None:
    f, k = welt
    konfiguration.setze(db, "saison_zahlungsziel_tage", 30)
    db.commit()
    assert _saison(db, _abo(db, f, k)).faellig_am == date(2027, 12, 25)


@pytest.mark.parametrize(
    "vorhanden,verrechnen,verrechnet,offen,rest,status",
    [
        (Decimal("50.00"), "ja", Decimal("50.00"), Decimal("70.00"), Decimal("0.00"), "offen"),
        (Decimal("200.00"), "ja", Decimal("120.00"), Decimal("0.00"), Decimal("80.00"), "bezahlt"),
        (Decimal("50.00"), "nein", Decimal("0.00"), Decimal("120.00"), Decimal("50.00"), "offen"),
    ],
)
def test_saisonrechnung_verrechnet_guthaben(
    db: Session,
    welt,
    vorhanden: Decimal,
    verrechnen: str,
    verrechnet: Decimal,
    offen: Decimal,
    rest: Decimal,
    status: str,
) -> None:
    f, k = welt
    guthaben.buche(db, kunde=k, betrag=vorhanden, art="manuell")
    konfiguration.setze(db, "guthaben_auf_saisonrechnung", verrechnen)
    db.commit()
    r = _saison(db, _abo(db, f, k))
    db.refresh(k)
    # Volle Rechnung, Guthaben als Zahlung – keine Preisminderung (A-ZAHL-4).
    assert r.brutto == Decimal("120.00") and r.status == status
    assert rechnungen.verrechnet(db, r) == verrechnet
    assert rechnungen.offener_betrag(db, r) == offen
    assert k.guthaben == rest
    if verrechnet:
        z = db.scalars(select(Zahlung).where(Zahlung.rechnung_id == r.id)).one()
        assert z.provider == "guthaben" and z.betrag == verrechnet
        g = db.scalars(select(GuthabenBuchung).where(GuthabenBuchung.art == "verrechnung")).one()
        assert g.bezug_id == r.id and g.betrag == -verrechnet


def test_beenden_erzeugt_einen_korrekturbeleg(db: Session, welt) -> None:
    f, k = welt
    d = _abo(db, f, k)
    belege = dauerbuchungen.beende(db, d, ab=date(2027, 12, 15), admin_user_id=None)
    db.commit()
    assert len(belege) == 1 and belege[0].brutto == Decimal("-60.00")
    assert len(belege[0].positionen) == 2
    r = _saison(db, d)
    # Offene Saisonrechnung: Die Forderung sinkt, Guthaben entsteht keins (A-RECH-7).
    assert rechnungen.offener_betrag(db, r) == Decimal("60.00")
    db.refresh(k)
    assert k.guthaben == Decimal("0.00")
    storniert = [b for b in d.buchungen if b.status == "storniert"]
    assert {b.storno.korrektur_rechnung_id for b in storniert} == {belege[0].id}


def test_beenden_mit_bezahlter_saisonrechnung_schreibt_gut(db: Session, welt) -> None:
    f, k = welt
    d = _abo(db, f, k)
    rechnungen.setze_bezahlt(db, _saison(db, d), admin_user_id=None)
    db.commit()
    dauerbuchungen.beende(db, d, ab=date(2027, 12, 15), admin_user_id=None)
    db.commit()
    db.refresh(k)
    assert k.guthaben == Decimal("60.00")


def test_kein_monatslauf_mehr() -> None:
    assert not hasattr(rechnungen, "monatslauf")
    assert not hasattr(rechnungen, "erzeuge_sammelrechnung")
    assert not hasattr(jobs, "monatslauf_ausfuehren")
    assert "rechnung_tag_im_folgemonat" not in konfiguration.DEFAULTS


def _altabo(db: Session, f: Feld, k: Kunde) -> Dauerbuchung:
    """Bestand vor 0014: Termine vorhanden, keine automatisch erzeugte Rechnung."""
    d = Dauerbuchung(
        kunde_id=k.id,
        feld_id=f.id,
        wochentag=2,
        start=time(19),
        ende=time(20),
        gueltig_von=date(2027, 12, 1),
        gueltig_bis=date(2027, 12, 22),
        pin_hash=pin.hash("654321"),
        pin_verschluesselt=pin.verschluessele("654321"),
    )
    db.add(d)
    db.flush()
    for tag in (1, 8, 15, 22):
        buchungen.lege_an(
            db,
            feld_id=f.id,
            kunde_id=k.id,
            beginn=kombiniere(date(2027, 12, tag), time(19)),
            ende=kombiniere(date(2027, 12, tag), time(20)),
            quelle="dauer",
            dauerbuchung_id=d.id,
            pin_klar="654321",
            zahlungsart="saison",
        )
    db.commit()
    return d


def test_manuelle_bestandsabrechnung_berechnet_nur_unberechnete_aktive_termine(
    eingeloggt: TestClient, db: Session, welt
) -> None:
    d = _altabo(db, *welt)
    alt = rechnungen.erzeuge_einzelrechnung(db, d.buchungen[0], status="offen")
    storno.storniere(db, d.buchungen[-1], durch="betreiber", kostenfrei=True)
    db.commit()
    c = eingeloggt
    seite = c.get(f"/admin/belegung/dauer/{d.id}")
    assert "Saisonrechnung erstellen" in seite.text
    assert db.query(Rechnung).count() == 1  # GET stellt nie eine Rechnung aus.
    antwort = c.post(f"/admin/belegung/dauer/{d.id}/rechnung", data={"csrf_token": c.csrf})
    assert antwort.status_code == 200
    db.expire_all()
    r = _saison(db, d)
    assert r.brutto == Decimal("60.00") and len(r.positionen) == 2
    assert (r.leistung_von, r.leistung_bis) == (date(2027, 12, 8), date(2027, 12, 15))
    assert d.buchungen[0].rechnung_position_id == alt.positionen[0].id
    assert r.pdf_sha256 and any(m["betreff"] == f"Rechnung {r.nummer}" for m in mail.TEST_AUSGANG)
    assert "Saisonrechnung erstellen" not in antwort.text
    doppelt = c.post(f"/admin/belegung/dauer/{d.id}/rechnung", data={"csrf_token": c.csrf})
    assert "bereits berechnet" in doppelt.text
    assert db.query(Rechnung).count() == 2


def test_vollstorno_erlaubt_explizite_neuausstellung_und_zeigt_neuesten_beleg(
    eingeloggt: TestClient, db: Session, welt
) -> None:
    d = _abo(db, *welt)
    alt = _saison(db, d)
    rechnungen.storniere(db, alt, admin_user_id=None, grund="Preisfehler")
    db.commit()
    c = eingeloggt
    assert "Saisonrechnung erstellen" in c.get(f"/admin/belegung/dauer/{d.id}").text
    antwort = c.post(f"/admin/belegung/dauer/{d.id}/rechnung", data={"csrf_token": c.csrf})
    assert antwort.status_code == 200
    db.expire_all()
    neu = db.scalars(
        select(Rechnung).where(Rechnung.dauerbuchung_id == d.id, Rechnung.status == "offen")
    ).one()
    assert neu.brutto == Decimal("120.00") and neu.nummer != alt.nummer
    assert f'href="/admin/rechnungen/{neu.id}"' in antwort.text
    assert f'href="/admin/rechnungen/{alt.id}"' not in antwort.text
    assert all(b.rechnung_position_id in {p.id for p in neu.positionen} for b in d.buchungen)
    assert alt.status == "storniert" and db.query(Rechnung).count() == 3


def test_rechnungserstellung_ohne_unberechnete_termine_bleibt_folgenlos(
    eingeloggt: TestClient, db: Session, welt
) -> None:
    d = _altabo(db, *welt)
    for b in d.buchungen:
        rechnungen.erzeuge_einzelrechnung(db, b)
    db.commit()
    c = eingeloggt
    assert "Saisonrechnung erstellen" not in c.get(f"/admin/belegung/dauer/{d.id}").text
    antwort = c.post(f"/admin/belegung/dauer/{d.id}/rechnung", data={"csrf_token": c.csrf})
    assert "Keine Termine im Zeitraum" in antwort.text
    assert db.query(Rechnung).count() == 4


def test_manuelle_saisonrechnung_braucht_csrf_und_schreibrolle(
    eingeloggt: TestClient, db: Session, welt
) -> None:
    from beachhub_core.main import app

    d = _altabo(db, *welt)
    c = eingeloggt
    assert c.post(f"/admin/belegung/dauer/{d.id}/rechnung", data={}).status_code == 403
    leser, _ = auth.lege_admin_an(db, name="leser", passwort="geheim-123456", rolle="lesend")
    db.commit()
    app.dependency_overrides[auth.aktueller_admin] = lambda: leser
    try:
        assert (
            c.post(
                f"/admin/belegung/dauer/{d.id}/rechnung", data={"csrf_token": c.csrf}
            ).status_code
            == 403
        )
    finally:
        app.dependency_overrides.pop(auth.aktueller_admin)
    assert db.query(Rechnung).count() == 0


def test_guthabenverrechnung_wird_nicht_wiederholt(db: Session, welt) -> None:
    f, k = welt
    guthaben.buche(db, kunde=k, betrag=Decimal("50.00"), art="manuell")
    db.commit()
    r = _saison(db, _abo(db, f, k))
    guthaben.buche(db, kunde=k, betrag=Decimal("20.00"), art="manuell")
    db.commit()
    assert rechnungen.verrechne_guthaben(db, r, quelle="admin") == Decimal("0.00")
    db.commit()
    assert k.guthaben == Decimal("20.00") and rechnungen.offener_betrag(db, r) == Decimal("70.00")
    assert db.query(Zahlung).count() == 1


def test_beenden_mit_teilweise_bezahlter_saisonrechnung(db: Session, welt) -> None:
    f, k = welt
    guthaben.buche(db, kunde=k, betrag=Decimal("80.00"), art="manuell")
    db.commit()
    d = _abo(db, f, k)
    belege = dauerbuchungen.beende(db, d, ab=date(2027, 12, 15), admin_user_id=None)
    db.commit()
    assert len(belege) == 1 and belege[0].brutto == Decimal("-60.00")
    assert rechnungen.offener_betrag(db, _saison(db, d)) == Decimal("0.00")
    assert k.guthaben == Decimal("20.00")


def test_bestaetigungsfehler_verhindert_saison_und_korrekturversand_nicht(
    eingeloggt: TestClient, db: Session, welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    f, k = welt
    fremd = kunden.lege_an(db, name="Fremd", email="fremd@x.de")
    b = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=fremd.id,
        beginn=kombiniere(date(2027, 12, 1), time(19)),
        ende=kombiniere(date(2027, 12, 1), time(20)),
    )
    rechnungen.erzeuge_einzelrechnung(db, b)
    db.commit()
    echt = mail.sende

    def scheitere_bestaetigung(an, betreff, text, **kwargs):
        if betreff == "Ihre Dauerbuchung":
            raise OSError("SMTP-Bestätigung fehlgeschlagen")
        echt(an, betreff, text, **kwargs)

    monkeypatch.setattr(mail, "sende", scheitere_bestaetigung)
    c = eingeloggt
    antwort = c.post(
        "/admin/belegung/dauer",
        data={
            "csrf_token": c.csrf,
            "kunde_id": str(k.id),
            "feld_id": str(f.id),
            "wochentag": "2",
            "start": "19:00",
            "ende": "20:00",
            "gueltig_von": "2027-12-01",
            "gueltig_bis": "2027-12-08",
            f"entscheidung_{b.id}": "stornieren",
        },
    )
    assert antwort.status_code == 200
    db.expire_all()
    belege = db.scalars(select(Rechnung).where(Rechnung.art.in_(("saison", "storno")))).all()
    assert len(belege) == 2 and all(r.pdf_sha256 for r in belege)
    assert {
        m["betreff"]
        for m in mail.TEST_AUSGANG
        if m["betreff"].startswith(("Rechnung ", "Stornorechnung "))
    } == {
        f"Stornorechnung {r.nummer}" if r.art == "storno" else f"Rechnung {r.nummer}"
        for r in belege
    }


def test_ui_beenden_versendet_einen_korrekturbeleg(
    eingeloggt: TestClient, db: Session, welt
) -> None:
    d = _abo(db, *welt)
    c = eingeloggt
    antwort = c.post(
        f"/admin/belegung/dauer/{d.id}/beenden", data={"csrf_token": c.csrf, "ab": "2027-12-15"}
    )
    assert antwort.status_code == 200
    db.expire_all()
    r = db.scalars(select(Rechnung).where(Rechnung.art == "storno")).one()
    assert r.brutto == Decimal("-60.00") and r.pdf_sha256
    assert [m["betreff"] for m in mail.TEST_AUSGANG] == [f"Stornorechnung {r.nummer}"]


def test_fehlende_saisonrechnung_rollt_gesamte_anlage_zurueck(
    db: Session, welt, monkeypatch
) -> None:
    def fehlgeschlagen(*args, **kwargs):
        raise rechnungen.RechnungsFehler("rechnung_fehlgeschlagen")

    monkeypatch.setattr(rechnungen, "_neue_rechnung", fehlgeschlagen)
    with pytest.raises(rechnungen.RechnungsFehler, match="rechnung_fehlgeschlagen"):
        _abo(db, *welt)
    db.rollback()
    assert db.query(Dauerbuchung).count() == 0
    assert db.query(Rechnung).count() == 0
    assert db.query(Zahlung).count() == 0


def test_parallele_saisonrechnung_verhindert_doppelabrechnung(db: Session, welt) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import monotonic, sleep

    from beachhub_core.database import SessionLocal, engine
    from sqlalchemy import text

    d = _altabo(db, *welt)
    did = d.id
    bereit, starten = Event(), Event()
    pid = []

    def zweiter():
        with SessionLocal() as session:
            alt = session.get(Dauerbuchung, did)
            list(alt.buchungen)
            pid.append(session.scalar(text("select pg_backend_pid()")))
            bereit.set()
            assert starten.wait(5)
            try:
                rechnungen.erzeuge_saisonrechnung(session, alt)
                session.commit()
                return "ok"
            except rechnungen.RechnungsFehler as exc:
                session.rollback()
                return str(exc)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(zweiter)
        assert bereit.wait(5)
        try:
            rechnungen.erzeuge_saisonrechnung(db, d)
            starten.set()
            deadline = monotonic() + 5
            with engine.connect() as conn:
                while not conn.scalar(
                    text(
                        "select exists(select 1 from pg_stat_activity "
                        "where pid=:pid and wait_event_type='Lock')"
                    ),
                    {"pid": pid[0]},
                ):
                    assert not future.done(), "Zweite Abrechnung lief ohne Sperre"
                    assert monotonic() < deadline, "Zweite Abrechnung wartet nicht"
                    sleep(0.01)
            db.commit()
        finally:
            db.rollback()
            starten.set()
        assert future.result(timeout=5) == "bereits_berechnet"
    db.expire_all()
    assert db.query(Rechnung).count() == 1 and _saison(db, d).brutto == Decimal("120.00")


@pytest.mark.parametrize("weg", ["storno", "gutschrift", "kulanz"])
@pytest.mark.parametrize("neuausstellung", [False, True])
@pytest.mark.parametrize("bezahlt", [False, True])
def test_alter_buchungsstand_korrigiert_neu_ausgestellte_saisonrechnung(
    db: Session, welt, weg: str, neuausstellung: bool, bezahlt: bool
) -> None:
    from beachhub_core.database import SessionLocal

    d = _altabo(db, *welt)
    if neuausstellung:
        rechnungen.erzeuge_saisonrechnung(db, d)
        db.commit()
    b = d.buchungen[0]  # Session A lädt den Rechnungszeiger vor dem anderen Commit.
    alter_zeiger = b.rechnung_position_id
    did, bid, kid = d.id, b.id, d.kunde_id
    with SessionLocal() as andere:
        aktuell = andere.get(Dauerbuchung, did)
        if neuausstellung:
            alt = andere.scalars(select(Rechnung).where(Rechnung.dauerbuchung_id == did)).one()
            rechnungen.storniere(andere, alt, admin_user_id=None, grund="Neuausstellung")
        r = rechnungen.erzeuge_saisonrechnung(andere, aktuell)
        if bezahlt:
            rechnungen.setze_bezahlt(andere, r, admin_user_id=None)
        rid = r.id
        neue_position = andere.get(Buchung, bid).rechnung_position_id
        if weg == "kulanz":
            s = storno.storniere(
                andere, andere.get(Buchung, bid), durch="betreiber", kostenfrei=False
            )
            sid = s.id
        andere.commit()
    assert neue_position != alter_zeiger and b.rechnung_position_id == alter_zeiger
    if weg == "storno":
        storno.storniere(db, b, durch="betreiber", kostenfrei=True)
    elif weg == "gutschrift":
        assert storno.gutschreiben(db, b, grund="Kulanz", quelle="admin") is not None
    else:
        s = db.get(Storno, sid)
        assert s.buchung is b  # Bereits gecachte Buchung bleibt ohne Refresh veraltet.
        storno.kulanz(db, s, admin_user_id=None, grund="Kulanz")
    db.commit()
    db.expire_all()
    korrektur = db.scalars(select(Rechnung).where(Rechnung.korrigiert_rechnung_id == rid)).one()
    assert korrektur.brutto == Decimal("-30.00") and len(korrektur.positionen) == 1
    assert db.get(Buchung, bid).rechnung_position_id == neue_position
    assert rechnungen.offener_betrag(db, db.get(Rechnung, rid)) == Decimal(
        "0.00" if bezahlt else "90.00"
    )
    assert db.get(Kunde, kid).guthaben == Decimal("30.00" if bezahlt else "0.00")
    if weg in ("storno", "kulanz"):
        assert db.get(Buchung, bid).storno.korrektur_rechnung_id == korrektur.id
    if weg == "storno":
        with pytest.raises(storno.StornoFehler, match="nicht_aktiv"):
            storno.storniere(db, b, durch="betreiber", kostenfrei=True)
        db.rollback()
    elif weg == "gutschrift":
        assert storno.gutschreiben(db, b, grund="Wiederholung", quelle="admin") is None
        db.commit()
    else:
        storno.kulanz(db, db.get(Storno, sid), admin_user_id=None, grund="Wiederholung")
        db.commit()
    assert db.query(Rechnung).filter_by(korrigiert_rechnung_id=rid).count() == 1
    assert db.query(GuthabenBuchung).filter_by(art="storno_gutschrift").count() == int(bezahlt)


def test_alter_aktiver_buchungsstand_wird_nicht_erneut_storniert(db: Session, welt) -> None:
    from beachhub_core.database import SessionLocal

    d = _altabo(db, *welt)
    b = d.buchungen[0]
    bid = b.id
    with SessionLocal() as andere:
        storno.storniere(andere, andere.get(Buchung, bid), durch="betreiber", kostenfrei=True)
        andere.commit()
    assert b.aktiv
    with pytest.raises(storno.StornoFehler, match="nicht_aktiv"):
        storno.storniere(db, b, durch="betreiber", kostenfrei=True)
    db.rollback()
    assert db.query(Storno).count() == 1
