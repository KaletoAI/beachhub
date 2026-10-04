from datetime import date, time
from decimal import Decimal

import pytest
from beachhub_core import clock
from beachhub_core.models import (
    Betriebszeit,
    Buchung,
    Feld,
    FeldRaster,
    GuthabenBuchung,
    Kunde,
    Rechnung,
    Tarif,
    Zahlung,
    utcnow,
)
from beachhub_core.services import buchungen, kunden, rechnungen, storno
from beachhub_shared.zeit import kombiniere
from sqlalchemy import select
from sqlalchemy.orm import Session

D = date(2027, 12, 1)


@pytest.fixture
def welt(db: Session) -> tuple[Feld, Kunde]:
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    db.add_all([f, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    k = kunden.lege_an(db, name="TSV", email="tsv@x.de", rechnungskunde=True)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    return f, k


def _rechnung(
    db: Session, f: Feld, k: Kunde, stunden: tuple[int, ...] = (19, 20), status: str = "offen"
) -> tuple[list[Buchung], Rechnung]:
    """Eine Rechnung mit einer Position je Stunde – gebaut wie die Saisonrechnung."""
    gebucht = [
        buchungen.lege_an(
            db,
            feld_id=f.id,
            kunde_id=k.id,
            beginn=kombiniere(D, time(s)),
            ende=kombiniere(D, time(s + 1)),
        )
        for s in stunden
    ]
    r = rechnungen._neue_rechnung(
        db,
        k,
        "einzel",
        [rechnungen.Posten(b, f"Termin {b.beginn:%H} Uhr", b.preis, b.ust_satz) for b in gebucht],
        D,
        D,
        status,
        quelle="admin",
    )
    db.commit()
    return gebucht, r


def _zahlung(db: Session, k: Kunde, r: Rechnung, betrag: Decimal) -> None:
    db.add(
        Zahlung(
            kunde_id=k.id,
            rechnung_id=r.id,
            provider="guthaben",
            provider_ref=f"guthaben:{r.id}",
            betrag=betrag,
            status=Zahlung.BEZAHLT,
            empfangen_am=utcnow(),
        )
    )


def test_teilstorno_korrigiert_nur_die_gewaehlte_position(db: Session, welt) -> None:
    f, k = welt
    (b1, _), r = _rechnung(db, f, k)
    beleg = rechnungen.korrigiere(db, [r.positionen[0]], grund="Absage", quelle="admin")
    db.commit()
    assert beleg.art == "storno" and beleg.status == "bezahlt"
    assert beleg.korrigiert_rechnung_id == r.id and beleg.korrigiert.nummer == r.nummer
    assert beleg.brutto == Decimal("-30.00") and beleg.positionen[0].ust_satz == Decimal("19.00")
    assert r.positionen[0].korrigiert_durch_id == beleg.positionen[0].id
    assert r.positionen[1].korrigiert_durch_id is None
    assert r.status == "offen" and rechnungen.offener_betrag(db, r) == Decimal("30.00")
    assert b1.rechnung_position_id == r.positionen[0].id  # die Buchung bleibt verknüpft


def test_position_wird_nur_einmal_korrigiert(db: Session, welt) -> None:
    f, k = welt
    _, r = _rechnung(db, f, k)
    rechnungen.korrigiere(db, [r.positionen[0]], grund="x", quelle="admin")
    db.commit()
    with pytest.raises(rechnungen.RechnungsFehler, match="bereits_korrigiert"):
        rechnungen.korrigiere(db, [r.positionen[0]], grund="x", quelle="admin")
    db.rollback()
    rechnungen.korrigiere(db, [r.positionen[1]], grund="x", quelle="admin")
    db.commit()
    db.refresh(r)
    # Alle Positionen korrigiert: Die Rechnung gilt als storniert (Abweichung B-4).
    assert r.status == "storniert" and rechnungen.offener_betrag(db, r) == Decimal("0.00")


def test_korrektur_nur_innerhalb_einer_rechnung(db: Session, welt) -> None:
    f, k = welt
    _, r1 = _rechnung(db, f, k, stunden=(19,))
    _, r2 = _rechnung(db, f, k, stunden=(20,))
    with pytest.raises(rechnungen.RechnungsFehler, match="verschiedene_rechnungen"):
        rechnungen.korrigiere(db, [r1.positionen[0], r2.positionen[0]], grund="x", quelle="admin")


def test_offener_betrag_rechnet_zahlungen_gegen(db: Session, welt) -> None:
    f, k = welt
    _, r = _rechnung(db, f, k)
    _zahlung(db, k, r, Decimal("20.00"))
    db.commit()
    assert rechnungen.verrechnet(db, r) == Decimal("20.00")
    assert rechnungen.offener_betrag(db, r) == Decimal("40.00")
    rechnungen.setze_bezahlt(db, r, admin_user_id=None)
    assert rechnungen.offener_betrag(db, r) == Decimal("0.00")


@pytest.mark.parametrize(
    "bezahlt,gutschrift",
    [
        (Decimal("0"), Decimal("0.00")),
        (Decimal("40.00"), Decimal("10.00")),
        (None, Decimal("30.00")),
    ],
)
def test_gutschrift_nur_fuer_bezahlten_anteil(
    db: Session, welt, bezahlt: Decimal | None, gutschrift: Decimal
) -> None:
    """Rechnung über 60 €, eine Position über 30 € wird kostenfrei: offen ohne Zahlung → nur die
    Forderung sinkt; 40 € schon bezahlt → 20 € decken die verbleibende Forderung, 10 € werden
    Guthaben; ganz bezahlt (None) → der volle Betrag (Abweichung B-3)."""
    f, k = welt
    (b1, _), r = _rechnung(db, f, k)
    if bezahlt is None:
        rechnungen.setze_bezahlt(db, r, admin_user_id=None)
    elif bezahlt:
        _zahlung(db, k, r, bezahlt)
    db.commit()
    beleg = storno.gutschreiben(db, b1, grund="Absage", quelle="admin")
    db.commit()
    assert beleg is not None and beleg.korrigiert_rechnung_id == r.id
    db.refresh(k)
    assert k.guthaben == gutschrift
    gutschriften = db.scalars(
        select(GuthabenBuchung).where(GuthabenBuchung.art == "storno_gutschrift")
    ).all()
    # Jede Gutschrift verweist auf ihren Korrekturbeleg (A-STORNO-6).
    assert [g.bezug_id for g in gutschriften] == ([beleg.id] if gutschrift else [])


def test_gutschreiben_ohne_rechnung_ist_folgenlos(db: Session, welt) -> None:
    f, k = welt
    b = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k.id,
        beginn=kombiniere(D, time(19)),
        ende=kombiniere(D, time(20)),
    )
    db.commit()
    assert storno.gutschreiben(db, b, grund="x", quelle="admin") is None
    assert k.guthaben == Decimal("0.00") and db.query(Rechnung).count() == 0


def test_storno_nach_vollstorno_der_rechnung_ohne_gutschrift(db: Session, welt) -> None:
    f, k = welt
    (b1, _), r = _rechnung(db, f, k)
    rechnungen.setze_bezahlt(db, r, admin_user_id=None)
    rechnungen.storniere(db, r, admin_user_id=None, grund="falsch berechnet")
    db.commit()
    s = storno.storniere(db, b1, durch="betreiber", kostenfrei=True)
    db.commit()
    assert s.korrektur_rechnung_id is None
    db.refresh(k)
    assert k.guthaben == Decimal("0.00") and db.query(Rechnung).count() == 2


def test_gutschreiben_alle_ein_beleg_je_rechnung(db: Session, welt) -> None:
    f, k = welt
    (b1, b2), _ = _rechnung(db, f, k, status="bezahlt")
    for b in (b1, b2):
        storno.storniere(db, b, durch="betreiber", kostenfrei=True, korrigieren=False)
    belege = storno.gutschreiben_alle(db, [b1, b2], grund="Ende", quelle="admin")
    db.commit()
    assert len(belege) == 1 and belege[0].brutto == Decimal("-60.00")
    assert {b.storno.korrektur_rechnung_id for b in (b1, b2)} == {belege[0].id}
    db.refresh(k)
    assert k.guthaben == Decimal("60.00")


@pytest.mark.parametrize("mit_guthaben", [False, True])
def test_historisch_stornierte_rechnung_wird_nicht_erneut_korrigiert(
    db: Session, welt, mit_guthaben: bool
) -> None:
    f, k = welt
    _, r = _rechnung(db, f, k)
    # Altdaten aus 0013: Vollstorno ohne Gegenpositionsmarker.
    r.status = "storniert"
    db.commit()
    dienst = storno.gutschreiben_positionen if mit_guthaben else rechnungen.korrigiere
    with pytest.raises(rechnungen.RechnungsFehler, match="nicht_stornierbar"):
        dienst(db, [r.positionen[0]], grund="x", quelle="admin")
    db.rollback()
    assert db.query(Rechnung).count() == 1
    assert k.guthaben == Decimal("0.00")


def test_doppelte_positionen_werden_nur_einmal_korrigiert(db: Session, welt) -> None:
    f, k = welt
    _, r = _rechnung(db, f, k, status="bezahlt")
    p = r.positionen[0]
    beleg = storno.gutschreiben_positionen(db, [p, p], grund="x", quelle="admin")
    db.commit()
    assert beleg.brutto == Decimal("-30.00") and len(beleg.positionen) == 1
    assert k.guthaben == Decimal("30.00")


@pytest.mark.parametrize("andere_position", [False, True])
def test_parallele_korrekturen_serialisieren_position_und_offenen_betrag(
    db: Session, welt, andere_position: bool
) -> None:
    """Der zweite Aufruf hat einen alten ORM-Lesestand und wartet auf den ersten Commit."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import monotonic, sleep

    from beachhub_core.database import SessionLocal, engine
    from sqlalchemy import text

    f, k = welt
    _, r = _rechnung(db, f, k)
    _zahlung(db, k, r, Decimal("40.00"))
    db.commit()
    rid, kid = r.id, k.id
    geladen, starten = Event(), Event()
    pid: list[int] = []

    def zweiter() -> str:
        with SessionLocal() as session:
            alt = session.get(Rechnung, rid)
            pos = alt.positionen[1 if andere_position else 0]
            pid.append(session.scalar(text("select pg_backend_pid()")))
            geladen.set()
            assert starten.wait(5)
            try:
                storno.gutschreiben_positionen(session, [pos], grund="zweiter", quelle="admin")
                session.commit()
                return "ok"
            except rechnungen.RechnungsFehler as exc:
                session.rollback()
                return str(exc)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(zweiter)
        assert geladen.wait(5)
        storno.gutschreiben_positionen(db, [r.positionen[0]], grund="erster", quelle="admin")
        starten.set()
        # Belege werden tatsächlich gleichzeitig verarbeitet: zweiter Backend wartet auf Lock.
        deadline = monotonic() + 5
        try:
            with engine.connect() as conn:
                while not conn.scalar(
                    text(
                        "select exists(select 1 from pg_stat_activity "
                        "where pid=:pid and wait_event_type='Lock')"
                    ),
                    {"pid": pid[0]},
                ):
                    assert not future.done(), "Zweite Korrektur lief ohne Transaktionssperre"
                    assert monotonic() < deadline, "Zweite Korrektur wartet nicht auf einen Lock"
                    sleep(0.01)
            db.commit()
        finally:
            db.rollback()
        assert future.result(timeout=5) == ("ok" if andere_position else "bereits_korrigiert")
    db.expire_all()
    assert db.get(Kunde, kid).guthaben == Decimal("40.00" if andere_position else "10.00")
    assert db.query(Rechnung).count() == (3 if andere_position else 2)
    assert rechnungen.offener_betrag(db, db.get(Rechnung, rid)) == Decimal("0.00")


@pytest.mark.parametrize("fehler", ["pdf", "smtp", "lesen", "db", "alarm"])
def test_belegfehler_hindert_naechsten_versand_nicht(
    db: Session, welt, monkeypatch, mail_ausgang, fehler: str
) -> None:
    from beachhub_core import mail
    from beachhub_core.services import benachrichtigung, rechnung_pdf

    f, k = welt
    _, r1 = _rechnung(db, f, k, stunden=(19,))
    _, r2 = _rechnung(db, f, k, stunden=(20,))
    ids = [r1.id, r2.id]
    nummer1, nummer2 = r1.nummer, r2.nummer
    if fehler in ("smtp", "lesen", "alarm"):
        rechnung_pdf.erzeuge(db, r1)
        db.commit()
    if fehler == "lesen":
        from pathlib import Path

        Path(r1.pdf_pfad).unlink()
    echt_pdf, echt_sende = rechnung_pdf.erzeuge, mail.sende

    def pdf(session, r):
        if r.nummer == nummer1 and fehler in ("pdf", "db"):
            if fehler == "db":
                session.add(Kunde(name="Duplikat", email=k.email))
                session.flush()  # echte fehlgeschlagene DB-Transaktion
            raise OSError("PDF kaputt")
        return echt_pdf(session, r)

    def sende(an, betreff, text, **kwargs):
        if betreff == f"Rechnung {nummer1}" and fehler in ("smtp", "alarm"):
            raise OSError("SMTP kaputt")
        if betreff.startswith("[Beachhub]") and fehler == "alarm":
            raise OSError("Alarm kaputt")
        return echt_sende(an, betreff, text, **kwargs)

    monkeypatch.setattr(rechnung_pdf, "erzeuge", pdf)
    monkeypatch.setattr(mail, "sende", sende)
    benachrichtigung.belege_versenden(db, [ids[0], None, ids[1], ids[1]])
    assert [m["betreff"] for m in mail_ausgang if not m["betreff"].startswith("[Beachhub]")] == [
        f"Rechnung {nummer2}"
    ]
    assert db.get(Rechnung, ids[1]).pdf_pfad
    if fehler != "alarm":
        assert len([m for m in mail_ausgang if m["betreff"].startswith("[Beachhub]")]) == 1


def test_migration_0014_uebernimmt_historischen_vollstorno(db: Session, welt) -> None:
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from beachhub_core.database import engine
    from beachhub_core.models import Storno
    from sqlalchemy import inspect

    f, k = welt
    (b1, _), r = _rechnung(db, f, k)
    s = Storno(buchung_id=b1.id, durch="betreiber", kostenfrei=False, grund="alt")
    db.add(s)
    beleg = rechnungen.storniere(db, r, admin_user_id=None, grund="alt")
    db.commit()
    rid, sid, beleg_id = r.id, s.id, beleg.id
    db.commit()
    cfg = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
    # Alembic darf die Logger späterer Tests nicht durch fileConfig deaktivieren.
    cfg.config_file_name = None
    # Das Fixture-Schema entspricht head; downgrade stellt die echten 0013-Altdaten her.
    command.stamp(cfg, "head")
    command.downgrade(cfg, "0013")
    command.upgrade(cfg, "0014")
    db.expire_all()
    assert db.get(Rechnung, beleg_id).korrigiert_rechnung_id == rid
    assert not db.get(Storno, sid).freie_absage
    assert all(p.korrigiert_durch_id is None for p in db.get(Rechnung, rid).positionen)
    with pytest.raises(rechnungen.RechnungsFehler, match="nicht_stornierbar"):
        storno.gutschreiben_positionen(
            db, db.get(Rechnung, rid).positionen, grund="erneut", quelle="admin"
        )
    db.rollback()
    inspector = inspect(engine)
    for tabelle, spalte, ziel in (
        ("rechnung", "dauerbuchung_id", "dauerbuchung"),
        ("rechnung", "korrigiert_rechnung_id", "rechnung"),
        ("rechnung_position", "korrigiert_durch_id", "rechnung_position"),
        ("storno", "korrektur_rechnung_id", "rechnung"),
        ("zahlung", "rechnung_id", "rechnung"),
    ):
        assert any(c["name"] == spalte and c["nullable"] for c in inspector.get_columns(tabelle))
        assert any(
            fk["constrained_columns"] == [spalte] and fk["referred_table"] == ziel
            for fk in inspector.get_foreign_keys(tabelle)
        )
    freie_absage = next(c for c in inspector.get_columns("storno") if c["name"] == "freie_absage")
    assert not freie_absage["nullable"] and freie_absage["default"] is None
    assert any(i["name"] == "ix_zahlung_rechnung_id" for i in inspector.get_indexes("zahlung"))


def test_portalstorno_und_vollstorno_sperren_kunden_vor_buchung(db: Session, welt) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import monotonic, sleep

    from beachhub_core.database import SessionLocal, engine
    from beachhub_core.services import online_buchung
    from sqlalchemy import text

    f, k = welt
    (b1, _), r = _rechnung(db, f, k, status="bezahlt")
    b1.zahlungsart = "online"
    b1.quelle = "portal"
    db.commit()
    bid, kid = b1.id, k.id
    db.execute(select(Kunde).where(Kunde.id == kid).with_for_update())
    gestartet = Event()
    pid: list[int] = []

    def portal():
        with SessionLocal() as session:
            kunde = session.get(Kunde, kid)
            pid.append(session.scalar(text("select pg_backend_pid()")))
            gestartet.set()
            ergebnis = online_buchung.storniere_fuer_kunde(session, kunde=kunde, buchung_id=bid)
            session.commit()
            return ergebnis.antwort.status

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(portal)
        assert gestartet.wait(5)
        deadline = monotonic() + 5
        try:
            with engine.connect() as conn:
                while not conn.scalar(
                    text(
                        "select exists(select 1 from pg_stat_activity "
                        "where pid=:pid and wait_event_type='Lock')"
                    ),
                    {"pid": pid[0]},
                ):
                    assert monotonic() < deadline
                    sleep(0.01)
            # Portal muss noch vor der Buchung auf den Kunden warten.
            db.execute(select(Buchung).where(Buchung.id == bid).with_for_update(nowait=True))
            rechnungen.storniere(db, r, admin_user_id=None, grund="Neuberechnung")
            db.commit()
        finally:
            db.rollback()
        assert future.result(timeout=5) == "ok"
    db.expire_all()
    assert db.get(Buchung, bid).status == "storniert"
    assert db.get(Kunde, kid).guthaben == Decimal("0.00")
    assert db.query(Rechnung).count() == 2


@pytest.mark.parametrize("batch", ["sperre", "dauer", "gutschriften"])
def test_mehrkundenbatch_sperrt_alle_kunden_vor_nummernkreis(db: Session, welt, batch: str) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from beachhub_core.database import SessionLocal
    from beachhub_core.models import Nummernkreis
    from beachhub_core.services import dauerbuchungen, sperren
    from hilfen_parallel import warte_auf_sperre
    from sqlalchemy import text

    f, k1 = welt
    k2 = kunden.lege_an(db, name="Zweitkunde", email="zwei@x.de")
    neu = kunden.lege_an(db, name="Abokunde", email="abo@x.de")
    db.commit()
    niedrig, hoch = sorted([k1, k2], key=lambda k: k.id)
    (b1,), r1 = _rechnung(db, f, niedrig, stunden=(19,), status="bezahlt")
    tag2 = D.replace(day=8) if batch == "dauer" else D
    b2 = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=hoch.id,
        beginn=kombiniere(tag2, time(19 if batch == "dauer" else 20)),
        ende=kombiniere(tag2, time(20 if batch == "dauer" else 21)),
    )
    r2 = rechnungen.erzeuge_einzelrechnung(db, b2)
    db.commit()
    bids = [b1.id, b2.id]
    f_id, neu_id, kunden_ids = f.id, neu.id, [niedrig.id, hoch.id]
    db.execute(select(Kunde).where(Kunde.id == hoch.id).with_for_update())
    bereit = Event()
    pid: list[int] = []

    def lauf():
        with SessionLocal() as session:
            gebucht = [session.get(Buchung, bid) for bid in bids]
            pid.append(session.scalar(text("select pg_backend_pid()")))
            bereit.set()
            if batch == "gutschriften":
                storno.gutschreiben_alle(session, gebucht, grund="batch", quelle="admin")
            elif batch == "sperre":
                sperren.lege_an(
                    session,
                    feld_ids=[f_id],
                    beginn=kombiniere(D, time(19)),
                    ende=kombiniere(D, time(21)),
                    grund="batch",
                    admin_user_id=None,
                    entscheidungen={bid: "stornieren" for bid in bids},
                )
            else:
                dauerbuchungen.lege_an(
                    session,
                    kunde_id=neu_id,
                    feld_id=f_id,
                    wochentag=D.weekday(),
                    start=time(19),
                    ende=time(20),
                    gueltig_von=D,
                    gueltig_bis=tag2,
                    admin_user_id=None,
                    auslassen=set(),
                    entscheidungen={bid: "stornieren" for bid in bids},
                )
            session.commit()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(lauf)
        assert bereit.wait(5)
        try:
            warte_auf_sperre(pid[0])
            # Solange der Batch noch Kunde hoch braucht, muss der Nummernkreis frei bleiben.
            db.execute(
                select(Nummernkreis).where(Nummernkreis.jahr == 2027).with_for_update(nowait=True)
            )
            storno.gutschreiben_positionen(db, r2.positionen, grund="einzeln", quelle="admin")
            db.commit()
        finally:
            db.rollback()
        future.result(timeout=5)
    db.expire_all()
    assert [db.get(Kunde, kid).guthaben for kid in kunden_ids] == [
        Decimal("30.00"),
        Decimal("30.00"),
    ]
    assert db.query(Rechnung).count() == (5 if batch == "dauer" else 4)
    if batch == "dauer":
        saison = db.scalars(select(Rechnung).where(Rechnung.art == "saison")).one()
        assert saison.kunde_id == neu_id
        assert saison.brutto == Decimal("60.00") and len(saison.positionen) == 2


def test_umgekehrte_gutschriftbatches_sperren_in_gleicher_reihenfolge(db: Session, welt) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from beachhub_core.database import SessionLocal
    from hilfen_parallel import warte_auf_sperre
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    f, k1 = welt
    k2 = kunden.lege_an(db, name="Zweitkunde", email="zwei@x.de")
    db.commit()
    niedrig, hoch = sorted([k1, k2], key=lambda k: k.id)
    (b1,), _ = _rechnung(db, f, niedrig, stunden=(19,), status="bezahlt")
    (b2,), _ = _rechnung(db, f, hoch, stunden=(20,), status="bezahlt")
    bids, kids = [b1.id, b2.id], [niedrig.id, hoch.id]
    db.execute(select(Kunde).where(Kunde.id == kids[1]).with_for_update())
    bereit = [Event(), Event()]
    pids: list[int | None] = [None, None]

    def lauf(index):
        with SessionLocal() as session:
            reihenfolge = bids[::-1] if index == 0 else bids
            gebucht = [session.get(Buchung, bid) for bid in reihenfolge]
            pids[index] = session.scalar(text("select pg_backend_pid()"))
            bereit[index].set()
            belege = storno.gutschreiben_alle(session, gebucht, grund="batch", quelle="admin")
            session.commit()
            return len(belege)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(lauf, 0)
        assert bereit[0].wait(5)
        try:
            warte_auf_sperre(pids[0])
            # Auch der umgekehrte Batch besitzt niedrig schon vor dem Warten auf hoch.
            with SessionLocal() as probe:
                with pytest.raises(OperationalError):
                    probe.execute(
                        select(Kunde).where(Kunde.id == kids[0]).with_for_update(nowait=True)
                    )
                probe.rollback()
            second = pool.submit(lauf, 1)
            assert bereit[1].wait(5)
            warte_auf_sperre(pids[1])
            db.commit()
        finally:
            db.rollback()
        assert sorted([first.result(timeout=5), second.result(timeout=5)]) == [0, 2]
    db.expire_all()
    assert [db.get(Kunde, kid).guthaben for kid in kids] == [Decimal("30.00"), Decimal("30.00")]
    assert db.query(Rechnung).count() == 4
