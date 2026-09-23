# Portal-Kern (Stufe 2 ohne 1a) – Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Kunden legen im Buchungsportal ein Konto an, sehen freie Zeiten, buchen und bezahlen Einzeltermine (vorerst über einen Fake-Zahlungsanbieter), sehen ihre Buchungen mit PIN, stornieren und laden Rechnungen herunter; das Hauptsystem verarbeitet ihre Anfragen über einen Long-Poll-Kanal, den es selbst aufbaut.

**Architecture:** Drei Teile. `shared/` bekommt den Kanalvertrag (`beachhub_shared/kanal.py`: Anfrage, Antwort, Nutzlasten, Allowlist der Portal-Dokumente). `core/` bekommt einen Kanal-Client (`kanal.py`, zwei Threads mit synchronem `httpx.Client`), die Anfrageverarbeitung (`services/anfragen.py`, `services/online_buchung.py`), die Zahlungsschnittstelle mit Fake-Anbieter (`zahlung/`) und die CLI für die interne CA. `portal/` ist eine neue FastAPI-App mit eigener PostgreSQL-Datenbank (Schema `spiegel`), serverseitig gerenderten Jinja2-Seiten und einem Briefkasten für das Hauptsystem (`/core/*`). Das Portal entscheidet fachlich nichts: Es zeigt den signierten Lesestand an und legt Anfragen ab; alles Verbindliche passiert im Hauptsystem.

**Tech Stack:** Python 3.12, FastAPI 0.115.6 (Starlette 0.41.3), SQLAlchemy 2.0.36, Alembic 1.14, PostgreSQL 16, Jinja2, pydantic 2.10 / pydantic-settings 2.7, httpx 0.28.1, APScheduler 3.11, PyNaCl (über `beachhub_shared.signatur`), cryptography 44 (CA), pytest 8.3, ruff 0.8.4, mypy 1.13.

**Spec:** `docs/superpowers/specs/2026-09-23-portal-kern-design.md` (Hauptspec: `docs/superpowers/specs/2026-09-05-beachhub-design.md` § 3, § 6, § 8.1, § 9, § 10).

## Global Constraints

- Python **3.12**; jedes Paket mit eigenem `pyproject.toml`, Abhängigkeiten mit exakten Versionen (`==`), `beachhub-shared` wird separat installiert (`pip install -e shared`).
- Geldbeträge durchgehend `decimal.Decimal` (`DECIMAL(10, 2)` in der DB), nie `float` (N-7).
- Zeitstempel UTC-aware speichern, Anzeige in `Europe/Berlin` (N-7). Zeiten im Kanal sind zeitzonenbewusst (`AwareDatetime`).
- Oberflächen auf Deutsch, serverseitig gerendert, **kein Frontend-Build, kein npm**, eine `style.css` je App, CSP ohne Inline-Skripte (`script-src 'self'`) (Hauptspec § 10, N-8). Das Portal duzt, die Mails des Hauptsystems siezen (bestehender Stil).
- **Das Hauptsystem baut alle Verbindungen auf.** Das Portal ruft das Hauptsystem nie auf (N-1).
- Das Portal speichert keine Postadressen, keine Zahlungsdaten und keine Rechnungen außer der 10-Minuten-Kopie für den Einmal-Link (N-2, A-RECH-5).
- Jede Nutzlast aus dem Portal ist im Hauptsystem nicht vertrauenswürdig: Den Kunden bestimmt `kunde.portal_konto_id`, den Zahlungseingang der Anbieter (A-ZAHL-2).
- Jede Änderung an Buchungen, Guthaben, Kunden erzeugt einen Audit-Eintrag mit `quelle="portal"` bzw. `"system"` (N-6).
- Mails, Rechnungs-PDFs und Betreiber-Alarme erst **nach** dem Commit (Muster aus `routes/belegung.py:buchung_anlegen`).
- Einstellungen des Portals tragen das Präfix `PORTAL_` (siehe Abweichung A-1 unten). Einstellungen des Hauptsystems bleiben ohne Präfix.
- Tests laufen gegen echte PostgreSQL-Datenbanken: `beachhub_test` (Hauptsystem), `beachhub_portal_test` (Portal). **Der Anwendungsserver wird nie gestartet** – auch nicht zum Prüfen; Verifikation ausschließlich über pytest/TestClient.
- Lint: vor jedem Commit `ruff format .`, dann `ruff check .` (Repo-Wurzel); Typen `mypy` (Repo-Wurzel, `mypy.ini`); beides grün nach jedem Task. Der Code im Plan ist nicht vorformatiert – `ruff format` bricht lange Aufrufe um; meldet `ruff check` danach noch E501, die Zeichenkette in zwei Literale teilen. CI prüft `ruff format --check .`.
- Commits auf Deutsch mit Präfix `feat(core):`, `feat(portal):`, `feat(shared):`, `test(e2e):`, `docs:`, `chore:`; jede Commit-Nachricht endet mit den Attributionszeilen aus der Sitzung.
- Befehle laufen aus der Repo-Wurzel `/home/dev/projekte/beachhub` (bzw. dem Worktree) mit dem venv `.venv/bin/…`. Umgebungsvariablen für Tests:
  `export TEST_DATABASE_URL=postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub_test`
  `export TEST_PORTAL_DATABASE_URL=postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub_portal_test`

## Abweichungen von der Spec (vor der Umsetzung bekannt)

- **A-1 Präfix `PORTAL_`** für alle Portal-Einstellungen (`PORTAL_DATABASE_URL`, `PORTAL_SECRET_KEY`, …). Grund: Der Ende-zu-Ende-Test lädt Portal und Hauptsystem im selben Prozess; beide läsen sonst dieselbe `DATABASE_URL`. In getrennten Containern stört das Präfix nicht.
- **A-2 mTLS auf eigenem Port statt „nur auf `/core/*`“.** Ein Client-Zertifikat wird im TLS-Handshake verlangt, bevor der Pfad bekannt ist; Caddy kann es nicht pfadweise erzwingen. Das Portal-Caddyfile hat deshalb zwei Blöcke: die öffentliche Domain (Port 443, `/core/*` → 404) und `:8443` mit `client_auth require_and_verify` (nur `/core/*`). `PORTAL_URL` im Hauptsystem zeigt auf `https://<domain>:8443`.
- **A-3 `PaymentProvider.erzeuge_sitzung` bekommt `rueckkehr_url`.** Jeder echte Anbieter braucht eine Rückkehradresse; das Hauptsystem bildet sie aus `PORTAL_URL` und der Anfrage-ID (`/zahlung/zurueck?anfrage=<id>`).
- **A-4 Fake-Zahlungsseite postet an `/test-zahlung/<ref>`**, nicht direkt an `/zahlung/rueckmeldung/fake`. Die Route legt denselben Briefkasteneintrag an wie eine echte Rückmeldung und leitet danach (nur auf den Pfad `/zahlung/zurueck`) weiter; eine Weiterleitung aus dem JSON-Briefkasten gäbe es bei echten Anbietern nicht.
- **A-5 `zahlung_eingegangen` braucht kein Konto.** Rückmeldungen der Anbieter kommen ohne Anmeldung; sie werden über `provider_ref` zugeordnet, nicht über `konto_id`. Die Regel „jede Anfrage außer `konto_angelegt` braucht ein Konto“ gilt für die übrigen Typen.
- **A-6 Nicht verifizierbare Zahlungsrückmeldungen werden nur geloggt**, nicht als Ereignis gespeichert: Die Tabelle `ereignis` entsteht erst mit dem Hallendienst (Migration 0009).
- **A-7 Das Konto entsteht beim ersten erfolgreichen Login** (mit leerem Anzeigenamen), die Anfrage `konto_angelegt` erst nach `/willkommen`. So hängt die Session von Anfang an an einem Konto (`session.konto_id` wie in der Spec).
- **A-8 `konto_angelegt` verknüpft neu**, wenn der Kunde schon eine andere `portal_konto_id` trägt: Das Portal hat die Adresse per Login-Code bestätigt; eine alte Verknüpfung stammt aus einem gelöschten oder wiederhergestellten Portal. Ohne das bliebe ein Kunde nach einer Portal-Wiederherstellung für immer ausgesperrt.
- **A-9 Zahlung unter dem offenen Betrag** bestätigt nichts; der Betrag wird Guthaben, der Betreiber bekommt eine Mail (die Spec schweigt dazu).

## Review Focus

1. **Doppelte oder verspätete Zahlungsrückmeldung** (Anbieter sendet zweimal; Zahlung trifft nach Storno der Reservierung ein) – erwartet: genau eine Bestätigung, eine Rechnung, kein doppeltes Guthaben; nach Storno/Verfall wird der Betrag Guthaben und der Betreiber informiert. *Tests: Task 4 `test_doppelte_rueckmeldung_ist_folgenlos`, `test_zahlung_nach_storno_wird_guthaben`.*
2. **Hauptsystem antwortet nicht** (Anfrage bleibt offen) – erwartet: Warteseite zeigt nach `antwort_hinweis_sekunden` den Hinweis „Anfrage gespeichert …“ statt endlos zu drehen. *Test: Task 12 `test_stand_zeigt_hinweis_nach_wartezeit`.*
3. **Mail-Scanner ruft den Anmeldelink per GET ab** – erwartet: Der Link bleibt gültig; erst der Knopf (POST) meldet an. *Test: Task 10 `test_link_get_verbraucht_nicht`.*
4. **Portal ohne Lesestand** (frisch installiert oder wiederhergestellt) – erwartet: Seiten zeigen „wird gerade geladen“ bzw. „Konto wird eingerichtet“ statt 500; das Hauptsystem schickt fehlende Dokumente beim Abgleich nach. *Tests: Task 11 `test_ohne_lesestand_hinweis`, Task 14 `test_buchungen_ohne_kunde`, Task 6 `test_abgleich_schickt_fehlende_und_aeltere`.*
5. **Nutzlast mit Fremdbezug** (Storno/Rechnung eines anderen Kunden, Rechnungs-Link eines anderen Kontos) – erwartet: `abgelehnt/nicht_gefunden` bzw. 404, keine Wirkung. *Tests: Task 4 `test_storno_fremder_buchung`, Task 5 `test_rechnung_nur_eigene`, Task 15 `test_link_nur_fuer_eigenes_konto`.*

---

## Dateistruktur

```
shared/beachhub_shared/
  kanal.py                 NEU  Kanalvertrag: Anfrage, Nutzlasten je Typ, Antwort, Listen, fuer_portal()
  lesestand.py             ÄND  BelegungInhalt: storno_frist_stunden, antwort_hinweis_sekunden;
                                KontoBuchung: checkout_url, reserviert_bis
shared/tests/test_kanal.py NEU

core/beachhub_core/
  config.py                ÄND  portal_url, kanal_token, portal_client_cert/_key, portal_ca, enable_kanal,
                                zahlung_provider, produktionsfehler
  main.py                  ÄND  Produktionsprüfung, Kanal im lifespan
  kanal.py                 NEU  Kanal-Client: abholen(), verteilen(), abgleichen(), Threads, Ausfall-Alarm
  zertifikate.py           NEU  interne CA + Client-Zertifikate (cryptography)
  cli.py                   ÄND  `beachhub-core zertifikate`
  jobs.py                  ÄND  Verfall-Job (minütlich)
  models/portal.py         NEU  AnfrageVerarbeitet, Zahlung (mit checkout_url)
  models/__init__.py       ÄND  Export
  zahlung/__init__.py      NEU  PaymentProvider, Sitzung, ZahlungsFehler, anbieter(), anbieter_fuer()
  zahlung/fake.py          NEU  FakeProvider
  services/ergebnis.py     NEU  Ergebnis(antwort, nach_commit), ok/abgelehnt/ignoriert
  services/online_buchung.py NEU Reservierung, Bestätigung, Zahlungseingang, Kunden-Storno, Verfall
  services/anfragen.py     NEU  verarbeite(), bearbeite() (idempotent), Verarbeiter je Typ
  services/buchungen.py    ÄND  lege_an(..., zahlungsart=None)
  services/guthaben.py     ÄND  Art `rueckbuchung`
  services/konfiguration.py ÄND `portal_kundengruppe`; Belegung neu markieren bei Stornofrist/Hinweis
  services/lesestand.py    ÄND  Belegung mit Stornofrist/Hinweis, Tarife nach Anlage sortiert, PIN nur bestätigt,
                                Zahlungslink offener Reservierungen
  services/benachrichtigung.py ÄND zahlungsfrist_abgelaufen()
  templates/mail/zahlungsfrist_abgelaufen.txt NEU
alembic/versions/0008_portal_kanal.py NEU
core/tests/test_portal_grundlagen.py, test_zahlung.py, test_online_buchung.py, test_anfragen.py,
           test_kanal.py, test_zertifikate.py  NEU

portal/
  pyproject.toml, alembic.ini, Dockerfile, docker-compose.yml, .env.example, README.md   NEU
  alembic/env.py, alembic/script.py.mako, alembic/versions/0001_spiegel.py               NEU
  deploy/Caddyfile, deploy/init-test-db.sql                                              NEU
  beachhub_portal/
    __init__.py, config.py, database.py, models.py, uhr.py, templating.py, main.py, auth.py, mail.py, jobs.py
    services/  __init__.py, wecker.py, anfragen.py, lesestand.py, tarife.py, slots.py, konten.py,
               briefkasten.py, rechnung_link.py
    routes/    __init__.py, kanal.py, oeffentlich.py, konto.py, belegung.py, buchen.py, zahlung.py,
               buchungen.py, rechnungen.py
    templates/ base.html, fehler.html, anmelden.html, anmelden_link.html, willkommen.html, konto.html,
               konto_loeschen.html, belegung.html, buchen.html, anfrage.html, test_zahlung.html,
               buchungen.html, stornieren.html, rechnungen.html, mail/login.txt
    static/    style.css, warten.js, favicon.svg
  tests/  conftest.py, hilfen.py, test_grundgeruest.py, test_kanal.py, test_login.py, test_belegung.py,
          test_buchen.py, test_zahlung.py, test_buchungen.py, test_rechnungen.py

e2e/conftest.py, e2e/test_ablauf.py   NEU  Portal + Hauptsystem im selben Prozess
docs/betrieb/portal.md NEU; docs/betrieb/hauptsystem.md, README.md, .github/workflows/ci.yml, mypy.ini, ruff.toml ÄND
```

---
## Task 1: shared – Kanalvertrag und Lesestand-Erweiterung

**Files:**
- Create: `shared/beachhub_shared/kanal.py`
- Modify: `shared/beachhub_shared/lesestand.py` (Klassen `BelegungInhalt`, `KontoBuchung`)
- Test: `shared/tests/test_kanal.py`

**Interfaces:**
- Produces: `beachhub_shared.kanal` mit
  - `ANFRAGETYPEN: tuple[str, ...]` – `konto_angelegt`, `konto_geaendert`, `konto_loeschen`, `buchung_anfragen`, `buchung_stornieren`, `zahlung_eingegangen`, `rechnung_anfordern`
  - `fuer_portal(name: str) -> bool` – Allowlist: `belegung`, `tarife`, `konto:<uuid>`; alles andere `False`
  - Nutzlasten `KontoAngelegt(email, anzeigename)`, `KontoGeaendert(anzeigename, bisher)`, `KontoLoeschen()`, `BuchungAnfragen(feld_id: UUID, beginn: AwareDatetime, ende: AwareDatetime)`, `BuchungStornieren(buchung_id: UUID)`, `ZahlungEingegangen(provider, rohdaten, signatur_header: str | None)`, `RechnungAnfordern(rechnung_nr)`; `NUTZLAST: dict[str, type[BaseModel]]`
  - `Anfrage(anfrage_id: UUID, typ: str, konto_id: UUID | None, kunde_id: UUID | None, nutzlast: dict[str, Any], erstellt_am: datetime)`, `AnfrageListe(anfragen)`
  - `Antwort(status: Literal["ok","reserviert","bestaetigt","abgelehnt","ignoriert","fehler"], grund, kunde_id, buchung_id, preis, guthaben_verrechnet, zu_zahlen, checkout_url, reserviert_bis, kostenfrei, pdf_base64, dateiname)` – alle außer `status` optional; übertragen mit `model_dump(mode="json", exclude_none=True)`
  - `AntwortEintrag(anfrage_id, antwort)`, `AntwortListe(antworten)`, `DokumentListe(dokumente: list[Dokument])`, `Verworfen(dokument, grund: Literal["signatur","version_alt","unbekannt"])`, `LesestandErgebnis(uebernommen: list[str], verworfen: list[Verworfen])`
- Produces: `BelegungInhalt.storno_frist_stunden: int = 24`, `BelegungInhalt.antwort_hinweis_sekunden: int = 120` (mit Vorgabe, damit ältere gespeicherte Dokumente gültig bleiben)
- Produces: `KontoBuchung.checkout_url: str | None = None`, `KontoBuchung.reserviert_bis: datetime | None = None` – offene Reservierung mit Zahlungslink (Hauptspec § 8.1); ebenfalls mit Vorgabe

- [ ] **Step 1: Failing Tests schreiben**

`shared/tests/test_kanal.py`:
```python
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from beachhub_shared import kanal
from beachhub_shared.lesestand import BelegungInhalt, KontoBuchung
from pydantic import ValidationError


def test_fuer_portal_ist_allowlist() -> None:
    assert kanal.fuer_portal("belegung")
    assert kanal.fuer_portal("tarife")
    assert kanal.fuer_portal(f"konto:{uuid.uuid4()}")
    # Neue Dokumente des Hauptsystems (etwa der Hallenplan) dürfen nie ans Portal gehen.
    assert not kanal.fuer_portal("hallenplan")
    assert not kanal.fuer_portal("konto:kein-uuid")
    assert not kanal.fuer_portal("konto:")
    assert not kanal.fuer_portal("")


def test_jeder_anfragetyp_hat_ein_nutzlastschema() -> None:
    assert set(kanal.NUTZLAST) == set(kanal.ANFRAGETYPEN)


def test_buchung_anfragen_validiert() -> None:
    n = kanal.NUTZLAST["buchung_anfragen"].model_validate(
        {
            "feld_id": str(uuid.uuid4()),
            "beginn": "2027-12-01T18:00:00Z",
            "ende": "2027-12-01T19:00:00Z",
        }
    )
    assert isinstance(n, kanal.BuchungAnfragen)
    assert n.beginn.tzinfo is not None
    with pytest.raises(ValidationError):
        kanal.NUTZLAST["buchung_anfragen"].model_validate({"feld_id": "x"})


def test_zeiten_ohne_zeitzone_werden_abgelehnt() -> None:
    with pytest.raises(ValidationError):
        kanal.BuchungAnfragen.model_validate(
            {
                "feld_id": str(uuid.uuid4()),
                "beginn": "2027-12-01T18:00:00",
                "ende": "2027-12-01T19:00:00",
            }
        )


def test_konto_angelegt_begrenzt_laengen() -> None:
    with pytest.raises(ValidationError):
        kanal.KontoAngelegt.model_validate({"email": "a@x.de", "anzeigename": ""})
    with pytest.raises(ValidationError):
        kanal.KontoAngelegt.model_validate({"email": "a@x.de", "anzeigename": "x" * 101})


def test_antwort_ohne_leere_felder_und_roundtrip() -> None:
    a = kanal.Antwort(
        status="reserviert",
        buchung_id=uuid.uuid4(),
        preis=Decimal("30.00"),
        checkout_url="/test-zahlung/fake_x",
    )
    d = a.model_dump(mode="json", exclude_none=True)
    assert d["preis"] == "30.00"
    assert "pdf_base64" not in d
    assert kanal.Antwort.model_validate(d) == a


def test_anfrage_liste_roundtrip() -> None:
    a = kanal.Anfrage(
        anfrage_id=uuid.uuid4(),
        typ="konto_angelegt",
        konto_id=uuid.uuid4(),
        nutzlast={"email": "a@x.de", "anzeigename": "A"},
        erstellt_am=datetime(2027, 11, 1, tzinfo=UTC),
    )
    liste = kanal.AnfrageListe(anfragen=[a])
    assert kanal.AnfrageListe.model_validate_json(liste.model_dump_json()).anfragen[0] == a


def test_belegung_neue_felder_mit_vorgabe() -> None:
    b = BelegungInhalt(
        felder=[],
        betriebszeiten=[],
        ausnahmetage=[],
        fenster_tage=14,
        mindestvorlauf_minuten=60,
        belegt={},
    )
    assert b.storno_frist_stunden == 24
    assert b.antwort_hinweis_sekunden == 120


def test_konto_buchung_zahlungslink_optional() -> None:
    alt = {
        "id": str(uuid.uuid4()),
        "feld_id": str(uuid.uuid4()),
        "feld_name": "F1",
        "beginn": "2027-12-01T18:00:00Z",
        "ende": "2027-12-01T19:00:00Z",
        "status": "reserviert",
        "preis": "30.00",
        "pin": None,
        "storno": None,
    }
    b = KontoBuchung.model_validate(alt)  # Dokument aus der Zeit vor Stufe 2
    assert b.checkout_url is None and b.reserviert_bis is None
    neu = KontoBuchung.model_validate(
        {**alt, "checkout_url": "/test-zahlung/fake_x", "reserviert_bis": "2027-11-25T09:15:00Z"}
    )
    assert neu.checkout_url == "/test-zahlung/fake_x" and neu.reserviert_bis is not None
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd shared && ../.venv/bin/pytest -q tests/test_kanal.py`
Expected: FAIL mit `ImportError: cannot import name 'kanal'`

- [ ] **Step 3: Implementieren**

`shared/beachhub_shared/kanal.py`:
```python
"""Vertrag des Kanals zwischen Portal (Briefkasten) und Hauptsystem (Verarbeiter).

Das Hauptsystem holt Anfragen per Long-Polling ab (`GET /core/anfragen`), schickt Antworten
(`POST /core/antworten`) und signierte Lesestände (`POST /core/lesestand`). Beide Seiten
validieren gegen diese Schemata; eine Änderung hier ist eine Änderung des Vertrags.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, Field

from beachhub_shared.lesestand import Dokument

ANFRAGETYPEN: tuple[str, ...] = (
    "konto_angelegt",
    "konto_geaendert",
    "konto_loeschen",
    "buchung_anfragen",
    "buchung_stornieren",
    "zahlung_eingegangen",
    "rechnung_anfordern",
)

# Allowlist: Ein Dokument geht nur ans Portal, wenn es hier ausdrücklich steht. Neue Dokumente
# des Hauptsystems (etwa der Hallenplan mit PIN-Hashes) bleiben so automatisch draußen.
PORTAL_DOKUMENTE: tuple[str, ...] = ("belegung", "tarife")


def fuer_portal(name: str) -> bool:
    if name in PORTAL_DOKUMENTE:
        return True
    praefix, _, rest = name.partition(":")
    if praefix != "konto" or not rest:
        return False
    try:
        uuid.UUID(rest)
    except ValueError:
        return False
    return True


# ---------- Nutzlasten je Anfragetyp ----------


class KontoAngelegt(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    anzeigename: str = Field(min_length=1, max_length=100)


class KontoGeaendert(BaseModel):
    anzeigename: str = Field(min_length=1, max_length=100)
    bisher: str = Field(max_length=100)


class KontoLoeschen(BaseModel):
    pass


class BuchungAnfragen(BaseModel):
    feld_id: uuid.UUID
    beginn: AwareDatetime
    ende: AwareDatetime


class BuchungStornieren(BaseModel):
    buchung_id: uuid.UUID


class ZahlungEingegangen(BaseModel):
    provider: str = Field(min_length=1, max_length=40)
    rohdaten: str = Field(max_length=65536)
    signatur_header: str | None = Field(default=None, max_length=2000)


class RechnungAnfordern(BaseModel):
    rechnung_nr: str = Field(min_length=1, max_length=20)


NUTZLAST: dict[str, type[BaseModel]] = {
    "konto_angelegt": KontoAngelegt,
    "konto_geaendert": KontoGeaendert,
    "konto_loeschen": KontoLoeschen,
    "buchung_anfragen": BuchungAnfragen,
    "buchung_stornieren": BuchungStornieren,
    "zahlung_eingegangen": ZahlungEingegangen,
    "rechnung_anfordern": RechnungAnfordern,
}


# ---------- Anfragen, Antworten, Lesestand ----------


class Anfrage(BaseModel):
    anfrage_id: uuid.UUID
    typ: str
    konto_id: uuid.UUID | None = None
    kunde_id: uuid.UUID | None = None
    nutzlast: dict[str, Any] = Field(default_factory=dict)
    erstellt_am: datetime


class AnfrageListe(BaseModel):
    anfragen: list[Anfrage]


class Antwort(BaseModel):
    status: Literal["ok", "reserviert", "bestaetigt", "abgelehnt", "ignoriert", "fehler"]
    grund: str | None = None
    kunde_id: uuid.UUID | None = None
    buchung_id: uuid.UUID | None = None
    preis: Decimal | None = None
    guthaben_verrechnet: Decimal | None = None
    zu_zahlen: Decimal | None = None
    checkout_url: str | None = None
    reserviert_bis: datetime | None = None
    kostenfrei: bool | None = None
    pdf_base64: str | None = None
    dateiname: str | None = None


class AntwortEintrag(BaseModel):
    anfrage_id: uuid.UUID
    antwort: Antwort


class AntwortListe(BaseModel):
    antworten: list[AntwortEintrag]


class DokumentListe(BaseModel):
    dokumente: list[Dokument]


class Verworfen(BaseModel):
    dokument: str
    grund: Literal["signatur", "version_alt", "unbekannt"]


class LesestandErgebnis(BaseModel):
    uebernommen: list[str]
    verworfen: list[Verworfen]
```

In `shared/beachhub_shared/lesestand.py` die Klasse `BelegungInhalt` ersetzen durch:
```python
class BelegungInhalt(BaseModel):
    felder: list[FeldInfo]
    betriebszeiten: list[BetriebszeitInfo]
    ausnahmetage: list[AusnahmeInfo]
    fenster_tage: int
    mindestvorlauf_minuten: int
    belegt: dict[str, list[Zeitraum]]
    # Mit Vorgabe, damit vor Stufe 2 gespeicherte Dokumente gültig bleiben.
    storno_frist_stunden: int = 24
    antwort_hinweis_sekunden: int = 120
```

und die Klasse `KontoBuchung` ersetzen durch:
```python
class KontoBuchung(BaseModel):
    id: str
    feld_id: str
    feld_name: str
    beginn: datetime
    ende: datetime
    status: str
    preis: Decimal
    pin: str | None
    storno: StornoInfo | None
    # Nur bei einer offenen Reservierung: Link zur Bezahlseite und Ende der Zahlungsfrist
    # (Hauptspec § 8.1). Mit Vorgabe, damit ältere gespeicherte Dokumente gültig bleiben.
    checkout_url: str | None = None
    reserviert_bis: datetime | None = None
```

- [ ] **Step 4: Tests grün**

Run: `cd shared && ../.venv/bin/pytest -q`
Expected: PASS (alle Shared-Tests, inkl. der neuen)

- [ ] **Step 5: Lint, Typen, Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy
git add shared/beachhub_shared/kanal.py shared/beachhub_shared/lesestand.py shared/tests/test_kanal.py
git commit -m "feat(shared): Kanalvertrag zwischen Portal und Hauptsystem"
```

---

## Task 2: core – Datenmodell für Kanal und Zahlung, kleine Anpassungen

**Files:**
- Create: `core/beachhub_core/models/portal.py`
- Modify: `core/beachhub_core/models/__init__.py`
- Create: `core/alembic/versions/0008_portal_kanal.py`
- Modify: `core/beachhub_core/services/buchungen.py` (`lege_an`)
- Modify: `core/beachhub_core/services/guthaben.py` (`ARTEN`)
- Modify: `core/beachhub_core/services/konfiguration.py` (`DEFAULTS`, `BESCHREIBUNGEN`, `setze`)
- Modify: `core/beachhub_core/services/lesestand.py` (`baue_belegung`, `baue_tarife`, `baue_konto`)
- Test: `core/tests/test_portal_grundlagen.py`

**Interfaces:**
- Consumes: `BelegungInhalt.storno_frist_stunden`, `.antwort_hinweis_sekunden` (Task 1)
- Produces: `models.AnfrageVerarbeitet(anfrage_id: UUID PK, typ: str, antwort_json: dict, verarbeitet_am: datetime)`
- Produces: `models.Zahlung(id, kunde_id, buchung_id?, provider, provider_ref (unique), betrag: Decimal, status: "offen"|"bezahlt"|"abgebrochen" (Konstanten `Zahlung.OFFEN/BEZAHLT/ABGEBROCHEN`), empfangen_am?, rohdaten_json?)` mit Beziehungen `kunde`, `buchung`
- Produces: `buchungen.lege_an(..., zahlungsart: str | None = None)` – ohne Angabe gilt `kunde.zahlungsart`
- Produces: Guthabenart `rueckbuchung` (zugehend, positiv)
- Produces: Konfigurationswert `portal_kundengruppe` (`str`, Vorgabe `""`)
- Produces: `Zahlung.checkout_url: str | None` (Spalte `checkout_url VARCHAR(1000)`, Migration 0008) – gesetzt von `online_buchung.anfragen` (Task 4)
- Produces: Lesestand `konto:*` füllt `checkout_url` und `reserviert_bis` nur bei `status == "reserviert"` mit einer noch nicht bezahlten Zahlung (`Zahlung.status != "bezahlt"`, jüngste Zeile); sonst `None`
- Produces: Lesestand `konto:*` zeigt die PIN nur bei `status == "bestaetigt"`; `tarife` sind nach `Tarif.created_at` sortiert (älteste zuerst – das Portal löst Gleichstände damit wie das Hauptsystem zugunsten der neueren Regel)

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_portal_grundlagen.py`:
```python
import uuid
from datetime import date, time
from decimal import Decimal
from pathlib import Path

import pytest
from beachhub_core import clock
from beachhub_core.config import settings
from beachhub_core.models import (
    AnfrageVerarbeitet,
    Betriebszeit,
    Buchung,
    Feld,
    FeldRaster,
    Kundengruppe,
    LesestandVersion,
    Tarif,
    Zahlung,
)
from beachhub_core.services import buchungen, guthaben, konfiguration, kunden, lesestand
from beachhub_shared.zeit import kombiniere
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

D = date(2027, 12, 1)


@pytest.fixture
def welt(db: Session):
    Path(settings.signatur_privatschluessel_pfad).unlink(missing_ok=True)
    lesestand.erzeuge_schluessel()
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    v = Kundengruppe(name="Verein", standard_zahlungsart="rechnung")
    db.add_all([f, v, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    k = kunden.lege_an(db, name="V", email="v@x.de", kundengruppe_id=v.id)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    return f, k


def test_lege_an_mit_abweichender_zahlungsart(db: Session, welt) -> None:
    f, k = welt
    b = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k.id,
        beginn=kombiniere(D, time(19)),
        ende=kombiniere(D, time(20)),
        zahlungsart="online",
    )
    assert k.zahlungsart == "rechnung"
    assert b.zahlungsart == "online"


def test_rueckbuchung_ist_zugehende_guthabenart(db: Session, welt) -> None:
    _, k = welt
    guthaben.buche(db, kunde=k, betrag=Decimal("5.00"), art="rueckbuchung")
    assert k.guthaben == Decimal("5.00")


def test_anfrage_verarbeitet_und_zahlung_eindeutig(db: Session, welt) -> None:
    _, k = welt
    db.add(
        AnfrageVerarbeitet(
            anfrage_id=uuid.uuid4(), typ="konto_angelegt", antwort_json={"status": "ok"}
        )
    )
    z = Zahlung(kunde_id=k.id, provider="fake", provider_ref="fake_1", betrag=Decimal("30.00"))
    db.add(z)
    db.commit()
    assert z.status == Zahlung.OFFEN
    db.add(Zahlung(kunde_id=k.id, provider="fake", provider_ref="fake_1", betrag=Decimal("1.00")))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_portal_kundengruppe_ist_konfigurierbar(db: Session) -> None:
    assert konfiguration.hole(db, "portal_kundengruppe") == ""
    konfiguration.setze(db, "portal_kundengruppe", "Privat")
    db.commit()
    assert konfiguration.hole(db, "portal_kundengruppe") == "Privat"
    assert konfiguration.BESCHREIBUNGEN["portal_kundengruppe"].gruppe == "Portal und Zugang"


def test_belegung_enthaelt_storno_frist_und_hinweis(db: Session, welt) -> None:
    konfiguration.setze(db, "storno_frist_stunden", 48)
    db.commit()
    b = lesestand.baue_belegung(db)
    assert b.storno_frist_stunden == 48
    assert b.antwort_hinweis_sekunden == 120


@pytest.mark.parametrize("schluessel", ["storno_frist_stunden", "antwort_hinweis_sekunden"])
def test_aenderung_markiert_belegung(db: Session, welt, schluessel: str) -> None:
    lesestand.verarbeite_geaenderte(db)
    konfiguration.setze(db, schluessel, 36)
    db.commit()
    assert db.get(LesestandVersion, "belegung").geaendert


def test_pin_nur_fuer_bestaetigte_buchung(db: Session, welt) -> None:
    f, k = welt
    buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k.id,
        beginn=kombiniere(D, time(19)),
        ende=kombiniere(D, time(20)),
        status=Buchung.RESERVIERT,
        zahlungsart="online",
    )
    buchungen.lege_an(
        db, feld_id=f.id, kunde_id=k.id, beginn=kombiniere(D, time(20)), ende=kombiniere(D, time(21))
    )
    db.commit()
    pins = {b.status: b.pin for b in lesestand.baue_konto(db, k).buchungen}
    assert pins["reserviert"] is None
    assert pins["bestaetigt"] is not None and len(pins["bestaetigt"]) == 6


def test_zahlungslink_nur_bei_offener_reservierung(db: Session, welt) -> None:
    f, k = welt
    offen = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k.id,
        beginn=kombiniere(D, time(19)),
        ende=kombiniere(D, time(20)),
        status=Buchung.RESERVIERT,
        zahlungsart="online",
    )
    offen.reserviert_bis = kombiniere(date(2027, 11, 25), time(10, 15))
    bezahlt = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k.id,
        beginn=kombiniere(D, time(20)),
        ende=kombiniere(D, time(21)),
        status=Buchung.RESERVIERT,
        zahlungsart="online",
    )
    db.add_all(
        [
            Zahlung(
                kunde_id=k.id,
                buchung_id=offen.id,
                provider="fake",
                provider_ref="fake_offen",
                betrag=Decimal("30.00"),
                checkout_url="/test-zahlung/fake_offen",
            ),
            Zahlung(
                kunde_id=k.id,
                buchung_id=bezahlt.id,
                provider="fake",
                provider_ref="fake_bezahlt",
                betrag=Decimal("30.00"),
                status=Zahlung.BEZAHLT,
                checkout_url="/test-zahlung/fake_bezahlt",
            ),
        ]
    )
    db.commit()
    je_id = {b.id: b for b in lesestand.baue_konto(db, k).buchungen}
    assert je_id[str(offen.id)].checkout_url == "/test-zahlung/fake_offen"
    assert je_id[str(offen.id)].reserviert_bis == offen.reserviert_bis
    assert je_id[str(bezahlt.id)].checkout_url is None
    assert je_id[str(bezahlt.id)].reserviert_bis is None


def test_tarife_aelteste_zuerst(db: Session, welt) -> None:
    db.add(Tarif(name="Neu", preis=Decimal("40.00")))
    db.commit()
    assert [r.name for r in lesestand.baue_tarife(db).regeln] == ["Std", "Neu"]
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && ../.venv/bin/pytest -q tests/test_portal_grundlagen.py`
Expected: FAIL mit `ImportError: cannot import name 'AnfrageVerarbeitet'`

- [ ] **Step 3: Modelle und Migration**

`core/beachhub_core/models/portal.py`:
```python
"""Tabellen für den Kanal zum Portal und die Online-Zahlung (Spec Portal-Kern § 4–5)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DECIMAL, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from beachhub_core.models.base import Base, UUIDMixin, ZeitstempelMixin, utcnow
from beachhub_core.models.buchungen import Buchung
from beachhub_core.models.kunden import Kunde


class AnfrageVerarbeitet(Base):
    """Jede Portal-Anfrage wird genau einmal verarbeitet; eine erneut zugestellte Anfrage
    bekommt die hier gespeicherte Antwort."""

    __tablename__ = "anfrage_verarbeitet"
    anfrage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    typ: Mapped[str] = mapped_column(String(40), nullable=False)
    antwort_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    verarbeitet_am: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Zahlung(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "zahlung"
    OFFEN, BEZAHLT, ABGEBROCHEN = "offen", "bezahlt", "abgebrochen"

    kunde_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("kunde.id"), nullable=False
    )
    buchung_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("buchung.id"), index=True
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_ref: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    betrag: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(12), default=OFFEN, nullable=False)
    empfangen_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rohdaten_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Bezahlseite des Anbieters; erscheint im Lesestand, solange die Reservierung offen ist.
    checkout_url: Mapped[str | None] = mapped_column(String(1000))
    kunde: Mapped[Kunde] = relationship()
    buchung: Mapped[Buchung | None] = relationship()
```

In `core/beachhub_core/models/__init__.py` ergänzen (Import und `__all__`, alphabetisch einsortiert):
```python
from beachhub_core.models.portal import AnfrageVerarbeitet, Zahlung
```
```python
    "AnfrageVerarbeitet",
    ...
    "Zahlung",
```

`core/alembic/versions/0008_portal_kanal.py`:
```python
"""portal kanal

Tabellen für den Kanal zum Portal: verarbeitete Anfragen (Idempotenz) und Zahlungen des
Online-Zahlungsdienstes.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "anfrage_verarbeitet",
        sa.Column("anfrage_id", sa.UUID(), nullable=False),
        sa.Column("typ", sa.String(length=40), nullable=False),
        sa.Column("antwort_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("verarbeitet_am", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("anfrage_id"),
    )
    op.create_table(
        "zahlung",
        sa.Column("kunde_id", sa.UUID(), nullable=False),
        sa.Column("buchung_id", sa.UUID(), nullable=True),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("provider_ref", sa.String(length=200), nullable=False),
        sa.Column("betrag", sa.DECIMAL(precision=10, scale=2), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("empfangen_am", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rohdaten_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("checkout_url", sa.String(length=1000), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["buchung_id"], ["buchung.id"]),
        sa.ForeignKeyConstraint(["kunde_id"], ["kunde.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_ref"),
    )
    op.create_index("ix_zahlung_buchung_id", "zahlung", ["buchung_id"])


def downgrade() -> None:
    op.drop_index("ix_zahlung_buchung_id", table_name="zahlung")
    op.drop_table("zahlung")
    op.drop_table("anfrage_verarbeitet")
```

- [ ] **Step 4: Dienste anpassen**

`core/beachhub_core/services/buchungen.py`, Funktion `lege_an`: neuen Parameter nach `pin_klar` ergänzen und beim Anlegen verwenden:
```python
    pin_klar: str | None = None,
    zahlungsart: str | None = None,
) -> Buchung:
```
```python
        zahlungsart=zahlungsart or kunde.zahlungsart,
```

`core/beachhub_core/services/guthaben.py`:
```python
ARTEN = {
    "storno_gutschrift",
    "verrechnung",
    "auszahlung",
    "manuell",
    "ueberzahlung",
    # Verrechnetes Guthaben einer Reservierung, die ohne Zahlung endet (Storno, Verfall).
    # Keine storno_gutschrift: Es gab keine Rechnung, also braucht es keinen Korrekturbeleg.
    "rueckbuchung",
}
```

`core/beachhub_core/services/konfiguration.py`:
- In `DEFAULTS` nach `"pin_laenge"` ergänzen: `"portal_kundengruppe": (str, ""),`
- In `BESCHREIBUNGEN` ergänzen:
```python
    "portal_kundengruppe": Beschreibung(
        "Portal und Zugang",
        "Kundengruppe neuer Portalkunden",
        "",
        "Name der Kundengruppe, die ein im Portal angelegter Kunde bekommt. Leer: die erste "
        "Gruppe in alphabetischer Reihenfolge.",
    ),
```
- In `setze` die Markierung erweitern:
```python
    if schluessel in (
        "fenster_tage",
        "mindestvorlauf_minuten",
        "storno_frist_stunden",
        "antwort_hinweis_sekunden",
    ):
```

`core/beachhub_core/services/lesestand.py`:
- In `baue_belegung` beim Konstruieren von `schema.BelegungInhalt` ergänzen:
```python
        storno_frist_stunden=konfiguration.hole(db, "storno_frist_stunden"),
        antwort_hinweis_sekunden=konfiguration.hole(db, "antwort_hinweis_sekunden"),
```
- In `baue_tarife` die Abfrage sortieren:
```python
    regeln = db.scalars(
        select(Tarif)
        .where(Tarif.aktiv.is_(True), or_(Tarif.gueltig_bis.is_(None), Tarif.gueltig_bis >= heute))
        .order_by(Tarif.created_at)
    ).all()
```
- `Zahlung` zum Import aus `beachhub_core.models` ergänzen.
- In `baue_konto` die Zeile `zeige_pin = …` ersetzen durch:
```python
        # Nur bestätigte Buchungen haben eine gültige PIN; eine Reservierung ist noch nicht bezahlt.
        zeige_pin = b.status == Buchung.BESTAETIGT and b.ende > jetzt and b.pin_verschluesselt
        # Offene Reservierung: Link zur Bezahlseite, solange nicht bezahlt (Hauptspec § 8.1).
        offene_zahlung = (
            db.scalar(
                select(Zahlung)
                .where(Zahlung.buchung_id == b.id, Zahlung.status != Zahlung.BEZAHLT)
                .order_by(Zahlung.created_at.desc())
                .limit(1)
            )
            if b.status == Buchung.RESERVIERT
            else None
        )
```
  und im Konstruktor von `schema.KontoBuchung(...)` nach `storno=…` ergänzen:
```python
                checkout_url=offene_zahlung.checkout_url if offene_zahlung else None,
                reserviert_bis=b.reserviert_bis if offene_zahlung else None,
```

- [ ] **Step 5: Tests grün (neu und bestehend)**

Run: `cd core && ../.venv/bin/pytest -q`
Expected: PASS (inkl. `test_migrationen.py`, der 0008 mit upgrade/downgrade durchläuft)

- [ ] **Step 6: Lint, Typen, Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy
git add core/beachhub_core/models core/alembic/versions/0008_portal_kanal.py \
  core/beachhub_core/services/buchungen.py core/beachhub_core/services/guthaben.py \
  core/beachhub_core/services/konfiguration.py core/beachhub_core/services/lesestand.py \
  core/tests/test_portal_grundlagen.py
git commit -m "feat(core): Tabellen für Portal-Kanal und Zahlung, PIN nur bei bestätigten Buchungen"
```

---

## Task 3: core – Zahlungsschnittstelle mit Fake-Anbieter und Kanal-Einstellungen

**Files:**
- Modify: `core/beachhub_core/config.py`
- Create: `core/beachhub_core/zahlung/__init__.py`
- Create: `core/beachhub_core/zahlung/fake.py`
- Modify: `mypy.ini`
- Test: `core/tests/test_zahlung.py`

**Interfaces:**
- Produces: `settings.portal_url: str = ""`, `settings.kanal_token: str = ""`, `settings.portal_client_cert: str = ""`, `settings.portal_client_key: str = ""`, `settings.portal_ca: str = ""`, `settings.enable_kanal: bool = True`, `settings.zahlung_provider: str = "fake"`, `settings.produktionsfehler -> list[str]`
- Produces: `zahlung.Sitzung(provider_ref: str, checkout_url: str)` (frozen dataclass), `zahlung.ZahlungsFehler(Exception)`, `zahlung.PaymentProvider` (Protocol mit `name`, `erzeuge_sitzung(*, betrag: Decimal, referenz: uuid.UUID, ablauf: datetime, rueckkehr_url: str) -> Sitzung`, `verifiziere(rohdaten: str, signatur_header: str | None) -> str | None`, `status(provider_ref: str) -> tuple[str, Decimal]` mit Zustand `"bezahlt" | "offen" | "abgebrochen"`)
- Produces: `zahlung.anbieter() -> PaymentProvider` (Singleton nach `settings.zahlung_provider`), `zahlung.anbieter_fuer(name: str) -> PaymentProvider | None`
- Produces: `zahlung.fake.FakeProvider` – `checkout_url = "/test-zahlung/<ref>?betrag=<betrag>&zurueck=<rueckkehr_url>"` (relativ: die Seite liegt im Portal); `verifiziere` erwartet JSON `{"ref": "fake_…", "ergebnis": "bezahlt"|"abgebrochen", "betrag": "<dezimal>"}`

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_zahlung.py`:
```python
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import pytest
from beachhub_core import zahlung
from beachhub_core.config import Settings, settings
from beachhub_core.zahlung.fake import FakeProvider


def test_fake_sitzung_verweist_auf_portalseite() -> None:
    s = FakeProvider().erzeuge_sitzung(
        betrag=Decimal("30.00"),
        referenz=uuid.uuid4(),
        ablauf=datetime(2027, 12, 1, tzinfo=UTC),
        rueckkehr_url="/zahlung/zurueck?anfrage=abc",
    )
    assert s.provider_ref.startswith("fake_")
    u = urlparse(s.checkout_url)
    assert u.path == f"/test-zahlung/{s.provider_ref}"
    q = parse_qs(u.query)
    assert q["betrag"] == ["30.00"]
    assert q["zurueck"] == ["/zahlung/zurueck?anfrage=abc"]


def test_fake_verifiziert_und_liefert_status() -> None:
    p = FakeProvider()
    roh = json.dumps({"ref": "fake_abc", "ergebnis": "bezahlt", "betrag": "30.00"})
    assert p.verifiziere(roh, None) == "fake_abc"
    assert p.status("fake_abc") == ("bezahlt", Decimal("30.00"))
    assert p.status("fake_unbekannt") == ("offen", Decimal("0.00"))


@pytest.mark.parametrize(
    "roh",
    [
        "kein json",
        json.dumps([1]),
        json.dumps({"ref": "x", "ergebnis": "bezahlt", "betrag": "1"}),
        json.dumps({"ref": "fake_a", "ergebnis": "gestohlen", "betrag": "1"}),
        json.dumps({"ref": "fake_a", "ergebnis": "bezahlt", "betrag": "abc"}),
        json.dumps({"ref": "fake_a", "ergebnis": "bezahlt", "betrag": "NaN"}),
    ],
)
def test_fake_verwirft_kaputte_rueckmeldungen(roh: str) -> None:
    assert FakeProvider().verifiziere(roh, None) is None


def test_fake_nur_in_entwicklung(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    with pytest.raises(zahlung.ZahlungsFehler):
        FakeProvider()


def test_anbieter_nach_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zahlung, "_instanz", None)
    assert zahlung.anbieter().name == "fake"
    assert zahlung.anbieter_fuer("fake") is zahlung.anbieter()
    assert zahlung.anbieter_fuer("stripe") is None


def test_unbekannter_anbieter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zahlung, "_instanz", None)
    monkeypatch.setattr(settings, "zahlung_provider", "gibtsnicht")
    with pytest.raises(zahlung.ZahlungsFehler):
        zahlung.anbieter()
    assert zahlung.anbieter_fuer("gibtsnicht") is None


def test_produktionsfehler() -> None:
    ohne_portal = Settings(app_env="production", portal_url="", zahlung_provider="fake")
    assert ohne_portal.produktionsfehler == []
    mit_portal = Settings(
        app_env="production", portal_url="https://p:8443", kanal_token="", zahlung_provider="fake"
    )
    assert len(mit_portal.produktionsfehler) == 2
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && ../.venv/bin/pytest -q tests/test_zahlung.py`
Expected: FAIL mit `ModuleNotFoundError: No module named 'beachhub_core.zahlung'`

- [ ] **Step 3: Implementieren**

`core/beachhub_core/config.py` – in `Settings` nach `betreiber_bank` ergänzen:
```python
    # Kanal zum Portal (Spec Portal-Kern § 3). Ohne PORTAL_URL läuft kein Kanal.
    portal_url: str = ""
    kanal_token: str = ""
    portal_client_cert: str = ""
    portal_client_key: str = ""
    portal_ca: str = ""  # leer: System-CAs (Portal mit öffentlichem Zertifikat)
    enable_kanal: bool = True
    zahlung_provider: str = "fake"
```
und nach `has_insecure_defaults`:
```python
    @property
    def produktionsfehler(self) -> list[str]:
        """Einstellungen, mit denen das Hauptsystem im Produktivbetrieb nicht starten darf."""
        fehler: list[str] = []
        if self.portal_url and not self.kanal_token:
            fehler.append("KANAL_TOKEN fehlt, obwohl PORTAL_URL gesetzt ist")
        if self.portal_url and self.zahlung_provider == "fake":
            fehler.append("ZAHLUNG_PROVIDER=fake ist nur für die Entwicklung")
        return fehler
```

`core/beachhub_core/zahlung/__init__.py`:
```python
"""Zahlungsschnittstelle (A-ZAHL-5).

Der Anbieter ist austauschbar und noch nicht entschieden (Ⓞ-13). Bis dahin gibt es nur den
Fake-Anbieter für Entwicklung und Tests; Stripe oder Mollie kommen als weitere Klasse dazu.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from beachhub_core.config import settings


class ZahlungsFehler(Exception):  # noqa: N818 – Fachfehler heißen im Projekt ...Fehler
    pass


@dataclass(frozen=True)
class Sitzung:
    provider_ref: str
    checkout_url: str


class PaymentProvider(Protocol):
    name: str

    def erzeuge_sitzung(
        self, *, betrag: Decimal, referenz: uuid.UUID, ablauf: datetime, rueckkehr_url: str
    ) -> Sitzung: ...

    def verifiziere(self, rohdaten: str, signatur_header: str | None) -> str | None:
        """Prüft eine Rückmeldung des Anbieters und liefert die Zahlungsreferenz – oder None."""
        ...

    def status(self, provider_ref: str) -> tuple[str, Decimal]:
        """Fragt den Anbieter nach dem Stand: ("bezahlt" | "offen" | "abgebrochen", Betrag)."""
        ...


_instanz: PaymentProvider | None = None


def anbieter() -> PaymentProvider:
    global _instanz
    if _instanz is None:
        if settings.zahlung_provider == "fake":
            from beachhub_core.zahlung.fake import FakeProvider

            _instanz = FakeProvider()
        else:
            raise ZahlungsFehler(f"Unbekannter Zahlungsanbieter: {settings.zahlung_provider}")
    return _instanz


def anbieter_fuer(name: str) -> PaymentProvider | None:
    """Der aktive Anbieter, wenn er so heißt. Rückmeldungen anderer Anbieter werden ignoriert."""
    try:
        aktiv = anbieter()
    except ZahlungsFehler:
        return None
    return aktiv if aktiv.name == name else None
```

`core/beachhub_core/zahlung/fake.py`:
```python
"""Fake-Zahlungsanbieter für Entwicklung und Tests.

Die Bezahlseite liegt im Portal (`/test-zahlung/<ref>`); sie schickt eine Rückmeldung in den
Briefkasten wie ein echter Anbieter. Im Produktivbetrieb verweigert der Konstruktor den Dienst.
"""

import json
import secrets
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from beachhub_core.config import settings
from beachhub_core.zahlung import Sitzung, ZahlungsFehler


class FakeProvider:
    name = "fake"

    def __init__(self) -> None:
        if settings.app_env not in ("dev", "test"):
            raise ZahlungsFehler("Der Fake-Zahlungsanbieter ist nur in der Entwicklung erlaubt")
        self._ergebnisse: dict[str, tuple[str, Decimal]] = {}

    def erzeuge_sitzung(
        self, *, betrag: Decimal, referenz: uuid.UUID, ablauf: datetime, rueckkehr_url: str
    ) -> Sitzung:
        ref = "fake_" + secrets.token_urlsafe(16)
        query = urlencode({"betrag": str(betrag), "zurueck": rueckkehr_url})
        return Sitzung(provider_ref=ref, checkout_url=f"/test-zahlung/{ref}?{query}")

    def verifiziere(self, rohdaten: str, signatur_header: str | None) -> str | None:
        try:
            daten = json.loads(rohdaten)
        except ValueError:
            return None
        if not isinstance(daten, dict):
            return None
        ref, ergebnis = daten.get("ref"), daten.get("ergebnis")
        if not isinstance(ref, str) or not ref.startswith("fake_"):
            return None
        if ergebnis not in ("bezahlt", "abgebrochen"):
            return None
        try:
            betrag = Decimal(str(daten.get("betrag"))).quantize(Decimal("0.01"))
        except InvalidOperation:
            return None
        self._ergebnisse[ref] = (ergebnis, betrag)
        return ref

    def status(self, provider_ref: str) -> tuple[str, Decimal]:
        return self._ergebnisse.get(provider_ref, ("offen", Decimal("0.00")))
```

`mypy.ini`, Zeile `files` ersetzen durch:
```ini
files = shared/beachhub_shared, core/beachhub_core/services, core/beachhub_core/zahlung
```

- [ ] **Step 4: Tests grün**

Run: `cd core && ../.venv/bin/pytest -q tests/test_zahlung.py`
Expected: PASS

- [ ] **Step 5: Lint, Typen, Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy
git add core/beachhub_core/config.py core/beachhub_core/zahlung mypy.ini core/tests/test_zahlung.py
git commit -m "feat(core): Zahlungsschnittstelle mit Fake-Anbieter"
```

---
## Task 4: core – Online-Buchung: Reservierung, Zahlungseingang, Kunden-Storno, Verfall

**Files:**
- Create: `core/beachhub_core/services/ergebnis.py`
- Create: `core/beachhub_core/services/online_buchung.py`
- Modify: `core/beachhub_core/services/benachrichtigung.py` (neue Funktion `zahlungsfrist_abgelaufen`)
- Create: `core/beachhub_core/templates/mail/zahlungsfrist_abgelaufen.txt`
- Modify: `core/beachhub_core/jobs.py` (Verfall-Job)
- Test: `core/tests/test_online_buchung.py`

**Interfaces:**
- Consumes: `zahlung.anbieter()`, `zahlung.anbieter_fuer()`, `Sitzung` (Task 3); `Zahlung`, `buchungen.lege_an(..., zahlungsart=)`, Guthabenart `rueckbuchung` (Task 2); `kanal.Antwort`, `kanal.ZahlungEingegangen` (Task 1)
- Produces: `services.ergebnis`: `Nachlauf = Callable[[Session], None]`; `@dataclass Ergebnis(antwort: kanal.Antwort, nach_commit: list[Nachlauf] = [])`; `ok(**felder) -> Ergebnis`, `abgelehnt(grund: str) -> Ergebnis`, `ignoriert(grund: str) -> Ergebnis`
- Produces: `online_buchung.anfragen(db, *, kunde: Kunde, anfrage_id: uuid.UUID, feld_id: uuid.UUID, beginn: datetime, ende: datetime, rueckkehr_url: str) -> Ergebnis` – speichert `checkout_url` an der Zeile `zahlung` (für den Lesestand, Task 2); Antwort `reserviert` (mit `buchung_id, preis, guthaben_verrechnet, zu_zahlen, checkout_url, reserviert_bis`), `bestaetigt` (mit `buchung_id, preis, guthaben_verrechnet`) oder `abgelehnt` (`belegt`, `ausserhalb_fenster`, `ausserhalb_betriebszeit`, `kein_tarif`, `feld_inaktiv`, `konto_gesperrt`)
- Produces: `online_buchung.zahlung_eingegangen(db, n: kanal.ZahlungEingegangen) -> Ergebnis` – `ok` oder `ignoriert` (`anbieter_unbekannt`, `nicht_verifiziert`, `zahlung_unbekannt`, `offen`)
- Produces: `online_buchung.storniere_fuer_kunde(db, *, kunde: Kunde, buchung_id: uuid.UUID) -> Ergebnis` – `ok` mit `kostenfrei` oder `abgelehnt` (`nicht_gefunden`, `zu_spaet`)
- Produces: `online_buchung.verfalle_abgelaufene(db) -> list[Buchung]` (committet nicht)
- Produces: `benachrichtigung.zahlungsfrist_abgelaufen(db, b: Buchung) -> None`
- Produces: `jobs.verfall_ausfuehren(db) -> int` (committet, mailt danach), Scheduler-Job `verfall` minütlich
- Alle Funktionen in `online_buchung` arbeiten in der Transaktion des Aufrufers und committen **nicht**; Mails und PDFs stehen in `Ergebnis.nach_commit`.

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_online_buchung.py`:
```python
import json
import uuid
from datetime import date, time
from decimal import Decimal

import pytest
from beachhub_core import clock, jobs
from beachhub_core.config import settings
from beachhub_core.models import (
    Betriebszeit,
    Buchung,
    Feld,
    FeldRaster,
    GuthabenBuchung,
    Kundengruppe,
    Rechnung,
    Tarif,
    Zahlung,
)
from beachhub_core.services import buchungen, guthaben, kunden, online_buchung
from beachhub_shared import kanal
from beachhub_shared.zeit import kombiniere
from sqlalchemy import select
from sqlalchemy.orm import Session

D = date(2027, 12, 1)
RUECK = "/zahlung/zurueck?anfrage=x"


@pytest.fixture
def welt(db: Session):
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    p = Kundengruppe(name="Privat")
    db.add_all([f, p, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    k = kunden.lege_an(db, name="A", email="a@x.de", kundengruppe_id=p.id)
    andere = kunden.lege_an(db, name="B", email="b@x.de", kundengruppe_id=p.id)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    return f, k, andere


def _anfragen(db: Session, f: Feld, k, stunde: int = 19, tag: date = D) -> online_buchung.Ergebnis:
    return online_buchung.anfragen(
        db,
        kunde=k,
        anfrage_id=uuid.uuid4(),
        feld_id=f.id,
        beginn=kombiniere(tag, time(stunde)),
        ende=kombiniere(tag, time(stunde + 1)),
        rueckkehr_url=RUECK,
    )


def _rueckmeldung(ref: str, ergebnis: str = "bezahlt", betrag: str = "30.00"):
    return kanal.ZahlungEingegangen(
        provider="fake",
        rohdaten=json.dumps({"ref": ref, "ergebnis": ergebnis, "betrag": betrag}),
    )


def _nachlauf(db: Session, erg: online_buchung.Ergebnis) -> None:
    db.commit()
    for schritt in erg.nach_commit:
        schritt(db)


def test_reservierung_mit_bezahlsitzung(db: Session, welt) -> None:
    f, k, _ = welt
    erg = _anfragen(db, f, k)
    db.commit()
    a = erg.antwort
    assert a.status == "reserviert"
    assert a.zu_zahlen == Decimal("30.00") and a.guthaben_verrechnet == Decimal("0.00")
    assert a.checkout_url is not None and a.checkout_url.startswith("/test-zahlung/fake_")
    b = db.get(Buchung, a.buchung_id)
    assert b.status == "reserviert" and b.zahlungsart == "online" and b.quelle == "portal"
    assert b.reserviert_bis == a.reserviert_bis
    z = db.scalar(select(Zahlung).where(Zahlung.buchung_id == b.id))
    assert z.status == "offen" and z.betrag == Decimal("30.00") and z.provider == "fake"
    assert z.checkout_url == a.checkout_url
    assert erg.nach_commit == []


def test_guthaben_deckt_alles(db: Session, welt, mail_ausgang) -> None:
    f, k, _ = welt
    guthaben.buche(db, kunde=k, betrag=Decimal("40.00"), art="manuell")
    erg = _anfragen(db, f, k)
    _nachlauf(db, erg)
    assert erg.antwort.status == "bestaetigt"
    assert erg.antwort.guthaben_verrechnet == Decimal("30.00")
    assert k.guthaben == Decimal("10.00")
    r = db.scalar(select(Rechnung))
    assert r.status == "bezahlt" and r.pdf_pfad
    betreffe = [m["betreff"] for m in mail_ausgang]
    assert any(b.startswith("Buchung bestätigt") for b in betreffe)
    assert any(b.startswith("Rechnung ") for b in betreffe)


def test_guthaben_teilweise(db: Session, welt) -> None:
    f, k, _ = welt
    guthaben.buche(db, kunde=k, betrag=Decimal("10.00"), art="manuell")
    erg = _anfragen(db, f, k)
    assert erg.antwort.status == "reserviert"
    assert erg.antwort.guthaben_verrechnet == Decimal("10.00")
    assert erg.antwort.zu_zahlen == Decimal("20.00")
    assert k.guthaben == Decimal("0.00")


def test_abgelehnt_mit_gruenden(db: Session, welt) -> None:
    f, k, andere = welt
    _anfragen(db, f, andere)
    db.commit()
    assert _anfragen(db, f, k).antwort.grund == "belegt"
    assert _anfragen(db, f, k, tag=date(2028, 1, 20)).antwort.grund == "ausserhalb_fenster"
    assert _anfragen(db, f, k, stunde=8).antwort.grund == "ausserhalb_betriebszeit"
    kunden.anonymisiere(db, k)
    db.commit()
    assert _anfragen(db, f, k, stunde=12).antwort.grund == "konto_gesperrt"


def test_kein_tarif(db: Session, welt) -> None:
    f, k, _ = welt
    for t in db.scalars(select(Tarif)):
        t.aktiv = False
    db.commit()
    assert _anfragen(db, f, k).antwort.grund == "kein_tarif"


def test_zahlung_bestaetigt_buchung(db: Session, welt, mail_ausgang) -> None:
    f, k, _ = welt
    a = _anfragen(db, f, k).antwort
    db.commit()
    z = db.scalar(select(Zahlung))
    erg = online_buchung.zahlung_eingegangen(db, _rueckmeldung(z.provider_ref))
    _nachlauf(db, erg)
    assert erg.antwort.status == "ok"
    b = db.get(Buchung, a.buchung_id)
    assert b.status == "bestaetigt" and b.reserviert_bis is None
    assert z.status == "bezahlt" and z.empfangen_am is not None
    assert db.scalar(select(Rechnung)).status == "bezahlt"
    assert any(m["betreff"].startswith("Buchung bestätigt") for m in mail_ausgang)


def test_doppelte_rueckmeldung_ist_folgenlos(db: Session, welt) -> None:
    f, k, _ = welt
    _anfragen(db, f, k)
    db.commit()
    ref = db.scalar(select(Zahlung)).provider_ref
    _nachlauf(db, online_buchung.zahlung_eingegangen(db, _rueckmeldung(ref)))
    zweite = online_buchung.zahlung_eingegangen(db, _rueckmeldung(ref))
    db.commit()
    assert zweite.antwort.status == "ok" and zweite.nach_commit == []
    assert len(db.scalars(select(Rechnung)).all()) == 1
    assert k.guthaben == Decimal("0.00")


def test_zahlung_nach_storno_wird_guthaben(db: Session, welt, mail_ausgang) -> None:
    f, k, _ = welt
    a = _anfragen(db, f, k).antwort
    db.commit()
    online_buchung.storniere_fuer_kunde(db, kunde=k, buchung_id=a.buchung_id)
    db.commit()
    ref = db.scalar(select(Zahlung)).provider_ref
    erg = online_buchung.zahlung_eingegangen(db, _rueckmeldung(ref))
    _nachlauf(db, erg)
    assert db.get(Buchung, a.buchung_id).status == "storniert"
    assert k.guthaben == Decimal("30.00")
    assert db.scalar(select(Rechnung)) is None
    assert any(m["an"] == settings.email_from for m in mail_ausgang)


def test_zahlung_nach_verfall_wird_guthaben(db: Session, welt) -> None:
    f, k, _ = welt
    a = _anfragen(db, f, k).antwort
    db.commit()
    clock.set_override(db, date(2027, 11, 26))
    assert [b.id for b in online_buchung.verfalle_abgelaufene(db)] == [a.buchung_id]
    db.commit()
    ref = db.scalar(select(Zahlung)).provider_ref
    _nachlauf(db, online_buchung.zahlung_eingegangen(db, _rueckmeldung(ref)))
    assert db.get(Buchung, a.buchung_id).status == "verfallen"
    assert k.guthaben == Decimal("30.00")


def test_zu_geringer_betrag_bestaetigt_nicht(db: Session, welt) -> None:
    f, k, _ = welt
    a = _anfragen(db, f, k).antwort
    db.commit()
    ref = db.scalar(select(Zahlung)).provider_ref
    erg = online_buchung.zahlung_eingegangen(db, _rueckmeldung(ref, betrag="10.00"))
    _nachlauf(db, erg)
    assert db.get(Buchung, a.buchung_id).status == "reserviert"
    assert k.guthaben == Decimal("10.00")


def test_abgebrochene_zahlung_laesst_reservierung_stehen(db: Session, welt) -> None:
    f, k, _ = welt
    a = _anfragen(db, f, k).antwort
    db.commit()
    z = db.scalar(select(Zahlung))
    erg = online_buchung.zahlung_eingegangen(db, _rueckmeldung(z.provider_ref, "abgebrochen"))
    db.commit()
    assert erg.antwort.status == "ok"
    assert z.status == "abgebrochen"
    assert db.get(Buchung, a.buchung_id).status == "reserviert"


def test_unbrauchbare_rueckmeldungen_werden_ignoriert(db: Session, welt) -> None:
    fremd = kanal.ZahlungEingegangen(provider="stripe", rohdaten="{}")
    assert online_buchung.zahlung_eingegangen(db, fremd).antwort.grund == "anbieter_unbekannt"
    kaputt = kanal.ZahlungEingegangen(provider="fake", rohdaten="kein json")
    assert online_buchung.zahlung_eingegangen(db, kaputt).antwort.grund == "nicht_verifiziert"
    unbekannt = _rueckmeldung("fake_gibtsnicht")
    assert online_buchung.zahlung_eingegangen(db, unbekannt).antwort.grund == "zahlung_unbekannt"


def test_storno_reservierung_kostenfrei_mit_rueckbuchung(db: Session, welt) -> None:
    f, k, _ = welt
    guthaben.buche(db, kunde=k, betrag=Decimal("10.00"), art="manuell")
    a = _anfragen(db, f, k).antwort
    db.commit()
    assert k.guthaben == Decimal("0.00")
    erg = online_buchung.storniere_fuer_kunde(db, kunde=k, buchung_id=a.buchung_id)
    db.commit()
    assert erg.antwort.status == "ok" and erg.antwort.kostenfrei is True
    assert k.guthaben == Decimal("10.00")
    assert db.get(Buchung, a.buchung_id).status == "storniert"
    arten = [g.art for g in db.scalars(select(GuthabenBuchung).order_by(GuthabenBuchung.created_at))]
    assert arten == ["manuell", "verrechnung", "rueckbuchung"]


def test_storno_bestaetigt_vor_frist_mit_gutschrift(db: Session, welt, mail_ausgang) -> None:
    f, k, _ = welt
    guthaben.buche(db, kunde=k, betrag=Decimal("30.00"), art="manuell")
    a = _anfragen(db, f, k).antwort
    db.commit()
    assert a.status == "bestaetigt"
    erg = online_buchung.storniere_fuer_kunde(db, kunde=k, buchung_id=a.buchung_id)
    _nachlauf(db, erg)
    assert erg.antwort.kostenfrei is True
    assert k.guthaben == Decimal("30.00")
    assert any(m["betreff"] == "Stornierung Ihrer Buchung" for m in mail_ausgang)


def test_storno_fremder_buchung(db: Session, welt) -> None:
    f, k, andere = welt
    a = _anfragen(db, f, andere).antwort
    db.commit()
    erg = online_buchung.storniere_fuer_kunde(db, kunde=k, buchung_id=a.buchung_id)
    assert erg.antwort.status == "abgelehnt" and erg.antwort.grund == "nicht_gefunden"
    assert db.get(Buchung, a.buchung_id).status == "reserviert"
    unbekannt = online_buchung.storniere_fuer_kunde(db, kunde=k, buchung_id=uuid.uuid4())
    assert unbekannt.antwort.grund == "nicht_gefunden"


def test_storno_nach_beginn_zu_spaet(db: Session, welt) -> None:
    f, k, _ = welt
    b = buchungen.lege_an(
        db, feld_id=f.id, kunde_id=k.id, beginn=kombiniere(D, time(9)), ende=kombiniere(D, time(10))
    )
    db.commit()
    clock.set_override(db, date(2027, 12, 2))
    erg = online_buchung.storniere_fuer_kunde(db, kunde=k, buchung_id=b.id)
    assert erg.antwort.grund == "zu_spaet"


def test_verfall_job_bucht_zurueck_und_mailt(db: Session, welt, mail_ausgang) -> None:
    f, k, _ = welt
    guthaben.buche(db, kunde=k, betrag=Decimal("10.00"), art="manuell")
    a = _anfragen(db, f, k).antwort
    db.commit()
    assert jobs.verfall_ausfuehren(db) == 0
    clock.set_override(db, date(2027, 11, 26))
    assert jobs.verfall_ausfuehren(db) == 1
    assert db.get(Buchung, a.buchung_id).status == "verfallen"
    assert k.guthaben == Decimal("10.00")
    assert any(m["betreff"] == "Reservierung verfallen" for m in mail_ausgang)
    assert jobs.verfall_ausfuehren(db) == 0
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && ../.venv/bin/pytest -q tests/test_online_buchung.py`
Expected: FAIL mit `ImportError: cannot import name 'online_buchung'`

- [ ] **Step 3: Ergebnis-Typ**

`core/beachhub_core/services/ergebnis.py`:
```python
"""Ergebnis einer Anfrageverarbeitung: die Antwort ans Portal und was nach dem Commit geschieht.

Mails, Rechnungs-PDFs und Betreiber-Alarme dürfen erst laufen, wenn die Transaktion steht –
sonst ginge eine Bestätigung hinaus, die ein Rollback wieder zurücknimmt.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from beachhub_shared import kanal
from sqlalchemy.orm import Session

Nachlauf = Callable[[Session], None]


@dataclass
class Ergebnis:
    antwort: kanal.Antwort
    nach_commit: list[Nachlauf] = field(default_factory=list)


def ok(**felder: Any) -> Ergebnis:
    return Ergebnis(kanal.Antwort(status="ok", **felder))


def abgelehnt(grund: str) -> Ergebnis:
    return Ergebnis(kanal.Antwort(status="abgelehnt", grund=grund))


def ignoriert(grund: str) -> Ergebnis:
    return Ergebnis(kanal.Antwort(status="ignoriert", grund=grund))
```

- [ ] **Step 4: Online-Buchung implementieren**

`core/beachhub_core/services/online_buchung.py`:
```python
"""Portal-Buchungen: Reservierung mit Online-Zahlung, Bestätigung, Storno und Verfall.

A-ZAHL-1 bis -4. Jede Funktion arbeitet in der Transaktion des Aufrufers und committet nicht.
"""

import logging
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from beachhub_shared import kanal
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from beachhub_core import clock, zahlung
from beachhub_core.models import Buchung, GuthabenBuchung, Kunde, Rechnung, Storno, Zahlung, utcnow
from beachhub_core.services import (
    benachrichtigung,
    buchungen,
    guthaben,
    konfiguration,
    rechnung_pdf,
    rechnungen,
    storno,
)
from beachhub_core.services.ergebnis import Ergebnis, Nachlauf, abgelehnt, ignoriert, ok
from beachhub_core.services.rechnungen import RechnungsFehler

logger = logging.getLogger(__name__)
NULL = Decimal("0.00")

# lege_an kennt feinere Gründe als der Kanalvertrag (Hauptspec § 8.1).
_GRUND = {"vergangenheit": "ausserhalb_fenster", "kunde_unbekannt": "konto_gesperrt"}

__all__ = [
    "Ergebnis",
    "anfragen",
    "storniere_fuer_kunde",
    "verfalle_abgelaufene",
    "zahlung_eingegangen",
]


def _bestaetigung_versenden(buchung_id: uuid.UUID, rechnung_id: uuid.UUID) -> Nachlauf:
    def lauf(db: Session) -> None:
        b, r = db.get(Buchung, buchung_id), db.get(Rechnung, rechnung_id)
        if b is None or r is None:
            return
        benachrichtigung.buchung_bestaetigt(db, b)
        try:
            rechnung_pdf.erzeuge(db, r)
            db.commit()
        except RechnungsFehler:
            logger.exception("PDF-Erzeugung für Rechnung %s fehlgeschlagen", r.nummer)
            db.rollback()
            return
        benachrichtigung.rechnung(db, r)

    return lauf


def _storno_mail(storno_id: uuid.UUID) -> Nachlauf:
    def lauf(db: Session) -> None:
        s = db.get(Storno, storno_id)
        if s is not None:
            benachrichtigung.storno(db, s)

    return lauf


def _alarm(betreff: str, text: str) -> Nachlauf:
    def lauf(db: Session) -> None:
        benachrichtigung.betreiber_alarm(betreff, text)

    return lauf


def _bestaetige(db: Session, b: Buchung, antwort: kanal.Antwort) -> Ergebnis:
    buchungen.setze_status(db, b, Buchung.BESTAETIGT, quelle="portal")
    b.reserviert_bis = None
    r = rechnungen.erzeuge_einzelrechnung(db, b, quelle="portal")
    return Ergebnis(antwort, [_bestaetigung_versenden(b.id, r.id)])


def _buche_verrechnung_zurueck(db: Session, b: Buchung, quelle: str) -> None:
    """Gibt das bei der Reservierung verrechnete Guthaben zurück. Zählt bereits erfolgte
    Rückbuchungen mit, damit ein zweiter Aufruf nichts doppelt gutschreibt."""
    summe = db.scalar(
        select(func.coalesce(func.sum(GuthabenBuchung.betrag), 0)).where(
            GuthabenBuchung.bezug_id == b.id,
            GuthabenBuchung.art.in_(("verrechnung", "rueckbuchung")),
        )
    )
    offen = -Decimal(str(summe))
    if offen > NULL:
        guthaben.buche(
            db,
            kunde=b.kunde,
            betrag=offen,
            art="rueckbuchung",
            bezug_id=b.id,
            notiz="Reservierung ohne Zahlung beendet",
            quelle=quelle,
        )


def anfragen(
    db: Session,
    *,
    kunde: Kunde,
    anfrage_id: uuid.UUID,
    feld_id: uuid.UUID,
    beginn: datetime,
    ende: datetime,
    rueckkehr_url: str,
) -> Ergebnis:
    if kunde.anonymisiert_am is not None:
        return abgelehnt("konto_gesperrt")
    try:
        with db.begin_nested():
            b = buchungen.lege_an(
                db,
                feld_id=feld_id,
                kunde_id=kunde.id,
                beginn=beginn,
                ende=ende,
                quelle="portal",
                pruefe_fenster=True,
                status=Buchung.RESERVIERT,
                anfrage_id=anfrage_id,
                zahlungsart="online",
            )
    except buchungen.BuchungsFehler as e:
        return abgelehnt(_GRUND.get(e.grund, e.grund))
    except IntegrityError:
        # Exklusionsconstraint: Ein gleichzeitiger Kunde war schneller.
        return abgelehnt("belegt")

    db.refresh(kunde)
    verrechnet = min(kunde.guthaben, b.preis)
    if verrechnet > NULL:
        guthaben.buche(
            db,
            kunde=kunde,
            betrag=-verrechnet,
            art="verrechnung",
            bezug_id=b.id,
            notiz="Verrechnung mit Portal-Buchung",
            quelle="portal",
        )
    rest = b.preis - verrechnet
    if rest == NULL:
        antwort = kanal.Antwort(
            status="bestaetigt", buchung_id=b.id, preis=b.preis, guthaben_verrechnet=verrechnet
        )
        return _bestaetige(db, b, antwort)

    anbieter = zahlung.anbieter()
    ablauf = clock.now(db) + timedelta(minutes=konfiguration.hole(db, "zahlungsfrist_minuten"))
    sitzung = anbieter.erzeuge_sitzung(
        betrag=rest, referenz=b.id, ablauf=ablauf, rueckkehr_url=rueckkehr_url
    )
    db.add(
        Zahlung(
            kunde_id=kunde.id,
            buchung_id=b.id,
            provider=anbieter.name,
            provider_ref=sitzung.provider_ref,
            betrag=rest,
            checkout_url=sitzung.checkout_url,
        )
    )
    b.reserviert_bis = ablauf
    db.flush()
    return Ergebnis(
        kanal.Antwort(
            status="reserviert",
            buchung_id=b.id,
            preis=b.preis,
            guthaben_verrechnet=verrechnet,
            zu_zahlen=rest,
            checkout_url=sitzung.checkout_url,
            reserviert_bis=ablauf,
        )
    )


def zahlung_eingegangen(db: Session, n: kanal.ZahlungEingegangen) -> Ergebnis:
    anbieter = zahlung.anbieter_fuer(n.provider)
    if anbieter is None:
        return ignoriert("anbieter_unbekannt")
    ref = anbieter.verifiziere(n.rohdaten, n.signatur_header)
    if ref is None:
        logger.warning("Zahlungsrückmeldung (%s) nicht verifizierbar: %.200s", n.provider, n.rohdaten)
        return ignoriert("nicht_verifiziert")
    z = db.scalar(select(Zahlung).where(Zahlung.provider_ref == ref).with_for_update())
    if z is None:
        logger.warning("Zahlungsrückmeldung zu unbekannter Referenz %s", ref)
        return ignoriert("zahlung_unbekannt")
    if z.status == Zahlung.BEZAHLT:
        return ok()
    # Dem Anbieter trauen, nie den Rohdaten (A-ZAHL-2).
    zustand, betrag = anbieter.status(ref)
    if zustand == "offen":
        return ignoriert("offen")
    if zustand != "bezahlt":
        z.status = Zahlung.ABGEBROCHEN
        return ok()

    z.status = Zahlung.BEZAHLT
    z.empfangen_am = utcnow()
    z.rohdaten_json = {"provider": n.provider, "rohdaten": n.rohdaten[:4000]}
    b = (
        db.scalar(select(Buchung).where(Buchung.id == z.buchung_id).with_for_update())
        if z.buchung_id
        else None
    )
    if b is not None and b.status == Buchung.RESERVIERT and betrag >= z.betrag:
        return _bestaetige(db, b, kanal.Antwort(status="ok"))

    # Geld ist da, aber es gibt nichts (mehr) zu bestätigen: Guthaben, der Betreiber entscheidet.
    if betrag >= z.betrag:
        grund = "Zahlung nach Verfall oder Storno"
    else:
        grund = "Zahlung unter dem offenen Betrag"
    if betrag > NULL:
        guthaben.buche(
            db,
            kunde=z.kunde,
            betrag=betrag,
            art="ueberzahlung",
            bezug_id=z.id,
            notiz=grund,
            quelle="portal",
        )
    text = (
        f"Die Zahlung {ref} über {betrag} € zur Buchung {z.buchung_id} ist eingegangen, "
        f"konnte aber nichts bestätigen ({grund}). Der Betrag wurde dem Kunden "
        f"{z.kunde.name} <{z.kunde.email}> als Guthaben gutgeschrieben."
    )
    return Ergebnis(kanal.Antwort(status="ok"), [_alarm(grund, text)])


def storniere_fuer_kunde(db: Session, *, kunde: Kunde, buchung_id: uuid.UUID) -> Ergebnis:
    b = db.scalar(select(Buchung).where(Buchung.id == buchung_id).with_for_update())
    if (
        b is None
        or b.kunde_id != kunde.id
        or b.status not in (Buchung.RESERVIERT, Buchung.BESTAETIGT)
    ):
        return abgelehnt("nicht_gefunden")
    if clock.now(db) >= b.beginn:
        return abgelehnt("zu_spaet")
    war_reserviert = b.status == Buchung.RESERVIERT
    s = storno.storniere(
        db,
        b,
        durch="kunde",
        grund="Storno im Portal",
        # Eine Reservierung ist nie bezahlt worden; ihr Storno kostet nichts.
        kostenfrei=True if war_reserviert else None,
    )
    if war_reserviert:
        _buche_verrechnung_zurueck(db, b, quelle="portal")
    return Ergebnis(kanal.Antwort(status="ok", kostenfrei=s.kostenfrei), [_storno_mail(s.id)])


def verfalle_abgelaufene(db: Session) -> list[Buchung]:
    jetzt = clock.now(db)
    abgelaufen = list(
        db.scalars(
            select(Buchung)
            .where(
                Buchung.status == Buchung.RESERVIERT,
                Buchung.reserviert_bis.is_not(None),
                Buchung.reserviert_bis < jetzt,
            )
            .with_for_update(skip_locked=True)
        ).all()
    )
    for b in abgelaufen:
        buchungen.setze_status(db, b, Buchung.VERFALLEN, quelle="system")
        _buche_verrechnung_zurueck(db, b, quelle="system")
    return abgelaufen
```

Hinweis zu `storno.storniere` mit `kostenfrei=None`: Der Parameter ist bereits `bool | None`; `None` bedeutet „nach Frist berechnen“ (bestehendes Verhalten).

- [ ] **Step 5: Mail und Job**

`core/beachhub_core/templates/mail/zahlungsfrist_abgelaufen.txt`:
```
Hallo {{ b.kunde.name }},

Ihre Reservierung wurde nicht rechtzeitig bezahlt und ist verfallen:
Feld {{ b.feld.name }}, {{ b.beginn|lokal }} bis {{ b.ende|uhrzeit }} Uhr

Der Platz ist wieder frei. Solange er verfügbar ist, können Sie ihn im Portal erneut buchen.
Mit der Reservierung verrechnetes Guthaben steht Ihnen wieder zur Verfügung.

Viele Grüße
{{ betreiber }}
```

`core/beachhub_core/services/benachrichtigung.py` – nach `storno` ergänzen:
```python
def zahlungsfrist_abgelaufen(db: Session, b: Buchung) -> None:
    mail.sende(b.kunde.email, "Reservierung verfallen", _text("zahlungsfrist_abgelaufen", b=b))
```

`core/beachhub_core/jobs.py`:
- Import ergänzen: `from beachhub_core.services import benachrichtigung, konfiguration, online_buchung, rechnung_pdf, rechnungen`
- Nach `monatslauf_ausfuehren` einfügen:
```python
def verfall_ausfuehren(db: Session) -> int:
    """Reservierungen mit abgelaufener Zahlungsfrist verfallen lassen (A-ZAHL-3)."""
    verfallen = online_buchung.verfalle_abgelaufene(db)
    db.commit()
    for b in verfallen:
        benachrichtigung.zahlungsfrist_abgelaufen(db, b)
    return len(verfallen)
```
- Nach `_job_lesestand` einfügen:
```python
def _job_verfall() -> None:
    with SessionLocal() as db:
        try:
            verfall_ausfuehren(db)
        except Exception:
            logger.exception("Verfall der Reservierungen fehlgeschlagen")
```
- In `starte_scheduler` vor `s.start()`:
```python
    s.add_job(_job_verfall, IntervalTrigger(minutes=1), id="verfall", replace_existing=True)
```

- [ ] **Step 6: Tests grün**

Run: `cd core && ../.venv/bin/pytest -q tests/test_online_buchung.py tests/test_jobs.py`
Expected: PASS

- [ ] **Step 7: Lint, Typen, Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy
git add core/beachhub_core/services/ergebnis.py core/beachhub_core/services/online_buchung.py \
  core/beachhub_core/services/benachrichtigung.py core/beachhub_core/templates/mail/zahlungsfrist_abgelaufen.txt \
  core/beachhub_core/jobs.py core/tests/test_online_buchung.py
git commit -m "feat(core): Online-Buchung mit Reservierung, Zahlungseingang, Storno und Verfall"
```

---

## Task 5: core – Anfrageverarbeitung (idempotent)

**Files:**
- Create: `core/beachhub_core/services/anfragen.py`
- Test: `core/tests/test_anfragen.py`

**Interfaces:**
- Consumes: `online_buchung.*`, `ergebnis.*` (Task 4); `AnfrageVerarbeitet` (Task 2); `kanal.Anfrage`, `kanal.NUTZLAST` (Task 1); `rechnung_pdf.pruefe_integritaet`, `kunden.lege_an`, `kunden.anonymisiere`, `lesestand.markiere_geaendert` (bestehend)
- Produces: `anfragen.verarbeite(db, anfrage: kanal.Anfrage) -> Ergebnis` (committet nicht)
- Produces: `anfragen.bearbeite(db, anfrage: kanal.Anfrage) -> tuple[kanal.Antwort, list[Nachlauf]]` – genau einmal: liegt `AnfrageVerarbeitet` vor, kommt die gespeicherte Antwort (ohne Nachlauf); sonst verarbeiten, Antwort speichern, **committen**. Unerwartete Ausnahme → Rollback, Antwort `fehler` gespeichert, Nachlauf = Betreiber-Alarm.
- Antworten je Typ wie Spec § 4; zusätzlich `abgelehnt/keine_kundengruppe`, wenn es keine Kundengruppe gibt.

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_anfragen.py`:
```python
import base64
import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path

import pytest
from beachhub_core import clock
from beachhub_core.config import settings
from beachhub_core.models import (
    AnfrageVerarbeitet,
    Betriebszeit,
    Buchung,
    Feld,
    FeldRaster,
    Kunde,
    Kundengruppe,
    LesestandVersion,
    Tarif,
)
from beachhub_core.services import anfragen, buchungen, konfiguration, kunden, rechnung_pdf, rechnungen
from beachhub_shared import kanal
from beachhub_shared.zeit import kombiniere
from sqlalchemy import select
from sqlalchemy.orm import Session

D = date(2027, 12, 1)


def anfrage(typ: str, konto_id: uuid.UUID | None = None, **nutzlast) -> kanal.Anfrage:
    return kanal.Anfrage(
        anfrage_id=uuid.uuid4(),
        typ=typ,
        konto_id=konto_id,
        nutzlast=nutzlast,
        erstellt_am=datetime.now(UTC),
    )


@pytest.fixture
def welt(db: Session):
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    p, v = Kundengruppe(name="Privat"), Kundengruppe(name="Verein")
    db.add_all([f, p, v, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    return f, p, v


def _angelegt(db: Session, konto_id: uuid.UUID, email: str = "anna@x.de") -> Kunde:
    antwort, _ = anfragen.bearbeite(
        db, anfrage("konto_angelegt", konto_id, email=email, anzeigename="Anna")
    )
    assert antwort.status == "ok"
    return db.get(Kunde, antwort.kunde_id)


def test_konto_angelegt_legt_kunden_an(db: Session, welt) -> None:
    _, p, _ = welt
    konto = uuid.uuid4()
    k = _angelegt(db, konto, email="  Anna@X.de ")
    assert k.email == "anna@x.de" and k.name == "Anna"
    assert k.portal_konto_id == konto and k.zahlungsart == "online"
    assert k.kundengruppe_id == p.id  # erste Gruppe nach Name: "Privat" < "Verein"
    assert db.get(LesestandVersion, f"konto:{k.id}").geaendert


def test_konto_angelegt_nutzt_konfigurierte_gruppe(db: Session, welt) -> None:
    _, _, v = welt
    konfiguration.setze(db, "portal_kundengruppe", "Verein")
    db.commit()
    assert _angelegt(db, uuid.uuid4()).kundengruppe_id == v.id
    konfiguration.setze(db, "portal_kundengruppe", "Gibtsnicht")
    db.commit()
    assert _angelegt(db, uuid.uuid4(), "b@x.de").kundengruppe.name == "Privat"


def test_konto_angelegt_verknuepft_bestehenden_kunden(db: Session, welt) -> None:
    _, _, v = welt
    alt = kunden.lege_an(db, name="Anna Abo", email="anna@x.de", kundengruppe_id=v.id)
    db.commit()
    k = _angelegt(db, uuid.uuid4())
    assert k.id == alt.id and k.name == "Anna Abo"
    neu = uuid.uuid4()
    assert _angelegt(db, neu).id == alt.id  # Portal wiederhergestellt: neue Konto-ID
    assert db.get(Kunde, alt.id).portal_konto_id == neu


def test_konto_angelegt_zweimal_gleicher_kunde(db: Session, welt) -> None:
    konto = uuid.uuid4()
    assert _angelegt(db, konto).id == _angelegt(db, konto).id
    assert len(db.scalars(select(Kunde)).all()) == 1


def test_ohne_kundengruppe(db: Session) -> None:
    antwort, _ = anfragen.bearbeite(
        db, anfrage("konto_angelegt", uuid.uuid4(), email="a@x.de", anzeigename="A")
    )
    assert antwort.status == "abgelehnt" and antwort.grund == "keine_kundengruppe"


def test_unbekannt_ungueltig_und_konto_unbekannt(db: Session, welt) -> None:
    assert anfragen.verarbeite(db, anfrage("gibtsnicht")).antwort.grund == "unbekannter_typ"
    kaputt = anfrage("buchung_anfragen", uuid.uuid4(), feld_id="x")
    assert anfragen.verarbeite(db, kaputt).antwort.grund == "ungueltig"
    fremd = anfrage("buchung_stornieren", uuid.uuid4(), buchung_id=str(uuid.uuid4()))
    assert anfragen.verarbeite(db, fremd).antwort.grund == "konto_unbekannt"


def test_zahlung_eingegangen_braucht_kein_konto(db: Session, welt) -> None:
    a = anfrage("zahlung_eingegangen", None, provider="fake", rohdaten="{}")
    assert anfragen.verarbeite(db, a).antwort.grund == "nicht_verifiziert"


def test_konto_geaendert_nur_wenn_name_unveraendert(db: Session, welt) -> None:
    konto = uuid.uuid4()
    k = _angelegt(db, konto)
    anfragen.bearbeite(db, anfrage("konto_geaendert", konto, anzeigename="Anni", bisher="Anna"))
    assert db.get(Kunde, k.id).name == "Anni"
    k.name = "Anna Beispiel"  # vom Betreiber gepflegt
    db.commit()
    anfragen.bearbeite(db, anfrage("konto_geaendert", konto, anzeigename="A.", bisher="Anni"))
    assert db.get(Kunde, k.id).name == "Anna Beispiel"


def test_konto_loeschen_anonymisiert(db: Session, welt) -> None:
    konto = uuid.uuid4()
    k = _angelegt(db, konto)
    antwort, _ = anfragen.bearbeite(db, anfrage("konto_loeschen", konto))
    assert antwort.status == "ok"
    k = db.get(Kunde, k.id)
    assert k.anonymisiert_am is not None and k.portal_konto_id is None
    assert k.name == "Gelöschter Kunde"


def test_buchung_anfragen_mit_rueckkehradresse(
    db: Session, welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    f, _, _ = welt
    konto = uuid.uuid4()
    _angelegt(db, konto)
    monkeypatch.setattr(settings, "portal_url", "https://portal.example:8443")
    a = anfrage(
        "buchung_anfragen",
        konto,
        feld_id=str(f.id),
        beginn=kombiniere(D, time(19)).isoformat(),
        ende=kombiniere(D, time(20)).isoformat(),
    )
    antwort, _ = anfragen.bearbeite(db, a)
    assert antwort.status == "reserviert"
    assert f"zurueck=https%3A%2F%2Fportal.example%3A8443%2Fzahlung%2Fzurueck%3Fanfrage%3D{a.anfrage_id}" in antwort.checkout_url
    assert db.get(Buchung, antwort.buchung_id).anfrage_id == a.anfrage_id


def test_rechnung_nur_eigene(db: Session, welt) -> None:
    f, p, _ = welt
    konto = uuid.uuid4()
    k = _angelegt(db, konto)
    fremd = kunden.lege_an(db, name="B", email="b@x.de", kundengruppe_id=p.id)
    eigene = []
    for kunde, stunde in ((k, 19), (fremd, 20)):
        b = buchungen.lege_an(
            db,
            feld_id=f.id,
            kunde_id=kunde.id,
            beginn=kombiniere(D, time(stunde)),
            ende=kombiniere(D, time(stunde + 1)),
            zahlungsart="online",
        )
        eigene.append(rechnungen.erzeuge_einzelrechnung(db, b))
    db.commit()
    for r in eigene:
        rechnung_pdf.erzeuge(db, r)
    db.commit()
    mein, sein = eigene
    antwort, _ = anfragen.bearbeite(db, anfrage("rechnung_anfordern", konto, rechnung_nr=mein.nummer))
    assert antwort.status == "ok" and antwort.dateiname == f"Rechnung-{mein.nummer}.pdf"
    assert base64.b64decode(antwort.pdf_base64) == Path(mein.pdf_pfad).read_bytes()
    antwort, _ = anfragen.bearbeite(db, anfrage("rechnung_anfordern", konto, rechnung_nr=sein.nummer))
    assert antwort.grund == "nicht_gefunden"


def test_rechnung_ohne_intaktes_pdf(db: Session, welt, mail_ausgang) -> None:
    f, _, _ = welt
    konto = uuid.uuid4()
    k = _angelegt(db, konto)
    b = buchungen.lege_an(
        db, feld_id=f.id, kunde_id=k.id, beginn=kombiniere(D, time(19)), ende=kombiniere(D, time(20))
    )
    r = rechnungen.erzeuge_einzelrechnung(db, b)
    db.commit()
    antwort, _ = anfragen.bearbeite(db, anfrage("rechnung_anfordern", konto, rechnung_nr=r.nummer))
    assert antwort.grund == "nicht_gefunden"  # noch kein PDF
    rechnung_pdf.erzeuge(db, r)
    db.commit()
    Path(r.pdf_pfad).write_bytes(b"manipuliert")
    antwort, nachlauf = anfragen.bearbeite(
        db, anfrage("rechnung_anfordern", konto, rechnung_nr=r.nummer)
    )
    assert antwort.grund == "nicht_gefunden" and len(nachlauf) == 1
    nachlauf[0](db)
    assert mail_ausgang[-1]["betreff"] == "[Beachhub] Rechnungs-PDF beschädigt"


def test_bearbeite_ist_idempotent(db: Session, welt) -> None:
    a = anfrage("konto_angelegt", uuid.uuid4(), email="a@x.de", anzeigename="A")
    erste, _ = anfragen.bearbeite(db, a)
    zweite, nachlauf = anfragen.bearbeite(db, a)
    assert erste == zweite and nachlauf == []
    assert db.get(AnfrageVerarbeitet, a.anfrage_id).typ == "konto_angelegt"
    assert len(db.scalars(select(Kunde)).all()) == 1


def test_ausnahme_wird_fehler_mit_alarm(
    db: Session, welt, monkeypatch: pytest.MonkeyPatch, mail_ausgang
) -> None:
    def kaputt(*args, **kwargs):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(anfragen, "_konto_angelegt", kaputt)
    a = anfrage("konto_angelegt", uuid.uuid4(), email="a@x.de", anzeigename="A")
    antwort, nachlauf = anfragen.bearbeite(db, a)
    assert antwort.status == "fehler"
    assert db.get(AnfrageVerarbeitet, a.anfrage_id).antwort_json == {"status": "fehler"}
    for schritt in nachlauf:
        schritt(db)
    assert mail_ausgang[-1]["betreff"] == "[Beachhub] Portal-Anfrage fehlgeschlagen"
    assert db.scalars(select(Kunde)).all() == []
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && ../.venv/bin/pytest -q tests/test_anfragen.py`
Expected: FAIL mit `ImportError: cannot import name 'anfragen'`

- [ ] **Step 3: Implementieren**

`core/beachhub_core/services/anfragen.py`:
```python
"""Verarbeitung der Portal-Anfragen im Hauptsystem (Spec Portal-Kern § 4).

Das Portal ist ein Briefkasten: Jede Nutzlast ist nicht vertrauenswürdig. Den Kunden ermittelt
das Hauptsystem selbst über `kunde.portal_konto_id`, nie über ein Feld der Anfrage.
"""

import base64
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from beachhub_shared import kanal
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from beachhub_core.config import settings
from beachhub_core.models import AnfrageVerarbeitet, Kunde, Kundengruppe, Rechnung
from beachhub_core.services import (
    audit,
    benachrichtigung,
    konfiguration,
    kunden,
    lesestand,
    online_buchung,
    rechnung_pdf,
)
from beachhub_core.services.ergebnis import Ergebnis, Nachlauf, abgelehnt, ok

logger = logging.getLogger(__name__)

Verarbeiter = Callable[[Session, Kunde, kanal.Anfrage, Any], Ergebnis]


def _alarm(betreff: str, text: str) -> Nachlauf:
    def lauf(db: Session) -> None:
        benachrichtigung.betreiber_alarm(betreff, text)

    return lauf


def _kunde_zum_konto(db: Session, konto_id: Any) -> Kunde | None:
    if konto_id is None:
        return None
    return db.scalar(select(Kunde).where(Kunde.portal_konto_id == konto_id))


def _portal_gruppe(db: Session) -> Kundengruppe | None:
    name = str(konfiguration.hole(db, "portal_kundengruppe")).strip()
    if name:
        gruppe = db.scalar(select(Kundengruppe).where(Kundengruppe.name == name))
        if gruppe is not None:
            return gruppe
        logger.warning("Kundengruppe %r aus portal_kundengruppe fehlt – nehme die erste", name)
    return db.scalar(select(Kundengruppe).order_by(Kundengruppe.name).limit(1))


def _konto_angelegt(db: Session, anfrage: kanal.Anfrage, n: kanal.KontoAngelegt) -> Ergebnis:
    if anfrage.konto_id is None:
        return abgelehnt("ungueltig")
    k = _kunde_zum_konto(db, anfrage.konto_id)
    if k is None:
        email = n.email.strip().lower()
        k = db.scalar(select(Kunde).where(Kunde.email == email).with_for_update())
        if k is None:
            gruppe = _portal_gruppe(db)
            if gruppe is None:
                return abgelehnt("keine_kundengruppe")
            k = kunden.lege_an(
                db,
                name=n.anzeigename.strip(),
                email=email,
                kundengruppe_id=gruppe.id,
                zahlungsart="online",
                quelle="portal",
            )
        vorher = audit.als_dict(k)
        # Das Portal hat die Adresse per Login-Code bestätigt. Eine abweichende alte Verknüpfung
        # stammt aus einem gelöschten oder wiederhergestellten Portal und wird ersetzt.
        k.portal_konto_id = anfrage.konto_id
        db.flush()
        audit.protokolliere(
            db,
            quelle="portal",
            objekt_typ="kunde",
            objekt_id=k.id,
            vorher=vorher,
            nachher=audit.als_dict(k),
        )
    lesestand.markiere_geaendert(db, f"konto:{k.id}")
    return ok(kunde_id=k.id)


def _konto_geaendert(
    db: Session, kunde: Kunde, anfrage: kanal.Anfrage, n: kanal.KontoGeaendert
) -> Ergebnis:
    neu = n.anzeigename.strip()
    # Hat der Betreiber den Namen inzwischen gepflegt (etwa für die Rechnung), bleibt er.
    if kunde.name == n.bisher and neu and neu != kunde.name:
        vorher = audit.als_dict(kunde)
        kunde.name = neu
        db.flush()
        audit.protokolliere(
            db,
            quelle="portal",
            objekt_typ="kunde",
            objekt_id=kunde.id,
            vorher=vorher,
            nachher=audit.als_dict(kunde),
        )
        lesestand.markiere_geaendert(db, f"konto:{kunde.id}")
    return ok()


def _konto_loeschen(
    db: Session, kunde: Kunde, anfrage: kanal.Anfrage, n: kanal.KontoLoeschen
) -> Ergebnis:
    kunden.anonymisiere(db, kunde)
    lesestand.markiere_geaendert(db, f"konto:{kunde.id}")
    return ok()


def _buchung_anfragen(
    db: Session, kunde: Kunde, anfrage: kanal.Anfrage, n: kanal.BuchungAnfragen
) -> Ergebnis:
    rueckkehr = f"{settings.portal_url.rstrip('/')}/zahlung/zurueck?anfrage={anfrage.anfrage_id}"
    return online_buchung.anfragen(
        db,
        kunde=kunde,
        anfrage_id=anfrage.anfrage_id,
        feld_id=n.feld_id,
        beginn=n.beginn,
        ende=n.ende,
        rueckkehr_url=rueckkehr,
    )


def _buchung_stornieren(
    db: Session, kunde: Kunde, anfrage: kanal.Anfrage, n: kanal.BuchungStornieren
) -> Ergebnis:
    return online_buchung.storniere_fuer_kunde(db, kunde=kunde, buchung_id=n.buchung_id)


def _rechnung_anfordern(
    db: Session, kunde: Kunde, anfrage: kanal.Anfrage, n: kanal.RechnungAnfordern
) -> Ergebnis:
    r = db.scalar(
        select(Rechnung).where(Rechnung.nummer == n.rechnung_nr, Rechnung.kunde_id == kunde.id)
    )
    if r is None or not r.pdf_pfad:
        return abgelehnt("nicht_gefunden")
    if not rechnung_pdf.pruefe_integritaet(r):
        text = (
            f"Das archivierte PDF der Rechnung {r.nummer} fehlt oder stimmt nicht mit seiner "
            "Prüfsumme überein. Der Kunde konnte es im Portal nicht abrufen."
        )
        erg = abgelehnt("nicht_gefunden")
        erg.nach_commit.append(_alarm("Rechnungs-PDF beschädigt", text))
        return erg
    daten = Path(r.pdf_pfad).read_bytes()
    return ok(pdf_base64=base64.b64encode(daten).decode(), dateiname=f"Rechnung-{r.nummer}.pdf")


_MIT_KUNDE: dict[str, Verarbeiter] = {
    "konto_geaendert": _konto_geaendert,
    "konto_loeschen": _konto_loeschen,
    "buchung_anfragen": _buchung_anfragen,
    "buchung_stornieren": _buchung_stornieren,
    "rechnung_anfordern": _rechnung_anfordern,
}


def verarbeite(db: Session, anfrage: kanal.Anfrage) -> Ergebnis:
    schema = kanal.NUTZLAST.get(anfrage.typ)
    if schema is None:
        return abgelehnt("unbekannter_typ")
    try:
        n = schema.model_validate(anfrage.nutzlast)
    except ValidationError:
        return abgelehnt("ungueltig")
    if isinstance(n, kanal.KontoAngelegt):
        return _konto_angelegt(db, anfrage, n)
    if isinstance(n, kanal.ZahlungEingegangen):
        # Rückmeldungen des Anbieters kommen ohne Konto; die Zuordnung läuft über die Referenz.
        return online_buchung.zahlung_eingegangen(db, n)
    kunde = _kunde_zum_konto(db, anfrage.konto_id)
    if kunde is None:
        return abgelehnt("konto_unbekannt")
    return _MIT_KUNDE[anfrage.typ](db, kunde, anfrage, n)


def _speichere(db: Session, anfrage: kanal.Anfrage, antwort: kanal.Antwort) -> None:
    db.add(
        AnfrageVerarbeitet(
            anfrage_id=anfrage.anfrage_id,
            typ=anfrage.typ,
            antwort_json=antwort.model_dump(mode="json", exclude_none=True),
        )
    )


def bearbeite(db: Session, anfrage: kanal.Anfrage) -> tuple[kanal.Antwort, list[Nachlauf]]:
    """Verarbeitet eine Anfrage genau einmal und committet. Eine erneut zugestellte Anfrage
    bekommt die gespeicherte Antwort, ohne dass etwas ein zweites Mal geschieht."""
    vorhanden = db.get(AnfrageVerarbeitet, anfrage.anfrage_id)
    if vorhanden is not None:
        return kanal.Antwort.model_validate(vorhanden.antwort_json), []
    try:
        erg = verarbeite(db, anfrage)
        _speichere(db, anfrage, erg.antwort)
        db.commit()
        return erg.antwort, erg.nach_commit
    except IntegrityError:
        db.rollback()
        vorhanden = db.get(AnfrageVerarbeitet, anfrage.anfrage_id)
        if vorhanden is not None:
            return kanal.Antwort.model_validate(vorhanden.antwort_json), []
        logger.exception("Anfrage %s (%s) fehlgeschlagen", anfrage.anfrage_id, anfrage.typ)
    except Exception:
        db.rollback()
        logger.exception("Anfrage %s (%s) fehlgeschlagen", anfrage.anfrage_id, anfrage.typ)
    fehler = kanal.Antwort(status="fehler")
    _speichere(db, anfrage, fehler)
    db.commit()
    text = (
        f"Die Portal-Anfrage {anfrage.anfrage_id} vom Typ {anfrage.typ} konnte nicht "
        "verarbeitet werden. Der Kunde sieht eine Fehlermeldung. Details im Log des Hauptsystems."
    )
    return fehler, [_alarm("Portal-Anfrage fehlgeschlagen", text)]
```

- [ ] **Step 4: Tests grün**

Run: `cd core && ../.venv/bin/pytest -q tests/test_anfragen.py`
Expected: PASS

- [ ] **Step 5: Lint, Typen, Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy
git add core/beachhub_core/services/anfragen.py core/tests/test_anfragen.py
git commit -m "feat(core): Portal-Anfragen genau einmal verarbeiten"
```

---
## Task 6: core – Kanal-Client (Long-Polling, Verteilen, Abgleich)

**Files:**
- Create: `core/beachhub_core/kanal.py`
- Modify: `core/beachhub_core/main.py` (Produktionsprüfung, lifespan)
- Modify: `core/tests/conftest.py` (Kanal in Tests aus)
- Modify: `mypy.ini`
- Test: `core/tests/test_kanal.py`

**Interfaces:**
- Consumes: `anfragen.bearbeite` (Task 5), `Nachlauf` (Task 4), `vertrag.fuer_portal`, `AnfrageListe`, `AntwortEintrag`, `AntwortListe`, `DokumentListe` (Task 1), `settings.portal_url/kanal_token/portal_client_cert/portal_client_key/portal_ca/enable_kanal/produktionsfehler` (Task 3), `lesestand.verarbeite_geaenderte`, `lesestand.lade` (bestehend)
- Produces: `kanal.baue_client() -> httpx.Client`
- Produces: `kanal.Kanal(client: httpx.Client, *, sitzung: Callable[[], Session] = SessionLocal, warten: int = 25, uhr: Callable[[], float] = time.monotonic)` mit
  - `abholen() -> int` – eine Long-Poll-Runde: abholen, je Anfrage `anfragen.bearbeite`, dann `verteilen()`, dann `POST /core/antworten`, zuletzt die Nachläufe (auch wenn das Senden scheitert); wirft `httpx.HTTPError`/`ValueError` bei Netz- oder Formatfehlern
  - `verteilen() -> int` – geänderte Lesestände veröffentlichen und nur die für das Portal erlaubten senden
  - `abgleichen() -> int` – `GET /core/lesestand/versionen`, fehlende oder ältere Dokumente aus `data/lesestand/` senden
  - `runde() -> float` – eine Runde der Abholschleife mit Fehlerbehandlung, liefert die Wartezeit (Backoff 1, 2, 4 … 60 s; Alarm „Portal nicht erreichbar“ einmal nach 30 min, „Portal wieder erreichbar“ beim ersten Erfolg danach)
  - `verteiler_runde() -> None` – Abgleich (beim Start, nach Fehlern, stündlich) und Verteilen; wirft nie
  - `starte()`, `stoppe()` – Threads `kanal-abholer` und `kanal-verteiler`
- Produces: Das Hauptsystem startet den Kanal im `lifespan`, wenn `ENABLE_KANAL` und `PORTAL_URL` gesetzt sind; mit `APP_ENV=production` verweigert es den Start bei `settings.produktionsfehler`.

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_kanal.py`:
```python
import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from beachhub_core import kanal
from beachhub_core.config import settings
from beachhub_core.models import Kunde, Kundengruppe
from beachhub_core.services import anfragen, lesestand
from beachhub_shared import kanal as vertrag
from sqlalchemy import select
from sqlalchemy.orm import Session

TOKEN = "t"


class FakePortal:
    """Spielt das Portal: liefert vorbereitete Anfragen und merkt sich alles, was ankommt."""

    def __init__(self) -> None:
        self.anfragen: list[dict] = []
        self.aufrufe: list[tuple[str, str]] = []
        self.antworten: list[dict] = []
        self.dokumente: list[dict] = []
        self.versionen: dict[str, int] = {}
        self.lesestand_status = 200
        self.kaputt = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.kaputt:
            raise httpx.ConnectError("Portal weg", request=request)
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        pfad = request.url.path
        self.aufrufe.append((request.method, pfad))
        if pfad == "/core/anfragen":
            liste, self.anfragen = self.anfragen, []
            return httpx.Response(200, json={"anfragen": liste})
        if pfad == "/core/antworten":
            neu = json.loads(request.content)["antworten"]
            self.antworten += neu
            return httpx.Response(200, json={"ok": len(neu)})
        if pfad == "/core/lesestand":
            self.dokumente += json.loads(request.content)["dokumente"]
            return httpx.Response(
                self.lesestand_status, json={"uebernommen": [], "verworfen": []}
            )
        if pfad == "/core/lesestand/versionen":
            return httpx.Response(200, json=self.versionen)
        return httpx.Response(404)


class Uhr:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def portal() -> FakePortal:
    return FakePortal()


@pytest.fixture
def uhr() -> Uhr:
    return Uhr()


@pytest.fixture
def k(portal: FakePortal, uhr: Uhr) -> kanal.Kanal:
    client = httpx.Client(
        transport=httpx.MockTransport(portal),
        base_url="http://portal",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    return kanal.Kanal(client, warten=0, uhr=uhr)


@pytest.fixture
def welt(db: Session) -> None:
    Path(settings.signatur_privatschluessel_pfad).unlink(missing_ok=True)
    lesestand.erzeuge_schluessel()
    db.add(Kundengruppe(name="Privat"))
    db.commit()


def konto_angelegt(email: str = "anna@x.de") -> dict:
    return vertrag.Anfrage(
        anfrage_id=uuid.uuid4(),
        typ="konto_angelegt",
        konto_id=uuid.uuid4(),
        nutzlast={"email": email, "anzeigename": "Anna"},
        erstellt_am=datetime.now(UTC),
    ).model_dump(mode="json")


def test_abholen_verarbeitet_und_antwortet(db: Session, welt, portal: FakePortal, k) -> None:
    portal.anfragen = [konto_angelegt()]
    assert k.abholen() == 1
    kunde = db.scalar(select(Kunde))
    assert portal.antworten[0]["antwort"] == {"status": "ok", "kunde_id": str(kunde.id)}
    assert f"konto:{kunde.id}" in [d["dokument"] for d in portal.dokumente]
    # Erst der Lesestand, dann die Antwort: Das Portal soll zeigen können, was es bestätigt.
    assert portal.aufrufe.index(("POST", "/core/lesestand")) < portal.aufrufe.index(
        ("POST", "/core/antworten")
    )


def test_leere_runde_sendet_nichts(welt, portal: FakePortal, k) -> None:
    assert k.abholen() == 0
    assert portal.aufrufe == [("GET", "/core/anfragen")]


def test_erneute_zustellung_gleiche_antwort(db: Session, welt, portal: FakePortal, k) -> None:
    a = konto_angelegt()
    portal.anfragen = [a]
    k.abholen()
    portal.anfragen = [a]
    k.abholen()
    assert portal.antworten[0] == portal.antworten[1]
    assert len(db.scalars(select(Kunde)).all()) == 1


def test_fehler_wird_beantwortet_und_alarmiert(
    welt, portal: FakePortal, k, monkeypatch: pytest.MonkeyPatch, mail_ausgang
) -> None:
    def kaputt(*args, **kwargs):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(anfragen, "_konto_angelegt", kaputt)
    portal.anfragen = [konto_angelegt()]
    k.abholen()
    assert portal.antworten[0]["antwort"] == {"status": "fehler"}
    assert any(m["betreff"] == "[Beachhub] Portal-Anfrage fehlgeschlagen" for m in mail_ausgang)


def test_verteilen_nur_portal_dokumente(
    db: Session, welt, portal: FakePortal, k, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesestand.markiere_geaendert(db, "belegung")
    db.commit()
    lesestand.verarbeite_geaenderte(db)
    beleg = lesestand.lade("belegung")
    hallenplan = beleg.model_copy(update={"dokument": "hallenplan"})
    monkeypatch.setattr(lesestand, "verarbeite_geaenderte", lambda db: ["belegung", "hallenplan"])
    monkeypatch.setattr(
        lesestand, "lade", lambda name: {"belegung": beleg, "hallenplan": hallenplan}[name]
    )
    assert k.verteilen() == 1
    assert [d["dokument"] for d in portal.dokumente] == ["belegung"]


def test_abgleich_schickt_fehlende_und_aeltere(db: Session, welt, portal: FakePortal, k) -> None:
    lesestand.markiere_geaendert(db, "belegung", "tarife")
    db.commit()
    lesestand.verarbeite_geaenderte(db)
    beleg = lesestand.lade("belegung")
    (settings.data_dir / "lesestand" / "hallenplan.json").write_text(
        beleg.model_copy(update={"dokument": "hallenplan"}).model_dump_json(), encoding="utf-8"
    )
    portal.versionen = {"belegung": 5}
    assert k.abgleichen() == 1
    assert [d["dokument"] for d in portal.dokumente] == ["tarife"]


def test_lesestand_abgelehnt_alarmiert(
    db: Session, welt, portal: FakePortal, k, mail_ausgang
) -> None:
    portal.lesestand_status = 422
    lesestand.markiere_geaendert(db, "belegung")
    db.commit()
    k.verteilen()
    assert mail_ausgang[-1]["betreff"] == "[Beachhub] Lesestand vom Portal abgelehnt"


def test_portal_ausfall_alarm_nach_30_minuten(
    welt, portal: FakePortal, k, uhr: Uhr, mail_ausgang
) -> None:
    portal.kaputt = True
    assert k.runde() == 1.0
    assert k.runde() == 2.0
    uhr.t += 29 * 60
    k.runde()
    assert mail_ausgang == []
    uhr.t += 2 * 60
    k.runde()
    k.runde()
    assert [m["betreff"] for m in mail_ausgang] == ["[Beachhub] Portal nicht erreichbar"]
    portal.kaputt = False
    assert k.runde() == 0.0
    assert mail_ausgang[-1]["betreff"] == "[Beachhub] Portal wieder erreichbar"


def test_verteiler_runde_wirft_nie_und_gleicht_danach_ab(welt, portal: FakePortal, k) -> None:
    portal.kaputt = True
    k.verteiler_runde()
    portal.kaputt = False
    k.verteiler_runde()
    assert ("GET", "/core/lesestand/versionen") in portal.aufrufe


def test_threads_laufen_und_stoppen(db: Session, welt, portal: FakePortal, k) -> None:
    portal.anfragen = [konto_angelegt()]
    k.starte()
    try:
        ende = time.monotonic() + 5
        while not portal.antworten and time.monotonic() < ende:
            time.sleep(0.05)
    finally:
        k.stoppe()
    assert portal.antworten[0]["antwort"]["status"] == "ok"


def test_baue_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "portal_url", "https://portal.example:8443")
    monkeypatch.setattr(settings, "kanal_token", "geheim")
    c = kanal.baue_client()
    assert c.base_url.host == "portal.example" and c.base_url.port == 8443
    assert c.headers["authorization"] == "Bearer geheim"
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && ../.venv/bin/pytest -q tests/test_kanal.py`
Expected: FAIL mit `ImportError: cannot import name 'kanal' from 'beachhub_core'`

- [ ] **Step 3: Implementieren**

`core/beachhub_core/kanal.py`:
```python
"""Kanal zum Portal (Spec Portal-Kern § 2–3).

Das Hauptsystem holt Anfragen per Long-Polling ab und liefert Antworten und Lesestände aus.
Alle Verbindungen gehen von hier aus; das Portal kann das Hauptsystem nicht erreichen (N-1).
Zwei Threads: der Abholer (Long-Poll, Verarbeitung, Antworten) und der Verteiler (geänderte
Lesestände alle 2 s, Abgleich der Versionen beim Start, nach Fehlern und stündlich).
"""

import logging
import ssl
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx
from beachhub_shared import kanal as vertrag
from beachhub_shared.lesestand import Dokument
from sqlalchemy.orm import Session

from beachhub_core.config import settings
from beachhub_core.database import SessionLocal
from beachhub_core.services import anfragen, benachrichtigung, lesestand
from beachhub_core.services.ergebnis import Nachlauf

logger = logging.getLogger(__name__)

AUSFALL_ALARM_SEKUNDEN = 30 * 60
ABGLEICH_SEKUNDEN = 60 * 60
VERTEIL_TAKT_SEKUNDEN = 2.0
MAX_BACKOFF_SEKUNDEN = 60.0


def baue_client() -> httpx.Client:
    """HTTP-Client mit Kanal-Token und, falls konfiguriert, Client-Zertifikat (mTLS)."""
    optionen: dict[str, Any] = {}
    if settings.portal_ca or settings.portal_client_cert:
        ctx = ssl.create_default_context(cafile=settings.portal_ca or None)
        if settings.portal_client_cert:
            ctx.load_cert_chain(settings.portal_client_cert, settings.portal_client_key or None)
        optionen["verify"] = ctx
    return httpx.Client(
        base_url=settings.portal_url,
        headers={"Authorization": f"Bearer {settings.kanal_token}"},
        timeout=httpx.Timeout(10.0, read=40.0),
        **optionen,
    )


class Kanal:
    def __init__(
        self,
        client: httpx.Client,
        *,
        sitzung: Callable[[], Session] = SessionLocal,
        warten: int = 25,
        uhr: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.sitzung = sitzung
        self.warten = warten
        self.uhr = uhr
        self._backoff = 1.0
        self._letzter_erfolg = uhr()
        self._ausfall_gemeldet = False
        self._abgleich_noetig = True
        self._letzter_abgleich = float("-inf")
        self._sende_sperre = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    # ---------- eine Runde ----------

    def abholen(self) -> int:
        r = self.client.get(
            "/core/anfragen",
            params={"warten": self.warten},
            timeout=httpx.Timeout(10.0, read=self.warten + 15.0),
        )
        r.raise_for_status()
        liste = vertrag.AnfrageListe.model_validate(r.json())
        self._erfolg()
        if not liste.anfragen:
            return 0
        eintraege: list[vertrag.AntwortEintrag] = []
        nachlauf: list[Nachlauf] = []
        with self.sitzung() as db:
            for a in liste.anfragen:
                antwort, nach = anfragen.bearbeite(db, a)
                eintraege.append(vertrag.AntwortEintrag(anfrage_id=a.anfrage_id, antwort=antwort))
                nachlauf.extend(nach)
        try:
            # Erst die Lesestände, dann die Antworten: Sobald das Portal eine Antwort sieht,
            # soll es die Buchung auch schon anzeigen können.
            self.verteilen()
            body = vertrag.AntwortListe(antworten=eintraege).model_dump(
                mode="json", exclude_none=True
            )
            self.client.post("/core/antworten", json=body).raise_for_status()
        finally:
            # Mails und PDFs hängen nicht am Portal: Sie laufen auch, wenn das Senden scheitert.
            # Die Antworten holt sich das Portal bei der erneuten Zustellung (idempotent).
            self._nachlauf(nachlauf)
        return len(eintraege)

    def verteilen(self) -> int:
        try:
            with self.sitzung() as db:
                namen = lesestand.verarbeite_geaenderte(db)
        except FileNotFoundError:
            logger.error("Signaturschlüssel fehlt – Lesestand kann nicht verteilt werden")
            return 0
        dokumente = [
            dok
            for name in namen
            if vertrag.fuer_portal(name) and (dok := lesestand.lade(name)) is not None
        ]
        try:
            self._sende(dokumente)
        except httpx.HTTPError:
            # Die Dokumente sind veröffentlicht, aber nicht angekommen: nachholen per Abgleich.
            self._abgleich_noetig = True
            raise
        return len(dokumente)

    def abgleichen(self) -> int:
        r = self.client.get("/core/lesestand/versionen")
        r.raise_for_status()
        im_portal: dict[str, int] = r.json()
        ordner = settings.data_dir / "lesestand"
        fehlend: list[Dokument] = []
        for pfad in sorted(ordner.glob("*.json")) if ordner.exists() else []:
            dok = Dokument.model_validate_json(pfad.read_text(encoding="utf-8"))
            if vertrag.fuer_portal(dok.dokument) and im_portal.get(dok.dokument, 0) < dok.version:
                fehlend.append(dok)
        self._sende(fehlend)
        self._abgleich_noetig = False
        self._letzter_abgleich = self.uhr()
        return len(fehlend)

    def runde(self) -> float:
        """Eine Runde der Abholschleife. Liefert die Wartezeit bis zur nächsten Runde."""
        try:
            self.abholen()
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("Portal nicht erreichbar oder Antwort unbrauchbar: %s", e)
            self._fehlschlag()
            wartezeit = self._backoff
            self._backoff = min(self._backoff * 2, MAX_BACKOFF_SEKUNDEN)
            return wartezeit
        except Exception:
            logger.exception("Unerwarteter Fehler im Kanal-Abholer")
            return 5.0
        self._backoff = 1.0
        return 0.0

    def verteiler_runde(self) -> None:
        try:
            if self._abgleich_noetig or self.uhr() - self._letzter_abgleich >= ABGLEICH_SEKUNDEN:
                self.abgleichen()
            self.verteilen()
        except (httpx.HTTPError, ValueError) as e:
            self._abgleich_noetig = True
            logger.warning("Lesestand-Verteilung fehlgeschlagen: %s", e)
        except Exception:
            self._abgleich_noetig = True
            logger.exception("Unerwarteter Fehler im Kanal-Verteiler")

    # ---------- Threads ----------

    def starte(self) -> None:
        self._stop.clear()
        for ziel, name in ((self._abholer, "kanal-abholer"), (self._verteiler, "kanal-verteiler")):
            t = threading.Thread(target=ziel, name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def stoppe(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=5)
        self._threads.clear()

    def _abholer(self) -> None:
        while not self._stop.is_set():
            wartezeit = self.runde()
            if wartezeit == 0.0 and self.warten == 0:
                wartezeit = 0.2  # ohne Long-Poll (Tests) nicht im Kreis drehen
            if wartezeit:
                self._stop.wait(wartezeit)

    def _verteiler(self) -> None:
        while not self._stop.wait(VERTEIL_TAKT_SEKUNDEN):
            self.verteiler_runde()

    # ---------- Hilfen ----------

    def _sende(self, dokumente: list[Dokument]) -> None:
        if not dokumente:
            return
        body = vertrag.DokumentListe(dokumente=dokumente).model_dump(mode="json")
        with self._sende_sperre:
            r = self.client.post("/core/lesestand", json=body)
        if r.status_code == 422:
            logger.error("Portal hat Lesestand abgelehnt: %s", r.text[:500])
            benachrichtigung.betreiber_alarm(
                "Lesestand vom Portal abgelehnt",
                "Das Portal hat mindestens ein Lesestand-Dokument wegen einer ungültigen Signatur "
                "verworfen. Prüfen Sie, ob im Portal der aktuelle öffentliche Schlüssel des "
                "Hauptsystems hinterlegt ist (beachhub-core keygen gibt ihn nicht erneut aus; "
                "er steht auf der System-Seite).\n\n" + r.text[:2000],
            )
            return
        r.raise_for_status()

    def _nachlauf(self, schritte: list[Nachlauf]) -> None:
        if not schritte:
            return
        with self.sitzung() as db:
            for schritt in schritte:
                try:
                    schritt(db)
                except Exception:
                    db.rollback()
                    logger.exception("Nachlauf nach Portal-Anfrage fehlgeschlagen")

    def _erfolg(self) -> None:
        if self._ausfall_gemeldet:
            benachrichtigung.betreiber_alarm(
                "Portal wieder erreichbar", "Das Hauptsystem erreicht das Portal wieder."
            )
        self._ausfall_gemeldet = False
        self._letzter_erfolg = self.uhr()

    def _fehlschlag(self) -> None:
        if self._ausfall_gemeldet:
            return
        if self.uhr() - self._letzter_erfolg >= AUSFALL_ALARM_SEKUNDEN:
            self._ausfall_gemeldet = True
            benachrichtigung.betreiber_alarm(
                "Portal nicht erreichbar",
                f"Das Hauptsystem erreicht das Portal seit mehr als "
                f"{AUSFALL_ALARM_SEKUNDEN // 60} Minuten nicht. Anfragen der Kunden bleiben im "
                "Portal liegen, bis die Verbindung zurück ist.",
            )
```

`core/beachhub_core/main.py`:
- Nach der bestehenden Prüfung `if settings.has_insecure_defaults: …` ergänzen:
```python
if settings.app_env == "production" and settings.produktionsfehler:
    raise RuntimeError("Start verweigert: " + "; ".join(settings.produktionsfehler))
```
- `lifespan` ersetzen durch:
```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if settings.enable_scheduler:
        from beachhub_core import jobs

        jobs.starte_scheduler()
    kanal_instanz = None
    if settings.enable_kanal and settings.portal_url:
        from beachhub_core import kanal

        kanal_instanz = kanal.Kanal(kanal.baue_client())
        kanal_instanz.starte()
    yield
    if kanal_instanz is not None:
        kanal_instanz.stoppe()
    if settings.enable_scheduler:
        from beachhub_core import jobs

        jobs.stoppe_scheduler()
```

`core/tests/conftest.py` – bei den übrigen `os.environ`-Zeilen ergänzen (eine lokale `.env` mit `PORTAL_URL` darf in Tests keinen Kanal starten):
```python
os.environ["ENABLE_KANAL"] = "false"
os.environ["PORTAL_URL"] = ""
```

`mypy.ini`, Zeile `files`:
```ini
files = shared/beachhub_shared, core/beachhub_core/services, core/beachhub_core/zahlung, core/beachhub_core/kanal.py
```

- [ ] **Step 4: Tests grün**

Run: `cd core && ../.venv/bin/pytest -q`
Expected: PASS (alle Core-Tests)

- [ ] **Step 5: Lint, Typen, Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy
git add core/beachhub_core/kanal.py core/beachhub_core/main.py core/tests/conftest.py \
  core/tests/test_kanal.py mypy.ini
git commit -m "feat(core): Kanal zum Portal mit Long-Polling, Verteilen und Abgleich"
```

---

## Task 7: core – interne CA und Client-Zertifikate (`beachhub-core zertifikate`)

**Files:**
- Create: `core/beachhub_core/zertifikate.py`
- Modify: `core/beachhub_core/cli.py`
- Modify: `mypy.ini`
- Test: `core/tests/test_zertifikate.py`

**Interfaces:**
- Produces: `zertifikate.erzeuge(ordner: Path, namen: Sequence[str] = ("portal-kanal", "halle")) -> list[Path]` – legt `ca.key`/`ca.crt` an, falls nicht vorhanden (5 Jahre), und stellt je Name `<name>.key`/`<name>.crt` neu aus (1 Jahr, EKU `clientAuth`, ECDSA P-256). Schlüssel mit Modus `0600`. Namen nur `[a-z0-9-]{1,40}`, sonst `ValueError`.
- Produces: CLI `beachhub-core zertifikate [--ziel DIR] [--name NAME ...]` (Vorgabe `data/zertifikate`, Namen `portal-kanal` und `halle`). Der Hallendienst-Plan nutzt `halle.crt/.key`.

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_zertifikate.py`:
```python
import ssl
from pathlib import Path

import pytest
from beachhub_core import zertifikate
from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def _lade(pfad: Path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(pfad.read_bytes())


def test_ca_und_client_zertifikate(tmp_path: Path) -> None:
    zertifikate.erzeuge(tmp_path)
    ca = _lade(tmp_path / "ca.crt")
    assert ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    for name in ("portal-kanal", "halle"):
        c = _lade(tmp_path / f"{name}.crt")
        c.verify_directly_issued_by(ca)
        eku = c.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        assert ExtendedKeyUsageOID.CLIENT_AUTH in eku
        assert c.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == name
        assert (tmp_path / f"{name}.key").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "ca.key").stat().st_mode & 0o777 == 0o600


def test_ca_bleibt_bei_rotation(tmp_path: Path) -> None:
    zertifikate.erzeuge(tmp_path)
    ca_vorher = (tmp_path / "ca.crt").read_bytes()
    halle_vorher = (tmp_path / "halle.crt").read_bytes()
    zertifikate.erzeuge(tmp_path, ["halle"])
    assert (tmp_path / "ca.crt").read_bytes() == ca_vorher
    assert (tmp_path / "halle.crt").read_bytes() != halle_vorher
    _lade(tmp_path / "halle.crt").verify_directly_issued_by(_lade(tmp_path / "ca.crt"))


@pytest.mark.parametrize("name", ["../x", "Portal", "", "a" * 41])
def test_ungueltiger_name(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError):
        zertifikate.erzeuge(tmp_path, [name])


def test_python_ssl_laedt_die_dateien(tmp_path: Path) -> None:
    """httpx baut seinen TLS-Kontext mit dem ssl-Modul; die Dateien müssen dort ladbar sein."""
    zertifikate.erzeuge(tmp_path)
    ctx = ssl.create_default_context(cafile=str(tmp_path / "ca.crt"))
    ctx.load_cert_chain(str(tmp_path / "portal-kanal.crt"), str(tmp_path / "portal-kanal.key"))
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && ../.venv/bin/pytest -q tests/test_zertifikate.py`
Expected: FAIL mit `ImportError: cannot import name 'zertifikate'`

- [ ] **Step 3: Implementieren**

`core/beachhub_core/zertifikate.py`:
```python
"""Interne CA und Client-Zertifikate für die mTLS-Kanäle (Portal, Halle).

Die CA bleibt bestehen, wenn sie schon existiert; Client-Zertifikate werden bei jedem Aufruf
neu ausgestellt. Laufzeit 1 Jahr, Rotation durch erneuten Aufruf (Hauptspec § 10).
"""

import os
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CA_TAGE = 5 * 365
CLIENT_TAGE = 365
_NAME = re.compile(r"[a-z0-9-]{1,40}")


def _name(cn: str) -> x509.Name:
    return x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Beachhub"),
            x509.NameAttribute(NameOID.COMMON_NAME, cn),
        ]
    )


def _schreibe_schluessel(pfad: Path, key: ec.EllipticCurvePrivateKey) -> None:
    daten = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    fd = os.open(pfad, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(daten)
    os.chmod(pfad, 0o600)  # falls die Datei schon mit anderen Rechten existierte


def _key_usage(*, ca: bool) -> x509.KeyUsage:
    return x509.KeyUsage(
        digital_signature=not ca,
        content_commitment=False,
        key_encipherment=False,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=ca,
        crl_sign=ca,
        encipher_only=False,
        decipher_only=False,
    )


def _lade_oder_erzeuge_ca(ordner: Path) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    key_pfad, crt_pfad = ordner / "ca.key", ordner / "ca.crt"
    if key_pfad.exists() and crt_pfad.exists():
        key = serialization.load_pem_private_key(key_pfad.read_bytes(), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise ValueError("ca.key ist kein EC-Schlüssel")
        return key, x509.load_pem_x509_certificate(crt_pfad.read_bytes())
    key = ec.generate_private_key(ec.SECP256R1())
    jetzt = datetime.now(UTC)
    name = _name("Beachhub interne CA")
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(jetzt - timedelta(minutes=5))
        .not_valid_after(jetzt + timedelta(days=CA_TAGE))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(_key_usage(ca=True), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    _schreibe_schluessel(key_pfad, key)
    crt_pfad.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return key, cert


def erzeuge(ordner: Path, namen: Sequence[str] = ("portal-kanal", "halle")) -> list[Path]:
    for name in namen:
        if not _NAME.fullmatch(name):
            raise ValueError(f"Ungültiger Zertifikatsname: {name!r}")
    ordner.mkdir(parents=True, exist_ok=True)
    ca_key, ca_cert = _lade_oder_erzeuge_ca(ordner)
    pfade = [ordner / "ca.crt"]
    for name in namen:
        key = ec.generate_private_key(ec.SECP256R1())
        jetzt = datetime.now(UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(_name(name))
            .issuer_name(ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(jetzt - timedelta(minutes=5))
            .not_valid_after(min(jetzt + timedelta(days=CLIENT_TAGE), ca_cert.not_valid_after_utc))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(_key_usage(ca=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        _schreibe_schluessel(ordner / f"{name}.key", key)
        (ordner / f"{name}.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        pfade += [ordner / f"{name}.crt", ordner / f"{name}.key"]
    return pfade
```

`core/beachhub_core/cli.py`:
- Nach `sub.add_parser("keygen")` ergänzen:
```python
    z = sub.add_parser("zertifikate", help="interne CA und Client-Zertifikate für mTLS")
    z.add_argument("--ziel", default="data/zertifikate")
    z.add_argument("--name", action="append", help="Zertifikatsname (mehrfach möglich)")
```
- Am Ende der `if/elif`-Kette ergänzen:
```python
    elif args.cmd == "zertifikate":
        from pathlib import Path

        from beachhub_core import zertifikate

        namen = args.name or ["portal-kanal", "halle"]
        for pfad in zertifikate.erzeuge(Path(args.ziel), namen):
            print(pfad)
```

`mypy.ini`, Zeile `files`:
```ini
files = shared/beachhub_shared, core/beachhub_core/services, core/beachhub_core/zahlung, core/beachhub_core/kanal.py, core/beachhub_core/zertifikate.py
```

- [ ] **Step 4: Tests grün**

Run: `cd core && ../.venv/bin/pytest -q tests/test_zertifikate.py`
Expected: PASS

- [ ] **Step 5: Lint, Typen, Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy
git add core/beachhub_core/zertifikate.py core/beachhub_core/cli.py mypy.ini core/tests/test_zertifikate.py
git commit -m "feat(core): interne CA und Client-Zertifikate für die mTLS-Kanäle"
```

---
## Task 8: portal – Grundgerüst (Paket, Datenbank, Modelle, Migration, Layout)

**Files:**
- Create: `portal/pyproject.toml`, `portal/alembic.ini`, `portal/alembic/env.py`, `portal/alembic/script.py.mako` (Kopie aus `core/`), `portal/alembic/versions/0001_spiegel.py`
- Create: `portal/beachhub_portal/__init__.py`, `config.py`, `database.py`, `models.py`, `uhr.py`, `templating.py`, `main.py`
- Create: `portal/beachhub_portal/services/__init__.py`, `portal/beachhub_portal/routes/__init__.py` (beide leer)
- Create: `portal/beachhub_portal/templates/base.html`, `templates/fehler.html`
- Create: `portal/beachhub_portal/static/style.css`, `static/favicon.svg` (Kopie aus `core/`)
- Create: `portal/tests/conftest.py`, `portal/tests/hilfen.py`, `portal/tests/test_grundgeruest.py`

**Interfaces:**
- Produces: `beachhub_portal.config.settings` (Präfix `PORTAL_`): `database_url`, `secret_key`, `app_env`, `cookie_secure`, `base_url`, `data_dir: Path`, `kanal_token`, `core_public_key`, `smtp_host/_port/_user/_password`, `email_from`, `betreiber_name`, `betreiber_email`, `fake_zahlung: bool`, `webhook_signatur_header: str` (kommagetrennt, Vorgabe `Stripe-Signature`), `enable_scheduler: bool`, Property `produktionsfehler -> list[str]`
- Produces: `database.engine`, `database.SessionLocal`, `database.get_db()`, `database.stelle_schema_sicher(engine)`
- Produces: Modelle im Schema `spiegel`: `Konto(id, email, anzeigename, kunde_id?, erstellt_am)`, `LoginToken(id, email, token_hash, code_hash, fehlversuche, laeuft_ab, verwendet_am?)`, `Sitzung` (Tabelle `session`: `id, konto_id → konto ON DELETE CASCADE, token_hash, csrf_token, laeuft_ab`), `Anfrage(id, typ, konto_id? ohne FK, nutzlast_json, erstellt_am, status, abgeholt_am?, antwort_json?, beantwortet_am?)` mit `Anfrage.OFFEN/ABGEHOLT/BEANTWORTET`, `Lesestand(dokument PK, version, erzeugt_am, signatur, inhalt_json, empfangen_am)`, `RechnungLink(id, konto_id → konto CASCADE, rechnung_nr, token_hash, pdf_pfad, laeuft_ab, abgerufen_am?)`, `WebhookEingang(id, provider, rohdaten, signatur_header?, empfangen_am, anfrage_id)`, `KanalKontakt(id=1, letzter_abruf)`
- Produces: `uhr.jetzt() -> datetime` (UTC; in Tests per `monkeypatch.setattr(uhr, "jetzt", …)` ersetzbar – alle Module rufen `uhr.jetzt()` über das Modul auf)
- Produces: `templating.render(request, name, konto: Konto | None = None, status_code: int = 200, **ctx) -> HTMLResponse` (Kontext: `konto`, `csrf_token` aus `request.state.csrf`, `flash`, `betreiber`), `templating.mit_flash(response, text, art="ok")`, Filter `euro`, `lokal`, `datum`, `uhrzeit`, `tag`
- Produces: `main.app` mit Security-Headern, `/static`, `/favicon.ico`, `/health`, Fehlerseite (303 → Redirect; `/core/*` → JSON; sonst `fehler.html`)
- Produces (Tests): `hilfen.PRIVAT`, `hilfen.OEFFENTLICH`, `hilfen.FELD_ID`, `hilfen.KUNDE_ID`, `hilfen.JETZT`, `hilfen.signiert(name, version, inhalt) -> dict`, `hilfen.belegung(**abweichend) -> dict`, `hilfen.tarif(name, preis, **kriterien) -> dict`, `hilfen.tarife(*regeln) -> dict`, `hilfen.buchung(beginn, ende, status="bestaetigt", pin="123456", storno=None, id=None, checkout_url=None, reserviert_bis=None) -> dict`, `hilfen.konto(buchungen=(), rechnungen=(), gruppe="Privat", guthaben="0.00") -> dict`, `hilfen.speichere(db, name, inhalt, version=1)`; Fixtures `db`, `client`, `uhr_steht` (Objekt mit `.jetzt` und `.weiter(**timedelta_kwargs)`)

- [ ] **Step 1: Datenbanken anlegen und Paket installieren**

```bash
for dbname in beachhub_portal beachhub_portal_test; do
  PGPASSWORD=beachhub psql -h localhost -U beachhub -d beachhub -tAc \
    "SELECT 1 FROM pg_database WHERE datname='$dbname'" | grep -q 1 \
  || PGPASSWORD=beachhub psql -h localhost -U beachhub -d beachhub -c "CREATE DATABASE $dbname OWNER beachhub"
done
```

`portal/pyproject.toml`:
```toml
[project]
name = "beachhub-portal"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  # beachhub-shared wird separat installiert: pip install -e ../shared
  "fastapi==0.115.6",
  "uvicorn[standard]==0.34.0",
  "sqlalchemy==2.0.36",
  "psycopg[binary]==3.2.3",
  "alembic==1.14.0",
  "jinja2==3.1.6",
  "python-multipart==0.0.32",
  "itsdangerous==2.2.0",
  "pydantic==2.10.3",
  "pydantic-settings==2.7.0",
  "apscheduler==3.11.0",
]
[project.optional-dependencies]
dev = ["pytest==8.3.4", "ruff==0.8.4", "mypy==1.13.0", "httpx==0.28.1"]
[build-system]
requires = ["setuptools==75.6.0"]
build-backend = "setuptools.build_meta"
[tool.setuptools.packages.find]
include = ["beachhub_portal*"]
[tool.setuptools.package-data]
beachhub_portal = ["templates/**/*", "static/*"]
```

```bash
mkdir -p portal/beachhub_portal/{services,routes,templates/mail,static} portal/alembic/versions portal/tests portal/deploy
touch portal/beachhub_portal/__init__.py portal/beachhub_portal/services/__init__.py portal/beachhub_portal/routes/__init__.py
cp core/alembic/script.py.mako portal/alembic/script.py.mako
cp core/beachhub_core/static/favicon.svg portal/beachhub_portal/static/favicon.svg
.venv/bin/pip install -e 'portal[dev]'
```

- [ ] **Step 2: Failing Tests schreiben**

`portal/tests/hilfen.py`:
```python
"""Testhilfen: Schlüsselpaar des „Hauptsystems“, signierte Dokumente, Beispielinhalte."""

import uuid
from datetime import UTC, datetime
from typing import Any

from beachhub_shared import signatur
from beachhub_shared.lesestand import Dokument
from sqlalchemy.orm import Session

PRIVAT, OEFFENTLICH = signatur.erzeuge_schluesselpaar()
FELD_ID = "11111111-1111-1111-1111-111111111111"
KUNDE_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
# Donnerstag, 25.11.2027, 10:00 Uhr in Berlin
JETZT = datetime(2027, 11, 25, 9, 0, tzinfo=UTC)


def signiert(name: str, version: int, inhalt: dict[str, Any]) -> dict[str, Any]:
    entwurf = Dokument(
        dokument=name, version=version, erzeugt_am=datetime.now(UTC), inhalt=inhalt, signatur=""
    )
    sig = signatur.signiere(entwurf.model_dump(mode="json", exclude={"signatur"}), PRIVAT)
    return entwurf.model_copy(update={"signatur": sig}).model_dump(mode="json")


def belegung(**abweichend: Any) -> dict[str, Any]:
    inhalt: dict[str, Any] = {
        "felder": [
            {
                "id": FELD_ID,
                "name": "Feld 1",
                "reihenfolge": 1,
                "raster": [{"wochentag": None, "modus": "dauer", "slot_minuten": 60, "fenster": []}],
            }
        ],
        "betriebszeiten": [
            {
                "wochentag": wt,
                "oeffnet": "09:00:00",
                "schliesst": "23:00:00",
                "gueltig_von": None,
                "gueltig_bis": None,
            }
            for wt in range(7)
        ],
        "ausnahmetage": [],
        "fenster_tage": 14,
        "mindestvorlauf_minuten": 60,
        "storno_frist_stunden": 24,
        "antwort_hinweis_sekunden": 120,
        "belegt": {FELD_ID: []},
    }
    inhalt.update(abweichend)
    return inhalt


def tarif(name: str, preis: str, **kriterien: Any) -> dict[str, Any]:
    regel: dict[str, Any] = {
        "name": name,
        "preis": preis,
        "feld_id": None,
        "wochentag": None,
        "uhrzeit_von": None,
        "uhrzeit_bis": None,
        "kundengruppe": None,
        "gueltig_von": None,
        "gueltig_bis": None,
    }
    regel.update(kriterien)
    return regel


def tarife(*regeln: dict[str, Any]) -> dict[str, Any]:
    return {"regeln": list(regeln) or [tarif("Std", "30.00")]}


def buchung(
    beginn: datetime,
    ende: datetime,
    status: str = "bestaetigt",
    pin: str | None = "123456",
    storno: dict[str, Any] | None = None,
    id: str | None = None,  # noqa: A002 – Feldname des Lesestands
    checkout_url: str | None = None,
    reserviert_bis: datetime | None = None,
) -> dict[str, Any]:
    return {
        "id": id or str(uuid.uuid4()),
        "feld_id": FELD_ID,
        "feld_name": "Feld 1",
        "beginn": beginn.isoformat(),
        "ende": ende.isoformat(),
        "status": status,
        "preis": "30.00",
        "pin": pin if status == "bestaetigt" else None,
        "storno": storno,
        "checkout_url": checkout_url,
        "reserviert_bis": reserviert_bis.isoformat() if reserviert_bis else None,
    }


def konto(
    buchungen: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
    rechnungen: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
    gruppe: str = "Privat",
    guthaben: str = "0.00",
) -> dict[str, Any]:
    return {
        "kunde_id": str(KUNDE_ID),
        "kundengruppe": gruppe,
        "zahlungsart": "online",
        "guthaben": guthaben,
        "buchungen": list(buchungen),
        "rechnungen": list(rechnungen),
    }


def speichere(db: Session, name: str, inhalt: dict[str, Any], version: int = 1) -> None:
    """Legt einen Lesestand direkt in der Datenbank ab (ohne Kanal, ohne Signaturprüfung)."""
    from beachhub_portal.models import Lesestand

    db.merge(
        Lesestand(
            dokument=name,
            version=version,
            erzeugt_am=datetime.now(UTC),
            signatur="00",
            inhalt_json=inhalt,
            empfangen_am=datetime.now(UTC),
        )
    )
    db.commit()
```

`portal/tests/conftest.py`:
```python
"""Tests laufen gegen eine echte PostgreSQL-Datenbank (TEST_PORTAL_DATABASE_URL).

Vor jedem Test wird das Schema `spiegel` neu aufgebaut. Lokal:
  export TEST_PORTAL_DATABASE_URL=postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub_portal_test
"""

import os
import shutil
import tempfile
from collections.abc import Iterator
from datetime import timedelta

from hilfen import JETZT, OEFFENTLICH

_tmp = tempfile.mkdtemp(prefix="beachhub-portal-test-")
os.environ["PORTAL_DATABASE_URL"] = os.environ.get(
    "TEST_PORTAL_DATABASE_URL",
    "postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub_portal_test",
)
os.environ["PORTAL_DATA_DIR"] = _tmp
os.environ["PORTAL_SECRET_KEY"] = "test-secret-key-0123456789abcdef"
os.environ["PORTAL_APP_ENV"] = "dev"
os.environ["PORTAL_COOKIE_SECURE"] = "false"
os.environ["PORTAL_SMTP_HOST"] = ""
os.environ["PORTAL_ENABLE_SCHEDULER"] = "false"
os.environ["PORTAL_KANAL_TOKEN"] = "test-kanal-token"
os.environ["PORTAL_CORE_PUBLIC_KEY"] = OEFFENTLICH
os.environ["PORTAL_FAKE_ZAHLUNG"] = "true"
os.environ["PORTAL_BETREIBER_EMAIL"] = "halle@example.org"
os.environ["PORTAL_BASE_URL"] = "http://testserver"

import pytest  # noqa: E402
from beachhub_portal import uhr  # noqa: E402
from beachhub_portal.config import settings  # noqa: E402
from beachhub_portal.database import SessionLocal, engine, stelle_schema_sicher  # noqa: E402
from beachhub_portal.main import app  # noqa: E402
from beachhub_portal.models import Base  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402


@pytest.fixture(autouse=True)
def frisches_schema() -> Iterator[None]:
    ordner = settings.data_dir / "rechnungen_tmp"
    if ordner.exists():
        shutil.rmtree(ordner)
    stelle_schema_sicher(engine)
    with engine.begin() as conn:
        # test_grundgeruest.py steuert Alembic direkt; ein abgebrochener Lauf hinterließe sonst
        # einen veralteten Versionsstand.
        conn.execute(text("DROP TABLE IF EXISTS spiegel.alembic_version"))
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class _Uhr:
    def __init__(self) -> None:
        self.jetzt = JETZT

    def weiter(self, **dauer: float) -> None:
        self.jetzt += timedelta(**dauer)


@pytest.fixture
def uhr_steht(monkeypatch: pytest.MonkeyPatch) -> _Uhr:
    u = _Uhr()
    monkeypatch.setattr(uhr, "jetzt", lambda: u.jetzt)
    return u
```

`portal/tests/test_grundgeruest.py`:
```python
import os

from alembic import command
from alembic.config import Config
from beachhub_portal.config import Settings
from beachhub_portal.database import engine
from beachhub_portal.models import Base
from fastapi.testclient import TestClient
from sqlalchemy import inspect


def test_health_mit_sicherheitskoepfen(client: TestClient) -> None:
    r = client.get("/health")
    assert r.json() == {"status": "ok"}
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"


def test_favicon(client: TestClient) -> None:
    assert client.get("/favicon.ico").status_code == 200


def test_fehlerseite_fuer_menschen(client: TestClient) -> None:
    r = client.get("/gibtsnicht")
    assert r.status_code == 404
    assert "Seite nicht gefunden" in r.text


def test_produktionsfehler() -> None:
    schlecht = Settings(
        app_env="production",
        secret_key="change-me",
        fake_zahlung=True,
        kanal_token="",
        core_public_key="",
        cookie_secure=False,
    )
    assert len(schlecht.produktionsfehler) == 5
    gut = Settings(
        app_env="production",
        secret_key="x" * 32,
        fake_zahlung=False,
        kanal_token="t",
        core_public_key="ab",
        cookie_secure=True,
    )
    assert gut.produktionsfehler == []


def test_migration_erzeugt_alle_tabellen() -> None:
    Base.metadata.drop_all(bind=engine)
    hier = os.path.dirname(__file__)
    cfg = Config(os.path.join(hier, "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(hier, "..", "alembic"))
    command.upgrade(cfg, "head")
    tabellen = set(inspect(engine).get_table_names(schema="spiegel"))
    assert {t.name for t in Base.metadata.tables.values()} <= tabellen
    command.downgrade(cfg, "base")
```

- [ ] **Step 3: Fehlschlag prüfen**

Run: `cd portal && ../.venv/bin/pytest -q`
Expected: FAIL mit `ModuleNotFoundError: No module named 'beachhub_portal.config'`

- [ ] **Step 4: Konfiguration, Datenbank, Modelle, Uhr**

`portal/beachhub_portal/config.py`:
```python
"""Einstellungen des Portals aus Umgebung/.env.

Alle Variablen tragen das Präfix PORTAL_, damit Portal und Hauptsystem im selben Prozess
(Ende-zu-Ende-Test) nicht dieselbe DATABASE_URL oder denselben SECRET_KEY lesen.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_SECRET = "change-me"  # noqa: S105 -- Marker-Wert, kein echtes Geheimnis


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PORTAL_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = "postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub_portal"
    secret_key: str = INSECURE_SECRET
    app_env: str = "dev"  # dev | production
    cookie_secure: bool = False
    base_url: str = "http://127.0.0.1:8001"
    data_dir: Path = Path("./data")
    kanal_token: str = ""
    core_public_key: str = ""  # Ed25519, hex – `beachhub-core keygen` bzw. System-Seite
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = "portal@example.org"
    betreiber_name: str = "Beachhalle"
    betreiber_email: str = ""
    fake_zahlung: bool = False
    webhook_signatur_header: str = "Stripe-Signature"  # kommagetrennt
    enable_scheduler: bool = True

    @property
    def produktionsfehler(self) -> list[str]:
        """Einstellungen, mit denen das Portal im Produktivbetrieb nicht starten darf."""
        fehler: list[str] = []
        if self.secret_key == INSECURE_SECRET:
            fehler.append("PORTAL_SECRET_KEY ist der Standardwert")
        if self.fake_zahlung:
            fehler.append("PORTAL_FAKE_ZAHLUNG ist nur für die Entwicklung")
        if not self.kanal_token:
            fehler.append("PORTAL_KANAL_TOKEN fehlt")
        if not self.core_public_key:
            fehler.append("PORTAL_CORE_PUBLIC_KEY fehlt")
        if not self.cookie_secure:
            fehler.append("PORTAL_COOKIE_SECURE muss im Produktivbetrieb true sein")
        return fehler


settings = Settings()
```

`portal/beachhub_portal/database.py`:
```python
from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from beachhub_portal.config import settings

engine: Engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def stelle_schema_sicher(target: Engine) -> None:
    with target.begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS spiegel"))


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

`portal/beachhub_portal/uhr.py`:
```python
"""Die eine Uhr des Portals. Alle Module rufen `uhr.jetzt()` über das Modul auf, damit Tests
die Zeit mit `monkeypatch.setattr(uhr, "jetzt", ...)` anhalten können."""

from datetime import UTC, datetime


def jetzt() -> datetime:
    return datetime.now(UTC)
```

`portal/beachhub_portal/models.py`:
```python
"""Datenmodell des Portals, Schema `spiegel` (Spec Portal-Kern § 7).

Das Portal hält Konten, Login-Codes, Sessions, die Anfragetabelle und den signierten Lesestand.
Postadressen, Rechnungen und Zahlungsdaten liegen ausschließlich im Hauptsystem (N-2).
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, MetaData, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "spiegel"


def _jetzt() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA)


class Konto(Base):
    __tablename__ = "konto"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    # Leer, bis der Kunde auf /willkommen seinen Namen angegeben hat.
    anzeigename: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    kunde_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    erstellt_am: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_jetzt, nullable=False
    )


class LoginToken(Base):
    """An die E-Mail gebunden, nicht an ein Konto: So lässt sich nicht erkennen, ob eine
    Adresse schon ein Konto hat."""

    __tablename__ = "login_token"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    fehlversuche: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    laeuft_ab: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verwendet_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Sitzung(Base):
    __tablename__ = "session"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    konto_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.konto.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    laeuft_ab: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Anfrage(Base):
    __tablename__ = "anfrage"
    OFFEN, ABGEHOLT, BEANTWORTET = "offen", "abgeholt", "beantwortet"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    typ: Mapped[str] = mapped_column(String(40), nullable=False)
    # Ohne Fremdschlüssel: `konto_loeschen` muss das gelöschte Konto überleben.
    konto_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    nutzlast_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    erstellt_am: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_jetzt, nullable=False
    )
    status: Mapped[str] = mapped_column(String(12), default=OFFEN, nullable=False)
    abgeholt_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    antwort_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    beantwortet_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_anfrage_status_erstellt", "status", "erstellt_am"),)


class Lesestand(Base):
    __tablename__ = "lesestand"
    dokument: Mapped[str] = mapped_column(String(80), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    erzeugt_am: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    signatur: Mapped[str] = mapped_column(String(200), nullable=False)
    inhalt_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    empfangen_am: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RechnungLink(Base):
    __tablename__ = "rechnung_link"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    konto_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.konto.id", ondelete="CASCADE"), nullable=False
    )
    rechnung_nr: Mapped[str] = mapped_column(String(20), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    pdf_pfad: Mapped[str] = mapped_column(String(300), nullable=False)
    laeuft_ab: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    abgerufen_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebhookEingang(Base):
    __tablename__ = "webhook_eingang"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    rohdaten: Mapped[str] = mapped_column(Text, nullable=False)
    signatur_header: Mapped[str | None] = mapped_column(Text)
    empfangen_am: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_jetzt, nullable=False
    )
    anfrage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)


class KanalKontakt(Base):
    """Eine Zeile: wann das Hauptsystem zuletzt Anfragen abgeholt hat."""

    __tablename__ = "kanal_kontakt"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    letzter_abruf: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

- [ ] **Step 5: Migration**

`portal/alembic.ini`:
```ini
[alembic]
script_location = alembic
prepend_sys_path = .
sqlalchemy.url = postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub_portal
[loggers]
keys = root,sqlalchemy,alembic
[handlers]
keys = console
[formatters]
keys = generic
[logger_root]
level = WARN
handlers = console
[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine
[logger_alembic]
level = INFO
handlers =
qualname = alembic
[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic
[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

`portal/alembic/env.py`:
```python
from logging.config import fileConfig

from alembic import context
from beachhub_portal.config import settings
from beachhub_portal.models import SCHEMA, Base
from sqlalchemy import engine_from_config, pool, text

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_schemas=True,
            version_table_schema=SCHEMA,
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
```

`portal/alembic/versions/0001_spiegel.py`:
```python
"""spiegel

Schema des Portals: Konten, Login-Codes, Sessions, Anfragetabelle, Lesestand,
Rechnungs-Einmal-Links, Briefkasten für Zahlungsrückmeldungen, Kontakt zum Hauptsystem.

Revision ID: 0001
Revises:
Create Date: 2026-09-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = "spiegel"


def upgrade() -> None:
    op.create_table(
        "konto",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=200), nullable=False),
        sa.Column("anzeigename", sa.String(length=100), nullable=False),
        sa.Column("kunde_id", sa.UUID(), nullable=True),
        sa.Column("erstellt_am", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        schema=S,
    )
    op.create_table(
        "login_token",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=200), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("fehlversuche", sa.Integer(), nullable=False),
        sa.Column("laeuft_ab", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verwendet_am", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
        schema=S,
    )
    op.create_index("ix_spiegel_login_token_email", "login_token", ["email"], schema=S)
    op.create_table(
        "session",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("konto_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token", sa.String(length=64), nullable=False),
        sa.Column("laeuft_ab", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["konto_id"], [f"{S}.konto.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
        schema=S,
    )
    op.create_table(
        "anfrage",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("typ", sa.String(length=40), nullable=False),
        sa.Column("konto_id", sa.UUID(), nullable=True),
        sa.Column("nutzlast_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("erstellt_am", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("abgeholt_am", sa.DateTime(timezone=True), nullable=True),
        sa.Column("antwort_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("beantwortet_am", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema=S,
    )
    op.create_index("ix_anfrage_status_erstellt", "anfrage", ["status", "erstellt_am"], schema=S)
    op.create_table(
        "lesestand",
        sa.Column("dokument", sa.String(length=80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("erzeugt_am", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signatur", sa.String(length=200), nullable=False),
        sa.Column("inhalt_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("empfangen_am", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("dokument"),
        schema=S,
    )
    op.create_table(
        "rechnung_link",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("konto_id", sa.UUID(), nullable=False),
        sa.Column("rechnung_nr", sa.String(length=20), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("pdf_pfad", sa.String(length=300), nullable=False),
        sa.Column("laeuft_ab", sa.DateTime(timezone=True), nullable=False),
        sa.Column("abgerufen_am", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["konto_id"], [f"{S}.konto.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
        schema=S,
    )
    op.create_table(
        "webhook_eingang",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("rohdaten", sa.Text(), nullable=False),
        sa.Column("signatur_header", sa.Text(), nullable=True),
        sa.Column("empfangen_am", sa.DateTime(timezone=True), nullable=False),
        sa.Column("anfrage_id", sa.UUID(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema=S,
    )
    op.create_table(
        "kanal_kontakt",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("letzter_abruf", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema=S,
    )


def downgrade() -> None:
    for tabelle in (
        "kanal_kontakt",
        "webhook_eingang",
        "rechnung_link",
        "lesestand",
        "anfrage",
        "session",
        "login_token",
        "konto",
    ):
        op.drop_table(tabelle, schema=S)
```

Der Indexname `ix_spiegel_login_token_email` ist der, den SQLAlchemy für `index=True` bei einem Schema-MetaData erzeugt (geprüft mit SQLAlchemy 2.0.36).

- [ ] **Step 6: Templating, Layout, App**

`portal/beachhub_portal/templating.py`:
```python
"""Jinja2-Templates, Filter und Flash-Meldungen des Portals."""

from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

import jinja2
from beachhub_shared.zeit import lokal
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeTimedSerializer

from beachhub_portal.config import settings
from beachhub_portal.models import Konto

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
# Nur .html automatisch escapen – die Mailvorlagen (.txt) sollen Sonderzeichen unverändert zeigen.
templates.env.autoescape = jinja2.select_autoescape(
    enabled_extensions=("html", "htm"), default_for_string=False, default=False
)
_WT = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
_flash = URLSafeTimedSerializer(settings.secret_key, salt="flash")
FLASH_COOKIE = "bp_flash"


def euro(v: Decimal | str | None) -> str:
    if v is None:
        return "–"
    d = Decimal(str(v))
    return f"{d:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"


def f_lokal(v: datetime) -> str:
    lv = lokal(v)
    return f"{_WT[lv.weekday()]} {lv:%d.%m.%Y %H:%M}"


def f_datum(v: date | datetime) -> str:
    return (lokal(v) if isinstance(v, datetime) else v).strftime("%d.%m.%Y")


def f_uhrzeit(v: time | datetime) -> str:
    return (lokal(v) if isinstance(v, datetime) else v).strftime("%H:%M")


def f_tag(v: date) -> str:
    return f"{_WT[v.weekday()]} {v:%d.%m.}"


templates.env.filters.update(
    {"euro": euro, "lokal": f_lokal, "datum": f_datum, "uhrzeit": f_uhrzeit, "tag": f_tag}
)


def render(
    request: Request,
    name: str,
    konto: Konto | None = None,
    status_code: int = 200,
    **ctx: Any,
) -> HTMLResponse:
    flash = None
    roh = request.cookies.get(FLASH_COOKIE)
    if roh:
        try:
            flash = _flash.loads(roh, max_age=60)
        except BadSignature:
            flash = None
    resp = templates.TemplateResponse(
        request,
        name,
        {
            "konto": konto,
            "csrf_token": getattr(request.state, "csrf", ""),
            "flash": flash,
            "betreiber": settings.betreiber_name,
            **ctx,
        },
        status_code=status_code,
    )
    if roh:
        resp.delete_cookie(FLASH_COOKIE, path="/")
    return resp


def mit_flash(response: Any, text: str, art: str = "ok") -> Any:
    response.set_cookie(
        FLASH_COOKIE,
        _flash.dumps({"text": text, "art": art}),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=60,
        path="/",
    )
    return response
```

`portal/beachhub_portal/templates/base.html`:
```html
<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% block title %}Buchung{% endblock %} · {{ betreiber }}</title>
<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/static/style.css">
{% block kopf %}{% endblock %}
</head>
<body>
<header class="top">
  <div class="topzeile">
    <a class="brand" href="/">{{ betreiber }}</a>
    <nav class="hauptnav" aria-label="Hauptmenü">
      {% set pfad = request.url.path %}
      <a href="/"{% if pfad == "/" %} aria-current="page"{% endif %}>Belegung</a>
      {% if konto %}
      <a href="/buchungen"{% if pfad.startswith("/buchungen") %} aria-current="page"{% endif %}>Meine Buchungen</a>
      <a href="/rechnungen"{% if pfad.startswith("/rechnungen") %} aria-current="page"{% endif %}>Rechnungen</a>
      <a href="/konto"{% if pfad.startswith("/konto") %} aria-current="page"{% endif %}>Konto</a>
      {% else %}
      <a href="/anmelden"{% if pfad.startswith("/anmelden") %} aria-current="page"{% endif %}>Anmelden</a>
      {% endif %}
    </nav>
  </div>
</header>
<main>
  {% if flash %}<div class="flash {{ flash.art }}">{{ flash.text }}</div>{% endif %}
  {% block content %}{% endblock %}
</main>
</body>
</html>
```

`portal/beachhub_portal/templates/fehler.html`:
```html
{% extends "base.html" %}{% block title %}Hinweis{% endblock %}
{% block content %}
<div class="karte">
  <h1>Das hat nicht geklappt</h1>
  <p>{{ text }}</p>
  <p><a href="/">Zur Belegung</a></p>
</div>
{% endblock %}
```

`portal/beachhub_portal/static/style.css`:
```css
:root {
  --bg:#f2f5f8; --fg:#1f2933; --gedaempft:#5a6775;
  --akzent:#0b4f6c; --akzent-hell:#e3eef4; --akzent-text:#0b4f6c;
  --rand:#d3dbe3; --rand-stark:#b6c2cd; --karte:#fff;
  --ok:#1b7f3b; --ok-bg:#e6f4ea; --fehler:#b42318; --fehler-bg:#fdecea;
  --warn:#b7791f; --warn-bg:#fdf6e7; --kopf:#0b4f6c; --kopf-fg:#fff;
  --schatten:0 1px 2px rgba(16,32,48,.06), 0 1px 8px rgba(16,32,48,.04);
  --radius:10px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg:#121417; --fg:#e6e9ee; --gedaempft:#9aa6b3;
    --akzent:#5fb3d1; --akzent-hell:#17323f; --akzent-text:#8ecbe3;
    --rand:#2e363f; --rand-stark:#414b56; --karte:#1a1e24;
    --ok:#5dd48b; --ok-bg:#11291b; --fehler:#f78b80; --fehler-bg:#2b1614;
    --warn:#e0b365; --warn-bg:#2a2113; --kopf:#0d2430; --kopf-fg:#e6e9ee;
    --schatten:0 1px 2px rgba(0,0,0,.4);
  }
}
* { box-sizing:border-box; }
body { margin:0; font:16px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif; background:var(--bg); color:var(--fg); }
:focus-visible { outline:2px solid var(--akzent); outline-offset:2px; border-radius:3px; }
a { color:var(--akzent-text); }

/* Mobil zuerst: eine Spalte, große Tippflächen. */
header.top { background:var(--kopf); color:var(--kopf-fg); box-shadow:var(--schatten); margin-bottom:1rem; }
.topzeile { display:flex; flex-wrap:wrap; align-items:center; gap:.4rem 1rem; max-width:720px; margin:0 auto; padding:.6rem 1rem; }
.brand { font-weight:700; color:var(--kopf-fg); text-decoration:none; }
.hauptnav { display:flex; flex-wrap:wrap; gap:.2rem; width:100%; }
.hauptnav a { color:var(--kopf-fg); text-decoration:none; padding:.4rem .6rem; border-radius:var(--radius); opacity:.85; }
.hauptnav a[aria-current] { opacity:1; background:rgba(255,255,255,.18); font-weight:600; }
main { max-width:720px; margin:0 auto; padding:0 1rem 3rem; }
h1 { font-size:1.35rem; margin:.25rem 0 1rem; }
h2 { font-size:1.1rem; margin:0 0 .6rem; }
p { margin:0 0 .7rem; }

.karte { background:var(--karte); border:1px solid var(--rand); border-radius:var(--radius); padding:1rem; margin-bottom:1rem; box-shadow:var(--schatten); }
.hinweis { font-size:.9rem; color:var(--gedaempft); }
label { display:block; margin-bottom:.8rem; color:var(--gedaempft); font-size:.95rem; }
input, select { width:100%; padding:.6rem .7rem; margin-top:.25rem; border:1px solid var(--rand-stark); border-radius:8px; background:var(--karte); color:var(--fg); font:inherit; }
input[type="radio"] { width:auto; margin-right:.5rem; }
button, a.knopf { display:inline-block; background:var(--akzent); color:#fff; border:0; border-radius:8px; padding:.65rem 1.1rem; font:inherit; font-weight:600; cursor:pointer; text-decoration:none; }
@media (prefers-color-scheme: dark) { button, a.knopf { color:#0b1a22; } }
button.gefahr { background:var(--fehler); }
button.leise { background:none; color:var(--akzent-text); padding:.4rem 0; font-weight:500; }
form.inline { display:inline; }

.flash { padding:.7rem .9rem; border-radius:var(--radius); margin-bottom:1rem; border:1px solid transparent; }
.flash.ok { background:var(--ok-bg); color:var(--ok); border-color:var(--ok); }
.flash.fehler { background:var(--fehler-bg); color:var(--fehler); border-color:var(--fehler); }
.flash.warn { background:var(--warn-bg); color:var(--warn); border-color:var(--warn); }
.fehler { color:var(--fehler); }

/* Tagesauswahl als waagrecht scrollbare Leiste */
.tage { display:flex; gap:.4rem; overflow-x:auto; padding-bottom:.4rem; margin-bottom:1rem; }
.tage a { flex:0 0 auto; padding:.45rem .7rem; border:1px solid var(--rand-stark); border-radius:999px; text-decoration:none; color:var(--fg); background:var(--karte); }
.tage a[aria-current] { background:var(--akzent); color:#fff; border-color:var(--akzent); }

.slots { display:grid; grid-template-columns:repeat(auto-fill, minmax(96px, 1fr)); gap:.4rem; }
.slot { display:block; padding:.55rem .4rem; border-radius:8px; text-align:center; font-size:.95rem; border:1px solid var(--rand); text-decoration:none; }
.slot.frei { background:var(--akzent-hell); color:var(--akzent-text); border-color:var(--akzent); }
.slot.belegt, .slot.vorbei { background:var(--bg); color:var(--gedaempft); }
.slot .preis { display:block; font-size:.8rem; }

.liste { list-style:none; padding:0; margin:0; }
.liste li { padding:.7rem 0; border-bottom:1px solid var(--rand); }
.liste li:last-child { border-bottom:0; }
.pin { font:700 1.6rem/1.2 ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing:.15em; }
.badge { display:inline-block; padding:.1rem .55rem; border-radius:999px; font-size:.8rem; border:1px solid var(--rand-stark); color:var(--gedaempft); }
.badge.ok { border-color:var(--ok); color:var(--ok); background:var(--ok-bg); }
.badge.warn { border-color:var(--warn); color:var(--warn); background:var(--warn-bg); }
.warten { text-align:center; padding:2rem 1rem; }
```

`portal/beachhub_portal/main.py`:
```python
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from beachhub_portal.config import INSECURE_SECRET, settings
from beachhub_portal.templating import render

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

if settings.app_env == "production" and settings.produktionsfehler:
    raise RuntimeError("Start verweigert: " + "; ".join(settings.produktionsfehler))
if settings.secret_key == INSECURE_SECRET:
    logger.warning("PORTAL_SECRET_KEY ist der Standardwert – nur für Entwicklung.")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield


app = FastAPI(
    title="Beachhub Portal", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'",
        )
        if settings.cookie_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response


app.add_middleware(SecurityHeadersMiddleware)

static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(static_dir / "favicon.svg", media_type="image/svg+xml")


@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.exception_handler(StarletteHTTPException)
async def http_fehler(request: Request, exc: StarletteHTTPException) -> Response:
    if exc.status_code == 303 and exc.headers and "Location" in exc.headers:
        return RedirectResponse(exc.headers["Location"], status_code=303)
    if request.url.path.startswith("/core/"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    # Eigene Texte (etwa „Link abgelaufen“) zeigen; Starlettes englische Standardtexte nicht.
    standard = exc.detail in (None, "", "Not Found", "Method Not Allowed")
    text = "Seite nicht gefunden." if standard else str(exc.detail)
    return render(request, "fehler.html", status_code=exc.status_code, text=text)
```

- [ ] **Step 7: Tests grün**

Run: `cd portal && ../.venv/bin/pytest -q`
Expected: PASS (5 Tests)

- [ ] **Step 8: Lint, Typen, Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy
git add portal
git commit -m "feat(portal): Grundgerüst mit Datenmodell, Migration und Layout"
```

---
## Task 9: portal – Briefkasten für das Hauptsystem (`/core/*`)

**Files:**
- Create: `portal/beachhub_portal/services/wecker.py`
- Create: `portal/beachhub_portal/services/anfragen.py`
- Create: `portal/beachhub_portal/services/lesestand.py`
- Create: `portal/beachhub_portal/routes/kanal.py`
- Modify: `portal/beachhub_portal/main.py` (Router einbinden)
- Modify: `mypy.ini`
- Test: `portal/tests/test_kanal.py`

**Interfaces:**
- Consumes: Modelle, `uhr`, `settings` (Task 8); `kanal.*`, `fuer_portal` (Task 1); `beachhub_shared.signatur.pruefe`
- Produces: `wecker.wecke() -> None`, `wecker.stand() -> int` – threadsicherer Zähler (siehe Kommentar im Modul: `asyncio.Event` wäre an einen Event-Loop gebunden, Anfragen entstehen aber in synchronen Routen im Threadpool)
- Produces: `anfragen.stelle(db, *, typ: str, konto_id: uuid.UUID | None, nutzlast: dict) -> Anfrage` – validiert die Nutzlast gegen `kanal.NUTZLAST[typ]` (`ValueError`/`ValidationError` bei Fehlern), **committet** und weckt
- Produces: `anfragen.abholen(db, jetzt) -> list[kanal.Anfrage]` – offene und seit > 60 s abgeholte, unbeantwortete Anfragen, älteste zuerst, höchstens 50; setzt `abgeholt`, schreibt `KanalKontakt`, committet
- Produces: `anfragen.beantworte(db, anfrage_id, antwort: kanal.Antwort, jetzt) -> bool` – `False` bei unbekannter oder schon beantworteter Anfrage; bei `konto_angelegt`/`ok` setzt sie `konto.kunde_id`; committet **nicht**
- Produces: `lesestand.uebernehme(db, dok: Dokument, jetzt) -> Literal["signatur","version_alt","unbekannt"] | None` (committet nicht), `lesestand.belegung(db) -> BelegungInhalt | None`, `lesestand.tarife(db) -> TarifeInhalt | None`, `lesestand.konto(db, kunde_id: uuid.UUID | None) -> KontoInhalt | None`, `lesestand.versionen(db) -> dict[str, int]`, `lesestand.loesche(db, name) -> None` (committet nicht)
- Produces: Routen `GET /core/anfragen?warten=0..30`, `POST /core/antworten`, `POST /core/lesestand` (422, wenn mindestens eine Signatur ungültig ist; gültige Dokumente werden trotzdem übernommen), `GET /core/lesestand/versionen`; alle nur mit `Authorization: Bearer <PORTAL_KANAL_TOKEN>` (401), ohne eingerichteten Token 404

- [ ] **Step 1: Failing Tests schreiben**

`portal/tests/test_kanal.py`:
```python
import threading
import time
import uuid

import pytest
from beachhub_portal.config import settings
from beachhub_portal.database import SessionLocal
from beachhub_portal.models import Anfrage, KanalKontakt, Konto, Lesestand
from beachhub_portal.services import anfragen, lesestand
from fastapi.testclient import TestClient
from hilfen import KUNDE_ID, belegung, konto, signiert, tarife
from sqlalchemy.orm import Session

KOPF = {"Authorization": "Bearer test-kanal-token"}


@pytest.fixture
def kanal_client(client: TestClient) -> TestClient:
    client.headers.update(KOPF)
    return client


def _stelle(typ: str = "konto_geaendert", konto_id: uuid.UUID | None = None, **nutzlast) -> Anfrage:
    with SessionLocal() as db:
        return anfragen.stelle(
            db, typ=typ, konto_id=konto_id, nutzlast=nutzlast or {"anzeigename": "A", "bisher": "B"}
        )


def test_ohne_oder_mit_falschem_token_401(client: TestClient) -> None:
    assert client.get("/core/anfragen?warten=0").status_code == 401
    falsch = {"Authorization": "Bearer falsch"}
    assert client.get("/core/anfragen?warten=0", headers=falsch).status_code == 401


def test_ohne_eingerichteten_kanal_404(
    kanal_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "kanal_token", "")
    assert kanal_client.get("/core/anfragen?warten=0").status_code == 404


def test_stelle_prueft_nutzlast(db: Session) -> None:
    with pytest.raises(ValueError):
        anfragen.stelle(db, typ="gibtsnicht", konto_id=None, nutzlast={})
    with pytest.raises(ValueError):
        anfragen.stelle(db, typ="buchung_anfragen", konto_id=None, nutzlast={"feld_id": "x"})


def test_abholen_aelteste_zuerst_und_nur_einmal(kanal_client: TestClient, uhr_steht) -> None:
    erste = _stelle()
    uhr_steht.weiter(seconds=1)
    zweite = _stelle()
    liste = kanal_client.get("/core/anfragen?warten=0").json()["anfragen"]
    assert [a["anfrage_id"] for a in liste] == [str(erste.id), str(zweite.id)]
    assert kanal_client.get("/core/anfragen?warten=0").json() == {"anfragen": []}


def test_unbeantwortete_nach_60_s_erneut(kanal_client: TestClient, uhr_steht) -> None:
    a = _stelle()
    kanal_client.get("/core/anfragen?warten=0")
    uhr_steht.weiter(seconds=59)
    assert kanal_client.get("/core/anfragen?warten=0").json()["anfragen"] == []
    uhr_steht.weiter(seconds=2)
    liste = kanal_client.get("/core/anfragen?warten=0").json()["anfragen"]
    assert [x["anfrage_id"] for x in liste] == [str(a.id)]


def test_long_poll_wacht_bei_neuer_anfrage_auf(kanal_client: TestClient) -> None:
    threading.Timer(0.3, _stelle).start()
    beginn = time.monotonic()
    r = kanal_client.get("/core/anfragen?warten=5")
    assert len(r.json()["anfragen"]) == 1
    assert time.monotonic() - beginn < 2


def test_long_poll_ohne_anfrage_wartet_und_liefert_leer(kanal_client: TestClient) -> None:
    beginn = time.monotonic()
    assert kanal_client.get("/core/anfragen?warten=1").json() == {"anfragen": []}
    assert 0.9 <= time.monotonic() - beginn < 3


def test_warten_ist_begrenzt(kanal_client: TestClient) -> None:
    assert kanal_client.get("/core/anfragen?warten=31").status_code == 422


def test_abholen_liefert_kunde_und_merkt_kontakt(
    kanal_client: TestClient, db: Session, uhr_steht
) -> None:
    k = Konto(email="a@x.de", anzeigename="A", kunde_id=KUNDE_ID)
    db.add(k)
    db.commit()
    _stelle(konto_id=k.id)
    a = kanal_client.get("/core/anfragen?warten=0").json()["anfragen"][0]
    assert a["kunde_id"] == str(KUNDE_ID) and a["konto_id"] == str(k.id)
    assert db.get(KanalKontakt, 1).letzter_abruf == uhr_steht.jetzt


def test_antwort_konto_angelegt_setzt_kunde(kanal_client: TestClient, db: Session) -> None:
    k = Konto(email="a@x.de", anzeigename="A")
    db.add(k)
    db.commit()
    a = _stelle("konto_angelegt", k.id, email="a@x.de", anzeigename="A")
    antworten = [
        {"anfrage_id": str(a.id), "antwort": {"status": "ok", "kunde_id": str(KUNDE_ID)}},
        {"anfrage_id": str(uuid.uuid4()), "antwort": {"status": "ok"}},
    ]
    assert kanal_client.post("/core/antworten", json={"antworten": antworten}).json() == {"ok": 1}
    db.expire_all()
    assert db.get(Konto, k.id).kunde_id == KUNDE_ID
    zeile = db.get(Anfrage, a.id)
    assert zeile.status == "beantwortet" and zeile.antwort_json["kunde_id"] == str(KUNDE_ID)


def test_zweite_antwort_wird_ignoriert(kanal_client: TestClient, db: Session) -> None:
    a = _stelle()
    ok = {"anfrage_id": str(a.id), "antwort": {"status": "ok"}}
    fehler = {"anfrage_id": str(a.id), "antwort": {"status": "fehler"}}
    kanal_client.post("/core/antworten", json={"antworten": [ok]})
    assert kanal_client.post("/core/antworten", json={"antworten": [fehler]}).json() == {"ok": 0}
    assert db.get(Anfrage, a.id).antwort_json == {"status": "ok"}


def test_lesestand_uebernehmen_und_version(kanal_client: TestClient, db: Session) -> None:
    r = kanal_client.post("/core/lesestand", json={"dokumente": [signiert("belegung", 2, belegung())]})
    assert r.status_code == 200 and r.json()["uebernommen"] == ["belegung"]
    assert lesestand.belegung(db).fenster_tage == 14
    alt = signiert("belegung", 2, belegung(fenster_tage=7))
    r = kanal_client.post("/core/lesestand", json={"dokumente": [alt]})
    assert r.json()["verworfen"] == [{"dokument": "belegung", "grund": "version_alt"}]
    assert kanal_client.get("/core/lesestand/versionen").json() == {"belegung": 2}


def test_lesestand_falsche_signatur_422(kanal_client: TestClient, db: Session) -> None:
    dok = signiert("belegung", 1, belegung())
    dok["inhalt"]["fenster_tage"] = 99
    r = kanal_client.post(
        "/core/lesestand", json={"dokumente": [dok, signiert("tarife", 1, tarife())]}
    )
    assert r.status_code == 422
    assert r.json()["verworfen"] == [{"dokument": "belegung", "grund": "signatur"}]
    assert r.json()["uebernommen"] == ["tarife"]
    assert lesestand.belegung(db) is None


def test_lesestand_nur_erlaubte_dokumente(kanal_client: TestClient, db: Session) -> None:
    r = kanal_client.post(
        "/core/lesestand", json={"dokumente": [signiert("hallenplan", 1, {"x": 1})]}
    )
    assert r.status_code == 200
    assert r.json()["verworfen"] == [{"dokument": "hallenplan", "grund": "unbekannt"}]
    assert db.get(Lesestand, "hallenplan") is None


def test_konto_dokument_lesen(kanal_client: TestClient, db: Session) -> None:
    dok = signiert(f"konto:{KUNDE_ID}", 1, konto())
    kanal_client.post("/core/lesestand", json={"dokumente": [dok]})
    assert lesestand.konto(db, KUNDE_ID).kundengruppe == "Privat"
    assert lesestand.konto(db, None) is None
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd portal && ../.venv/bin/pytest -q tests/test_kanal.py`
Expected: FAIL mit `ImportError: cannot import name 'anfragen'`

- [ ] **Step 3: Dienste implementieren**

`portal/beachhub_portal/services/wecker.py`:
```python
"""Weckt wartende Long-Polls, sobald eine neue Anfrage angelegt wurde.

Ein Zähler statt asyncio.Event: Anfragen entstehen in synchronen Routen (Threadpool), der
Long-Poll wartet im Event-Loop. Ein Event wäre an einen Loop gebunden; der Zähler ist über
Threads hinweg sicher, und der Long-Poll vergleicht ihn alle 50 ms. Anfragen aus einem anderen
Worker sieht er nicht – dafür prüft der Long-Poll zusätzlich jede Sekunde die Datenbank.
"""

import threading

_sperre = threading.Lock()
_stand = 0


def wecke() -> None:
    global _stand
    with _sperre:
        _stand += 1


def stand() -> int:
    return _stand
```

`portal/beachhub_portal/services/anfragen.py`:
```python
"""Anfragetabelle des Portals: der Briefkasten für das Hauptsystem (Spec Portal-Kern § 2).

Das Portal entscheidet nichts. Es legt Anfragen ab, liefert sie aus und merkt sich die Antwort.
"""

import uuid
from datetime import datetime, timedelta
from typing import Any

from beachhub_shared import kanal
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from beachhub_portal import uhr
from beachhub_portal.models import Anfrage, KanalKontakt, Konto
from beachhub_portal.services import wecker

ERNEUT_NACH = timedelta(seconds=60)
HOECHSTENS = 50


def stelle(
    db: Session, *, typ: str, konto_id: uuid.UUID | None, nutzlast: dict[str, Any]
) -> Anfrage:
    """Legt eine Anfrage an, committet und weckt wartende Long-Polls."""
    schema = kanal.NUTZLAST.get(typ)
    if schema is None:
        raise ValueError(f"Unbekannter Anfragetyp: {typ}")
    schema.model_validate(nutzlast)  # das Portal schickt nur Nutzlasten, die der Vertrag kennt
    a = Anfrage(
        id=uuid.uuid4(),
        typ=typ,
        konto_id=konto_id,
        nutzlast_json=nutzlast,
        erstellt_am=uhr.jetzt(),
        status=Anfrage.OFFEN,
    )
    db.add(a)
    db.commit()
    wecker.wecke()
    return a


def abholen(db: Session, jetzt: datetime) -> list[kanal.Anfrage]:
    zeilen = list(
        db.scalars(
            select(Anfrage)
            .where(
                or_(
                    Anfrage.status == Anfrage.OFFEN,
                    and_(
                        Anfrage.status == Anfrage.ABGEHOLT,
                        Anfrage.abgeholt_am < jetzt - ERNEUT_NACH,
                    ),
                )
            )
            .order_by(Anfrage.erstellt_am)
            .limit(HOECHSTENS)
            .with_for_update(skip_locked=True)
        ).all()
    )
    konto_ids = {z.konto_id for z in zeilen if z.konto_id is not None}
    kunden: dict[uuid.UUID, uuid.UUID | None] = {}
    if konto_ids:
        for konto_id, kunde_id in db.execute(
            select(Konto.id, Konto.kunde_id).where(Konto.id.in_(konto_ids))
        ).tuples():
            kunden[konto_id] = kunde_id
    liste: list[kanal.Anfrage] = []
    for z in zeilen:
        z.status = Anfrage.ABGEHOLT
        z.abgeholt_am = jetzt
        liste.append(
            kanal.Anfrage(
                anfrage_id=z.id,
                typ=z.typ,
                konto_id=z.konto_id,
                kunde_id=kunden.get(z.konto_id) if z.konto_id else None,
                nutzlast=z.nutzlast_json,
                erstellt_am=z.erstellt_am,
            )
        )
    db.merge(KanalKontakt(id=1, letzter_abruf=jetzt))
    db.commit()
    return liste


def beantworte(
    db: Session, anfrage_id: uuid.UUID, antwort: kanal.Antwort, jetzt: datetime
) -> bool:
    a = db.get(Anfrage, anfrage_id, with_for_update=True)
    if a is None or a.status == Anfrage.BEANTWORTET:
        return False
    daten = antwort.model_dump(mode="json", exclude_none=True)
    if a.typ == "konto_angelegt" and antwort.status == "ok" and antwort.kunde_id and a.konto_id:
        konto = db.get(Konto, a.konto_id)
        if konto is not None:
            konto.kunde_id = antwort.kunde_id
    a.status = Anfrage.BEANTWORTET
    a.antwort_json = daten
    a.beantwortet_am = jetzt
    return True
```

`portal/beachhub_portal/services/lesestand.py`:
```python
"""Signierter Lesestand im Portal: prüfen, speichern, typisiert lesen (Hauptspec § 8.1)."""

import uuid
from datetime import datetime
from typing import Any, Literal

from beachhub_shared import kanal, signatur
from beachhub_shared.lesestand import BelegungInhalt, Dokument, KontoInhalt, TarifeInhalt
from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_portal.config import settings
from beachhub_portal.models import Lesestand

Grund = Literal["signatur", "version_alt", "unbekannt"]


def uebernehme(db: Session, dok: Dokument, jetzt: datetime) -> Grund | None:
    """Übernimmt ein Dokument, wenn Name, Signatur und Version stimmen; sonst den Grund."""
    if not kanal.fuer_portal(dok.dokument):
        return "unbekannt"
    inhalt = dok.model_dump(mode="json", exclude={"signatur"})
    if not settings.core_public_key or not signatur.pruefe(
        inhalt, dok.signatur, settings.core_public_key
    ):
        return "signatur"
    zeile = db.get(Lesestand, dok.dokument, with_for_update=True)
    if zeile is not None and zeile.version >= dok.version:
        return "version_alt"
    if zeile is None:
        zeile = Lesestand(dokument=dok.dokument)
        db.add(zeile)
    zeile.version = dok.version
    zeile.erzeugt_am = dok.erzeugt_am
    zeile.signatur = dok.signatur
    zeile.inhalt_json = dok.inhalt
    zeile.empfangen_am = jetzt
    return None


def _inhalt(db: Session, name: str) -> dict[str, Any] | None:
    zeile = db.get(Lesestand, name)
    return zeile.inhalt_json if zeile is not None else None


def belegung(db: Session) -> BelegungInhalt | None:
    d = _inhalt(db, "belegung")
    return BelegungInhalt.model_validate(d) if d is not None else None


def tarife(db: Session) -> TarifeInhalt | None:
    d = _inhalt(db, "tarife")
    return TarifeInhalt.model_validate(d) if d is not None else None


def konto(db: Session, kunde_id: uuid.UUID | None) -> KontoInhalt | None:
    if kunde_id is None:
        return None
    d = _inhalt(db, f"konto:{kunde_id}")
    return KontoInhalt.model_validate(d) if d is not None else None


def versionen(db: Session) -> dict[str, int]:
    return {z.dokument: z.version for z in db.scalars(select(Lesestand))}


def loesche(db: Session, name: str) -> None:
    zeile = db.get(Lesestand, name)
    if zeile is not None:
        db.delete(zeile)
```

- [ ] **Step 4: Routen**

`portal/beachhub_portal/routes/kanal.py`:
```python
"""Briefkasten für das Hauptsystem. Nur das Hauptsystem ruft diese Pfade auf – über mTLS
(Caddy, eigener Port) und zusätzlich mit dem Kanal-Token."""

import asyncio
import hmac
import logging
import time
from typing import Any

from beachhub_shared import kanal
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from beachhub_portal import uhr
from beachhub_portal.config import settings
from beachhub_portal.database import SessionLocal, get_db
from beachhub_portal.services import anfragen, lesestand, wecker

logger = logging.getLogger(__name__)


def pruefe_token(request: Request) -> None:
    if not settings.kanal_token:
        raise HTTPException(status_code=404, detail="Kanal nicht eingerichtet")
    erwartet = f"Bearer {settings.kanal_token}".encode()
    if not hmac.compare_digest(request.headers.get("authorization", "").encode(), erwartet):
        raise HTTPException(status_code=401, detail="Kanal-Token ungültig")


router = APIRouter(dependencies=[Depends(pruefe_token)])


def _abholen() -> list[kanal.Anfrage]:
    with SessionLocal() as db:
        return anfragen.abholen(db, uhr.jetzt())


@router.get("/anfragen")
async def anfragen_abholen(warten: int = Query(25, ge=0, le=30)) -> dict[str, Any]:
    """Long-Poll: sofort antworten, wenn Anfragen da sind; sonst bis `warten` Sekunden warten.
    Die Datenbank wird im Threadpool abgefragt, damit der Event-Loop frei bleibt."""
    ende = time.monotonic() + warten
    while True:
        stand = wecker.stand()
        liste = await run_in_threadpool(_abholen)
        if liste or time.monotonic() >= ende:
            return kanal.AnfrageListe(anfragen=liste).model_dump(mode="json")
        naechste_pruefung = min(time.monotonic() + 1.0, ende)
        while time.monotonic() < naechste_pruefung and wecker.stand() == stand:
            await asyncio.sleep(0.05)


@router.post("/antworten")
def antworten(liste: kanal.AntwortListe, db: Session = Depends(get_db)) -> dict[str, int]:
    jetzt = uhr.jetzt()
    n = sum(anfragen.beantworte(db, e.anfrage_id, e.antwort, jetzt) for e in liste.antworten)
    db.commit()
    return {"ok": n}


@router.post("/lesestand")
def lesestand_empfangen(liste: kanal.DokumentListe, db: Session = Depends(get_db)) -> JSONResponse:
    jetzt = uhr.jetzt()
    ergebnis = kanal.LesestandErgebnis(uebernommen=[], verworfen=[])
    for dok in liste.dokumente:
        grund = lesestand.uebernehme(db, dok, jetzt)
        if grund is None:
            ergebnis.uebernommen.append(dok.dokument)
        else:
            ergebnis.verworfen.append(kanal.Verworfen(dokument=dok.dokument, grund=grund))
    db.commit()
    signaturfehler = [v.dokument for v in ergebnis.verworfen if v.grund == "signatur"]
    if signaturfehler:
        logger.error("Lesestand mit ungültiger Signatur verworfen: %s", signaturfehler)
    return JSONResponse(ergebnis.model_dump(mode="json"), status_code=422 if signaturfehler else 200)


@router.get("/lesestand/versionen")
def lesestand_versionen(db: Session = Depends(get_db)) -> dict[str, int]:
    return lesestand.versionen(db)
```

`portal/beachhub_portal/main.py`:
- Bei den `beachhub_portal`-Importen ergänzen: `from beachhub_portal.routes import kanal`
- Nach der `health`-Route (vor dem Exception-Handler) einfügen:
```python
app.include_router(kanal.router, prefix="/core")
```
Alle weiteren Router werden in den folgenden Tasks an derselben Stelle ergänzt.

`mypy.ini`, Zeile `files`:
```ini
files = shared/beachhub_shared, core/beachhub_core/services, core/beachhub_core/zahlung, core/beachhub_core/kanal.py, core/beachhub_core/zertifikate.py, portal/beachhub_portal/services
```

- [ ] **Step 5: Tests grün**

Run: `cd portal && ../.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 6: Lint, Typen, Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy
git add portal mypy.ini
git commit -m "feat(portal): Briefkasten für das Hauptsystem mit Long-Poll und Lesestand-Prüfung"
```

---

## Task 10: portal – Anmeldung, Session, CSRF, Konto

**Files:**
- Create: `portal/beachhub_portal/auth.py`, `portal/beachhub_portal/mail.py`
- Create: `portal/beachhub_portal/services/konten.py`
- Create: `portal/beachhub_portal/routes/oeffentlich.py`, `portal/beachhub_portal/routes/konto.py`
- Create: `portal/beachhub_portal/templates/anmelden.html`, `anmelden_link.html`, `willkommen.html`, `konto.html`, `konto_loeschen.html`, `templates/mail/login.txt`
- Modify: `portal/beachhub_portal/main.py`, `portal/tests/conftest.py`, `portal/tests/hilfen.py`
- Test: `portal/tests/test_login.py`

**Interfaces:**
- Consumes: `anfragen.stelle`, `lesestand.loesche` (Task 9)
- Produces: `auth.COOKIE = "bp_session"`, `auth.normalisiere_email(str) -> str`, `auth.fordere_an(db, email, jetzt) -> tuple[token, code]`, `auth.pruefe_code(db, email, code, jetzt) -> bool`, `auth.email_zum_link(db, token, jetzt) -> str | None` (verbraucht nicht), `auth.loese_link_ein(db, token, jetzt) -> str | None` (verbraucht), `auth.melde_an(db, email, jetzt) -> tuple[Konto, session_token]` (legt Konto mit leerem Namen an, falls nötig), `auth.lade_sitzung(db, token, jetzt) -> Sitzung | None` (gleitend 30 Tage), `auth.beende(db, token)`, `auth.setze_cookie/loesche_cookie(response[, token])`
- Produces: Abhängigkeiten `auth.konto_optional -> Konto | None`, `auth.konto_pflicht -> Konto` (303 nach `/anmelden`; ohne Namen 303 nach `/willkommen`), `auth.verify_csrf` (Router-Abhängigkeit; prüft `csrf_token` im Formular, sobald eine Session besteht), `auth.pruefe_rate_limit(schluessel, maximum)` (15 min, sonst 429), `auth.client_ip(request)`, `auth.reset_rate_limits()`
- Produces: `mail.sende(an, betreff, text)` mit `mail.TEST_AUSGANG`
- Produces: `konten.loesche(db, konto)` – löscht Rechnungslink-Dateien, `konto:<kunde_id>`-Lesestand, Login-Tokens der Adresse, Konto (Sessions und Links per CASCADE), committet
- Produces: Routen `GET/POST /anmelden`, `POST /anmelden/code`, `GET/POST /anmelden/link/{token}`, `POST /abmelden`, `GET/POST /willkommen`, `GET /konto`, `POST /konto/name`, `GET/POST /konto/loeschen`
- Produces (Tests): Fixture `angemeldet` (Konto „Anna“, `anna@example.org`, `kunde_id = KUNDE_ID`, Uhr steht auf `JETZT`; Attribute `angemeldet.csrf`, `angemeldet.konto_id`), autouse-Fixtures `mail_ausgang`, `_rate_limits_leeren`; `hilfen.csrf(html) -> str`

- [ ] **Step 1: Testhilfen ergänzen**

`portal/tests/hilfen.py` – `import re` zu den Importen und am Ende ergänzen:
```python
def csrf(html: str) -> str:
    treffer = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert treffer, "kein CSRF-Token in der Seite"
    return treffer.group(1)
```

`portal/tests/conftest.py` – die Zeile `from hilfen import JETZT, OEFFENTLICH` wird zu `from hilfen import JETZT, KUNDE_ID, OEFFENTLICH`; der Importblock nach den `os.environ`-Zeilen wird zu:
```python
import pytest  # noqa: E402
from beachhub_portal import auth, mail, uhr  # noqa: E402
from beachhub_portal.config import settings  # noqa: E402
from beachhub_portal.database import SessionLocal, engine, stelle_schema_sicher  # noqa: E402
from beachhub_portal.main import app  # noqa: E402
from beachhub_portal.models import Base, Sitzung  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
```
Am Ende anfügen:
```python
@pytest.fixture(autouse=True)
def _rate_limits_leeren() -> None:
    auth.reset_rate_limits()


@pytest.fixture(autouse=True)
def mail_ausgang(monkeypatch: pytest.MonkeyPatch) -> Iterator[list]:
    ausgang: list = []
    monkeypatch.setattr(mail, "TEST_AUSGANG", ausgang)
    yield ausgang


@pytest.fixture
def angemeldet(uhr_steht: _Uhr, client: TestClient, db: Session) -> TestClient:
    """Browser mit Session des Kontos „Anna“ (Kunde KUNDE_ID); die Uhr steht auf JETZT.
    `angemeldet.csrf` enthält das CSRF-Token, `angemeldet.konto_id` die Konto-ID."""
    konto, token = auth.melde_an(db, "anna@example.org", uhr.jetzt())
    konto.anzeigename = "Anna"
    konto.kunde_id = KUNDE_ID
    db.commit()
    client.cookies.set(auth.COOKIE, token)
    client.csrf = db.scalar(select(Sitzung.csrf_token).where(Sitzung.konto_id == konto.id))  # type: ignore[attr-defined]
    client.konto_id = konto.id  # type: ignore[attr-defined]
    return client
```

- [ ] **Step 2: Failing Tests schreiben**

`portal/tests/test_login.py`:
```python
import re

from beachhub_portal import auth
from beachhub_portal.models import Anfrage, Konto, Lesestand, Sitzung
from fastapi.testclient import TestClient
from hilfen import KUNDE_ID, csrf, konto, speichere
from sqlalchemy import select
from sqlalchemy.orm import Session


def _code(ausgang: list) -> str:
    return re.search(r"Anmeldeseite ein: (\d{6})", ausgang[-1]["text"]).group(1)


def _link(ausgang: list) -> str:
    return re.search(r"http://testserver(/anmelden/link/\S+)", ausgang[-1]["text"]).group(1)


def _einloggen(client: TestClient, ausgang: list, email: str = "anna@example.org") -> None:
    client.post("/anmelden", data={"email": email})
    client.post("/anmelden/code", data={"email": email, "code": _code(ausgang)})


def test_anfordern_gleiche_antwort_und_mail(client: TestClient, mail_ausgang) -> None:
    r = client.post("/anmelden", data={"email": "neu@example.org"})
    assert r.status_code == 200 and "Wenn die Adresse stimmt" in r.text
    assert mail_ausgang[-1]["an"] == "neu@example.org"
    assert re.search(r"Anmeldeseite ein: \d{6}", mail_ausgang[-1]["text"])
    assert "http://testserver/anmelden/link/" in mail_ausgang[-1]["text"]


def test_ungueltige_adresse(client: TestClient, mail_ausgang) -> None:
    r = client.post("/anmelden", data={"email": "kein-at"})
    assert r.status_code == 400 and mail_ausgang == []


def test_code_meldet_an_und_legt_konto_an(client: TestClient, db: Session, mail_ausgang) -> None:
    client.post("/anmelden", data={"email": "  Anna@Example.org "})
    r = client.post(
        "/anmelden/code",
        data={"email": "anna@example.org", "code": _code(mail_ausgang)},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == "/willkommen"
    k = db.scalar(select(Konto))
    assert k.email == "anna@example.org" and k.anzeigename == ""
    assert client.get("/willkommen").status_code == 200


def test_bekanntes_konto_geht_direkt_zur_belegung(
    client: TestClient, db: Session, mail_ausgang
) -> None:
    db.add(Konto(email="anna@example.org", anzeigename="Anna"))
    db.commit()
    client.post("/anmelden", data={"email": "anna@example.org"})
    r = client.post(
        "/anmelden/code",
        data={"email": "anna@example.org", "code": _code(mail_ausgang)},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/"


def test_fuenf_fehlversuche_verbrauchen_den_code(client: TestClient, mail_ausgang) -> None:
    client.post("/anmelden", data={"email": "a@example.org"})
    richtig = _code(mail_ausgang)
    falsch = "000000" if richtig != "000000" else "111111"
    for _ in range(5):
        r = client.post("/anmelden/code", data={"email": "a@example.org", "code": falsch})
        assert r.status_code == 401
    r = client.post("/anmelden/code", data={"email": "a@example.org", "code": richtig})
    assert r.status_code == 401


def test_code_laeuft_nach_15_minuten_ab(client: TestClient, mail_ausgang, uhr_steht) -> None:
    client.post("/anmelden", data={"email": "a@example.org"})
    uhr_steht.weiter(minutes=16)
    r = client.post("/anmelden/code", data={"email": "a@example.org", "code": _code(mail_ausgang)})
    assert r.status_code == 401


def test_neue_anforderung_macht_alten_code_ungueltig(client: TestClient, mail_ausgang) -> None:
    client.post("/anmelden", data={"email": "a@example.org"})
    alt = _code(mail_ausgang)
    client.post("/anmelden", data={"email": "a@example.org"})
    neu = _code(mail_ausgang)
    if alt != neu:
        r = client.post("/anmelden/code", data={"email": "a@example.org", "code": alt})
        assert r.status_code == 401
    r = client.post(
        "/anmelden/code", data={"email": "a@example.org", "code": neu}, follow_redirects=False
    )
    assert r.status_code == 303


def test_link_get_verbraucht_nicht(client: TestClient, mail_ausgang) -> None:
    client.post("/anmelden", data={"email": "a@example.org"})
    pfad = _link(mail_ausgang)
    assert "Jetzt anmelden" in client.get(pfad).text
    assert "Jetzt anmelden" in client.get(pfad).text  # Mail-Scanner hat schon einmal geöffnet
    r = client.post(pfad, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/willkommen"
    client.cookies.clear()
    assert client.post(pfad, follow_redirects=False).status_code == 400


def test_rate_limit_je_adresse_und_ip(client: TestClient) -> None:
    for _ in range(3):
        assert client.post("/anmelden", data={"email": "a@example.org"}).status_code == 200
    assert client.post("/anmelden", data={"email": "a@example.org"}).status_code == 429
    auth.reset_rate_limits()
    for i in range(5):
        client.post("/anmelden", data={"email": f"x{i}@example.org"})
    assert client.post("/anmelden", data={"email": "y@example.org"}).status_code == 429


def test_entwicklung_zeigt_code_ohne_mailserver(client: TestClient, mail_ausgang) -> None:
    r = client.post("/anmelden", data={"email": "a@example.org"})
    assert _code(mail_ausgang) in r.text


def test_willkommen_legt_konto_angelegt_an(
    client: TestClient, db: Session, mail_ausgang
) -> None:
    _einloggen(client, mail_ausgang)
    token = csrf(client.get("/willkommen").text)
    r = client.post(
        "/willkommen",
        data={"anzeigename": " Anna ", "csrf_token": token},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == "/"
    a = db.scalar(select(Anfrage))
    assert a.typ == "konto_angelegt"
    assert a.nutzlast_json == {"email": "anna@example.org", "anzeigename": "Anna"}
    assert client.get("/willkommen", follow_redirects=False).headers["location"] == "/"


def test_geschuetzte_seiten(client: TestClient, mail_ausgang) -> None:
    assert client.get("/konto", follow_redirects=False).headers["location"] == "/anmelden"
    _einloggen(client, mail_ausgang)
    assert client.get("/konto", follow_redirects=False).headers["location"] == "/willkommen"


def test_csrf_pflicht(angemeldet: TestClient) -> None:
    assert angemeldet.post("/konto/name", data={"anzeigename": "X"}).status_code == 403
    r = angemeldet.post(
        "/konto/name",
        data={"anzeigename": "X", "csrf_token": angemeldet.csrf},
        follow_redirects=False,
    )
    assert r.status_code == 303


def test_name_aendern_legt_anfrage_an(angemeldet: TestClient, db: Session) -> None:
    angemeldet.post("/konto/name", data={"anzeigename": "Anni", "csrf_token": angemeldet.csrf})
    a = db.scalar(select(Anfrage))
    assert a.typ == "konto_geaendert"
    assert a.nutzlast_json == {"anzeigename": "Anni", "bisher": "Anna"}


def test_konto_seite_zeigt_adresse_und_abo_hinweis(angemeldet: TestClient) -> None:
    seite = angemeldet.get("/konto").text
    assert "anna@example.org" in seite and "halle@example.org" in seite


def test_session_gleitet_und_abmelden(angemeldet: TestClient, db: Session, uhr_steht) -> None:
    ablauf = db.scalar(select(Sitzung)).laeuft_ab
    uhr_steht.weiter(days=2)
    assert angemeldet.get("/konto").status_code == 200
    db.expire_all()
    assert db.scalar(select(Sitzung)).laeuft_ab > ablauf
    angemeldet.post("/abmelden", data={"csrf_token": angemeldet.csrf})
    assert db.scalar(select(Sitzung)) is None


def test_konto_loeschen(angemeldet: TestClient, db: Session) -> None:
    speichere(db, f"konto:{KUNDE_ID}", konto())
    assert "endgültig" in angemeldet.get("/konto/loeschen").text
    r = angemeldet.post(
        "/konto/loeschen", data={"csrf_token": angemeldet.csrf}, follow_redirects=False
    )
    assert r.status_code == 303
    db.expire_all()
    assert db.scalar(select(Konto)) is None and db.scalar(select(Sitzung)) is None
    assert db.get(Lesestand, f"konto:{KUNDE_ID}") is None
    a = db.scalar(select(Anfrage))
    assert a.typ == "konto_loeschen" and a.konto_id == angemeldet.konto_id
    assert angemeldet.get("/konto", follow_redirects=False).headers["location"] == "/anmelden"
```

- [ ] **Step 3: Fehlschlag prüfen**

Run: `cd portal && ../.venv/bin/pytest -q tests/test_login.py`
Expected: FAIL mit `ImportError: cannot import name 'auth'`

- [ ] **Step 4: Anmeldung und Mail implementieren**

`portal/beachhub_portal/auth.py`:
```python
"""Anmeldung per Link oder Code aus der Mail (Muster SportAbo-Manager), serverseitige
Sessions, CSRF-Token und Rate-Limits (Hauptspec § 10)."""

import hashlib
import hmac
import secrets
import time as _time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from beachhub_portal import uhr
from beachhub_portal.config import settings
from beachhub_portal.database import get_db
from beachhub_portal.models import Konto, LoginToken, Sitzung

COOKIE = "bp_session"
SESSION_DAUER = timedelta(days=30)
TOKEN_DAUER = timedelta(minutes=15)
MAX_CODE_FEHLVERSUCHE = 5
RATE_FENSTER_SEKUNDEN = 15 * 60
_versuche: dict[str, list[float]] = defaultdict(list)


def normalisiere_email(email: str) -> str:
    return email.strip().lower()


def _hash(wert: str) -> str:
    return hashlib.sha256(wert.encode()).hexdigest()


def fordere_an(db: Session, email: str, jetzt: datetime) -> tuple[str, str]:
    """Neuer Link und Code für eine Adresse; ältere Links und Codes werden ungültig."""
    db.execute(delete(LoginToken).where(LoginToken.email == email))
    token = secrets.token_urlsafe(32)
    code = f"{secrets.randbelow(10**6):06d}"
    db.add(
        LoginToken(
            email=email,
            token_hash=_hash(token),
            code_hash=_hash(code),
            laeuft_ab=jetzt + TOKEN_DAUER,
        )
    )
    db.commit()
    return token, code


def pruefe_code(db: Session, email: str, code: str, jetzt: datetime) -> bool:
    offen = db.scalars(
        select(LoginToken).where(
            LoginToken.email == email,
            LoginToken.verwendet_am.is_(None),
            LoginToken.laeuft_ab > jetzt,
        )
    ).all()
    gesucht = _hash(code.strip())
    treffer = next((t for t in offen if hmac.compare_digest(t.code_hash, gesucht)), None)
    if treffer is None:
        for t in offen:
            t.fehlversuche += 1
            if t.fehlversuche >= MAX_CODE_FEHLVERSUCHE:
                t.verwendet_am = jetzt  # verbraucht: auch der Link gilt nicht mehr
        db.commit()
        return False
    treffer.verwendet_am = jetzt
    db.commit()
    return True


def email_zum_link(db: Session, token: str, jetzt: datetime) -> str | None:
    t = db.scalar(select(LoginToken).where(LoginToken.token_hash == _hash(token)))
    if t is None or t.verwendet_am is not None or t.laeuft_ab <= jetzt:
        return None
    return t.email


def loese_link_ein(db: Session, token: str, jetzt: datetime) -> str | None:
    t = db.scalar(
        select(LoginToken).where(LoginToken.token_hash == _hash(token)).with_for_update()
    )
    if t is None or t.verwendet_am is not None or t.laeuft_ab <= jetzt:
        db.rollback()
        return None
    t.verwendet_am = jetzt
    db.commit()
    return t.email


def melde_an(db: Session, email: str, jetzt: datetime) -> tuple[Konto, str]:
    konto = db.scalar(select(Konto).where(Konto.email == email))
    if konto is None:
        konto = Konto(id=uuid.uuid4(), email=email, anzeigename="", erstellt_am=jetzt)
        db.add(konto)
        db.flush()
    token = secrets.token_urlsafe(32)
    db.add(
        Sitzung(
            konto_id=konto.id,
            token_hash=_hash(token),
            csrf_token=secrets.token_urlsafe(32),
            laeuft_ab=jetzt + SESSION_DAUER,
        )
    )
    db.commit()
    return konto, token


def lade_sitzung(db: Session, token: str | None, jetzt: datetime) -> Sitzung | None:
    if not token:
        return None
    s = db.scalar(select(Sitzung).where(Sitzung.token_hash == _hash(token)))
    if s is None or s.laeuft_ab <= jetzt:
        return None
    if s.laeuft_ab - jetzt < SESSION_DAUER - timedelta(days=1):
        s.laeuft_ab = jetzt + SESSION_DAUER  # gleitend, höchstens einmal am Tag geschrieben
        db.commit()
    return s


def beende(db: Session, token: str | None) -> None:
    if token:
        db.execute(delete(Sitzung).where(Sitzung.token_hash == _hash(token)))
        db.commit()


def setze_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=int(SESSION_DAUER.total_seconds()),
        path="/",
    )


def loesche_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")


def aktuelles_konto(request: Request, db: Session) -> Konto | None:
    s = lade_sitzung(db, request.cookies.get(COOKIE), uhr.jetzt())
    if s is None:
        return None
    request.state.csrf = s.csrf_token
    return db.get(Konto, s.konto_id)


def konto_optional(request: Request, db: Session = Depends(get_db)) -> Konto | None:
    return aktuelles_konto(request, db)


def konto_pflicht(request: Request, db: Session = Depends(get_db)) -> Konto:
    konto = aktuelles_konto(request, db)
    if konto is None:
        raise HTTPException(status_code=303, headers={"Location": "/anmelden"})
    if not konto.anzeigename and request.url.path != "/willkommen":
        raise HTTPException(status_code=303, headers={"Location": "/willkommen"})
    return konto


async def verify_csrf(request: Request, db: Session = Depends(get_db)) -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    s = lade_sitzung(db, request.cookies.get(COOKIE), uhr.jetzt())
    if s is None:
        return  # Anmeldeformulare ohne Session; dort greift das Rate-Limit
    form = await request.form()
    if not secrets.compare_digest(str(form.get("csrf_token", "")), s.csrf_token):
        raise HTTPException(
            status_code=403,
            detail="Die Seite ist veraltet. Bitte lade sie neu und versuche es noch einmal.",
        )


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def pruefe_rate_limit(schluessel: str, maximum: int) -> None:
    jetzt = _time.monotonic()
    _versuche[schluessel] = [t for t in _versuche[schluessel] if jetzt - t < RATE_FENSTER_SEKUNDEN]
    if len(_versuche[schluessel]) >= maximum:
        raise HTTPException(
            status_code=429, detail="Zu viele Versuche. Bitte in einigen Minuten erneut versuchen."
        )
    _versuche[schluessel].append(jetzt)


def reset_rate_limits() -> None:
    _versuche.clear()
```

`portal/beachhub_portal/mail.py`:
```python
"""Mailversand des Portals: nur Login-Codes (A-MAIL-1; Gruppen-Mails folgen mit Stufe 4)."""

import logging
import smtplib
from email.message import EmailMessage
from typing import Any

from beachhub_portal.config import settings

logger = logging.getLogger(__name__)
TEST_AUSGANG: list[dict[str, Any]] | None = None


def sende(an: str, betreff: str, text: str) -> None:
    if TEST_AUSGANG is not None:
        TEST_AUSGANG.append({"an": an, "betreff": betreff, "text": text})
        return
    if not settings.smtp_host:
        logger.warning("kein SMTP konfiguriert – Mail an %s (%s) nicht gesendet", an, betreff)
        return
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = settings.email_from, an, betreff
    m.set_content(text)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
            smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(m)
    except (smtplib.SMTPException, OSError):
        logger.exception("Mailversand an %s fehlgeschlagen", an)
```

`portal/beachhub_portal/services/konten.py`:
```python
"""Konto löschen: alles, was das Portal zu einem Konto hält (Hauptspec § 10, Löschkonzept)."""

from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from beachhub_portal.models import Konto, LoginToken, RechnungLink
from beachhub_portal.services import lesestand


def loesche(db: Session, konto: Konto) -> None:
    for link in db.scalars(select(RechnungLink).where(RechnungLink.konto_id == konto.id)):
        Path(link.pdf_pfad).unlink(missing_ok=True)
    if konto.kunde_id is not None:
        lesestand.loesche(db, f"konto:{konto.kunde_id}")
    db.execute(delete(LoginToken).where(LoginToken.email == konto.email))
    db.delete(konto)  # Sitzungen und Rechnungslinks per ON DELETE CASCADE
    db.commit()
```

- [ ] **Step 5: Routen und Templates**

`portal/beachhub_portal/routes/oeffentlich.py`:
```python
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from beachhub_portal import auth, mail, uhr
from beachhub_portal.config import settings
from beachhub_portal.database import get_db
from beachhub_portal.models import Konto
from beachhub_portal.templating import mit_flash, render, templates

router = APIRouter()

NEUTRAL = (
    "Wenn die Adresse stimmt, ist eine Mail mit Anmeldelink und Code unterwegs. "
    "Bitte schau in dein Postfach."
)
FALSCHER_CODE = (
    "Der Code ist ungültig oder abgelaufen. "
    "Nach mehreren Fehlversuchen bitte einen neuen anfordern."
)
UNGUELTIGER_LINK = (
    "Der Anmeldelink ist ungültig, abgelaufen oder wurde schon benutzt. "
    "Bitte fordere einen neuen an."
)


@router.get("/anmelden", response_class=HTMLResponse)
def anmelden_seite(
    request: Request, konto: Konto | None = Depends(auth.konto_optional)
) -> Response:
    if konto is not None:
        return RedirectResponse("/", status_code=303)
    return render(request, "anmelden.html")


@router.post("/anmelden", response_class=HTMLResponse)
def anmelden(
    request: Request,
    email: str = Form(..., max_length=200),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    auth.pruefe_rate_limit(f"anfordern-ip:{auth.client_ip(request)}", 5)
    adresse = auth.normalisiere_email(email)
    if "@" not in adresse or len(adresse) < 3:
        return render(
            request,
            "anmelden.html",
            status_code=400,
            fehler="Bitte gib eine gültige E-Mail-Adresse an.",
        )
    auth.pruefe_rate_limit(f"anfordern-mail:{adresse}", 3)
    token, code = auth.fordere_an(db, adresse, uhr.jetzt())
    link = f"{settings.base_url.rstrip('/')}/anmelden/link/{token}"
    zeigen = not settings.smtp_host and settings.app_env != "production"
    resp = render(
        request,
        "anmelden.html",
        meldung=NEUTRAL,
        code_email=adresse,
        dev_link=link if zeigen else None,
        dev_code=code if zeigen else None,
    )
    text = templates.env.get_template("mail/login.txt").render(
        link=link, code=code, betreiber=settings.betreiber_name
    )
    # Erst nach der Antwort senden: gleiche Antwortzeit für bekannte und unbekannte Adressen.
    resp.background = BackgroundTask(
        mail.sende, adresse, f"Dein Anmeldelink – {settings.betreiber_name}", text
    )
    return resp


def _angemeldet(db: Session, adresse: str) -> RedirectResponse:
    konto, token = auth.melde_an(db, adresse, uhr.jetzt())
    resp = RedirectResponse("/" if konto.anzeigename else "/willkommen", status_code=303)
    auth.setze_cookie(resp, token)
    return resp


@router.post("/anmelden/code", response_model=None)
def anmelden_mit_code(
    request: Request,
    email: str = Form(..., max_length=200),
    code: str = Form(..., max_length=12),
    db: Session = Depends(get_db),
) -> Response:
    auth.pruefe_rate_limit(f"code-ip:{auth.client_ip(request)}", 10)
    adresse = auth.normalisiere_email(email)
    if not auth.pruefe_code(db, adresse, code, uhr.jetzt()):
        return render(
            request, "anmelden.html", status_code=401, fehler=FALSCHER_CODE, code_email=adresse
        )
    return _angemeldet(db, adresse)


@router.get("/anmelden/link/{token}", response_class=HTMLResponse)
def link_seite(request: Request, token: str, db: Session = Depends(get_db)) -> HTMLResponse:
    """Nur ein Knopf: Mail-Scanner öffnen Links per GET und würden ihn sonst verbrauchen."""
    if auth.email_zum_link(db, token, uhr.jetzt()) is None:
        return render(request, "anmelden.html", status_code=400, fehler=UNGUELTIGER_LINK)
    return render(request, "anmelden_link.html", token=token)


@router.post("/anmelden/link/{token}", response_model=None)
def link_einloesen(request: Request, token: str, db: Session = Depends(get_db)) -> Response:
    adresse = auth.loese_link_ein(db, token, uhr.jetzt())
    if adresse is None:
        return render(request, "anmelden.html", status_code=400, fehler=UNGUELTIGER_LINK)
    return _angemeldet(db, adresse)


@router.post("/abmelden")
def abmelden(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    auth.beende(db, request.cookies.get(auth.COOKIE))
    resp = RedirectResponse("/", status_code=303)
    auth.loesche_cookie(resp)
    return mit_flash(resp, "Du bist abgemeldet.")
```

`portal/beachhub_portal/routes/konto.py`:
```python
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from beachhub_portal import auth
from beachhub_portal.config import settings
from beachhub_portal.database import get_db
from beachhub_portal.models import Konto
from beachhub_portal.services import anfragen, konten
from beachhub_portal.templating import mit_flash, render

router = APIRouter()


@router.get("/willkommen", response_model=None)
def willkommen_seite(request: Request, konto: Konto = Depends(auth.konto_pflicht)) -> Response:
    if konto.anzeigename:
        return RedirectResponse("/", status_code=303)
    return render(request, "willkommen.html", konto=konto)


@router.post("/willkommen", response_model=None)
def willkommen(
    request: Request,
    anzeigename: str = Form(..., max_length=100),
    konto: Konto = Depends(auth.konto_pflicht),
    db: Session = Depends(get_db),
) -> Response:
    if konto.anzeigename:
        return RedirectResponse("/", status_code=303)
    name = anzeigename.strip()
    if not name:
        return render(
            request, "willkommen.html", konto=konto, status_code=400, fehler="Bitte gib einen Namen an."
        )
    konto.anzeigename = name
    db.commit()
    anfragen.stelle(
        db,
        typ="konto_angelegt",
        konto_id=konto.id,
        nutzlast={"email": konto.email, "anzeigename": name},
    )
    return mit_flash(RedirectResponse("/", status_code=303), f"Willkommen, {name}!")


@router.get("/konto", response_class=HTMLResponse)
def konto_seite(request: Request, konto: Konto = Depends(auth.konto_pflicht)) -> HTMLResponse:
    return render(request, "konto.html", konto=konto, betreiber_email=settings.betreiber_email)


@router.post("/konto/name")
def name_aendern(
    anzeigename: str = Form(..., max_length=100),
    konto: Konto = Depends(auth.konto_pflicht),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    name = anzeigename.strip()
    ziel = RedirectResponse("/konto", status_code=303)
    if not name:
        return mit_flash(ziel, "Bitte gib einen Namen an.", "fehler")
    if name != konto.anzeigename:
        bisher = konto.anzeigename
        konto.anzeigename = name
        db.commit()
        anfragen.stelle(
            db,
            typ="konto_geaendert",
            konto_id=konto.id,
            nutzlast={"anzeigename": name, "bisher": bisher},
        )
    return mit_flash(ziel, "Name gespeichert.")


@router.get("/konto/loeschen", response_class=HTMLResponse)
def loeschen_seite(request: Request, konto: Konto = Depends(auth.konto_pflicht)) -> HTMLResponse:
    return render(request, "konto_loeschen.html", konto=konto)


@router.post("/konto/loeschen")
def loeschen(
    konto: Konto = Depends(auth.konto_pflicht), db: Session = Depends(get_db)
) -> RedirectResponse:
    anfragen.stelle(db, typ="konto_loeschen", konto_id=konto.id, nutzlast={})
    konten.loesche(db, konto)
    resp = RedirectResponse("/", status_code=303)
    auth.loesche_cookie(resp)
    return mit_flash(resp, "Dein Konto ist gelöscht.")
```

`portal/beachhub_portal/templates/mail/login.txt`:
```
Hallo,

mit diesem Link meldest du dich im Buchungsportal der {{ betreiber }} an:
{{ link }}

Oder gib diesen Code auf der Anmeldeseite ein: {{ code }}

Link und Code gelten 15 Minuten und nur einmal. Wenn du keine Anmeldung angefordert hast,
kannst du diese Mail ignorieren.

Viele Grüße
{{ betreiber }}
```

`portal/beachhub_portal/templates/anmelden.html`:
```html
{% extends "base.html" %}{% block title %}Anmelden{% endblock %}
{% block content %}
<h1>Anmelden</h1>
{% if fehler %}<div class="flash fehler">{{ fehler }}</div>{% endif %}
{% if meldung %}<div class="flash ok">{{ meldung }}</div>{% endif %}
{% if dev_code %}<div class="flash warn">Entwicklung ohne Mailserver – Code <strong>{{ dev_code }}</strong> · <a href="{{ dev_link }}">Anmeldelink</a></div>{% endif %}
<form method="post" action="/anmelden" class="karte">
  <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
  <label>E-Mail-Adresse<input type="email" name="email" value="{{ code_email or '' }}" autocomplete="email" required></label>
  <button>Link und Code anfordern</button>
  <p class="hinweis">Du bekommst eine Mail mit einem Anmeldelink und einem sechsstelligen Code. Ein Passwort gibt es nicht.</p>
</form>
{% if code_email %}
<form method="post" action="/anmelden/code" class="karte">
  <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
  <input type="hidden" name="email" value="{{ code_email }}">
  <label>Code aus der Mail<input name="code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}" maxlength="6" required></label>
  <button>Anmelden</button>
</form>
{% endif %}
{% endblock %}
```

`portal/beachhub_portal/templates/anmelden_link.html`:
```html
{% extends "base.html" %}{% block title %}Anmelden{% endblock %}
{% block content %}
<div class="karte">
  <h1>Anmelden</h1>
  <p>Tippe auf den Knopf, um dich anzumelden.</p>
  <form method="post" action="/anmelden/link/{{ token }}">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <button>Jetzt anmelden</button>
  </form>
</div>
{% endblock %}
```

`portal/beachhub_portal/templates/willkommen.html`:
```html
{% extends "base.html" %}{% block title %}Willkommen{% endblock %}
{% block content %}
<h1>Willkommen!</h1>
{% if fehler %}<div class="flash fehler">{{ fehler }}</div>{% endif %}
<form method="post" action="/willkommen" class="karte">
  <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
  <label>Wie heißt du?<input name="anzeigename" maxlength="100" autocomplete="name" required></label>
  <p class="hinweis">Der Name erscheint auf deinen Buchungen und Rechnungen. Die Halle kann ihn für die Rechnung ergänzen.</p>
  <button>Weiter</button>
</form>
{% endblock %}
```

`portal/beachhub_portal/templates/konto.html`:
```html
{% extends "base.html" %}{% block title %}Konto{% endblock %}
{% block content %}
<h1>Dein Konto</h1>
<div class="karte">
  <p>Angemeldet als <strong>{{ konto.email }}</strong></p>
  {% if not konto.kunde_id %}<p class="hinweis">Dein Kundenkonto wird gerade eingerichtet.</p>{% endif %}
  <form method="post" action="/konto/name">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <label>Name<input name="anzeigename" value="{{ konto.anzeigename }}" maxlength="100" required></label>
    <button>Speichern</button>
  </form>
</div>
<div class="karte">
  <h2>Abo für eine ganze Saison</h2>
  <p>Ein festes Feld jede Woche lässt sich nicht online buchen. Schreib dafür bitte eine Mail an
  {% if betreiber_email %}<a href="mailto:{{ betreiber_email }}">{{ betreiber_email }}</a>{% else %}die Halle{% endif %}.</p>
</div>
<div class="karte">
  <form method="post" action="/abmelden" class="inline">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <button class="leise">Abmelden</button>
  </form>
  · <a href="/konto/loeschen">Konto löschen</a>
</div>
{% endblock %}
```

`portal/beachhub_portal/templates/konto_loeschen.html`:
```html
{% extends "base.html" %}{% block title %}Konto löschen{% endblock %}
{% block content %}
<h1>Konto löschen</h1>
<div class="karte">
  <p>Dein Konto wird endgültig gelöscht. Bestehende Buchungen bleiben bei der Halle bestehen;
  Rechnungen bewahrt die Halle aus gesetzlichen Gründen zehn Jahre auf.</p>
  <form method="post" action="/konto/loeschen">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <button class="gefahr">Konto endgültig löschen</button>
  </form>
  <p><a href="/konto">Abbrechen</a></p>
</div>
{% endblock %}
```

`portal/beachhub_portal/main.py`:
- Importe anpassen: `from fastapi import Depends, FastAPI`, `from beachhub_portal import auth`, `from beachhub_portal.routes import kanal, konto, oeffentlich`
- Die Router-Einbindung nach der `health`-Route ersetzen durch:
```python
csrf = [Depends(auth.verify_csrf)]
app.include_router(kanal.router, prefix="/core")
app.include_router(oeffentlich.router, dependencies=csrf)
app.include_router(konto.router, dependencies=csrf)
```

- [ ] **Step 6: Tests grün**

Run: `cd portal && ../.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 7: Lint, Typen, Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy
git add portal
git commit -m "feat(portal): Anmeldung per Link oder Code, Sessions, CSRF und Konto"
```

---
## Task 11: portal – Belegung mit freien Zeiten und Preisen

**Files:**
- Create: `portal/beachhub_portal/services/tarife.py`
- Create: `portal/beachhub_portal/services/slots.py`
- Create: `portal/beachhub_portal/routes/belegung.py`
- Create: `portal/beachhub_portal/templates/belegung.html`
- Modify: `portal/beachhub_portal/main.py`
- Test: `portal/tests/test_belegung.py`

**Interfaces:**
- Consumes: `lesestand.belegung/tarife/konto` (Task 9), `auth.konto_optional` (Task 10), `beachhub_shared.slots`
- Produces: `tarife.regel(t: TarifeInhalt, feld_id: str, slot: Slot, gruppe: str | None) -> TarifInfo | None` (spezifischste Regel, bei Gleichstand die spätere in der Liste = die neuere), `tarife.preis(t, feld_id, slots: list[Slot], gruppe) -> Decimal | None` (`None`, sobald ein Slot keine Regel hat)
- Produces: `slots.SlotAnzeige(beginn, ende, zustand: "frei"|"belegt"|"vorbei", preis: Decimal | None)`; `slots.feld(b, feld_id) -> FeldInfo | None`, `slots.felder(b) -> list[FeldInfo]`, `slots.tages_slots(b, feld: FeldInfo, datum) -> list[Slot]`, `slots.tage(b, jetzt) -> list[date]` (heute bis heute + `fenster_tage`), `slots.zustand(b, feld_id, slot, jetzt) -> str`, `slots.tagesansicht(b, t | None, gruppe | None, datum, jetzt) -> list[tuple[FeldInfo, list[SlotAnzeige]]]`, `slots.folge(b, feld_id, beginn, jetzt) -> list[Slot]` (der Slot ab `beginn` und alle direkt anschließenden freien)
- Produces: Route `GET /?tag=JJJJ-MM-TT`
- Regeln wie im Hauptsystem (`buchungen._pruefe_zeitraum`): frei = keine Überlappung mit `belegt`, `beginn >= jetzt + mindestvorlauf`, `beginn <= jetzt + fenster_tage`.

- [ ] **Step 1: Failing Tests schreiben**

`portal/tests/test_belegung.py`:
```python
from datetime import date, time
from decimal import Decimal

from beachhub_portal.services import slots
from beachhub_portal.services import tarife as tarif_dienst
from beachhub_shared.lesestand import BelegungInhalt, TarifeInhalt
from beachhub_shared.slots import Slot
from beachhub_shared.zeit import kombiniere
from fastapi.testclient import TestClient
from hilfen import FELD_ID, JETZT, KUNDE_ID, belegung, konto, speichere, tarif, tarife
from sqlalchemy.orm import Session

HEUTE = date(2027, 11, 25)  # JETZT = 10:00 Uhr Berlin


def _b(**abweichend) -> BelegungInhalt:
    return BelegungInhalt.model_validate(belegung(**abweichend))


def _slot(stunde: int, tag: date = HEUTE) -> Slot:
    return Slot(kombiniere(tag, time(stunde)), kombiniere(tag, time(stunde + 1)))


def _belegt(tag: date, von: int, bis: int) -> dict:
    return {
        "beginn": kombiniere(tag, time(von)).isoformat(),
        "ende": kombiniere(tag, time(bis)).isoformat(),
    }


def test_zustaende() -> None:
    b = _b(belegt={FELD_ID: [_belegt(HEUTE, 19, 21)]})
    [(feld, liste)] = slots.tagesansicht(b, None, None, HEUTE, JETZT)
    zustand = {s.beginn: s.zustand for s in liste}
    assert len(liste) == 14  # 9 bis 23 Uhr
    assert zustand[kombiniere(HEUTE, time(10))] == "vorbei"  # Mindestvorlauf 60 min
    assert zustand[kombiniere(HEUTE, time(11))] == "frei"
    assert zustand[kombiniere(HEUTE, time(19))] == "belegt"
    assert zustand[kombiniere(HEUTE, time(20))] == "belegt"
    assert all(s.preis is None for s in liste)


def test_tage_und_fenstergrenze() -> None:
    b = _b(fenster_tage=3)
    assert slots.tage(b, JETZT) == [date(2027, 11, d) for d in (25, 26, 27, 28)]
    letzter = date(2027, 11, 28)  # Fensterende: 28.11. 10:00 Uhr
    assert slots.zustand(b, FELD_ID, _slot(9, letzter), JETZT) == "frei"
    assert slots.zustand(b, FELD_ID, _slot(12, letzter), JETZT) == "vorbei"


def test_betriebszeit_gueltigkeit_und_ausnahmetag() -> None:
    bz = [
        {
            "wochentag": wt,
            "oeffnet": "09:00:00",
            "schliesst": "23:00:00",
            "gueltig_von": None,
            "gueltig_bis": "2027-11-25",
        }
        for wt in range(7)
    ]
    ausnahme = {"datum": "2027-11-25", "geschlossen": False, "oeffnet": "18:00:00", "schliesst": "20:00:00"}
    b = _b(betriebszeiten=bz, ausnahmetage=[ausnahme])
    f = slots.feld(b, FELD_ID)
    assert [s.beginn for s in slots.tages_slots(b, f, HEUTE)] == [
        kombiniere(HEUTE, time(18)),
        kombiniere(HEUTE, time(19)),
    ]
    assert slots.tages_slots(b, f, date(2027, 11, 26)) == []


def test_fensterraster() -> None:
    raster = [{"wochentag": None, "modus": "fenster", "slot_minuten": None,
               "fenster": [["19:00", "21:00"], ["21:00", "23:00"]]}]
    b = _b(felder=[{"id": FELD_ID, "name": "Feld 1", "reihenfolge": 1, "raster": raster}])
    ergebnis = [(s.beginn, s.ende) for s in slots.tages_slots(b, slots.feld(b, FELD_ID), HEUTE)]
    assert ergebnis == [
        (kombiniere(HEUTE, time(19)), kombiniere(HEUTE, time(21))),
        (kombiniere(HEUTE, time(21)), kombiniere(HEUTE, time(23))),
    ]


def test_folge_bis_zur_naechsten_belegung() -> None:
    b = _b(belegt={FELD_ID: [_belegt(HEUTE, 19, 21)]})
    assert slots.folge(b, FELD_ID, kombiniere(HEUTE, time(17)), JETZT) == [_slot(17), _slot(18)]
    assert slots.folge(b, FELD_ID, kombiniere(HEUTE, time(19)), JETZT) == []
    assert slots.folge(b, FELD_ID, kombiniere(HEUTE, time(17, 30)), JETZT) == []
    assert slots.folge(b, "gibtsnicht", kombiniere(HEUTE, time(17)), JETZT) == []


def test_preis_spezifischste_regel_und_neuere_bei_gleichstand() -> None:
    t = TarifeInhalt.model_validate(
        tarife(
            tarif("Std", "30.00"),
            tarif("Abend", "40.00", uhrzeit_von="18:00:00", uhrzeit_bis="23:00:00"),
            tarif("Abend neu", "42.00", uhrzeit_von="18:00:00", uhrzeit_bis="23:00:00"),
            tarif("Mitglied", "20.00", kundengruppe="Mitglied"),
        )
    )
    assert tarif_dienst.preis(t, FELD_ID, [_slot(12)], "Privat") == Decimal("30.00")
    assert tarif_dienst.preis(t, FELD_ID, [_slot(19)], "Privat") == Decimal("42.00")
    assert tarif_dienst.preis(t, FELD_ID, [_slot(12)], "Mitglied") == Decimal("20.00")
    assert tarif_dienst.preis(t, FELD_ID, [_slot(18), _slot(19)], "Privat") == Decimal("84.00")
    assert tarif_dienst.preis(TarifeInhalt(regeln=[]), FELD_ID, [_slot(12)], "Privat") is None
    assert tarif_dienst.preis(t, FELD_ID, [], "Privat") is None


def test_ohne_lesestand_hinweis(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200 and "wird gerade geladen" in r.text


def test_anonym_ohne_preise(client: TestClient, db: Session, uhr_steht) -> None:
    speichere(db, "belegung", belegung())
    speichere(db, "tarife", tarife())
    seite = client.get("/").text
    assert "Feld 1" in seite and "/buchen?feld=" in seite
    assert "30,00" not in seite and "Melde dich an" in seite


def test_angemeldet_mit_preisen_und_tagwahl(angemeldet: TestClient, db: Session) -> None:
    speichere(db, "belegung", belegung())
    speichere(db, "tarife", tarife())
    speichere(db, f"konto:{KUNDE_ID}", konto())
    seite = angemeldet.get("/?tag=2027-11-26").text
    assert "30,00 €" in seite
    assert 'href="/?tag=2027-11-26" aria-current="page"' in seite
    assert angemeldet.get("/?tag=quatsch").status_code == 200
    assert angemeldet.get("/?tag=2030-01-01").status_code == 200
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd portal && ../.venv/bin/pytest -q tests/test_belegung.py`
Expected: FAIL mit `ImportError: cannot import name 'slots'`

- [ ] **Step 3: Implementieren**

`portal/beachhub_portal/services/tarife.py`:
```python
"""Preis zur Anzeige im Portal – dieselbe Auflösung wie im Hauptsystem (A-TARIF-2).

Verbindlich ist der Preis, den das Hauptsystem beim Buchen festschreibt; hier geht es nur
darum, dem Kunden vorher den richtigen Betrag zu zeigen.
"""

from decimal import Decimal

from beachhub_shared.lesestand import TarifeInhalt, TarifInfo
from beachhub_shared.slots import Slot
from beachhub_shared.zeit import lokal


def _passt(t: TarifInfo, feld_id: str, slot: Slot, gruppe: str | None) -> bool:
    lok = lokal(slot.beginn)
    if t.feld_id is not None and t.feld_id != feld_id:
        return False
    if t.wochentag is not None and t.wochentag != lok.weekday():
        return False
    if t.uhrzeit_von is not None and t.uhrzeit_bis is not None:
        if not (t.uhrzeit_von <= lok.time() < t.uhrzeit_bis):
            return False
    if t.kundengruppe is not None and t.kundengruppe != gruppe:
        return False
    if t.gueltig_von is not None and lok.date() < t.gueltig_von:
        return False
    if t.gueltig_bis is not None and lok.date() > t.gueltig_bis:
        return False
    return True


def _spezifitaet(t: TarifInfo) -> int:
    return sum(
        [
            t.feld_id is not None,
            t.wochentag is not None,
            t.uhrzeit_von is not None and t.uhrzeit_bis is not None,
            t.kundengruppe is not None,
            t.gueltig_von is not None or t.gueltig_bis is not None,
        ]
    )


def regel(t: TarifeInhalt, feld_id: str, slot: Slot, gruppe: str | None) -> TarifInfo | None:
    kandidaten = [(i, r) for i, r in enumerate(t.regeln) if _passt(r, feld_id, slot, gruppe)]
    if not kandidaten:
        return None
    # Das Hauptsystem liefert die Regeln nach Anlage sortiert: der höhere Index ist die neuere.
    return max(kandidaten, key=lambda paar: (_spezifitaet(paar[1]), paar[0]))[1]


def preis(t: TarifeInhalt, feld_id: str, slots: list[Slot], gruppe: str | None) -> Decimal | None:
    if not slots:
        return None
    summe = Decimal("0.00")
    for slot in slots:
        r = regel(t, feld_id, slot, gruppe)
        if r is None:
            return None
        summe += r.preis
    return summe
```

`portal/beachhub_portal/services/slots.py`:
```python
"""Freie Zeiten aus dem Lesestand `belegung` – reine Funktionen ohne Datenbank.

Dieselben Regeln wie im Hauptsystem (`buchungen._pruefe_zeitraum`); verbindlich prüft aber
erst das Hauptsystem beim Buchen.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal

from beachhub_shared import slots as sl
from beachhub_shared.lesestand import BelegungInhalt, FeldInfo, TarifeInhalt
from beachhub_shared.zeit import lokales_datum

from beachhub_portal.services import tarife as tarif_dienst

Zustand = Literal["frei", "belegt", "vorbei"]


@dataclass(frozen=True)
class SlotAnzeige:
    beginn: datetime
    ende: datetime
    zustand: Zustand
    preis: Decimal | None


def _zeit(wert: str) -> time:
    h, m = wert.split(":")[:2]
    return time(int(h), int(m))


def _raster(feld: FeldInfo) -> list[sl.RasterKonfig]:
    return [
        sl.RasterKonfig(
            wochentag=r.wochentag,
            modus="fenster" if r.modus == "fenster" else "dauer",
            slot_minuten=r.slot_minuten,
            fenster=[(_zeit(a), _zeit(b)) for a, b in r.fenster],
        )
        for r in feld.raster
    ]


def _betriebszeiten(b: BelegungInhalt, datum: date) -> list[sl.Betriebszeit]:
    return [
        sl.Betriebszeit(z.wochentag, z.oeffnet, z.schliesst)
        for z in b.betriebszeiten
        if (z.gueltig_von is None or z.gueltig_von <= datum)
        and (z.gueltig_bis is None or z.gueltig_bis >= datum)
    ]


def _ausnahmen(b: BelegungInhalt, datum: date) -> list[sl.Ausnahme]:
    return [
        sl.Ausnahme(a.datum, a.geschlossen, a.oeffnet, a.schliesst)
        for a in b.ausnahmetage
        if a.datum == datum
    ]


def felder(b: BelegungInhalt) -> list[FeldInfo]:
    return sorted(b.felder, key=lambda f: f.reihenfolge)


def feld(b: BelegungInhalt, feld_id: str) -> FeldInfo | None:
    return next((f for f in b.felder if f.id == feld_id), None)


def tages_slots(b: BelegungInhalt, f: FeldInfo, datum: date) -> list[sl.Slot]:
    return sl.slots_fuer_tag(datum, _raster(f), _betriebszeiten(b, datum), _ausnahmen(b, datum))


def tage(b: BelegungInhalt, jetzt: datetime) -> list[date]:
    heute = lokales_datum(jetzt)
    return [heute + timedelta(days=i) for i in range(b.fenster_tage + 1)]


def zustand(b: BelegungInhalt, feld_id: str, slot: sl.Slot, jetzt: datetime) -> Zustand:
    for z in b.belegt.get(feld_id, []):
        if z.beginn < slot.ende and z.ende > slot.beginn:
            return "belegt"
    if slot.beginn < jetzt + timedelta(minutes=b.mindestvorlauf_minuten):
        return "vorbei"
    if slot.beginn > jetzt + timedelta(days=b.fenster_tage):
        return "vorbei"
    return "frei"


def tagesansicht(
    b: BelegungInhalt,
    t: TarifeInhalt | None,
    gruppe: str | None,
    datum: date,
    jetzt: datetime,
) -> list[tuple[FeldInfo, list[SlotAnzeige]]]:
    ansicht: list[tuple[FeldInfo, list[SlotAnzeige]]] = []
    for f in felder(b):
        liste: list[SlotAnzeige] = []
        for s in tages_slots(b, f, datum):
            z = zustand(b, f.id, s, jetzt)
            preis = None
            if t is not None and gruppe is not None and z == "frei":
                preis = tarif_dienst.preis(t, f.id, [s], gruppe)
            liste.append(SlotAnzeige(s.beginn, s.ende, z, preis))
        ansicht.append((f, liste))
    return ansicht


def folge(b: BelegungInhalt, feld_id: str, beginn: datetime, jetzt: datetime) -> list[sl.Slot]:
    """Der Slot ab `beginn` und alle direkt anschließenden, ebenfalls freien Slots."""
    f = feld(b, feld_id)
    if f is None:
        return []
    tages = tages_slots(b, f, lokales_datum(beginn))
    start = next((i for i, s in enumerate(tages) if s.beginn == beginn), None)
    if start is None or zustand(b, feld_id, tages[start], jetzt) != "frei":
        return []
    ergebnis = [tages[start]]
    for s in tages[start + 1 :]:
        if s.beginn != ergebnis[-1].ende or zustand(b, feld_id, s, jetzt) != "frei":
            break
        ergebnis.append(s)
    return ergebnis
```

`portal/beachhub_portal/routes/belegung.py`:
```python
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from beachhub_portal import auth, uhr
from beachhub_portal.database import get_db
from beachhub_portal.models import Konto
from beachhub_portal.services import lesestand, slots
from beachhub_portal.templating import render

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def belegung_seite(
    request: Request,
    tag: str = "",
    konto: Konto | None = Depends(auth.konto_optional),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    b = lesestand.belegung(db)
    if b is None:
        return render(request, "belegung.html", konto=konto, belegung=None)
    jetzt = uhr.jetzt()
    tage = slots.tage(b, jetzt)
    try:
        datum = date.fromisoformat(tag) if tag else tage[0]
    except ValueError:
        datum = tage[0]
    if datum not in tage:
        datum = tage[0]
    inhalt = lesestand.konto(db, konto.kunde_id) if konto else None
    gruppe = inhalt.kundengruppe if inhalt else None
    ansicht = slots.tagesansicht(b, lesestand.tarife(db), gruppe, datum, jetzt)
    return render(
        request,
        "belegung.html",
        konto=konto,
        belegung=b,
        tage=tage,
        datum=datum,
        ansicht=ansicht,
    )
```

`portal/beachhub_portal/templates/belegung.html`:
```html
{% extends "base.html" %}{% block title %}Belegung{% endblock %}
{% block content %}
<h1>Freie Zeiten</h1>
{% if belegung is none %}
<div class="karte"><p>Die Belegung wird gerade geladen. Bitte versuche es in einer Minute noch einmal.</p></div>
{% else %}
<nav class="tage" aria-label="Tag wählen">
  {% for t in tage %}<a href="/?tag={{ t.isoformat() }}"{% if t == datum %} aria-current="page"{% endif %}>{{ t|tag }}</a>{% endfor %}
</nav>
{% if not konto %}<p class="hinweis"><a href="/anmelden">Melde dich an</a>, um Preise zu sehen und zu buchen.</p>{% endif %}
{% for feld, liste in ansicht %}
<section class="karte">
  <h2>{{ feld.name }}</h2>
  {% if liste %}
  <div class="slots">
    {% for s in liste %}
    {% if s.zustand == "frei" %}
    <a class="slot frei" href="/buchen?feld={{ feld.id }}&amp;beginn={{ s.beginn.isoformat()|urlencode }}">{{ s.beginn|uhrzeit }}{% if s.preis is not none %}<span class="preis">{{ s.preis|euro }}</span>{% endif %}</a>
    {% else %}
    <span class="slot {{ s.zustand }}">{{ s.beginn|uhrzeit }}<span class="preis">{{ "belegt" if s.zustand == "belegt" else "–" }}</span></span>
    {% endif %}
    {% endfor %}
  </div>
  {% else %}
  <p class="hinweis">An diesem Tag ist das Feld nicht buchbar.</p>
  {% endif %}
</section>
{% endfor %}
{% endif %}
{% endblock %}
```

`portal/beachhub_portal/main.py`: Import `belegung` zu `from beachhub_portal.routes import …` ergänzen und nach den übrigen `include_router`-Zeilen `app.include_router(belegung.router, dependencies=csrf)`.

- [ ] **Step 4: Tests grün**

Run: `cd portal && ../.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 5: Lint, Typen, Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy
git add portal
git commit -m "feat(portal): Belegung mit freien Zeiten und Preisen aus dem Lesestand"
```

---

## Task 12: portal – Buchen und Warteseite

**Files:**
- Modify: `portal/beachhub_portal/services/anfragen.py` (`Stand`, `stand()`, Texte)
- Create: `portal/beachhub_portal/routes/buchen.py`
- Create: `portal/beachhub_portal/templates/buchen.html`, `templates/anfrage.html`, `static/warten.js`
- Modify: `portal/beachhub_portal/main.py`
- Test: `portal/tests/test_buchen.py`

**Interfaces:**
- Consumes: `slots.folge/feld`, `tarife.preis` (Task 11), `anfragen.stelle/beantworte` (Task 9), `lesestand.*`
- Produces: `anfragen.Stand(zustand: "wartet"|"zahlung"|"fertig"|"abgelehnt"|"fehler", text: str, ziel: str | None = None, zahlung_url: str | None = None)`
- Produces: `anfragen.stand(db, a: Anfrage, kunde_id: uuid.UUID | None, jetzt, *, weiter: bool, hinweis_sekunden: int) -> Stand` – `ziel` für `buchung_anfragen`/`bestaetigt` bzw. nach Zahlung: `/buchungen?meldung=bestaetigt`; `reserviert` mit `weiter=True`: `checkout_url`; `buchung_stornieren`: `/buchungen?meldung=storniert_kostenfrei|storniert_kostenpflichtig`; `rechnung_anfordern` mit `link_token` (Task 15): `/rechnung/<token>`; `konto_*`: `/konto`
- Produces: `anfragen.GRUENDE: dict[str, str]`, `anfragen.MELDUNGEN: dict[str, str]` (Schlüssel `bestaetigt`, `storniert_kostenfrei`, `storniert_kostenpflichtig`)
- Produces: Routen `GET /buchen?feld=&beginn=`, `POST /buchen` → `303 /anfrage/<id>?weiter=1`, `GET /anfrage/{id}[?weiter=1]` (303 zum Ziel, sonst Seite), `GET /anfrage/{id}/stand` → `{"zustand", "text", "ziel"}`

- [ ] **Step 1: Failing Tests schreiben**

`portal/tests/test_buchen.py`:
```python
import uuid
from datetime import date, time, timedelta

import pytest
from beachhub_portal.models import Anfrage, KanalKontakt
from beachhub_portal.services import anfragen
from beachhub_shared import kanal
from beachhub_shared.zeit import kombiniere
from fastapi.testclient import TestClient
from hilfen import FELD_ID, JETZT, KUNDE_ID, belegung, buchung, konto, speichere, tarife
from sqlalchemy import select
from sqlalchemy.orm import Session

TAG = date(2027, 11, 26)
B17, B18 = kombiniere(TAG, time(17)), kombiniere(TAG, time(18))


@pytest.fixture
def welt(db: Session) -> None:
    belegt = {"beginn": kombiniere(TAG, time(19)).isoformat(), "ende": kombiniere(TAG, time(21)).isoformat()}
    speichere(db, "belegung", belegung(belegt={FELD_ID: [belegt]}))
    speichere(db, "tarife", tarife())
    speichere(db, f"konto:{KUNDE_ID}", konto())


def _anfrage(db: Session, konto_id: uuid.UUID) -> Anfrage:
    return anfragen.stelle(
        db,
        typ="buchung_anfragen",
        konto_id=konto_id,
        nutzlast={"feld_id": FELD_ID, "beginn": B17.isoformat(), "ende": B18.isoformat()},
    )


def _antworte(db: Session, a: Anfrage, **antwort) -> None:
    anfragen.beantworte(db, a.id, kanal.Antwort(**antwort), JETZT)
    db.commit()


def test_buchen_seite_bietet_folgeslots(angemeldet: TestClient, welt) -> None:
    seite = angemeldet.get("/buchen", params={"feld": FELD_ID, "beginn": B17.isoformat()}).text
    assert "bis 18:00 Uhr – 30,00 €" in seite
    assert "bis 19:00 Uhr – 60,00 €" in seite
    assert "bis 20:00" not in seite
    assert "24 Stunden" in seite


def test_buchen_seite_anonym(client: TestClient, welt, uhr_steht) -> None:
    seite = client.get("/buchen", params={"feld": FELD_ID, "beginn": B17.isoformat()}).text
    assert "Melde dich an" in seite and "Verbindlich buchen" not in seite


def test_belegter_oder_kaputter_termin(angemeldet: TestClient, welt) -> None:
    belegt = kombiniere(TAG, time(19)).isoformat()
    r = angemeldet.get("/buchen", params={"feld": FELD_ID, "beginn": belegt})
    assert r.status_code == 409 and "nicht mehr frei" in r.text
    assert angemeldet.get("/buchen", params={"feld": FELD_ID, "beginn": "quatsch"}).status_code == 409
    ohne_zone = "2027-11-26T17:00:00"
    assert angemeldet.get("/buchen", params={"feld": FELD_ID, "beginn": ohne_zone}).status_code == 409


def test_buchen_legt_anfrage_an(angemeldet: TestClient, welt, db: Session) -> None:
    ende = kombiniere(TAG, time(19))
    r = angemeldet.post(
        "/buchen",
        data={"feld": FELD_ID, "beginn": B17.isoformat(), "ende": ende.isoformat(), "csrf_token": angemeldet.csrf},
        follow_redirects=False,
    )
    a = db.scalar(select(Anfrage))
    assert r.headers["location"] == f"/anfrage/{a.id}?weiter=1"
    assert a.typ == "buchung_anfragen" and a.konto_id == angemeldet.konto_id
    assert a.nutzlast_json == {"feld_id": FELD_ID, "beginn": B17.isoformat(), "ende": ende.isoformat()}


def test_buchen_ueber_belegung_hinaus_abgewiesen(angemeldet: TestClient, welt, db: Session) -> None:
    r = angemeldet.post(
        "/buchen",
        data={
            "feld": FELD_ID,
            "beginn": B17.isoformat(),
            "ende": kombiniere(TAG, time(20)).isoformat(),
            "csrf_token": angemeldet.csrf,
        },
        follow_redirects=False,
    )
    assert r.headers["location"] == "/"
    assert db.scalar(select(Anfrage)) is None


def test_buchen_ohne_csrf_verboten(angemeldet: TestClient, welt) -> None:
    daten = {"feld": FELD_ID, "beginn": B17.isoformat(), "ende": B18.isoformat()}
    assert angemeldet.post("/buchen", data=daten).status_code == 403


def test_stand_wartet(angemeldet: TestClient, welt, db: Session) -> None:
    a = _anfrage(db, angemeldet.konto_id)
    r = angemeldet.get(f"/anfrage/{a.id}")
    assert r.status_code == 200 and "wird bearbeitet" in r.text and "/static/warten.js" in r.text
    stand = angemeldet.get(f"/anfrage/{a.id}/stand").json()
    assert stand["zustand"] == "wartet" and stand["ziel"] is None


def test_stand_zeigt_hinweis_nach_wartezeit(angemeldet: TestClient, welt, db: Session, uhr_steht) -> None:
    a = _anfrage(db, angemeldet.konto_id)
    uhr_steht.weiter(seconds=121)
    assert "nicht erreichbar" in angemeldet.get(f"/anfrage/{a.id}/stand").json()["text"]


def test_stand_hinweis_wenn_hauptsystem_lange_still(angemeldet: TestClient, welt, db: Session) -> None:
    db.merge(KanalKontakt(id=1, letzter_abruf=JETZT - timedelta(minutes=5)))
    db.commit()
    a = _anfrage(db, angemeldet.konto_id)
    assert "nicht erreichbar" in angemeldet.get(f"/anfrage/{a.id}/stand").json()["text"]


def test_reserviert_leitet_einmal_zur_zahlung(angemeldet: TestClient, welt, db: Session) -> None:
    a = _anfrage(db, angemeldet.konto_id)
    _antworte(db, a, status="reserviert", buchung_id=uuid.uuid4(), checkout_url="/test-zahlung/fake_x?betrag=30.00")
    r = angemeldet.get(f"/anfrage/{a.id}?weiter=1", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/test-zahlung/fake_x?betrag=30.00"
    # Zurück von der Zahlung (ohne weiter): nicht erneut weiterleiten, sondern warten
    r = angemeldet.get(f"/anfrage/{a.id}")
    assert r.status_code == 200 and "Zur Zahlung" in r.text


def test_nach_zahlung_bestaetigt(angemeldet: TestClient, welt, db: Session) -> None:
    a = _anfrage(db, angemeldet.konto_id)
    bid = uuid.uuid4()
    _antworte(db, a, status="reserviert", buchung_id=bid, checkout_url="/x")
    speichere(db, f"konto:{KUNDE_ID}", konto(buchungen=[buchung(B17, B18, id=str(bid))]), version=2)
    r = angemeldet.get(f"/anfrage/{a.id}", follow_redirects=False)
    assert r.headers["location"] == "/buchungen?meldung=bestaetigt"


def test_verfallene_reservierung(angemeldet: TestClient, welt, db: Session) -> None:
    a = _anfrage(db, angemeldet.konto_id)
    bid = uuid.uuid4()
    _antworte(db, a, status="reserviert", buchung_id=bid, checkout_url="/x")
    verfallen = buchung(B17, B18, status="verfallen", id=str(bid))
    speichere(db, f"konto:{KUNDE_ID}", konto(buchungen=[verfallen]), version=2)
    assert "Zahlungsfrist ist abgelaufen" in angemeldet.get(f"/anfrage/{a.id}").text


def test_bestaetigt_abgelehnt_fehler(angemeldet: TestClient, welt, db: Session) -> None:
    a = _anfrage(db, angemeldet.konto_id)
    _antworte(db, a, status="bestaetigt", buchung_id=uuid.uuid4())
    r = angemeldet.get(f"/anfrage/{a.id}", follow_redirects=False)
    assert r.headers["location"] == "/buchungen?meldung=bestaetigt"
    b = _anfrage(db, angemeldet.konto_id)
    _antworte(db, b, status="abgelehnt", grund="belegt")
    assert "inzwischen vergeben" in angemeldet.get(f"/anfrage/{b.id}").text
    c = _anfrage(db, angemeldet.konto_id)
    _antworte(db, c, status="fehler")
    assert "Fehler aufgetreten" in angemeldet.get(f"/anfrage/{c.id}").text


def test_fremde_anfrage_404(angemeldet: TestClient, welt, db: Session) -> None:
    a = _anfrage(db, uuid.uuid4())
    assert angemeldet.get(f"/anfrage/{a.id}").status_code == 404
    assert angemeldet.get(f"/anfrage/{a.id}/stand").status_code == 404


def test_warten_js_wird_ausgeliefert(client: TestClient) -> None:
    assert client.get("/static/warten.js").status_code == 200
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd portal && ../.venv/bin/pytest -q tests/test_buchen.py`
Expected: FAIL (404 auf `/buchen`)

- [ ] **Step 3: Stand der Anfrage**

`portal/beachhub_portal/services/anfragen.py` – Importe ergänzen (`from dataclasses import dataclass`, `from typing import Any, Literal`, `from beachhub_portal.services import lesestand, wecker`) und am Ende anfügen:
```python
WARTET = "Deine Anfrage wird bearbeitet …"
HINWEIS = (
    "Deine Anfrage ist gespeichert. Das Buchungssystem ist gerade nicht erreichbar; "
    "du bekommst die Bestätigung per E-Mail."
)
GRUENDE: dict[str, str] = {
    "belegt": "Der Termin ist inzwischen vergeben. Bitte wähle einen anderen.",
    "ausserhalb_fenster": "Der Termin liegt außerhalb des Buchungsfensters.",
    "ausserhalb_betriebszeit": "Zu dieser Zeit ist die Halle nicht geöffnet.",
    "kein_tarif": "Für diesen Termin gibt es keinen Preis. Bitte wende dich an die Halle.",
    "feld_inaktiv": "Dieses Feld ist derzeit nicht buchbar.",
    "konto_gesperrt": "Dein Konto ist für Buchungen gesperrt. Bitte wende dich an die Halle.",
    "konto_unbekannt": "Dein Konto wird noch eingerichtet. Bitte versuche es gleich noch einmal.",
    "nicht_gefunden": "Das haben wir nicht gefunden.",
    "zu_spaet": "Der Termin hat schon begonnen und kann nicht mehr storniert werden.",
}
MELDUNGEN: dict[str, str] = {
    "bestaetigt": "Deine Buchung ist bestätigt.",
    "storniert_kostenfrei": "Deine Buchung ist storniert. Die Stornierung ist kostenfrei.",
    "storniert_kostenpflichtig": (
        "Deine Buchung ist storniert. Da die Stornofrist abgelaufen war, bleibt der Betrag fällig."
    ),
}


@dataclass(frozen=True)
class Stand:
    zustand: Literal["wartet", "zahlung", "fertig", "abgelehnt", "fehler"]
    text: str
    ziel: str | None = None
    zahlung_url: str | None = None


def _nach_zahlung(
    db: Session, antwort: dict[str, Any], kunde_id: uuid.UUID | None, weiter: bool
) -> Stand:
    inhalt = lesestand.konto(db, kunde_id)
    buchung_id = antwort.get("buchung_id")
    kb = next((b for b in inhalt.buchungen if b.id == buchung_id), None) if inhalt else None
    if kb is not None and kb.status == "bestaetigt":
        return Stand("fertig", MELDUNGEN["bestaetigt"], ziel="/buchungen?meldung=bestaetigt")
    if kb is not None and kb.status in ("verfallen", "storniert"):
        return Stand(
            "abgelehnt", "Die Zahlungsfrist ist abgelaufen; der Termin wurde wieder freigegeben."
        )
    url = antwort.get("checkout_url")
    return Stand(
        "zahlung",
        "Bitte schließe die Zahlung ab. Sobald sie bestätigt ist, geht es hier automatisch weiter.",
        ziel=url if weiter else None,
        zahlung_url=url,
    )


def stand(
    db: Session,
    a: Anfrage,
    kunde_id: uuid.UUID | None,
    jetzt: datetime,
    *,
    weiter: bool,
    hinweis_sekunden: int,
) -> Stand:
    if a.status != Anfrage.BEANTWORTET:
        alter = (jetzt - a.erstellt_am).total_seconds()
        kontakt = db.get(KanalKontakt, 1)
        still = (
            kontakt is not None
            and (jetzt - kontakt.letzter_abruf).total_seconds() > hinweis_sekunden
        )
        return Stand("wartet", HINWEIS if alter > hinweis_sekunden or still else WARTET)
    antwort = a.antwort_json or {}
    status = antwort.get("status")
    if status == "fehler":
        return Stand(
            "fehler",
            "Bei der Verarbeitung ist ein Fehler aufgetreten. "
            "Bitte versuche es später noch einmal.",
        )
    if status in ("abgelehnt", "ignoriert"):
        return Stand(
            "abgelehnt", GRUENDE.get(str(antwort.get("grund")), "Die Anfrage wurde abgelehnt.")
        )
    if a.typ == "buchung_anfragen":
        if status == "bestaetigt":
            return Stand("fertig", MELDUNGEN["bestaetigt"], ziel="/buchungen?meldung=bestaetigt")
        return _nach_zahlung(db, antwort, kunde_id, weiter)
    if a.typ == "buchung_stornieren":
        art = "storniert_kostenfrei" if antwort.get("kostenfrei") else "storniert_kostenpflichtig"
        return Stand("fertig", MELDUNGEN[art], ziel=f"/buchungen?meldung={art}")
    if a.typ == "rechnung_anfordern":
        token = antwort.get("link_token")
        if token:
            return Stand("fertig", "Deine Rechnung steht bereit.", ziel=f"/rechnung/{token}")
        return Stand("abgelehnt", "Die Rechnung konnte nicht bereitgestellt werden.")
    return Stand("fertig", "Erledigt.", ziel="/konto")
```

- [ ] **Step 4: Routen, Templates, Skript**

`portal/beachhub_portal/routes/buchen.py`:
```python
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from beachhub_portal import auth, uhr
from beachhub_portal.database import get_db
from beachhub_portal.models import Anfrage, Konto
from beachhub_portal.services import anfragen, lesestand, slots
from beachhub_portal.services import tarife as tarif_dienst
from beachhub_portal.templating import mit_flash, render

router = APIRouter()


def _zeitpunkt(wert: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(wert)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else None


@router.get("/buchen", response_class=HTMLResponse)
def buchen_seite(
    request: Request,
    feld: str = "",
    beginn: str = "",
    konto: Konto | None = Depends(auth.konto_optional),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    b = lesestand.belegung(db)
    start = _zeitpunkt(beginn)
    f = slots.feld(b, feld) if b else None
    folge = slots.folge(b, feld, start, uhr.jetzt()) if (b and f and start) else []
    if not folge or b is None or f is None or start is None:
        return render(request, "buchen.html", konto=konto, status_code=409, nicht_frei=True)
    inhalt = lesestand.konto(db, konto.kunde_id) if konto else None
    t = lesestand.tarife(db)
    optionen = [
        {
            "ende": s.ende,
            "preis": (
                tarif_dienst.preis(t, feld, folge[: i + 1], inhalt.kundengruppe)
                if t and inhalt
                else None
            ),
        }
        for i, s in enumerate(folge)
    ]
    return render(
        request,
        "buchen.html",
        konto=konto,
        feld=f,
        beginn=start,
        optionen=optionen,
        storno_frist=b.storno_frist_stunden,
    )


@router.post("/buchen")
def buchen(
    feld: str = Form(...),
    beginn: str = Form(...),
    ende: str = Form(...),
    konto: Konto = Depends(auth.konto_pflicht),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    b = lesestand.belegung(db)
    start, schluss = _zeitpunkt(beginn), _zeitpunkt(ende)
    folge = slots.folge(b, feld, start, uhr.jetzt()) if (b and start) else []
    if start is None or schluss is None or schluss not in [s.ende for s in folge]:
        return mit_flash(
            RedirectResponse("/", status_code=303),
            "Der Termin ist nicht mehr frei. Bitte wähle einen anderen.",
            "fehler",
        )
    a = anfragen.stelle(
        db,
        typ="buchung_anfragen",
        konto_id=konto.id,
        nutzlast={"feld_id": feld, "beginn": start.isoformat(), "ende": schluss.isoformat()},
    )
    return RedirectResponse(f"/anfrage/{a.id}?weiter=1", status_code=303)


def _eigene(db: Session, anfrage_id: uuid.UUID, konto: Konto) -> Anfrage:
    a = db.get(Anfrage, anfrage_id)
    if a is None or a.konto_id != konto.id:
        raise HTTPException(status_code=404)
    return a


def _stand(db: Session, a: Anfrage, konto: Konto, weiter: bool) -> anfragen.Stand:
    b = lesestand.belegung(db)
    return anfragen.stand(
        db,
        a,
        konto.kunde_id,
        uhr.jetzt(),
        weiter=weiter,
        hinweis_sekunden=b.antwort_hinweis_sekunden if b else 120,
    )


@router.get("/anfrage/{anfrage_id}", response_model=None)
def anfrage_seite(
    request: Request,
    anfrage_id: uuid.UUID,
    weiter: int = 0,
    konto: Konto = Depends(auth.konto_pflicht),
    db: Session = Depends(get_db),
) -> Response:
    a = _eigene(db, anfrage_id, konto)
    st = _stand(db, a, konto, bool(weiter))
    if st.ziel:
        return RedirectResponse(st.ziel, status_code=303)
    stand_url = f"/anfrage/{a.id}/stand" + ("?weiter=1" if weiter else "")
    return render(request, "anfrage.html", konto=konto, stand=st, stand_url=stand_url)


@router.get("/anfrage/{anfrage_id}/stand")
def anfrage_stand(
    anfrage_id: uuid.UUID,
    weiter: int = 0,
    konto: Konto = Depends(auth.konto_pflicht),
    db: Session = Depends(get_db),
) -> dict[str, str | None]:
    st = _stand(db, _eigene(db, anfrage_id, konto), konto, bool(weiter))
    return {"zustand": st.zustand, "text": st.text, "ziel": st.ziel}
```

`portal/beachhub_portal/templates/buchen.html`:
```html
{% extends "base.html" %}{% block title %}Buchen{% endblock %}
{% block content %}
<h1>Buchen</h1>
{% if nicht_frei %}
<div class="karte">
  <p>Dieser Termin ist nicht mehr frei oder nicht mehr buchbar.</p>
  <p><a href="/">Zurück zur Belegung</a></p>
</div>
{% else %}
<div class="karte">
  <h2>{{ feld.name }} · {{ beginn|lokal }} Uhr</h2>
  {% if konto %}
  <form method="post" action="/buchen">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <input type="hidden" name="feld" value="{{ feld.id }}">
    <input type="hidden" name="beginn" value="{{ beginn.isoformat() }}">
    <p>Bis wann?</p>
    {% for o in optionen %}
    <label><input type="radio" name="ende" value="{{ o.ende.isoformat() }}"{% if loop.first %} checked{% endif %}>bis {{ o.ende|uhrzeit }} Uhr{% if o.preis is not none %} – {{ o.preis|euro }}{% endif %}</label>
    {% endfor %}
    <p class="hinweis">Bis {{ storno_frist }} Stunden vor Beginn kannst du kostenfrei stornieren, danach bleibt der Betrag fällig. Bezahlt wird direkt online.</p>
    <button>Verbindlich buchen</button>
  </form>
  {% else %}
  <p><a href="/anmelden">Melde dich an</a>, um diesen Termin zu buchen.</p>
  {% endif %}
</div>
{% endif %}
{% endblock %}
```

`portal/beachhub_portal/templates/anfrage.html`:
```html
{% extends "base.html" %}{% block title %}Anfrage{% endblock %}
{% block kopf %}{% if stand.zustand in ("wartet", "zahlung") %}
<noscript><meta http-equiv="refresh" content="3"></noscript>
<script src="/static/warten.js" defer></script>
{% endif %}{% endblock %}
{% block content %}
<div class="karte warten" id="warten" data-stand="{{ stand_url }}" data-zustand="{{ stand.zustand }}">
  {% if stand.zustand == "wartet" %}<h1>Einen Moment …</h1>
  {% elif stand.zustand == "zahlung" %}<h1>Zahlung</h1>
  {% elif stand.zustand == "abgelehnt" %}<h1>Das hat nicht geklappt</h1>
  {% else %}<h1>Fehler</h1>{% endif %}
  <p id="warten-text">{{ stand.text }}</p>
  {% if stand.zustand == "zahlung" and stand.zahlung_url %}<p><a class="knopf" href="{{ stand.zahlung_url }}">Zur Zahlung</a></p>{% endif %}
  {% if stand.zustand in ("abgelehnt", "fehler") %}<p><a href="/">Zurück zur Belegung</a></p>{% endif %}
</div>
{% endblock %}
```

`portal/beachhub_portal/static/warten.js`:
```js
// Warteseite: fragt alle 2 s den Stand der Anfrage ab und geht weiter, sobald sich etwas tut.
// Ohne JavaScript übernimmt das <meta http-equiv="refresh"> im <noscript>-Block.
(function () {
  var el = document.getElementById("warten");
  if (!el) return;
  var url = el.getAttribute("data-stand");
  var zustand = el.getAttribute("data-zustand");
  function frage() {
    fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (s) {
        if (!s) return;
        if (s.ziel) { window.location.assign(s.ziel); return; }
        if (s.zustand !== zustand) { window.location.reload(); return; }
        var text = document.getElementById("warten-text");
        if (text && s.text) text.textContent = s.text;
      })
      .catch(function () {})
      .finally(function () { setTimeout(frage, 2000); });
  }
  setTimeout(frage, 2000);
})();
```

`portal/beachhub_portal/main.py`: `buchen` zum Routen-Import ergänzen und `app.include_router(buchen.router, dependencies=csrf)`.

- [ ] **Step 5: Tests grün**

Run: `cd portal && ../.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 6: Lint, Typen, Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy
git add portal
git commit -m "feat(portal): Buchen mit Folgeslots und Warteseite"
```

---

## Task 13: portal – Zahlungsrückmeldung und Fake-Zahlungsseite

**Files:**
- Create: `portal/beachhub_portal/services/briefkasten.py`
- Create: `portal/beachhub_portal/routes/zahlung.py`
- Create: `portal/beachhub_portal/templates/test_zahlung.html`
- Modify: `portal/beachhub_portal/main.py`
- Test: `portal/tests/test_zahlung.py`

**Interfaces:**
- Consumes: `anfragen.stelle` (Task 9), `WebhookEingang` (Task 8)
- Produces: `briefkasten.MAX_BYTES = 65536`, `briefkasten.nimm_an(db, *, provider: str, rohdaten: str, signatur_header: str | None) -> Anfrage` (Anfrage `zahlung_eingegangen` ohne Konto + `WebhookEingang`, committet)
- Produces: `POST /zahlung/rueckmeldung/{provider}` (ohne Anmeldung, ohne CSRF; `provider` `[a-z]{2,20}` sonst 404; > 64 KB → 413; Antwort `{"ok": true}`), `GET /zahlung/zurueck?anfrage=<id>` → `303 /anfrage/<id>` (sonst `/buchungen`), `GET/POST /test-zahlung/{ref}` (nur mit `PORTAL_FAKE_ZAHLUNG=true`, sonst 404; POST legt dieselbe Rückmeldung an wie ein Anbieter und leitet nur auf den Pfad `/zahlung/zurueck` weiter)

- [ ] **Step 1: Failing Tests schreiben**

`portal/tests/test_zahlung.py`:
```python
import json
import uuid

import pytest
from beachhub_portal.config import settings
from beachhub_portal.models import Anfrage, WebhookEingang
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session


def test_briefkasten_speichert_ohne_pruefung(client: TestClient, db: Session) -> None:
    r = client.post(
        "/zahlung/rueckmeldung/stripe",
        content=b'{"id": "evt_1"}',
        headers={"Stripe-Signature": "t=1,v1=abc", "Content-Type": "application/json"},
    )
    assert r.status_code == 200 and r.json() == {"ok": True}
    e = db.scalar(select(WebhookEingang))
    assert e.rohdaten == '{"id": "evt_1"}' and e.signatur_header == "t=1,v1=abc"
    a = db.get(Anfrage, e.anfrage_id)
    assert a.typ == "zahlung_eingegangen" and a.konto_id is None
    assert a.nutzlast_json == {
        "provider": "stripe",
        "rohdaten": '{"id": "evt_1"}',
        "signatur_header": "t=1,v1=abc",
    }


def test_briefkasten_braucht_kein_csrf(angemeldet: TestClient) -> None:
    assert angemeldet.post("/zahlung/rueckmeldung/stripe", content=b"{}").status_code == 200


def test_briefkasten_zu_gross(client: TestClient, db: Session) -> None:
    r = client.post("/zahlung/rueckmeldung/stripe", content=b"x" * (64 * 1024 + 1))
    assert r.status_code == 413
    assert db.scalar(select(Anfrage)) is None


@pytest.mark.parametrize("provider", ["Stripe", "x", "a-b", "sehrsehrsehrlangername"])
def test_briefkasten_unbekannter_pfad(client: TestClient, provider: str) -> None:
    assert client.post(f"/zahlung/rueckmeldung/{provider}", content=b"{}").status_code == 404


def test_fake_seite_nur_wenn_eingeschaltet(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    params = {"betrag": "30.00", "zurueck": "/zahlung/zurueck?anfrage=1"}
    seite = client.get("/test-zahlung/fake_abc", params=params).text
    assert "Bezahlen" in seite and "30.00" in seite
    monkeypatch.setattr(settings, "fake_zahlung", False)
    assert client.get("/test-zahlung/fake_abc").status_code == 404
    assert client.post("/test-zahlung/fake_abc", data={"ergebnis": "bezahlt"}).status_code == 404


def test_fake_zahlung_legt_rueckmeldung_an(client: TestClient, db: Session) -> None:
    aid = uuid.uuid4()
    r = client.post(
        "/test-zahlung/fake_abc",
        data={
            "ergebnis": "bezahlt",
            "betrag": "30.00",
            "zurueck": f"https://portal.example:8443/zahlung/zurueck?anfrage={aid}",
        },
        follow_redirects=False,
    )
    assert r.headers["location"] == f"/zahlung/zurueck?anfrage={aid}"
    a = db.scalar(select(Anfrage))
    assert a.typ == "zahlung_eingegangen"
    assert json.loads(a.nutzlast_json["rohdaten"]) == {
        "ref": "fake_abc",
        "ergebnis": "bezahlt",
        "betrag": "30.00",
    }


def test_fake_zahlung_leitet_nie_nach_draussen(client: TestClient) -> None:
    r = client.post(
        "/test-zahlung/fake_abc",
        data={"ergebnis": "abgebrochen", "zurueck": "https://evil.example/anderswo"},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/buchungen"
    assert client.post("/test-zahlung/fake_abc", data={"ergebnis": "gestohlen"}).status_code == 400


def test_zurueck_fuehrt_zur_warteseite(client: TestClient) -> None:
    aid = uuid.uuid4()
    r = client.get(f"/zahlung/zurueck?anfrage={aid}", follow_redirects=False)
    assert r.headers["location"] == f"/anfrage/{aid}"
    r = client.get("/zahlung/zurueck?anfrage=quatsch", follow_redirects=False)
    assert r.headers["location"] == "/buchungen"
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd portal && ../.venv/bin/pytest -q tests/test_zahlung.py`
Expected: FAIL (404 auf `/zahlung/rueckmeldung/stripe`)

- [ ] **Step 3: Implementieren**

`portal/beachhub_portal/services/briefkasten.py`:
```python
"""Briefkasten für Rückmeldungen des Zahlungsanbieters (A-ZAHL-2).

Das Portal prüft nichts und kennt keine Geheimnisse des Anbieters. Es speichert die Rohdaten und
reicht sie als Anfrage `zahlung_eingegangen` weiter; den Eingang stellt das Hauptsystem fest.
"""

from sqlalchemy.orm import Session

from beachhub_portal import uhr
from beachhub_portal.models import Anfrage, WebhookEingang
from beachhub_portal.services import anfragen

MAX_BYTES = 64 * 1024


def nimm_an(
    db: Session, *, provider: str, rohdaten: str, signatur_header: str | None
) -> Anfrage:
    a = anfragen.stelle(
        db,
        typ="zahlung_eingegangen",
        konto_id=None,
        nutzlast={"provider": provider, "rohdaten": rohdaten, "signatur_header": signatur_header},
    )
    db.add(
        WebhookEingang(
            provider=provider,
            rohdaten=rohdaten,
            signatur_header=signatur_header,
            empfangen_am=uhr.jetzt(),
            anfrage_id=a.id,
        )
    )
    db.commit()
    return a
```

`portal/beachhub_portal/routes/zahlung.py`:
```python
import json
import re
import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from beachhub_portal import auth
from beachhub_portal.config import settings
from beachhub_portal.database import SessionLocal, get_db
from beachhub_portal.models import Konto
from beachhub_portal.services import briefkasten
from beachhub_portal.templating import render

# Ohne Anmeldung und ohne CSRF: Hier schreibt der Zahlungsanbieter hinein.
briefkasten_router = APIRouter()
# Seiten für den Kunden (mit CSRF-Prüfung).
router = APIRouter()

_PROVIDER = re.compile(r"[a-z]{2,20}")


def _signatur_header(request: Request) -> str | None:
    for name in settings.webhook_signatur_header.split(","):
        name = name.strip()
        if name and name in request.headers:
            return request.headers[name]
    return None


def _speichere(provider: str, rohdaten: str, kopf: str | None) -> None:
    with SessionLocal() as db:
        briefkasten.nimm_an(db, provider=provider, rohdaten=rohdaten, signatur_header=kopf)


@briefkasten_router.post("/zahlung/rueckmeldung/{provider}")
async def rueckmeldung(provider: str, request: Request) -> JSONResponse:
    if not _PROVIDER.fullmatch(provider):
        raise HTTPException(status_code=404)
    laenge = request.headers.get("content-length", "")
    if laenge.isdigit() and int(laenge) > briefkasten.MAX_BYTES:
        raise HTTPException(status_code=413, detail="Rückmeldung zu groß")
    roh = await request.body()
    if len(roh) > briefkasten.MAX_BYTES:
        raise HTTPException(status_code=413, detail="Rückmeldung zu groß")
    await run_in_threadpool(
        _speichere, provider, roh.decode("utf-8", errors="replace"), _signatur_header(request)
    )
    return JSONResponse({"ok": True})


def _sicheres_ziel(zurueck: str) -> str:
    """Nur auf die eigene Rückkehrseite weiterleiten, nie auf einen fremden Host."""
    teile = urlsplit(zurueck)
    if teile.path == "/zahlung/zurueck":
        return "/zahlung/zurueck" + (f"?{teile.query}" if teile.query else "")
    return "/buchungen"


@router.get("/zahlung/zurueck")
def zurueck(anfrage: str = "") -> RedirectResponse:
    try:
        aid = uuid.UUID(anfrage)
    except ValueError:
        return RedirectResponse("/buchungen", status_code=303)
    return RedirectResponse(f"/anfrage/{aid}", status_code=303)


def _nur_mit_fake() -> None:
    if not settings.fake_zahlung:
        raise HTTPException(status_code=404)


@router.get("/test-zahlung/{ref}", response_class=HTMLResponse)
def test_zahlung_seite(
    request: Request,
    ref: str,
    betrag: str = "",
    zurueck: str = "",
    konto: Konto | None = Depends(auth.konto_optional),
) -> HTMLResponse:
    _nur_mit_fake()
    return render(
        request,
        "test_zahlung.html",
        konto=konto,
        ref=ref,
        betrag=betrag,
        zurueck=_sicheres_ziel(zurueck),
    )


@router.post("/test-zahlung/{ref}")
def test_zahlung(
    ref: str,
    ergebnis: str = Form(...),
    betrag: str = Form(""),
    zurueck: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    _nur_mit_fake()
    if ergebnis not in ("bezahlt", "abgebrochen"):
        raise HTTPException(status_code=400, detail="Unbekanntes Ergebnis")
    rohdaten = json.dumps({"ref": ref, "ergebnis": ergebnis, "betrag": betrag})
    briefkasten.nimm_an(db, provider="fake", rohdaten=rohdaten, signatur_header=None)
    return RedirectResponse(_sicheres_ziel(zurueck), status_code=303)
```

`portal/beachhub_portal/templates/test_zahlung.html`:
```html
{% extends "base.html" %}{% block title %}Testzahlung{% endblock %}
{% block content %}
<h1>Testzahlung</h1>
<div class="karte">
  <div class="flash warn">Nur für die Entwicklung: Hier wird kein Geld bewegt.</div>
  <p>Zu zahlen: <strong>{{ betrag or "–" }} €</strong></p>
  <form method="post" action="/test-zahlung/{{ ref }}">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <input type="hidden" name="betrag" value="{{ betrag }}">
    <input type="hidden" name="zurueck" value="{{ zurueck }}">
    <button name="ergebnis" value="bezahlt">Bezahlen</button>
    <button name="ergebnis" value="abgebrochen" class="gefahr">Abbrechen</button>
  </form>
</div>
{% endblock %}
```

`portal/beachhub_portal/main.py`: `zahlung` zum Routen-Import ergänzen und
```python
app.include_router(zahlung.briefkasten_router)
app.include_router(zahlung.router, dependencies=csrf)
```

- [ ] **Step 4: Tests grün**

Run: `cd portal && ../.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 5: Lint, Typen, Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy
git add portal
git commit -m "feat(portal): Briefkasten für Zahlungsrückmeldungen und Fake-Zahlungsseite"
```

---
## Task 14: portal – Meine Buchungen und Storno

**Files:**
- Create: `portal/beachhub_portal/routes/buchungen.py`
- Create: `portal/beachhub_portal/templates/buchungen.html`, `templates/stornieren.html`
- Modify: `portal/beachhub_portal/main.py`
- Test: `portal/tests/test_buchungen.py`

**Interfaces:**
- Consumes: `lesestand.konto/belegung`, `anfragen.stelle`, `anfragen.MELDUNGEN` (Tasks 9, 12)
- Consumes: `KontoBuchung.checkout_url/reserviert_bis` (Tasks 1, 2)
- Produces: `GET /buchungen?meldung=<schlüssel>` (kommende = `reserviert`/`bestaetigt` mit `ende > jetzt`, aufsteigend; übrige absteigend; ohne `kunde_id` oder Lesestand: „wird gerade eingerichtet“; bei einer Reservierung mit `checkout_url` und `reserviert_bis > jetzt` der Link „Jetzt bezahlen (bis HH:MM)“, sonst „Zahlung ausstehend“), `GET /buchungen/{id}/stornieren` (404, wenn nicht eigene kommende Buchung mit `beginn > jetzt`), `POST /buchungen/{id}/stornieren` → Anfrage `buchung_stornieren` → `303 /anfrage/<id>`
- Kostenfrei-Anzeige wie im Hauptsystem: Reservierung immer; sonst `jetzt <= beginn - storno_frist_stunden`.

- [ ] **Step 1: Failing Tests schreiben**

`portal/tests/test_buchungen.py`:
```python
import uuid
from datetime import date, time

from beachhub_portal.models import Anfrage, Konto
from beachhub_shared.zeit import kombiniere
from fastapi.testclient import TestClient
from hilfen import KUNDE_ID, belegung, buchung, konto, speichere
from sqlalchemy import select
from sqlalchemy.orm import Session

MORGEN = date(2027, 11, 26)
UEBERMORGEN = date(2027, 11, 27)
FRUEHER = date(2027, 11, 20)


def _b(tag: date, stunde: int, **kw) -> dict:
    return buchung(kombiniere(tag, time(stunde)), kombiniere(tag, time(stunde + 1)), **kw)


def test_buchungen_ohne_kunde(angemeldet: TestClient, db: Session) -> None:
    db.get(Konto, angemeldet.konto_id).kunde_id = None
    db.commit()
    assert "wird gerade eingerichtet" in angemeldet.get("/buchungen").text


def test_liste_mit_pin_und_frueheren(angemeldet: TestClient, db: Session) -> None:
    speichere(
        db,
        f"konto:{KUNDE_ID}",
        konto(
            buchungen=[
                _b(MORGEN, 19, pin="654321"),
                _b(MORGEN, 21, status="reserviert"),
                _b(FRUEHER, 19),
                _b(MORGEN, 17, status="storniert", storno={"kostenfrei": True}),
            ]
        ),
    )
    seite = angemeldet.get("/buchungen").text
    kommend, frueher = seite.split("Frühere und stornierte")
    assert '<span class="pin">654321</span>' in kommend
    assert "Zahlung ausstehend" in kommend
    assert "20.11.2027" in frueher and "storniert (kostenfrei)" in frueher


def test_reservierung_mit_zahlungslink(angemeldet: TestClient, db: Session) -> None:
    frist = kombiniere(date(2027, 11, 25), time(10, 15))  # jetzt: 10:00 Uhr
    offen = _b(MORGEN, 19, status="reserviert", checkout_url="/test-zahlung/fake_x", reserviert_bis=frist)
    abgelaufen = _b(
        MORGEN,
        20,
        status="reserviert",
        checkout_url="/test-zahlung/fake_y",
        reserviert_bis=kombiniere(date(2027, 11, 25), time(9, 45)),
    )
    speichere(db, f"konto:{KUNDE_ID}", konto(buchungen=[offen, abgelaufen]))
    seite = angemeldet.get("/buchungen").text
    assert '<a class="knopf" href="/test-zahlung/fake_x">Jetzt bezahlen (bis 10:15)</a>' in seite
    assert "fake_y" not in seite
    assert "Zahlung ausstehend" in seite


def test_meldung(angemeldet: TestClient, db: Session) -> None:
    speichere(db, f"konto:{KUNDE_ID}", konto())
    assert "Deine Buchung ist bestätigt" in angemeldet.get("/buchungen?meldung=bestaetigt").text
    assert "gibtsnicht" not in angemeldet.get("/buchungen?meldung=gibtsnicht").text


def test_stornieren_kostenfrei_und_kostenpflichtig(angemeldet: TestClient, db: Session) -> None:
    speichere(db, "belegung", belegung())
    frueh = _b(UEBERMORGEN, 19)  # mehr als 24 h vorher
    spaet = _b(MORGEN, 9)  # 23 h vorher (jetzt: 25.11. 10:00)
    reserviert = _b(MORGEN, 10, status="reserviert")
    speichere(db, f"konto:{KUNDE_ID}", konto(buchungen=[frueh, spaet, reserviert]))
    assert "<strong>kostenfrei</strong>" in angemeldet.get(f"/buchungen/{frueh['id']}/stornieren").text
    assert "bleibt aber fällig" in angemeldet.get(f"/buchungen/{spaet['id']}/stornieren").text
    seite = angemeldet.get(f"/buchungen/{reserviert['id']}/stornieren").text
    assert "<strong>kostenfrei</strong>" in seite


def test_stornieren_legt_anfrage_an(angemeldet: TestClient, db: Session) -> None:
    b = _b(UEBERMORGEN, 19)
    speichere(db, f"konto:{KUNDE_ID}", konto(buchungen=[b]))
    r = angemeldet.post(
        f"/buchungen/{b['id']}/stornieren",
        data={"csrf_token": angemeldet.csrf},
        follow_redirects=False,
    )
    a = db.scalar(select(Anfrage))
    assert r.headers["location"] == f"/anfrage/{a.id}"
    assert a.typ == "buchung_stornieren" and a.nutzlast_json == {"buchung_id": b["id"]}


def test_stornieren_unbekannt_oder_vergangen(angemeldet: TestClient, db: Session) -> None:
    alt = _b(FRUEHER, 19)
    speichere(db, f"konto:{KUNDE_ID}", konto(buchungen=[alt]))
    assert angemeldet.get(f"/buchungen/{alt['id']}/stornieren").status_code == 404
    r = angemeldet.post(
        f"/buchungen/{uuid.uuid4()}/stornieren",
        data={"csrf_token": angemeldet.csrf},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/buchungen"
    assert db.scalar(select(Anfrage)) is None
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd portal && ../.venv/bin/pytest -q tests/test_buchungen.py`
Expected: FAIL (404 auf `/buchungen`)

- [ ] **Step 3: Implementieren**

`portal/beachhub_portal/routes/buchungen.py`:
```python
from datetime import datetime, timedelta

from beachhub_shared.lesestand import KontoBuchung
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from beachhub_portal import auth, uhr
from beachhub_portal.database import get_db
from beachhub_portal.models import Konto
from beachhub_portal.services import anfragen, lesestand
from beachhub_portal.templating import mit_flash, render

router = APIRouter()
AKTIV = ("reserviert", "bestaetigt")


def _kommend(b: KontoBuchung, jetzt: datetime) -> bool:
    return b.status in AKTIV and b.ende > jetzt


@router.get("/buchungen", response_class=HTMLResponse)
def buchungen_seite(
    request: Request,
    meldung: str = "",
    konto: Konto = Depends(auth.konto_pflicht),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    text = anfragen.MELDUNGEN.get(meldung)
    inhalt = lesestand.konto(db, konto.kunde_id)
    if inhalt is None:
        return render(request, "buchungen.html", konto=konto, einrichtung=True, meldung=text)
    jetzt = uhr.jetzt()
    kommende = sorted((b for b in inhalt.buchungen if _kommend(b, jetzt)), key=lambda b: b.beginn)
    fruehere = sorted(
        (b for b in inhalt.buchungen if not _kommend(b, jetzt)),
        key=lambda b: b.beginn,
        reverse=True,
    )
    return render(
        request,
        "buchungen.html",
        konto=konto,
        kommende=kommende,
        fruehere=fruehere,
        jetzt=jetzt,
        meldung=text,
    )


def _stornierbar(db: Session, konto: Konto, buchung_id: str) -> KontoBuchung | None:
    inhalt = lesestand.konto(db, konto.kunde_id)
    if inhalt is None:
        return None
    b = next((x for x in inhalt.buchungen if x.id == buchung_id), None)
    if b is None or b.status not in AKTIV or b.beginn <= uhr.jetzt():
        return None
    return b


@router.get("/buchungen/{buchung_id}/stornieren", response_class=HTMLResponse)
def stornieren_seite(
    request: Request,
    buchung_id: str,
    konto: Konto = Depends(auth.konto_pflicht),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    b = _stornierbar(db, konto, buchung_id)
    if b is None:
        raise HTTPException(status_code=404, detail="Diese Buchung kann nicht mehr storniert werden.")
    belegung = lesestand.belegung(db)
    frist = belegung.storno_frist_stunden if belegung else 24
    kostenfrei = b.status == "reserviert" or uhr.jetzt() <= b.beginn - timedelta(hours=frist)
    return render(
        request, "stornieren.html", konto=konto, b=b, kostenfrei=kostenfrei, frist=frist
    )


@router.post("/buchungen/{buchung_id}/stornieren")
def stornieren(
    buchung_id: str,
    konto: Konto = Depends(auth.konto_pflicht),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    b = _stornierbar(db, konto, buchung_id)
    if b is None:
        return mit_flash(
            RedirectResponse("/buchungen", status_code=303),
            "Diese Buchung kann nicht mehr storniert werden.",
            "fehler",
        )
    a = anfragen.stelle(
        db, typ="buchung_stornieren", konto_id=konto.id, nutzlast={"buchung_id": b.id}
    )
    return RedirectResponse(f"/anfrage/{a.id}", status_code=303)
```

`portal/beachhub_portal/templates/buchungen.html`:
```html
{% extends "base.html" %}{% block title %}Meine Buchungen{% endblock %}
{% block content %}
<h1>Meine Buchungen</h1>
{% if meldung %}<div class="flash ok">{{ meldung }}</div>{% endif %}
{% if einrichtung %}
<div class="karte"><p>Dein Konto wird gerade eingerichtet. Deine Buchungen erscheinen hier in wenigen Augenblicken.</p></div>
{% else %}
<section class="karte">
  <h2>Kommende</h2>
  {% if kommende %}
  <ul class="liste">
    {% for b in kommende %}
    <li>
      <strong>{{ b.feld_name }}</strong> · {{ b.beginn|lokal }} bis {{ b.ende|uhrzeit }} Uhr · {{ b.preis|euro }}<br>
      {% if b.status == "bestaetigt" %}
      Zahlencode für die Tür: <span class="pin">{{ b.pin or "–" }}</span>
      {% elif b.checkout_url and b.reserviert_bis and b.reserviert_bis > jetzt %}
      <a class="knopf" href="{{ b.checkout_url }}">Jetzt bezahlen (bis {{ b.reserviert_bis|uhrzeit }})</a>
      {% else %}
      <span class="badge warn">Zahlung ausstehend</span>
      {% endif %}
      {% if b.beginn > jetzt %}<br><a href="/buchungen/{{ b.id }}/stornieren">Stornieren</a>{% endif %}
    </li>
    {% endfor %}
  </ul>
  {% else %}
  <p class="hinweis">Keine kommenden Buchungen. <a href="/">Jetzt buchen</a></p>
  {% endif %}
</section>
{% if fruehere %}
<section class="karte">
  <h2>Frühere und stornierte</h2>
  <ul class="liste">
    {% for b in fruehere %}
    <li>{{ b.feld_name }} · {{ b.beginn|lokal }} ·
      {% if b.status == "storniert" %}storniert{% if b.storno and b.storno.kostenfrei %} (kostenfrei){% elif b.storno %} (Betrag bleibt fällig){% endif %}
      {% elif b.status == "verfallen" %}verfallen (nicht bezahlt)
      {% elif b.status == "abgelehnt" %}abgelehnt
      {% else %}{{ b.preis|euro }}{% endif %}
    </li>
    {% endfor %}
  </ul>
</section>
{% endif %}
{% endif %}
{% endblock %}
```

`portal/beachhub_portal/templates/stornieren.html`:
```html
{% extends "base.html" %}{% block title %}Stornieren{% endblock %}
{% block content %}
<h1>Buchung stornieren</h1>
<div class="karte">
  <p><strong>{{ b.feld_name }}</strong> · {{ b.beginn|lokal }} bis {{ b.ende|uhrzeit }} Uhr · {{ b.preis|euro }}</p>
  {% if kostenfrei %}
  <p>Die Stornierung ist <strong>kostenfrei</strong>.{% if b.status == "bestaetigt" %} Der Betrag wird deinem Guthaben gutgeschrieben und mit der nächsten Buchung verrechnet.{% endif %}</p>
  {% else %}
  <p>Die Stornofrist von {{ frist }} Stunden ist abgelaufen. Der Termin wird freigegeben, <strong>der Betrag bleibt aber fällig</strong>.</p>
  {% endif %}
  <form method="post" action="/buchungen/{{ b.id }}/stornieren">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <button class="gefahr">Verbindlich stornieren</button>
  </form>
  <p><a href="/buchungen">Abbrechen</a></p>
</div>
{% endblock %}
```

`portal/beachhub_portal/main.py`: `buchungen` zum Routen-Import ergänzen und `app.include_router(buchungen.router, dependencies=csrf)`.

- [ ] **Step 4: Tests grün**

Run: `cd portal && ../.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 5: Lint, Typen, Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy
git add portal
git commit -m "feat(portal): Meine Buchungen mit PIN und Storno"
```

---

## Task 15: portal – Rechnungen mit Einmal-Link und Aufräum-Job

**Files:**
- Create: `portal/beachhub_portal/services/rechnung_link.py`
- Modify: `portal/beachhub_portal/services/anfragen.py` (`beantworte`: PDF → Einmal-Link)
- Create: `portal/beachhub_portal/routes/rechnungen.py`, `portal/beachhub_portal/templates/rechnungen.html`
- Create: `portal/beachhub_portal/jobs.py`
- Modify: `portal/beachhub_portal/main.py` (Router, Scheduler im lifespan)
- Test: `portal/tests/test_rechnungen.py`

**Interfaces:**
- Consumes: `anfragen.stelle/beantworte/stand` (Tasks 9, 12), `lesestand.konto`
- Produces: `rechnung_link.DAUER = timedelta(minutes=10)`, `rechnung_link.lege_an(db, *, konto_id, rechnung_nr, pdf: bytes, jetzt) -> str` (Datei `DATA_DIR/rechnungen_tmp/<uuid>.pdf` mit `0600`, Link-Zeile; committet nicht; liefert das Klartext-Token), `rechnung_link.einloesen(db, *, token, konto_id, jetzt) -> tuple[rechnung_nr, bytes] | None` (nur eigenes Konto, nicht abgelaufen; löscht Datei und Link, committet)
- Produces: `anfragen.beantworte` legt bei `rechnung_anfordern`/`ok` den Einmal-Link an, entfernt `pdf_base64` aus der gespeicherten Antwort und merkt sich `link_token` (daraus bildet `stand()` das Ziel `/rechnung/<token>`)
- Produces: Routen `GET /rechnungen`, `POST /rechnungen/{nummer}/anfordern` (nur Nummern aus dem eigenen Lesestand), `GET /rechnung/{token}` (PDF, `Content-Disposition: attachment`, `Cache-Control: no-store`; sonst 404 mit Hinweis)
- Produces: `jobs.aufraeumen(db, jetzt) -> dict[str, int]`, `jobs.starte_scheduler()`, `jobs.stoppe_scheduler()` (alle 5 min)

- [ ] **Step 1: Failing Tests schreiben**

`portal/tests/test_rechnungen.py`:
```python
import base64
import os
import uuid
from datetime import timedelta

from beachhub_portal import jobs, uhr
from beachhub_portal.config import settings
from beachhub_portal.models import Anfrage, Konto, Lesestand
from beachhub_portal.services import anfragen, rechnung_link
from beachhub_shared import kanal
from fastapi.testclient import TestClient
from hilfen import KUNDE_ID, konto, speichere
from sqlalchemy import select
from sqlalchemy.orm import Session

RECHNUNG = {"nummer": "2027-00001", "datum": "2027-11-20", "brutto": "30.00", "status": "bezahlt"}


def _tmp_pdfs() -> list:
    ordner = settings.data_dir / "rechnungen_tmp"
    return list(ordner.glob("*.pdf")) if ordner.exists() else []


def _bereitstellen(db: Session, konto_id: uuid.UUID) -> Anfrage:
    speichere(db, f"konto:{KUNDE_ID}", konto(rechnungen=[RECHNUNG]))
    a = anfragen.stelle(
        db, typ="rechnung_anfordern", konto_id=konto_id, nutzlast={"rechnung_nr": "2027-00001"}
    )
    antwort = kanal.Antwort(
        status="ok",
        pdf_base64=base64.b64encode(b"%PDF-1.7 test").decode(),
        dateiname="Rechnung-2027-00001.pdf",
    )
    anfragen.beantworte(db, a.id, antwort, uhr.jetzt())
    db.commit()
    return a


def test_liste(angemeldet: TestClient, db: Session) -> None:
    speichere(db, f"konto:{KUNDE_ID}", konto(rechnungen=[RECHNUNG]))
    seite = angemeldet.get("/rechnungen").text
    assert "2027-00001" in seite and "20.11.2027" in seite and "30,00 €" in seite


def test_anfordern_nur_eigene_nummer(angemeldet: TestClient, db: Session) -> None:
    speichere(db, f"konto:{KUNDE_ID}", konto(rechnungen=[RECHNUNG]))
    r = angemeldet.post(
        "/rechnungen/2027-00001/anfordern",
        data={"csrf_token": angemeldet.csrf},
        follow_redirects=False,
    )
    a = db.scalar(select(Anfrage))
    assert r.headers["location"] == f"/anfrage/{a.id}"
    assert a.typ == "rechnung_anfordern" and a.nutzlast_json == {"rechnung_nr": "2027-00001"}
    r = angemeldet.post(
        "/rechnungen/2027-99999/anfordern",
        data={"csrf_token": angemeldet.csrf},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/rechnungen"


def test_antwort_wird_einmal_link(angemeldet: TestClient, db: Session) -> None:
    a = _bereitstellen(db, angemeldet.konto_id)
    assert "pdf_base64" not in a.antwort_json and a.antwort_json["link_token"]
    ziel = angemeldet.get(f"/anfrage/{a.id}", follow_redirects=False).headers["location"]
    assert ziel.startswith("/rechnung/")
    r = angemeldet.get(ziel)
    assert r.content == b"%PDF-1.7 test"
    assert r.headers["content-type"] == "application/pdf"
    assert 'filename="Rechnung-2027-00001.pdf"' in r.headers["content-disposition"]
    assert r.headers["cache-control"] == "no-store"
    zweiter = angemeldet.get(ziel)
    assert zweiter.status_code == 404 and "abgelaufen oder wurde schon benutzt" in zweiter.text
    assert _tmp_pdfs() == []


def test_link_nur_fuer_eigenes_konto(angemeldet: TestClient, db: Session) -> None:
    fremd = Konto(email="b@x.de", anzeigename="B")
    db.add(fremd)
    db.commit()
    token = rechnung_link.lege_an(
        db, konto_id=fremd.id, rechnung_nr="2027-00002", pdf=b"%PDF", jetzt=uhr.jetzt()
    )
    db.commit()
    assert angemeldet.get(f"/rechnung/{token}").status_code == 404
    assert len(_tmp_pdfs()) == 1


def test_link_laeuft_nach_10_minuten_ab(angemeldet: TestClient, db: Session, uhr_steht) -> None:
    a = _bereitstellen(db, angemeldet.konto_id)
    ziel = angemeldet.get(f"/anfrage/{a.id}", follow_redirects=False).headers["location"]
    uhr_steht.weiter(minutes=11)
    assert angemeldet.get(ziel).status_code == 404


def test_aufraeumen(db: Session, uhr_steht) -> None:
    jetzt = uhr_steht.jetzt
    k = Konto(email="a@x.de", anzeigename="A", kunde_id=KUNDE_ID)
    db.add(k)
    db.commit()
    rechnung_link.lege_an(db, konto_id=k.id, rechnung_nr="1", pdf=b"x", jetzt=jetzt - timedelta(minutes=11))
    rechnung_link.lege_an(db, konto_id=k.id, rechnung_nr="2", pdf=b"y", jetzt=jetzt)
    db.commit()
    waise = settings.data_dir / "rechnungen_tmp" / "waise.pdf"
    waise.write_bytes(b"z")
    os.utime(waise, (0, 0))
    db.add(
        Anfrage(
            typ="konto_loeschen",
            nutzlast_json={},
            erstellt_am=jetzt - timedelta(days=40),
            status="beantwortet",
            beantwortet_am=jetzt - timedelta(days=31),
        )
    )
    db.add(Anfrage(typ="konto_loeschen", nutzlast_json={}, erstellt_am=jetzt, status="offen"))
    db.commit()
    speichere(db, f"konto:{KUNDE_ID}", konto())
    speichere(db, f"konto:{uuid.uuid4()}", konto())  # Konto gibt es im Portal nicht (mehr)
    n = jobs.aufraeumen(db, jetzt)
    assert n["rechnung_links"] == 1 and n["waisen"] == 1
    assert n["anfragen"] == 1 and n["lesestand"] == 1
    assert len(_tmp_pdfs()) == 1
    assert db.get(Lesestand, f"konto:{KUNDE_ID}") is not None
    assert len(db.scalars(select(Anfrage)).all()) == 1
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd portal && ../.venv/bin/pytest -q tests/test_rechnungen.py`
Expected: FAIL mit `ImportError: cannot import name 'jobs'`

- [ ] **Step 3: Einmal-Link und Antwortverarbeitung**

`portal/beachhub_portal/services/rechnung_link.py`:
```python
"""Einmal-Links für Rechnungs-PDFs (A-RECH-5): Das Portal speichert keine Rechnungen, nur die
angeforderte Kopie für höchstens zehn Minuten; nach dem Abruf ist sie weg."""

import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_portal.config import settings
from beachhub_portal.models import RechnungLink

DAUER = timedelta(minutes=10)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def lege_an(
    db: Session, *, konto_id: uuid.UUID, rechnung_nr: str, pdf: bytes, jetzt: datetime
) -> str:
    ordner = settings.data_dir / "rechnungen_tmp"
    ordner.mkdir(parents=True, exist_ok=True)
    pfad = ordner / f"{uuid.uuid4()}.pdf"
    fd = os.open(pfad, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pdf)
    token = secrets.token_urlsafe(32)
    db.add(
        RechnungLink(
            konto_id=konto_id,
            rechnung_nr=rechnung_nr,
            token_hash=_hash(token),
            pdf_pfad=str(pfad),
            laeuft_ab=jetzt + DAUER,
        )
    )
    return token


def einloesen(
    db: Session, *, token: str, konto_id: uuid.UUID, jetzt: datetime
) -> tuple[str, bytes] | None:
    link = db.scalar(
        select(RechnungLink).where(RechnungLink.token_hash == _hash(token)).with_for_update()
    )
    if link is None or link.konto_id != konto_id or link.laeuft_ab <= jetzt:
        db.rollback()
        return None
    pfad = link.pdf_pfad
    nummer = link.rechnung_nr
    try:
        with open(pfad, "rb") as f:
            daten = f.read()
    except OSError:
        daten = None
    if os.path.exists(pfad):
        os.unlink(pfad)
    db.delete(link)
    db.commit()
    return (nummer, daten) if daten is not None else None
```

`portal/beachhub_portal/services/anfragen.py` – Importe ergänzen (`import base64`, `import binascii`, `from beachhub_portal.services import lesestand, rechnung_link, wecker`) und in `beantworte` vor `a.status = Anfrage.BEANTWORTET` einfügen:
```python
    if a.typ == "rechnung_anfordern" and antwort.status == "ok":
        # Das PDF kommt nicht in die Anfragetabelle, sondern als Datei hinter einen Einmal-Link.
        daten.pop("pdf_base64", None)
        daten.pop("dateiname", None)
        if antwort.pdf_base64 and a.konto_id is not None and db.get(Konto, a.konto_id):
            try:
                pdf = base64.b64decode(antwort.pdf_base64, validate=True)
            except (binascii.Error, ValueError):
                daten = {"status": "fehler"}
            else:
                daten["link_token"] = rechnung_link.lege_an(
                    db,
                    konto_id=a.konto_id,
                    rechnung_nr=str(a.nutzlast_json.get("rechnung_nr", "")),
                    pdf=pdf,
                    jetzt=jetzt,
                )
```

- [ ] **Step 4: Routen, Template, Aufräum-Job**

`portal/beachhub_portal/routes/rechnungen.py`:
```python
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from beachhub_portal import auth, uhr
from beachhub_portal.database import get_db
from beachhub_portal.models import Konto
from beachhub_portal.services import anfragen, lesestand, rechnung_link
from beachhub_portal.templating import mit_flash, render

router = APIRouter()


@router.get("/rechnungen", response_class=HTMLResponse)
def rechnungen_seite(
    request: Request, konto: Konto = Depends(auth.konto_pflicht), db: Session = Depends(get_db)
) -> HTMLResponse:
    inhalt = lesestand.konto(db, konto.kunde_id)
    return render(
        request, "rechnungen.html", konto=konto, rechnungen=inhalt.rechnungen if inhalt else None
    )


@router.post("/rechnungen/{nummer}/anfordern")
def anfordern(
    nummer: str, konto: Konto = Depends(auth.konto_pflicht), db: Session = Depends(get_db)
) -> RedirectResponse:
    inhalt = lesestand.konto(db, konto.kunde_id)
    if inhalt is None or nummer not in {r.nummer for r in inhalt.rechnungen}:
        return mit_flash(
            RedirectResponse("/rechnungen", status_code=303), "Diese Rechnung gibt es nicht.", "fehler"
        )
    a = anfragen.stelle(
        db, typ="rechnung_anfordern", konto_id=konto.id, nutzlast={"rechnung_nr": nummer}
    )
    return RedirectResponse(f"/anfrage/{a.id}", status_code=303)


@router.get("/rechnung/{token}")
def herunterladen(
    token: str, konto: Konto = Depends(auth.konto_pflicht), db: Session = Depends(get_db)
) -> Response:
    ergebnis = rechnung_link.einloesen(db, token=token, konto_id=konto.id, jetzt=uhr.jetzt())
    if ergebnis is None:
        raise HTTPException(
            status_code=404,
            detail="Der Link ist abgelaufen oder wurde schon benutzt. "
            "Bitte fordere die Rechnung noch einmal an.",
        )
    nummer, daten = ergebnis
    name = re.sub(r"[^0-9A-Za-z-]", "", nummer) or "Rechnung"
    return Response(
        daten,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="Rechnung-{name}.pdf"',
            "Cache-Control": "no-store",
        },
    )
```

`portal/beachhub_portal/templates/rechnungen.html`:
```html
{% extends "base.html" %}{% block title %}Rechnungen{% endblock %}
{% block content %}
<h1>Rechnungen</h1>
{% if rechnungen is none %}
<div class="karte"><p>Dein Konto wird gerade eingerichtet.</p></div>
{% elif not rechnungen %}
<div class="karte"><p class="hinweis">Noch keine Rechnungen.</p></div>
{% else %}
<div class="karte">
  <ul class="liste">
    {% for r in rechnungen %}
    <li>
      <strong>{{ r.nummer }}</strong> vom {{ r.datum|datum }} · {{ r.brutto|euro }}
      <span class="badge{% if r.status == 'bezahlt' %} ok{% endif %}">{{ r.status }}</span>
      <form method="post" action="/rechnungen/{{ r.nummer }}/anfordern" class="inline">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <button class="leise">PDF herunterladen</button>
      </form>
    </li>
    {% endfor %}
  </ul>
</div>
{% endif %}
{% endblock %}
```

`portal/beachhub_portal/jobs.py`:
```python
"""Aufräumen im Portal, alle 5 Minuten: abgelaufene Einmal-Links samt Dateien, verwaiste
PDF-Dateien, Login-Codes und Sessions, alte beantwortete Anfragen und Briefkasteneinträge,
Lesestände von Konten, die es im Portal nicht mehr gibt."""

import logging
from datetime import datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from beachhub_portal import uhr
from beachhub_portal.config import settings
from beachhub_portal.database import SessionLocal
from beachhub_portal.models import (
    Anfrage,
    Konto,
    Lesestand,
    LoginToken,
    RechnungLink,
    Sitzung,
    WebhookEingang,
)

logger = logging.getLogger(__name__)
AUFBEWAHRUNG = timedelta(days=30)
_scheduler: BackgroundScheduler | None = None


def aufraeumen(db: Session, jetzt: datetime) -> dict[str, int]:
    n: dict[str, int] = {}
    abgelaufen = db.scalars(select(RechnungLink).where(RechnungLink.laeuft_ab <= jetzt)).all()
    for link in abgelaufen:
        Path(link.pdf_pfad).unlink(missing_ok=True)
        db.delete(link)
    n["rechnung_links"] = len(abgelaufen)

    # Dateien ohne Link, etwa nach einem Absturz zwischen Schreiben und Commit.
    bekannt = set(db.scalars(select(RechnungLink.pdf_pfad).where(RechnungLink.laeuft_ab > jetzt)))
    ordner = settings.data_dir / "rechnungen_tmp"
    grenze = (jetzt - timedelta(hours=1)).timestamp()
    waisen = 0
    if ordner.exists():
        for pfad in ordner.glob("*.pdf"):
            if str(pfad) not in bekannt and pfad.stat().st_mtime < grenze:
                pfad.unlink(missing_ok=True)
                waisen += 1
    n["waisen"] = waisen

    n["login_token"] = db.execute(delete(LoginToken).where(LoginToken.laeuft_ab <= jetzt)).rowcount
    n["sitzungen"] = db.execute(delete(Sitzung).where(Sitzung.laeuft_ab <= jetzt)).rowcount
    n["anfragen"] = db.execute(
        delete(Anfrage).where(
            Anfrage.status == Anfrage.BEANTWORTET, Anfrage.beantwortet_am < jetzt - AUFBEWAHRUNG
        )
    ).rowcount
    n["webhooks"] = db.execute(
        delete(WebhookEingang).where(WebhookEingang.empfangen_am < jetzt - AUFBEWAHRUNG)
    ).rowcount

    # konto:-Dokumente können vor dem Konto ankommen; erst nach einem Tag gelten sie als verwaist.
    kunden = {
        f"konto:{k}" for k in db.scalars(select(Konto.kunde_id).where(Konto.kunde_id.is_not(None)))
    }
    verwaist = [
        z
        for z in db.scalars(
            select(Lesestand).where(
                Lesestand.dokument.like("konto:%"),
                Lesestand.empfangen_am < jetzt - timedelta(days=1),
            )
        )
        if z.dokument not in kunden
    ]
    for z in verwaist:
        db.delete(z)
    n["lesestand"] = len(verwaist)
    db.commit()
    return n


def _job_aufraeumen() -> None:
    with SessionLocal() as db:
        try:
            aufraeumen(db, uhr.jetzt())
        except Exception:
            logger.exception("Aufräumen fehlgeschlagen")


def starte_scheduler() -> BackgroundScheduler:
    global _scheduler
    s = BackgroundScheduler(timezone="Europe/Berlin")
    s.add_job(_job_aufraeumen, IntervalTrigger(minutes=5), id="aufraeumen", replace_existing=True)
    s.start()
    _scheduler = s
    return s


def stoppe_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
```

`portal/beachhub_portal/main.py`:
- `lifespan` ersetzen durch:
```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if settings.enable_scheduler:
        from beachhub_portal import jobs

        jobs.starte_scheduler()
    yield
    if settings.enable_scheduler:
        from beachhub_portal import jobs

        jobs.stoppe_scheduler()
```
- `rechnungen` zum Routen-Import ergänzen und `app.include_router(rechnungen.router, dependencies=csrf)`.

- [ ] **Step 5: Tests grün**

Run: `cd portal && ../.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 6: Lint, Typen, Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy
git add portal
git commit -m "feat(portal): Rechnungen per Einmal-Link und Aufräum-Job"
```

---
## Task 16: Ende-zu-Ende – Portal und Hauptsystem im selben Prozess

**Files:**
- Create: `e2e/conftest.py`, `e2e/test_ablauf.py`
- Modify: `ruff.toml` (Testausnahmen für `e2e/`)

**Interfaces:**
- Consumes: alles aus Tasks 1–15. Das Hauptsystem spricht das Portal über `kanal.Kanal(client, warten=0)` an; `client` ist ein `fastapi.testclient.TestClient` auf die Portal-App (ein `httpx.Client`, geprüft mit Starlette 0.41.3/httpx 0.28.1) mit Kanal-Token im Header. Der „Browser“ ist ein zweiter `TestClient` auf dieselbe App mit eigenen Cookies. Threads laufen keine; `hauptsystem.abholen()` und `hauptsystem.verteilen()` werden im Test gezielt aufgerufen.
- Produces: Vertragstest über `shared/kanal.py` und `shared/lesestand.py` mit echten Signaturen, beide Datenbanken, echtem PDF.

- [ ] **Step 1: Test schreiben**

`e2e/conftest.py`:
```python
"""Portal und Hauptsystem im selben Prozess, jedes mit eigener Test-Datenbank.

Beide lesen ihre Einstellungen beim Import. Das Portal nutzt das Präfix PORTAL_, deshalb
stören sich die Variablen nicht. Der Signaturschlüssel des Hauptsystems entsteht vor dem Import,
damit das Portal den passenden öffentlichen Schlüssel bekommt.
"""

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

from beachhub_shared.signatur import erzeuge_schluesselpaar

KANAL_TOKEN = "e2e-kanal-token"
_tmp = Path(tempfile.mkdtemp(prefix="beachhub-e2e-"))
(_tmp / "core").mkdir()
_privat, _oeffentlich = erzeuge_schluesselpaar()
(_tmp / "core" / "signatur.key").write_text(_privat + "\n", encoding="utf-8")

os.environ.update(
    {
        "DATABASE_URL": os.environ.get(
            "TEST_DATABASE_URL", "postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub_test"
        ),
        "DATA_DIR": str(_tmp / "core"),
        "SIGNATUR_PRIVATSCHLUESSEL_PFAD": str(_tmp / "core" / "signatur.key"),
        "SECRET_KEY": "e2e-secret-key-0123456789abcdef",
        "PIN_SCHLUESSEL": "ZTJlLXBpbi1zY2hsdWVzc2VsLTMyLWJ5dGVzLSEhIQ==",
        "APP_ENV": "dev",
        "SMTP_HOST": "",
        "ENABLE_SCHEDULER": "false",
        "ENABLE_KANAL": "false",
        "PORTAL_URL": "",
        "ZAHLUNG_PROVIDER": "fake",
        "PORTAL_DATABASE_URL": os.environ.get(
            "TEST_PORTAL_DATABASE_URL",
            "postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub_portal_test",
        ),
        "PORTAL_DATA_DIR": str(_tmp / "portal"),
        "PORTAL_SECRET_KEY": "e2e-portal-secret-0123456789abcdef",
        "PORTAL_APP_ENV": "dev",
        "PORTAL_COOKIE_SECURE": "false",
        "PORTAL_SMTP_HOST": "",
        "PORTAL_ENABLE_SCHEDULER": "false",
        "PORTAL_KANAL_TOKEN": KANAL_TOKEN,
        "PORTAL_CORE_PUBLIC_KEY": _oeffentlich,
        "PORTAL_FAKE_ZAHLUNG": "true",
        "PORTAL_BASE_URL": "http://testserver",
    }
)

import pytest  # noqa: E402
from beachhub_core import mail as core_mail  # noqa: E402
from beachhub_core.database import engine as core_engine  # noqa: E402
from beachhub_core.database import stelle_extensions_sicher  # noqa: E402
from beachhub_core.kanal import Kanal  # noqa: E402
from beachhub_core.models import Base as CoreBase  # noqa: E402
from beachhub_portal import auth as portal_auth  # noqa: E402
from beachhub_portal import mail as portal_mail  # noqa: E402
from beachhub_portal.database import engine as portal_engine  # noqa: E402
from beachhub_portal.database import stelle_schema_sicher  # noqa: E402
from beachhub_portal.main import app as portal_app  # noqa: E402
from beachhub_portal.models import Base as PortalBase  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402


@pytest.fixture(autouse=True)
def frische_datenbanken() -> Iterator[None]:
    for ordner in ("core/lesestand", "core/rechnungen", "portal/rechnungen_tmp"):
        shutil.rmtree(_tmp / ordner, ignore_errors=True)
    stelle_extensions_sicher(core_engine)
    with core_engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    CoreBase.metadata.drop_all(bind=core_engine)
    CoreBase.metadata.create_all(bind=core_engine)
    stelle_schema_sicher(portal_engine)
    with portal_engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS spiegel.alembic_version"))
    PortalBase.metadata.drop_all(bind=portal_engine)
    PortalBase.metadata.create_all(bind=portal_engine)
    yield


@pytest.fixture(autouse=True)
def mails(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    postfach: dict[str, list] = {"core": [], "portal": []}
    monkeypatch.setattr(core_mail, "TEST_AUSGANG", postfach["core"])
    monkeypatch.setattr(portal_mail, "TEST_AUSGANG", postfach["portal"])
    portal_auth.reset_rate_limits()
    return postfach


@pytest.fixture
def browser() -> TestClient:
    return TestClient(portal_app)


@pytest.fixture
def hauptsystem() -> Kanal:
    client = TestClient(portal_app, headers={"Authorization": f"Bearer {KANAL_TOKEN}"})
    return Kanal(client, warten=0)
```

`e2e/test_ablauf.py`:
```python
"""Der ganze Weg eines Kunden: anmelden, buchen, bezahlen, PIN sehen, Rechnung laden, stornieren."""

import re
import uuid
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from beachhub_core.database import SessionLocal as CoreSession
from beachhub_core.kanal import Kanal
from beachhub_core.models import (
    Betriebszeit,
    Buchung,
    Feld,
    FeldRaster,
    Kunde,
    Kundengruppe,
    Rechnung,
    Tarif,
    Zahlung,
)
from beachhub_core.services import lesestand, pin
from beachhub_portal import uhr as portal_uhr
from beachhub_shared.zeit import kombiniere, lokales_datum
from fastapi.testclient import TestClient
from sqlalchemy import select

EMAIL = "anna@example.org"


@pytest.fixture
def feld_id(hauptsystem: Kanal) -> uuid.UUID:
    """Ein Feld mit Stundenraster, 9–23 Uhr, 30 € – im Hauptsystem angelegt und verteilt."""
    with CoreSession() as db:
        f = Feld(name="Feld 1", reihenfolge=1)
        f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
        db.add_all([f, Kundengruppe(name="Privat"), Tarif(name="Std", preis=Decimal("30.00"))])
        for wt in range(7):
            db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
        db.commit()
        lesestand.markiere_geaendert(db, "belegung", "tarife")
        db.commit()
        ergebnis = f.id
    assert hauptsystem.verteilen() == 2
    return ergebnis


def _csrf(html: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def _anmelden(browser: TestClient, mails: dict, hauptsystem: Kanal) -> str:
    browser.post("/anmelden", data={"email": EMAIL})
    code = re.search(r"Anmeldeseite ein: (\d{6})", mails["portal"][-1]["text"]).group(1)
    browser.post("/anmelden/code", data={"email": EMAIL, "code": code})
    csrf = _csrf(browser.get("/willkommen").text)
    browser.post("/willkommen", data={"anzeigename": "Anna", "csrf_token": csrf})
    assert hauptsystem.abholen() == 1
    return csrf


def _termin() -> tuple[datetime, datetime]:
    tag = lokales_datum(datetime.now(UTC)) + timedelta(days=2)
    return kombiniere(tag, time(19)), kombiniere(tag, time(20))


def _buchen(browser: TestClient, csrf: str, feld_id: uuid.UUID) -> str:
    beginn, ende = _termin()
    r = browser.post(
        "/buchen",
        data={"feld": str(feld_id), "beginn": beginn.isoformat(), "ende": ende.isoformat(), "csrf_token": csrf},
        follow_redirects=False,
    )
    assert r.status_code == 303
    return r.headers["location"]


def test_buchen_bezahlen_rechnung_stornieren(
    browser: TestClient, hauptsystem: Kanal, feld_id: uuid.UUID, mails: dict
) -> None:
    csrf = _anmelden(browser, mails, hauptsystem)
    with CoreSession() as db:
        kunde = db.scalar(select(Kunde))
        assert kunde.name == "Anna" and kunde.email == EMAIL and kunde.portal_konto_id
    beginn, _ = _termin()
    assert "30,00 €" in browser.get("/", params={"tag": lokales_datum(beginn).isoformat()}).text

    # Buchen: Hauptsystem reserviert und schickt zur (Fake-)Zahlung
    warteseite = _buchen(browser, csrf, feld_id)
    assert hauptsystem.abholen() == 1
    zahlseite = browser.get(warteseite, follow_redirects=False).headers["location"]
    assert zahlseite.startswith("/test-zahlung/fake_")
    teile = urlsplit(zahlseite)
    query = parse_qs(teile.query)
    assert query["betrag"] == ["30.00"]
    assert "Bezahlen" in browser.get(zahlseite).text
    r = browser.post(
        teile.path,
        data={"ergebnis": "bezahlt", "betrag": "30.00", "zurueck": query["zurueck"][0], "csrf_token": csrf},
        follow_redirects=False,
    )
    anfrage_url = browser.get(r.headers["location"], follow_redirects=False).headers["location"]
    assert "Zur Zahlung" in browser.get(anfrage_url).text  # Hauptsystem hat noch nicht geprüft
    assert hauptsystem.abholen() == 1

    # Bestätigt: PIN im Portal ist die PIN des Hauptsystems
    r = browser.get(anfrage_url, follow_redirects=False)
    assert r.headers["location"] == "/buchungen?meldung=bestaetigt"
    pin_im_portal = re.search(r'class="pin">(\d{6})<', browser.get("/buchungen").text).group(1)
    with CoreSession() as db:
        b = db.scalar(select(Buchung))
        assert b.status == "bestaetigt"
        assert pin.entschluessele(b.pin_verschluesselt) == pin_im_portal
        rechnung_nr = db.scalar(select(Rechnung)).nummer
        buchung_id = b.id
    assert any(m["betreff"].startswith("Buchung bestätigt") for m in mails["core"])

    # Rechnung über den Einmal-Link
    r = browser.post(
        f"/rechnungen/{rechnung_nr}/anfordern", data={"csrf_token": csrf}, follow_redirects=False
    )
    anfrage_url = r.headers["location"]
    assert hauptsystem.abholen() == 1
    link = browser.get(anfrage_url, follow_redirects=False).headers["location"]
    assert browser.get(link).content.startswith(b"%PDF")
    assert browser.get(link).status_code == 404

    # Storno mehr als 24 h vorher: kostenfrei, der Betrag wird Guthaben
    r = browser.post(
        f"/buchungen/{buchung_id}/stornieren", data={"csrf_token": csrf}, follow_redirects=False
    )
    anfrage_url = r.headers["location"]
    assert hauptsystem.abholen() == 1
    r = browser.get(anfrage_url, follow_redirects=False)
    assert r.headers["location"] == "/buchungen?meldung=storniert_kostenfrei"
    assert "storniert (kostenfrei)" in browser.get("/buchungen").text
    with CoreSession() as db:
        assert db.scalar(select(Kunde)).guthaben == Decimal("30.00")


def test_verlorene_antwort_wird_folgenlos_erneut_zugestellt(
    browser: TestClient,
    hauptsystem: Kanal,
    feld_id: uuid.UUID,
    mails: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    csrf = _anmelden(browser, mails, hauptsystem)
    warteseite = _buchen(browser, csrf, feld_id)

    original = hauptsystem.client.post

    def antworten_gehen_verloren(url: str, **kwargs):
        if url == "/core/antworten":
            raise httpx.ConnectError("Verbindung weg")
        return original(url, **kwargs)

    monkeypatch.setattr(hauptsystem.client, "post", antworten_gehen_verloren)
    with pytest.raises(httpx.ConnectError):
        hauptsystem.abholen()
    monkeypatch.setattr(hauptsystem.client, "post", original)

    # Nach 60 s liefert das Portal die unbeantwortete Anfrage erneut aus.
    spaeter = datetime.now(UTC) + timedelta(seconds=61)
    monkeypatch.setattr(portal_uhr, "jetzt", lambda: spaeter)
    assert hauptsystem.abholen() == 1
    with CoreSession() as db:
        assert len(db.scalars(select(Buchung)).all()) == 1
        assert len(db.scalars(select(Zahlung)).all()) == 1
    assert browser.get(warteseite, follow_redirects=False).headers["location"].startswith(
        "/test-zahlung/fake_"
    )
```

`ruff.toml` – Abschnitt `[lint.per-file-ignores]` erweitern:
```toml
[lint.per-file-ignores]
"*/tests/*" = ["S105", "S106"]
"e2e/*" = ["S105", "S106"]
```

- [ ] **Step 2: Test laufen lassen**

Run: `.venv/bin/pytest -q e2e`
Expected: PASS (2 Tests). Scheitert er, ist das ein echter Integrationsfehler zwischen den Tasks – mit superpowers:systematic-debugging eingrenzen, nicht den Test aufweichen.

- [ ] **Step 3: Lint, Typen, Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy
git add e2e ruff.toml
git commit -m "test(e2e): Buchung, Zahlung, Rechnung und Storno über den echten Kanal"
```

---

## Task 17: Betrieb – Container, Caddy, Handbuch, CI

**Files:**
- Create: `portal/Dockerfile`, `portal/docker-compose.yml`, `portal/.env.example`, `portal/deploy/Caddyfile`, `portal/deploy/init-test-db.sql`, `portal/README.md`
- Create: `docs/betrieb/portal.md`
- Modify: `docs/betrieb/hauptsystem.md` (neuer Abschnitt „Kanal zum Portal“), `core/.env.example`, `.github/workflows/ci.yml`, `README.md`

**Interfaces:**
- Consumes: CLI `beachhub-core zertifikate` (Task 7), `beachhub-core keygen` (bestehend), Einstellungen aus Tasks 3 und 8.
- Produces: Betriebsdokumentation; keine Codeänderung.

- [ ] **Step 1: Container und Caddy**

`portal/Dockerfile`:
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY shared /app/shared
COPY portal /app/portal
RUN pip install --no-cache-dir /app/shared /app/portal \
    && useradd --system --uid 10001 --home /app portal \
    && mkdir -p /app/data && chown portal /app/data
WORKDIR /app/portal
USER portal
# Caddy reicht die Client-IP als X-Forwarded-For durch; der App-Port ist nur an 127.0.0.1 gebunden.
CMD ["sh", "-c", "alembic upgrade head && uvicorn beachhub_portal.main:app --host 0.0.0.0 --port 8001 --proxy-headers --forwarded-allow-ips='*'"]
```

`portal/docker-compose.yml`:
```yaml
services:
  db:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_USER: beachhub
      POSTGRES_PASSWORD: beachhub
      POSTGRES_DB: beachhub_portal
    ports: ["127.0.0.1:5433:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data", "./deploy/init-test-db.sql:/docker-entrypoint-initdb.d/10-test-db.sql:ro"]
  app:
    build: { context: .., dockerfile: portal/Dockerfile }
    restart: unless-stopped
    env_file: .env
    environment:
      PORTAL_DATABASE_URL: postgresql+psycopg://beachhub:beachhub@db:5432/beachhub_portal
      PORTAL_DATA_DIR: /app/data
    depends_on: [db]
    ports: ["127.0.0.1:8001:8001"]
    volumes: ["./data:/app/data"]
  caddy:
    image: caddy:2
    restart: unless-stopped
    network_mode: host
    env_file: .env
    volumes:
      - "./deploy/Caddyfile:/etc/caddy/Caddyfile:ro"
      - "./data/zertifikate/ca.crt:/etc/caddy/beachhub-ca.crt:ro"
      - "caddy_data:/data"
    depends_on: [app]
volumes:
  pgdata: {}
  caddy_data: {}
```

`portal/deploy/init-test-db.sql`:
```sql
CREATE DATABASE beachhub_portal_test OWNER beachhub;
```

`portal/deploy/Caddyfile`:
```caddyfile
# Öffentliche Seite des Portals. Der Kanal des Hauptsystems (/core/*) ist hier gesperrt.
{$PORTAL_DOMAIN} {
	encode zstd gzip
	@kanal path /core/*
	respond @kanal 404
	reverse_proxy 127.0.0.1:8001
}

# Kanal des Hauptsystems: nur mit Client-Zertifikat der internen CA (beachhub-core zertifikate).
# Ein Client-Zertifikat wird im TLS-Handshake verlangt, bevor der Pfad bekannt ist – deshalb ein
# eigener Port statt einer Pfadregel. Firewall: 8443 nur für die Adresse des Hauptsystems öffnen.
{$PORTAL_DOMAIN}:8443 {
	tls {
		client_auth {
			mode require_and_verify
			trust_pool file /etc/caddy/beachhub-ca.crt
		}
	}
	@nicht_kanal not path /core/*
	respond @nicht_kanal 404
	reverse_proxy 127.0.0.1:8001
}
```

`portal/.env.example`:
```dotenv
# Alle Einstellungen des Portals tragen das Präfix PORTAL_.
PORTAL_DATABASE_URL=postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub_portal
PORTAL_SECRET_KEY=change-me
PORTAL_APP_ENV=dev
PORTAL_COOKIE_SECURE=false
PORTAL_BASE_URL=http://127.0.0.1:8001
PORTAL_DATA_DIR=./data
# Gemeinsames Geheimnis mit KANAL_TOKEN des Hauptsystems (zusätzlich zu mTLS)
PORTAL_KANAL_TOKEN=
# Öffentlicher Schlüssel des Hauptsystems (Hex), steht im Admin-UI unter System
PORTAL_CORE_PUBLIC_KEY=
PORTAL_SMTP_HOST=
PORTAL_SMTP_PORT=587
PORTAL_SMTP_USER=
PORTAL_SMTP_PASSWORD=
PORTAL_EMAIL_FROM=portal@example.org
PORTAL_BETREIBER_NAME=Beachhalle Musterstadt
PORTAL_BETREIBER_EMAIL=halle@example.org
# Nur Entwicklung: Testzahlung ohne echten Anbieter (im Produktivbetrieb verboten)
PORTAL_FAKE_ZAHLUNG=true
PORTAL_WEBHOOK_SIGNATUR_HEADER=Stripe-Signature
PORTAL_ENABLE_SCHEDULER=true
# Nur für Caddy im Produktivbetrieb
PORTAL_DOMAIN=buchung.example.org
```

`core/.env.example` – am Ende ergänzen:
```dotenv
# Kanal zum Portal (leer = kein Kanal). Lokal ohne mTLS: http://127.0.0.1:8001
PORTAL_URL=
KANAL_TOKEN=
PORTAL_CLIENT_CERT=
PORTAL_CLIENT_KEY=
PORTAL_CA=
ENABLE_KANAL=true
ZAHLUNG_PROVIDER=fake
```

- [ ] **Step 2: README und Betriebshandbuch**

`portal/README.md`:
````markdown
# Beachhub Buchungsportal (`portal/`)

Öffentliche FastAPI-App für Kunden: Konto per Link oder Code, freie Zeiten, Buchen mit
Online-Zahlung, Meine Buchungen mit PIN, Storno, Rechnungen. Das Portal entscheidet fachlich
nichts; es zeigt den signierten Lesestand des Hauptsystems und legt Anfragen ab, die das
Hauptsystem per Long-Polling abholt.

- Design: `docs/superpowers/specs/2026-09-23-portal-kern-design.md`
- Plan: `docs/superpowers/plans/2026-09-23-portal-kern.md`
- Betrieb: `docs/betrieb/portal.md`

## Schnellstart (Entwicklung)

```bash
pip install -e ../shared -e .[dev]
cp .env.example .env          # PORTAL_CORE_PUBLIC_KEY und PORTAL_KANAL_TOKEN eintragen
alembic upgrade head
uvicorn beachhub_portal.main:app --port 8001 --reload
```

Das Hauptsystem verbindet sich lokal ohne mTLS: in `core/.env` `PORTAL_URL=http://127.0.0.1:8001`
und denselben Token als `KANAL_TOKEN`. Ohne Mailserver zeigt die Anmeldeseite Link und Code direkt
an; bezahlt wird über die Testzahlungsseite (`PORTAL_FAKE_ZAHLUNG=true`).

## Tests

```bash
TEST_PORTAL_DATABASE_URL=postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub_portal_test pytest -q
```
Ende-zu-Ende mit dem Hauptsystem (aus der Repo-Wurzel): `pytest -q e2e`
````

`docs/betrieb/portal.md`:
````markdown
# Betriebshandbuch: Beachhub-Buchungsportal

Das Portal läuft als eigener Container (LXC) auf demselben Proxmox-Host wie das Hauptsystem, in
einer eigenen Netzzone hinter Caddy. Es ist aus dem Internet erreichbar; das Hauptsystem nicht.
Alle Verbindungen zwischen beiden baut das Hauptsystem auf (Hauptspec § 2, N-1).

## 1. Voraussetzungen

- Container mit Docker und Compose-Plugin, öffentliche Domain mit DNS-Eintrag auf den Container.
- Firewall: `443/tcp` öffentlich; `8443/tcp` nur für die ausgehende Adresse des Hauptsystems.
- Vom Hauptsystem: öffentlicher Signaturschlüssel (Admin-UI → System) und die interne CA.

## 2. Zertifikate und Token

Auf dem Hauptsystem (einmalig, erneut zur jährlichen Rotation):

```bash
docker compose -f core/docker-compose.yml exec app beachhub-core zertifikate --ziel /app/data/zertifikate
```

Das legt `ca.crt/ca.key` (bleibt bei Rotation bestehen) sowie `portal-kanal.crt/.key` und
`halle.crt/.key` an. `ca.key` verlässt das Hauptsystem nie.

- Auf das Portal kopieren: nur `ca.crt` nach `portal/data/zertifikate/ca.crt` (Caddy prüft damit).
- Im Hauptsystem eintragen (`core/.env`): `PORTAL_URL=https://<domain>:8443`,
  `PORTAL_CLIENT_CERT=/app/data/zertifikate/portal-kanal.crt`,
  `PORTAL_CLIENT_KEY=/app/data/zertifikate/portal-kanal.key`, `PORTAL_CA=` leer lassen (das Portal
  hat ein öffentliches Let's-Encrypt-Zertifikat).
- Einen zufälligen Token erzeugen (`python -c "import secrets; print(secrets.token_urlsafe(32))"`)
  und als `KANAL_TOKEN` (Hauptsystem) und `PORTAL_KANAL_TOKEN` (Portal) eintragen.

## 3. Installation

```bash
cd /opt/beachhub/portal
cp .env.example .env    # alle Werte setzen, PORTAL_APP_ENV=production, PORTAL_COOKIE_SECURE=true,
                        # PORTAL_FAKE_ZAHLUNG=false, PORTAL_SECRET_KEY zufällig
docker compose up -d --build
```

Mit `PORTAL_APP_ENV=production` verweigert das Portal den Start, solange Secret, Token,
öffentlicher Schlüssel, `COOKIE_SECURE` oder die Testzahlung nicht stimmen. Das Hauptsystem
verweigert den Start mit gesetzter `PORTAL_URL`, solange `ZAHLUNG_PROVIDER=fake` ist – der
echte Anbieter (Stripe oder Mollie) folgt nach der Entscheidung Ⓞ-13.

## 4. Prüfen

- `https://<domain>/health` → `{"status":"ok"}`
- `https://<domain>/core/anfragen` → 404 (öffentlich gesperrt)
- `curl https://<domain>:8443/core/anfragen` ohne Zertifikat → TLS-Fehler
- Im Log des Hauptsystems erscheinen nach dem Start keine Warnungen „Portal nicht erreichbar“.
- Admin-UI → System: Lesestand erzeugen; danach zeigt `https://<domain>/` die Belegung.

## 5. Backup

Das Portal hält Konten, Anfragen und (ab Stufe 4) die Gruppenverwaltung, für die es Master ist.
Täglich sichern:

```bash
docker compose exec -T db pg_dump -U beachhub -Fc beachhub_portal | gpg --encrypt -r <betreiber> \
  > /var/backups/beachhub/portal-$(date +%F).dump.gpg
```

Nach einer Wiederherstellung schickt das Hauptsystem fehlende Lesestände beim nächsten Abgleich
von selbst (spätestens nach einer Stunde, sofort nach einem Neustart des Hauptsystems).

## 6. Störungen

| Anzeichen | Ursache und Abhilfe |
|---|---|
| Kunden sehen „Anfrage gespeichert, Bestätigung folgt per E-Mail“ | Hauptsystem holt nicht ab: WireGuard/Netz, Zertifikat abgelaufen, `KANAL_TOKEN` ungleich. Nach 30 min bekommt der Betreiber eine Mail. |
| Mail „Lesestand vom Portal abgelehnt“ | `PORTAL_CORE_PUBLIC_KEY` passt nicht zum Schlüssel des Hauptsystems. |
| Belegung leer mit „wird gerade geladen“ | Noch kein Lesestand angekommen: Admin-UI → System → „Lesestand erzeugen (alle)“. |
````

`docs/betrieb/hauptsystem.md` – vor „## 6. Backup und Wiederherstellung“ einen Abschnitt einfügen (und die folgenden Nummern nicht ändern; der neue Abschnitt heißt „5a“):
```markdown
## 5a. Kanal zum Portal

Das Hauptsystem holt Anfragen des Portals per Long-Polling ab und liefert Antworten und Lesestände
aus; das Portal kann das Hauptsystem nicht erreichen. Der Kanal startet mit der Anwendung, sobald
`PORTAL_URL` gesetzt ist (`ENABLE_KANAL=false` schaltet ihn ab). Einrichtung von Zertifikaten und
Token: `docs/betrieb/portal.md`, Abschnitt 2.

- Ist das Portal 30 Minuten nicht erreichbar, kommt eine Mail „Portal nicht erreichbar“, bei
  Rückkehr „Portal wieder erreichbar“.
- Reservierungen ohne Zahlung verfallen nach `zahlungsfrist_minuten`; der Kunde bekommt eine Mail.
- Bis zur Entscheidung über den Zahlungsanbieter (Ⓞ-13) gibt es nur die Testzahlung. Mit gesetzter
  `PORTAL_URL` und `APP_ENV=production` startet das Hauptsystem deshalb bewusst nicht.
```

`README.md` (Repo-Wurzel):
- Statuszeile ersetzen durch: „Status: Stufe 1 (Hauptsystem) fertig; Portal-Kern (Stufe 2 ohne Mitgliedschaft, Gutscheine und echten Zahlungsanbieter) implementiert; Hallendienst (Stufe 3) in Arbeit.“
- Unter den Dokumentlinks ergänzen: `- Portal: Design docs/superpowers/specs/2026-09-23-portal-kern-design.md, Betrieb docs/betrieb/portal.md, Kurzstart portal/README.md`
- Im Abschnitt „Entwicklung“ ergänzen:
```bash
pip install -e portal[dev]
cd portal && pytest          # TEST_PORTAL_DATABASE_URL setzen
pytest e2e                   # aus der Repo-Wurzel, beide Test-Datenbanken
```

- [ ] **Step 3: CI**

`.github/workflows/ci.yml` – Schritte ab `pip install` ersetzen durch:
```yaml
      - run: pip install -e shared[dev] -e core[dev] -e portal[dev]
      - run: PGPASSWORD=beachhub psql -h localhost -U beachhub -d beachhub_test -c "CREATE DATABASE beachhub_portal_test"
      - run: ruff check . && ruff format --check .
      - run: mypy
      - run: cd shared && pytest -q
      - run: cd core && pytest -q
        env:
          TEST_DATABASE_URL: postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub_test
      - run: cd portal && pytest -q
        env:
          TEST_PORTAL_DATABASE_URL: postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub_portal_test
      - run: pytest -q e2e
        env:
          TEST_DATABASE_URL: postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub_test
          TEST_PORTAL_DATABASE_URL: postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub_portal_test
```

- [ ] **Step 4: Alles prüfen**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy
(cd shared && ../.venv/bin/pytest -q)
(cd core && ../.venv/bin/pytest -q)
(cd portal && ../.venv/bin/pytest -q)
.venv/bin/pytest -q e2e
(cd core && ../.venv/bin/alembic upgrade head && ../.venv/bin/alembic current)
(cd portal && ../.venv/bin/alembic upgrade head && ../.venv/bin/alembic current)
```
Expected: alle Tests grün; `alembic current` zeigt `0008 (head)` für das Hauptsystem und `0001 (head)` für das Portal. (Die Migrationen laufen gegen die Entwicklungsdatenbanken `beachhub` und `beachhub_portal`; der Server wird dafür nicht gestartet.)

- [ ] **Step 5: Commit**

```bash
git add portal/Dockerfile portal/docker-compose.yml portal/.env.example portal/deploy portal/README.md \
  docs/betrieb core/.env.example .github/workflows/ci.yml README.md
git commit -m "docs: Betrieb des Portals, Kanal im Betriebshandbuch, CI für Portal und E2E"
```

---

## Abnahme (Ende des Plans)

Kai startet Hauptsystem und Portal selbst (nie der Agent):
```bash
! cd core && uvicorn beachhub_core.main:app --port 8000
! cd portal && uvicorn beachhub_portal.main:app --port 8001
```
mit `core/.env`: `PORTAL_URL=http://127.0.0.1:8001`, `KANAL_TOKEN=<x>`; `portal/.env`: `PORTAL_KANAL_TOKEN=<x>`, `PORTAL_CORE_PUBLIC_KEY=<aus Admin-UI → System>`, `PORTAL_FAKE_ZAHLUNG=true`.

- [ ] Admin-UI: Felder, Betriebszeiten, Tarif angelegt; System → „Lesestand erzeugen“; das Portal zeigt die Belegung ohne Kundennamen (N-2, Ⓞ-15).
- [ ] Anmeldung per Code (Dev-Hinweis auf der Seite) und per Link; Name auf `/willkommen`; im Admin-UI erscheint der Kunde mit der Portal-Kundengruppe.
- [ ] Buchen → Testzahlung „Bezahlen“ → „Meine Buchungen“ zeigt die PIN; Mail „Buchung bestätigt“ im Log des Hauptsystems; Rechnung im Admin-UI `bezahlt` (A-ZAHL-2, A-RECH-2).
- [ ] Testzahlung „Abbrechen“, 15 Minuten warten → Reservierung verfallen, Slot wieder frei, Mail „Reservierung verfallen“ (A-ZAHL-3).
- [ ] Storno mehr als 24 h vorher → kostenfrei, Guthaben im Admin-UI; nächste Buchung verrechnet es (A-STORNO-1, A-ZAHL-4).
- [ ] Rechnung im Portal herunterladen; zweiter Klick auf denselben Link → Hinweis (A-RECH-5).
- [ ] Hauptsystem stoppen, im Portal buchen → nach 2 Minuten Hinweis „Anfrage gespeichert …“; Hauptsystem starten → Anfrage wird verarbeitet (N-4).
- [ ] Konto löschen → Kunde im Admin-UI anonymisiert (Hauptspec § 10).

Nicht Teil dieses Plans: Mitgliedschaft, Gutscheine, Stripe/Mollie, Gruppenverwaltung, Erinnerungsmail 24 h vor dem Termin, Korrekturbeleg bei Storno-Gutschrift (A-STORNO-6 – bestehende Lücke aus Stufe 1, gehört zu 1a).

**Abschluss:** Branch nach `main` mergen (`--no-ff`), pushen, `alembic upgrade head` für Hauptsystem **und** Portal, Worktree entfernen, lokalen und Remote-Branch löschen. Den Neustart der Anwendungen übernimmt Kai.
