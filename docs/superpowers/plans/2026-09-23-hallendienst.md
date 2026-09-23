# Stufe 3: Hallendienst – Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein eigenständiger Hallendienst (`hall/`) schaltet Licht, Heizung und Tür über Home Assistant passend zu den Buchungen, prüft PINs am Tastenfeld, puffert Ereignisse und arbeitet ohne Verbindung mindestens 72 h weiter. Das Hauptsystem liefert ihm dafür einen signierten 7-Tage-Plan und nimmt Ereignisse und Status entgegen.

**Architecture:** Teil A baut das Paket `hall/`: Python 3.12 mit asyncio, SQLite (WAL) über SQLAlchemy 2.0, HA per REST und WebSocket (aiohttp), das Hauptsystem per HTTPS (httpx). Jede Aufgabe des Dienstes (Plan-Abruf, Steuerung, HA-Zuhörer, Melder, Status nach HA) ist eine Klasse mit einer Methode `einmal()`, die genau einen Durchlauf macht. Die Endlosschleifen rufen sie nur auf. Dadurch lassen sich alle Abläufe mit einer simulierten Uhr deterministisch testen, auch 72 h Offline-Betrieb ohne echtes Warten. Teil B ergänzt das Hauptsystem um den Plan als signiertes Lesestand-Dokument `hallenplan`, die Schnittstelle `/hall/*`, die Tabellen `ereignis` und `hallen_status`, Betreiber-Alarme und die Admin-Seite „Halle“. Der gemeinsame Vertrag (Plan, Ereignis, Status, PIN-Hash) liegt in `shared/beachhub_shared/hallenplan.py`.

**Tech Stack:** Python 3.12, aiohttp 3.14.3, httpx 0.28.1, SQLAlchemy 2.0.36 (SQLite), argon2-cffi 25.1.0, pydantic 2.10.3, pydantic-settings 2.7.0, PyNaCl (über `beachhub-shared`), pytest 8.3.4 mit pytest-asyncio 1.3.0; im Hauptsystem FastAPI, PostgreSQL 16, Alembic, APScheduler wie in Stufe 1.

**Spec:** `docs/superpowers/specs/2026-09-23-hallendienst-design.md` (vollständig), dazu die Hauptspec `docs/superpowers/specs/2026-09-05-beachhub-design.md` § 3.10, § 7, § 8.2, § 9 „Hallentag“ und „Internetausfall Halle“.

**Reihenfolge und Voraussetzungen:**
- **Teil A (Tasks 1–12)** hängt nicht vom Portal ab und kann sofort umgesetzt werden. Getestet wird gegen einen Core-Simulator und einen HA-Simulator.
- **Teil B (Tasks 13–16)** setzt voraus, dass der Portal-Kern-Plan (`docs/superpowers/plans/2026-09-23-portal-kern.md`) bereits nach `main` gemergt ist. Daraus stammen drei Dinge: die Migration `0008` (deshalb gilt hier `down_revision = "0008"`), die CLI `beachhub-core zertifikate` (interne CA und Client-Zertifikate) und `beachhub_shared.kanal.fuer_portal()`, die Allowlist des Kanal-Verteilers, der nur `belegung`, `tarife` und `konto:<uuid>` ans Portal schickt.

## Global Constraints

- Python **3.12**. Jedes Paket hat ein eigenes `pyproject.toml`, Abhängigkeiten sind exakt gepinnt (`==`).
- Neue Abhängigkeiten in `hall`: `aiohttp==3.14.3`, `httpx==0.28.1`, `sqlalchemy==2.0.36`, `argon2-cffi==25.1.0`, `pydantic==2.10.3`, `pydantic-settings==2.7.0`. Entwicklung: `pytest==8.3.4`, `pytest-asyncio==1.3.0` (1.4.0 verlangt ein neueres pytest), `ruff==0.8.4`, `mypy==1.13.0`. `shared` bekommt zusätzlich `argon2-cffi==25.1.0`.
- **Keine Custom Component**: HA nur über REST und WebSocket mit Long-Lived Access Token (Hauptspec § 8.2).
- **Die Halle kennt keine Personendaten**: kein Name, keine E-Mail. PINs nur als Argon2id-Hash. **Eine eingegebene PIN wird nie gespeichert, geloggt oder als Ereignis gemeldet.**
- Der Master-PIN steht nur als Argon2id-Hash in `hall.toml` und nie im Hauptsystem.
- Zeiten werden als UTC-aware `datetime` gespeichert und gerechnet. SQLite speichert UTC ohne Zeitzone, gelesen wird mit `tzinfo=UTC`.
- **Sperren schalten weder Licht noch Heizung und öffnen keine Tür** (Spec § 1, Ⓞ-16).
- Ereignistypen gibt es genau 15 (Spec § 5). `tastenfeld_gesperrt` gibt es nicht mehr, es heißt jetzt `tastenfeld_fehlversuche`.
- Vorgaben der Konfiguration im Hauptsystem: `heiz_vorlauf_minuten = 30`, `spiel_temperatur = 18.0`, `grund_temperatur = 0.0`, `praesenz_alarm_minuten = 10`.
- Zeitgrenzen: Plan-Abruf alle 5 min, Steuerung alle 30 s, Melder alle 60 s mit höchstens 200 Ereignissen je Lieferung, Status nach HA alle 60 s, Fehlversuchsserie ab 5 Versuchen, Serienende nach 15 min Ruhe, `ha_nicht_erreichbar` nach 2 min ohne HA, `binary_sensor.beachhub_verbunden` bei Kontakt innerhalb von 10 min, Ereignisse 90 Tage aufbewahren, Alarm „Halle ohne Kontakt“ nach 60 min, Nachlieferung gilt ab einem Alter von 6 h.
- **Der Dienst wird in keinem Schritt gestartet**, weder `beachhub-hall start` noch uvicorn. Verifiziert wird ausschließlich über Tests.
- Alle Befehle laufen aus der Repo-Wurzel `/home/dev/projekte/beachhub` mit aktivem venv (`source .venv/bin/activate`). Core-Tests brauchen `TEST_DATABASE_URL=postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub_test`.
- Commits auf Deutsch mit Präfix `feat:`, `test:`, `fix:`, `chore:`, `docs:` und Bereich (`feat(hall): …`, `feat(core): …`). Am Ende jeder Commit-Nachricht stehen die Attributionszeilen der Sitzung.
- Lint: `ruff check .` und `ruff format --check .` für das ganze Repo. Typen: `mypy` (strict) für `shared/beachhub_shared`, `core/beachhub_core/services` und neu `hall/beachhub_hall`. Der Code in diesem Plan ist inhaltlich vollständig, aber nicht überall auf 100 Zeichen umbrochen. Nach dem Einfügen bereinigen `ruff check --fix .` (Import-Reihenfolge) und `ruff format .` (Umbruch) das automatisch. Teil A wurde so geprüft: 86 Tests grün, danach ruff und mypy ohne Befund.

## Review Focus

1. **Tastenfeld liefert den Code als Zahl oder mit Leerzeichen.** ESPHome oder eine HA-Automation schickt `code: 482913` als Zahl oder `" 482913"`. Erwartet wird, dass die Tür öffnet. Test: Task 8 (`eingabe(" 482913 ")`) und Task 10 (Ereignis mit `"code": 482913` als int).
2. **Buchung am Tag der Zeitumstellung.** Am 31.10.2027 (Umstellung auf Winterzeit) gelten Licht- und Heizvorlauf in lokaler Zeit genauso wie an jedem anderen Tag, weil intern in UTC gerechnet wird. Test: Task 3 `test_zeitumstellung_winterzeit`.
3. **HA-Neustart löscht die Status-Sensoren.** Per `POST /api/states` geschriebene Zustände überleben keinen HA-Neustart. Erwartet wird, dass der Dienst sie nach der Wiederverbindung sofort neu schreibt. Test: Task 10 `test_nach_verbindung_status_und_steuerung_angestossen`.
4. **Das Hauptsystem antwortet mit einer Fehlerseite oder kaputtem JSON** (Caddy 502, HTML statt JSON). Erwartet: kein Absturz, kein Ereignis `plan_verworfen`, alter Plan bleibt gültig. Test: Task 5 `test_fehlerseite_und_kaputtes_json`.
5. **Plan abgelaufen oder Uhr falsch.** Liegt `jetzt` hinter `gueltig_bis`, etwa nach 8 Tagen offline, öffnet nur noch der Master-PIN, das Licht bleibt aus und die Heizung auf Grundtemperatur. Test: Task 8 `test_abgelaufener_plan_nur_master` und Task 9 `test_abgelaufener_plan_grundzustand`.

## Dateistruktur (Zielbild)

```
shared/beachhub_shared/hallenplan.py     Vertrag: Plan, Ereignis, Status, Lieferung, pin_hash(), Ereignistypen
shared/tests/test_hallenplan.py

hall/
  pyproject.toml, Dockerfile, docker-compose.yml, .env.example, hall.toml.example, README.md
  beachhub_hall/
    __init__.py          __version__
    __main__.py          CLI: `beachhub-hall start` | `beachhub-hall master-pin`
    config.py            Umgebung (pydantic-settings) + hall.toml → Zuordnung
    clock.py             Uhr-Protokoll, EchteUhr, SimulierteUhr
    db.py                SQLite-Tabellen, UTCZeit, lies/schreibe (zustand), dienst_id
    plan.py              pruefe / speichere / lade / version / buchungen_mit_pin
    soll.py              reine Funktionen: sollzustand, zusammenlegen, laufende_buchung, zutritt_offen
    ereignisse.py        Ereignis-Warteschlange (melde, unbestaetigt, bestaetige_bis, offen, raeume_auf)
    core.py              CoreClient (httpx) für /hall/*
    ha.py                HaClient (REST) + HaWebSocket
    lage.py              Lage: zuletzt bekannter HA-Zustand im Speicher
    tuer.py              Tür öffnen (lock.* / switch.* mit Impuls)
    pin.py               PinPruefer mit Fehlversuchsserie
    dienst.py            Dienst: setzt alle Teile zusammen, laufen()
    health.py            GET /health (aiohttp)
    aufgaben/
      __init__.py        takt(): Schleife um einmal() mit Intervall und Wecker
      plan_abruf.py      PlanAbruf
      melder.py          Melder, baue_status
      steuerung.py       Steuerung (inkl. Präsenzalarm)
      ha_zuhoerer.py     HaZuhoerer
      status_ha.py       StatusHa
  tests/
    __init__.py, conftest.py, hilfen.py
    core_simulator.py    Fake-Hauptsystem als httpx.MockTransport
    ha_simulator.py      Fake-HA: REST + WebSocket (aiohttp), startbar mit `python -m tests.ha_simulator`
    test_grundlagen.py test_soll.py test_plan.py test_ereignisse.py test_plan_abruf.py test_melder.py
    test_ha.py test_pin.py test_steuerung.py test_zuhoerer.py test_dienst.py test_integration.py
    test_beispielkonfiguration.py

core/  (Teil B)
  beachhub_core/models/halle.py           Ereignis, HallenStatusZeile
  beachhub_core/services/halle.py         Ereignisse speichern, Kontakt, plan_neu, Alarm-Mails, Kontakt-Prüfung
  beachhub_core/routes/hall.py            GET /hall/plan, POST /hall/ereignisse, POST /hall/status
  beachhub_core/templates/system/halle.html
  alembic/versions/0009_halle.py
  geändert: config.py, main.py, jobs.py, navigation.py, routes/system.py, services/{konfiguration,pin,lesestand}.py, deploy/Caddyfile, .env.example
  tests/test_hallenplan.py test_hall_api.py test_halle_jobs.py test_ui_halle.py test_hall_vertrag.py

docs/betrieb/hallendienst.md             Betrieb, HA-Einrichtung, Probelauf im Homelab
mypy.ini, .github/workflows/ci.yml        um hall erweitert
```

---

# Teil A – Hallendienst (`hall/`)

## Task 1: Vertrag in `shared` – Plan, Ereignis, Status, PIN-Hash

**Files:**
- Create: `shared/beachhub_shared/hallenplan.py`
- Modify: `shared/pyproject.toml` (Abhängigkeit `argon2-cffi==25.1.0`)
- Test: `shared/tests/test_hallenplan.py`

**Interfaces:**
- Consumes: `beachhub_shared.lesestand.Dokument` (vorhanden).
- Produces (`beachhub_shared.hallenplan`):
  - `DOKUMENT = "hallenplan"`, `EREIGNISTYPEN: frozenset[str]` (15 Typen), `ALARM_TYPEN: frozenset[str]` (6 Typen)
  - `PlanFeld(id: str, name: str, aktiv: bool)`, `PlanBuchung(buchung_id: str, feld_id: str, beginn: datetime, ende: datetime, pin_hash: str)`, `PlanSperre(feld_id: str | None, beginn: datetime, ende: datetime)`
  - `PlanKonfig(heiz_vorlauf_minuten: int, spiel_temperatur: Decimal, grund_temperatur: Decimal, licht_vorlauf_minuten: int, licht_nachlauf_minuten: int, zutritt_vorlauf_minuten: int, praesenz_alarm_minuten: int)`
  - `PinParameter(verfahren: Literal["argon2id"] = "argon2id", salt_b64: str, time_cost: int, memory_cost: int, parallelism: int, hash_len: int)`
  - `HallenplanInhalt(gueltig_ab, gueltig_bis, felder: list[PlanFeld], buchungen: list[PlanBuchung], sperren: list[PlanSperre], konfig: PlanKonfig, pin: PinParameter)`
  - `pin_hash(klar: str, p: PinParameter) -> str` mit dem Ergebnis `"argon2id$" + base64(raw)`, identisch zu `core` `pin.hash()`
  - `HallenEreignis(seq: int ≥ 1, typ: str ∈ EREIGNISTYPEN, zeitpunkt: datetime mit tz, feld_id: str | None, buchung_id: str | None, daten: dict)`
  - `FeldStatus(feld_id, licht_ist: bool | None, praesenz: bool | None)`, `HeizungStatus(soll: Decimal | None, ist: Decimal | None)`, `TuerStatus(verriegelt: bool | None, offen: bool | None)`
  - `HallenStatus(planversion: int, letzter_abruf: datetime | None, ha_erreichbar: bool, handbetrieb: bool, felder: list[FeldStatus], heizung: HeizungStatus, tuer: TuerStatus, warteschlange: int, version_dienst: str)`
  - `EreignisLieferung(dienst_id: uuid.UUID, ereignisse: list[HallenEreignis] (max. 500), status: HallenStatus | None)`, `EreignisAntwort(bestaetigt_bis: int, plan_neu: bool)`, `StatusAntwort(plan_neu: bool)`

- [ ] **Step 1: Failing Test schreiben**

`shared/tests/test_hallenplan.py`:
```python
import base64
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from beachhub_shared.hallenplan import (
    ALARM_TYPEN,
    EREIGNISTYPEN,
    EreignisLieferung,
    HallenEreignis,
    HallenplanInhalt,
    PinParameter,
    PlanBuchung,
    PlanFeld,
    PlanKonfig,
    pin_hash,
)
from beachhub_shared.lesestand import Dokument
from pydantic import ValidationError

P = PinParameter(
    salt_b64=base64.b64encode(b"0123456789abcdef").decode(),
    time_cost=1,
    memory_cost=1024,
    parallelism=1,
    hash_len=32,
)
T0 = datetime(2027, 12, 1, 17, 0, tzinfo=UTC)


def test_pin_hash_ist_deterministisch_und_haengt_von_salt_und_pin_ab() -> None:
    assert pin_hash("482913", P) == pin_hash("482913", P)
    assert pin_hash("482913", P).startswith("argon2id$")
    anderes_salt = P.model_copy(update={"salt_b64": base64.b64encode(b"fedcba9876543210").decode()})
    assert pin_hash("482913", anderes_salt) != pin_hash("482913", P)
    assert pin_hash("482914", P) != pin_hash("482913", P)


def test_ereignistypen() -> None:
    assert len(EREIGNISTYPEN) == 15
    assert "tastenfeld_fehlversuche" in EREIGNISTYPEN
    assert "tastenfeld_gesperrt" not in EREIGNISTYPEN
    assert ALARM_TYPEN <= EREIGNISTYPEN
    assert len(ALARM_TYPEN) == 6


def test_ereignis_prueft_typ_zeitzone_und_seq() -> None:
    HallenEreignis(seq=1, typ="pin_akzeptiert", zeitpunkt=T0)
    with pytest.raises(ValidationError):
        HallenEreignis(seq=1, typ="gibt_es_nicht", zeitpunkt=T0)
    with pytest.raises(ValidationError):
        HallenEreignis(seq=1, typ="pin_akzeptiert", zeitpunkt=datetime(2027, 12, 1, 17, 0))
    with pytest.raises(ValidationError):
        HallenEreignis(seq=0, typ="pin_akzeptiert", zeitpunkt=T0)


def test_plan_ueberlebt_den_weg_durch_ein_dokument() -> None:
    inhalt = HallenplanInhalt(
        gueltig_ab=T0,
        gueltig_bis=T0 + timedelta(days=7),
        felder=[PlanFeld(id="f1", name="Feld 1", aktiv=True)],
        buchungen=[
            PlanBuchung(
                buchung_id="b1",
                feld_id="f1",
                beginn=T0 + timedelta(hours=2),
                ende=T0 + timedelta(hours=4),
                pin_hash=pin_hash("482913", P),
            )
        ],
        sperren=[],
        konfig=PlanKonfig(
            heiz_vorlauf_minuten=30,
            spiel_temperatur=Decimal("18.0"),
            grund_temperatur=Decimal("0.0"),
            licht_vorlauf_minuten=5,
            licht_nachlauf_minuten=5,
            zutritt_vorlauf_minuten=15,
            praesenz_alarm_minuten=10,
        ),
        pin=P,
    )
    d = Dokument(
        dokument="hallenplan",
        version=1,
        erzeugt_am=T0,
        inhalt=inhalt.model_dump(mode="json"),
        signatur="00",
    )
    assert d.inhalt["konfig"]["spiel_temperatur"] == "18.0"
    wieder = HallenplanInhalt.model_validate(Dokument.model_validate_json(d.model_dump_json()).inhalt)
    assert wieder == inhalt


def test_lieferung_ohne_status_ist_erlaubt() -> None:
    lieferung = EreignisLieferung.model_validate({"dienst_id": str(uuid.uuid4()), "ereignisse": []})
    assert lieferung.status is None
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd shared && pytest -q tests/test_hallenplan.py`
Expected: FAIL mit `ModuleNotFoundError: No module named 'beachhub_shared.hallenplan'`

- [ ] **Step 3: Abhängigkeit ergänzen**

In `shared/pyproject.toml` die Abhängigkeitsliste ersetzen durch:
```toml
dependencies = [
  "pydantic==2.10.3",
  "pynacl==1.5.0",
  "argon2-cffi==25.1.0",
]
```
Run: `pip install -e shared[dev]`

- [ ] **Step 4: Vertrag implementieren**

`shared/beachhub_shared/hallenplan.py`:
```python
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
from pydantic import BaseModel, Field, field_validator

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
    beginn: datetime
    ende: datetime
    pin_hash: str


class PlanSperre(BaseModel):
    feld_id: str | None
    beginn: datetime
    ende: datetime


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
    gueltig_ab: datetime
    gueltig_bis: datetime
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
```

- [ ] **Step 5: Tests, Lint, Typen**

Run: `cd shared && pytest -q && cd .. && ruff check shared && ruff format --check shared && mypy`
Expected: alle Tests PASS, keine Lint- oder Typfehler

- [ ] **Step 6: Commit**

```bash
git add shared/beachhub_shared/hallenplan.py shared/tests/test_hallenplan.py shared/pyproject.toml
git commit -m "feat(shared): Vertrag Hallenplan, Ereignisse, Status und PIN-Hash"
```

---

## Task 2: `hall`-Grundgerüst – Paket, Konfiguration, Uhr, SQLite, CI

**Files:**
- Create: `hall/pyproject.toml`, `hall/beachhub_hall/__init__.py`, `hall/beachhub_hall/config.py`, `hall/beachhub_hall/clock.py`, `hall/beachhub_hall/db.py`, `hall/beachhub_hall/aufgaben/__init__.py` (vorerst nur Docstring), `hall/tests/__init__.py`, `hall/tests/conftest.py`, `hall/tests/hilfen.py`
- Modify: `mypy.ini`, `.github/workflows/ci.yml`, `.gitignore`
- Test: `hall/tests/test_grundlagen.py`

**Interfaces:**
- Produces `beachhub_hall.__version__ = "0.1.0"`.
- Produces `beachhub_hall.config`:
  - `Umgebung(BaseSettings)` mit `core_url`, `hall_token`, `core_client_cert`, `core_client_key`, `core_ca`, `core_public_key`, `ha_url`, `ha_token`, `data_dir: Path`, `hall_toml: Path`, `health_port: int`
  - `KonfigFehler(Exception)`
  - `FeldZuordnung(licht: str | None, praesenz: str | None)`, `HeizungKonfig(entity: str | None, ist_sensor: str | None)`, `TuerKonfig(entity: str | None, impuls_sekunden: float, kontakt: str | None)`, `TastenfeldKonfig(ereignis: str, feld: str, verzoegerung_sekunden: float)`
  - `Zuordnung(master_pin_hash: str, felder: dict[str, FeldZuordnung], heizung: HeizungKonfig, tuer: TuerKonfig, tastenfeld: TastenfeldKonfig, handbetrieb: str | None)` mit `feld_fuer_praesenz(entity_id) -> str | None` und `gesteuerte() -> set[str]` (alle Licht-Entitäten plus Heizung)
  - `lade_zuordnung(pfad: Path) -> Zuordnung`, wirft `KonfigFehler`
- Produces `beachhub_hall.clock`: Protokoll `Uhr` mit `jetzt() -> datetime`, dazu `EchteUhr()` und `SimulierteUhr(start)` mit `stelle(t)` und `vor(**timedelta_kwargs)`.
- Produces `beachhub_hall.db`:
  - `UTCZeit` (TypeDecorator)
  - Tabellen `PlanMetaZeile` (`plan_meta`), `PlanBuchungZeile` (`plan_buchung`), `PlanSperreZeile` (`plan_sperre`), `PlanFeldZeile` (`plan_feld`), `PlanKonfigZeile` (`plan_konfig`), `EreignisZeile` (`ereignis_queue`, `seq` wird nie wiederverwendet), `ZustandZeile` (`zustand`)
  - `oeffne(pfad: Path) -> sessionmaker[Session]`
  - `lies(db, schluessel, standard=None) -> Any`, `schreibe(db, schluessel, wert) -> None` (flusht, committet nicht)
  - `dienst_id(sitzungen) -> str` (stabile UUID je Datenbankdatei)
- Produces `tests/hilfen.py` (wird in späteren Tasks erweitert): `F1`, `F2` (Feld-UUIDs), `TAG = date(2027, 12, 1)`, `t(stunde, minute=0, tag=TAG) -> datetime`, `MASTER_PIN = "9999"`, `MASTER_HASH`, `TOML_BEISPIEL`, `FakeSchlaf`.
- Produces `tests/conftest.py`-Fixtures: `sitzungen(tmp_path)`, `uhr` (`SimulierteUhr` bei `t(17)`).

- [ ] **Step 1: Paketdefinition anlegen**

`hall/pyproject.toml`:
```toml
[project]
name = "beachhub-hall"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  # beachhub-shared wird separat installiert: pip install -e ../shared
  "aiohttp==3.14.3",
  "httpx==0.28.1",
  "sqlalchemy==2.0.36",
  "argon2-cffi==25.1.0",
  "pydantic==2.10.3",
  "pydantic-settings==2.7.0",
]
[project.optional-dependencies]
dev = ["pytest==8.3.4", "pytest-asyncio==1.3.0", "ruff==0.8.4", "mypy==1.13.0"]
[project.scripts]
beachhub-hall = "beachhub_hall.__main__:main"
[build-system]
requires = ["setuptools==75.6.0"]
build-backend = "setuptools.build_meta"
[tool.setuptools.packages.find]
include = ["beachhub_hall*"]
[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
```

`hall/beachhub_hall/__init__.py`:
```python
"""Hallendienst: steuert die Halle über Home Assistant nach dem Plan des Hauptsystems."""

__version__ = "0.1.0"
```

`hall/beachhub_hall/aufgaben/__init__.py`:
```python
"""Die dauerhaft laufenden Aufgaben des Hallendienstes."""
```

`hall/tests/__init__.py`: leere Datei.

Run: `pip install -e hall[dev]`
Expected: installiert `aiohttp 3.14.3` und `pytest-asyncio 1.3.0`

- [ ] **Step 2: Failing Tests schreiben**

`hall/tests/hilfen.py`:
```python
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


TOML_BEISPIEL = f'''
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
'''


class FakeSchlaf:
    """Ersatz für asyncio.sleep: kehrt sofort zurück und merkt sich die Dauer."""

    def __init__(self) -> None:
        self.aufrufe: list[float] = []

    async def __call__(self, sekunden: float) -> None:
        self.aufrufe.append(sekunden)
```

`hall/tests/conftest.py`:
```python
from pathlib import Path

import pytest
from beachhub_hall.clock import SimulierteUhr
from beachhub_hall.db import oeffne
from sqlalchemy.orm import Session, sessionmaker

from tests.hilfen import t


@pytest.fixture
def sitzungen(tmp_path: Path) -> sessionmaker[Session]:
    return oeffne(tmp_path / "hall.sqlite")


@pytest.fixture
def uhr() -> SimulierteUhr:
    return SimulierteUhr(t(17))
```

`hall/tests/test_grundlagen.py`:
```python
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
```

- [ ] **Step 3: Fehlschlag prüfen**

Run: `cd hall && pytest -q tests/test_grundlagen.py`
Expected: FAIL mit `ModuleNotFoundError: No module named 'beachhub_hall.clock'`

- [ ] **Step 4: Konfiguration, Uhr und Datenbank implementieren**

`hall/beachhub_hall/config.py`:
```python
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
    heizung, tasten, hand = roh.get("heizung", {}), roh.get("tastenfeld", {}), roh.get("handbetrieb", {})
    return Zuordnung(
        master_pin_hash=master,
        felder={
            str(feld_id): FeldZuordnung(licht=_text(z.get("licht")), praesenz=_text(z.get("praesenz")))
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
```

`hall/beachhub_hall/clock.py`:
```python
"""Injizierbare Uhr. Die Fachlogik fragt nie `datetime.now()`, sondern immer eine Uhr –
so lassen sich Hallentage und 72 h Offline-Betrieb in Tests in Sekunden durchspielen."""

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol


class Uhr(Protocol):
    def jetzt(self) -> datetime: ...


class EchteUhr:
    def jetzt(self) -> datetime:
        return datetime.now(UTC)


class SimulierteUhr:
    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("Startzeit braucht eine Zeitzone")
        self._t = start

    def jetzt(self) -> datetime:
        return self._t

    def stelle(self, t: datetime) -> None:
        self._t = t

    def vor(self, **dauer: Any) -> None:
        self._t += timedelta(**dauer)
```

`hall/beachhub_hall/db.py`:
```python
"""SQLite-Datenbank des Hallendienstes (WAL-Modus, eine Datei).

Alle Zeiten werden als UTC gespeichert. SQLite kennt keine Zeitzonen; `UTCZeit` speichert
deshalb ohne tzinfo und liefert beim Lesen immer `tzinfo=UTC` zurück.
"""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, String, Text, create_engine, event
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import TypeDecorator


class UTCZeit(TypeDecorator[datetime]):
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Zeit ohne Zeitzone")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    pass


class PlanMetaZeile(Base):
    __tablename__ = "plan_meta"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    erzeugt_am: Mapped[datetime] = mapped_column(UTCZeit, nullable=False)
    gueltig_bis: Mapped[datetime] = mapped_column(UTCZeit, nullable=False)
    empfangen_am: Mapped[datetime] = mapped_column(UTCZeit, nullable=False)
    # Das ganze signierte Dokument: Quelle für plan.lade(), die Tabellen darunter dienen Abfragen.
    dokument_json: Mapped[str] = mapped_column(Text, nullable=False)


class PlanBuchungZeile(Base):
    __tablename__ = "plan_buchung"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    buchung_id: Mapped[str] = mapped_column(String(36), nullable=False)
    feld_id: Mapped[str] = mapped_column(String(36), nullable=False)
    beginn: Mapped[datetime] = mapped_column(UTCZeit, nullable=False)
    ende: Mapped[datetime] = mapped_column(UTCZeit, nullable=False)
    pin_hash: Mapped[str] = mapped_column(String(200), nullable=False, index=True)


class PlanSperreZeile(Base):
    __tablename__ = "plan_sperre"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    feld_id: Mapped[str | None] = mapped_column(String(36))
    beginn: Mapped[datetime] = mapped_column(UTCZeit, nullable=False)
    ende: Mapped[datetime] = mapped_column(UTCZeit, nullable=False)


class PlanFeldZeile(Base):
    __tablename__ = "plan_feld"
    feld_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    aktiv: Mapped[bool] = mapped_column(Boolean, nullable=False)


class PlanKonfigZeile(Base):
    __tablename__ = "plan_konfig"
    schluessel: Mapped[str] = mapped_column(String(60), primary_key=True)
    wert: Mapped[str] = mapped_column(String(100), nullable=False)


class EreignisZeile(Base):
    __tablename__ = "ereignis_queue"
    # AUTOINCREMENT: eine gelöschte seq wird nie wieder vergeben (90-Tage-Aufräumen), sonst
    # hielte das Hauptsystem neue Ereignisse für Duplikate.
    __table_args__ = {"sqlite_autoincrement": True}
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    typ: Mapped[str] = mapped_column(String(40), nullable=False)
    zeitpunkt: Mapped[datetime] = mapped_column(UTCZeit, nullable=False)
    feld_id: Mapped[str | None] = mapped_column(String(36))
    buchung_id: Mapped[str | None] = mapped_column(String(36))
    daten_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    gesendet_am: Mapped[datetime | None] = mapped_column(UTCZeit, index=True)


class ZustandZeile(Base):
    __tablename__ = "zustand"
    schluessel: Mapped[str] = mapped_column(String(60), primary_key=True)
    wert_json: Mapped[str] = mapped_column(Text, nullable=False)


def _pragmas(dbapi_verbindung: Any, _eintrag: Any) -> None:
    cur = dbapi_verbindung.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def oeffne(pfad: Path) -> sessionmaker[Session]:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{pfad}")
    event.listen(engine, "connect", _pragmas)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def lies(db: Session, schluessel: str, standard: Any = None) -> Any:
    z = db.get(ZustandZeile, schluessel)
    return standard if z is None else json.loads(z.wert_json)


def schreibe(db: Session, schluessel: str, wert: Any) -> None:
    roh = json.dumps(wert, default=str)
    z = db.get(ZustandZeile, schluessel)
    if z is None:
        db.add(ZustandZeile(schluessel=schluessel, wert_json=roh))
    else:
        z.wert_json = roh
    db.flush()


def dienst_id(sitzungen: sessionmaker[Session]) -> str:
    """Einmal je Datenbankdatei erzeugt. Das Hauptsystem erkennt Ereignisse an
    (dienst_id, seq); nach einer Neuinstallation beginnt seq wieder bei 1."""
    with sitzungen() as db:
        wert = lies(db, "dienst_id")
        if wert is None:
            wert = str(uuid.uuid4())
            schreibe(db, "dienst_id", wert)
            db.commit()
        return str(wert)
```

- [ ] **Step 5: Tests laufen lassen**

Run: `cd hall && pytest -q tests/test_grundlagen.py`
Expected: 9 passed

- [ ] **Step 6: mypy, CI und .gitignore erweitern**

`mypy.ini`, Zeile `files` ersetzen durch:
```ini
files = shared/beachhub_shared, core/beachhub_core/services, hall/beachhub_hall
```

`.github/workflows/ci.yml`: Die Installationszeile und die Testzeilen ersetzen durch:
```yaml
      - run: pip install -e shared[dev] -e core[dev] -e hall[dev]
      - run: ruff check . && ruff format --check .
      - run: mypy
      - run: cd shared && pytest -q
      - run: cd hall && pytest -q
      - run: cd core && pytest -q
        env:
          TEST_DATABASE_URL: postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub_test
```

`.gitignore`, anhängen:
```
hall/data/
hall/.env
hall/hall.toml
```

Run: `ruff check hall && ruff format --check hall && mypy`
Expected: keine Fehler (falls `ruff format --check` meldet: `ruff format hall` ausführen)

- [ ] **Step 7: Commit**

```bash
git add hall mypy.ini .github/workflows/ci.yml .gitignore
git commit -m "feat(hall): Grundgerüst mit Konfiguration, Uhr und SQLite"
```

---
## Task 3: Sollzustand als reine Funktion

**Files:**
- Create: `hall/beachhub_hall/soll.py`
- Modify: `hall/tests/hilfen.py` (Plan-Bausteine ergänzen)
- Test: `hall/tests/test_soll.py`

**Interfaces:**
- Consumes: `beachhub_shared.hallenplan.HallenplanInhalt`, `PlanBuchung` (Task 1).
- Produces `beachhub_hall.soll`:
  - `Soll(steuern: bool, licht: dict[str, bool], heizung: Decimal | None)` (frozen dataclass)
  - `zusammenlegen(intervalle: Iterable[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]`: überlappende und sich berührende Intervalle werden zusammengelegt
  - `sollzustand(plan: HallenplanInhalt | None, felder: Iterable[str], jetzt: datetime, handbetrieb: bool) -> Soll`
  - `laufende_buchung(plan: HallenplanInhalt | None, feld_id: str, jetzt: datetime) -> PlanBuchung | None`: Intervall `[beginn, ende)`
  - `zutritt_offen(plan: HallenplanInhalt | None, jetzt: datetime) -> list[PlanBuchung]`: Intervall `[beginn − zutritt_vorlauf, ende)`, bei abgelaufenem Plan leer
- Produces in `tests/hilfen.py`: `PIN_PARAMETER`, `KONFIG`, `buchung(feld, beginn, ende, pin="123456", buchung_id=None) -> PlanBuchung`, `baue_plan(buchungen, *, ab=None, sperren=(), konfig=KONFIG) -> HallenplanInhalt` (Felder F1 und F2, gültig 7 Tage ab `ab`, Vorgabe `t(0)`).

- [ ] **Step 1: Testhilfen erweitern**

`hall/tests/hilfen.py`: Import-Block und Konstanten am Dateianfang ergänzen, sodass die Datei so beginnt (der Rest aus Task 2 bleibt unverändert):
```python
"""Gemeinsame Testdaten. Alle Zeiten sind UTC-aware und werden aus Berliner Ortszeit gebildet."""

import base64
import uuid
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from argon2 import PasswordHasher
from beachhub_shared.hallenplan import (
    HallenplanInhalt,
    PinParameter,
    PlanBuchung,
    PlanFeld,
    PlanKonfig,
    PlanSperre,
    pin_hash,
)
from beachhub_shared.zeit import kombiniere
```
und am Dateiende anhängen:
```python
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
        felder=[PlanFeld(id=F1, name="Feld 1", aktiv=True), PlanFeld(id=F2, name="Feld 2", aktiv=True)],
        buchungen=list(buchungen),
        sperren=list(sperren),
        konfig=konfig,
        pin=PIN_PARAMETER,
    )
```
Die Konstanten `F1`, `F2`, `TAG`, `MASTER_PIN`, `MASTER_HASH`, die Funktion `t()`, `TOML_BEISPIEL` und `FakeSchlaf` aus Task 2 bleiben zwischen Import-Block und neuem Ende stehen. Die Datei hat danach genau einen Import-Block.

- [ ] **Step 2: Failing Tests schreiben**

`hall/tests/test_soll.py`:
```python
from datetime import date, timedelta
from decimal import Decimal

from beachhub_hall.soll import laufende_buchung, sollzustand, zusammenlegen, zutritt_offen
from beachhub_shared.hallenplan import PlanSperre

from tests.hilfen import F1, F2, baue_plan, buchung, t

FELDER = [F1, F2]


def test_zusammenlegen_verbindet_ueberlappende_und_beruehrende() -> None:
    a, b, c = (t(18), t(19)), (t(19), t(20)), (t(21), t(22))
    assert zusammenlegen([c, b, a]) == [(t(18), t(20)), (t(21), t(22))]
    assert zusammenlegen([(t(18), t(20)), (t(18, 30), t(19))]) == [(t(18), t(20))]
    assert zusammenlegen([]) == []


def test_licht_mit_vorlauf_und_nachlauf() -> None:
    plan = baue_plan([buchung(F2, t(19), t(21))])
    assert sollzustand(plan, FELDER, t(18, 54), False).licht == {F1: False, F2: False}
    assert sollzustand(plan, FELDER, t(18, 55), False).licht == {F1: False, F2: True}
    assert sollzustand(plan, FELDER, t(21, 4), False).licht[F2] is True
    assert sollzustand(plan, FELDER, t(21, 5), False).licht[F2] is False


def test_licht_bleibt_zwischen_direkt_folgenden_buchungen_an() -> None:
    plan = baue_plan([buchung(F1, t(19), t(20)), buchung(F1, t(20, 8), t(21))])
    # Lücke 20:00–20:08 ist kleiner als Nachlauf (5) + Vorlauf (5): Licht bleibt an.
    for minute in range(0, 10):
        assert sollzustand(plan, FELDER, t(20, minute), False).licht[F1] is True


def test_licht_geht_aus_bei_grosser_luecke() -> None:
    plan = baue_plan([buchung(F1, t(19), t(20)), buchung(F1, t(21), t(22))])
    assert sollzustand(plan, FELDER, t(20, 30), False).licht[F1] is False


def test_heizung_hallenweit_mit_vorlauf() -> None:
    plan = baue_plan([buchung(F1, t(19), t(20)), buchung(F2, t(20), t(21))])
    assert sollzustand(plan, FELDER, t(18, 29), False).heizung == Decimal("0.0")
    assert sollzustand(plan, FELDER, t(18, 30), False).heizung == Decimal("18.0")
    assert sollzustand(plan, FELDER, t(20, 30), False).heizung == Decimal("18.0")
    assert sollzustand(plan, FELDER, t(21), False).heizung == Decimal("0.0")


def test_sperren_schalten_nichts() -> None:
    plan = baue_plan([], sperren=[PlanSperre(feld_id=None, beginn=t(18), ende=t(23))])
    soll = sollzustand(plan, FELDER, t(19), False)
    assert soll.licht == {F1: False, F2: False}
    assert soll.heizung == Decimal("0.0")
    assert zutritt_offen(plan, t(19)) == []


def test_handbetrieb_steuert_nichts() -> None:
    plan = baue_plan([buchung(F1, t(19), t(20))])
    soll = sollzustand(plan, FELDER, t(19), True)
    assert soll.steuern is False and soll.licht == {} and soll.heizung is None


def test_ohne_plan_licht_aus_und_heizung_unangetastet() -> None:
    soll = sollzustand(None, FELDER, t(19), False)
    assert soll.steuern is True
    assert soll.licht == {F1: False, F2: False}
    assert soll.heizung is None


def test_abgelaufener_plan_gibt_grundzustand() -> None:
    plan = baue_plan([buchung(F1, t(19, tag=date(2027, 12, 9)), t(20, tag=date(2027, 12, 9)))])
    nach_ablauf = plan.gueltig_bis + timedelta(hours=1)
    soll = sollzustand(plan, FELDER, nach_ablauf, False)
    assert soll.licht == {F1: False, F2: False}
    assert soll.heizung == Decimal("0.0")
    assert zutritt_offen(plan, nach_ablauf) == []


def test_nur_zugeordnete_felder_werden_gesteuert() -> None:
    plan = baue_plan([buchung(F2, t(19), t(20))])
    assert sollzustand(plan, [F1], t(19), False).licht == {F1: False}


def test_laufende_buchung_und_zutritt() -> None:
    b = buchung(F1, t(19), t(21))
    plan = baue_plan([b])
    assert laufende_buchung(plan, F1, t(18, 59)) is None
    assert laufende_buchung(plan, F1, t(19)) == b
    assert laufende_buchung(plan, F2, t(19)) is None
    assert laufende_buchung(plan, F1, t(21)) is None
    assert zutritt_offen(plan, t(18, 44)) == []
    assert zutritt_offen(plan, t(18, 45)) == [b]
    assert zutritt_offen(plan, t(20, 59)) == [b]
    assert zutritt_offen(plan, t(21)) == []
    assert laufende_buchung(None, F1, t(19)) is None and zutritt_offen(None, t(19)) == []


def test_zeitumstellung_winterzeit() -> None:
    """31.10.2027: Die Uhr wird um 03:00 auf 02:00 zurückgestellt. Vorläufe gelten in Ortszeit
    trotzdem genau wie an jedem anderen Tag, weil intern in UTC gerechnet wird."""
    tag = date(2027, 10, 31)
    plan = baue_plan([buchung(F1, t(19, tag=tag), t(21, tag=tag))], ab=t(0, tag=tag))
    assert sollzustand(plan, FELDER, t(18, 54, tag=tag), False).licht[F1] is False
    assert sollzustand(plan, FELDER, t(18, 55, tag=tag), False).licht[F1] is True
    assert sollzustand(plan, FELDER, t(18, 29, tag=tag), False).heizung == Decimal("0.0")
    assert sollzustand(plan, FELDER, t(18, 30, tag=tag), False).heizung == Decimal("18.0")
```

- [ ] **Step 3: Fehlschlag prüfen**

Run: `cd hall && pytest -q tests/test_soll.py`
Expected: FAIL mit `ModuleNotFoundError: No module named 'beachhub_hall.soll'`

- [ ] **Step 4: Implementieren**

`hall/beachhub_hall/soll.py`:
```python
"""Sollzustand der Halle als reine Funktionen (A-HALLE-1, A-HALLE-2, A-HALLE-7).

Nur Buchungen schalten. Sperren stehen im Plan, schalten aber weder Licht noch Heizung und
öffnen keine Tür (Hallendienst-Spec § 1, Ⓞ-16).
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from beachhub_shared.hallenplan import HallenplanInhalt, PlanBuchung

Intervall = tuple[datetime, datetime]


@dataclass(frozen=True)
class Soll:
    steuern: bool
    licht: dict[str, bool] = field(default_factory=dict)
    heizung: Decimal | None = None


def zusammenlegen(intervalle: Iterable[Intervall]) -> list[Intervall]:
    ergebnis: list[Intervall] = []
    for von, bis in sorted(intervalle):
        if ergebnis and von <= ergebnis[-1][1]:
            ergebnis[-1] = (ergebnis[-1][0], max(ergebnis[-1][1], bis))
        else:
            ergebnis.append((von, bis))
    return ergebnis


def _enthaelt(intervalle: Iterable[Intervall], jetzt: datetime) -> bool:
    return any(von <= jetzt < bis for von, bis in intervalle)


def _gueltig(plan: HallenplanInhalt | None, jetzt: datetime) -> HallenplanInhalt | None:
    return plan if plan is not None and jetzt < plan.gueltig_bis else None


def sollzustand(
    plan: HallenplanInhalt | None, felder: Iterable[str], jetzt: datetime, handbetrieb: bool
) -> Soll:
    if handbetrieb:
        return Soll(steuern=False)
    felder = list(felder)
    if plan is None:
        # Noch nie ein Plan: Licht aus. Die Grundtemperatur kennt nur der Plan, also
        # bleibt die Heizung unangetastet.
        return Soll(steuern=True, licht=dict.fromkeys(felder, False), heizung=None)
    k = plan.konfig
    gueltig = _gueltig(plan, jetzt)
    if gueltig is None:
        return Soll(steuern=True, licht=dict.fromkeys(felder, False), heizung=k.grund_temperatur)
    vor = timedelta(minutes=k.licht_vorlauf_minuten)
    nach = timedelta(minutes=k.licht_nachlauf_minuten)
    licht = {
        f: _enthaelt(
            zusammenlegen((b.beginn - vor, b.ende + nach) for b in gueltig.buchungen if b.feld_id == f),
            jetzt,
        )
        for f in felder
    }
    heiz_vor = timedelta(minutes=k.heiz_vorlauf_minuten)
    heizen = _enthaelt(zusammenlegen((b.beginn - heiz_vor, b.ende) for b in gueltig.buchungen), jetzt)
    return Soll(
        steuern=True, licht=licht, heizung=k.spiel_temperatur if heizen else k.grund_temperatur
    )


def laufende_buchung(
    plan: HallenplanInhalt | None, feld_id: str, jetzt: datetime
) -> PlanBuchung | None:
    gueltig = _gueltig(plan, jetzt)
    if gueltig is None:
        return None
    return next(
        (b for b in gueltig.buchungen if b.feld_id == feld_id and b.beginn <= jetzt < b.ende), None
    )


def zutritt_offen(plan: HallenplanInhalt | None, jetzt: datetime) -> list[PlanBuchung]:
    gueltig = _gueltig(plan, jetzt)
    if gueltig is None:
        return []
    vorlauf = timedelta(minutes=gueltig.konfig.zutritt_vorlauf_minuten)
    return [b for b in gueltig.buchungen if b.beginn - vorlauf <= jetzt < b.ende]
```

- [ ] **Step 5: Tests laufen lassen**

Run: `cd hall && pytest -q tests/test_soll.py && cd .. && ruff check hall && ruff format --check hall && mypy`
Expected: 12 passed, keine Lint- oder Typfehler

- [ ] **Step 6: Commit**

```bash
git add hall/beachhub_hall/soll.py hall/tests/hilfen.py hall/tests/test_soll.py
git commit -m "feat(hall): Sollzustand für Licht, Heizung und Zutritt als reine Funktion"
```

---

## Task 4: Plan prüfen und speichern, Ereignis-Warteschlange

**Files:**
- Create: `hall/beachhub_hall/plan.py`, `hall/beachhub_hall/ereignisse.py`
- Modify: `hall/tests/hilfen.py` (`signiertes_dokument` ergänzen)
- Test: `hall/tests/test_plan.py`, `hall/tests/test_ereignisse.py`

**Interfaces:**
- Consumes: `db.*` (Task 2), `HallenplanInhalt`, `PlanBuchung`, `HallenEreignis`, `EREIGNISTYPEN`, `DOKUMENT` (Task 1), `beachhub_shared.signatur`, `beachhub_shared.lesestand.Dokument`, `clock.Uhr`.
- Produces `beachhub_hall.plan`:
  - `PlanFehler(grund: str)` mit `.grund ∈ {"schema", "signatur", "version_alt"}`
  - `GespeicherterPlan(version: int, empfangen_am: datetime, inhalt: HallenplanInhalt)`
  - `pruefe(roh: dict, oeffentlich_hex: str, aktuelle_version: int) -> tuple[Dokument, HallenplanInhalt]`
  - `speichere(db, dok, inhalt, empfangen_am) -> None` (eine Transaktion, bei Fehler Rollback)
  - `lade(db) -> GespeicherterPlan | None`, `version(db) -> int` (0 ohne Plan), `buchungen_mit_pin(db, pin_hash: str) -> list[PlanBuchung]`
- Produces `beachhub_hall.ereignisse.Ereignisse(sitzungen, uhr)`:
  - Attribut `neu: asyncio.Event` (wird bei jedem `melde` gesetzt)
  - `melde(typ, *, feld_id=None, buchung_id=None, **daten) -> int` (seq; wirft `ValueError` bei unbekanntem Typ)
  - `unbestaetigt(limit=200) -> list[HallenEreignis]` (nach seq)
  - `bestaetige_bis(seq) -> None`, `offen() -> int`, `raeume_auf() -> int` (löscht bestätigte Ereignisse, die älter als 90 Tage sind)
- Produces in `tests/hilfen.py`: `signiertes_dokument(inhalt, version, privat_hex, dokument="hallenplan") -> Dokument`.

- [ ] **Step 1: Testhilfe ergänzen**

In `hall/tests/hilfen.py` den Import-Block um diese Zeilen ergänzen:
```python
from beachhub_shared.lesestand import Dokument
from beachhub_shared.signatur import signiere
```
und am Ende anhängen:
```python
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
```

- [ ] **Step 2: Failing Tests schreiben**

`hall/tests/test_plan.py`:
```python
from typing import Any

import pytest
from beachhub_hall import plan
from beachhub_shared.hallenplan import PlanFeld, pin_hash
from beachhub_shared.signatur import erzeuge_schluesselpaar
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tests.hilfen import F1, PIN_PARAMETER, baue_plan, buchung, signiertes_dokument, t

PRIV, PUB = erzeuge_schluesselpaar()


def _roh(version: int = 1, dokument: str = "hallenplan") -> dict[str, Any]:
    inhalt = baue_plan([buchung(F1, t(19), t(21), pin="482913", buchung_id="b-1")])
    return signiertes_dokument(inhalt, version, PRIV, dokument).model_dump(mode="json")


def test_gueltiger_plan_wird_gespeichert_und_geladen(sitzungen: sessionmaker[Session]) -> None:
    dok, inhalt = plan.pruefe(_roh(), PUB, 0)
    with sitzungen() as db:
        assert plan.version(db) == 0 and plan.lade(db) is None
        plan.speichere(db, dok, inhalt, t(17))
    with sitzungen() as db:
        geladen = plan.lade(db)
        assert geladen is not None
        assert geladen.version == 1 and geladen.inhalt == inhalt and geladen.empfangen_am == t(17)
        assert plan.version(db) == 1
        treffer = plan.buchungen_mit_pin(db, pin_hash("482913", PIN_PARAMETER))
        assert [b.buchung_id for b in treffer] == ["b-1"]
        assert treffer[0].beginn == t(19)
        assert plan.buchungen_mit_pin(db, pin_hash("000000", PIN_PARAMETER)) == []


def test_falscher_schluessel_und_manipulation() -> None:
    _, fremd_pub = erzeuge_schluesselpaar()
    with pytest.raises(plan.PlanFehler) as e:
        plan.pruefe(_roh(), fremd_pub, 0)
    assert e.value.grund == "signatur"
    roh = _roh()
    roh["inhalt"]["buchungen"][0]["ende"] = t(23).isoformat()
    with pytest.raises(plan.PlanFehler) as e:
        plan.pruefe(roh, PUB, 0)
    assert e.value.grund == "signatur"


def test_alte_oder_gleiche_version_wird_verworfen() -> None:
    with pytest.raises(plan.PlanFehler) as e:
        plan.pruefe(_roh(version=3), PUB, 3)
    assert e.value.grund == "version_alt"
    with pytest.raises(plan.PlanFehler):
        plan.pruefe(_roh(version=2), PUB, 3)


def test_falscher_dokumentname_und_kaputtes_schema() -> None:
    with pytest.raises(plan.PlanFehler) as e:
        plan.pruefe(_roh(dokument="belegung"), PUB, 0)
    assert e.value.grund == "schema"
    with pytest.raises(plan.PlanFehler) as e:
        plan.pruefe({"dokument": "hallenplan"}, PUB, 0)
    assert e.value.grund == "schema"
    # korrekt signiert, aber der Inhalt ist kein Hallenplan
    from beachhub_shared.lesestand import Dokument
    from beachhub_shared.signatur import signiere

    entwurf = Dokument(dokument="hallenplan", version=1, erzeugt_am=t(0), inhalt={"x": 1}, signatur="")
    sig = signiere(entwurf.model_dump(mode="json", exclude={"signatur"}), PRIV)
    with pytest.raises(plan.PlanFehler) as e:
        plan.pruefe(entwurf.model_copy(update={"signatur": sig}).model_dump(mode="json"), PUB, 0)
    assert e.value.grund == "schema"


def test_ersetzen_ist_atomar(sitzungen: sessionmaker[Session]) -> None:
    dok, inhalt = plan.pruefe(_roh(version=1), PUB, 0)
    with sitzungen() as db:
        plan.speichere(db, dok, inhalt, t(17))
    kaputt = inhalt.model_copy(
        update={"felder": [PlanFeld(id=F1, name="A", aktiv=True), PlanFeld(id=F1, name="B", aktiv=True)]}
    )
    dok2 = signiertes_dokument(kaputt, 2, PRIV)
    with sitzungen() as db, pytest.raises(IntegrityError):
        plan.speichere(db, dok2, kaputt, t(18))
    with sitzungen() as db:
        geladen = plan.lade(db)
        assert geladen is not None and geladen.version == 1
        assert len(plan.buchungen_mit_pin(db, pin_hash("482913", PIN_PARAMETER))) == 1
```

`hall/tests/test_ereignisse.py`:
```python
import json
from decimal import Decimal

import pytest
from beachhub_hall.clock import SimulierteUhr
from beachhub_hall.db import EreignisZeile
from beachhub_hall.ereignisse import Ereignisse
from sqlalchemy.orm import Session, sessionmaker

from tests.hilfen import F1


def test_melden_und_ausliefern(sitzungen: sessionmaker[Session], uhr: SimulierteUhr) -> None:
    e = Ereignisse(sitzungen, uhr)
    assert not e.neu.is_set()
    assert e.melde("dienst_gestartet", version="0.1.0") == 1
    assert e.neu.is_set()
    uhr.vor(minutes=1)
    assert e.melde("heizung_gesetzt", soll=Decimal("18.0")) == 2
    assert e.melde("licht_geschaltet", feld_id=F1, an=True) == 3
    liste = e.unbestaetigt()
    assert [x.seq for x in liste] == [1, 2, 3]
    assert liste[1].daten == {"soll": "18.0"} and liste[1].zeitpunkt == uhr.jetzt()
    assert liste[2].feld_id == F1
    assert [x.seq for x in e.unbestaetigt(limit=2)] == [1, 2]
    assert e.offen() == 3
    e.bestaetige_bis(2)
    assert e.offen() == 1 and [x.seq for x in e.unbestaetigt()] == [3]


def test_unbekannter_typ(sitzungen: sessionmaker[Session], uhr: SimulierteUhr) -> None:
    with pytest.raises(ValueError, match="gibt_es_nicht"):
        Ereignisse(sitzungen, uhr).melde("gibt_es_nicht")


def test_aufraeumen_nur_bestaetigte_nach_90_tagen(
    sitzungen: sessionmaker[Session], uhr: SimulierteUhr
) -> None:
    e = Ereignisse(sitzungen, uhr)
    e.melde("dienst_gestartet")
    e.melde("dienst_gestartet")
    e.bestaetige_bis(1)
    uhr.vor(days=89)
    assert e.raeume_auf() == 0
    uhr.vor(days=2)
    assert e.raeume_auf() == 1
    with sitzungen() as db:
        rest = db.query(EreignisZeile).all()
        assert [z.seq for z in rest] == [2]
        assert json.loads(rest[0].daten_json) == {}
    assert e.offen() == 1
```

- [ ] **Step 3: Fehlschlag prüfen**

Run: `cd hall && pytest -q tests/test_plan.py tests/test_ereignisse.py`
Expected: FAIL mit `ImportError: cannot import name 'plan' from 'beachhub_hall'`

- [ ] **Step 4: Implementieren**

`hall/beachhub_hall/plan.py`:
```python
"""Betriebsplan: prüfen, atomar ersetzen, laden (Hallendienst-Spec § 5 „Plan-Abruf“).

Geprüft wird in dieser Reihenfolge: Form, Signatur, Dokumentname, Version, Inhalt. Erst ein
vollständig geprüfter Plan ersetzt den alten – in einer Transaktion, damit ein Fehler
mittendrin nie einen halben Plan hinterlässt.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from beachhub_shared import signatur
from beachhub_shared.hallenplan import DOKUMENT, HallenplanInhalt, PlanBuchung
from beachhub_shared.lesestand import Dokument
from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from beachhub_hall.db import (
    PlanBuchungZeile,
    PlanFeldZeile,
    PlanKonfigZeile,
    PlanMetaZeile,
    PlanSperreZeile,
)


class PlanFehler(Exception):  # noqa: N818
    def __init__(self, grund: str) -> None:
        super().__init__(grund)
        self.grund = grund


@dataclass(frozen=True)
class GespeicherterPlan:
    version: int
    empfangen_am: datetime
    inhalt: HallenplanInhalt


def pruefe(
    roh: dict[str, Any], oeffentlich_hex: str, aktuelle_version: int
) -> tuple[Dokument, HallenplanInhalt]:
    try:
        dok = Dokument.model_validate(roh)
    except ValidationError as e:
        raise PlanFehler("schema") from e
    daten = dok.model_dump(mode="json", exclude={"signatur"})
    if not signatur.pruefe(daten, dok.signatur, oeffentlich_hex):
        raise PlanFehler("signatur")
    if dok.dokument != DOKUMENT:
        raise PlanFehler("schema")
    if dok.version <= aktuelle_version:
        raise PlanFehler("version_alt")
    try:
        inhalt = HallenplanInhalt.model_validate(dok.inhalt)
    except ValidationError as e:
        raise PlanFehler("schema") from e
    return dok, inhalt


def speichere(db: Session, dok: Dokument, inhalt: HallenplanInhalt, empfangen_am: datetime) -> None:
    try:
        for tabelle in (PlanBuchungZeile, PlanSperreZeile, PlanFeldZeile, PlanKonfigZeile, PlanMetaZeile):
            db.execute(delete(tabelle))
        db.add(
            PlanMetaZeile(
                id=1,
                version=dok.version,
                erzeugt_am=dok.erzeugt_am,
                gueltig_bis=inhalt.gueltig_bis,
                empfangen_am=empfangen_am,
                dokument_json=dok.model_dump_json(),
            )
        )
        db.add_all(PlanFeldZeile(feld_id=f.id, name=f.name, aktiv=f.aktiv) for f in inhalt.felder)
        db.add_all(
            PlanBuchungZeile(
                buchung_id=b.buchung_id, feld_id=b.feld_id, beginn=b.beginn, ende=b.ende, pin_hash=b.pin_hash
            )
            for b in inhalt.buchungen
        )
        db.add_all(PlanSperreZeile(feld_id=s.feld_id, beginn=s.beginn, ende=s.ende) for s in inhalt.sperren)
        db.add_all(
            PlanKonfigZeile(schluessel=k, wert=str(v))
            for k, v in inhalt.konfig.model_dump(mode="json").items()
        )
        db.commit()
    except Exception:
        db.rollback()
        raise


def lade(db: Session) -> GespeicherterPlan | None:
    meta = db.get(PlanMetaZeile, 1)
    if meta is None:
        return None
    dok = Dokument.model_validate_json(meta.dokument_json)
    return GespeicherterPlan(
        version=meta.version,
        empfangen_am=meta.empfangen_am,
        inhalt=HallenplanInhalt.model_validate(dok.inhalt),
    )


def version(db: Session) -> int:
    meta = db.get(PlanMetaZeile, 1)
    return meta.version if meta else 0


def buchungen_mit_pin(db: Session, pin_hash: str) -> list[PlanBuchung]:
    zeilen = db.scalars(select(PlanBuchungZeile).where(PlanBuchungZeile.pin_hash == pin_hash))
    return [
        PlanBuchung(
            buchung_id=z.buchung_id, feld_id=z.feld_id, beginn=z.beginn, ende=z.ende, pin_hash=z.pin_hash
        )
        for z in zeilen
    ]
```

`hall/beachhub_hall/ereignisse.py`:
```python
"""Ereignis-Warteschlange: alles, was in der Halle passiert, wartet hier auf das Hauptsystem.

Ein Ereignis gilt erst als zugestellt, wenn das Hauptsystem seine seq bestätigt hat. Bis dahin
bleibt es liegen – auch über Tage ohne Verbindung (A-HALLE-6).
"""

import asyncio
import json
import logging
from datetime import timedelta
from typing import Any

from beachhub_shared.hallenplan import EREIGNISTYPEN, HallenEreignis
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from beachhub_hall.clock import Uhr
from beachhub_hall.db import EreignisZeile

logger = logging.getLogger(__name__)
AUFBEWAHRUNG = timedelta(days=90)


class Ereignisse:
    def __init__(self, sitzungen: sessionmaker[Session], uhr: Uhr) -> None:
        self._sitzungen = sitzungen
        self._uhr = uhr
        self.neu = asyncio.Event()

    def melde(
        self, typ: str, *, feld_id: str | None = None, buchung_id: str | None = None, **daten: Any
    ) -> int:
        if typ not in EREIGNISTYPEN:
            raise ValueError(f"unbekannter Ereignistyp: {typ}")
        with self._sitzungen() as db:
            z = EreignisZeile(
                typ=typ,
                zeitpunkt=self._uhr.jetzt(),
                feld_id=feld_id,
                buchung_id=buchung_id,
                daten_json=json.dumps(daten, default=str, sort_keys=True),
            )
            db.add(z)
            db.commit()
            seq = z.seq
        logger.info("Ereignis %s %s feld=%s buchung=%s %s", seq, typ, feld_id, buchung_id, daten)
        self.neu.set()
        return seq

    def unbestaetigt(self, limit: int = 200) -> list[HallenEreignis]:
        with self._sitzungen() as db:
            zeilen = db.scalars(
                select(EreignisZeile)
                .where(EreignisZeile.gesendet_am.is_(None))
                .order_by(EreignisZeile.seq)
                .limit(limit)
            ).all()
            return [
                HallenEreignis(
                    seq=z.seq,
                    typ=z.typ,
                    zeitpunkt=z.zeitpunkt,
                    feld_id=z.feld_id,
                    buchung_id=z.buchung_id,
                    daten=json.loads(z.daten_json),
                )
                for z in zeilen
            ]

    def bestaetige_bis(self, seq: int) -> None:
        with self._sitzungen() as db:
            db.execute(
                update(EreignisZeile)
                .where(EreignisZeile.seq <= seq, EreignisZeile.gesendet_am.is_(None))
                .values(gesendet_am=self._uhr.jetzt())
            )
            db.commit()

    def offen(self) -> int:
        with self._sitzungen() as db:
            anzahl = db.scalar(
                select(func.count()).select_from(EreignisZeile).where(EreignisZeile.gesendet_am.is_(None))
            )
            return int(anzahl or 0)

    def raeume_auf(self) -> int:
        grenze = self._uhr.jetzt() - AUFBEWAHRUNG
        with self._sitzungen() as db:
            ergebnis = db.execute(
                delete(EreignisZeile).where(
                    EreignisZeile.gesendet_am.is_not(None), EreignisZeile.gesendet_am < grenze
                )
            )
            db.commit()
            return int(ergebnis.rowcount)
```

Hinweis zu `raeume_auf`: Maßgeblich ist das Bestätigungsdatum `gesendet_am`, nicht der Zeitpunkt des Ereignisses. Ein Ereignis, das nach langem Ausfall spät bestätigt wurde, bleibt deshalb nach der Bestätigung noch volle 90 Tage liegen.

- [ ] **Step 5: Tests laufen lassen**

Run: `cd hall && pytest -q tests/test_plan.py tests/test_ereignisse.py && cd .. && ruff check hall && ruff format --check hall && mypy`
Expected: 8 passed, keine Lint- oder Typfehler

- [ ] **Step 6: Commit**

```bash
git add hall/beachhub_hall/plan.py hall/beachhub_hall/ereignisse.py hall/tests/hilfen.py hall/tests/test_plan.py hall/tests/test_ereignisse.py
git commit -m "feat(hall): Plan prüfen und atomar ersetzen, Ereignis-Warteschlange"
```

---
## Task 5: Hauptsystem-Client, Core-Simulator und Plan-Abruf

**Files:**
- Create: `hall/beachhub_hall/core.py`, `hall/beachhub_hall/aufgaben/plan_abruf.py`, `hall/tests/core_simulator.py`
- Test: `hall/tests/test_plan_abruf.py`

**Interfaces:**
- Consumes: `plan.*`, `Ereignisse` (Task 4), `db.lies/schreibe` (Task 2), `config.Zuordnung` (Task 2), `EreignisLieferung`, `EreignisAntwort` (Task 1), `tests.hilfen.signiertes_dokument` (Task 4).
- Produces `beachhub_hall.core`:
  - `CoreNichtErreichbar(Exception)`: wird bei jedem Netz-, HTTP- oder Formatfehler geworfen
  - `CoreClient(basis_url: str, token: str, *, verify: ssl.SSLContext | bool = True, transport: httpx.AsyncBaseTransport | None = None)`
  - `async hole_plan(ab: int) -> dict[str, Any] | None` (`None` bei 304), `async sende_ereignisse(lieferung: EreignisLieferung) -> EreignisAntwort`, `async schliesse() -> None`
  - `ssl_kontext(ca: str, cert: str, key: str) -> ssl.SSLContext | bool`
- Produces `beachhub_hall.aufgaben.plan_abruf.PlanAbruf(sitzungen, uhr, core, oeffentlich_hex, ereignisse, zuordnung, nach_neuem_plan: Callable[[], None])`:
  - Attribut `wecker: asyncio.Event` (setzt der Melder bei `plan_neu`)
  - `async einmal() -> bool` (True, wenn ein neuer Plan gespeichert wurde). Schreibt `zustand["letzter_abruf"]` (ISO-Zeit) bei 200 und 304. Meldet `plan_verworfen` mit `grund`. Meldet `aktor_fehler` mit `grund="feld_nicht_zugeordnet"` einmal je aktivem Feld ohne Eintrag in `hall.toml`.
- Produces `tests/core_simulator.py`: `CoreSimulator` mit `TOKEN`, `BASIS`, `privat`, `oeffentlich`, `veroeffentliche(inhalt, *, version=None, privat=None, dokument="hallenplan")`, `offline: bool`, `plan_neu: bool`, `ersatzantwort: httpx.Response | None`, `empfangen: list[HallenEreignis]`, `status: list[HallenStatus]`, `anfragen: int`, `transport() -> httpx.MockTransport`, `typen() -> list[str]`, `client() -> CoreClient`.

- [ ] **Step 1: Failing Tests schreiben**

`hall/tests/core_simulator.py`:
```python
"""Fake-Hauptsystem für Tests: signiert Pläne wie das echte und nimmt Ereignisse an.

Läuft als httpx.MockTransport im selben Prozess – ohne Netz, ohne Wartezeit. `offline = True`
simuliert den Internetausfall, `ersatzantwort` eine Fehlerseite des Reverse Proxys.
"""

import httpx
from beachhub_hall.core import CoreClient
from beachhub_shared.hallenplan import (
    EreignisLieferung,
    HallenEreignis,
    HallenplanInhalt,
    HallenStatus,
)
from beachhub_shared.lesestand import Dokument
from beachhub_shared.signatur import erzeuge_schluesselpaar

from tests.hilfen import signiertes_dokument


class CoreSimulator:
    TOKEN = "hall-test-token"
    BASIS = "http://core.test"

    def __init__(self) -> None:
        self.privat, self.oeffentlich = erzeuge_schluesselpaar()
        self.dokument: Dokument | None = None
        self.empfangen: list[HallenEreignis] = []
        self.status: list[HallenStatus] = []
        self.offline = False
        self.plan_neu = False
        self.ersatzantwort: httpx.Response | None = None
        self.anfragen = 0

    def veroeffentliche(
        self,
        inhalt: HallenplanInhalt,
        *,
        version: int | None = None,
        privat: str | None = None,
        dokument: str = "hallenplan",
    ) -> Dokument:
        if version is None:
            version = self.dokument.version + 1 if self.dokument else 1
        self.dokument = signiertes_dokument(inhalt, version, privat or self.privat, dokument)
        return self.dokument

    def _antwort(self, request: httpx.Request) -> httpx.Response:
        self.anfragen += 1
        if self.offline:
            raise httpx.ConnectError("Hauptsystem nicht erreichbar", request=request)
        if self.ersatzantwort is not None:
            return self.ersatzantwort
        if request.headers.get("authorization") != f"Bearer {self.TOKEN}":
            return httpx.Response(401)
        if request.method == "GET" and request.url.path == "/hall/plan":
            if self.dokument is None:
                return httpx.Response(404)
            if int(request.url.params.get("ab", "0")) == self.dokument.version:
                return httpx.Response(304)
            return httpx.Response(200, json=self.dokument.model_dump(mode="json"))
        if request.method == "POST" and request.url.path == "/hall/ereignisse":
            lieferung = EreignisLieferung.model_validate_json(request.content)
            bekannt = {e.seq for e in self.empfangen}
            self.empfangen += [e for e in lieferung.ereignisse if e.seq not in bekannt]
            if lieferung.status is not None:
                self.status.append(lieferung.status)
            neu, self.plan_neu = self.plan_neu, False
            hoechste = max((e.seq for e in self.empfangen), default=0)
            return httpx.Response(200, json={"bestaetigt_bis": hoechste, "plan_neu": neu})
        return httpx.Response(404)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._antwort)

    def client(self) -> CoreClient:
        return CoreClient(self.BASIS, self.TOKEN, transport=self.transport())

    def typen(self) -> list[str]:
        return [e.typ for e in self.empfangen]
```

`hall/tests/test_plan_abruf.py`:
```python
from collections.abc import AsyncIterator

import httpx
import pytest
from beachhub_hall import plan
from beachhub_hall.aufgaben.plan_abruf import PlanAbruf
from beachhub_hall.clock import SimulierteUhr
from beachhub_hall.config import FeldZuordnung, Zuordnung
from beachhub_hall.db import lies
from beachhub_hall.ereignisse import Ereignisse
from beachhub_shared.signatur import erzeuge_schluesselpaar
from sqlalchemy.orm import Session, sessionmaker

from tests.core_simulator import CoreSimulator
from tests.hilfen import F1, F2, MASTER_HASH, baue_plan, buchung, t

ZUORDNUNG = Zuordnung(
    master_pin_hash=MASTER_HASH,
    felder={F1: FeldZuordnung("light.feld_1"), F2: FeldZuordnung("light.feld_2")},
)


class Aufbau:
    def __init__(self, sitzungen: sessionmaker[Session], uhr: SimulierteUhr, core: CoreSimulator, zuordnung: Zuordnung = ZUORDNUNG) -> None:
        self.core = core
        self.sitzungen = sitzungen
        self.ereignisse = Ereignisse(sitzungen, uhr)
        self.angestossen = 0
        self.client = core.client()
        self.abruf = PlanAbruf(
            sitzungen, uhr, self.client, core.oeffentlich, self.ereignisse, zuordnung, self._anstossen
        )

    def _anstossen(self) -> None:
        self.angestossen += 1

    def typen(self) -> list[str]:
        return [e.typ for e in self.ereignisse.unbestaetigt()]


@pytest.fixture
async def a(sitzungen: sessionmaker[Session], uhr: SimulierteUhr) -> AsyncIterator[Aufbau]:
    aufbau = Aufbau(sitzungen, uhr, CoreSimulator())
    yield aufbau
    await aufbau.client.schliesse()


async def test_neuer_plan_wird_gespeichert_danach_304(a: Aufbau) -> None:
    a.core.veroeffentliche(baue_plan([buchung(F1, t(19), t(20))]))
    assert await a.abruf.einmal() is True
    assert a.angestossen == 1
    with a.sitzungen() as db:
        assert plan.version(db) == 1
        assert lies(db, "letzter_abruf") == t(17).isoformat()
    assert await a.abruf.einmal() is False
    assert a.angestossen == 1
    a.core.veroeffentliche(baue_plan([]))
    assert await a.abruf.einmal() is True
    with a.sitzungen() as db:
        assert plan.version(db) == 2
    assert a.typen() == []


async def test_falsche_signatur_wird_verworfen_und_gemeldet(a: Aufbau) -> None:
    a.core.veroeffentliche(baue_plan([]))
    await a.abruf.einmal()
    fremd, _ = erzeuge_schluesselpaar()
    a.core.veroeffentliche(baue_plan([buchung(F1, t(19), t(20))]), privat=fremd)
    assert await a.abruf.einmal() is False
    with a.sitzungen() as db:
        assert plan.version(db) == 1
    verworfen = [e for e in a.ereignisse.unbestaetigt() if e.typ == "plan_verworfen"]
    assert len(verworfen) == 1 and verworfen[0].daten == {"grund": "signatur", "version": 2}


async def test_offline_ist_kein_ereignis(a: Aufbau) -> None:
    a.core.offline = True
    assert await a.abruf.einmal() is False
    assert a.typen() == []
    with a.sitzungen() as db:
        assert lies(db, "letzter_abruf") is None


async def test_fehlerseite_und_kaputtes_json(a: Aufbau) -> None:
    a.core.veroeffentliche(baue_plan([]))
    await a.abruf.einmal()
    a.core.veroeffentliche(baue_plan([buchung(F1, t(19), t(20))]))
    a.core.ersatzantwort = httpx.Response(502, text="<html>Bad Gateway</html>")
    assert await a.abruf.einmal() is False
    a.core.ersatzantwort = httpx.Response(200, text="<html>kein JSON</html>")
    assert await a.abruf.einmal() is False
    a.core.ersatzantwort = httpx.Response(200, json=[1, 2, 3])
    assert await a.abruf.einmal() is False
    assert a.typen() == []
    with a.sitzungen() as db:
        assert plan.version(db) == 1


async def test_nicht_zugeordnetes_feld_wird_einmal_gemeldet(
    sitzungen: sessionmaker[Session], uhr: SimulierteUhr
) -> None:
    nur_f1 = Zuordnung(master_pin_hash=MASTER_HASH, felder={F1: FeldZuordnung("light.feld_1")})
    aufbau = Aufbau(sitzungen, uhr, CoreSimulator(), nur_f1)
    aufbau.core.veroeffentliche(baue_plan([]))
    await aufbau.abruf.einmal()
    aufbau.core.veroeffentliche(baue_plan([]))
    await aufbau.abruf.einmal()
    fehler = [e for e in aufbau.ereignisse.unbestaetigt() if e.typ == "aktor_fehler"]
    assert len(fehler) == 1
    assert fehler[0].feld_id == F2 and fehler[0].daten == {"grund": "feld_nicht_zugeordnet"}
    await aufbau.client.schliesse()
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd hall && pytest -q tests/test_plan_abruf.py`
Expected: FAIL mit `ModuleNotFoundError: No module named 'beachhub_hall.core'`

- [ ] **Step 3: Implementieren**

`hall/beachhub_hall/core.py`:
```python
"""Verbindung zum Hauptsystem (Hauptspec § 8.2): HTTPS mit mTLS über WireGuard.

Jeder Fehler – Netz, HTTP-Status, kein JSON – wird zu `CoreNichtErreichbar`. Für den Dienst
ist das derselbe Fall: Er arbeitet mit dem gespeicherten Plan weiter und versucht es später.
"""

import ssl
from typing import Any

import httpx
from beachhub_shared.hallenplan import EreignisAntwort, EreignisLieferung
from pydantic import ValidationError


class CoreNichtErreichbar(Exception):  # noqa: N818
    pass


def ssl_kontext(ca: str, cert: str, key: str) -> ssl.SSLContext | bool:
    """CA des Caddy vor dem Hauptsystem und Client-Zertifikat der Halle. Ohne beides (lokale
    Entwicklung über http://) prüft httpx wie üblich."""
    if not ca and not cert:
        return True
    kontext = ssl.create_default_context(cafile=ca or None)
    if cert:
        kontext.load_cert_chain(cert, key or None)
    return kontext


class CoreClient:
    def __init__(
        self,
        basis_url: str,
        token: str,
        *,
        verify: ssl.SSLContext | bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=basis_url,
            headers={"Authorization": f"Bearer {token}"},
            verify=verify,
            transport=transport,
            timeout=httpx.Timeout(20.0),
        )

    async def hole_plan(self, ab: int) -> dict[str, Any] | None:
        try:
            r = await self._client.get("/hall/plan", params={"ab": ab})
        except httpx.HTTPError as e:
            raise CoreNichtErreichbar(str(e)) from e
        if r.status_code == 304:
            return None
        if r.status_code != 200:
            raise CoreNichtErreichbar(f"GET /hall/plan: HTTP {r.status_code}")
        try:
            daten = r.json()
        except ValueError as e:
            raise CoreNichtErreichbar("GET /hall/plan: kein JSON") from e
        if not isinstance(daten, dict):
            raise CoreNichtErreichbar("GET /hall/plan: kein JSON-Objekt")
        return daten

    async def sende_ereignisse(self, lieferung: EreignisLieferung) -> EreignisAntwort:
        try:
            r = await self._client.post("/hall/ereignisse", json=lieferung.model_dump(mode="json"))
        except httpx.HTTPError as e:
            raise CoreNichtErreichbar(str(e)) from e
        if r.status_code != 200:
            raise CoreNichtErreichbar(f"POST /hall/ereignisse: HTTP {r.status_code}")
        try:
            return EreignisAntwort.model_validate_json(r.content)
        except ValidationError as e:
            raise CoreNichtErreichbar("POST /hall/ereignisse: unerwartete Antwort") from e

    async def schliesse(self) -> None:
        await self._client.aclose()
```

`hall/beachhub_hall/aufgaben/plan_abruf.py`:
```python
"""Plan-Abruf: alle 5 Minuten, beim Start und sofort, wenn das Hauptsystem `plan_neu` meldet."""

import asyncio
import logging
from collections.abc import Callable

from beachhub_shared.hallenplan import HallenplanInhalt
from sqlalchemy.orm import Session, sessionmaker

from beachhub_hall import plan
from beachhub_hall.clock import Uhr
from beachhub_hall.config import Zuordnung
from beachhub_hall.core import CoreClient, CoreNichtErreichbar
from beachhub_hall.db import lies, schreibe
from beachhub_hall.ereignisse import Ereignisse

logger = logging.getLogger(__name__)


class PlanAbruf:
    def __init__(
        self,
        sitzungen: sessionmaker[Session],
        uhr: Uhr,
        core: CoreClient,
        oeffentlich_hex: str,
        ereignisse: Ereignisse,
        zuordnung: Zuordnung,
        nach_neuem_plan: Callable[[], None],
    ) -> None:
        self._sitzungen = sitzungen
        self._uhr = uhr
        self._core = core
        self._oeffentlich = oeffentlich_hex
        self._ereignisse = ereignisse
        self._zuordnung = zuordnung
        self._nach_neuem_plan = nach_neuem_plan
        self.wecker = asyncio.Event()

    async def einmal(self) -> bool:
        with self._sitzungen() as db:
            aktuell = plan.version(db)
        try:
            roh = await self._core.hole_plan(aktuell)
        except CoreNichtErreichbar as e:
            # Kein Ereignis: Der Ausfall zeigt sich im Status und im Kontakt-Alarm des Hauptsystems.
            logger.warning("Plan-Abruf fehlgeschlagen: %s", e)
            return False
        jetzt = self._uhr.jetzt()
        with self._sitzungen() as db:
            schreibe(db, "letzter_abruf", jetzt.isoformat())
            db.commit()
        if roh is None:
            return False
        try:
            dok, inhalt = plan.pruefe(roh, self._oeffentlich, aktuell)
        except plan.PlanFehler as e:
            logger.error("Plan verworfen: %s", e.grund)
            self._ereignisse.melde("plan_verworfen", grund=e.grund, version=roh.get("version"))
            return False
        with self._sitzungen() as db:
            plan.speichere(db, dok, inhalt, jetzt)
        logger.info("Plan Version %s übernommen (%s Buchungen)", dok.version, len(inhalt.buchungen))
        self._melde_unzugeordnete(inhalt)
        self._nach_neuem_plan()
        return True

    def _melde_unzugeordnete(self, inhalt: HallenplanInhalt) -> None:
        with self._sitzungen() as db:
            gemeldet = set(lies(db, "unzugeordnet_gemeldet", []))
            neu = [
                f.id
                for f in inhalt.felder
                if f.aktiv and f.id not in self._zuordnung.felder and f.id not in gemeldet
            ]
            if not neu:
                return
            schreibe(db, "unzugeordnet_gemeldet", sorted(gemeldet | set(neu)))
            db.commit()
        for feld_id in neu:
            self._ereignisse.melde("aktor_fehler", feld_id=feld_id, grund="feld_nicht_zugeordnet")
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd hall && pytest -q tests/test_plan_abruf.py && cd .. && ruff check hall && ruff format --check hall && mypy`
Expected: 5 passed, keine Lint- oder Typfehler

- [ ] **Step 5: Commit**

```bash
git add hall/beachhub_hall/core.py hall/beachhub_hall/aufgaben/plan_abruf.py hall/tests/core_simulator.py hall/tests/test_plan_abruf.py
git commit -m "feat(hall): Plan-Abruf beim Hauptsystem mit Signaturprüfung"
```

---

## Task 6: Lage im Speicher, Melder und Status für das Hauptsystem

**Files:**
- Create: `hall/beachhub_hall/lage.py`, `hall/beachhub_hall/aufgaben/melder.py`
- Test: `hall/tests/test_melder.py`

**Interfaces:**
- Consumes: `CoreClient`, `CoreNichtErreichbar` (Task 5), `Ereignisse` (Task 4), `plan.version` (Task 4), `db.lies/schreibe/dienst_id` (Task 2), `config.Zuordnung`, `HeizungKonfig` (Task 2), `HallenStatus`, `FeldStatus`, `HeizungStatus`, `TuerStatus`, `EreignisLieferung`, `EreignisAntwort` (Task 1), `beachhub_hall.__version__`.
- Produces `beachhub_hall.lage.Lage` (dataclass): `ist: dict[str, dict[str, Any]]` (Entity → HA-Zustandsobjekt), `ha_verbunden: bool = False`, `soll_heizung: Decimal | None = None`, dazu `state(entity: str | None) -> str | None`, `ist_an(entity: str | None) -> bool | None`, `temperatur(heizung: HeizungKonfig) -> Decimal | None`.
- Produces `beachhub_hall.aufgaben.melder`:
  - `baue_status(sitzungen, zuordnung, lage, ereignisse) -> HallenStatus`
  - `Melder(sitzungen, uhr, core, ereignisse, status: Callable[[], HallenStatus], plan_wecker: asyncio.Event)`
  - `async einmal() -> EreignisAntwort | None`: bei Fehler Backoff 1, 2, 4 … 60 s nach der Uhr, schreibt `zustand["letzter_kontakt"]`, setzt `plan_wecker` bei `plan_neu`, setzt `ereignisse.neu`, wenn noch Ereignisse warten
  - `async leeren() -> None`: sendet, bis nichts mehr offen ist oder nichts mehr vorankommt

- [ ] **Step 1: Failing Tests schreiben**

`hall/tests/test_melder.py`:
```python
import asyncio
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from beachhub_hall import plan
from beachhub_hall.aufgaben.melder import Melder, baue_status
from beachhub_hall.clock import SimulierteUhr
from beachhub_hall.config import FeldZuordnung, HeizungKonfig, TuerKonfig, Zuordnung
from beachhub_hall.db import lies, schreibe
from beachhub_hall.ereignisse import Ereignisse
from beachhub_hall.lage import Lage
from sqlalchemy.orm import Session, sessionmaker

from tests.core_simulator import CoreSimulator
from tests.hilfen import F1, F2, MASTER_HASH, baue_plan, signiertes_dokument, t

ZUORDNUNG = Zuordnung(
    master_pin_hash=MASTER_HASH,
    felder={
        F1: FeldZuordnung("light.feld_1", "binary_sensor.praesenz_feld_1"),
        F2: FeldZuordnung("light.feld_2", None),
    },
    heizung=HeizungKonfig("climate.halle"),
    tuer=TuerKonfig("lock.eingang", kontakt="binary_sensor.tuer"),
)


class Aufbau:
    def __init__(self, sitzungen: sessionmaker[Session], uhr: SimulierteUhr) -> None:
        self.sitzungen, self.uhr = sitzungen, uhr
        self.core = CoreSimulator()
        self.client = self.core.client()
        self.ereignisse = Ereignisse(sitzungen, uhr)
        self.lage = Lage()
        self.plan_wecker = asyncio.Event()
        self.melder = Melder(
            sitzungen,
            uhr,
            self.client,
            self.ereignisse,
            lambda: baue_status(sitzungen, ZUORDNUNG, self.lage, self.ereignisse),
            self.plan_wecker,
        )


@pytest.fixture
async def a(sitzungen: sessionmaker[Session], uhr: SimulierteUhr) -> AsyncIterator[Aufbau]:
    aufbau = Aufbau(sitzungen, uhr)
    yield aufbau
    await aufbau.client.schliesse()


async def test_sendet_ereignisse_und_status(a: Aufbau) -> None:
    a.ereignisse.melde("dienst_gestartet", version="0.1.0")
    a.ereignisse.melde("licht_geschaltet", feld_id=F1, an=True)
    antwort = await a.melder.einmal()
    assert antwort is not None and antwort.bestaetigt_bis == 2
    assert a.core.typen() == ["dienst_gestartet", "licht_geschaltet"]
    assert a.ereignisse.offen() == 0
    assert a.core.status[-1].warteschlange == 2  # Stand beim Absenden
    with a.sitzungen() as db:
        assert lies(db, "letzter_kontakt") == t(17).isoformat()


async def test_offline_mit_backoff_nach_der_uhr(a: Aufbau) -> None:
    a.ereignisse.melde("dienst_gestartet")
    a.core.offline = True
    assert await a.melder.einmal() is None
    anfragen = a.core.anfragen
    assert await a.melder.einmal() is None
    assert a.core.anfragen == anfragen  # innerhalb des Backoffs kein neuer Versuch
    a.uhr.vor(seconds=2)
    a.core.offline = False
    assert await a.melder.einmal() is not None
    assert a.ereignisse.offen() == 0


async def test_plan_neu_weckt_den_plan_abruf(a: Aufbau) -> None:
    a.core.plan_neu = True
    await a.melder.einmal()
    assert a.plan_wecker.is_set()


async def test_mehr_als_200_ereignisse(a: Aufbau) -> None:
    for _ in range(250):
        a.ereignisse.melde("pin_abgelehnt")
    a.ereignisse.neu.clear()
    await a.melder.einmal()
    assert len(a.core.empfangen) == 200 and a.ereignisse.neu.is_set()
    await a.melder.leeren()
    assert [e.seq for e in a.core.empfangen] == list(range(1, 251))
    assert a.ereignisse.offen() == 0


async def test_leeren_bricht_ab_wenn_offline(a: Aufbau) -> None:
    a.ereignisse.melde("dienst_gestartet")
    a.core.offline = True
    await asyncio.wait_for(a.melder.leeren(), timeout=2)
    assert a.ereignisse.offen() == 1


async def test_status_inhalt(a: Aufbau) -> None:
    a.core.veroeffentliche(baue_plan([]))
    dok = signiertes_dokument(baue_plan([]), 4, a.core.privat)
    with a.sitzungen() as db:
        plan.speichere(db, dok, baue_plan([]), t(16))
        schreibe(db, "handbetrieb", True)
        schreibe(db, "letzter_abruf", t(16, 30).isoformat())
        schreibe(db, "praesenz", {F1: {"seit": t(16).isoformat(), "ohne_buchung_seit": None, "alarm": False}})
        db.commit()
    a.lage.ha_verbunden = True
    a.lage.soll_heizung = Decimal("18.0")
    a.lage.ist = {
        "light.feld_1": {"state": "on"},
        "light.feld_2": {"state": "off"},
        "climate.halle": {"state": "heat", "attributes": {"current_temperature": 12.5}},
        "lock.eingang": {"state": "locked"},
        "binary_sensor.tuer": {"state": "off"},
    }
    s = baue_status(a.sitzungen, ZUORDNUNG, a.lage, a.ereignisse)
    assert s.planversion == 4 and s.handbetrieb is True and s.ha_erreichbar is True
    assert s.letzter_abruf == t(16, 30)
    felder = {f.feld_id: f for f in s.felder}
    assert felder[F1].licht_ist is True and felder[F1].praesenz is True
    assert felder[F2].licht_ist is False and felder[F2].praesenz is None
    assert s.heizung.soll == Decimal("18.0") and s.heizung.ist == Decimal("12.5")
    assert s.tuer.verriegelt is True and s.tuer.offen is False
    assert s.version_dienst == "0.1.0"


def test_lage_temperatur_robust() -> None:
    lage = Lage(ist={"sensor.t": {"state": "unavailable"}, "climate.h": {"state": "heat", "attributes": {}}})
    assert lage.temperatur(HeizungKonfig("climate.h", "sensor.t")) is None
    assert lage.temperatur(HeizungKonfig("climate.h")) is None
    lage.ist["sensor.t"] = {"state": "nan"}
    assert lage.temperatur(HeizungKonfig("climate.h", "sensor.t")) is None
    lage.ist["sensor.t"] = {"state": "17.25"}
    assert lage.temperatur(HeizungKonfig("climate.h", "sensor.t")) == Decimal("17.25")
    assert lage.ist_an(None) is None and lage.ist_an("light.unbekannt") is None
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd hall && pytest -q tests/test_melder.py`
Expected: FAIL mit `ModuleNotFoundError: No module named 'beachhub_hall.aufgaben.melder'`

- [ ] **Step 3: Implementieren**

`hall/beachhub_hall/lage.py`:
```python
"""Zuletzt bekannter Zustand der HA-Entitäten, nur im Speicher.

Der HA-Zuhörer hält ihn über den WebSocket aktuell, die Steuerung ergänzt ihn bei jedem
Abgleich. Er dient nur der Anzeige im Status; geschaltet wird immer nach frischem Ist aus HA.
"""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from beachhub_hall.config import HeizungKonfig


def _dezimal(wert: object) -> Decimal | None:
    if wert is None:
        return None
    try:
        d = Decimal(str(wert))
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


@dataclass
class Lage:
    ist: dict[str, dict[str, Any]] = field(default_factory=dict)
    ha_verbunden: bool = False
    soll_heizung: Decimal | None = None

    def state(self, entity: str | None) -> str | None:
        if entity is None or entity not in self.ist:
            return None
        wert = self.ist[entity].get("state")
        return str(wert) if wert is not None else None

    def ist_an(self, entity: str | None) -> bool | None:
        s = self.state(entity)
        return None if s is None else s == "on"

    def temperatur(self, heizung: HeizungKonfig) -> Decimal | None:
        if heizung.ist_sensor:
            return _dezimal(self.state(heizung.ist_sensor))
        if heizung.entity and heizung.entity in self.ist:
            attribute = self.ist[heizung.entity].get("attributes") or {}
            return _dezimal(attribute.get("current_temperature"))
        return None
```

`hall/beachhub_hall/aufgaben/melder.py`:
```python
"""Melder: liefert Ereignisse und Status ans Hauptsystem (Hauptspec § 8.2).

Neue Ereignisse wecken ihn sofort, sonst läuft er alle 60 s. Ist das Hauptsystem nicht
erreichbar, wartet er nach der Uhr 1, 2, 4 … höchstens 60 s, bevor er es wieder versucht –
neue Ereignisse wecken ihn in dieser Zeit nicht. Nichts geht verloren: Erst die Bestätigung
des Hauptsystems markiert ein Ereignis als zugestellt.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from beachhub_shared.hallenplan import (
    EreignisAntwort,
    EreignisLieferung,
    FeldStatus,
    HallenStatus,
    HeizungStatus,
    TuerStatus,
)
from sqlalchemy.orm import Session, sessionmaker

from beachhub_hall import __version__, plan
from beachhub_hall.clock import Uhr
from beachhub_hall.config import Zuordnung
from beachhub_hall.core import CoreClient, CoreNichtErreichbar
from beachhub_hall.db import dienst_id, lies, schreibe
from beachhub_hall.ereignisse import Ereignisse
from beachhub_hall.lage import Lage

logger = logging.getLogger(__name__)
MAX_JE_LIEFERUNG = 200
MAX_BACKOFF = 60.0


def baue_status(
    sitzungen: sessionmaker[Session], zuordnung: Zuordnung, lage: Lage, ereignisse: Ereignisse
) -> HallenStatus:
    with sitzungen() as db:
        version = plan.version(db)
        abruf = lies(db, "letzter_abruf")
        handbetrieb = bool(lies(db, "handbetrieb", False))
        praesenz = lies(db, "praesenz", {})
    tuer = zuordnung.tuer
    tuer_state = lage.state(tuer.entity)
    return HallenStatus(
        planversion=version,
        letzter_abruf=datetime.fromisoformat(abruf) if abruf else None,
        ha_erreichbar=lage.ha_verbunden,
        handbetrieb=handbetrieb,
        felder=[
            FeldStatus(
                feld_id=feld_id,
                licht_ist=lage.ist_an(z.licht) if z.licht else None,
                praesenz=(feld_id in praesenz) if z.praesenz else None,
            )
            for feld_id, z in zuordnung.felder.items()
        ],
        heizung=HeizungStatus(soll=lage.soll_heizung, ist=lage.temperatur(zuordnung.heizung)),
        tuer=TuerStatus(
            verriegelt=(tuer_state == "locked")
            if tuer.entity and tuer.entity.startswith("lock.") and tuer_state is not None
            else None,
            offen=lage.ist_an(tuer.kontakt) if tuer.kontakt else None,
        ),
        warteschlange=ereignisse.offen(),
        version_dienst=__version__,
    )


class Melder:
    def __init__(
        self,
        sitzungen: sessionmaker[Session],
        uhr: Uhr,
        core: CoreClient,
        ereignisse: Ereignisse,
        status: Callable[[], HallenStatus],
        plan_wecker: asyncio.Event,
    ) -> None:
        self._sitzungen = sitzungen
        self._uhr = uhr
        self._core = core
        self._ereignisse = ereignisse
        self._status = status
        self._plan_wecker = plan_wecker
        self._backoff = 0.0
        self._naechster_versuch: datetime | None = None

    async def einmal(self) -> EreignisAntwort | None:
        jetzt = self._uhr.jetzt()
        if self._naechster_versuch is not None and jetzt < self._naechster_versuch:
            return None
        lieferung = EreignisLieferung(
            dienst_id=dienst_id(self._sitzungen),
            ereignisse=self._ereignisse.unbestaetigt(MAX_JE_LIEFERUNG),
            status=self._status(),
        )
        try:
            antwort = await self._core.sende_ereignisse(lieferung)
        except CoreNichtErreichbar as e:
            self._backoff = min(MAX_BACKOFF, max(1.0, self._backoff * 2))
            self._naechster_versuch = jetzt + timedelta(seconds=self._backoff)
            logger.warning("Hauptsystem nicht erreichbar (%s), nächster Versuch in %.0f s", e, self._backoff)
            return None
        self._backoff = 0.0
        self._naechster_versuch = None
        self._ereignisse.bestaetige_bis(antwort.bestaetigt_bis)
        with self._sitzungen() as db:
            schreibe(db, "letzter_kontakt", jetzt.isoformat())
            db.commit()
        if antwort.plan_neu:
            self._plan_wecker.set()
        if self._ereignisse.offen() > 0:
            self._ereignisse.neu.set()
        return antwort

    async def leeren(self) -> None:
        vorher = -1
        while self._ereignisse.offen() > 0:
            antwort = await self.einmal()
            if antwort is None or antwort.bestaetigt_bis == vorher:
                return
            vorher = antwort.bestaetigt_bis
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd hall && pytest -q tests/test_melder.py && cd .. && ruff check hall && ruff format --check hall && mypy`
Expected: 7 passed, keine Lint- oder Typfehler

- [ ] **Step 5: Commit**

```bash
git add hall/beachhub_hall/lage.py hall/beachhub_hall/aufgaben/melder.py hall/tests/test_melder.py
git commit -m "feat(hall): Melder liefert Ereignisse und Status mit Backoff ans Hauptsystem"
```

---
## Task 7: Home-Assistant-Client und HA-Simulator

**Files:**
- Create: `hall/beachhub_hall/ha.py`, `hall/tests/ha_simulator.py`
- Modify: `hall/tests/conftest.py` (Fixture `ha`)
- Test: `hall/tests/test_ha.py`

**Interfaces:**
- Produces `beachhub_hall.ha`:
  - `HaFehler(Exception)`
  - `HaClient(basis_url: str, token: str)` mit `async zustand(entity_id) -> dict | None` (None bei 404), `async zustaende() -> list[dict]`, `async dienst(domain, service, daten: dict) -> None`, `async setze_zustand(entity_id, state: str, attribute: dict) -> None`, `async websocket() -> HaWebSocket` (angemeldet), `async schliesse() -> None`. Jeder Netz- oder HTTP-Fehler ≥ 400 außer 404 wird zu `HaFehler`.
  - `HaWebSocket` mit `async abonniere(event_type: str) -> None`, `async naechstes() -> dict` (liefert das Objekt `event` einer `type: "event"`-Nachricht), `async schliesse() -> None`
- Produces `tests/ha_simulator.py`: `HaSimulator` mit
  - `TOKEN`, `url` (nach `start()`), `zustaende: dict[str, dict]`, `aufrufe: list[tuple[str, str, dict]]`, `geschrieben: dict[str, dict]`, `fehler_bei_diensten: bool`, `verbindungen_gesamt: int`
  - `entitaet(entity_id, state, **attribute)` (anlegen ohne Ereignis), `async setze(entity_id, state, **attribute)` (mit `state_changed`), `async feuere(event_type, daten)`, `abonnements() -> int`, `async trenne_alle()`, `async start()`, `async stop()`
  - Wirkung der Dienste: `light.turn_on/turn_off`, `switch.turn_on/turn_off`, `lock.unlock/lock`, `climate.set_temperature`. Nur vorhandene Entitäten ändern sich, wie in echtem HA.
- Produces Fixture `ha` in `tests/conftest.py`: gestarteter Simulator mit den Standard-Entitäten `light.feld_1`, `light.feld_2` (off), `binary_sensor.praesenz_feld_1`, `binary_sensor.praesenz_feld_2`, `binary_sensor.tuer` (off), `climate.halle` (state `heat`, `temperature` 0.0, `current_temperature` 5.0), `lock.eingang` (locked), `input_boolean.beachhub_handbetrieb` (off).

- [ ] **Step 1: HA-Simulator schreiben**

`hall/tests/ha_simulator.py`:
```python
"""Fake-Home-Assistant für Tests und lokale Probeläufe.

REST: GET /api/states, GET/POST /api/states/<entity_id>, POST /api/services/<domain>/<service>.
WebSocket /api/websocket nach dem echten Protokoll: auth_required → auth → auth_ok,
subscribe_events → result, danach Nachrichten vom Typ "event".

Lokal starten (z. B. für einen Probelauf ohne echtes HA): `cd hall && python -m tests.ha_simulator`
"""

import json
from datetime import UTC, datetime
from typing import Any

from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer


def _jetzt() -> str:
    return datetime.now(UTC).isoformat()


class HaSimulator:
    TOKEN = "ha-test-token"

    def __init__(self) -> None:
        self.zustaende: dict[str, dict[str, Any]] = {}
        self.aufrufe: list[tuple[str, str, dict[str, Any]]] = []
        self.geschrieben: dict[str, dict[str, Any]] = {}
        self.fehler_bei_diensten = False
        self.verbindungen_gesamt = 0
        self._abos: dict[web.WebSocketResponse, dict[int, str | None]] = {}
        self._server: TestServer | None = None
        self.url = ""
        self.app = web.Application(middlewares=[self._auth])
        self.app.router.add_get("/api/states", self._alle)
        self.app.router.add_get("/api/states/{entity_id}", self._einer)
        self.app.router.add_post("/api/states/{entity_id}", self._schreiben)
        self.app.router.add_post("/api/services/{domain}/{service}", self._dienst)
        self.app.router.add_get("/api/websocket", self._websocket)

    # --- Steuerung durch Tests -------------------------------------------------------

    def entitaet(self, entity_id: str, state: str, **attribute: Any) -> None:
        self.zustaende[entity_id] = {
            "entity_id": entity_id,
            "state": state,
            "attributes": attribute,
            "last_changed": _jetzt(),
        }

    async def setze(self, entity_id: str, state: str, **attribute: Any) -> None:
        alt = self.zustaende.get(entity_id)
        attr = {**(alt or {}).get("attributes", {}), **attribute}
        self.entitaet(entity_id, state, **attr)
        await self._state_changed(entity_id, alt)

    async def feuere(self, event_type: str, daten: dict[str, Any]) -> None:
        await self._sende_event(event_type, daten)

    def abonnements(self) -> int:
        return sum(len(a) for a in self._abos.values())

    async def trenne_alle(self) -> None:
        for ws in list(self._abos):
            await ws.close()

    async def start(self) -> None:
        self._server = TestServer(self.app, host="127.0.0.1")
        await self._server.start_server()
        self.url = str(self._server.make_url("")).rstrip("/")

    async def stop(self) -> None:
        await self.trenne_alle()
        if self._server is not None:
            await self._server.close()

    # --- intern ------------------------------------------------------------------------

    @web.middleware
    async def _auth(self, request: web.Request, handler: Any) -> web.StreamResponse:
        if request.path != "/api/websocket" and request.headers.get("Authorization") != f"Bearer {self.TOKEN}":
            return web.json_response({"message": "401: Unauthorized"}, status=401)
        antwort: web.StreamResponse = await handler(request)
        return antwort

    async def _alle(self, request: web.Request) -> web.Response:
        return web.json_response(list(self.zustaende.values()))

    async def _einer(self, request: web.Request) -> web.Response:
        z = self.zustaende.get(request.match_info["entity_id"])
        if z is None:
            return web.json_response({"message": "Entity not found."}, status=404)
        return web.json_response(z)

    async def _schreiben(self, request: web.Request) -> web.Response:
        entity_id = request.match_info["entity_id"]
        daten = await request.json()
        self.geschrieben[entity_id] = daten
        neu = entity_id not in self.zustaende
        await self.setze(entity_id, str(daten["state"]), **daten.get("attributes", {}))
        return web.json_response(self.zustaende[entity_id], status=201 if neu else 200)

    async def _dienst(self, request: web.Request) -> web.Response:
        domain, service = request.match_info["domain"], request.match_info["service"]
        daten = await request.json()
        self.aufrufe.append((domain, service, daten))
        if self.fehler_bei_diensten:
            return web.json_response({"message": "Service call failed"}, status=500)
        ziele = daten.get("entity_id", [])
        for entity_id in [ziele] if isinstance(ziele, str) else ziele:
            if entity_id not in self.zustaende:
                continue  # wie echtes HA: unbekannte Entität, keine Wirkung
            if (domain, service) in (("light", "turn_on"), ("switch", "turn_on")):
                await self.setze(entity_id, "on")
            elif (domain, service) in (("light", "turn_off"), ("switch", "turn_off")):
                await self.setze(entity_id, "off")
            elif (domain, service) == ("lock", "unlock"):
                await self.setze(entity_id, "unlocked")
            elif (domain, service) == ("lock", "lock"):
                await self.setze(entity_id, "locked")
            elif (domain, service) == ("climate", "set_temperature"):
                await self.setze(
                    entity_id, self.zustaende[entity_id]["state"], temperature=daten["temperature"]
                )
        return web.json_response([])

    async def _state_changed(self, entity_id: str, alt: dict[str, Any] | None) -> None:
        await self._sende_event(
            "state_changed",
            {"entity_id": entity_id, "old_state": alt, "new_state": self.zustaende.get(entity_id)},
        )

    async def _sende_event(self, event_type: str, daten: dict[str, Any]) -> None:
        for ws, abos in list(self._abos.items()):
            for sub_id, typ in abos.items():
                if typ is None or typ == event_type:
                    nachricht = {
                        "id": sub_id,
                        "type": "event",
                        "event": {
                            "event_type": event_type,
                            "data": daten,
                            "origin": "LOCAL",
                            "time_fired": _jetzt(),
                        },
                    }
                    if not ws.closed:
                        await ws.send_json(nachricht)

    async def _websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "auth_required", "ha_version": "2027.11.0"})
        auth = await ws.receive()
        daten = json.loads(auth.data) if auth.type == WSMsgType.TEXT else {}
        if daten.get("type") != "auth" or daten.get("access_token") != self.TOKEN:
            await ws.send_json({"type": "auth_invalid", "message": "Invalid access token"})
            await ws.close()
            return ws
        await ws.send_json({"type": "auth_ok", "ha_version": "2027.11.0"})
        self.verbindungen_gesamt += 1
        self._abos[ws] = {}
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                befehl = json.loads(msg.data)
                if befehl.get("type") == "subscribe_events":
                    self._abos[ws][befehl["id"]] = befehl.get("event_type")
                    await ws.send_json({"id": befehl["id"], "type": "result", "success": True, "result": None})
                else:
                    await ws.send_json(
                        {
                            "id": befehl.get("id"),
                            "type": "result",
                            "success": False,
                            "error": {"code": "unknown_command", "message": "Unknown command."},
                        }
                    )
        finally:
            self._abos.pop(ws, None)
        return ws


def standard_entitaeten(sim: HaSimulator) -> None:
    for e in ("light.feld_1", "light.feld_2"):
        sim.entitaet(e, "off")
    for e in ("binary_sensor.praesenz_feld_1", "binary_sensor.praesenz_feld_2", "binary_sensor.tuer"):
        sim.entitaet(e, "off")
    sim.entitaet("climate.halle", "heat", temperature=0.0, current_temperature=5.0)
    sim.entitaet("lock.eingang", "locked")
    sim.entitaet("input_boolean.beachhub_handbetrieb", "off")


if __name__ == "__main__":
    simulator = HaSimulator()
    standard_entitaeten(simulator)
    print(f"HA-Simulator auf http://127.0.0.1:8123, Token: {HaSimulator.TOKEN}")  # noqa: T201
    web.run_app(simulator.app, host="127.0.0.1", port=8123)
```

In `hall/tests/conftest.py` ergänzen (Importe oben, Fixture unten):
```python
from collections.abc import AsyncIterator

from tests.ha_simulator import HaSimulator, standard_entitaeten


@pytest.fixture
async def ha() -> AsyncIterator[HaSimulator]:
    sim = HaSimulator()
    standard_entitaeten(sim)
    await sim.start()
    yield sim
    await sim.stop()
```

- [ ] **Step 2: Failing Tests schreiben**

`hall/tests/test_ha.py`:
```python
import asyncio
from collections.abc import AsyncIterator

import pytest
from beachhub_hall.ha import HaClient, HaFehler

from tests.ha_simulator import HaSimulator


@pytest.fixture
async def client(ha: HaSimulator) -> AsyncIterator[HaClient]:
    c = HaClient(ha.url, HaSimulator.TOKEN)
    yield c
    await c.schliesse()


async def test_rest_lesen_schreiben_schalten(ha: HaSimulator, client: HaClient) -> None:
    z = await client.zustand("light.feld_1")
    assert z is not None and z["state"] == "off"
    assert await client.zustand("light.gibt_es_nicht") is None
    assert {s["entity_id"] for s in await client.zustaende()} >= {"light.feld_1", "climate.halle"}
    await client.dienst("light", "turn_on", {"entity_id": "light.feld_1"})
    assert ha.zustaende["light.feld_1"]["state"] == "on"
    assert ha.aufrufe[-1] == ("light", "turn_on", {"entity_id": "light.feld_1"})
    await client.dienst("climate", "set_temperature", {"entity_id": "climate.halle", "temperature": 18.0})
    assert ha.zustaende["climate.halle"]["attributes"]["temperature"] == 18.0
    await client.setze_zustand("sensor.beachhub_planversion", "3", {"friendly_name": "Planversion"})
    assert ha.geschrieben["sensor.beachhub_planversion"]["state"] == "3"


async def test_fehler_werden_zu_hafehler(ha: HaSimulator) -> None:
    falsch = HaClient(ha.url, "falsches-token")
    with pytest.raises(HaFehler):
        await falsch.zustand("light.feld_1")
    await falsch.schliesse()
    ha.fehler_bei_diensten = True
    c = HaClient(ha.url, HaSimulator.TOKEN)
    with pytest.raises(HaFehler):
        await c.dienst("light", "turn_on", {"entity_id": "light.feld_1"})
    await c.schliesse()
    weg = HaClient("http://127.0.0.1:9", HaSimulator.TOKEN)
    with pytest.raises(HaFehler):
        await weg.zustand("light.feld_1")
    with pytest.raises(HaFehler):
        await weg.websocket()
    await weg.schliesse()


async def test_websocket_abonnieren_und_empfangen(ha: HaSimulator, client: HaClient) -> None:
    ws = await client.websocket()
    await ws.abonniere("state_changed")
    await ws.abonniere("esphome.beachhub_pin")
    assert ha.abonnements() == 2
    await ha.setze("binary_sensor.praesenz_feld_1", "on")
    event = await asyncio.wait_for(ws.naechstes(), timeout=2)
    assert event["event_type"] == "state_changed"
    assert event["data"]["entity_id"] == "binary_sensor.praesenz_feld_1"
    assert event["data"]["new_state"]["state"] == "on"
    await ha.feuere("esphome.beachhub_pin", {"code": "482913"})
    event = await asyncio.wait_for(ws.naechstes(), timeout=2)
    assert event["event_type"] == "esphome.beachhub_pin"
    assert event["data"] == {"code": "482913"}
    await ws.schliesse()


async def test_websocket_falsches_token_und_trennung(ha: HaSimulator) -> None:
    falsch = HaClient(ha.url, "falsches-token")
    with pytest.raises(HaFehler, match="Anmeldung"):
        await falsch.websocket()
    await falsch.schliesse()
    c = HaClient(ha.url, HaSimulator.TOKEN)
    ws = await c.websocket()
    await ws.abonniere("state_changed")
    await ha.trenne_alle()
    with pytest.raises(HaFehler):
        await asyncio.wait_for(ws.naechstes(), timeout=2)
    await c.schliesse()
```

- [ ] **Step 3: Fehlschlag prüfen**

Run: `cd hall && pytest -q tests/test_ha.py`
Expected: FAIL mit `ModuleNotFoundError: No module named 'beachhub_hall.ha'`

- [ ] **Step 4: Implementieren**

`hall/beachhub_hall/ha.py`:
```python
"""Home Assistant über REST und WebSocket mit Long-Lived Access Token – keine Custom
Component (Hauptspec § 8.2).

Jeder Fehler wird zu `HaFehler`. Die Aufrufer unterscheiden nur „HA hat geantwortet“ und
„HA hat nicht geantwortet“; was daraus folgt (warten, melden), entscheiden sie selbst.
"""

import json
from typing import Any

import aiohttp


class HaFehler(Exception):  # noqa: N818
    pass


class HaWebSocket:
    def __init__(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        self._ws = ws
        self._id = 0

    async def _lies(self) -> dict[str, Any]:
        try:
            msg = await self._ws.receive()
        except (aiohttp.ClientError, TimeoutError) as e:
            raise HaFehler(f"WebSocket: {e}") from e
        if msg.type != aiohttp.WSMsgType.TEXT:
            raise HaFehler(f"WebSocket beendet ({msg.type.name})")
        daten = json.loads(msg.data)
        if not isinstance(daten, dict):
            raise HaFehler("WebSocket: unerwartete Nachricht")
        return daten

    async def _sende(self, nachricht: dict[str, Any]) -> None:
        try:
            await self._ws.send_json(nachricht)
        except (aiohttp.ClientError, ConnectionError) as e:
            raise HaFehler(f"WebSocket: {e}") from e

    async def anmelden(self, token: str) -> None:
        if (await self._lies()).get("type") != "auth_required":
            raise HaFehler("WebSocket: kein auth_required")
        await self._sende({"type": "auth", "access_token": token})
        if (await self._lies()).get("type") != "auth_ok":
            raise HaFehler("HA-Anmeldung abgelehnt – Token prüfen")

    async def abonniere(self, event_type: str) -> None:
        self._id += 1
        await self._sende({"id": self._id, "type": "subscribe_events", "event_type": event_type})
        while True:
            antwort = await self._lies()
            if antwort.get("type") == "result" and antwort.get("id") == self._id:
                if not antwort.get("success"):
                    raise HaFehler(f"Abonnement {event_type} abgelehnt")
                return

    async def naechstes(self) -> dict[str, Any]:
        while True:
            nachricht = await self._lies()
            if nachricht.get("type") == "event" and isinstance(nachricht.get("event"), dict):
                event: dict[str, Any] = nachricht["event"]
                return event

    async def schliesse(self) -> None:
        await self._ws.close()


class HaClient:
    def __init__(self, basis_url: str, token: str) -> None:
        self._basis = basis_url.rstrip("/")
        self._token = token
        self._session: aiohttp.ClientSession | None = None

    def _s(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=aiohttp.ClientTimeout(total=15),
            )
        return self._session

    async def _anfrage(self, methode: str, pfad: str, daten: dict[str, Any] | None = None) -> Any:
        try:
            async with self._s().request(methode, self._basis + pfad, json=daten) as r:
                if r.status == 404:
                    return None
                if r.status >= 400:
                    raise HaFehler(f"HA {methode} {pfad}: HTTP {r.status}")
                return await r.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as e:
            raise HaFehler(f"HA {methode} {pfad}: {e}") from e

    async def zustand(self, entity_id: str) -> dict[str, Any] | None:
        z = await self._anfrage("GET", f"/api/states/{entity_id}")
        return z if isinstance(z, dict) else None

    async def zustaende(self) -> list[dict[str, Any]]:
        z = await self._anfrage("GET", "/api/states")
        return [s for s in z if isinstance(s, dict)] if isinstance(z, list) else []

    async def dienst(self, domain: str, service: str, daten: dict[str, Any]) -> None:
        await self._anfrage("POST", f"/api/services/{domain}/{service}", daten)

    async def setze_zustand(self, entity_id: str, state: str, attribute: dict[str, Any]) -> None:
        await self._anfrage("POST", f"/api/states/{entity_id}", {"state": state, "attributes": attribute})

    async def websocket(self) -> HaWebSocket:
        try:
            ws = await self._s().ws_connect(self._basis + "/api/websocket", heartbeat=30)
        except (aiohttp.ClientError, TimeoutError) as e:
            raise HaFehler(f"HA-WebSocket: {e}") from e
        verbindung = HaWebSocket(ws)
        try:
            await verbindung.anmelden(self._token)
        except HaFehler:
            await verbindung.schliesse()
            raise
        return verbindung

    async def schliesse(self) -> None:
        if self._session is not None:
            await self._session.close()
```

- [ ] **Step 5: Tests laufen lassen**

Run: `cd hall && pytest -q tests/test_ha.py && cd .. && ruff check hall && ruff format --check hall && mypy`
Expected: 4 passed, keine Lint- oder Typfehler. Meldet ruff `T201` nicht (Regel nicht aktiv), ist das `noqa` überflüssig und darf wegfallen, falls `ruff check` es als `RUF100` beanstandet.

- [ ] **Step 6: Commit**

```bash
git add hall/beachhub_hall/ha.py hall/tests/ha_simulator.py hall/tests/conftest.py hall/tests/test_ha.py
git commit -m "feat(hall): HA-Client für REST und WebSocket, HA-Simulator für Tests"
```

---

## Task 8: Tür und PIN-Prüfung am Tastenfeld

**Files:**
- Create: `hall/beachhub_hall/tuer.py`, `hall/beachhub_hall/pin.py`
- Test: `hall/tests/test_pin.py`

**Interfaces:**
- Consumes: `HaClient`, `HaFehler` (Task 7), `Ereignisse` (Task 4), `plan.lade`, `plan.buchungen_mit_pin` (Task 4), `db.lies/schreibe` (Task 2), `config.TuerKonfig`, `Zuordnung` (Task 2), `pin_hash` (Task 1).
- Produces `beachhub_hall.tuer.Tuer(ha, konfig: TuerKonfig, ereignisse, schlafen=asyncio.sleep)` mit `async oeffne() -> bool` und `async warte() -> None` (wartet auf laufende Impulse, nur für Tests). Bei `lock.*` wird `lock.unlock` aufgerufen, bei `switch.*` `turn_on` und nach `impuls_sekunden` `turn_off`. Fehler führen zu `aktor_fehler`.
- Produces `beachhub_hall.pin`:
  - Konstanten `SERIE_SCHWELLE = 5`, `SERIE_ENDE = timedelta(minutes=15)`
  - `ist_master(klar: str, master_hash: str) -> bool`
  - `PinPruefer(sitzungen, uhr, zuordnung, ereignisse, tuer, schlafen=asyncio.sleep)` mit `async eingabe(code: str) -> bool`
  - Zustandsschlüssel: `zustand["fehlserie"] = {"anzahl": int, "letzte": iso | None, "gemeldet": bool}`, `zustand["letzter_master"] = iso`

- [ ] **Step 1: Failing Tests schreiben**

`hall/tests/test_pin.py`:
```python
import asyncio
import json
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from beachhub_hall import plan
from beachhub_hall.clock import SimulierteUhr
from beachhub_hall.config import TastenfeldKonfig, TuerKonfig, Zuordnung
from beachhub_hall.db import EreignisZeile, lies, schreibe
from beachhub_hall.ereignisse import Ereignisse
from beachhub_hall.ha import HaClient
from beachhub_hall.pin import PinPruefer, ist_master
from beachhub_hall.tuer import Tuer
from beachhub_shared.hallenplan import PlanBuchung
from beachhub_shared.signatur import erzeuge_schluesselpaar
from sqlalchemy.orm import Session, sessionmaker

from tests.ha_simulator import HaSimulator
from tests.hilfen import F1, MASTER_HASH, MASTER_PIN, FakeSchlaf, baue_plan, buchung, signiertes_dokument, t

PRIV, _ = erzeuge_schluesselpaar()
PIN = "482913"


class Aufbau:
    def __init__(self, sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator, tuer: TuerKonfig) -> None:
        self.sitzungen, self.uhr, self.sim = sitzungen, uhr, ha
        self.schlaf = FakeSchlaf()
        self.client = HaClient(ha.url, HaSimulator.TOKEN)
        self.ereignisse = Ereignisse(sitzungen, uhr)
        self.tuer = Tuer(self.client, tuer, self.ereignisse, self.schlaf)
        z = Zuordnung(master_pin_hash=MASTER_HASH, tuer=tuer, tastenfeld=TastenfeldKonfig(verzoegerung_sekunden=3))
        self.pruefer = PinPruefer(sitzungen, uhr, z, self.ereignisse, self.tuer, self.schlaf)

    def plan(self, *buchungen: PlanBuchung) -> None:
        inhalt = baue_plan(buchungen)
        with self.sitzungen() as db:
            plan.speichere(db, signiertes_dokument(inhalt, plan.version(db) + 1, PRIV), inhalt, t(0))

    def typen(self) -> list[str]:
        return [e.typ for e in self.ereignisse.unbestaetigt(1000)]

    def geoeffnet(self) -> int:
        return sum(1 for a in self.sim.aufrufe if a[:2] in (("lock", "unlock"), ("switch", "turn_on")))


@pytest.fixture
async def a(sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator) -> AsyncIterator[Aufbau]:
    aufbau = Aufbau(sitzungen, uhr, ha, TuerKonfig("lock.eingang"))
    yield aufbau
    await aufbau.client.schliesse()


async def test_pin_oeffnet_im_zutrittsfenster(a: Aufbau) -> None:
    b = buchung(F1, t(19), t(21), pin=PIN)
    a.plan(b)
    a.uhr.stelle(t(18, 44))
    assert await a.pruefer.eingabe(PIN) is False
    a.uhr.stelle(t(18, 45))
    assert await a.pruefer.eingabe(PIN) is True
    assert a.sim.zustaende["lock.eingang"]["state"] == "unlocked"
    a.uhr.stelle(t(21))
    assert await a.pruefer.eingabe(PIN) is False
    akzeptiert = [e for e in a.ereignisse.unbestaetigt() if e.typ == "pin_akzeptiert"]
    assert len(akzeptiert) == 1
    assert akzeptiert[0].buchung_id == b.buchung_id and akzeptiert[0].feld_id == F1


async def test_pin_wird_nie_gespeichert(a: Aufbau) -> None:
    a.plan(buchung(F1, t(19), t(21), pin=PIN))
    a.uhr.stelle(t(19))
    await a.pruefer.eingabe(PIN)
    await a.pruefer.eingabe("135790")
    with a.sitzungen() as db:
        alles = json.dumps([(z.typ, z.daten_json) for z in db.query(EreignisZeile)])
    assert PIN not in alles and "135790" not in alles


async def test_code_mit_leerzeichen_wird_akzeptiert(a: Aufbau) -> None:
    a.plan(buchung(F1, t(19), t(21), pin=PIN))
    a.uhr.stelle(t(19))
    assert await a.pruefer.eingabe(f" {PIN} ") is True


async def test_master_oeffnet_immer(a: Aufbau) -> None:
    assert ist_master(MASTER_PIN, MASTER_HASH) and not ist_master("1234", MASTER_HASH)
    assert not ist_master(MASTER_PIN, "$argon2id$kaputt")
    assert await a.pruefer.eingabe(MASTER_PIN) is True  # ganz ohne Plan
    e = [x for x in a.ereignisse.unbestaetigt() if x.typ == "pin_akzeptiert"][0]
    assert e.daten == {"master": True} and e.buchung_id is None
    with a.sitzungen() as db:
        assert lies(db, "letzter_master") == a.uhr.jetzt().isoformat()


async def test_handbetrieb_aendert_nichts_am_zutritt(a: Aufbau) -> None:
    a.plan(buchung(F1, t(19), t(21), pin=PIN))
    with a.sitzungen() as db:
        schreibe(db, "handbetrieb", True)
        db.commit()
    a.uhr.stelle(t(19))
    assert await a.pruefer.eingabe(PIN) is True


async def test_abgelaufener_plan_nur_master(a: Aufbau) -> None:
    a.plan(buchung(F1, t(19), t(21), pin=PIN))
    a.uhr.stelle(t(19) + timedelta(days=8))
    assert await a.pruefer.eingabe(PIN) is False
    assert await a.pruefer.eingabe(MASTER_PIN) is True


async def test_ungueltige_eingaben_sind_fehlversuche(a: Aufbau) -> None:
    for code in ("", "12", "abcdef", "1234567890123", "12 34"):
        assert await a.pruefer.eingabe(code) is False
    assert a.typen().count("pin_abgelehnt") == 5
    assert a.geoeffnet() == 0


async def test_fehlversuchsserie_meldet_einmal_und_verzoegert(a: Aufbau) -> None:
    a.plan(buchung(F1, t(19), t(21), pin=PIN))
    a.uhr.stelle(t(19))
    for _ in range(5):
        assert await a.pruefer.eingabe("111111") is False
    assert a.typen().count("tastenfeld_fehlversuche") == 1
    assert a.schlaf.aufrufe == []  # die ersten fünf Eingaben werden sofort geprüft
    assert await a.pruefer.eingabe("111111") is False
    assert a.schlaf.aufrufe == [3.0]
    assert a.typen().count("tastenfeld_fehlversuche") == 1
    # Das Tastenfeld bleibt aktiv: die richtige PIN öffnet trotzdem (nur verzögert) ...
    assert await a.pruefer.eingabe(PIN) is True
    assert a.schlaf.aufrufe == [3.0, 3.0]
    # ... und beendet die Serie.
    assert await a.pruefer.eingabe("111111") is False
    assert a.schlaf.aufrufe == [3.0, 3.0]


async def test_serie_endet_nach_15_minuten_ruhe(a: Aufbau) -> None:
    for _ in range(5):
        await a.pruefer.eingabe("111111")
    a.uhr.vor(minutes=15)
    await a.pruefer.eingabe("111111")
    assert a.schlaf.aufrufe == []
    for _ in range(4):
        await a.pruefer.eingabe("111111")
    assert a.typen().count("tastenfeld_fehlversuche") == 2


async def test_gleichzeitige_fehleingaben_zaehlen_beide(a: Aufbau) -> None:
    await asyncio.gather(a.pruefer.eingabe("111111"), a.pruefer.eingabe("222222"))
    with a.sitzungen() as db:
        assert lies(db, "fehlserie")["anzahl"] == 2


async def test_tuer_als_switch_mit_impuls(sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator) -> None:
    ha.entitaet("switch.tueroeffner", "off")
    aufbau = Aufbau(sitzungen, uhr, ha, TuerKonfig("switch.tueroeffner", impuls_sekunden=5))
    assert await aufbau.tuer.oeffne() is True
    await aufbau.tuer.warte()
    assert [x[:2] for x in ha.aufrufe] == [("switch", "turn_on"), ("switch", "turn_off")]
    assert aufbau.schlaf.aufrufe == [5.0]
    await aufbau.client.schliesse()


async def test_tuer_fehler_werden_gemeldet(sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator) -> None:
    ohne = Aufbau(sitzungen, uhr, ha, TuerKonfig(None))
    assert await ohne.tuer.oeffne() is False
    ha.fehler_bei_diensten = True
    mit = Aufbau(sitzungen, uhr, ha, TuerKonfig("lock.eingang"))
    assert await mit.tuer.oeffne() is False
    fehler = [e for e in mit.ereignisse.unbestaetigt() if e.typ == "aktor_fehler"]
    assert [f.daten["entity"] for f in fehler] == ["tuer", "lock.eingang"]
    await ohne.client.schliesse()
    await mit.client.schliesse()
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd hall && pytest -q tests/test_pin.py`
Expected: FAIL mit `ModuleNotFoundError: No module named 'beachhub_hall.pin'`

- [ ] **Step 3: Implementieren**

`hall/beachhub_hall/tuer.py`:
```python
"""Türöffner: lock.* wird entriegelt (das Schloss bzw. eine HA-Automation verriegelt wieder),
switch.* bekommt einen Impuls von `impuls_sekunden`."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from beachhub_hall.config import TuerKonfig
from beachhub_hall.ereignisse import Ereignisse
from beachhub_hall.ha import HaClient, HaFehler

logger = logging.getLogger(__name__)


class Tuer:
    def __init__(
        self,
        ha: HaClient,
        konfig: TuerKonfig,
        ereignisse: Ereignisse,
        schlafen: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._ha = ha
        self._k = konfig
        self._ereignisse = ereignisse
        self._schlafen = schlafen
        self._impulse: set[asyncio.Task[None]] = set()

    async def oeffne(self) -> bool:
        entity = self._k.entity
        if not entity:
            self._ereignisse.melde("aktor_fehler", entity="tuer", grund="nicht_zugeordnet")
            return False
        try:
            if entity.startswith("lock."):
                await self._ha.dienst("lock", "unlock", {"entity_id": entity})
            else:
                await self._ha.dienst("switch", "turn_on", {"entity_id": entity})
                aufgabe = asyncio.create_task(self._impuls_ende(entity))
                self._impulse.add(aufgabe)
                aufgabe.add_done_callback(self._impulse.discard)
        except HaFehler as e:
            logger.error("Tür lässt sich nicht öffnen: %s", e)
            self._ereignisse.melde("aktor_fehler", entity=entity, grund="tuer_oeffnen_fehlgeschlagen")
            return False
        return True

    async def _impuls_ende(self, entity: str) -> None:
        await self._schlafen(self._k.impuls_sekunden)
        try:
            await self._ha.dienst("switch", "turn_off", {"entity_id": entity})
        except HaFehler:
            self._ereignisse.melde("aktor_fehler", entity=entity, grund="tuer_impuls_nicht_beendet")

    async def warte(self) -> None:
        if self._impulse:
            await asyncio.gather(*list(self._impulse))
```

`hall/beachhub_hall/pin.py`:
```python
"""PIN-Prüfung am Tastenfeld (A-HALLE-3).

Reihenfolge: Serie prüfen (ab 5 Fehlversuchen wird die Annahme um wenige Sekunden verzögert,
aber nie gesperrt), Master-PIN, dann PIN einer Buchung im Zutrittsfenster. Die Eingabe selbst
wird nie gespeichert oder gemeldet. Das Hashen läuft in einem Thread, damit der Dienst
währenddessen weiterarbeitet.
"""

import asyncio
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from beachhub_shared.hallenplan import PlanBuchung, pin_hash
from sqlalchemy.orm import Session, sessionmaker

from beachhub_hall import plan
from beachhub_hall.clock import Uhr
from beachhub_hall.config import Zuordnung
from beachhub_hall.db import lies, schreibe
from beachhub_hall.ereignisse import Ereignisse
from beachhub_hall.tuer import Tuer

SERIE_SCHWELLE = 5
SERIE_ENDE = timedelta(minutes=15)
_ZIFFERN = re.compile(r"\d{4,12}")
_ph = PasswordHasher()
MASTER = "master"


def ist_master(klar: str, master_hash: str) -> bool:
    try:
        return _ph.verify(master_hash, klar)
    except (VerificationError, InvalidHashError):
        return False


def _leere_serie() -> dict[str, Any]:
    return {"anzahl": 0, "letzte": None, "gemeldet": False}


class PinPruefer:
    def __init__(
        self,
        sitzungen: sessionmaker[Session],
        uhr: Uhr,
        zuordnung: Zuordnung,
        ereignisse: Ereignisse,
        tuer: Tuer,
        schlafen: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._sitzungen = sitzungen
        self._uhr = uhr
        self._z = zuordnung
        self._ereignisse = ereignisse
        self._tuer = tuer
        self._schlafen = schlafen

    def _serie(self, db: Session, jetzt: datetime) -> dict[str, Any]:
        serie: dict[str, Any] = lies(db, "fehlserie") or _leere_serie()
        letzte = serie.get("letzte")
        if letzte and jetzt - datetime.fromisoformat(letzte) >= SERIE_ENDE:
            return _leere_serie()
        return serie

    async def eingabe(self, code: str) -> bool:
        code = code.strip()
        with self._sitzungen() as db:
            serie = self._serie(db, self._uhr.jetzt())
        if serie["anzahl"] >= SERIE_SCHWELLE:
            await self._schlafen(self._z.tastenfeld.verzoegerung_sekunden)
        jetzt = self._uhr.jetzt()
        treffer = await self._pruefe(code, jetzt)
        if treffer is None:
            self._fehlversuch(jetzt)
            return False
        with self._sitzungen() as db:
            schreibe(db, "fehlserie", _leere_serie())
            if treffer == MASTER:
                schreibe(db, "letzter_master", jetzt.isoformat())
            db.commit()
        if isinstance(treffer, PlanBuchung):
            self._ereignisse.melde(
                "pin_akzeptiert", feld_id=treffer.feld_id, buchung_id=treffer.buchung_id
            )
        else:
            self._ereignisse.melde("pin_akzeptiert", master=True)
        await self._tuer.oeffne()
        return True

    async def _pruefe(self, code: str, jetzt: datetime) -> PlanBuchung | str | None:
        if not _ZIFFERN.fullmatch(code):
            return None
        if await asyncio.to_thread(ist_master, code, self._z.master_pin_hash):
            return MASTER
        with self._sitzungen() as db:
            gespeichert = plan.lade(db)
        if gespeichert is None or jetzt >= gespeichert.inhalt.gueltig_bis:
            return None
        h = await asyncio.to_thread(pin_hash, code, gespeichert.inhalt.pin)
        vorlauf = timedelta(minutes=gespeichert.inhalt.konfig.zutritt_vorlauf_minuten)
        with self._sitzungen() as db:
            kandidaten = plan.buchungen_mit_pin(db, h)
        return next((b for b in kandidaten if b.beginn - vorlauf <= jetzt < b.ende), None)

    def _fehlversuch(self, jetzt: datetime) -> None:
        # Serie neu lesen: Während des Hashens kann eine zweite Eingabe gezählt worden sein.
        with self._sitzungen() as db:
            serie = self._serie(db, jetzt)
            serie["anzahl"] += 1
            serie["letzte"] = jetzt.isoformat()
            melden = serie["anzahl"] >= SERIE_SCHWELLE and not serie["gemeldet"]
            if melden:
                serie["gemeldet"] = True
            schreibe(db, "fehlserie", serie)
            db.commit()
        self._ereignisse.melde("pin_abgelehnt", fehlversuche=serie["anzahl"])
        if melden:
            self._ereignisse.melde("tastenfeld_fehlversuche", anzahl=serie["anzahl"])
```

Warum die Ereignisse erst nach `commit()` gemeldet werden: `melde()` öffnet eine eigene Sitzung. Eine offene Schreibtransaktion würde die SQLite-Datei sperren.

- [ ] **Step 4: Tests laufen lassen**

Run: `cd hall && pytest -q tests/test_pin.py && cd .. && ruff check hall && ruff format --check hall && mypy`
Expected: 12 passed, keine Lint- oder Typfehler

- [ ] **Step 5: Commit**

```bash
git add hall/beachhub_hall/tuer.py hall/beachhub_hall/pin.py hall/tests/test_pin.py
git commit -m "feat(hall): PIN-Prüfung mit Master-PIN, Fehlversuchsserie ohne Sperre, Türöffner"
```

---
## Task 9: Steuerung – Soll gegen Ist, Aktorfehler, Präsenzalarm

**Files:**
- Create: `hall/beachhub_hall/aufgaben/steuerung.py`
- Test: `hall/tests/test_steuerung.py`

**Interfaces:**
- Consumes: `sollzustand`, `laufende_buchung` (Task 3), `plan.lade` (Task 4), `Ereignisse` (Task 4), `HaClient`, `HaFehler` (Task 7), `Lage` (Task 6), `Zuordnung` (Task 2), `db.lies/schreibe` (Task 2).
- Produces `beachhub_hall.aufgaben.steuerung.Steuerung(sitzungen, uhr, zuordnung, ha, ereignisse, lage)`:
  - Attribut `wecker: asyncio.Event`
  - `async einmal() -> None`: Präsenzalarm prüfen, Sollzustand berechnen, `lage.soll_heizung` setzen, Licht und Heizung nur bei Abweichung schalten
  - Ereignisse: `licht_geschaltet` (feld_id, `entity`, `an`), `heizung_gesetzt` (`soll`, `ist_temperatur`), `aktor_fehler` (`entity`, `grund` ∈ {`dienst_fehlgeschlagen`, `zustand_weicht_ab`}, einmal je Entität und Störung), `praesenz_ohne_buchung` (feld_id, `minuten`)
  - Liest `zustand["praesenz"]` im Format `{feld_id: {"seit": iso, "ohne_buchung_seit": iso | None, "alarm": bool}}`. Den Eintrag legt der HA-Zuhörer an (Task 10), die Steuerung pflegt `ohne_buchung_seit` und `alarm`.
- Regel „Aktorfehler“: Jeder Durchlauf, in dem Soll und Ist abweichen, ist ein Versuch. Schlägt der dritte Dienstaufruf fehl oder weicht der Zustand vor dem vierten Versuch noch ab, entsteht einmal `aktor_fehler`. Stimmen Soll und Ist wieder überein, endet die Störung. `licht_geschaltet` und `heizung_gesetzt` werden nur für die ersten drei Versuche gemeldet.

- [ ] **Step 1: Failing Tests schreiben**

`hall/tests/test_steuerung.py`:
```python
from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal

import pytest
from beachhub_hall import plan
from beachhub_hall.aufgaben.steuerung import Steuerung
from beachhub_hall.clock import SimulierteUhr
from beachhub_hall.config import FeldZuordnung, HeizungKonfig, Zuordnung
from beachhub_hall.db import lies, schreibe
from beachhub_hall.ereignisse import Ereignisse
from beachhub_hall.ha import HaClient
from beachhub_hall.lage import Lage
from beachhub_shared.hallenplan import PlanBuchung, PlanSperre
from beachhub_shared.signatur import erzeuge_schluesselpaar
from sqlalchemy.orm import Session, sessionmaker

from tests.ha_simulator import HaSimulator
from tests.hilfen import F1, F2, MASTER_HASH, baue_plan, buchung, signiertes_dokument, t

PRIV, _ = erzeuge_schluesselpaar()

ZUORDNUNG = Zuordnung(
    master_pin_hash=MASTER_HASH,
    felder={
        F1: FeldZuordnung("light.feld_1", "binary_sensor.praesenz_feld_1"),
        F2: FeldZuordnung("light.feld_2", "binary_sensor.praesenz_feld_2"),
    },
    heizung=HeizungKonfig("climate.halle"),
)


class Aufbau:
    def __init__(self, sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator, zuordnung: Zuordnung = ZUORDNUNG, url: str | None = None) -> None:
        self.sitzungen, self.uhr, self.sim = sitzungen, uhr, ha
        self.client = HaClient(url or ha.url, HaSimulator.TOKEN)
        self.ereignisse = Ereignisse(sitzungen, uhr)
        self.lage = Lage()
        self.steuerung = Steuerung(sitzungen, uhr, zuordnung, self.client, self.ereignisse, self.lage)

    def plan(self, *buchungen: PlanBuchung, sperren: tuple[PlanSperre, ...] = ()) -> None:
        inhalt = baue_plan(buchungen, sperren=sperren)
        with self.sitzungen() as db:
            plan.speichere(db, signiertes_dokument(inhalt, plan.version(db) + 1, PRIV), inhalt, t(0))

    def ereignis(self, typ: str) -> list[dict[str, object]]:
        return [
            {"feld_id": e.feld_id, **e.daten} for e in self.ereignisse.unbestaetigt(1000) if e.typ == typ
        ]

    def dienste(self) -> list[tuple[str, str]]:
        return [a[:2] for a in self.sim.aufrufe]

    async def um(self, stunde: int, minute: int = 0) -> None:
        self.uhr.stelle(t(stunde, minute))
        await self.steuerung.einmal()


@pytest.fixture
async def a(sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator) -> AsyncIterator[Aufbau]:
    aufbau = Aufbau(sitzungen, uhr, ha)
    yield aufbau
    await aufbau.client.schliesse()


async def test_hallenabend_schaltet_heizung_und_licht(a: Aufbau) -> None:
    a.plan(buchung(F2, t(19), t(21)))
    await a.um(18, 29)
    assert a.dienste() == []
    await a.um(18, 30)
    assert a.sim.zustaende["climate.halle"]["attributes"]["temperature"] == 18.0
    assert a.ereignis("heizung_gesetzt") == [{"feld_id": None, "soll": "18.0", "ist_temperatur": "5.0"}]
    assert a.lage.soll_heizung == Decimal("18.0")
    await a.um(18, 55)
    assert a.sim.zustaende["light.feld_2"]["state"] == "on"
    assert a.sim.zustaende["light.feld_1"]["state"] == "off"
    assert a.ereignis("licht_geschaltet") == [{"feld_id": F2, "entity": "light.feld_2", "an": True}]
    anzahl = len(a.sim.aufrufe)
    await a.um(19, 30)
    assert len(a.sim.aufrufe) == anzahl  # nichts zu tun: idempotent
    await a.um(21, 5)
    assert a.sim.zustaende["light.feld_2"]["state"] == "off"
    assert a.sim.zustaende["climate.halle"]["attributes"]["temperature"] == 0.0


async def test_handbetrieb_schaltet_nichts(a: Aufbau) -> None:
    a.plan(buchung(F1, t(19), t(21)))
    with a.sitzungen() as db:
        schreibe(db, "handbetrieb", True)
        db.commit()
    await a.um(19)
    assert a.dienste() == []


async def test_ohne_plan_licht_aus_heizung_unangetastet(a: Aufbau) -> None:
    await a.sim.setze("light.feld_1", "on")
    await a.um(19)
    assert a.dienste() == [("light", "turn_off")]


async def test_abgelaufener_plan_grundzustand(a: Aufbau) -> None:
    a.plan(buchung(F1, t(19), t(21)))
    await a.sim.setze("climate.halle", "heat", temperature=18.0)
    await a.sim.setze("light.feld_1", "on")
    a.uhr.stelle(t(19) + timedelta(days=8))
    await a.steuerung.einmal()
    assert a.sim.zustaende["light.feld_1"]["state"] == "off"
    assert a.sim.zustaende["climate.halle"]["attributes"]["temperature"] == 0.0


async def test_sperre_schaltet_nichts(a: Aufbau) -> None:
    a.plan(sperren=(PlanSperre(feld_id=None, beginn=t(18), ende=t(23)),))
    await a.um(19)
    assert a.dienste() == []


async def test_dienstfehler_ergibt_einmal_aktorfehler(a: Aufbau) -> None:
    a.plan(buchung(F1, t(19), t(21)))
    a.sim.fehler_bei_diensten = True
    for minute in (0, 1, 2, 3, 4):
        await a.um(19, minute)
    fehler = a.ereignis("aktor_fehler")
    assert len(fehler) == 2  # Licht Feld 1 und Heizung, je einmal
    assert {f["entity"] for f in fehler} == {"light.feld_1", "climate.halle"}
    assert {f["grund"] for f in fehler} == {"dienst_fehlgeschlagen"}
    assert a.ereignis("licht_geschaltet") == []
    a.sim.fehler_bei_diensten = False
    await a.um(19, 5)
    assert a.sim.zustaende["light.feld_1"]["state"] == "on"


async def test_unbekannte_entitaet_weicht_dauerhaft_ab(sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator) -> None:
    z = Zuordnung(master_pin_hash=MASTER_HASH, felder={F1: FeldZuordnung("light.gibt_es_nicht")})
    aufbau = Aufbau(sitzungen, uhr, ha, z)
    aufbau.plan(buchung(F1, t(19), t(21)))
    for minute in range(6):
        await aufbau.um(19, minute)
    assert len(aufbau.ereignis("licht_geschaltet")) == 3
    fehler = aufbau.ereignis("aktor_fehler")
    assert fehler == [{"feld_id": F1, "entity": "light.gibt_es_nicht", "grund": "zustand_weicht_ab"}]
    await aufbau.client.schliesse()


async def test_ha_nicht_erreichbar_bricht_still_ab(sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator) -> None:
    aufbau = Aufbau(sitzungen, uhr, ha, url="http://127.0.0.1:9")
    aufbau.plan(buchung(F1, t(19), t(21)))
    await aufbau.um(19)
    assert aufbau.ereignisse.unbestaetigt() == []
    await aufbau.client.schliesse()


async def test_praesenz_ohne_buchung_meldet_einmal(a: Aufbau) -> None:
    a.plan(buchung(F1, t(19), t(21)))
    with a.sitzungen() as db:
        schreibe(db, "praesenz", {F2: {"seit": t(17).isoformat(), "ohne_buchung_seit": t(17).isoformat(), "alarm": False}})
        db.commit()
    await a.um(17, 9)
    assert a.ereignis("praesenz_ohne_buchung") == []
    await a.um(17, 10)
    assert a.ereignis("praesenz_ohne_buchung") == [{"feld_id": F2, "minuten": 10}]
    await a.um(17, 30)
    assert len(a.ereignis("praesenz_ohne_buchung")) == 1


async def test_praesenz_waehrend_buchung_ist_kein_alarm(a: Aufbau) -> None:
    a.plan(buchung(F1, t(19), t(21)))
    with a.sitzungen() as db:
        schreibe(db, "praesenz", {F1: {"seit": t(18, 50).isoformat(), "ohne_buchung_seit": t(18, 50).isoformat(), "alarm": False}})
        db.commit()
    await a.um(19)
    with a.sitzungen() as db:
        assert lies(db, "praesenz")[F1]["ohne_buchung_seit"] is None
    await a.um(20, 30)
    assert a.ereignis("praesenz_ohne_buchung") == []
    await a.um(21)  # Buchung vorbei: ab jetzt zählt die Zeit ohne Buchung
    await a.um(21, 9)
    assert a.ereignis("praesenz_ohne_buchung") == []
    await a.um(21, 10)
    assert len(a.ereignis("praesenz_ohne_buchung")) == 1
```

Die Zeit ohne Buchung zählt ab dem ersten Steuerungslauf nach Buchungsende. Im Betrieb läuft die Steuerung alle 30 s, der Alarm kommt also höchstens 30 s später als die eingestellte Minutenzahl.

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd hall && pytest -q tests/test_steuerung.py`
Expected: FAIL mit `ModuleNotFoundError: No module named 'beachhub_hall.aufgaben.steuerung'`

- [ ] **Step 3: Implementieren**

`hall/beachhub_hall/aufgaben/steuerung.py`:
```python
"""Steuerung: vergleicht alle 30 s (und bei Anstoß) den Sollzustand mit dem Ist in HA und
schaltet nur bei Abweichung (idempotent, Hauptspec § 8.2).

Ist HA gar nicht erreichbar, bricht der Durchlauf still ab – das meldet der HA-Zuhörer als
`ha_nicht_erreichbar`. Scheitert dagegen ein einzelner Dienstaufruf oder reagiert ein Gerät
nicht, entsteht `aktor_fehler`.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from beachhub_shared.hallenplan import HallenplanInhalt
from sqlalchemy.orm import Session, sessionmaker

from beachhub_hall import plan
from beachhub_hall.clock import Uhr
from beachhub_hall.config import Zuordnung
from beachhub_hall.db import lies, schreibe
from beachhub_hall.ereignisse import Ereignisse
from beachhub_hall.ha import HaClient, HaFehler
from beachhub_hall.lage import Lage
from beachhub_hall.soll import laufende_buchung, sollzustand

logger = logging.getLogger(__name__)
MAX_VERSUCHE = 3
PRAESENZ_ALARM_VORGABE = 10


class Steuerung:
    def __init__(
        self,
        sitzungen: sessionmaker[Session],
        uhr: Uhr,
        zuordnung: Zuordnung,
        ha: HaClient,
        ereignisse: Ereignisse,
        lage: Lage,
    ) -> None:
        self._sitzungen = sitzungen
        self._uhr = uhr
        self._z = zuordnung
        self._ha = ha
        self._ereignisse = ereignisse
        self._lage = lage
        self._versuche: dict[str, int] = {}
        self._gestoert: set[str] = set()
        self.wecker = asyncio.Event()

    async def einmal(self) -> None:
        jetzt = self._uhr.jetzt()
        with self._sitzungen() as db:
            gespeichert = plan.lade(db)
            handbetrieb = bool(lies(db, "handbetrieb", False))
        inhalt = gespeichert.inhalt if gespeichert else None
        self._praesenzalarm(inhalt, jetzt)
        soll = sollzustand(inhalt, self._z.felder.keys(), jetzt, handbetrieb)
        self._lage.soll_heizung = soll.heizung
        if not soll.steuern:
            return
        try:
            for feld_id, an in soll.licht.items():
                entity = self._z.felder[feld_id].licht
                if entity:
                    await self._licht(feld_id, entity, an)
            if soll.heizung is not None and self._z.heizung.entity:
                await self._heizung(self._z.heizung.entity, soll.heizung)
        except HaFehler as e:
            logger.warning("Steuerung übersprungen, HA nicht erreichbar: %s", e)

    async def _ist(self, entity: str) -> dict[str, Any] | None:
        zustand = await self._ha.zustand(entity)  # HaFehler: HA weg → Durchlauf abbrechen
        if zustand is not None:
            self._lage.ist[entity] = zustand
        return zustand

    def _in_ordnung(self, entity: str) -> None:
        self._versuche.pop(entity, None)
        self._gestoert.discard(entity)

    def _stoerung(self, entity: str, grund: str, feld_id: str | None) -> None:
        if entity not in self._gestoert:
            self._gestoert.add(entity)
            self._ereignisse.melde("aktor_fehler", feld_id=feld_id, entity=entity, grund=grund)

    async def _schalte(
        self, entity: str, domain: str, service: str, daten: dict[str, Any], feld_id: str | None
    ) -> bool:
        """Ein Versuch. True, wenn der Dienstaufruf angenommen wurde und gemeldet werden soll."""
        versuch = self._versuche.get(entity, 0) + 1
        self._versuche[entity] = versuch
        if versuch > MAX_VERSUCHE:
            self._stoerung(entity, "zustand_weicht_ab", feld_id)
        try:
            await self._ha.dienst(domain, service, {"entity_id": entity, **daten})
        except HaFehler as e:
            logger.error("%s.%s für %s fehlgeschlagen: %s", domain, service, entity, e)
            if versuch >= MAX_VERSUCHE:
                self._stoerung(entity, "dienst_fehlgeschlagen", feld_id)
            return False
        return versuch <= MAX_VERSUCHE

    async def _licht(self, feld_id: str, entity: str, an: bool) -> None:
        zustand = await self._ist(entity)
        ist_an = zustand is not None and zustand.get("state") == "on"
        if ist_an == an:
            self._in_ordnung(entity)
            return
        if await self._schalte(entity, "light", "turn_on" if an else "turn_off", {}, feld_id):
            self._ereignisse.melde("licht_geschaltet", feld_id=feld_id, entity=entity, an=an)

    async def _heizung(self, entity: str, soll: Decimal) -> None:
        zustand = await self._ist(entity)
        attribute = (zustand or {}).get("attributes") or {}
        eingestellt = attribute.get("temperature")
        if eingestellt is not None and Decimal(str(eingestellt)) == soll:
            self._in_ordnung(entity)
            return
        if await self._schalte(entity, "climate", "set_temperature", {"temperature": float(soll)}, None):
            if self._z.heizung.ist_sensor:
                await self._ist(self._z.heizung.ist_sensor)
            ist = self._lage.temperatur(self._z.heizung)
            self._ereignisse.melde(
                "heizung_gesetzt", soll=str(soll), ist_temperatur=str(ist) if ist is not None else None
            )

    def _praesenzalarm(self, inhalt: HallenplanInhalt | None, jetzt: datetime) -> None:
        minuten = inhalt.konfig.praesenz_alarm_minuten if inhalt else PRAESENZ_ALARM_VORGABE
        zu_melden: list[str] = []
        with self._sitzungen() as db:
            praesenz: dict[str, dict[str, Any]] = lies(db, "praesenz", {})
            if not praesenz:
                return
            for feld_id, p in praesenz.items():
                if laufende_buchung(inhalt, feld_id, jetzt) is not None:
                    p["ohne_buchung_seit"] = None
                    continue
                if p.get("ohne_buchung_seit") is None:
                    p["ohne_buchung_seit"] = jetzt.isoformat()
                seit = datetime.fromisoformat(p["ohne_buchung_seit"])
                if not p.get("alarm") and jetzt - seit >= timedelta(minutes=minuten):
                    p["alarm"] = True
                    zu_melden.append(feld_id)
            schreibe(db, "praesenz", praesenz)
            db.commit()
        for feld_id in zu_melden:
            self._ereignisse.melde("praesenz_ohne_buchung", feld_id=feld_id, minuten=minuten)
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd hall && pytest -q tests/test_steuerung.py && cd .. && ruff check hall && ruff format --check hall && mypy`
Expected: 10 passed, keine Lint- oder Typfehler

- [ ] **Step 5: Commit**

```bash
git add hall/beachhub_hall/aufgaben/steuerung.py hall/tests/test_steuerung.py
git commit -m "feat(hall): Steuerung für Licht und Heizung mit Aktorfehler und Präsenzalarm"
```

---
## Task 10: HA-Zuhörer und Status-Sensoren in HA

**Files:**
- Create: `hall/beachhub_hall/aufgaben/ha_zuhoerer.py`, `hall/beachhub_hall/aufgaben/status_ha.py`
- Test: `hall/tests/test_zuhoerer.py`

**Interfaces:**
- Consumes: `HaClient`, `HaWebSocket`, `HaFehler` (Task 7), `PinPruefer` (Task 8), `Ereignisse`, `plan.lade`, `plan.version` (Task 4), `laufende_buchung`, `zutritt_offen` (Task 3), `Lage` (Task 6), `Zuordnung` (Task 2), `db.lies/schreibe` (Task 2).
- Produces `beachhub_hall.aufgaben.ha_zuhoerer.HaZuhoerer(sitzungen, uhr, zuordnung, ha, ereignisse, lage, pruefer, steuerung_wecker: asyncio.Event, status_wecker: asyncio.Event, backoff_start: float = 1.0)`:
  - `async laufen() -> None`: Endlosschleife aus Verbinden, Abonnieren (`state_changed` und Tastenfeld-Ereignis), `verbunden()`, Ereignisse verarbeiten. Bei `HaFehler` folgen `getrennt()`, `pruefe_ausfall()` und Backoff (Start `backoff_start`, verdoppelt bis 60 s).
  - `async verbunden() -> None`: `lage.ha_verbunden = True`, alle Zustände neu einlesen, Handbetrieb und Präsenz abgleichen, `status_wecker` und `steuerung_wecker` setzen
  - `getrennt() -> None`, `pruefe_ausfall() -> None` (meldet `ha_nicht_erreichbar` einmal, wenn seit 2 min keine Verbindung besteht; der Ausfall zählt ab Dienststart)
  - `async verarbeite(event: dict) -> None`: verarbeitet Tastenfeld-Ereignis, Handbetrieb, Präsenz, Türkontakt und externe Änderung gesteuerter Entitäten
  - `async warte_auf_eingaben() -> None` (nur für Tests)
- Produces `beachhub_hall.aufgaben.status_ha.StatusHa(sitzungen, uhr, ha, ereignisse)` mit Attribut `wecker: asyncio.Event` und `async einmal() -> None`. Schreibt `sensor.beachhub_planversion`, `sensor.beachhub_letzter_kontakt`, `binary_sensor.beachhub_verbunden` (Kontakt ≤ 10 min) und `sensor.beachhub_warteschlange`.
- Zustandsschlüssel: `handbetrieb` (bool), `praesenz` (Format wie Task 9), `letzter_master` (von Task 8).

- [ ] **Step 1: Failing Tests schreiben**

`hall/tests/test_zuhoerer.py`:
```python
import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import timedelta

import pytest
from beachhub_hall import plan
from beachhub_hall.aufgaben.ha_zuhoerer import HaZuhoerer
from beachhub_hall.aufgaben.status_ha import StatusHa
from beachhub_hall.clock import SimulierteUhr
from beachhub_hall.config import (
    FeldZuordnung,
    HeizungKonfig,
    TastenfeldKonfig,
    TuerKonfig,
    Zuordnung,
)
from beachhub_hall.db import lies, schreibe
from beachhub_hall.ereignisse import Ereignisse
from beachhub_hall.ha import HaClient
from beachhub_hall.lage import Lage
from beachhub_hall.pin import PinPruefer
from beachhub_hall.tuer import Tuer
from beachhub_shared.hallenplan import PlanBuchung
from beachhub_shared.signatur import erzeuge_schluesselpaar
from sqlalchemy.orm import Session, sessionmaker

from tests.ha_simulator import HaSimulator
from tests.hilfen import F1, F2, MASTER_HASH, MASTER_PIN, FakeSchlaf, baue_plan, buchung, signiertes_dokument, t

PRIV, _ = erzeuge_schluesselpaar()
PIN = "482913"
ZUORDNUNG = Zuordnung(
    master_pin_hash=MASTER_HASH,
    felder={
        F1: FeldZuordnung("light.feld_1", "binary_sensor.praesenz_feld_1"),
        F2: FeldZuordnung("light.feld_2", "binary_sensor.praesenz_feld_2"),
    },
    heizung=HeizungKonfig("climate.halle"),
    tuer=TuerKonfig("lock.eingang", kontakt="binary_sensor.tuer"),
    tastenfeld=TastenfeldKonfig("esphome.beachhub_pin", "code", 3),
    handbetrieb="input_boolean.beachhub_handbetrieb",
)


def zustandswechsel(entity_id: str, state: str) -> dict[str, object]:
    return {
        "event_type": "state_changed",
        "data": {"entity_id": entity_id, "old_state": None, "new_state": {"entity_id": entity_id, "state": state, "attributes": {}}},
    }


class Aufbau:
    def __init__(self, sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator, url: str | None = None) -> None:
        self.sitzungen, self.uhr, self.sim = sitzungen, uhr, ha
        self.client = HaClient(url or ha.url, HaSimulator.TOKEN)
        self.ereignisse = Ereignisse(sitzungen, uhr)
        self.lage = Lage()
        self.steuerung_wecker = asyncio.Event()
        self.status_wecker = asyncio.Event()
        schlaf = FakeSchlaf()
        tuer = Tuer(self.client, ZUORDNUNG.tuer, self.ereignisse, schlaf)
        pruefer = PinPruefer(sitzungen, uhr, ZUORDNUNG, self.ereignisse, tuer, schlaf)
        self.zuhoerer = HaZuhoerer(
            sitzungen, uhr, ZUORDNUNG, self.client, self.ereignisse, self.lage, pruefer,
            self.steuerung_wecker, self.status_wecker, backoff_start=0.01,
        )
        self.status = StatusHa(sitzungen, uhr, self.client, self.ereignisse)

    def plan(self, *buchungen: PlanBuchung) -> None:
        inhalt = baue_plan(buchungen)
        with self.sitzungen() as db:
            plan.speichere(db, signiertes_dokument(inhalt, plan.version(db) + 1, PRIV), inhalt, t(0))

    def ereignis(self, typ: str) -> list[dict[str, object]]:
        return [
            {"feld_id": e.feld_id, "buchung_id": e.buchung_id, **e.daten}
            for e in self.ereignisse.unbestaetigt(1000)
            if e.typ == typ
        ]


@pytest.fixture
async def a(sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator) -> AsyncIterator[Aufbau]:
    aufbau = Aufbau(sitzungen, uhr, ha)
    yield aufbau
    await aufbau.client.schliesse()


async def warte_bis(bedingung: Callable[[], bool], sekunden: float = 3.0) -> None:
    async def schleife() -> None:
        while not bedingung():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(schleife(), timeout=sekunden)


async def test_tastenfeld_code_als_zahl_oeffnet(a: Aufbau) -> None:
    a.plan(buchung(F1, t(19), t(21), pin=PIN))
    a.uhr.stelle(t(19))
    await a.zuhoerer.verarbeite({"event_type": "esphome.beachhub_pin", "data": {"code": int(PIN)}})
    await a.zuhoerer.warte_auf_eingaben()
    assert a.sim.zustaende["lock.eingang"]["state"] == "unlocked"
    await a.zuhoerer.verarbeite({"event_type": "esphome.beachhub_pin", "data": {}})
    await a.zuhoerer.warte_auf_eingaben()
    assert len(a.ereignis("pin_akzeptiert")) == 1 and a.ereignis("pin_abgelehnt") == []


async def test_handbetrieb_an_und_aus(a: Aufbau) -> None:
    await a.zuhoerer.verarbeite(zustandswechsel("input_boolean.beachhub_handbetrieb", "on"))
    await a.zuhoerer.verarbeite(zustandswechsel("input_boolean.beachhub_handbetrieb", "on"))
    with a.sitzungen() as db:
        assert lies(db, "handbetrieb") is True
    assert len(a.ereignis("handbetrieb_an")) == 1
    assert not a.steuerung_wecker.is_set()
    await a.zuhoerer.verarbeite(zustandswechsel("input_boolean.beachhub_handbetrieb", "off"))
    assert len(a.ereignis("handbetrieb_aus")) == 1
    assert a.steuerung_wecker.is_set()  # Rückkehr zur Automatik stellt den Sollzustand sofort her


async def test_praesenz_mit_und_ohne_buchung(a: Aufbau) -> None:
    b = buchung(F1, t(19), t(21))
    a.plan(b)
    a.uhr.stelle(t(19, 2))
    await a.zuhoerer.verarbeite(zustandswechsel("binary_sensor.praesenz_feld_1", "on"))
    await a.zuhoerer.verarbeite(zustandswechsel("binary_sensor.praesenz_feld_2", "on"))
    start = a.ereignis("praesenz_start")
    assert start[0]["feld_id"] == F1 and start[0]["buchung_id"] == b.buchung_id
    assert start[1]["feld_id"] == F2 and start[1]["buchung_id"] is None
    with a.sitzungen() as db:
        praesenz = lies(db, "praesenz")
    assert praesenz[F1]["ohne_buchung_seit"] is None
    assert praesenz[F2]["ohne_buchung_seit"] == t(19, 2).isoformat()
    a.uhr.stelle(t(19, 40))
    await a.zuhoerer.verarbeite(zustandswechsel("binary_sensor.praesenz_feld_1", "off"))
    assert a.ereignis("praesenz_ende") == [{"feld_id": F1, "buchung_id": None, "dauer_minuten": 38}]
    with a.sitzungen() as db:
        assert F1 not in lies(db, "praesenz")


async def test_tuer_offen_ausserhalb(a: Aufbau) -> None:
    a.plan(buchung(F1, t(19), t(21)))
    a.uhr.stelle(t(18, 50))  # im Zutrittsfenster
    await a.zuhoerer.verarbeite(zustandswechsel("binary_sensor.tuer", "on"))
    assert a.ereignis("tuer_offen_ausserhalb") == []
    a.uhr.stelle(t(22))
    await a.zuhoerer.verarbeite(zustandswechsel("binary_sensor.tuer", "on"))
    assert len(a.ereignis("tuer_offen_ausserhalb")) == 1
    a.uhr.stelle(t(23))
    await a.zuhoerer.verarbeite({"event_type": "esphome.beachhub_pin", "data": {"code": MASTER_PIN}})
    await a.zuhoerer.warte_auf_eingaben()
    a.uhr.vor(minutes=3)
    await a.zuhoerer.verarbeite(zustandswechsel("binary_sensor.tuer", "on"))
    assert len(a.ereignis("tuer_offen_ausserhalb")) == 1  # Master-PIN vor 3 min: kein Alarm


async def test_externe_aenderung_stoesst_steuerung_an(a: Aufbau) -> None:
    await a.zuhoerer.verarbeite(zustandswechsel("light.feld_1", "on"))
    assert a.steuerung_wecker.is_set()
    assert a.lage.ist_an("light.feld_1") is True


async def test_ha_ausfall_wird_nach_2_minuten_einmal_gemeldet(sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator) -> None:
    aufbau = Aufbau(sitzungen, uhr, ha, url="http://127.0.0.1:9")
    aufbau.zuhoerer.getrennt()
    uhr.vor(minutes=1, seconds=59)
    aufbau.zuhoerer.pruefe_ausfall()
    assert aufbau.ereignis("ha_nicht_erreichbar") == []
    uhr.vor(seconds=1)
    aufbau.zuhoerer.pruefe_ausfall()
    aufbau.zuhoerer.pruefe_ausfall()
    assert len(aufbau.ereignis("ha_nicht_erreichbar")) == 1
    await aufbau.client.schliesse()


async def test_nach_verbindung_status_und_steuerung_angestossen(a: Aufbau) -> None:
    await a.sim.setze("input_boolean.beachhub_handbetrieb", "on")
    await a.sim.setze("binary_sensor.praesenz_feld_2", "on")
    await a.zuhoerer.verbunden()
    assert a.lage.ha_verbunden is True and a.lage.ist_an("binary_sensor.praesenz_feld_2") is True
    assert a.status_wecker.is_set() and a.steuerung_wecker.is_set()
    assert len(a.ereignis("handbetrieb_an")) == 1
    assert [e["feld_id"] for e in a.ereignis("praesenz_start")] == [F2]


async def test_wiederverbindung_ueber_websocket(a: Aufbau) -> None:
    aufgabe = asyncio.create_task(a.zuhoerer.laufen())
    try:
        await warte_bis(lambda: a.sim.abonnements() == 2)
        await a.sim.trenne_alle()
        await warte_bis(lambda: a.sim.verbindungen_gesamt == 2 and a.sim.abonnements() == 2)
        await a.sim.setze("input_boolean.beachhub_handbetrieb", "on")
        await warte_bis(lambda: len(a.ereignis("handbetrieb_an")) == 1)
    finally:
        aufgabe.cancel()
        with pytest.raises(asyncio.CancelledError):
            await aufgabe


async def test_status_sensoren(a: Aufbau) -> None:
    a.plan()
    with a.sitzungen() as db:
        schreibe(db, "letzter_kontakt", (t(17) - timedelta(minutes=5)).isoformat())
        db.commit()
    a.ereignisse.melde("dienst_gestartet")
    await a.status.einmal()
    g = a.sim.geschrieben
    assert g["sensor.beachhub_planversion"]["state"] == "1"
    assert g["sensor.beachhub_letzter_kontakt"]["attributes"]["device_class"] == "timestamp"
    assert g["binary_sensor.beachhub_verbunden"]["state"] == "on"
    assert g["sensor.beachhub_warteschlange"]["state"] == "1"
    a.uhr.vor(minutes=6)
    await a.status.einmal()
    assert a.sim.geschrieben["binary_sensor.beachhub_verbunden"]["state"] == "off"


async def test_status_ohne_ha_kein_absturz(sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator) -> None:
    aufbau = Aufbau(sitzungen, uhr, ha, url="http://127.0.0.1:9")
    await aufbau.status.einmal()
    await aufbau.client.schliesse()

```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd hall && pytest -q tests/test_zuhoerer.py`
Expected: FAIL mit `ModuleNotFoundError: No module named 'beachhub_hall.aufgaben.ha_zuhoerer'`

- [ ] **Step 3: Implementieren**

`hall/beachhub_hall/aufgaben/ha_zuhoerer.py`:
```python
"""HA-Zuhörer: abonniert über den HA-WebSocket Zustandsänderungen und das Tastenfeld-Ereignis.

Bei Verbindungsverlust verbindet er sich mit Backoff (1 s bis 60 s) neu; nach 2 min ohne
Verbindung meldet er einmal `ha_nicht_erreichbar`. Nach jeder (Wieder-)Verbindung liest er alle
Zustände neu ein – so gehen weder Handbetrieb noch Präsenz während eines Ausfalls verloren –
und stößt Steuerung und Status an (HA hat die Status-Sensoren bei einem Neustart vergessen).
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from beachhub_hall import plan
from beachhub_hall.clock import Uhr
from beachhub_hall.config import Zuordnung
from beachhub_hall.db import lies, schreibe
from beachhub_hall.ereignisse import Ereignisse
from beachhub_hall.ha import HaClient, HaFehler
from beachhub_hall.lage import Lage
from beachhub_hall.pin import PinPruefer
from beachhub_hall.soll import laufende_buchung, zutritt_offen

logger = logging.getLogger(__name__)
AUSFALL_MELDEN_NACH = timedelta(minutes=2)
MASTER_TUER_KULANZ = timedelta(minutes=5)
MAX_BACKOFF = 60.0


class HaZuhoerer:
    def __init__(
        self,
        sitzungen: sessionmaker[Session],
        uhr: Uhr,
        zuordnung: Zuordnung,
        ha: HaClient,
        ereignisse: Ereignisse,
        lage: Lage,
        pruefer: PinPruefer,
        steuerung_wecker: asyncio.Event,
        status_wecker: asyncio.Event,
        backoff_start: float = 1.0,
    ) -> None:
        self._sitzungen = sitzungen
        self._uhr = uhr
        self._z = zuordnung
        self._ha = ha
        self._ereignisse = ereignisse
        self._lage = lage
        self._pruefer = pruefer
        self._steuerung_wecker = steuerung_wecker
        self._status_wecker = status_wecker
        self._backoff_start = backoff_start
        self._eingaben: set[asyncio.Task[bool]] = set()
        # Bis zur ersten Verbindung gilt HA als ausgefallen – seit dem Start des Dienstes.
        self._ausfall_seit: datetime | None = uhr.jetzt()
        self._ausfall_gemeldet = False

    async def laufen(self) -> None:
        backoff = self._backoff_start
        while True:
            try:
                ws = await self._ha.websocket()
                try:
                    await ws.abonniere("state_changed")
                    await ws.abonniere(self._z.tastenfeld.ereignis)
                    await self.verbunden()
                    backoff = self._backoff_start
                    while True:
                        await self.verarbeite(await ws.naechstes())
                finally:
                    await ws.schliesse()
            except HaFehler as e:
                logger.warning("HA-WebSocket getrennt: %s", e)
            self.getrennt()
            self.pruefe_ausfall()
            await asyncio.sleep(backoff)
            backoff = min(MAX_BACKOFF, backoff * 2)

    async def verbunden(self) -> None:
        self._lage.ha_verbunden = True
        self._ausfall_seit = None
        self._ausfall_gemeldet = False
        for zustand in await self._ha.zustaende():
            entity = zustand.get("entity_id")
            if isinstance(entity, str):
                self._lage.ist[entity] = zustand
        if self._z.handbetrieb and self._lage.state(self._z.handbetrieb) is not None:
            self._handbetrieb(self._lage.state(self._z.handbetrieb) == "on")
        for feld_id, z in self._z.felder.items():
            if z.praesenz and self._lage.state(z.praesenz) is not None:
                self._praesenz(feld_id, self._lage.state(z.praesenz) == "on")
        self._status_wecker.set()
        self._steuerung_wecker.set()

    def getrennt(self) -> None:
        self._lage.ha_verbunden = False
        if self._ausfall_seit is None:
            self._ausfall_seit = self._uhr.jetzt()

    def pruefe_ausfall(self) -> None:
        if self._ausfall_seit is None or self._ausfall_gemeldet:
            return
        if self._uhr.jetzt() - self._ausfall_seit >= AUSFALL_MELDEN_NACH:
            self._ausfall_gemeldet = True
            self._ereignisse.melde("ha_nicht_erreichbar", seit=self._ausfall_seit.isoformat())

    async def verarbeite(self, event: dict[str, Any]) -> None:
        typ = event.get("event_type")
        daten = event.get("data") or {}
        if typ == self._z.tastenfeld.ereignis:
            code = daten.get(self._z.tastenfeld.feld)
            if code is not None:
                aufgabe = asyncio.create_task(self._pruefer.eingabe(str(code)))
                self._eingaben.add(aufgabe)
                aufgabe.add_done_callback(self._eingaben.discard)
            return
        if typ != "state_changed":
            return
        entity = daten.get("entity_id")
        neu = daten.get("new_state")
        if not isinstance(entity, str):
            return
        if not isinstance(neu, dict):
            self._lage.ist.pop(entity, None)
            return
        self._lage.ist[entity] = neu
        an = neu.get("state") == "on"
        if entity == self._z.handbetrieb:
            self._handbetrieb(an)
        elif (feld_id := self._z.feld_fuer_praesenz(entity)) is not None:
            self._praesenz(feld_id, an)
        elif entity == self._z.tuer.kontakt:
            if an:
                self._tuer_geoeffnet()
        elif entity in self._z.gesteuerte():
            self._steuerung_wecker.set()

    async def warte_auf_eingaben(self) -> None:
        if self._eingaben:
            await asyncio.gather(*list(self._eingaben))

    def _handbetrieb(self, an: bool) -> None:
        with self._sitzungen() as db:
            if bool(lies(db, "handbetrieb", False)) == an:
                return
            schreibe(db, "handbetrieb", an)
            db.commit()
        self._ereignisse.melde("handbetrieb_an" if an else "handbetrieb_aus")
        if not an:
            self._steuerung_wecker.set()
        self._status_wecker.set()

    def _praesenz(self, feld_id: str, an: bool) -> None:
        jetzt = self._uhr.jetzt()
        with self._sitzungen() as db:
            gespeichert = plan.lade(db)
            praesenz: dict[str, dict[str, Any]] = lies(db, "praesenz", {})
            if an == (feld_id in praesenz):
                return
            laufend = laufende_buchung(gespeichert.inhalt if gespeichert else None, feld_id, jetzt)
            if an:
                praesenz[feld_id] = {
                    "seit": jetzt.isoformat(),
                    "ohne_buchung_seit": None if laufend else jetzt.isoformat(),
                    "alarm": False,
                }
            else:
                eintrag = praesenz.pop(feld_id)
            schreibe(db, "praesenz", praesenz)
            db.commit()
        if an:
            self._ereignisse.melde(
                "praesenz_start", feld_id=feld_id, buchung_id=laufend.buchung_id if laufend else None
            )
        else:
            dauer = jetzt - datetime.fromisoformat(eintrag["seit"])
            self._ereignisse.melde(
                "praesenz_ende", feld_id=feld_id, dauer_minuten=int(dauer.total_seconds() // 60)
            )

    def _tuer_geoeffnet(self) -> None:
        jetzt = self._uhr.jetzt()
        with self._sitzungen() as db:
            gespeichert = plan.lade(db)
            letzter_master = lies(db, "letzter_master")
        if zutritt_offen(gespeichert.inhalt if gespeichert else None, jetzt):
            return
        if letzter_master and jetzt - datetime.fromisoformat(letzter_master) < MASTER_TUER_KULANZ:
            return
        self._ereignisse.melde("tuer_offen_ausserhalb")
```

`hall/beachhub_hall/aufgaben/status_ha.py`:
```python
"""Status nach HA: vier Sensoren, die der Betreiber im HA-Dashboard sieht – ganz ohne Custom
Component. Per `POST /api/states` geschriebene Zustände überleben keinen HA-Neustart; der
HA-Zuhörer stößt deshalb nach jeder Wiederverbindung ein neues Schreiben an."""

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker

from beachhub_hall import plan
from beachhub_hall.clock import Uhr
from beachhub_hall.db import lies
from beachhub_hall.ereignisse import Ereignisse
from beachhub_hall.ha import HaClient, HaFehler

logger = logging.getLogger(__name__)
VERBUNDEN_FENSTER = timedelta(minutes=10)


class StatusHa:
    def __init__(
        self, sitzungen: sessionmaker[Session], uhr: Uhr, ha: HaClient, ereignisse: Ereignisse
    ) -> None:
        self._sitzungen = sitzungen
        self._uhr = uhr
        self._ha = ha
        self._ereignisse = ereignisse
        self.wecker = asyncio.Event()

    async def einmal(self) -> None:
        with self._sitzungen() as db:
            gespeichert = plan.lade(db)
            kontakt = lies(db, "letzter_kontakt")
        verbunden = bool(kontakt) and self._uhr.jetzt() - datetime.fromisoformat(kontakt) <= VERBUNDEN_FENSTER
        try:
            await self._ha.setze_zustand(
                "sensor.beachhub_planversion",
                str(gespeichert.version if gespeichert else 0),
                {
                    "friendly_name": "Beachhub Planversion",
                    "gueltig_bis": gespeichert.inhalt.gueltig_bis.isoformat() if gespeichert else None,
                    "empfangen_am": gespeichert.empfangen_am.isoformat() if gespeichert else None,
                },
            )
            await self._ha.setze_zustand(
                "sensor.beachhub_letzter_kontakt",
                kontakt or "unknown",
                {"friendly_name": "Beachhub letzter Kontakt zum Hauptsystem", "device_class": "timestamp"},
            )
            await self._ha.setze_zustand(
                "binary_sensor.beachhub_verbunden",
                "on" if verbunden else "off",
                {"friendly_name": "Beachhub mit Hauptsystem verbunden", "device_class": "connectivity"},
            )
            await self._ha.setze_zustand(
                "sensor.beachhub_warteschlange",
                str(self._ereignisse.offen()),
                {"friendly_name": "Beachhub nicht zugestellte Ereignisse", "unit_of_measurement": "Ereignisse"},
            )
        except HaFehler as e:
            logger.warning("Status nach HA nicht geschrieben: %s", e)
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd hall && pytest -q tests/test_zuhoerer.py && cd .. && ruff check hall && ruff format --check hall && mypy`
Expected: 10 passed, keine Lint- oder Typfehler

- [ ] **Step 5: Commit**

```bash
git add hall/beachhub_hall/aufgaben/ha_zuhoerer.py hall/beachhub_hall/aufgaben/status_ha.py hall/tests/test_zuhoerer.py
git commit -m "feat(hall): HA-Zuhörer für Tastenfeld, Präsenz, Tür und Handbetrieb, Status-Sensoren in HA"
```

---
## Task 11: Dienst zusammensetzen – Takt, Start, Health, Hallentag und 72 h offline

**Files:**
- Create: `hall/beachhub_hall/dienst.py`, `hall/beachhub_hall/health.py`, `hall/beachhub_hall/__main__.py`
- Modify: `hall/beachhub_hall/aufgaben/__init__.py` (Funktion `takt`)
- Test: `hall/tests/test_dienst.py`, `hall/tests/test_integration.py`

**Interfaces:**
- Consumes: alle Klassen aus Tasks 2–10.
- Produces `beachhub_hall.aufgaben.takt(name: str, einmal: Callable[[], Awaitable[object]], intervall: float, wecker: asyncio.Event | None = None) -> None` (async): ruft `einmal()` auf, loggt Ausnahmen statt abzubrechen und wartet dann `intervall` Sekunden oder bis der `wecker` gesetzt wird.
- Produces `beachhub_hall.dienst.Dienst(*, oeffentlich_hex, zuordnung, sitzungen, uhr, ha, core, schlafen=asyncio.sleep, zuhoerer_backoff=1.0)`:
  - Attribute `ereignisse`, `lage`, `steuerung`, `plan_abruf`, `melder`, `status_ha`, `tuer`, `pruefer`, `zuhoerer`, `ha`, `core`
  - `status() -> HallenStatus`, `gestartet() -> None` (meldet `dienst_gestartet` mit `version`), `async laufen() -> None`, `async schliesse() -> None`
  - `Dienst.aus_umgebung(u: Umgebung) -> Dienst` (lädt `hall.toml`, öffnet `DATA_DIR/hall.sqlite`, baut den mTLS-Kontext)
- Produces `beachhub_hall.health.starte_health(dienst, port: int) -> web.AppRunner` (async) und `health_app(dienst) -> web.Application` mit `GET /health` und der Antwort `{"status": "ok", "planversion", "ha_verbunden", "warteschlange"}`.
- Produces `beachhub_hall.__main__.main(argv: list[str] | None = None) -> None` mit den Unterbefehlen `start` (Vorgabe) und `master-pin`.

- [ ] **Step 1: Failing Tests schreiben**

`hall/tests/test_dienst.py`:
```python
import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer
from argon2 import PasswordHasher
from beachhub_hall import __main__ as cli
from beachhub_hall.aufgaben import takt
from beachhub_hall.clock import SimulierteUhr
from beachhub_hall.config import KonfigFehler, Umgebung, lade_zuordnung
from beachhub_hall.dienst import Dienst
from beachhub_hall.ha import HaClient
from beachhub_hall.health import health_app
from sqlalchemy.orm import Session, sessionmaker

from tests.core_simulator import CoreSimulator
from tests.ha_simulator import HaSimulator
from tests.hilfen import TOML_BEISPIEL, FakeSchlaf, baue_plan


async def test_takt_laeuft_weiter_nach_fehler_und_wacht_auf() -> None:
    aufrufe: list[int] = []
    wecker = asyncio.Event()

    async def einmal() -> None:
        aufrufe.append(len(aufrufe))
        if len(aufrufe) == 1:
            raise RuntimeError("erster Lauf scheitert")

    aufgabe = asyncio.create_task(takt("test", einmal, intervall=3600, wecker=wecker))
    await asyncio.sleep(0.01)
    assert len(aufrufe) == 1
    wecker.set()
    await asyncio.sleep(0.01)
    assert len(aufrufe) == 2 and not wecker.is_set()
    aufgabe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await aufgabe


def test_master_pin_befehl(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    eingaben = iter(["4711", "4711"])
    monkeypatch.setattr(cli, "getpass", lambda _prompt: next(eingaben))
    cli.main(["master-pin"])
    ausgabe = capsys.readouterr().out.strip()
    assert PasswordHasher().verify(ausgabe, "4711")
    eingaben = iter(["4711", "4712"])
    with pytest.raises(SystemExit):
        cli.main(["master-pin"])


def test_aus_umgebung(tmp_path: Path) -> None:
    toml = tmp_path / "hall.toml"
    toml.write_text(TOML_BEISPIEL, encoding="utf-8")
    u = Umgebung(data_dir=tmp_path / "daten", hall_toml=toml, core_public_key="00" * 32)
    d = Dienst.aus_umgebung(u)
    assert (tmp_path / "daten" / "hall.sqlite").exists()
    assert d.pruefer is not None
    with pytest.raises(KonfigFehler):
        Dienst.aus_umgebung(Umgebung(data_dir=tmp_path, hall_toml=tmp_path / "fehlt.toml"))
    assert lade_zuordnung(toml).felder


@pytest.fixture
async def dienst(
    sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator, tmp_path: Path
) -> AsyncIterator[tuple[Dienst, CoreSimulator]]:
    toml = tmp_path / "hall.toml"
    toml.write_text(TOML_BEISPIEL, encoding="utf-8")
    core = CoreSimulator()
    d = Dienst(
        oeffentlich_hex=core.oeffentlich,
        zuordnung=lade_zuordnung(toml),
        sitzungen=sitzungen,
        uhr=uhr,
        ha=HaClient(ha.url, HaSimulator.TOKEN),
        core=core.client(),
        schlafen=FakeSchlaf(),
        zuhoerer_backoff=0.01,
    )
    yield d, core
    await d.schliesse()


async def test_health(dienst: tuple[Dienst, CoreSimulator]) -> None:
    d, _ = dienst
    async with TestClient(TestServer(health_app(d))) as client:
        r = await client.get("/health")
        assert r.status == 200
        assert await r.json() == {"status": "ok", "planversion": 0, "ha_verbunden": False, "warteschlange": 0}


async def test_laufen_holt_plan_verbindet_ha_und_meldet_start(
    dienst: tuple[Dienst, CoreSimulator], ha: HaSimulator
) -> None:
    d, core = dienst
    core.veroeffentliche(baue_plan([]))
    aufgabe = asyncio.create_task(d.laufen())
    try:
        async def bereit() -> None:
            while not (
                core.typen()
                and ha.abonnements() == 2
                and "sensor.beachhub_planversion" in ha.geschrieben
                and d.status().planversion == 1
            ):
                await asyncio.sleep(0.01)

        await asyncio.wait_for(bereit(), timeout=5)
        assert core.typen()[0] == "dienst_gestartet"
    finally:
        aufgabe.cancel()
        with pytest.raises(asyncio.CancelledError):
            await aufgabe
```

`hall/tests/test_integration.py`:
```python
"""Ganze Abläufe gegen HA- und Core-Simulator mit simulierter Uhr (Hallendienst-Spec § 7)."""

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest
from beachhub_hall.clock import SimulierteUhr
from beachhub_hall.config import lade_zuordnung
from beachhub_hall.dienst import Dienst
from beachhub_hall.ha import HaClient
from sqlalchemy.orm import Session, sessionmaker

from tests.core_simulator import CoreSimulator
from tests.ha_simulator import HaSimulator
from tests.hilfen import F1, F2, TAG, TOML_BEISPIEL, FakeSchlaf, baue_plan, buchung, t

# F1: Abo-Abende im Offline-Test, F2: Buchung im Hallentag.


@pytest.fixture
async def aufbau(
    sitzungen: sessionmaker[Session], uhr: SimulierteUhr, ha: HaSimulator, tmp_path: Path
) -> AsyncIterator[tuple[Dienst, CoreSimulator]]:
    toml = tmp_path / "hall.toml"
    toml.write_text(TOML_BEISPIEL, encoding="utf-8")
    core = CoreSimulator()
    d = Dienst(
        oeffentlich_hex=core.oeffentlich,
        zuordnung=lade_zuordnung(toml),
        sitzungen=sitzungen,
        uhr=uhr,
        ha=HaClient(ha.url, HaSimulator.TOKEN),
        core=core.client(),
        schlafen=FakeSchlaf(),
    )
    yield d, core
    await d.schliesse()


def state_changed(entity_id: str, state: str) -> dict[str, object]:
    return {
        "event_type": "state_changed",
        "data": {"entity_id": entity_id, "new_state": {"entity_id": entity_id, "state": state}},
    }


async def test_hallentag(aufbau: tuple[Dienst, CoreSimulator], ha: HaSimulator, uhr: SimulierteUhr) -> None:
    """Hauptspec § 9 „Hallentag“ mit den Werten des Betreibers (Heizvorlauf 30 min)."""
    d, core = aufbau
    b = buchung(F2, t(19), t(21), pin="482913")
    core.veroeffentliche(baue_plan([b]))
    assert await d.plan_abruf.einmal()

    uhr.stelle(t(18, 29))
    await d.steuerung.einmal()
    assert ha.zustaende["climate.halle"]["attributes"]["temperature"] == 0.0
    uhr.stelle(t(18, 30))
    await d.steuerung.einmal()
    assert ha.zustaende["climate.halle"]["attributes"]["temperature"] == 18.0
    uhr.stelle(t(18, 55))
    await d.steuerung.einmal()
    assert ha.zustaende["light.feld_2"]["state"] == "on"
    assert ha.zustaende["light.feld_1"]["state"] == "off"

    uhr.stelle(t(19, 2))
    await d.zuhoerer.verarbeite({"event_type": "esphome.beachhub_pin", "data": {"code": "482913"}})
    await d.zuhoerer.warte_auf_eingaben()
    assert ha.zustaende["lock.eingang"]["state"] == "unlocked"
    await d.zuhoerer.verarbeite(state_changed("binary_sensor.praesenz_feld_2", "on"))

    uhr.stelle(t(21, 4))
    await d.steuerung.einmal()
    assert ha.zustaende["light.feld_2"]["state"] == "on"
    uhr.stelle(t(21, 5))
    await d.steuerung.einmal()
    assert ha.zustaende["light.feld_2"]["state"] == "off"
    assert ha.zustaende["climate.halle"]["attributes"]["temperature"] == 0.0

    await d.melder.leeren()
    assert core.typen() == [
        "heizung_gesetzt",
        "licht_geschaltet",
        "pin_akzeptiert",
        "praesenz_start",
        "heizung_gesetzt",  # 21:04 – Heizen endet mit der letzten Buchung
        "licht_geschaltet",  # 21:05 – Licht nach 5 min Nachlauf aus
    ]
    pin, praesenz = core.empfangen[2], core.empfangen[3]
    assert pin.buchung_id == b.buchung_id and praesenz.buchung_id == b.buchung_id
    assert core.status[-1].planversion == 1
    assert d.ereignisse.offen() == 0


async def test_72_stunden_ohne_hauptsystem(
    aufbau: tuple[Dienst, CoreSimulator], ha: HaSimulator, uhr: SimulierteUhr
) -> None:
    """Hauptspec § 9 „Internetausfall Halle“: Der Plan gilt weiter, Ereignisse sammeln sich
    und werden nach der Rückkehr lückenlos in seq-Reihenfolge nachgeliefert."""
    d, core = aufbau
    tage = [TAG + timedelta(days=i) for i in range(4)]
    core.veroeffentliche(baue_plan([buchung(F1, t(19, tag=tag), t(20, tag=tag)) for tag in tage]))
    assert await d.plan_abruf.einmal()
    core.offline = True

    start = t(12)
    uhr.stelle(start)
    while uhr.jetzt() < start + timedelta(hours=72):
        await d.steuerung.einmal()
        await d.plan_abruf.einmal()
        await d.melder.einmal()
        uhr.vor(minutes=30)

    einschalten = [a for a in ha.aufrufe if a[:2] == ("light", "turn_on")]
    assert len(einschalten) == 3  # an drei Abenden geschaltet, ganz ohne Hauptsystem
    assert core.empfangen == []
    offen = d.ereignisse.offen()
    assert offen == 12  # je Abend: Heizung an/aus, Licht an/aus

    core.offline = False
    await d.melder.leeren()
    seqs = [e.seq for e in core.empfangen]
    assert seqs == list(range(1, offen + 1))
    assert d.ereignisse.offen() == 0
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd hall && pytest -q tests/test_dienst.py tests/test_integration.py`
Expected: FAIL mit `ImportError: cannot import name 'takt' from 'beachhub_hall.aufgaben'`

- [ ] **Step 3: Implementieren**

`hall/beachhub_hall/aufgaben/__init__.py` ersetzen durch:
```python
"""Die dauerhaft laufenden Aufgaben des Hallendienstes.

Jede Aufgabe hat eine Methode `einmal()` für genau einen Durchlauf; `takt` macht daraus die
Dauerschleife. Tests rufen `einmal()` direkt mit simulierter Uhr auf.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


async def takt(
    name: str,
    einmal: Callable[[], Awaitable[object]],
    intervall: float,
    wecker: asyncio.Event | None = None,
) -> None:
    while True:
        try:
            await einmal()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Aufgabe %s fehlgeschlagen", name)
        if wecker is None:
            await asyncio.sleep(intervall)
            continue
        try:
            await asyncio.wait_for(wecker.wait(), timeout=intervall)
        except TimeoutError:
            pass
        wecker.clear()
```

`hall/beachhub_hall/dienst.py`:
```python
"""Setzt den Hallendienst aus seinen Teilen zusammen und startet die Aufgaben.

Nach dem Start arbeitet der Dienst ab der ersten Sekunde aus dem gespeicherten Plan – noch
bevor Hauptsystem oder HA erreichbar sind (Hallendienst-Spec § 5 „Start“).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from beachhub_shared.hallenplan import HallenStatus
from sqlalchemy.orm import Session, sessionmaker

from beachhub_hall import __version__
from beachhub_hall.aufgaben import takt
from beachhub_hall.aufgaben.ha_zuhoerer import HaZuhoerer
from beachhub_hall.aufgaben.melder import Melder, baue_status
from beachhub_hall.aufgaben.plan_abruf import PlanAbruf
from beachhub_hall.aufgaben.status_ha import StatusHa
from beachhub_hall.aufgaben.steuerung import Steuerung
from beachhub_hall.clock import EchteUhr, Uhr
from beachhub_hall.config import Umgebung, Zuordnung, lade_zuordnung
from beachhub_hall.core import CoreClient, ssl_kontext
from beachhub_hall.db import oeffne
from beachhub_hall.ereignisse import Ereignisse
from beachhub_hall.ha import HaClient
from beachhub_hall.lage import Lage
from beachhub_hall.pin import PinPruefer
from beachhub_hall.tuer import Tuer

logger = logging.getLogger(__name__)


class Dienst:
    def __init__(
        self,
        *,
        oeffentlich_hex: str,
        zuordnung: Zuordnung,
        sitzungen: sessionmaker[Session],
        uhr: Uhr,
        ha: HaClient,
        core: CoreClient,
        schlafen: Callable[[float], Awaitable[None]] = asyncio.sleep,
        zuhoerer_backoff: float = 1.0,
    ) -> None:
        self.zuordnung = zuordnung
        self.sitzungen = sitzungen
        self.uhr = uhr
        self.ha = ha
        self.core = core
        self.lage = Lage()
        self.ereignisse = Ereignisse(sitzungen, uhr)
        self.steuerung = Steuerung(sitzungen, uhr, zuordnung, ha, self.ereignisse, self.lage)
        self.plan_abruf = PlanAbruf(
            sitzungen, uhr, core, oeffentlich_hex, self.ereignisse, zuordnung, self.steuerung.wecker.set
        )
        self.status_ha = StatusHa(sitzungen, uhr, ha, self.ereignisse)
        self.melder = Melder(sitzungen, uhr, core, self.ereignisse, self.status, self.plan_abruf.wecker)
        self.tuer = Tuer(ha, zuordnung.tuer, self.ereignisse, schlafen)
        self.pruefer = PinPruefer(sitzungen, uhr, zuordnung, self.ereignisse, self.tuer, schlafen)
        self.zuhoerer = HaZuhoerer(
            sitzungen,
            uhr,
            zuordnung,
            ha,
            self.ereignisse,
            self.lage,
            self.pruefer,
            self.steuerung.wecker,
            self.status_ha.wecker,
            backoff_start=zuhoerer_backoff,
        )

    @classmethod
    def aus_umgebung(cls, u: Umgebung) -> "Dienst":
        zuordnung = lade_zuordnung(u.hall_toml)
        return cls(
            oeffentlich_hex=u.core_public_key,
            zuordnung=zuordnung,
            sitzungen=oeffne(u.data_dir / "hall.sqlite"),
            uhr=EchteUhr(),
            ha=HaClient(u.ha_url, u.ha_token),
            core=CoreClient(
                u.core_url,
                u.hall_token,
                verify=ssl_kontext(u.core_ca, u.core_client_cert, u.core_client_key),
            ),
        )

    def status(self) -> HallenStatus:
        return baue_status(self.sitzungen, self.zuordnung, self.lage, self.ereignisse)

    def gestartet(self) -> None:
        self.ereignisse.melde("dienst_gestartet", version=__version__)

    async def _aufraeumen(self) -> None:
        geloescht = self.ereignisse.raeume_auf()
        if geloescht:
            logger.info("%s bestätigte Ereignisse älter als 90 Tage gelöscht", geloescht)

    async def laufen(self) -> None:
        self.gestartet()
        aufgaben = [
            asyncio.create_task(takt("steuerung", self.steuerung.einmal, 30, self.steuerung.wecker)),
            asyncio.create_task(takt("plan", self.plan_abruf.einmal, 300, self.plan_abruf.wecker)),
            asyncio.create_task(self.zuhoerer.laufen()),
            asyncio.create_task(takt("melder", self.melder.einmal, 60, self.ereignisse.neu)),
            asyncio.create_task(takt("status_ha", self.status_ha.einmal, 60, self.status_ha.wecker)),
            asyncio.create_task(takt("aufraeumen", self._aufraeumen, 24 * 3600)),
        ]
        try:
            await asyncio.gather(*aufgaben)
        finally:
            for aufgabe in aufgaben:
                aufgabe.cancel()
            await asyncio.gather(*aufgaben, return_exceptions=True)

    async def schliesse(self) -> None:
        await self.ha.schliesse()
        await self.core.schliesse()
```

`hall/beachhub_hall/health.py`:
```python
"""GET /health für Docker und Monitoring. Keine weitere Oberfläche: Status und Handbetrieb
gibt es im HA-Dashboard."""

from aiohttp import web

from beachhub_hall.dienst import Dienst


def health_app(dienst: Dienst) -> web.Application:
    async def health(_request: web.Request) -> web.Response:
        status = dienst.status()
        return web.json_response(
            {
                "status": "ok",
                "planversion": status.planversion,
                "ha_verbunden": status.ha_erreichbar,
                "warteschlange": status.warteschlange,
            }
        )

    app = web.Application()
    app.router.add_get("/health", health)
    return app


async def starte_health(dienst: Dienst, port: int) -> web.AppRunner:
    runner = web.AppRunner(health_app(dienst))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()  # noqa: S104 – Container-Netz
    return runner
```

`hall/beachhub_hall/__main__.py`:
```python
"""Kommandozeile: `beachhub-hall start` (Vorgabe) und `beachhub-hall master-pin`."""

import argparse
import asyncio
import logging
import re
from getpass import getpass

from argon2 import PasswordHasher

from beachhub_hall.config import Umgebung
from beachhub_hall.dienst import Dienst
from beachhub_hall.health import starte_health


def _master_pin() -> None:
    erste = getpass("Master-PIN (4–12 Ziffern): ")
    zweite = getpass("Master-PIN wiederholen: ")
    if erste != zweite or not re.fullmatch(r"\d{4,12}", erste):
        raise SystemExit("Die PINs stimmen nicht überein oder sind nicht 4 bis 12 Ziffern lang.")
    print(PasswordHasher().hash(erste))  # noqa: T201 – Ausgabe für hall.toml


async def _start(umgebung: Umgebung) -> None:
    dienst = Dienst.aus_umgebung(umgebung)
    runner = await starte_health(dienst, umgebung.health_port)
    try:
        await dienst.laufen()
    finally:
        await runner.cleanup()
        await dienst.schliesse()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="beachhub-hall")
    unter = parser.add_subparsers(dest="befehl")
    unter.add_parser("start", help="Hallendienst starten (Vorgabe)")
    unter.add_parser("master-pin", help="Hash für master_pin_hash in hall.toml erzeugen")
    args = parser.parse_args(argv)
    if args.befehl == "master-pin":
        _master_pin()
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    umgebung = Umgebung()
    if not umgebung.core_public_key:
        raise SystemExit("CORE_PUBLIC_KEY fehlt (öffentlicher Schlüssel von der System-Seite des Hauptsystems).")
    asyncio.run(_start(umgebung))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd hall && pytest -q && cd .. && ruff check hall && ruff format --check hall && mypy`
Expected: alle Hall-Tests PASS (`test_dienst.py` 5, `test_integration.py` 2), keine Lint- oder Typfehler. `ruff` meldet `RUF100` für das `noqa: T201` nur, wenn `RUF` aktiv ist, was es in `ruff.toml` nicht ist.

- [ ] **Step 5: Commit**

```bash
git add hall/beachhub_hall/aufgaben/__init__.py hall/beachhub_hall/dienst.py hall/beachhub_hall/health.py hall/beachhub_hall/__main__.py hall/tests/test_dienst.py hall/tests/test_integration.py
git commit -m "feat(hall): Dienst mit Takt, Health-Endpunkt und CLI; Hallentag und 72 h offline getestet"
```

---

## Task 12: Betrieb des Hallendienstes – Container, Beispielkonfiguration, HA-Einrichtung

**Files:**
- Create: `hall/Dockerfile`, `hall/docker-compose.yml`, `hall/.env.example`, `hall/hall.toml.example`, `hall/README.md`, `docs/betrieb/hallendienst.md`
- Test: `hall/tests/test_beispielkonfiguration.py`

**Interfaces:**
- Consumes: `lade_zuordnung`, `KonfigFehler` (Task 2), `Umgebung` (Task 2), CLI `beachhub-hall master-pin` (Task 11).
- Produces: Beispieldateien, die sich laden lassen. Die Platzhalter-Zeile `master_pin_hash = "$argon2id$ERSETZEN"` lässt den Dienst absichtlich nicht starten.

- [ ] **Step 1: Failing Test schreiben**

`hall/tests/test_beispielkonfiguration.py`:
```python
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
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd hall && pytest -q tests/test_beispielkonfiguration.py`
Expected: FAIL mit `FileNotFoundError` für `hall.toml.example`

- [ ] **Step 3: Beispieldateien und Container anlegen**

`hall/hall.toml.example`:
```toml
# Zuordnung der Felder zu Home-Assistant-Entitäten (A-FELD-4).
# Die Feld-UUIDs stehen im Admin-UI des Hauptsystems unter Stammdaten → Felder.

# Pflicht: mit `beachhub-hall master-pin` erzeugen und hier eintragen.
# Mit diesem Platzhalter startet der Dienst nicht.
master_pin_hash = "$argon2id$ERSETZEN"

[felder."00000000-0000-0000-0000-000000000001"]
licht = "light.feld_1"
praesenz = "binary_sensor.praesenz_feld_1"

[felder."00000000-0000-0000-0000-000000000002"]
licht = "light.feld_2"
praesenz = "binary_sensor.praesenz_feld_2"

[felder."00000000-0000-0000-0000-000000000003"]
licht = "light.feld_3"
praesenz = "binary_sensor.praesenz_feld_3"

[heizung]
entity = "climate.halle"
# ist_sensor = "sensor.halle_temperatur"   # optional, sonst Attribut current_temperature

[tuer]
entity = "lock.eingang"            # lock.* → lock.unlock; switch.* → Impuls
impuls_sekunden = 5                # nur bei switch.*
kontakt = "binary_sensor.tuer"     # optional

[tastenfeld]
ereignis = "esphome.beachhub_pin"  # HA-Ereignistyp
feld = "code"                      # Schlüssel in den Ereignisdaten
verzoegerung_sekunden = 3          # ab dem 5. Fehlversuch einer Serie

[handbetrieb]
entity = "input_boolean.beachhub_handbetrieb"
```

`hall/.env.example`:
```
# Hauptsystem über WireGuard (Caddy mit mTLS, Port 8444 nur für /hall/*)
CORE_URL=https://10.8.0.1:8444
HALL_TOKEN=change-me
CORE_CLIENT_CERT=/zertifikate/halle.crt
CORE_CLIENT_KEY=/zertifikate/halle.key
CORE_CA=/zertifikate/caddy-root.crt
# Öffentlicher Signaturschlüssel von der System-Seite im Admin-UI
CORE_PUBLIC_KEY=
# Home Assistant
HA_URL=http://127.0.0.1:8123
HA_TOKEN=
# Ablage
DATA_DIR=/data
HALL_TOML=/config/hall.toml
HEALTH_PORT=8099
```

`hall/Dockerfile`:
```dockerfile
# Multi-Arch (arm64/amd64): docker buildx build --platform linux/arm64,linux/amd64 -f hall/Dockerfile .
FROM python:3.12-slim
RUN useradd --create-home --uid 1000 halle && mkdir -p /data /config && chown halle /data
WORKDIR /app
COPY shared /app/shared
COPY hall /app/hall
RUN pip install --no-cache-dir /app/shared /app/hall
USER halle
ENV DATA_DIR=/data HALL_TOML=/config/hall.toml
VOLUME ["/data"]
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8099/health').status == 200 else 1)"
CMD ["beachhub-hall", "start"]
```

`hall/docker-compose.yml`:
```yaml
# Betrieb neben Home Assistant. Netzwerk des Hosts, damit HA (127.0.0.1:8123) und der
# WireGuard-Tunnel zum Hauptsystem ohne weitere Weiterleitung erreichbar sind.
services:
  hall:
    build: { context: .., dockerfile: hall/Dockerfile }
    restart: unless-stopped
    network_mode: host
    env_file: .env
    volumes:
      - "./data:/data"
      - "./hall.toml:/config/hall.toml:ro"
      - "./zertifikate:/zertifikate:ro"
```

`hall/README.md`:
````markdown
# Hallendienst

Steuert Licht, Heizung und Tür der Halle über Home Assistant nach dem signierten 7-Tage-Plan des
Hauptsystems. Er arbeitet ohne Verbindung zum Hauptsystem weiter, bis der Plan abläuft.

- Design: `docs/superpowers/specs/2026-09-23-hallendienst-design.md`
- Betrieb und HA-Einrichtung: `docs/betrieb/hallendienst.md`

## Entwicklung

```bash
pip install -e shared[dev] -e hall[dev]
cd hall && pytest
```

Die Tests brauchen weder Home Assistant noch das Hauptsystem. `tests/ha_simulator.py` und
`tests/core_simulator.py` ersetzen beide, eine simulierte Uhr ersetzt das Warten.

Einen HA-Simulator für eigene Versuche startet `cd hall && python -m tests.ha_simulator`
(Port 8123, Token `ha-test-token`).
````

- [ ] **Step 4: Betriebsdokumentation schreiben**

`docs/betrieb/hallendienst.md`:
````markdown
# Hallendienst – Betrieb

Der Hallendienst läuft auf dem Rechner in der Halle neben Home Assistant (HA). Er holt alle
5 Minuten den Plan vom Hauptsystem, schaltet Licht und Heizung, prüft PINs am Tastenfeld und
meldet Ereignisse zurück. Fällt das Internet aus, arbeitet er mit dem gespeicherten Plan weiter
(bis zu 7 Tage) und liefert die Ereignisse nach.

## 1. Voraussetzungen

- Home Assistant läuft auf demselben Rechner oder im selben Netz.
- WireGuard-Tunnel zum Hauptsystem (Beispiel: `core/deploy/wireguard-beispiel.md`); der
  Hallendienst ist Client mit `PersistentKeepalive = 25`.
- Docker (bei HA OS: ein Host mit Container-Unterstützung neben HA).

## 2. Zertifikate, Token, Schlüssel

Auf dem Hauptsystem:

1. `beachhub-core zertifikate` ausführen (siehe `docs/betrieb/portal.md`); es entstehen die
   interne CA und das Client-Zertifikat `halle.crt`/`halle.key`.
2. In `core/.env` `HALL_TOKEN` auf einen langen Zufallswert setzen
   (`python -c "import secrets; print(secrets.token_urlsafe(32))"`) und das Hauptsystem neu starten.
3. Das Root-Zertifikat von Caddy exportieren (Volume `caddy_data`,
   `pki/authorities/local/root.crt`) – damit prüft die Halle den Server.
4. Den öffentlichen Signaturschlüssel von der Seite **System** im Admin-UI kopieren.

Auf den Hallenrechner kopieren: `halle.crt`, `halle.key`, `caddy-root.crt` nach
`hall/zertifikate/`.

## 3. Home Assistant einrichten

1. **Benutzer und Token:** In HA einen eigenen Benutzer „beachhub“ anlegen (kein Administrator),
   mit ihm anmelden, unter *Profil → Sicherheit* einen Long-Lived Access Token erzeugen und als
   `HA_TOKEN` eintragen.
2. **Handbetrieb-Schalter:** *Einstellungen → Geräte & Dienste → Helfer → Schalter* mit dem Namen
   „beachhub_handbetrieb“ anlegen (`input_boolean.beachhub_handbetrieb`). Ist er an, schaltet der
   Dienst weder Licht noch Heizung; die Tür öffnet weiterhin mit gültiger PIN.
3. **Tastenfeld:** Das Tastenfeld muss nach der Eingabe ein HA-Ereignis mit dem Code als Text
   auslösen. Beispiel mit ESPHome (die tatsächliche Hardware klärt der Hallenhersteller):

   ```yaml
   matrix_keypad:
     id: tastenfeld
     rows: [{pin: GPIO21}, {pin: GPIO19}, {pin: GPIO18}, {pin: GPIO5}]
     columns: [{pin: GPIO17}, {pin: GPIO16}, {pin: GPIO4}]
     keys: "123456789*0#"
   key_collector:
     - id: pin_eingabe
       source_id: tastenfeld
       min_length: 4
       max_length: 12
       end_keys: "#"
       clear_keys: "*"
       timeout: 10s
       on_result:
         - homeassistant.event:
             event: esphome.beachhub_pin
             data:
               code: !lambda 'return x;'
   ```

   Im ESPHome-Gerät in HA muss „Gerät darf Home-Assistant-Aktionen ausführen“ eingeschaltet sein.
   Den Code immer als Text senden – als Zahl gingen führende Nullen verloren.
4. **Dashboard:** Eine Karte mit `sensor.beachhub_planversion`, `sensor.beachhub_letzter_kontakt`,
   `binary_sensor.beachhub_verbunden`, `sensor.beachhub_warteschlange` und dem Schalter
   `input_boolean.beachhub_handbetrieb`. Die Sensoren schreibt der Dienst selbst; nach einem
   HA-Neustart erscheinen sie innerhalb einer Minute wieder.
5. **Rückfall-Automation** (falls der Dienst ausfällt, Hauptspec § 8.2): Licht um 23:30 aus,
   wenn kein Handbetrieb läuft.

   ```yaml
   alias: Beachhub Rückfall – Licht aus nach Betriebsschluss
   mode: single
   triggers:
     - trigger: time
       at: "23:30:00"
   conditions:
     - condition: state
       entity_id: input_boolean.beachhub_handbetrieb
       state: "off"
   actions:
     - action: light.turn_off
       target:
         entity_id: [light.feld_1, light.feld_2, light.feld_3]
   ```

   Wichtig für die Heizung: Der Sollwert 0 °C muss am Heizgerät dem Frostschutz entsprechen,
   nicht „aus“ (A-HALLE-1).

## 4. Installation

```bash
cd hall
cp .env.example .env            # Werte aus Abschnitt 2 und 3 eintragen
cp hall.toml.example hall.toml  # Feld-UUIDs und Entitäten eintragen
docker compose run --rm hall beachhub-hall master-pin   # Hash in hall.toml eintragen
mkdir -p data && sudo chown 1000 data
docker compose up -d --build
docker compose logs -f hall
```

`GET http://127.0.0.1:8099/health` zeigt Planversion, HA-Verbindung und Länge der
Warteschlange.

## 5. Probelauf im eigenen Home Assistant

Ohne echte Hallentechnik lässt sich der Dienst gegen Test-Entitäten laufen lassen. In
`configuration.yaml` des Test-HA:

```yaml
input_boolean:
  demo_feld_1_licht: { name: Demo Feld 1 Licht }
  demo_praesenz_feld_1: { name: Demo Präsenz Feld 1 }
  demo_heizung: { name: Demo Heizung }
  demo_tuer: { name: Demo Türöffner }
  beachhub_handbetrieb: { name: Beachhub Handbetrieb }
input_number:
  demo_hallentemperatur: { name: Demo Hallentemperatur, min: -10, max: 30, step: 0.5 }

template:
  - light:
      - name: Demo Feld 1
        unique_id: demo_feld_1
        state: "{{ is_state('input_boolean.demo_feld_1_licht', 'on') }}"
        turn_on: { action: input_boolean.turn_on, target: { entity_id: input_boolean.demo_feld_1_licht } }
        turn_off: { action: input_boolean.turn_off, target: { entity_id: input_boolean.demo_feld_1_licht } }
    switch:
      - name: Demo Tueroeffner
        unique_id: demo_tueroeffner
        state: "{{ is_state('input_boolean.demo_tuer', 'on') }}"
        turn_on: { action: input_boolean.turn_on, target: { entity_id: input_boolean.demo_tuer } }
        turn_off: { action: input_boolean.turn_off, target: { entity_id: input_boolean.demo_tuer } }
    sensor:
      - name: Demo Hallentemperatur
        unique_id: demo_hallentemperatur
        unit_of_measurement: "°C"
        state: "{{ states('input_number.demo_hallentemperatur') }}"
    binary_sensor:
      - name: Demo Praesenz Feld 1
        unique_id: demo_praesenz_feld_1
        state: "{{ is_state('input_boolean.demo_praesenz_feld_1', 'on') }}"

climate:
  - platform: generic_thermostat
    name: Demo Halle
    heater: input_boolean.demo_heizung
    target_sensor: sensor.demo_hallentemperatur
```

Das ist die Template-Syntax ab HA 2025. Ältere Versionen schreiben Template-Lights und
-Switches als `light: - platform: template` bzw. `switch: - platform: template`.

Passende `hall.toml` (Feld-UUID aus dem eigenen Hauptsystem):

```toml
master_pin_hash = "…"   # beachhub-hall master-pin
[felder."<uuid-feld-1>"]
licht = "light.demo_feld_1"
praesenz = "binary_sensor.demo_praesenz_feld_1"
[heizung]
entity = "climate.demo_halle"
[tuer]
entity = "switch.demo_tueroeffner"
impuls_sekunden = 5
```

PIN-Eingaben lassen sich unter *Entwicklerwerkzeuge → Ereignisse* simulieren: Ereignistyp
`esphome.beachhub_pin`, Daten `code: "123456"`.

## 6. Störungen

| Anzeige | Bedeutung | Was tun |
|---|---|---|
| `binary_sensor.beachhub_verbunden` aus | Hauptsystem seit über 10 min nicht erreicht | WireGuard prüfen; die Halle arbeitet mit dem gespeicherten Plan weiter |
| Mail „Halle ohne Kontakt“ | Hauptsystem hört seit 60 min nichts | wie oben; neue Buchungen kennt die Halle erst nach der Rückkehr |
| Mail „Gerät in der Halle reagiert nicht“ | `aktor_fehler`: Dienstaufruf scheitert oder Gerät schaltet nicht | Entität in HA prüfen, Zuordnung in `hall.toml` prüfen |
| Mail „Home Assistant nicht erreichbar“ | Dienst erreicht HA seit 2 min nicht | HA-Status, `HA_URL` und `HA_TOKEN` prüfen |
| Mail „Halle hat den Plan verworfen“ | Signatur oder Version passt nicht | `CORE_PUBLIC_KEY` mit der System-Seite vergleichen |
| Dienst startet nicht: „master_pin_hash fehlt“ | Platzhalter in `hall.toml` | `beachhub-hall master-pin` ausführen und eintragen |
````

- [ ] **Step 5: Tests laufen lassen**

Run: `cd hall && pytest -q tests/test_beispielkonfiguration.py && pytest -q`
Expected: 2 passed, dann alle Hall-Tests PASS

- [ ] **Step 6: Commit**

```bash
git add hall/Dockerfile hall/docker-compose.yml hall/.env.example hall/hall.toml.example hall/README.md hall/tests/test_beispielkonfiguration.py docs/betrieb/hallendienst.md
git commit -m "docs(hall): Container, Beispielkonfiguration und HA-Einrichtung für den Hallendienst"
```

---
# Teil B – Hauptsystem (`core/`)

**Vor Task 13:** Der Portal-Kern-Plan muss nach `main` gemergt sein (Migration `0008`, `beachhub-core zertifikate`, `beachhub_shared.kanal.fuer_portal`). Prüfen mit:

```bash
ls core/alembic/versions/0008_*.py && grep -n "def fuer_portal" shared/beachhub_shared/kanal.py && beachhub-core zertifikate --help
```
Expected: alle drei Befehle finden etwas. Schlägt einer fehl, zuerst den Portal-Kern-Plan abschließen.

## Task 13: Hallenplan im Hauptsystem – Konfiguration, PIN-Parameter, signiertes Dokument

**Files:**
- Modify: `core/beachhub_core/services/konfiguration.py` (Vorgaben, neuer Schlüssel, Beschreibung, Markierung), `core/beachhub_core/services/pin.py` (Hash über `shared`, `salt()`, `parameter()`), `core/beachhub_core/services/lesestand.py` (`baue_hallenplan`, `_inhalt`, `markiere_geaendert`)
- Modify: `core/tests/test_lesestand.py` (erwartete Markierungen)
- Test: `core/tests/test_hallenplan.py`

**Interfaces:**
- Consumes: `beachhub_shared.hallenplan` (Task 1): `HallenplanInhalt`, `PlanFeld`, `PlanBuchung`, `PlanSperre`, `PlanKonfig`, `PinParameter`, `pin_hash`, `DOKUMENT`.
- Produces `konfiguration.HALLEN_KONFIG: tuple[str, ...]` (die sieben Schlüssel von `PlanKonfig`). Neue Vorgaben: `heiz_vorlauf_minuten=30`, `spiel_temperatur=Decimal("18.0")`, `grund_temperatur=Decimal("0.0")`, neu `praesenz_alarm_minuten=10`. `setze()` markiert `hallenplan` bei jedem Schlüssel aus `HALLEN_KONFIG`.
- Produces `pin.salt() -> bytes`, `pin.parameter() -> PinParameter`. `pin.hash(klar)` liefert **bitgleich** dasselbe wie bisher.
- Produces `lesestand.baue_hallenplan(db) -> HallenplanInhalt`: bestätigte Buchungen mit PIN-Hash und Sperren, die zwischen `jetzt` und `jetzt + 7 Tage` liegen, alle Felder, die Konfiguration und die PIN-Parameter; `gueltig_ab = jetzt`, `gueltig_bis = jetzt + 7 Tage`. `publiziere(db, "hallenplan")` funktioniert.
- `lesestand.markiere_geaendert(db, "belegung", …)` markiert zusätzlich `hallenplan`.

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_hallenplan.py`:
```python
import base64
import hashlib
from datetime import date, time
from decimal import Decimal
from pathlib import Path

import pytest
from argon2.low_level import Type, hash_secret_raw
from beachhub_core import clock
from beachhub_core.config import settings
from beachhub_core.models import (
    Betriebszeit,
    Buchung,
    Feld,
    FeldRaster,
    Kundengruppe,
    LesestandVersion,
    Sperre,
    Tarif,
)
from beachhub_core.services import buchungen, konfiguration, kunden, lesestand, pin, storno
from beachhub_shared.hallenplan import HallenplanInhalt, pin_hash
from beachhub_shared.zeit import kombiniere
from sqlalchemy.orm import Session


@pytest.fixture
def welt(db: Session):
    Path(settings.signatur_privatschluessel_pfad).unlink(missing_ok=True)
    lesestand.erzeuge_schluessel()
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    p = Kundengruppe(name="Privat")
    db.add_all([f, p, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    k = kunden.lege_an(db, name="A", email="a@x.de", kundengruppe_id=p.id)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    return f, k


def _buchung(db: Session, f: Feld, k, tag: date, von: int, bis: int, **kw) -> Buchung:
    return buchungen.lege_an(
        db, feld_id=f.id, kunde_id=k.id, beginn=kombiniere(tag, time(von)), ende=kombiniere(tag, time(bis)), **kw
    )


def test_neue_vorgaben_des_betreibers(db: Session) -> None:
    assert konfiguration.hole(db, "heiz_vorlauf_minuten") == 30
    assert konfiguration.hole(db, "spiel_temperatur") == Decimal("18.0")
    assert konfiguration.hole(db, "grund_temperatur") == Decimal("0.0")
    assert konfiguration.hole(db, "praesenz_alarm_minuten") == 10
    assert set(konfiguration.HALLEN_KONFIG) <= set(konfiguration.BESCHREIBUNGEN)


def test_pin_hash_ist_unveraendert() -> None:
    """Bestehende Buchungen tragen Hashes aus Stufe 1 – der Umbau auf shared darf keinen
    einzigen davon ungültig machen."""
    schluessel = hashlib.sha256(settings.pin_schluessel.encode()).digest()
    salt = hashlib.sha256(b"beachhub-pin-salt" + schluessel).digest()[:16]
    raw = hash_secret_raw(
        b"482913", salt, time_cost=2, memory_cost=65536, parallelism=1, hash_len=32, type=Type.ID
    )
    assert pin.hash("482913") == "argon2id$" + base64.b64encode(raw).decode()
    assert base64.b64decode(pin.parameter().salt_b64) == pin.salt() == salt


def test_plan_enthaelt_nur_bestaetigte_buchungen_der_naechsten_7_tage(db: Session, welt) -> None:
    f, k = welt
    rein = _buchung(db, f, k, date(2027, 11, 27), 19, 20, pin_klar="482913")
    _buchung(db, f, k, date(2027, 11, 27), 20, 21, status=Buchung.RESERVIERT)
    _buchung(db, f, k, date(2027, 12, 5), 19, 20)  # 10 Tage entfernt
    weg = _buchung(db, f, k, date(2027, 11, 28), 19, 20)
    storno.storniere(db, weg, durch="betreiber", kostenfrei=True)
    db.add(Sperre(feld_id=None, beginn=kombiniere(date(2027, 11, 29), time(9)), ende=kombiniere(date(2027, 11, 29), time(12)), grund="Turnier"))
    db.commit()
    inhalt = lesestand.baue_hallenplan(db)
    assert [b.buchung_id for b in inhalt.buchungen] == [str(rein.id)]
    assert inhalt.buchungen[0].pin_hash == pin.hash("482913") == rein.pin_hash
    assert len(inhalt.sperren) == 1 and inhalt.sperren[0].feld_id is None
    assert [x.id for x in inhalt.felder] == [str(f.id)]
    assert inhalt.konfig.heiz_vorlauf_minuten == 30 and inhalt.konfig.spiel_temperatur == Decimal("18.0")
    assert (inhalt.gueltig_bis - inhalt.gueltig_ab).days == 7
    assert "a@x.de" not in inhalt.model_dump_json() and '"A"' not in inhalt.model_dump_json()


def test_vertrag_pin_hash_halle_gleich_hauptsystem(db: Session, welt) -> None:
    """Die Halle prüft mit shared.pin_hash und den Parametern aus dem signierten Plan – das
    muss exakt den Hash ergeben, den das Hauptsystem an der Buchung speichert."""
    f, k = welt
    b = _buchung(db, f, k, date(2027, 11, 27), 19, 20, pin_klar="031415")
    db.commit()
    dok = lesestand.publiziere(db, "hallenplan")
    db.commit()
    assert dok.dokument == "hallenplan" and lesestand.pruefe(dok)
    inhalt = HallenplanInhalt.model_validate(dok.inhalt)
    assert pin_hash("031415", inhalt.pin) == b.pin_hash


def test_buchung_markiert_den_plan(db: Session, welt) -> None:
    f, k = welt
    lesestand.publiziere(db, "hallenplan")
    db.commit()
    assert db.get(LesestandVersion, "hallenplan").geaendert is False
    _buchung(db, f, k, date(2027, 11, 27), 19, 20)
    db.commit()
    assert db.get(LesestandVersion, "hallenplan").geaendert is True


def test_konfiguration_markiert_den_plan_nur_bei_hallenwerten(db: Session, welt) -> None:
    lesestand.publiziere(db, "hallenplan")
    db.commit()
    konfiguration.setze(db, "storno_frist_stunden", 48)
    db.commit()
    assert db.get(LesestandVersion, "hallenplan").geaendert is False
    konfiguration.setze(db, "licht_vorlauf_minuten", 10)
    db.commit()
    assert db.get(LesestandVersion, "hallenplan").geaendert is True
    assert lesestand.baue_hallenplan(db).konfig.licht_vorlauf_minuten == 10
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_hallenplan.py`
Expected: FAIL (u. a. `AttributeError: module 'beachhub_core.services.pin' has no attribute 'parameter'` und falsche Vorgabe `heiz_vorlauf_minuten == 60`)

- [ ] **Step 3: Konfiguration anpassen**

In `core/beachhub_core/services/konfiguration.py`:

Im Dict `DEFAULTS` die drei Zeilen ersetzen und eine ergänzen:
```python
    "heiz_vorlauf_minuten": (int, 30),
```
```python
    "spiel_temperatur": (Decimal, Decimal("18.0")),
    "grund_temperatur": (Decimal, Decimal("0.0")),
```
und direkt nach `"zutritt_vorlauf_minuten": (int, 15),` einfügen:
```python
    "praesenz_alarm_minuten": (int, 10),
```

Unter `DEFAULTS` einfügen:
```python
# Werte, die in den Plan der Halle eingehen (shared.hallenplan.PlanKonfig).
HALLEN_KONFIG: tuple[str, ...] = (
    "heiz_vorlauf_minuten",
    "spiel_temperatur",
    "grund_temperatur",
    "licht_vorlauf_minuten",
    "licht_nachlauf_minuten",
    "zutritt_vorlauf_minuten",
    "praesenz_alarm_minuten",
)
```

In `BESCHREIBUNGEN` die Einträge `grund_temperatur` ersetzen und nach `zutritt_vorlauf_minuten` einen neuen ergänzen:
```python
    "grund_temperatur": Beschreibung(
        "Halle",
        "Grundtemperatur",
        "Grad",
        "Temperatur außerhalb der Buchungen. 0 °C muss am Heizgerät dem Frostschutz entsprechen.",
    ),
```
```python
    "praesenz_alarm_minuten": Beschreibung(
        "Halle",
        "Alarm bei Anwesenheit ohne Buchung",
        "Minuten",
        "Meldet ein Sensor so lange Anwesenheit auf einem Feld ohne laufende Buchung, "
        "bekommen Sie eine E-Mail.",
    ),
```

Am Ende von `setze()` anhängen:
```python
    if schluessel in HALLEN_KONFIG:
        from beachhub_core.services import lesestand

        lesestand.markiere_geaendert(db, "hallenplan")
```

- [ ] **Step 4: PIN-Hash über `shared`**

`core/beachhub_core/services/pin.py`: Die Importe `from argon2.low_level import Type, hash_secret_raw` entfernen und `from beachhub_shared.hallenplan import PinParameter, pin_hash` ergänzen. Dann die Funktion `hash` ersetzen durch:
```python
def salt() -> bytes:
    """Hallenweites Salt, abgeleitet aus dem PIN-Schlüssel. Es geht mit dem Plan an die Halle;
    es erlaubt das Nachrechnen von Hashes, nicht das Entschlüsseln gespeicherter PINs."""
    return hashlib.sha256(b"beachhub-pin-salt" + _schluessel()).digest()[:16]


def parameter() -> PinParameter:
    # Eine Änderung dieser Werte machte alle gespeicherten PIN-Hashes ungültig.
    return PinParameter(
        salt_b64=base64.b64encode(salt()).decode(),
        time_cost=2,
        memory_cost=65536,
        parallelism=1,
        hash_len=32,
    )


def hash(klar: str) -> str:  # noqa: A001 – bewusst so benannt, wird als pin.hash() gelesen
    """Argon2id mit hallenweitem Salt: deterministisch, damit die Halle lokal prüfen kann."""
    return pin_hash(klar, parameter())
```

- [ ] **Step 5: Plan als Lesestand-Dokument**

In `core/beachhub_core/services/lesestand.py`:

Importe ergänzen:
```python
from beachhub_shared import hallenplan
```

`markiere_geaendert` ersetzen durch:
```python
def markiere_geaendert(db: Session, *namen: str) -> None:
    # Jede Änderung der Belegung (Buchung, Sperre, Feld) betrifft auch den Plan der Halle.
    if "belegung" in namen and hallenplan.DOKUMENT not in namen:
        namen = (*namen, hallenplan.DOKUMENT)
    for name in namen:
        zeile = db.get(LesestandVersion, name)
        if zeile is None:
            db.add(LesestandVersion(dokument=name, version=0, geaendert=True))
        else:
            zeile.geaendert = True
    db.flush()
```

Nach `baue_konto` einfügen:
```python
def baue_hallenplan(db: Session) -> hallenplan.HallenplanInhalt:
    """Betriebsplan der Halle für 7 Tage: nur bestätigte Buchungen (mit PIN-Hash), Sperren nur
    zur Information – sie schalten in der Halle nichts. Keine Personendaten."""
    jetzt = clock.now(db)
    bis = jetzt + timedelta(days=7)
    gebucht = db.scalars(
        select(Buchung)
        .where(
            Buchung.status == Buchung.BESTAETIGT,
            Buchung.beginn < bis,
            Buchung.ende > jetzt,
            Buchung.pin_hash.is_not(None),
        )
        .order_by(Buchung.beginn)
    ).all()
    gesperrt = db.scalars(
        select(Sperre).where(Sperre.beginn < bis, Sperre.ende > jetzt).order_by(Sperre.beginn)
    ).all()
    return hallenplan.HallenplanInhalt(
        gueltig_ab=jetzt,
        gueltig_bis=bis,
        felder=[
            hallenplan.PlanFeld(id=str(f.id), name=f.name, aktiv=f.aktiv)
            for f in db.scalars(select(Feld).order_by(Feld.reihenfolge))
        ],
        buchungen=[
            hallenplan.PlanBuchung(
                buchung_id=str(b.id),
                feld_id=str(b.feld_id),
                beginn=b.beginn,
                ende=b.ende,
                pin_hash=b.pin_hash or "",
            )
            for b in gebucht
        ],
        sperren=[
            hallenplan.PlanSperre(
                feld_id=str(s.feld_id) if s.feld_id else None, beginn=s.beginn, ende=s.ende
            )
            for s in gesperrt
        ],
        konfig=hallenplan.PlanKonfig(
            **{k: konfiguration.hole(db, k) for k in konfiguration.HALLEN_KONFIG}
        ),
        pin=pin.parameter(),
    )
```

In `_inhalt` vor `raise KeyError(name)` einfügen:
```python
    if name == hallenplan.DOKUMENT:
        return baue_hallenplan(db).model_dump(mode="json")
```

- [ ] **Step 6: Bestehenden Lesestand-Test anpassen**

In `core/tests/test_lesestand.py`, `test_aenderungen_markieren_und_verarbeiten`: Eine Buchung markiert jetzt auch den Hallenplan. Ersetze
```python
    assert markiert == {"belegung", f"konto:{a.id}"}
```
durch
```python
    assert markiert == {"belegung", "hallenplan", f"konto:{a.id}"}
```
und ersetze
```python
    assert db.query(LesestandVersion).filter_by(geaendert=True).count() == 2
```
durch
```python
    assert db.query(LesestandVersion).filter_by(geaendert=True).count() == 3
```

In `test_verarbeite_geaenderte_ueberspringt_fehlerhaftes_dokument` ersetze
```python
    assert lesestand.verarbeite_geaenderte(db) == ["belegung"]
```
durch
```python
    assert sorted(lesestand.verarbeite_geaenderte(db)) == ["belegung", "hallenplan"]
```

- [ ] **Step 7: Tests laufen lassen**

Run: `cd core && pytest -q && cd .. && ruff check . && ruff format --check . && mypy`
Expected: alle Core-Tests PASS (neu: 6 in `test_hallenplan.py`), keine Lint- oder Typfehler. Teil B wurde gegen den Stand von `main` vom 23.09. geprüft, also ohne den Portal-Kern: Außer den beiden angepassten Lesestand-Tests schlug nichts fehl. Scheitert nach dem Portal-Merge ein weiterer Test an einer zusätzlichen `hallenplan`-Markierung, gilt dieselbe Anpassung.

- [ ] **Step 8: Commit**

```bash
git add core/beachhub_core/services/konfiguration.py core/beachhub_core/services/pin.py core/beachhub_core/services/lesestand.py core/tests/test_hallenplan.py core/tests/test_lesestand.py
git commit -m "feat(core): Hallenplan als signiertes Dokument, Heizwerte des Betreibers, PIN-Hash über shared"
```

---

## Task 14: Schnittstelle `/hall/*` – Tabellen, Ereignisse, Status, Alarm-Mails

**Files:**
- Create: `core/beachhub_core/models/halle.py`, `core/alembic/versions/0009_halle.py`, `core/beachhub_core/services/halle.py`, `core/beachhub_core/routes/hall.py`
- Modify: `core/beachhub_core/models/__init__.py`, `core/beachhub_core/config.py` (`hall_token`), `core/beachhub_core/main.py` (Router), `core/.env.example`
- Test: `core/tests/test_hall_api.py`

**Interfaces:**
- Consumes: `lesestand.publiziere`, `lesestand.lade`, `LesestandVersion` (vorhanden), `baue_hallenplan` (Task 13), `benachrichtigung.betreiber_alarm` (vorhanden), `clock.now` (vorhanden), `EreignisLieferung`, `EreignisAntwort`, `HallenStatus`, `StatusAntwort`, `ALARM_TYPEN`, `DOKUMENT` (Task 1), `beachhub_shared.kanal.fuer_portal(name: str) -> bool` (aus dem Portal-Kern-Plan, Allowlist `belegung`, `tarife`, `konto:<uuid>`).
- Produces Modelle `Ereignis` (Tabelle `ereignis`: `id`, `quelle`, `typ`, `zeitpunkt`, `feld_id?`, `buchung_id?`, `daten_json`, `halle_dienst_id?`, `halle_seq?`, `created_at`, `updated_at`, unique `(halle_dienst_id, halle_seq)`) und `HallenStatusZeile` (Tabelle `hallen_status`: `id=1`, `daten_json?`, `empfangen_am`).
- Produces `beachhub_core.services.halle`:
  - `KONTAKT_MARKER = "halle_ohne_kontakt_seit"`, `ALARM_BETREFF: dict[str, str]`
  - `speichere_ereignisse(db, lieferung, jetzt) -> tuple[int, list[Ereignis]]` (bestätigt bis, neu gespeicherte)
  - `kontakt(db, jetzt, status: HallenStatus | None) -> str | None` (Text der Entwarnung, falls vorher ein Kontakt-Alarm lief)
  - `plan_neu(db, planversion: int | None) -> bool`
  - `alarm_mails(db, neu: list[Ereignis], jetzt) -> list[tuple[str, str]]` (Betreff, Text)
  - `pruefe_kontakt(db, jetzt) -> bool` (für Task 15)
- Produces Routen ohne CSRF und ohne Admin-Session, geschützt durch `Authorization: Bearer <HALL_TOKEN>` (leerer Token ergibt 404, falscher 401): `GET /hall/plan?ab=` (200 mit `Dokument` oder 304), `POST /hall/ereignisse` (`EreignisLieferung` → `EreignisAntwort`), `POST /hall/status` (`HallenStatus` → `StatusAntwort`).

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_hall_api.py`:
```python
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from beachhub_core import clock
from beachhub_core.config import settings
from beachhub_core.models import (
    AppSetting,
    Betriebszeit,
    Ereignis,
    Feld,
    FeldRaster,
    HallenStatusZeile,
    Kundengruppe,
    Tarif,
)
from beachhub_core.services import buchungen, halle, kunden, lesestand
from beachhub_shared.hallenplan import EreignisLieferung, HallenEreignis, HallenStatus
from beachhub_shared.lesestand import Dokument
from beachhub_shared.zeit import kombiniere
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

TOKEN = "hall-token-0123456789abcdef"
H = {"Authorization": f"Bearer {TOKEN}"}
DIENST = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


@pytest.fixture(autouse=True)
def token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "hall_token", TOKEN)


@pytest.fixture
def welt(db: Session):
    Path(settings.signatur_privatschluessel_pfad).unlink(missing_ok=True)
    lesestand.erzeuge_schluessel()
    f = Feld(name="Feld 1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    p = Kundengruppe(name="Privat")
    db.add_all([f, p, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    k = kunden.lege_an(db, name="A", email="a@x.de", kundengruppe_id=p.id)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    return f, k


def _status(version: int) -> HallenStatus:
    return HallenStatus(
        planversion=version, letzter_abruf=None, ha_erreichbar=True, handbetrieb=False, version_dienst="0.1.0"
    )


def _lieferung(jetzt: datetime, *seqs: int, typ: str = "licht_geschaltet", status: HallenStatus | None = None, dienst: uuid.UUID = DIENST) -> dict:
    return EreignisLieferung(
        dienst_id=dienst,
        ereignisse=[HallenEreignis(seq=s, typ=typ, zeitpunkt=jetzt) for s in seqs],
        status=status,
    ).model_dump(mode="json")


def test_token_pflicht(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    assert client.get("/hall/plan").status_code == 401
    assert client.get("/hall/plan", headers={"Authorization": "Bearer falsch"}).status_code == 401
    monkeypatch.setattr(settings, "hall_token", "")
    assert client.get("/hall/plan", headers=H).status_code == 404


def test_plan_ist_signiert_und_liefert_304(client: TestClient, db: Session, welt) -> None:
    f, k = welt
    buchungen.lege_an(db, feld_id=f.id, kunde_id=k.id, beginn=kombiniere(date(2027, 11, 27), time(19)), ende=kombiniere(date(2027, 11, 27), time(20)))
    db.commit()
    r = client.get("/hall/plan?ab=0", headers=H)
    assert r.status_code == 200
    dok = Dokument.model_validate(r.json())
    assert dok.dokument == "hallenplan" and lesestand.pruefe(dok)
    assert len(dok.inhalt["buchungen"]) == 1
    assert client.get(f"/hall/plan?ab={dok.version}", headers=H).status_code == 304
    buchungen.lege_an(db, feld_id=f.id, kunde_id=k.id, beginn=kombiniere(date(2027, 11, 28), time(19)), ende=kombiniere(date(2027, 11, 28), time(20)))
    db.commit()
    r2 = client.get(f"/hall/plan?ab={dok.version}", headers=H)
    assert r2.status_code == 200 and r2.json()["version"] == dok.version + 1


def test_plan_ohne_schluessel_503(client: TestClient, welt) -> None:
    Path(settings.signatur_privatschluessel_pfad).unlink()
    assert client.get("/hall/plan", headers=H).status_code == 503


def test_ereignisse_idempotent_je_dienst(client: TestClient, db: Session, welt) -> None:
    jetzt = clock.now(db)
    r = client.post("/hall/ereignisse", headers=H, json=_lieferung(jetzt, 1, 2))
    assert r.status_code == 200 and r.json()["bestaetigt_bis"] == 2
    r = client.post("/hall/ereignisse", headers=H, json=_lieferung(jetzt, 1, 2, 3))
    assert r.json()["bestaetigt_bis"] == 3
    assert db.query(Ereignis).count() == 3
    anderer = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    r = client.post("/hall/ereignisse", headers=H, json=_lieferung(jetzt, 1, dienst=anderer))
    assert r.json()["bestaetigt_bis"] == 1
    assert db.query(Ereignis).count() == 4
    leer = client.post("/hall/ereignisse", headers=H, json=_lieferung(jetzt))
    assert leer.json()["bestaetigt_bis"] == 3


def test_ereignis_mit_unbekannter_feld_id_wird_trotzdem_gespeichert(client: TestClient, db: Session, welt) -> None:
    lieferung = EreignisLieferung(
        dienst_id=DIENST,
        ereignisse=[HallenEreignis(seq=1, typ="aktor_fehler", zeitpunkt=clock.now(db), feld_id="kein-uuid", daten={"grund": "x"})],
    )
    assert client.post("/hall/ereignisse", headers=H, json=lieferung.model_dump(mode="json")).status_code == 200
    e = db.query(Ereignis).one()
    assert e.feld_id is None and e.daten_json == {"grund": "x", "feld_id_unbekannt": "kein-uuid"}


def test_alarm_mails_frisch_einzeln_nachgeliefert_gesammelt(client: TestClient, db: Session, welt, mail_ausgang: list) -> None:
    jetzt = clock.now(db)
    client.post("/hall/ereignisse", headers=H, json=_lieferung(jetzt, 1, typ="tastenfeld_fehlversuche"))
    assert [m["betreff"] for m in mail_ausgang] == ["[Beachhub] Tastenfeld: mehrere falsche Codes"]
    alt = jetzt - timedelta(hours=7)
    client.post("/hall/ereignisse", headers=H, json=_lieferung(alt, 2, 3, typ="aktor_fehler"))
    assert mail_ausgang[-1]["betreff"] == "[Beachhub] 2 nachgelieferte Meldungen der Halle"
    client.post("/hall/ereignisse", headers=H, json=_lieferung(jetzt, 4, typ="licht_geschaltet"))
    assert len(mail_ausgang) == 2


def test_plan_neu_und_status(client: TestClient, db: Session, welt) -> None:
    jetzt = clock.now(db)
    r = client.post("/hall/ereignisse", headers=H, json=_lieferung(jetzt, status=_status(0)))
    assert r.json()["plan_neu"] is True
    version = client.get("/hall/plan", headers=H).json()["version"]
    r = client.post("/hall/status", headers=H, json=_status(version).model_dump(mode="json"))
    assert r.status_code == 200 and r.json() == {"plan_neu": False}
    zeile = db.get(HallenStatusZeile, 1)
    db.refresh(zeile)
    assert zeile.daten_json["planversion"] == version


def test_entwarnung_nach_kontakt_alarm(client: TestClient, db: Session, welt, mail_ausgang: list) -> None:
    jetzt = clock.now(db)
    db.add(AppSetting(key=halle.KONTAKT_MARKER, value=(jetzt - timedelta(hours=3)).isoformat()))
    db.commit()
    client.post("/hall/ereignisse", headers=H, json=_lieferung(jetzt, status=_status(0)))
    assert mail_ausgang[-1]["betreff"] == "[Beachhub] Halle wieder verbunden"
    assert "3 h 0 min" in mail_ausgang[-1]["text"]
    db.expire_all()
    assert db.get(AppSetting, halle.KONTAKT_MARKER) is None


def test_hallenplan_geht_nicht_ans_portal() -> None:
    from beachhub_shared.kanal import fuer_portal

    assert fuer_portal("hallenplan") is False
    assert fuer_portal("belegung") is True
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_hall_api.py`
Expected: FAIL mit `ImportError: cannot import name 'Ereignis' from 'beachhub_core.models'`

- [ ] **Step 3: Modelle und Migration**

`core/beachhub_core/models/halle.py`:
```python
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from beachhub_core.models.base import Base, UUIDMixin, ZeitstempelMixin


class Ereignis(UUIDMixin, ZeitstempelMixin, Base):
    """Ereignisse aus der Halle (und später aus Portal und Admin). Die Halle zählt ihre
    Ereignisse mit seq je Dienst-ID; das Paar ist eindeutig und macht Nachlieferungen
    idempotent."""

    __tablename__ = "ereignis"
    __table_args__ = (
        UniqueConstraint("halle_dienst_id", "halle_seq", name="ereignis_halle_seq_eindeutig"),
    )
    quelle: Mapped[str] = mapped_column(String(10), nullable=False)  # halle|portal|admin|system
    typ: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    zeitpunkt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    # Ohne Fremdschlüssel: Die Halle meldet, was sie kennt; ein Ereignis darf nie verloren
    # gehen, nur weil ein Bezug unbekannt ist.
    feld_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    buchung_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    daten_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    halle_dienst_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    halle_seq: Mapped[int | None] = mapped_column(Integer)


class HallenStatusZeile(Base):
    """Zuletzt gemeldeter Status der Halle – genau eine Zeile (id = 1)."""

    __tablename__ = "hallen_status"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    daten_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    empfangen_am: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

`core/beachhub_core/models/__init__.py`: Import `from beachhub_core.models.halle import Ereignis, HallenStatusZeile` ergänzen und beide Namen alphabetisch in `__all__` eintragen.

`core/alembic/versions/0009_halle.py`:
```python
"""halle: Ereignisse und Status des Hallendienstes

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ereignis",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("quelle", sa.String(length=10), nullable=False),
        sa.Column("typ", sa.String(length=40), nullable=False),
        sa.Column("zeitpunkt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("feld_id", sa.UUID(), nullable=True),
        sa.Column("buchung_id", sa.UUID(), nullable=True),
        sa.Column("daten_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("halle_dienst_id", sa.UUID(), nullable=True),
        sa.Column("halle_seq", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("halle_dienst_id", "halle_seq", name="ereignis_halle_seq_eindeutig"),
    )
    op.create_index("ix_ereignis_typ", "ereignis", ["typ"])
    op.create_index("ix_ereignis_zeitpunkt", "ereignis", ["zeitpunkt"])
    op.create_table(
        "hallen_status",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("daten_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("empfangen_am", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("hallen_status")
    op.drop_index("ix_ereignis_zeitpunkt", table_name="ereignis")
    op.drop_index("ix_ereignis_typ", table_name="ereignis")
    op.drop_table("ereignis")
```

`core/beachhub_core/config.py`, in `Settings` nach `enable_scheduler` ergänzen:
```python
    hall_token: str = ""  # leer = Hallenschnittstelle abgeschaltet (404)
```

`core/.env.example`, anhängen:
```
# Hallendienst: langer Zufallswert, identisch mit HALL_TOKEN in hall/.env; leer = aus
HALL_TOKEN=
```

- [ ] **Step 4: Dienst `halle`**

`core/beachhub_core/services/halle.py`:
```python
"""Ereignisse und Status der Halle, Alarme an den Betreiber (Hallendienst-Spec § 2)."""

import uuid
from datetime import datetime, timedelta

from beachhub_shared.hallenplan import ALARM_TYPEN, DOKUMENT, EreignisLieferung, HallenStatus
from beachhub_shared.zeit import lokal
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from beachhub_core.models import AppSetting, Ereignis, Feld, HallenStatusZeile, LesestandVersion

KONTAKT_MARKER = "halle_ohne_kontakt_seit"
KONTAKT_GRENZE = timedelta(minutes=60)
NACHLIEFERUNG = timedelta(hours=6)

ALARM_BETREFF: dict[str, str] = {
    "tastenfeld_fehlversuche": "Tastenfeld: mehrere falsche Codes",
    "praesenz_ohne_buchung": "Anwesenheit ohne Buchung",
    "aktor_fehler": "Gerät in der Halle reagiert nicht",
    "ha_nicht_erreichbar": "Home Assistant nicht erreichbar",
    "plan_verworfen": "Halle hat den Plan verworfen",
    "tuer_offen_ausserhalb": "Tür außerhalb der Buchungszeiten geöffnet",
}


def _uuid(wert: str | None) -> uuid.UUID | None:
    if not wert:
        return None
    try:
        return uuid.UUID(wert)
    except ValueError:
        return None


def _zeit(t: datetime) -> str:
    return lokal(t).strftime("%d.%m.%Y %H:%M")


def speichere_ereignisse(
    db: Session, lieferung: EreignisLieferung, jetzt: datetime
) -> tuple[int, list[Ereignis]]:
    seqs = [e.seq for e in lieferung.ereignisse]
    vorhanden = (
        set(
            db.scalars(
                select(Ereignis.halle_seq).where(
                    Ereignis.halle_dienst_id == lieferung.dienst_id, Ereignis.halle_seq.in_(seqs)
                )
            ).all()
        )
        if seqs
        else set()
    )
    neu: list[Ereignis] = []
    for e in sorted(lieferung.ereignisse, key=lambda x: x.seq):
        if e.seq in vorhanden:
            continue
        vorhanden.add(e.seq)
        daten = dict(e.daten)
        if e.feld_id and _uuid(e.feld_id) is None:
            daten.setdefault("feld_id_unbekannt", e.feld_id)
        zeile = Ereignis(
            quelle="halle",
            typ=e.typ,
            zeitpunkt=e.zeitpunkt,
            feld_id=_uuid(e.feld_id),
            buchung_id=_uuid(e.buchung_id),
            daten_json=daten,
            halle_dienst_id=lieferung.dienst_id,
            halle_seq=e.seq,
        )
        db.add(zeile)
        neu.append(zeile)
    db.flush()
    hoechste = db.scalar(
        select(func.max(Ereignis.halle_seq)).where(Ereignis.halle_dienst_id == lieferung.dienst_id)
    )
    return int(hoechste or 0), neu


def kontakt(db: Session, jetzt: datetime, status: HallenStatus | None) -> str | None:
    zeile = db.get(HallenStatusZeile, 1)
    if zeile is None:
        zeile = HallenStatusZeile(id=1, daten_json=None, empfangen_am=jetzt)
        db.add(zeile)
    if status is not None:
        zeile.daten_json = status.model_dump(mode="json")
    zeile.empfangen_am = jetzt
    marker = db.get(AppSetting, KONTAKT_MARKER)
    if marker is None or not marker.value:
        return None
    seit = datetime.fromisoformat(marker.value)
    db.delete(marker)
    dauer = jetzt - seit
    stunden, rest = divmod(int(dauer.total_seconds()) // 60, 60)
    return (
        f"Die Halle meldet sich wieder. Letzter Kontakt davor: {_zeit(seit)} Uhr "
        f"(ohne Kontakt: {stunden} h {rest} min). Ereignisse aus dieser Zeit werden nachgeliefert."
    )


def plan_neu(db: Session, planversion: int | None) -> bool:
    if planversion is None:
        return False
    zeile = db.get(LesestandVersion, DOKUMENT)
    return zeile is None or zeile.geaendert or planversion < zeile.version


def _zeile(e: Ereignis, felder: dict[uuid.UUID, str]) -> str:
    teile = [f"{_zeit(e.zeitpunkt)} Uhr", ALARM_BETREFF[e.typ]]
    if e.feld_id is not None and e.feld_id in felder:
        teile.append(f"Feld {felder[e.feld_id]}")
    if e.daten_json:
        teile.append(", ".join(f"{k}: {v}" for k, v in sorted(e.daten_json.items())))
    return " – ".join(teile)


def alarm_mails(db: Session, neu: list[Ereignis], jetzt: datetime) -> list[tuple[str, str]]:
    alarme = [e for e in neu if e.typ in ALARM_TYPEN]
    if not alarme:
        return []
    felder = {f.id: f.name for f in db.scalars(select(Feld))}
    hinweis = "\n\nDetails im Verwaltungsbereich unter System → Halle."
    frisch = [e for e in alarme if jetzt - e.zeitpunkt <= NACHLIEFERUNG]
    alt = [e for e in alarme if jetzt - e.zeitpunkt > NACHLIEFERUNG]
    mails = [(ALARM_BETREFF[e.typ], _zeile(e, felder) + hinweis) for e in frisch]
    if alt:
        mails.append(
            (
                f"{len(alt)} nachgelieferte Meldungen der Halle",
                "Nach einer Unterbrechung hat die Halle diese Meldungen nachgeliefert:\n\n"
                + "\n".join(_zeile(e, felder) for e in alt)
                + hinweis,
            )
        )
    return mails


def pruefe_kontakt(db: Session, jetzt: datetime) -> bool:
    """Job alle 5 min: meldet einmal, wenn die Halle seit 60 min schweigt (A-HALLE-8).
    Hat sich die Halle noch nie gemeldet, ist sie noch nicht eingerichtet – kein Alarm."""
    zeile = db.get(HallenStatusZeile, 1)
    if zeile is None or jetzt - zeile.empfangen_am <= KONTAKT_GRENZE:
        return False
    if db.get(AppSetting, KONTAKT_MARKER) is not None:
        return False
    db.add(AppSetting(key=KONTAKT_MARKER, value=zeile.empfangen_am.isoformat()))
    db.commit()
    from beachhub_core.services import benachrichtigung

    benachrichtigung.betreiber_alarm(
        "Halle ohne Kontakt",
        f"Die Halle hat sich seit {_zeit(zeile.empfangen_am)} Uhr nicht mehr gemeldet. "
        "Sie arbeitet mit ihrem gespeicherten Plan weiter; neue Buchungen kennt sie erst nach "
        "der Rückkehr. Bitte WireGuard-Verbindung und Hallenrechner prüfen.",
    )
    return True
```

- [ ] **Step 5: Routen**

`core/beachhub_core/routes/hall.py`:
```python
"""Schnittstelle für den Hallendienst (Hauptspec § 8.2). Der Hallendienst ruft, das
Hauptsystem antwortet – nie umgekehrt. Caddy erzwingt mTLS, hier zusätzlich ein Token."""

import hmac
from typing import Annotated

from beachhub_shared.hallenplan import (
    DOKUMENT,
    EreignisAntwort,
    EreignisLieferung,
    HallenStatus,
    StatusAntwort,
)
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from beachhub_core import clock
from beachhub_core.config import settings
from beachhub_core.database import get_db
from beachhub_core.models import LesestandVersion
from beachhub_core.services import benachrichtigung, halle, lesestand


def pruefe_token(authorization: Annotated[str, Header()] = "") -> None:
    if not settings.hall_token:
        raise HTTPException(status_code=404)
    erwartet = f"Bearer {settings.hall_token}"
    if not hmac.compare_digest(authorization.encode(), erwartet.encode()):
        raise HTTPException(status_code=401, detail="Token ungültig")


router = APIRouter(prefix="/hall", dependencies=[Depends(pruefe_token)])


@router.get("/plan")
def plan(ab: int = 0, db: Session = Depends(get_db)) -> Response:
    zeile = db.get(LesestandVersion, DOKUMENT)
    dok = lesestand.lade(DOKUMENT)
    if zeile is None or zeile.geaendert or dok is None or dok.version != zeile.version:
        try:
            dok = lesestand.publiziere(db, DOKUMENT)
            db.commit()
        except FileNotFoundError as e:
            db.rollback()
            raise HTTPException(status_code=503, detail="Signaturschlüssel fehlt") from e
    if ab == dok.version:
        return Response(status_code=304)
    return JSONResponse(dok.model_dump(mode="json"))


@router.post("/ereignisse")
def ereignisse(lieferung: EreignisLieferung, db: Session = Depends(get_db)) -> EreignisAntwort:
    jetzt = clock.now(db)
    bis, neu = halle.speichere_ereignisse(db, lieferung, jetzt)
    entwarnung = halle.kontakt(db, jetzt, lieferung.status)
    mails = halle.alarm_mails(db, neu, jetzt)
    antwort = EreignisAntwort(
        bestaetigt_bis=bis,
        plan_neu=halle.plan_neu(db, lieferung.status.planversion if lieferung.status else None),
    )
    db.commit()
    for betreff, text in mails:
        benachrichtigung.betreiber_alarm(betreff, text)
    if entwarnung:
        benachrichtigung.betreiber_alarm("Halle wieder verbunden", entwarnung)
    return antwort


@router.post("/status")
def status(daten: HallenStatus, db: Session = Depends(get_db)) -> StatusAntwort:
    jetzt = clock.now(db)
    entwarnung = halle.kontakt(db, jetzt, daten)
    antwort = StatusAntwort(plan_neu=halle.plan_neu(db, daten.planversion))
    db.commit()
    if entwarnung:
        benachrichtigung.betreiber_alarm("Halle wieder verbunden", entwarnung)
    return antwort
```

`core/beachhub_core/main.py`: `hall` in den Import aus `beachhub_core.routes` aufnehmen und nach den Admin-Routern eintragen, **ohne** CSRF-Abhängigkeit:
```python
app.include_router(hall.router)  # eigener Token statt Admin-Session/CSRF
```

- [ ] **Step 6: Tests laufen lassen**

Run: `cd core && pytest -q && cd .. && ruff check . && ruff format --check . && mypy`
Expected: alle Core-Tests PASS (neu: 9 in `test_hall_api.py`, `test_migrationen.py` findet die neuen Tabellen), keine Lint- oder Typfehler

- [ ] **Step 7: Commit**

```bash
git add core/beachhub_core/models/halle.py core/beachhub_core/models/__init__.py core/alembic/versions/0009_halle.py core/beachhub_core/services/halle.py core/beachhub_core/routes/hall.py core/beachhub_core/main.py core/beachhub_core/config.py core/.env.example core/tests/test_hall_api.py
git commit -m "feat(core): Schnittstelle /hall für Plan, Ereignisse und Status mit Betreiber-Alarmen"
```

---
## Task 15: Kontakt-Alarm, nächtlicher Plan und Admin-Seite „Halle“

**Files:**
- Modify: `core/beachhub_core/jobs.py` (zwei Jobs), `core/beachhub_core/navigation.py` (Punkt „Halle“ unter System), `core/beachhub_core/routes/system.py` (Route `/halle`), `core/tests/test_ui_navigation.py` (`SEITEN`)
- Create: `core/beachhub_core/templates/system/halle.html`
- Test: `core/tests/test_halle_jobs.py`, `core/tests/test_ui_halle.py`

**Interfaces:**
- Consumes: `halle.pruefe_kontakt`, `halle.KONTAKT_MARKER`, `Ereignis`, `HallenStatusZeile` (Task 14), `lesestand.markiere_geaendert`, `lesestand.verarbeite_geaenderte` (vorhanden), `HallenStatus`, `EREIGNISTYPEN`, `DOKUMENT` (Task 1).
- Produces `jobs.hallenplan_nachts(db) -> None` sowie die Scheduler-Jobs `halle_kontakt` (alle 5 min) und `hallenplan_nachts` (täglich 00:05, Europe/Berlin).
- Produces Admin-Route `GET /admin/halle?typ=` (beide Rollen) mit Template `system/halle.html` und den Navigationspunkt `Punkt("Halle", "/admin/halle")` im Bereich System.

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_halle_jobs.py`:
```python
from datetime import timedelta
from pathlib import Path

from beachhub_core import clock, jobs
from beachhub_core.config import settings
from beachhub_core.models import AppSetting, HallenStatusZeile, LesestandVersion
from beachhub_core.services import halle, lesestand
from sqlalchemy.orm import Session


def test_kontakt_alarm_einmal_und_nur_nach_erstkontakt(db: Session, mail_ausgang: list) -> None:
    jetzt = clock.now(db)
    assert halle.pruefe_kontakt(db, jetzt) is False  # Halle noch nie gemeldet
    db.add(HallenStatusZeile(id=1, daten_json=None, empfangen_am=jetzt - timedelta(minutes=59)))
    db.commit()
    assert halle.pruefe_kontakt(db, jetzt) is False
    assert halle.pruefe_kontakt(db, jetzt + timedelta(minutes=2)) is True
    assert [m["betreff"] for m in mail_ausgang] == ["[Beachhub] Halle ohne Kontakt"]
    assert halle.pruefe_kontakt(db, jetzt + timedelta(minutes=30)) is False
    assert len(mail_ausgang) == 1
    assert db.get(AppSetting, halle.KONTAKT_MARKER) is not None


def test_plan_wird_nachts_neu_signiert(db: Session) -> None:
    Path(settings.signatur_privatschluessel_pfad).unlink(missing_ok=True)
    lesestand.erzeuge_schluessel()
    lesestand.publiziere(db, "hallenplan")
    db.commit()
    jobs.hallenplan_nachts(db)
    zeile = db.get(LesestandVersion, "hallenplan")
    db.refresh(zeile)
    assert zeile.version == 2 and zeile.geaendert is False


def test_scheduler_kennt_die_hallenjobs() -> None:
    s = jobs.starte_scheduler()
    try:
        assert s.get_job("halle_kontakt") is not None
        assert s.get_job("hallenplan_nachts") is not None
    finally:
        jobs.stoppe_scheduler()
```

`core/tests/test_ui_halle.py`:
```python
import uuid
from datetime import timedelta

from beachhub_core import clock
from beachhub_core.models import Ereignis, Feld, HallenStatusZeile, LesestandVersion
from beachhub_shared.hallenplan import FeldStatus, HallenStatus, HeizungStatus
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def test_halle_ohne_meldung(eingeloggt: TestClient) -> None:
    r = eingeloggt.get("/admin/halle")
    assert r.status_code == 200
    assert "Die Halle hat sich noch nie gemeldet" in r.text


def test_halle_mit_status_und_ereignissen(eingeloggt: TestClient, db: Session) -> None:
    jetzt = clock.now(db)
    f = Feld(name="Feld 1", reihenfolge=1)
    db.add(f)
    db.flush()
    status = HallenStatus(
        planversion=3,
        letzter_abruf=jetzt,
        ha_erreichbar=True,
        handbetrieb=True,
        felder=[FeldStatus(feld_id=str(f.id), licht_ist=True, praesenz=False)],
        heizung=HeizungStatus(soll="18.0", ist="12.5"),
        warteschlange=4,
        version_dienst="0.1.0",
    )
    db.add(HallenStatusZeile(id=1, daten_json=status.model_dump(mode="json"), empfangen_am=jetzt - timedelta(minutes=90)))
    db.add(LesestandVersion(dokument="hallenplan", version=5, geaendert=False))
    dienst = uuid.uuid4()
    db.add(Ereignis(quelle="halle", typ="pin_akzeptiert", zeitpunkt=jetzt, feld_id=f.id, daten_json={}, halle_dienst_id=dienst, halle_seq=1))
    db.add(Ereignis(quelle="halle", typ="aktor_fehler", zeitpunkt=jetzt, daten_json={"entity": "light.feld_1"}, halle_dienst_id=dienst, halle_seq=2))
    db.commit()
    text = eingeloggt.get("/admin/halle").text
    assert "Die Halle hat noch nicht den aktuellen Plan" in text and "Version 3" in text
    assert "seit über 60 Minuten" in text
    assert "Handbetrieb" in text and "Feld 1" in text and "12,5" in text
    assert "pin_akzeptiert" in text and "light.feld_1" in text
    gefiltert = eingeloggt.get("/admin/halle?typ=aktor_fehler").text
    assert "light.feld_1" in gefiltert and "pin_akzeptiert</td>" not in gefiltert
```

In `core/tests/test_ui_navigation.py` in der Liste `SEITEN` nach `"/admin/system/audit",` ergänzen:
```python
    "/admin/halle",
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_halle_jobs.py tests/test_ui_halle.py tests/test_ui_navigation.py`
Expected: FAIL (`AttributeError: module 'beachhub_core.jobs' has no attribute 'hallenplan_nachts'`, 404 auf `/admin/halle`)

- [ ] **Step 3: Jobs**

In `core/beachhub_core/jobs.py`:

Import-Zeile der Dienste ersetzen durch
```python
from beachhub_core.services import (
    benachrichtigung,
    halle,
    konfiguration,
    lesestand,
    rechnung_pdf,
    rechnungen,
)
```
(die Zeile `from beachhub_core.services import lesestand  # Task 19` in `_job_lesestand` entfällt dann).

Vor `starte_scheduler` einfügen:
```python
def hallenplan_nachts(db: Session) -> None:
    """Das 7-Tage-Fenster wandert jede Nacht einen Tag weiter. Ohne neue Version fehlte der
    Halle nach und nach der letzte Tag, auch wenn sich keine Buchung ändert."""
    lesestand.markiere_geaendert(db, "hallenplan")
    db.commit()
    lesestand.verarbeite_geaenderte(db)


def _job_hallenplan() -> None:
    with SessionLocal() as db:
        try:
            hallenplan_nachts(db)
        except Exception:
            logger.exception("Nächtlicher Hallenplan fehlgeschlagen")


def _job_halle_kontakt() -> None:
    with SessionLocal() as db:
        try:
            halle.pruefe_kontakt(db, clock.now(db))
        except Exception:
            logger.exception("Prüfung des Hallenkontakts fehlgeschlagen")
```

In `starte_scheduler` vor `s.start()` ergänzen:
```python
    s.add_job(_job_halle_kontakt, IntervalTrigger(minutes=5), id="halle_kontakt", replace_existing=True)
    s.add_job(
        _job_hallenplan, CronTrigger(hour=0, minute=5), id="hallenplan_nachts", replace_existing=True
    )
```

- [ ] **Step 4: Admin-Seite**

`core/beachhub_core/navigation.py`: im Bereich `System` nach `Punkt("Änderungsprotokoll", "/admin/system/audit"),` ergänzen:
```python
            Punkt("Halle", "/admin/halle"),
```

`core/beachhub_core/routes/system.py`: Importe ergänzen
```python
from datetime import timedelta

from beachhub_shared.hallenplan import DOKUMENT, EREIGNISTYPEN, HallenStatus

from beachhub_core.models import Ereignis, Feld, HallenStatusZeile
```
(die bestehende Zeile `from beachhub_core.models import …` um `Ereignis, Feld, HallenStatusZeile` erweitern, statt eine zweite anzulegen) und am Ende anfügen:
```python
@router.get("/halle", response_class=HTMLResponse)
def halle_seite(
    request: Request,
    typ: str = "",
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    zeile = db.get(HallenStatusZeile, 1)
    status = HallenStatus.model_validate(zeile.daten_json) if zeile and zeile.daten_json else None
    plan = db.get(LesestandVersion, DOKUMENT)
    stmt = (
        select(Ereignis)
        .where(Ereignis.quelle == "halle")
        .order_by(Ereignis.zeitpunkt.desc(), Ereignis.halle_seq.desc())
        .limit(100)
    )
    if typ:
        stmt = stmt.where(Ereignis.typ == typ)
    return render(
        request,
        "system/halle.html",
        admin=admin,
        status=status,
        empfangen_am=zeile.empfangen_am if zeile else None,
        ohne_kontakt=zeile is not None
        and clock.now(db) - zeile.empfangen_am > timedelta(minutes=60),
        aktuelle_version=plan.version if plan else 0,
        ereignisse=db.scalars(stmt).all(),
        felder={str(f.id): f.name for f in db.scalars(select(Feld))},
        typ=typ,
        typen=sorted(EREIGNISTYPEN),
    )
```

`core/beachhub_core/templates/system/halle.html`:
```html
{% extends "base.html" %}{% block title %}Halle{% endblock %}
{% block content %}
<h1>Halle</h1>

{% if empfangen_am is none %}
<div class="karte schmal">
  <p class="hinweis">Die Halle hat sich noch nie gemeldet. Sobald der Hallendienst läuft und
  verbunden ist, erscheint hier sein Zustand.</p>
</div>
{% else %}
<div class="karte schmal">
  <h2>Verbindung</h2>
  <p>Letzter Kontakt: {{ empfangen_am|lokal }}
  {% if ohne_kontakt %}<span class="fehler"> – seit über 60 Minuten keine Meldung</span>{% endif %}</p>
  {% if status %}
  <p>Plan der Halle: Version {{ status.planversion }} · aktuell: Version {{ aktuelle_version }}</p>
  {% if status.planversion < aktuelle_version %}
  <p class="fehler">Die Halle hat noch nicht den aktuellen Plan – neue Buchungen seit Version
  {{ status.planversion }} kennt sie nicht. Betroffenen Kunden bei Bedarf mit dem Master-PIN
  Zutritt verschaffen.</p>
  {% endif %}
  <p>Home Assistant: {{ "erreichbar" if status.ha_erreichbar else "nicht erreichbar" }}
  · {{ "Handbetrieb ist an" if status.handbetrieb else "Automatik" }}
  · nicht zugestellte Ereignisse: {{ status.warteschlange }}
  · Dienst {{ status.version_dienst }}</p>
  <p class="hinweis">Den Handbetrieb schalten Sie in Home Assistant, nicht hier.</p>
  {% endif %}
</div>

{% if status %}
<div class="karte schmal">
  <h2>Geräte</h2>
  <table>
    <tr><th>Feld</th><th>Licht</th><th>Anwesenheit</th></tr>
    {% for f in status.felder %}<tr>
      <td>{{ felder.get(f.feld_id, f.feld_id) }}</td>
      <td>{{ "an" if f.licht_ist else ("aus" if f.licht_ist is not none else "–") }}</td>
      <td>{{ "ja" if f.praesenz else ("nein" if f.praesenz is not none else "–") }}</td>
    </tr>{% endfor %}
  </table>
  <p>Heizung: Soll {{ status.heizung.soll|wert if status.heizung.soll is not none else "–" }} °C
  · Ist {{ status.heizung.ist|wert if status.heizung.ist is not none else "–" }} °C</p>
  <p>Tür: {{ "verriegelt" if status.tuer.verriegelt else ("entriegelt" if status.tuer.verriegelt is not none else "–") }}
  {% if status.tuer.offen %} · offen{% endif %}</p>
</div>
{% endif %}
{% endif %}

<div class="karte">
  <h2>Ereignisse</h2>
  <form method="get" action="/admin/halle" class="zeile">
    <label>Typ
      <select name="typ">
        <option value="">alle</option>
        {% for t in typen %}<option value="{{ t }}"{% if t == typ %} selected{% endif %}>{{ t }}</option>{% endfor %}
      </select>
    </label>
    <button>Filtern</button>
  </form>
  <table>
    <tr><th>Zeit</th><th>Typ</th><th>Feld</th><th>Details</th></tr>
    {% for e in ereignisse %}<tr>
      <td>{{ e.zeitpunkt|lokal }}</td>
      <td>{{ e.typ }}</td>
      <td>{{ felder.get(e.feld_id|string, "–") if e.feld_id else "–" }}</td>
      <td>{% for k, v in e.daten_json|dictsort %}{{ k }}: {{ v }}{% if not loop.last %}, {% endif %}{% endfor %}</td>
    </tr>{% else %}
    <tr><td colspan="4" class="leer">Keine Ereignisse.</td></tr>
    {% endfor %}
  </table>
</div>
{% endblock %}
```

- [ ] **Step 5: Tests laufen lassen**

Run: `cd core && pytest -q && cd .. && ruff check . && ruff format --check . && mypy`
Expected: alle Core-Tests PASS (neu: 3 in `test_halle_jobs.py`, 2 in `test_ui_halle.py`, `/admin/halle` in den Navigationstests), keine Lint- oder Typfehler

- [ ] **Step 6: Commit**

```bash
git add core/beachhub_core/jobs.py core/beachhub_core/navigation.py core/beachhub_core/routes/system.py core/beachhub_core/templates/system/halle.html core/tests/test_halle_jobs.py core/tests/test_ui_halle.py core/tests/test_ui_navigation.py
git commit -m "feat(core): Kontakt-Alarm, nächtlicher Hallenplan und Seite Halle im Admin-UI"
```

---

## Task 16: Anbindung im Betrieb – Caddy mit mTLS, Betriebshandbuch, Vertragstest Ende-zu-Ende

**Files:**
- Modify: `core/deploy/Caddyfile`, `docs/betrieb/hauptsystem.md` (neuer Abschnitt), `README.md` (Status)
- Test: `core/tests/test_hall_vertrag.py`

**Interfaces:**
- Consumes: `beachhub_hall.core.CoreClient`, `beachhub_hall.plan`, `beachhub_hall.db.oeffne`, `beachhub_hall.ereignisse.Ereignisse`, `beachhub_hall.clock.SimulierteUhr` (Teil A) und die Core-App (`beachhub_core.main.app`, Tasks 13–15). Der Test überspringt sich selbst, wenn `beachhub_hall` nicht installiert ist. In CI ist es installiert (Task 2).
- Produces: Caddy-Site `https://10.8.0.1:8444` nur für `/hall/*` mit `client_auth require_and_verify`. Auf 8443 (Admin-UI) antwortet `/hall/*` mit 404.

- [ ] **Step 1: Failing Test schreiben**

`core/tests/test_hall_vertrag.py`:
```python
"""Ende-zu-Ende: der echte Hallendienst-Client gegen die echte Core-App, ohne Netz.

Prüft den ganzen Vertrag: Plan signiert abholen und prüfen, PIN mit den Plan-Parametern
finden, 304, Ereignis zurückliefern und im Hauptsystem wiederfinden.
"""

import asyncio
import uuid
from datetime import date, time
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from beachhub_core import clock
from beachhub_core.config import settings
from beachhub_core.main import app
from beachhub_core.models import Betriebszeit, Ereignis, Feld, FeldRaster, Kundengruppe, Tarif
from beachhub_core.services import buchungen, kunden, lesestand
from beachhub_shared.hallenplan import EreignisLieferung, pin_hash
from beachhub_shared.zeit import kombiniere
from sqlalchemy.orm import Session

pytest.importorskip("beachhub_hall")

from beachhub_hall import plan as hall_plan  # noqa: E402
from beachhub_hall.clock import SimulierteUhr  # noqa: E402
from beachhub_hall.core import CoreClient  # noqa: E402
from beachhub_hall.db import oeffne  # noqa: E402
from beachhub_hall.ereignisse import Ereignisse  # noqa: E402

TOKEN = "hall-token-0123456789abcdef"


def test_halle_holt_plan_und_liefert_ereignisse(db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "hall_token", TOKEN)
    Path(settings.signatur_privatschluessel_pfad).unlink(missing_ok=True)
    oeffentlich = lesestand.erzeuge_schluessel()
    f = Feld(name="Feld 1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    g = Kundengruppe(name="Privat")
    db.add_all([f, g, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    k = kunden.lege_an(db, name="A", email="a@x.de", kundengruppe_id=g.id)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    b = buchungen.lege_an(
        db, feld_id=f.id, kunde_id=k.id, pin_klar="271828",
        beginn=kombiniere(date(2027, 11, 27), time(19)), ende=kombiniere(date(2027, 11, 27), time(20)),
    )
    db.commit()
    jetzt = clock.now(db)
    sitzungen = oeffne(tmp_path / "hall.sqlite")
    uhr = SimulierteUhr(jetzt)

    async def ablauf() -> int:
        core = CoreClient("http://core.test", TOKEN, transport=httpx.ASGITransport(app=app))
        try:
            roh = await core.hole_plan(0)
            assert roh is not None
            dok, inhalt = hall_plan.pruefe(roh, oeffentlich, 0)
            with sitzungen() as hdb:
                hall_plan.speichere(hdb, dok, inhalt, jetzt)
            with sitzungen() as hdb:
                treffer = hall_plan.buchungen_mit_pin(hdb, pin_hash("271828", inhalt.pin))
            assert [t.buchung_id for t in treffer] == [str(b.id)]
            assert await core.hole_plan(dok.version) is None
            ereignisse = Ereignisse(sitzungen, uhr)
            ereignisse.melde("pin_akzeptiert", feld_id=str(f.id), buchung_id=str(b.id))
            antwort = await core.sende_ereignisse(
                EreignisLieferung(dienst_id=uuid.uuid4(), ereignisse=ereignisse.unbestaetigt())
            )
            return antwort.bestaetigt_bis
        finally:
            await core.schliesse()

    assert asyncio.run(ablauf()) == 1
    e = db.query(Ereignis).one()
    assert e.typ == "pin_akzeptiert" and e.buchung_id == b.id and e.feld_id == f.id
```

- [ ] **Step 2: Test laufen lassen**

Run: `cd core && pytest -q tests/test_hall_vertrag.py`
Expected: PASS. Die Bausteine gibt es schon; der Test sichert den Vertrag dauerhaft ab. Schlägt er fehl, liegt ein Vertragsbruch zwischen Teil A und Teil B vor. Dann den Unterschied beheben, nicht den Test anpassen.

- [ ] **Step 3: Caddy**

`core/deploy/Caddyfile` ersetzen durch:
```
{
	auto_https disable_redirects
}

# Admin-UI – nur über WireGuard. Die Hallenschnittstelle ist hier bewusst nicht erreichbar.
https://10.8.0.1:8443 {
	tls internal
	respond /hall/* 404
	reverse_proxy 127.0.0.1:8000
}

# Hallendienst – nur über WireGuard und nur mit Client-Zertifikat der internen CA
# (erzeugt mit `beachhub-core zertifikate`, siehe docs/betrieb/portal.md).
https://10.8.0.1:8444 {
	tls internal {
		client_auth {
			mode require_and_verify
			trust_pool file /etc/caddy/beachhub-ca.crt
		}
	}
	handle /hall/* {
		reverse_proxy 127.0.0.1:8000
	}
	handle {
		respond 404
	}
}
```

In `core/docker-compose.yml` beim Dienst `caddy` die Volumes um die CA ergänzen:
```yaml
    volumes: ["./deploy/Caddyfile:/etc/caddy/Caddyfile:ro", "./data/zertifikate/ca.crt:/etc/caddy/beachhub-ca.crt:ro", "caddy_data:/data"]
```
`beachhub-core zertifikate` legt nach dem Portal-Kern-Plan standardmäßig unter `core/data/zertifikate/` die Dateien `ca.crt`/`ca.key` sowie `portal-kanal.*` und `halle.*` ab; `halle.crt`/`halle.key` kommen auf den Hallenrechner.

- [ ] **Step 4: Betriebshandbuch und README**

In `docs/betrieb/hauptsystem.md` nach Abschnitt „## 5. Laufender Betrieb“ einen neuen Abschnitt einfügen (die folgenden Abschnittsnummern verschieben sich um eins):
````markdown
## 6. Hallendienst anbinden

Der Hallendienst in der Halle ruft das Hauptsystem über WireGuard auf Port **8444** auf; Caddy
lässt dort nur `/hall/*` durch und nur mit einem Client-Zertifikat der internen CA. Zusätzlich
prüft das Hauptsystem den Token `HALL_TOKEN`.

1. `HALL_TOKEN` in `core/.env` auf einen langen Zufallswert setzen und denselben Wert in
   `hall/.env` eintragen. Leer bedeutet: Schnittstelle aus (404).
2. `beachhub-core zertifikate` erzeugt CA und Client-Zertifikat der Halle; die CA wird Caddy
   als `trust_pool` gegeben (siehe `core/docker-compose.yml`).
3. `docker compose up -d` – danach zeigt **System → Halle** den ersten Kontakt.

Das Hauptsystem erzeugt den Plan neu, sobald sich eine Buchung, Sperre, ein Feld oder ein
Hallenwert der Konfiguration ändert, und zusätzlich jede Nacht um 00:05. Meldet sich die Halle
60 Minuten nicht, kommt eine Mail „Halle ohne Kontakt“, bei Rückkehr „Halle wieder verbunden“.
Alarme der Halle (Fehlversuche am Tastenfeld, Geräte, Anwesenheit ohne Buchung, Tür) kommen als
Mail an `EMAIL_FROM`; nachgelieferte Alarme nach einem Ausfall gesammelt in einer Mail.

Einrichtung des Hallenrechners und von Home Assistant: `docs/betrieb/hallendienst.md`.
````

In `README.md` die Zeile mit dem Status ergänzen und die Dokumentliste erweitern:
```markdown
Status: Stufe 1 (Hauptsystem) fertig implementiert, Stufe 3 (Hallendienst) implementiert und gegen
simuliertes Home Assistant getestet; die Anbindung an die echte Hallentechnik folgt, sobald der
Hallenhersteller die Schnittstellen festlegt.
```
```markdown
- Hallendienst: `hall/README.md`, Betrieb und HA-Einrichtung `docs/betrieb/hallendienst.md`
```
Im Abschnitt „Entwicklung“ `-e hall[dev]` in den `pip install`-Befehl aufnehmen.

- [ ] **Step 5: Gesamtprüfung**

Run:
```bash
cd shared && pytest -q && cd ../hall && pytest -q && cd ../core && pytest -q && cd .. && ruff check . && ruff format --check . && mypy
```
Expected: alle drei Test-Suiten PASS, keine Lint- oder Typfehler

- [ ] **Step 6: Commit**

```bash
git add core/deploy/Caddyfile core/docker-compose.yml docs/betrieb/hauptsystem.md README.md core/tests/test_hall_vertrag.py
git commit -m "docs(core): Hallendienst über Caddy mit mTLS anbinden, Vertragstest Ende-zu-Ende"
```

---

## Abnahme der Stufe 3 (Ende des Plans)

- [ ] `cd hall && pytest -q`: alle Tests grün, darunter Hallentag und 72 h offline.
- [ ] `cd core && pytest -q`: alle Tests grün, darunter `test_hall_vertrag.py` (nicht übersprungen).
- [ ] `ruff check . && ruff format --check . && mypy` ohne Befund.
- [ ] `alembic upgrade head` im Hauptcheckout ausgeführt (Migration `0009`).
- [ ] Übergabe nach `main` wie gewohnt: Merge mit `--no-ff`, Push, Worktree und Branch entfernen. Den Dienst startet Kai selbst. Für den Probelauf gegen das Homelab-HA gilt `docs/betrieb/hallendienst.md` Abschnitt 5.

## Offene Punkte außerhalb dieses Plans

- Stufe 5: Anwesenheit aus `pin_akzeptiert` und `praesenz_*` ableiten (A-HALLE-5), Klärungsliste „Präsenz ohne Buchung“, Markierung einzelner Buchungen als „Halle nicht informiert“.
- Ⓞ-16 (Sperren schalten nichts) ist eine Annahme und kommt in die nächste Rückfragerunde an den Betreiber.
- Das echte Tastenfeld und den Türöffner legt der Hallenhersteller fest (O-10). Bis dahin gelten die Annahmen in `hall.toml` (Ereignis `esphome.beachhub_pin`, `lock.*` oder `switch.*`).
