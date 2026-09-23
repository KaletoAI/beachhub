"""Gemeinsame Testdaten. Alle Zeiten sind UTC-aware und werden aus Berliner Ortszeit gebildet."""

import base64
import uuid
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from argon2 import PasswordHasher
from beachhub_hall import plan
from beachhub_hall.aufgaben.steuerung import Steuerung
from beachhub_hall.clock import SimulierteUhr
from beachhub_hall.config import (
    FeldZuordnung,
    HeizungKonfig,
    TastenfeldKonfig,
    TuerKonfig,
    Zuordnung,
)
from beachhub_hall.ereignisse import Ereignisse
from beachhub_hall.ha import HaClient
from beachhub_hall.lage import Lage
from beachhub_hall.pin import PinPruefer
from beachhub_hall.tuer import Tuer
from beachhub_shared.hallenplan import (
    HallenplanInhalt,
    PinParameter,
    PlanBuchung,
    PlanFeld,
    PlanKonfig,
    PlanSperre,
    pin_hash,
)
from beachhub_shared.lesestand import Dokument
from beachhub_shared.signatur import erzeuge_schluesselpaar, signiere
from beachhub_shared.zeit import kombiniere
from sqlalchemy.orm import Session, sessionmaker

from tests.ha_simulator import HaSimulator

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


PIN_PARAMETER = PinParameter(
    salt_b64=base64.b64encode(b"0123456789abcdef").decode(),
    time_cost=1,
    memory_cost=1024,
    parallelism=1,
    hash_len=32,
)
KONFIG = PlanKonfig(
    heiz_vorlauf_minuten=30,
    spiel_temperatur=Decimal("18.0"),
    grund_temperatur=Decimal("0.0"),
    licht_vorlauf_minuten=5,
    licht_nachlauf_minuten=5,
    zutritt_vorlauf_minuten=15,
    praesenz_alarm_minuten=10,
)


def buchung(
    feld: str, beginn: datetime, ende: datetime, pin: str = "123456", buchung_id: str | None = None
) -> PlanBuchung:
    return PlanBuchung(
        buchung_id=buchung_id or str(uuid.uuid4()),
        feld_id=feld,
        beginn=beginn,
        ende=ende,
        pin_hash=pin_hash(pin, PIN_PARAMETER),
    )


def baue_plan(
    buchungen: Iterable[PlanBuchung],
    *,
    ab: datetime | None = None,
    sperren: Iterable[PlanSperre] = (),
    konfig: PlanKonfig = KONFIG,
) -> HallenplanInhalt:
    start = ab or t(0)
    return HallenplanInhalt(
        gueltig_ab=start,
        gueltig_bis=start + timedelta(days=7),
        felder=[
            PlanFeld(id=F1, name="Feld 1", aktiv=True),
            PlanFeld(id=F2, name="Feld 2", aktiv=True),
        ],
        buchungen=list(buchungen),
        sperren=list(sperren),
        konfig=konfig,
        pin=PIN_PARAMETER,
    )


def signiertes_dokument(
    inhalt: HallenplanInhalt, version: int, privat_hex: str, dokument: str = "hallenplan"
) -> Dokument:
    """Signiert genau wie das Hauptsystem (core/services/lesestand.publiziere)."""
    entwurf = Dokument(
        dokument=dokument,
        version=version,
        erzeugt_am=inhalt.gueltig_ab,
        inhalt=inhalt.model_dump(mode="json"),
        signatur="",
    )
    sig = signiere(entwurf.model_dump(mode="json", exclude={"signatur"}), privat_hex)
    return entwurf.model_copy(update={"signatur": sig})


PRIV, _ = erzeuge_schluesselpaar()


def speichere_plan(
    sitzungen: sessionmaker[Session], *buchungen: PlanBuchung, sperren: Iterable[PlanSperre] = ()
) -> None:
    """Baut, signiert und speichert einen Plan wie `plan.speichere()` ihn erwartet. Gemeinsame
    Grundlage der `Aufbau`-Testhelfer, damit die Boilerplate nicht in jedem von ihnen erneut
    steht."""
    inhalt = baue_plan(buchungen, sperren=sperren)
    with sitzungen() as db:
        plan.speichere(db, signiertes_dokument(inhalt, plan.version(db) + 1, PRIV), inhalt, t(0))


class Aufbau:
    """Verdrahtet Tür und PIN-Prüfung gegen eine simulierte Uhr und ein simuliertes HA.
    Gemeinsame Testinfrastruktur ab Task 8, von mehreren Testdateien wiederverwendet."""

    def __init__(
        self,
        sitzungen: sessionmaker[Session],
        uhr: SimulierteUhr,
        ha: HaSimulator,
        tuer: TuerKonfig,
    ) -> None:
        self.sitzungen, self.uhr, self.sim = sitzungen, uhr, ha
        self.schlaf = FakeSchlaf()
        self.client = HaClient(ha.url, HaSimulator.TOKEN)
        self.ereignisse = Ereignisse(sitzungen, uhr)
        self.tuer = Tuer(self.client, tuer, self.ereignisse, self.schlaf)
        z = Zuordnung(
            master_pin_hash=MASTER_HASH,
            tuer=tuer,
            tastenfeld=TastenfeldKonfig(verzoegerung_sekunden=3),
        )
        self.pruefer = PinPruefer(sitzungen, uhr, z, self.ereignisse, self.tuer, self.schlaf)

    def plan(self, *buchungen: PlanBuchung) -> None:
        speichere_plan(self.sitzungen, *buchungen)

    def typen(self) -> list[str]:
        return [e.typ for e in self.ereignisse.unbestaetigt(1000)]

    def geoeffnet(self) -> int:
        return sum(
            1 for a in self.sim.aufrufe if a[:2] in (("lock", "unlock"), ("switch", "turn_on"))
        )


STEUERUNGS_ZUORDNUNG = Zuordnung(
    master_pin_hash=MASTER_HASH,
    felder={
        F1: FeldZuordnung("light.feld_1", "binary_sensor.praesenz_feld_1"),
        F2: FeldZuordnung("light.feld_2", "binary_sensor.praesenz_feld_2"),
    },
    heizung=HeizungKonfig("climate.halle"),
)


class SteuerungsAufbau:
    """Verdrahtet die Steuerung gegen eine simulierte Uhr und ein simuliertes HA. Gemeinsame
    Testinfrastruktur ab Task 9, von mehreren Testdateien wiederverwendet."""

    def __init__(
        self,
        sitzungen: sessionmaker[Session],
        uhr: SimulierteUhr,
        ha: HaSimulator,
        zuordnung: Zuordnung = STEUERUNGS_ZUORDNUNG,
        url: str | None = None,
    ) -> None:
        self.sitzungen, self.uhr, self.sim = sitzungen, uhr, ha
        self.client = HaClient(url or ha.url, HaSimulator.TOKEN)
        self.ereignisse = Ereignisse(sitzungen, uhr)
        self.lage = Lage()
        self.steuerung = Steuerung(
            sitzungen, uhr, zuordnung, self.client, self.ereignisse, self.lage
        )

    def plan(self, *buchungen: PlanBuchung, sperren: Iterable[PlanSperre] = ()) -> None:
        speichere_plan(self.sitzungen, *buchungen, sperren=sperren)

    def ereignis(self, typ: str) -> list[dict[str, object]]:
        return [
            {"feld_id": e.feld_id, **e.daten}
            for e in self.ereignisse.unbestaetigt(1000)
            if e.typ == typ
        ]

    def dienste(self) -> list[tuple[str, str]]:
        return [a[:2] for a in self.sim.aufrufe]

    async def um(self, stunde: int, minute: int = 0) -> None:
        self.uhr.stelle(t(stunde, minute))
        await self.steuerung.einmal()
