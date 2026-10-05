"""Echte PostgreSQL-Locks; der Verbrauchsdienst folgt erst im Einlösungs-Task."""

import time
import uuid
from concurrent.futures import Future
from datetime import date
from datetime import time as uhrzeit
from threading import Thread

import pytest
from beachhub_core.database import SessionLocal, engine
from beachhub_core.models import Gutschein, GutscheinFehlversuch
from beachhub_core.services import gutschein_sperren as sperren
from hilfen_gutschein import kaufgutschein
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session


def test_online_kundenlock_entsteht_vor_savepoint(db: Session, gwelt) -> None:
    from beachhub_core.services import online_buchung
    from beachhub_shared.zeit import kombiniere

    statements = []

    def beobachte(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", beobachte)
    try:
        erg = online_buchung.anfragen(
            db,
            kunde=gwelt.mitglied,
            anfrage_id=uuid.uuid4(),
            feld_id=gwelt.feld.id,
            beginn=kombiniere(date(2020, 1, 1), uhrzeit(10)),
            ende=kombiniere(date(2020, 1, 1), uhrzeit(11)),
            rueckkehr_url="https://example.org/",
        )
    finally:
        event.remove(engine, "before_cursor_execute", beobachte)
    assert erg.antwort.status == "abgelehnt"
    kunde_lock = next(
        i for i, sql in enumerate(statements) if "from kunde" in sql and "for update" in sql
    )
    savepoint = next(i for i, sql in enumerate(statements) if sql.startswith("savepoint"))
    assert kunde_lock < savepoint


@pytest.mark.parametrize("vorgang", ["sperre", "daueranlage", "beenden", "verfall"])
def test_batch_sperrt_gutscheinzahlung_vor_buchung_oder_sperranlage(
    db: Session, gwelt, vorgang
) -> None:
    from datetime import timedelta

    from beachhub_core import clock
    from beachhub_core.models import Buchung, Dauerbuchung
    from beachhub_core.services import dauerbuchungen, online_buchung
    from beachhub_core.services import sperren as hallsperren
    from hilfen_gutschein import einloesung

    g = kaufgutschein(db, gwelt, inhaber_kunde_id=gwelt.nichtmitglied.id)
    e = einloesung(db, gwelt, g)
    b = db.get(Buchung, e.buchung_id)
    if vorgang == "beenden":
        dauer = Dauerbuchung(
            kunde_id=b.kunde_id,
            feld_id=b.feld_id,
            wochentag=2,
            start=uhrzeit(19),
            ende=uhrzeit(21),
            gueltig_von=date(2027, 12, 1),
            gueltig_bis=date(2027, 12, 1),
            pin_hash="test",
            pin_verschluesselt="test",
        )
        db.add(dauer)
        db.flush()
        b.dauerbuchung_id = dauer.id
    if vorgang == "verfall":
        b.status = Buchung.RESERVIERT
        b.reserviert_bis = clock.now(db) - timedelta(minutes=1)
    gwelt.nichtmitglied.rechnungskunde = True
    gwelt.nichtmitglied.mitglied_bis = date(2030, 12, 31)
    db.commit()
    statements = []

    def beobachte(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", beobachte)
    try:
        if vorgang == "sperre":
            hallsperren.lege_an(
                db,
                feld_ids=[b.feld_id],
                beginn=b.beginn,
                ende=b.ende,
                grund="Test",
                admin_user_id=None,
                entscheidungen={b.id: "stornieren"},
            )
        elif vorgang == "daueranlage":
            dauerbuchungen.lege_an(
                db,
                kunde_id=gwelt.nichtmitglied.id,
                feld_id=b.feld_id,
                wochentag=2,
                start=uhrzeit(19),
                ende=uhrzeit(21),
                gueltig_von=date(2027, 12, 1),
                gueltig_bis=date(2027, 12, 1),
                admin_user_id=None,
                auslassen=set(),
                entscheidungen={b.id: "stornieren"},
            )
        elif vorgang == "beenden":
            dauerbuchungen.beende(db, dauer, ab=date(2027, 12, 1), admin_user_id=None)
        else:
            online_buchung.verfalle_abgelaufene(db)
    finally:
        event.remove(engine, "before_cursor_execute", beobachte)
    zlock = next(
        (i for i, s in enumerate(statements) if "from zahlung" in s and "for update" in s), None
    )
    glock = next(
        (i for i, s in enumerate(statements) if "from gutschein \n" in s and "for update" in s),
        None,
    )
    weitere = next(
        i
        for i, s in enumerate(statements)
        if s.startswith("insert into sperre")
        or "for update" in s
        and any(
            f"from {t}" in s
            for t in ("buchung", "dauerbuchung", "rechnung", "nummernkreis", "feld")
        )
    )
    assert zlock is not None and glock is not None
    assert zlock < glock < weitere


def _starte(aktion):
    pid = Future()
    ergebnis = Future()

    def lauf():
        with SessionLocal() as session:
            try:
                session.execute(text("SET LOCAL statement_timeout = '8s'"))
                pid.set_result(session.scalar(text("SELECT pg_backend_pid()")))
                wert = aktion(session)
                session.commit()
                ergebnis.set_result(wert)
            except Exception as exc:
                session.rollback()
                ergebnis.set_exception(exc)

    thread = Thread(target=lauf, daemon=True)
    thread.start()
    return pid.result(timeout=3), ergebnis, thread


def _warte_auf_lock(pid):
    frist = time.monotonic() + 3
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        while time.monotonic() < frist:
            if (
                connection.scalar(
                    text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                    {"pid": pid},
                )
                == "Lock"
            ):
                return
            time.sleep(0.01)
    pytest.fail("Zweite PostgreSQL-Session wartet nicht beobachtbar auf Vorgangslock")


def test_zwei_kunden_selber_code_serialisiert_vorbereitung(db: Session, gwelt) -> None:
    g = kaufgutschein(db, gwelt)
    db.commit()
    gid, mitglied, gast = g.id, gwelt.mitglied.id, gwelt.nichtmitglied.id
    db.commit()
    sperren.bereite_vorgang_vor(db, kunde_ids=[mitglied], gutschein_ids=[gid])
    pid, future, thread = _starte(
        lambda s: sperren.bereite_vorgang_vor(s, kunde_ids=[gast], gutschein_ids=[gid]).kunde_ids
    )
    try:
        _warte_auf_lock(pid)
        assert not future.done()
    finally:
        db.commit()
    assert future.result(timeout=5) == frozenset([mitglied, gast])
    thread.join(timeout=3)


def test_umgekehrte_codeliste_kein_deadlock(db: Session, gwelt) -> None:
    codes = [
        Gutschein(
            code=c * 13, art="frei", modell="einheit", steuer="keine", minuten=60, status="aktiv"
        )
        for c in ("A", "B")
    ]
    db.add_all(codes)
    db.commit()
    gids = [g.id for g in codes]
    kunde = gwelt.mitglied.id
    db.commit()
    sperren.bereite_vorgang_vor(db, kunde_ids=[], gutschein_ids=gids)
    pid, future, thread = _starte(
        lambda s: set(
            sperren.bereite_vorgang_vor(
                s, kunde_ids=[kunde], gutschein_ids=reversed(gids)
            ).gutscheine
        )
    )
    try:
        _warte_auf_lock(pid)
        assert not future.done()
    finally:
        db.commit()
    assert future.result(timeout=5) == set(gids)
    thread.join(timeout=3)


def test_besitzerwechsel_erfordert_neue_vorbereitung(db: Session, gwelt) -> None:
    g = kaufgutschein(db, gwelt)
    db.commit()
    gid, alt, neu = g.id, gwelt.mitglied.id, gwelt.nichtmitglied.id
    db.commit()
    sperren.bereite_vorgang_vor(db, kunde_ids=[alt], gutschein_ids=[gid])

    def aktion(s):
        vorher = s.get(Gutschein, gid)
        assert vorher.inhaber_kunde_id == alt
        return sperren.bereite_vorgang_vor(s, kunde_ids=[alt], gutschein_ids=[gid])

    pid, future, thread = _starte(aktion)
    try:
        _warte_auf_lock(pid)
        g.inhaber_kunde_id = neu
    finally:
        db.commit()
    with pytest.raises(sperren.GutscheinVorbereitungWiederholen):
        future.result(timeout=5)
    thread.join(timeout=3)
    with SessionLocal() as s:
        kontext = sperren.bereite_vorgang_vor(s, kunde_ids=[alt], gutschein_ids=[gid])
        assert kontext.kunde_ids == frozenset([alt, neu])
        assert kontext.gutscheine[gid].inhaber_kunde_id == neu


def test_savepoint_rollback_behaelt_kundenlock_und_serialisiert_rate_limit(
    db: Session, gwelt
) -> None:
    kunde = gwelt.mitglied.id
    db.commit()
    sperren.bereite_vorgang_vor(db, kunde_ids=[kunde], gutschein_ids=[])
    for _ in range(9):
        sperren.merke_fehlversuch(db, kunde)
    with pytest.raises(ValueError), db.begin_nested():
        raise ValueError("abgelehnte Buchung")
    sperren.merke_fehlversuch(db, kunde)

    def aktion(s):
        sperren.pruefe_rate_limit(s, kunde)
        sperren.merke_fehlversuch(s, kunde)

    pid, future, thread = _starte(aktion)
    try:
        _warte_auf_lock(pid)
        assert not future.done()
    finally:
        db.commit()
    with pytest.raises(sperren.GutscheinRateLimitFehler):
        future.result(timeout=5)
    thread.join(timeout=3)
    assert len(db.scalars(select(GutscheinFehlversuch)).all()) == 10


def test_insert_codekollision_verliert_keine_vorgangslocks(db: Session, gwelt, monkeypatch) -> None:
    from beachhub_core.services import gutschein_codes

    g = kaufgutschein(db, gwelt, code="A" * 13)
    db.commit()
    gid, kunde = g.id, gwelt.mitglied.id
    db.commit()
    sperren.bereite_vorgang_vor(db, kunde_ids=[kunde], gutschein_ids=[gid])
    kandidaten = iter(["A" * 13, "B" * 13])
    monkeypatch.setattr(gutschein_codes, "neuer_code", lambda session: next(kandidaten))
    neu = Gutschein(art="frei", modell="einheit", steuer="keine", minuten=60, status="aktiv")
    gutschein_codes.fuege_mit_neuem_code_ein(db, neu)
    pid, future, thread = _starte(
        lambda s: sperren.bereite_vorgang_vor(s, kunde_ids=[kunde], gutschein_ids=[gid])
        .gutscheine[gid]
        .code
    )
    try:
        _warte_auf_lock(pid)
        assert not future.done()
    finally:
        db.commit()
    assert future.result(timeout=5) == "A" * 13
    assert neu.code == "B" * 13
    thread.join(timeout=3)


def test_ratelimit_sperrt_kunde_vor_autoflush(db: Session, gwelt) -> None:
    kunde_id = gwelt.mitglied.id
    db.commit()
    # SessionLocal deaktiviert Autoflush; die Dienstschnittstelle akzeptiert auch
    # gewöhnliche SQLAlchemy-Sessions mit aktiviertem Autoflush.
    db.autoflush = True
    db.add(
        Gutschein(
            code="C" * 13, art="frei", modell="einheit", steuer="keine", minuten=60, status="aktiv"
        )
    )
    statements = []

    def beobachte(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", beobachte)
    try:
        sperren.pruefe_rate_limit(db, kunde_id)
    finally:
        event.remove(engine, "before_cursor_execute", beobachte)
    kundenlock = next(
        i for i, s in enumerate(statements) if "from kunde" in s and "for update" in s
    )
    insert = next(i for i, s in enumerate(statements) if s.startswith("insert into gutschein"))
    assert kundenlock < insert


def test_beenden_neuer_termin_waehrend_vorbereitung_erfordert_retry(db: Session, gwelt) -> None:
    from beachhub_core.models import Buchung, Dauerbuchung
    from beachhub_core.services import buchungen, dauerbuchungen
    from beachhub_shared.zeit import kombiniere
    from hilfen_gutschein import einloesung

    dauer = Dauerbuchung(
        kunde_id=gwelt.mitglied.id,
        feld_id=gwelt.feld.id,
        wochentag=2,
        start=uhrzeit(19),
        ende=uhrzeit(21),
        gueltig_von=date(2027, 12, 1),
        gueltig_bis=date(2027, 12, 8),
        pin_hash="test",
        pin_verschluesselt="test",
    )
    db.add(dauer)
    db.flush()
    alt = buchungen.lege_an(
        db,
        kunde_id=gwelt.mitglied.id,
        feld_id=gwelt.feld.id,
        beginn=kombiniere(date(2027, 12, 1), uhrzeit(19)),
        ende=kombiniere(date(2027, 12, 1), uhrzeit(21)),
        dauerbuchung_id=dauer.id,
    )
    g = Gutschein(
        code="D" * 13,
        art="frei",
        modell="einheit",
        steuer="keine",
        minuten=120,
        inhaber_kunde_id=gwelt.nichtmitglied.id,
        status="aktiv",
    )
    db.add(g)
    db.commit()
    dauer_id, feld_id, kunde_id, gid, alt_id = (
        dauer.id,
        gwelt.feld.id,
        gwelt.mitglied.id,
        g.id,
        alt.id,
    )
    wurde_eingefuegt = False

    def fuege_vor_kundenlock_ein(conn, cursor, statement, parameters, context, executemany):
        nonlocal wurde_eingefuegt
        if wurde_eingefuegt or "FROM kunde" not in statement or "FOR UPDATE" not in statement:
            return
        wurde_eingefuegt = True
        with SessionLocal() as zweite:
            neu = buchungen.lege_an(
                zweite,
                kunde_id=kunde_id,
                feld_id=feld_id,
                beginn=kombiniere(date(2027, 12, 8), uhrzeit(19)),
                ende=kombiniere(date(2027, 12, 8), uhrzeit(21)),
                dauerbuchung_id=dauer_id,
            )
            einloesung(zweite, gwelt, zweite.get(Gutschein, gid), buchung=neu)
            zweite.commit()

    event.listen(engine, "before_cursor_execute", fuege_vor_kundenlock_ein)
    try:
        with pytest.raises(sperren.GutscheinVorbereitungWiederholen):
            dauerbuchungen.beende(db, dauer, ab=date(2027, 12, 1), admin_user_id=None)
    finally:
        event.remove(engine, "before_cursor_execute", fuege_vor_kundenlock_ein)
        db.rollback()
    assert wurde_eingefuegt
    assert db.get(Buchung, alt_id).status == Buchung.BESTAETIGT
