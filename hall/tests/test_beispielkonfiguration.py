from pathlib import Path

import pytest
from beachhub_hall.config import KonfigFehler, Umgebung, lade_zuordnung

from tests.hilfen import MASTER_HASH

HALL = Path(__file__).resolve().parent.parent


def test_beispiel_toml_startet_erst_mit_eigenem_master_pin(tmp_path: Path) -> None:
    text = (HALL / "hall.toml.example").read_text(encoding="utf-8")
    beispiel = tmp_path / "hall.toml"
    beispiel.write_text(text, encoding="utf-8")
    with pytest.raises(KonfigFehler, match="master_pin_hash"):
        lade_zuordnung(beispiel)
    beispiel.write_text(text.replace("$argon2id$ERSETZEN", MASTER_HASH), encoding="utf-8")
    z = lade_zuordnung(beispiel)
    assert len(z.felder) == 3 and z.heizung.entity == "climate.halle"
    assert z.tuer.entity == "lock.eingang" and z.tastenfeld.ereignis == "esphome.beachhub_pin"


def test_env_beispiel_kennt_alle_einstellungen() -> None:
    zeilen = (HALL / ".env.example").read_text(encoding="utf-8").splitlines()
    schluessel = {z.split("=", 1)[0].lower() for z in zeilen if "=" in z and not z.startswith("#")}
    assert schluessel == set(Umgebung.model_fields)
