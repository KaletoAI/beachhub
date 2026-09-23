"""Gemeinsame Testdaten. Alle Zeiten sind UTC-aware und werden aus Berliner Ortszeit gebildet."""

from datetime import date, datetime, time

from argon2 import PasswordHasher
from beachhub_shared.zeit import kombiniere

F1 = "11111111-1111-1111-1111-111111111111"
F2 = "22222222-2222-2222-2222-222222222222"
TAG = date(2027, 12, 1)
MASTER_PIN = "9999"
# Kleine Argon2-Parameter, damit Tests schnell bleiben; verify() liest sie aus dem Hash.
MASTER_HASH = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1).hash(MASTER_PIN)


def t(stunde: int, minute: int = 0, tag: date = TAG) -> datetime:
    return kombiniere(tag, time(stunde, minute))


TOML_BEISPIEL = f"""
master_pin_hash = "{MASTER_HASH}"

[felder."{F1}"]
licht = "light.feld_1"
praesenz = "binary_sensor.praesenz_feld_1"

[felder."{F2}"]
licht = "light.feld_2"
praesenz = "binary_sensor.praesenz_feld_2"

[heizung]
entity = "climate.halle"

[tuer]
entity = "lock.eingang"
kontakt = "binary_sensor.tuer"

[tastenfeld]
ereignis = "esphome.beachhub_pin"
feld = "code"
verzoegerung_sekunden = 3

[handbetrieb]
entity = "input_boolean.beachhub_handbetrieb"
"""


class FakeSchlaf:
    """Ersatz für asyncio.sleep: kehrt sofort zurück und merkt sich die Dauer."""

    def __init__(self) -> None:
        self.aufrufe: list[float] = []

    async def __call__(self, sekunden: float) -> None:
        self.aufrufe.append(sekunden)
