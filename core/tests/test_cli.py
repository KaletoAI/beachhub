import sys
from pathlib import Path

import pytest
from beachhub_core import cli


def test_zertifikate_ungueltiger_name_zeigt_fehler_statt_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["beachhub-core", "zertifikate", "--ziel", str(tmp_path), "--name", "Ungültig!"],
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    fehlerausgabe = capsys.readouterr().err
    assert "Ungültiger Zertifikatsname" in fehlerausgabe
    assert "Traceback" not in fehlerausgabe


def test_zertifikate_ohne_name_nutzt_vorgabe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["beachhub-core", "zertifikate", "--ziel", str(tmp_path)])
    cli.main()
    ausgabe = capsys.readouterr().out
    assert str(tmp_path / "portal-kanal.crt") in ausgabe
    assert str(tmp_path / "halle.crt") in ausgabe


def test_demodaten_fuellt_leere_datenbank_fuer_die_pruefliste(
    db, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from datetime import date, time
    from decimal import Decimal

    from beachhub_core import clock
    from beachhub_core.models import (
        Ausnahmetag,
        Betriebszeit,
        Buchung,
        Dauerbuchung,
        Feld,
        Kunde,
        Rechnung,
    )

    # Abweichender Altbestand wird durch die tägliche Öffnung ersetzt.
    db.add(Betriebszeit(wochentag=0, oeffnet=time(14, 45), schliesst=time(21, 30)))
    db.commit()
    clock.set_override(db, date(2027, 11, 25))  # Donnerstag
    monkeypatch.setattr(sys, "argv", ["beachhub-core", "demodaten"])
    cli.main()
    ausgabe = capsys.readouterr().out
    assert "Betriebszeit täglich 15:00–23:00 (ersetzt 1 abweichende)" in ausgabe
    assert "Sonderschließung 2027 am 24.12., 25.12., 26.12., 31.12." in ausgabe
    # Fr 26.11., Sa 27.11. und So 28.11. träfen in acht Wochen einen Schließtag.
    assert "Abo Carla: Montags 15:00–17:00, 29.11.2027 bis 17.01.2028" in ausgabe
    db.expire_all()
    assert sorted(f.name for f in db.query(Feld)) == ["Feld 1", "Feld 2", "Feld 3"]
    assert all(f.raster for f in db.query(Feld))
    assert sorted(
        (b.wochentag, b.oeffnet.isoformat(), b.schliesst.isoformat())
        for b in db.query(Betriebszeit)
    ) == [(wt, "15:00:00", "23:00:00") for wt in range(7)]
    assert {a.datum.isoformat() for a in db.query(Ausnahmetag) if a.geschlossen} == {
        "2027-12-24",
        "2027-12-25",
        "2027-12-26",
        "2027-12-31",
    }
    k = {x.email.split(".")[0]: x for x in db.query(Kunde)}
    assert set(k) == {"anna", "ben", "carla", "dora"} and k["dora"].guthaben == Decimal("50.00")
    assert len(db.query(Dauerbuchung).one().buchungen) == 8
    status = {(r.kunde_id, r.art, r.status) for r in db.query(Rechnung)}
    assert (k["carla"].id, "saison", "offen") in status
    assert (k["anna"].id, "einzel", "bezahlt") in status
    assert sum(1 for r in db.query(Rechnung) if r.kunde_id == k["ben"].id) == 2
    assert all(r.pdf_pfad for r in db.query(Rechnung))
    assert db.query(Buchung).filter(Buchung.status == Buchung.BESTAETIGT).count() == 11
    # Ein zweiter Lauf verweigert sich, statt Daten zu verdoppeln.
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2 and "bereits Kunden" in capsys.readouterr().err
