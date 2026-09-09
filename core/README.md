# Beachhub Hauptsystem (`core/`)

FastAPI-Admin-UI, PostgreSQL-Datenbank und CLI für Belegungsplan, Buchungen, Tarife, Rechnungen
und den signierten Lesestand des Beachhub-Hauptsystems. Diese Datei ist die Kurzfassung für
Entwickler; für den produktiven Betrieb siehe `docs/betrieb/hauptsystem.md` im Repo-Wurzelverzeichnis.

- Technische Spezifikation: `docs/superpowers/specs/2026-09-05-beachhub-design.md`
- Umsetzungsplan Stufe 1: `docs/superpowers/plans/2026-09-08-stufe-1-hauptsystem.md`

## Schnellstart (Entwicklung)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ../shared -e .[dev]
```

PostgreSQL bereitstellen – entweder per Compose (Container `db`, Port `127.0.0.1:5432`):

```bash
docker compose up -d db
```

oder eine lokal installierte PostgreSQL-16-Instanz mit Datenbank/Benutzer `beachhub`/`beachhub`
gemäß `DATABASE_URL` in `.env.example`.

Konfiguration und Datenbankschema:

```bash
cp .env.example .env   # SECRET_KEY/PIN_SCHLUESSEL für echten Betrieb ersetzen, s.u.
alembic upgrade head
```

Signaturschlüssel für den Lesestand erzeugen (einmalig, legt `data/signatur.key` an und gibt den
öffentlichen Schlüssel als Hex aus):

```bash
beachhub-core keygen
```

Ersten Admin-Benutzer anlegen (fragt interaktiv nach dem Passwort und gibt TOTP-Secret sowie
`otpauth://`-URI für die Authenticator-App aus):

```bash
beachhub-core create-admin --name admin
```

Anwendung starten:

```bash
uvicorn beachhub_core.main:app --reload
```

Das Admin-UI ist danach unter `http://127.0.0.1:8000/admin/login` erreichbar.

## Tests

```bash
TEST_DATABASE_URL=postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub_test pytest -q
```

Die Testdatenbank `beachhub_test` wird beim Start von `docker compose up -d db` durch
`deploy/init-test-db.sql` automatisch angelegt; bei lokaler PostgreSQL-Installation muss sie
händisch erstellt werden (`createdb -O beachhub beachhub_test`).

## Lint

```bash
ruff check . && ruff format --check .
```

## CLI-Übersicht

- `beachhub-core keygen` – erzeugt das Ed25519-Schlüsselpaar für die Signatur des Lesestands.
- `beachhub-core create-admin --name <name> [--rolle admin|lesend]` – legt einen Admin-Benutzer an.
- `beachhub-core monatslauf JJJJ-MM` – stößt den Monatslauf (Sammelrechnungen) manuell für einen
  Monat an; im laufenden Betrieb übernimmt das der Scheduler täglich um 06:00 Uhr automatisch.
