from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pyotp
import pytest
from apscheduler.triggers import cron
from beachhub_core import auth, clock, jobs, mail
from beachhub_core.models import AppSetting, Audit, GuthabenBuchung, Kunde, utcnow
from beachhub_core.services import guthaben, konfiguration, kunden
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session


def _kunden(db: Session) -> tuple[Kunde, Kunde]:
    a = kunden.lege_an(db, name="Anna", email="anna@x.de")
    b = kunden.lege_an(db, name="Bea", email="bea@x.de")
    guthaben.buche(db, kunde=a, betrag=Decimal("30.00"), art="manuell")
    db.commit()
    return a, b


def test_guthabenliste_nur_mit_guthaben(db: Session) -> None:
    a, _ = _kunden(db)
    assert guthaben.guthabenliste(db) == [a]


def test_saisonende_einmal_im_jahr(db: Session) -> None:
    _kunden(db)
    clock.set_override(db, date(2028, 4, 29))
    assert guthaben.saisonende_faellig(db) is None
    clock.set_override(db, date(2028, 4, 30))
    assert guthaben.saisonende_faellig(db) == (1, Decimal("30.00"))
    db.commit()
    assert guthaben.saisonende_faellig(db) is None
    konfiguration.setze(db, "saisonende_guthabenliste", "31.03.")
    db.commit()
    clock.set_override(db, date(2029, 4, 1))
    assert guthaben.saisonende_faellig(db) == (1, Decimal("30.00"))


def test_saisonende_job_mailt_nach_commit(db: Session, mail_ausgang: list) -> None:
    _kunden(db)
    clock.set_override(db, date(2028, 5, 2))
    jobs.saisonende_ausfuehren(db)
    assert [m["betreff"] for m in mail_ausgang] == ["[Beachhub] Guthabenliste zum Saisonende"]
    assert "1 Kunden" in mail_ausgang[0]["text"] and "30,00 €" in mail_ausgang[0]["text"]
    jobs.saisonende_ausfuehren(db)
    assert len(mail_ausgang) == 1


@pytest.mark.parametrize(("monat", "utc_stunde"), [(1, 6), (7, 5)])
def test_saisonende_trigger_um_0705_berlin_bei_utc_server(
    monkeypatch: pytest.MonkeyPatch, monat: int, utc_stunde: int
) -> None:
    monkeypatch.setattr(cron, "get_localzone", lambda: ZoneInfo("UTC"))
    monkeypatch.setattr(jobs.BackgroundScheduler, "start", lambda self: None)
    monkeypatch.setattr(jobs, "_scheduler", None)

    scheduler = jobs.starte_scheduler()
    job = scheduler.get_job("saisonende")
    assert job is not None
    naechster_lauf = job.trigger.get_next_fire_time(None, datetime(2028, monat, 1, tzinfo=UTC))
    assert naechster_lauf is not None
    assert naechster_lauf.astimezone(ZoneInfo("Europe/Berlin")) == datetime(
        2028, monat, 1, 7, 5, tzinfo=ZoneInfo("Europe/Berlin")
    )
    assert naechster_lauf.astimezone(UTC) == datetime(2028, monat, 1, utc_stunde, 5, tzinfo=UTC)


def test_auszahlung_in_der_liste_abhaken(eingeloggt: TestClient, db: Session) -> None:
    c = eingeloggt
    a, _ = _kunden(db)
    seite = c.get("/admin/kunden/guthabenliste")
    assert "Anna" in seite.text and "bea@x.de" not in seite.text and "30,00 €" in seite.text
    r = c.post(
        f"/admin/kunden/guthabenliste/{a.id}/auszahlung",
        data={"csrf_token": c.csrf, "betrag": "30,00", "notiz": "überwiesen"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.expire_all()
    assert db.get(Kunde, a.id).guthaben == Decimal("0.00")
    g = db.scalars(select(GuthabenBuchung).where(GuthabenBuchung.art == "auszahlung")).one()
    assert g.betrag == Decimal("-30.00") and g.notiz == "überwiesen"
    assert "Niemand hat Guthaben" in c.get("/admin/kunden/guthabenliste").text


def test_auszahlung_ueber_guthaben_hinaus_meldet_fehler(
    eingeloggt: TestClient, db: Session
) -> None:
    c = eingeloggt
    a, _ = _kunden(db)
    seite = c.post(
        f"/admin/kunden/guthabenliste/{a.id}/auszahlung",
        data={"csrf_token": c.csrf, "betrag": "50,00", "notiz": ""},
    )
    assert "nicht gedeckt" in seite.text
    db.expire_all()
    assert db.get(Kunde, a.id).guthaben == Decimal("30.00")


def test_liste_sortiert_und_ohne_anonymisierte_kunden(db: Session) -> None:
    a, b = _kunden(db)
    a.name, b.name = "Bea", "Anna"
    db.flush()
    guthaben.buche(db, kunde=b, betrag=Decimal("10.00"), art="manuell")
    z = kunden.lege_an(db, name="Zora", email="zora@x.de")
    guthaben.buche(db, kunde=z, betrag=Decimal("5.00"), art="manuell")
    z.anonymisiert_am = utcnow()
    db.commit()
    assert [k.name for k in guthaben.guthabenliste(db)] == ["Anna", "Bea"]


def test_saisonende_marker_wird_bei_rollback_nicht_verbraucht(db: Session) -> None:
    _kunden(db)
    clock.set_override(db, date(2028, 4, 30))
    assert guthaben.saisonende_faellig(db) == (1, Decimal("30.00"))
    db.rollback()
    assert guthaben.saisonende_faellig(db) == (1, Decimal("30.00"))


def test_saisonende_leere_liste_meldet_null(db: Session, mail_ausgang: list) -> None:
    clock.set_override(db, date(2028, 4, 30))
    jobs.saisonende_ausfuehren(db)
    assert "0 Kunden" in mail_ausgang[0]["text"]
    assert "0,00 €" in mail_ausgang[0]["text"]


def test_mail_sieht_committeten_marker_und_verlinkt_liste(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _kunden(db)
    clock.set_override(db, date(2028, 4, 30))
    ausgang = []

    def sende(empfaenger, betreff, text, **kwargs):
        with Session(db.get_bind()) as andere:
            assert andere.get(AppSetting, "guthabenliste_letzter").value == "2028"
        ausgang.append(text)

    monkeypatch.setattr(mail, "sende", sende)
    jobs.saisonende_ausfuehren(db)
    assert "/admin/kunden/guthabenliste" in ausgang[0]


def test_mailfehler_wiederholt_nicht_im_selben_jahr(
    db: Session, monkeypatch: pytest.MonkeyPatch, mail_ausgang: list
) -> None:
    _kunden(db)
    clock.set_override(db, date(2028, 4, 30))
    with monkeypatch.context() as patch:

        def fehler(*args, **kwargs):
            raise OSError("SMTP nicht erreichbar")

        patch.setattr(mail, "sende", fehler)
        with pytest.raises(OSError, match="SMTP"):
            jobs.saisonende_ausfuehren(db)
    jobs.saisonende_ausfuehren(db)
    assert mail_ausgang == []


@pytest.mark.parametrize("betrag", ["0", "-1", "", "falsch", "NaN", "Infinity"])
def test_auszahlung_ungueltiger_betrag_bleibt_folgenlos(
    eingeloggt: TestClient, db: Session, betrag: str
) -> None:
    a, _ = _kunden(db)
    antwort = eingeloggt.post(
        f"/admin/kunden/guthabenliste/{a.id}/auszahlung",
        data={"csrf_token": eingeloggt.csrf, "betrag": betrag},
    )
    assert antwort.status_code == 200
    db.refresh(a)
    assert a.guthaben == Decimal("30.00")
    assert db.scalar(select(GuthabenBuchung.id).where(GuthabenBuchung.art == "auszahlung")) is None


def test_teilweise_auszahlung_auditiert_und_bleibt_in_liste(
    eingeloggt: TestClient, db: Session
) -> None:
    a, _ = _kunden(db)
    eingeloggt.post(
        f"/admin/kunden/guthabenliste/{a.id}/auszahlung",
        data={"csrf_token": eingeloggt.csrf, "betrag": "10,00", "notiz": "Bar"},
    )
    db.refresh(a)
    assert a.guthaben == Decimal("20.00")
    buchung = db.scalars(select(GuthabenBuchung).where(GuthabenBuchung.art == "auszahlung")).one()
    audit = db.scalars(select(Audit).where(Audit.objekt_id == buchung.id)).one()
    assert audit.admin_user_id == buchung.admin_user_id
    assert audit.nachher_json["guthaben"] == "20.00"
    assert "20,00 €" in eingeloggt.get("/admin/kunden/guthabenliste").text


def test_auszahlung_ohne_csrf_verweigert(eingeloggt: TestClient, db: Session) -> None:
    a, _ = _kunden(db)
    antwort = eingeloggt.post(
        f"/admin/kunden/guthabenliste/{a.id}/auszahlung", data={"betrag": "10"}
    )
    assert antwort.status_code == 403
    db.refresh(a)
    assert a.guthaben == Decimal("30.00")


def test_leser_sieht_liste_aber_kann_nicht_auszahlen(client: TestClient, db: Session) -> None:
    a, _ = _kunden(db)
    _, secret = auth.lege_admin_an(db, name="leser", passwort="test-passwort-1234", rolle="lesend")
    db.commit()
    client.post(
        "/admin/login",
        data={"name": "leser", "passwort": "test-passwort-1234", "code": pyotp.TOTP(secret).now()},
    )
    antwort = client.get("/admin/kunden/guthabenliste")
    assert antwort.status_code == 200 and "Anna" in antwort.text
    import re

    token = re.search(r'name="csrf_token" value="([^\"]+)"', antwort.text).group(1)
    antwort = client.post(
        f"/admin/kunden/guthabenliste/{a.id}/auszahlung", data={"csrf_token": token, "betrag": "10"}
    )
    assert antwort.status_code == 403
    db.refresh(a)
    assert a.guthaben == Decimal("30.00")


def test_saisonende_frischer_marker_trotz_altem_session_cache(db: Session) -> None:
    _kunden(db)
    db.add(AppSetting(key="guthabenliste_letzter", value="2027"))
    db.commit()
    clock.set_override(db, date(2028, 4, 30))
    with Session(db.get_bind()) as andere:
        alter_marker = andere.get(AppSetting, "guthabenliste_letzter")
        assert alter_marker.value == "2027"
        assert guthaben.saisonende_faellig(db) == (1, Decimal("30.00"))
        db.commit()
        assert guthaben.saisonende_faellig(andere) is None


@pytest.mark.parametrize("parallel", [False, True])
def test_auszahlung_prueft_anonymisierung_frisch_unter_sperre(
    db: Session, admin, parallel: bool
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from beachhub_core.database import SessionLocal
    from beachhub_core.routes.kunden import guthaben_auszahlen
    from hilfen_parallel import warte_auf_sperre
    from sqlalchemy import text

    kunde, _ = _kunden(db)
    kid, aid = kunde.id, admin[0].id
    db.commit()
    bereit, starten = Event(), Event()
    pid = []

    def auszahlen():
        with SessionLocal() as session:
            session.execute(text("SET LOCAL statement_timeout = '8s'"))
            alt = session.get(Kunde, kid)
            assert alt.anonymisiert_am is None
            pid.append(session.scalar(text("select pg_backend_pid()")))
            bereit.set()
            assert starten.wait(5)
            return guthaben_auszahlen(
                kid,
                betrag="10,00",
                notiz="überwiesen",
                admin=session.get(type(admin[0]), aid),
                db=session,
            )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(auszahlen)
        try:
            assert bereit.wait(5)
            kunden.anonymisiere(db, kunde)
            if not parallel:
                db.commit()
            starten.set()
            if parallel:
                warte_auf_sperre(pid[0])
                db.commit()
        finally:
            db.rollback()
            starten.set()
        antwort = future.result(timeout=10)
    assert antwort.status_code == 303
    db.refresh(kunde)
    assert kunde.anonymisiert_am is not None and kunde.guthaben == Decimal("30.00")
    assert db.scalar(select(GuthabenBuchung.id).where(GuthabenBuchung.art == "auszahlung")) is None
    assert db.query(Audit).filter(Audit.objekt_typ == "guthaben").count() == 1
