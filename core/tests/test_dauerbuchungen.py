from datetime import date, time
from decimal import Decimal

import pytest
from beachhub_core import clock
from beachhub_core.models import (
    Audit,
    Betriebszeit,
    Buchung,
    Dauerbuchung,
    Feld,
    FeldRaster,
    Rechnung,
    Sperre,
    Tarif,
)
from beachhub_core.services import buchungen, dauerbuchungen, konfiguration, kunden, pin
from beachhub_shared.zeit import kombiniere
from sqlalchemy import select
from sqlalchemy.orm import Session


@pytest.fixture
def welt(db: Session):
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    db.add_all([f, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    k = kunden.lege_an(db, name="TSV", email="tsv@x.de", rechnungskunde=True)
    k.mitglied_bis = date(2028, 4, 30)
    k2 = kunden.lege_an(db, name="B", email="b@x.de")
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    return f, k, k2


def test_planen_zeigt_termine_und_kollisionen(db: Session, welt) -> None:
    f, k, k2 = welt
    d = date(2027, 12, 7)  # Dienstag
    buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k2.id,
        beginn=kombiniere(d, time(19)),
        ende=kombiniere(d, time(20)),
    )
    db.add(
        Sperre(
            feld_id=f.id,
            beginn=kombiniere(date(2027, 12, 21), time(9)),
            ende=kombiniere(date(2027, 12, 22), time(9)),
            grund="X",
        )
    )
    db.commit()
    plan = dauerbuchungen.plane(
        db,
        kunde_id=k.id,
        feld_id=f.id,
        wochentag=1,
        start=time(19),
        ende=time(21),
        gueltig_von=date(2027, 12, 1),
        gueltig_bis=date(2027, 12, 31),
    )
    assert [t.datum for t in plan] == [
        date(2027, 12, 7),
        date(2027, 12, 14),
        date(2027, 12, 21),
        date(2027, 12, 28),
    ]
    assert len(plan[0].kollisionen) == 1 and isinstance(plan[2].kollisionen[0], Sperre)
    assert plan[1].preis == Decimal("60.00")


def test_anlegen_mit_auslassen_und_gemeinsamer_pin(db: Session, welt) -> None:
    f, k, k2 = welt
    d = date(2027, 12, 7)
    fremd = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k2.id,
        beginn=kombiniere(d, time(19)),
        ende=kombiniere(d, time(20)),
    )
    db.commit()
    with pytest.raises(dauerbuchungen.DauerbuchungsFehler, match="entscheidung_fehlt"):
        dauerbuchungen.lege_an(
            db,
            kunde_id=k.id,
            feld_id=f.id,
            wochentag=1,
            start=time(19),
            ende=time(21),
            gueltig_von=date(2027, 12, 1),
            gueltig_bis=date(2027, 12, 31),
            admin_user_id=None,
            auslassen=set(),
            entscheidungen={},
        )
    db.rollback()
    dauer = dauerbuchungen.lege_an(
        db,
        kunde_id=k.id,
        feld_id=f.id,
        wochentag=1,
        start=time(19),
        ende=time(21),
        gueltig_von=date(2027, 12, 1),
        gueltig_bis=date(2027, 12, 31),
        admin_user_id=None,
        auslassen={date(2027, 12, 28)},
        entscheidungen={fremd.id: "stornieren"},
    )
    db.commit()
    assert len(dauer.buchungen) == 3
    assert {b.pin_hash for b in dauer.buchungen} == {dauer.pin_hash}
    assert db.get(Buchung, fremd.id).status == "storniert"
    assert all(b.zahlungsart == "saison" and b.quelle == "dauer" for b in dauer.buchungen)
    assert pin.entschluessele(dauer.pin_verschluesselt) == pin.entschluessele(
        dauer.buchungen[0].pin_verschluesselt
    )

    r = db.scalars(select(Rechnung).where(Rechnung.dauerbuchung_id == dauer.id)).one()
    assert r.art == "saison" and len(r.positionen) == 3
    assert all(b.rechnung_position_id is not None for b in dauer.buchungen)


def test_anlegen_ohne_tarif_wirft_und_schreibt_nichts(db: Session, welt) -> None:
    f, k, _ = welt
    db.query(Tarif).delete()
    db.commit()
    with pytest.raises(dauerbuchungen.DauerbuchungsFehler, match="kein_tarif"):
        dauerbuchungen.lege_an(
            db,
            kunde_id=k.id,
            feld_id=f.id,
            wochentag=1,
            start=time(19),
            ende=time(21),
            gueltig_von=date(2027, 12, 1),
            gueltig_bis=date(2027, 12, 31),
            admin_user_id=None,
            auslassen=set(),
            entscheidungen={},
        )
    db.rollback()
    assert db.query(Dauerbuchung).count() == 0
    assert db.query(Buchung).count() == 0


def test_beenden_storniert_kuenftige(db: Session, welt) -> None:
    f, k, _ = welt
    dauer = dauerbuchungen.lege_an(
        db,
        kunde_id=k.id,
        feld_id=f.id,
        wochentag=1,
        start=time(19),
        ende=time(21),
        gueltig_von=date(2027, 12, 1),
        gueltig_bis=date(2027, 12, 31),
        admin_user_id=None,
        auslassen=set(),
        entscheidungen={},
    )
    db.commit()
    belege = dauerbuchungen.beende(db, dauer, ab=date(2027, 12, 20), admin_user_id=None)
    db.commit()
    assert len(belege) == 1 and belege[0].brutto == Decimal("-120.00")
    status = [b.status for b in dauer.buchungen]
    assert status == ["bestaetigt", "bestaetigt", "storniert", "storniert"]
    assert dauer.beendet_ab == date(2027, 12, 20) and dauer.beendet_am is not None


def _abo(db: Session, f, kunde, **extra):
    return dauerbuchungen.lege_an(
        db,
        kunde_id=kunde.id,
        feld_id=f.id,
        wochentag=1,
        start=time(19),
        ende=time(21),
        gueltig_von=date(2027, 12, 1),
        gueltig_bis=date(2027, 12, 31),
        admin_user_id=None,
        auslassen=set(),
        entscheidungen={},
        **extra,
    )


def test_abo_nur_fuer_rechnungskunden(db: Session, welt) -> None:
    f, _, k2 = welt  # k2 ist kein Rechnungskunde
    k2.mitglied_bis = date(2028, 4, 30)
    db.commit()
    with pytest.raises(dauerbuchungen.DauerbuchungsFehler, match="kein_rechnungskunde"):
        _abo(db, f, k2)
    db.rollback()
    assert db.query(Dauerbuchung).count() == 0 and db.query(Rechnung).count() == 0
    d = _abo(db, f, k2, rechnungskunde_setzen=True)
    db.commit()
    db.refresh(k2)
    assert k2.rechnungskunde is True and len(d.buchungen) == 4
    eintraege = db.query(Audit).filter(Audit.objekt_id == k2.id).all()
    assert any(
        a.vorher_json
        and a.vorher_json["rechnungskunde"] is False
        and a.nachher_json["rechnungskunde"] is True
        for a in eintraege
    )


def test_abo_nur_fuer_mitglieder_bis_zum_letzten_termin(db: Session, welt) -> None:
    f, k, _ = welt
    k.mitglied_bis = date(2027, 12, 20)  # endet vor dem 21. und 28.12.
    db.commit()
    with pytest.raises(dauerbuchungen.DauerbuchungsFehler, match="mitgliedschaft_zu_kurz"):
        _abo(db, f, k)
    db.rollback()
    assert db.query(Dauerbuchung).count() == 0
    konfiguration.setze(db, "abo_nur_mitglieder", "nein")
    db.commit()
    d = _abo(db, f, k)
    db.commit()
    # Ohne die Regel gelten die Konditionen am jeweiligen Termin (A-KUND-6).
    assert [b.ust_satz for b in d.buchungen] == [Decimal("7.00")] * 2 + [Decimal("19.00")] * 2


def test_abo_prueft_letzten_nicht_ausgelassenen_termin(db: Session, welt) -> None:
    f, k, _ = welt
    k.mitglied_bis = date(2027, 12, 21)
    db.commit()
    d = dauerbuchungen.lege_an(
        db,
        kunde_id=k.id,
        feld_id=f.id,
        wochentag=1,
        start=time(19),
        ende=time(21),
        gueltig_von=date(2027, 12, 1),
        gueltig_bis=date(2027, 12, 31),
        admin_user_id=None,
        auslassen={date(2027, 12, 28)},
        entscheidungen={},
    )
    db.commit()
    assert len(d.buchungen) == 3
    assert all(b.ust_satz == Decimal("7.00") for b in d.buchungen)


@pytest.mark.parametrize(
    "feld,wert,fehler",
    [
        ("rechnungskunde", False, "kein_rechnungskunde"),
        ("mitglied_bis", None, "mitgliedschaft_zu_kurz"),
    ],
)
def test_abo_prueft_frischen_kundenstand_unter_sperre(
    db: Session, welt, feld, wert, fehler
) -> None:
    from beachhub_core.database import SessionLocal
    from beachhub_core.models import Kunde
    from sqlalchemy import update

    f, k, _ = welt
    kid = k.id
    # Die Hauptsession hält den alten Stand in ihrer Identity Map.
    assert k.rechnungskunde and k.mitglied_bis == date(2028, 4, 30)
    with SessionLocal() as andere:
        andere.execute(update(Kunde).where(Kunde.id == kid).values(**{feld: wert}))
        andere.commit()
    with pytest.raises(dauerbuchungen.DauerbuchungsFehler, match=fehler):
        _abo(db, f, k)
    db.rollback()
    assert db.query(Dauerbuchung).count() == 0
    assert db.query(Rechnung).count() == 0


def test_abo_markierung_erst_nach_vollstaendiger_kundensperre(db: Session, welt) -> None:
    from sqlalchemy import event

    f, k, k2 = welt
    k2.mitglied_bis = date(2028, 4, 30)
    fremd = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k.id,
        beginn=kombiniere(date(2027, 12, 7), time(19)),
        ende=kombiniere(date(2027, 12, 7), time(20)),
    )
    db.commit()
    kid, fremd_id = k2.id, fremd.id
    kunden_ids = {k.id, kid}
    batch_gesperrt = False
    markierung_gesehen = False

    def pruefe(conn, cursor, statement, parameters, context, executemany):
        nonlocal batch_gesperrt, markierung_gesehen
        if "FOR UPDATE" in statement and "ORDER BY kunde.id" in statement:
            batch_gesperrt |= kunden_ids.issubset(set(parameters.values()))
        if statement.startswith("UPDATE kunde SET rechnungskunde"):
            assert batch_gesperrt, "Kundenmutation vor vollständiger Batchsperre"
            markierung_gesehen = True

    verbindung = db.connection()
    event.listen(verbindung, "before_cursor_execute", pruefe)
    try:
        d = dauerbuchungen.lege_an(
            db,
            kunde_id=kid,
            feld_id=f.id,
            wochentag=1,
            start=time(19),
            ende=time(21),
            gueltig_von=date(2027, 12, 1),
            gueltig_bis=date(2027, 12, 31),
            admin_user_id=None,
            auslassen=set(),
            entscheidungen={fremd_id: "stornieren"},
            rechnungskunde_setzen=True,
        )
        assert batch_gesperrt and markierung_gesehen
        assert k2.rechnungskunde and len(d.buchungen) == 4
    finally:
        event.remove(verbindung, "before_cursor_execute", pruefe)
    db.commit()
