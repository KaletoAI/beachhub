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
