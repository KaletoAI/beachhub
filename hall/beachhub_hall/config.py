"""Einstellungen: Verbindungen aus der Umgebung (.env), Gerätezuordnung aus hall.toml.

Welche Lampe, welcher Präsenzsensor und welche Heizung zu einem Feld gehören, steht nur hier
(A-FELD-4). Das Hauptsystem kennt die Felder nur über ihre UUID.
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from argon2 import extract_parameters
from argon2.exceptions import InvalidHashError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Umgebung(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    core_url: str = "http://127.0.0.1:8000"
    hall_token: str = ""
    core_client_cert: str = ""
    core_client_key: str = ""
    core_ca: str = ""
    core_public_key: str = ""
    ha_url: str = "http://127.0.0.1:8123"
    ha_token: str = ""
    data_dir: Path = Path("./data")
    hall_toml: Path = Path("./hall.toml")
    health_port: int = 8099


class KonfigFehler(Exception):  # noqa: N818
    pass


@dataclass(frozen=True)
class FeldZuordnung:
    licht: str | None = None
    praesenz: str | None = None


@dataclass(frozen=True)
class HeizungKonfig:
    entity: str | None = None
    ist_sensor: str | None = None


@dataclass(frozen=True)
class TuerKonfig:
    entity: str | None = None
    impuls_sekunden: float = 5.0
    kontakt: str | None = None


@dataclass(frozen=True)
class TastenfeldKonfig:
    ereignis: str = "esphome.beachhub_pin"
    feld: str = "code"
    verzoegerung_sekunden: float = 3.0


@dataclass(frozen=True)
class Zuordnung:
    master_pin_hash: str
    felder: dict[str, FeldZuordnung] = field(default_factory=dict)
    heizung: HeizungKonfig = HeizungKonfig()
    tuer: TuerKonfig = TuerKonfig()
    tastenfeld: TastenfeldKonfig = TastenfeldKonfig()
    handbetrieb: str | None = "input_boolean.beachhub_handbetrieb"

    def feld_fuer_praesenz(self, entity_id: str) -> str | None:
        return next((f for f, z in self.felder.items() if z.praesenz == entity_id), None)

    def gesteuerte(self) -> set[str]:
        """Entitäten, die der Dienst selbst schaltet. Ändert sie jemand von außen, stellt die
        Steuerung den Sollzustand wieder her."""
        lichter = {z.licht for z in self.felder.values() if z.licht}
        return lichter | ({self.heizung.entity} if self.heizung.entity else set())


def _text(wert: Any) -> str | None:
    return str(wert) if wert else None


def lade_zuordnung(pfad: Path) -> Zuordnung:
    try:
        roh = tomllib.loads(pfad.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise KonfigFehler(f"{pfad} fehlt") from e
    except tomllib.TOMLDecodeError as e:
        raise KonfigFehler(f"{pfad} ist kein gültiges TOML: {e}") from e
    master = roh.get("master_pin_hash", "")
    try:
        if not isinstance(master, str) or not master.startswith("$argon2id$"):
            raise InvalidHashError
        extract_parameters(master)
    except InvalidHashError as e:
        raise KonfigFehler(
            "master_pin_hash fehlt oder ist kein argon2id-Hash – mit "
            "`beachhub-hall master-pin` erzeugen"
        ) from e
    tuer = roh.get("tuer", {})
    tuer_entity = _text(tuer.get("entity"))
    if tuer_entity and not tuer_entity.startswith(("lock.", "switch.")):
        raise KonfigFehler("tuer.entity muss eine lock.*- oder switch.*-Entität sein")
    heizung, tasten, hand = (
        roh.get("heizung", {}),
        roh.get("tastenfeld", {}),
        roh.get("handbetrieb", {}),
    )
    return Zuordnung(
        master_pin_hash=master,
        felder={
            str(feld_id): FeldZuordnung(
                licht=_text(z.get("licht")), praesenz=_text(z.get("praesenz"))
            )
            for feld_id, z in roh.get("felder", {}).items()
        },
        heizung=HeizungKonfig(
            entity=_text(heizung.get("entity")), ist_sensor=_text(heizung.get("ist_sensor"))
        ),
        tuer=TuerKonfig(
            entity=tuer_entity,
            impuls_sekunden=float(tuer.get("impuls_sekunden", 5)),
            kontakt=_text(tuer.get("kontakt")),
        ),
        tastenfeld=TastenfeldKonfig(
            ereignis=str(tasten.get("ereignis", "esphome.beachhub_pin")),
            feld=str(tasten.get("feld", "code")),
            verzoegerung_sekunden=float(tasten.get("verzoegerung_sekunden", 3)),
        ),
        handbetrieb=_text(hand.get("entity", "input_boolean.beachhub_handbetrieb")),
    )
