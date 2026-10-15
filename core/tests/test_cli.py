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
    from datetime import date
    from decimal import Decimal

    from beachhub_core import clock
    from beachhub_core.models import Betriebszeit, Buchung, Dauerbuchung, Feld, Kunde, Rechnung

    clock.set_override(db, date(2027, 11, 25))  # Donnerstag
    monkeypatch.setattr(sys, "argv", ["beachhub-core", "demodaten"])
    cli.main()
    assert "Abo Carla: Montags 14:45–16:45, 29.11.2027 bis 17.01.2028" in capsys.readouterr().out
    db.expire_all()
    assert sorted(f.name for f in db.query(Feld)) == ["Feld 1", "Feld 2", "Feld 3"]
    assert [(b.wochentag, b.oeffnet.isoformat()) for b in db.query(Betriebszeit)] == [
        (0, "14:45:00")
    ]
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
