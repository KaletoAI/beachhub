from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from beachhub_hall.clock import EchteUhr, SimulierteUhr
from beachhub_hall.config import KonfigFehler, lade_zuordnung
from beachhub_hall.db import EreignisZeile, dienst_id, lies, oeffne, schreibe
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

from tests.hilfen import F1, F2, MASTER_HASH, TOML_BEISPIEL


def test_zuordnung_wird_geladen(tmp_path: Path) -> None:
    p = tmp_path / "hall.toml"
    p.write_text(TOML_BEISPIEL, encoding="utf-8")
    z = lade_zuordnung(p)
    assert z.master_pin_hash == MASTER_HASH
    assert z.felder[F1].licht == "light.feld_1"
    assert z.feld_fuer_praesenz("binary_sensor.praesenz_feld_2") == F2
    assert z.feld_fuer_praesenz("binary_sensor.gibt_es_nicht") is None
    assert z.gesteuerte() == {"light.feld_1", "light.feld_2", "climate.halle"}
    assert z.tuer.entity == "lock.eingang" and z.tuer.kontakt == "binary_sensor.tuer"
    assert z.tuer.impuls_sekunden == 5
    assert z.tastenfeld.verzoegerung_sekunden == 3
    assert z.handbetrieb == "input_boolean.beachhub_handbetrieb"


def test_ohne_master_pin_startet_der_dienst_nicht(tmp_path: Path) -> None:
    p = tmp_path / "hall.toml"
    p.write_text(TOML_BEISPIEL.replace(MASTER_HASH, ""), encoding="utf-8")
    with pytest.raises(KonfigFehler, match="master_pin_hash"):
        lade_zuordnung(p)
    p.write_text(TOML_BEISPIEL.replace(MASTER_HASH, "$argon2id$ERSETZEN"), encoding="utf-8")
    with pytest.raises(KonfigFehler, match="master_pin_hash"):
        lade_zuordnung(p)


def test_tuer_muss_lock_oder_switch_sein(tmp_path: Path) -> None:
    p = tmp_path / "hall.toml"
    p.write_text(TOML_BEISPIEL.replace("lock.eingang", "light.eingang"), encoding="utf-8")
    with pytest.raises(KonfigFehler, match="tuer.entity"):
        lade_zuordnung(p)


def test_fehlende_oder_kaputte_datei(tmp_path: Path) -> None:
    with pytest.raises(KonfigFehler, match="fehlt"):
        lade_zuordnung(tmp_path / "gibt_es_nicht.toml")
    p = tmp_path / "hall.toml"
    p.write_text("das ist [kein toml", encoding="utf-8")
    with pytest.raises(KonfigFehler):
        lade_zuordnung(p)


@pytest.mark.parametrize(
    ("kaputt", "schluessel"),
    [
        ('tuer = "lock.eingang"', "tuer"),
        ('heizung = "climate.halle"', "heizung"),
        ("tastenfeld = 3", "tastenfeld"),
        ('handbetrieb = "input_boolean.x"', "handbetrieb"),
        ('felder = "light.feld_1"', "felder"),
        (f'[felder]\n"{F1}" = "light.feld_1"', f"felder.{F1}"),
    ],
)
def test_falscher_tabellentyp_wird_konfigfehler(
    tmp_path: Path, kaputt: str, schluessel: str
) -> None:
    """Ein Schlüssel, der eine Tabelle sein muss, aber ein einfacher Wert ist (z. B. `tuer =
    "lock.eingang"` statt `[tuer]`), führt zu einem lesbaren KonfigFehler statt einem
    AttributeError mit Traceback."""
    p = tmp_path / "hall.toml"
    p.write_text(f'master_pin_hash = "{MASTER_HASH}"\n{kaputt}\n', encoding="utf-8")
    with pytest.raises(KonfigFehler, match=schluessel):
        lade_zuordnung(p)


def test_nicht_numerische_zeiten_werden_abgelehnt(tmp_path: Path) -> None:
    p = tmp_path / "hall.toml"
    p.write_text(
        TOML_BEISPIEL.replace(
            '[tuer]\nentity = "lock.eingang"',
            '[tuer]\nentity = "lock.eingang"\nimpuls_sekunden = "abc"',
        ),
        encoding="utf-8",
    )
    with pytest.raises(KonfigFehler, match="tuer.impuls_sekunden"):
        lade_zuordnung(p)
    p.write_text(
        TOML_BEISPIEL.replace("verzoegerung_sekunden = 3", 'verzoegerung_sekunden = "abc"'),
        encoding="utf-8",
    )
    with pytest.raises(KonfigFehler, match="tastenfeld.verzoegerung_sekunden"):
        lade_zuordnung(p)


def test_zeiten_kommen_mit_zeitzone_zurueck(sitzungen: sessionmaker[Session]) -> None:
    zeitpunkt = datetime(2027, 12, 1, 18, 0, tzinfo=UTC)
    with sitzungen() as db:
        db.add(EreignisZeile(typ="dienst_gestartet", zeitpunkt=zeitpunkt))
        db.commit()
    with sitzungen() as db:
        z = db.get(EreignisZeile, 1)
        assert z is not None and z.zeitpunkt == zeitpunkt and z.zeitpunkt.tzinfo is not None


def test_naive_zeit_wird_abgelehnt(sitzungen: sessionmaker[Session]) -> None:
    with sitzungen() as db:
        db.add(EreignisZeile(typ="dienst_gestartet", zeitpunkt=datetime(2027, 12, 1, 18, 0)))
        with pytest.raises(Exception, match="Zeitzone"):
            db.commit()


def test_seq_wird_nach_dem_loeschen_nicht_wiederverwendet(sitzungen: sessionmaker[Session]) -> None:
    jetzt = datetime(2027, 12, 1, 18, 0, tzinfo=UTC)
    with sitzungen() as db:
        db.add_all([EreignisZeile(typ="dienst_gestartet", zeitpunkt=jetzt) for _ in range(3)])
        db.commit()
        db.execute(delete(EreignisZeile))
        db.commit()
        neu = EreignisZeile(typ="dienst_gestartet", zeitpunkt=jetzt)
        db.add(neu)
        db.commit()
        assert neu.seq == 4


def test_zustand_und_dienst_id(tmp_path: Path) -> None:
    sitzungen = oeffne(tmp_path / "a.sqlite")
    with sitzungen() as db:
        assert lies(db, "handbetrieb", False) is False
        schreibe(db, "handbetrieb", True)
        db.commit()
    with sitzungen() as db:
        assert lies(db, "handbetrieb") is True
    erste = dienst_id(sitzungen)
    assert dienst_id(sitzungen) == erste
    assert dienst_id(oeffne(tmp_path / "a.sqlite")) == erste
    assert dienst_id(oeffne(tmp_path / "b.sqlite")) != erste


def test_uhren() -> None:
    u = SimulierteUhr(datetime(2027, 12, 1, 17, 0, tzinfo=UTC))
    u.vor(minutes=30)
    assert u.jetzt() == datetime(2027, 12, 1, 17, 30, tzinfo=UTC)
    u.stelle(datetime(2027, 12, 2, 0, 0, tzinfo=UTC))
    assert u.jetzt().day == 2
    assert abs(EchteUhr().jetzt() - datetime.now(UTC)) < timedelta(seconds=5)
