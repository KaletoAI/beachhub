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
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ../shared -e .[dev]
```

PostgreSQL bereitstellen – entweder per Compose (Container `db`, Port `127.0.0.1:5433` – bewusst
nicht `5432`, damit dieselbe Maschine gleichzeitig `core`s eigenen Compose-`db`-Dienst auf `5432`
laufen haben kann; `PORTAL_DATABASE_URL`/`TEST_PORTAL_DATABASE_URL` unten dann auf Port `5433`
setzen):

```bash
docker compose up -d db
```

oder eine lokal installierte PostgreSQL-16-Instanz auf dem Standardport `5432` (passt zum
Vorgabewert in `.env.example`). Die Datenbank `beachhub_portal` muss mit
`TEMPLATE template0 ENCODING 'UTF8'` angelegt werden (ein `CREATE DATABASE` aus `template1`
liefert auf manchen Systemen `SQL_ASCII`, woran psycopg3 beim ersten Connect abstürzt):

```bash
createdb -O beachhub --template=template0 --encoding=UTF8 beachhub_portal
```

Konfiguration und Datenbankschema:

```bash
cp .env.example .env          # PORTAL_CORE_PUBLIC_KEY und PORTAL_KANAL_TOKEN eintragen
alembic upgrade head
uvicorn beachhub_portal.main:app --port 8001 --reload
```

Das Hauptsystem verbindet sich lokal ohne mTLS: in `core/.env` `PORTAL_URL=http://127.0.0.1:8001`,
`PORTAL_OEFFENTLICHE_URL=http://127.0.0.1:8001` und denselben Token als `KANAL_TOKEN`. Ohne
Mailserver zeigt die Anmeldeseite Link und Code direkt an; bezahlt wird über die
Testzahlungsseite (`PORTAL_FAKE_ZAHLUNG=true`).

## Tests

```bash
createdb -O beachhub --template=template0 --encoding=UTF8 beachhub_portal_test
TEST_PORTAL_DATABASE_URL=postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub_portal_test pytest -q
```

Port `5432` passt zu einer lokal installierten PostgreSQL; mit dem Compose-`db`-Dienst (s. o.,
Port `5433`) `TEST_PORTAL_DATABASE_URL` entsprechend anpassen. Die Testdatenbank
`beachhub_portal_test` wird beim Start von `docker compose up -d db` durch
`deploy/init-test-db.sql` automatisch angelegt; bei lokaler PostgreSQL-Installation muss sie
händisch erstellt werden (Befehl s. o.).

Ende-zu-Ende mit dem Hauptsystem (aus der Repo-Wurzel): `pytest -q e2e`
