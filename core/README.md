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

## Saisonrechnung und Guthaben

Neue Dauerbuchungen erhalten ihre Saisonrechnung atomar bei der Anlage; einen Monatslauf gibt es
nicht mehr. Für unberechnete Bestandsabos und die Neuausstellung nach vollständigem Rechnungsstorno
steht auf der Abo-Detailseite die ausdrückliche Aktion **Saisonrechnung erstellen** bereit. Sie
berechnet aktive sowie kostenpflichtig abgesagte unberechnete Termine und lässt historische
Belege unverändert. Kostenfreie Absagen und bereits korrigierte Termine bleiben ausgeschlossen.
Vollstorno gibt den verbleibenden bezahlten Anteil mit Korrekturbeleg als Guthaben zurück;
vorherige Teilgutschriften werden dabei nicht doppelt erstattet. Beim Upgrade
muss der Betreiber solche Bestandsabos prüfen; Migrationen rechnen sie nicht automatisch ab.
Details zu Korrekturbelegen, freien Abo-Absagen und manuellen Auszahlungen stehen im
[Betriebshandbuch](../docs/betrieb/hauptsystem.md#5-laufender-betrieb).

Bei Buchungsanlagen gilt die Sperrreihenfolge Kunde vor Feld, auch für Portal-Einzelbuchungen.
Daueranlagen sperren vorher die vollständige Menge beteiligter Kunden nach UUID; die
Fremdschlüsselprüfungen von PostgreSQL sind bei weiteren Änderungen mitzuberücksichtigen.

## Gutschein-Datenbasis (Stufe 1a-III, in Arbeit)

Revision `0015` bereitet Kauf-Snapshots, Einlösungszustände und die genaue Belegbasis vor.
Offene Käufe haben noch kein Ablaufdatum; Gültigkeitsdauer und Zahlungsfrist bleiben gespeichert.
Der Rechnungskreis behält seine bisherigen Jahreszähler; steuerfreie Kaufbelege erhalten einen
eigenen Kreis `gutschein` mit Nummern `GB-JJJJ-NNNNN`. Freischaltcodes haben Steuer `keine`.
Freischaltcodes lassen sich unter **Gutscheine → Freischaltcodes** ausstellen und als Serie
exportieren. Die Übersicht und Detailseite erlauben Sperren und Gültigkeitsänderungen mit Audit.
Reaktivierung erhält verbrauchte Einlösungen und Restwerte; zurückgegebene Kaufgutscheine bleiben
dauerhaft gesperrt. Die Einlösung und getrennte Buchungsbelege sind im Hauptsystem-Kanal implementiert;
Die Rückabwicklung ist implementiert; Kauf und Portaloberfläche folgen in weiteren Schritten.
Der Gutschein-Nachtjob läuft täglich um 00:20 Europe/Berlin; das Ablaufdatum gilt einschließlich.
Die internen Dienste für Codes, Vorgangsvorbereitung und Fehlversuchslimit sind vorhanden:
`gutschein_codes` normalisiert 13 Zeichen, zeigt sie als `4-4-5` an und fügt neue Codes
mit einem separaten Insert-Savepoint kollisionsfest ein. Die 32 Symbole schließen `0/O/1/I` aus.
`gutschein_sperren.bereite_vorgang_vor` sperrt die vollständige Kundenmenge, vorhandene
Zahlungen und Gutscheine jeweils UUID-sortiert und lädt deren Beziehungen frisch.
`bereite_buchungen_vor` ermittelt dieselbe Menge für Batch-Stornos vor weiteren Sperren.
Ein zwischenzeitlicher Besitzer- oder Zahlungswechsel kann einen internen Retry der gesamten
Transaktion erfordern; zusätzliche Kunden dürfen nicht nachträglich gesperrt werden.
Die Online-Kundensperre liegt bereits außerhalb des Buchungs-Savepoints. Das Limit zählt zehn
Fehlversuche je Konto in 60 Minuten mit der Fachuhr; eine `anfrage_id` dedupliziert Wiederholungen.
Codehaltige Buchungen prüfen das Limit außerhalb ihres Savepoints. Abgelehnte Anfragen
rollen Buchung und Einlösungen zurück; ihre Anfrage-ID zählt genau einmal. Besitzer-/Zahlungswechsel
werden im Kanal mit höchstens drei vollständigen Transaktionsversuchen neu vorbereitet.
`gutschein_einloesung.loese_ein` konsumiert den vorher erzeugten `GutscheinSperrkontext`;
`gutschein_belege.rechnung_fuer_buchung` gibt Geldrechnung und sämtliche Einlösungsbelege zurück.
Einlösungspositionen haben keinen Buchungs-FK; der Buchungsmarker verweist nur auf den Geldrest.
Historische Minuten und Mitgliedsvoraussetzung bleiben Kauf-Snapshots. Nicht volle Slots werden
zeitproportional gedeckt und kumulativ auf Cent gerundet.
Ein Downgrade verweigert vorhandenen Gutscheinbestand, Gutscheinbelege und verbrauchte
Gutschein-Nummernkreise ausdrücklich. Es löscht diese Daten nicht.

Für Buchungspreis und Gutschein-Tarifdeckung gilt derselbe unveränderliche Slotpreisstand.
`buchungen.lege_an_mit_preisstand` liefert intern `Buchungsanlage(buchung, preisstand)`;
`lege_an` behält seine bisherige Signatur und Buchungsrückgabe. Die Einlösung erhält
`preisstand` explizit, prüft Feld, Zeitraum, Gruppe, Slotfolge und Summe und liest keine
aktuellen Tarife nach. Alle Tarifregeln der Slotpreisliste stammen aus einer einzigen Abfrage.
Der Preisstand ist kein Feld des Portal-Kanalvertrags.

Kostenfreier bestätigter Storno schreibt Wertanteile dem Einlöser mit Gegenbeleg gut;
der Gutscheinrest bleibt unverändert. Einheiten/Freicodes erhalten ihren Bestand zurück,
Mehrzweck-Einheiten einen steuerlichen Gegenbeleg ohne Gutscheinwertguthaben. Die ursprüngliche
Gültigkeit bleibt bestehen; gesperrte/verfallene Codes werden nicht reaktiviert. Geldrest und
Käuferbeleg bleiben getrennt. Reservierungsabbruch/-verfall stellt lediglich den ursprünglichen
Bestand und das reservierte Kundenguthaben wieder her, auch beim Betreiberstorno oder
in Batches. Der Zahlungs-Callback prüft `reserviert_bis` selbst; nach Ablauf entstehen
Rücknahme und Spätzahlungsguthaben atomar, auch wenn der Job noch nicht gelaufen ist.
`gutschein_einloesung.storno_rueckgaengig` konsumiert den vollständigen Sperrkontext und liefert
freigegebene Gutscheine, tatsächliches Guthaben (einschließlich Reservierungsrückbuchung)
und sämtliche Gegenbelege. Batch-Einstiege
reichen denselben Kontext bis zur letzten Korrektur weiter. `gutschein_belege.korrigiere_wertanteil`
berechnet die Nettodifferenz aus kumulativ korrigiertem Brutto/Netto, unabhängig von der
Stornoreihenfolge. Ursprungpositionen erhalten ihren Korrekturmarker erst bei vollständiger
Korrektur. Gegenbelege und Einlöserhinweise werden nach Commit versandt; Käufer-PDFs gehen
nur an den Käufer. Die öffentlichen Rechnungsaktionsguards folgen in Task 8.
