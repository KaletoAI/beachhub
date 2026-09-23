"""Vertrag zwischen Hauptsystem (Erzeuger) und Hallendienst (Verbraucher).

Der Betriebsplan reist als signiertes `lesestand.Dokument` mit `dokument = "hallenplan"`;
sein `inhalt` hat die Form von `HallenplanInhalt`. Ereignisse und Status gehen den
umgekehrten Weg. `pin_hash` ist die einzige Implementierung des PIN-Hashes: Das Hauptsystem
erzeugt damit die Hashes, die Halle prüft damit die Eingabe am Tastenfeld.
"""

import base64
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from argon2.low_level import Type, hash_secret_raw
from pydantic import AwareDatetime, BaseModel, Field, field_validator

DOKUMENT = "hallenplan"

EREIGNISTYPEN: frozenset[str] = frozenset(
    {
        "pin_akzeptiert",
        "pin_abgelehnt",
        "tastenfeld_fehlversuche",
        "praesenz_start",
        "praesenz_ende",
        "praesenz_ohne_buchung",
        "tuer_offen_ausserhalb",
        "licht_geschaltet",
        "heizung_gesetzt",
        "ha_nicht_erreichbar",
        "aktor_fehler",
        "plan_verworfen",
        "handbetrieb_an",
        "handbetrieb_aus",
        "dienst_gestartet",
    }
)

# Bei diesen Typen bekommt der Betreiber eine Mail (A-MAIL-2).
ALARM_TYPEN: frozenset[str] = frozenset(
    {
        "tastenfeld_fehlversuche",
        "praesenz_ohne_buchung",
        "aktor_fehler",
        "ha_nicht_erreichbar",
        "plan_verworfen",
        "tuer_offen_ausserhalb",
    }
)


class PlanFeld(BaseModel):
    id: str
    name: str
    aktiv: bool


class PlanBuchung(BaseModel):
    buchung_id: str
    feld_id: str
    beginn: AwareDatetime
    ende: AwareDatetime
    pin_hash: str


class PlanSperre(BaseModel):
    feld_id: str | None
    beginn: AwareDatetime
    ende: AwareDatetime


class PlanKonfig(BaseModel):
    heiz_vorlauf_minuten: int
    spiel_temperatur: Decimal
    grund_temperatur: Decimal
    licht_vorlauf_minuten: int
    licht_nachlauf_minuten: int
    zutritt_vorlauf_minuten: int
    praesenz_alarm_minuten: int


class PinParameter(BaseModel):
    verfahren: Literal["argon2id"] = "argon2id"
    salt_b64: str
    time_cost: int
    memory_cost: int
    parallelism: int
    hash_len: int


class HallenplanInhalt(BaseModel):
    gueltig_ab: AwareDatetime
    gueltig_bis: AwareDatetime
    felder: list[PlanFeld]
    buchungen: list[PlanBuchung]
    sperren: list[PlanSperre]
    konfig: PlanKonfig
    pin: PinParameter


def pin_hash(klar: str, p: PinParameter) -> str:
    """Argon2id mit dem hallenweiten Salt aus dem Plan – deterministisch, damit die Halle
    eine Eingabe ohne Rückfrage beim Hauptsystem gegen die Hashes im Plan prüfen kann."""
    raw = hash_secret_raw(
        klar.encode(),
        base64.b64decode(p.salt_b64),
        time_cost=p.time_cost,
        memory_cost=p.memory_cost,
        parallelism=p.parallelism,
        hash_len=p.hash_len,
        type=Type.ID,
    )
    return "argon2id$" + base64.b64encode(raw).decode()


class HallenEreignis(BaseModel):
    seq: int = Field(ge=1)
    typ: str
    zeitpunkt: datetime
    feld_id: str | None = None
    buchung_id: str | None = None
    daten: dict[str, Any] = Field(default_factory=dict)

    @field_validator("typ")
    @classmethod
    def _typ_bekannt(cls, v: str) -> str:
        if v not in EREIGNISTYPEN:
            raise ValueError(f"unbekannter Ereignistyp: {v}")
        return v

    @field_validator("zeitpunkt")
    @classmethod
    def _mit_zeitzone(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("zeitpunkt braucht eine Zeitzone")
        return v


class FeldStatus(BaseModel):
    feld_id: str
    licht_ist: bool | None = None
    praesenz: bool | None = None


class HeizungStatus(BaseModel):
    soll: Decimal | None = None
    ist: Decimal | None = None


class TuerStatus(BaseModel):
    verriegelt: bool | None = None
    offen: bool | None = None


class HallenStatus(BaseModel):
    planversion: int
    letzter_abruf: datetime | None
    ha_erreichbar: bool
    handbetrieb: bool
    felder: list[FeldStatus] = Field(default_factory=list)
    heizung: HeizungStatus = Field(default_factory=HeizungStatus)
    tuer: TuerStatus = Field(default_factory=TuerStatus)
    warteschlange: int = 0
    version_dienst: str


class EreignisLieferung(BaseModel):
    dienst_id: uuid.UUID
    ereignisse: list[HallenEreignis] = Field(default_factory=list, max_length=500)
    status: HallenStatus | None = None


class EreignisAntwort(BaseModel):
    bestaetigt_bis: int
    plan_neu: bool


class StatusAntwort(BaseModel):
    plan_neu: bool
