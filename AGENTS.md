# Beachhub – Arbeitsgrundlagen

## Orientierung

Beachhub verwaltet Buchungen, Abrechnung und Hallentechnik einer Beachvolleyballhalle.
Arbeits- und Dokumentationssprache ist Deutsch.

- `core/`: Hauptsystem mit FastAPI-Admin-UI, PostgreSQL, Fachlogik und Abrechnung.
- `portal/`: öffentliches FastAPI-Kundenportal, eigene PostgreSQL-Datenbank.
- `hall/`: autonomer Hallendienst mit SQLite und Home-Assistant-Anbindung.
- `shared/`: gemeinsame Datenverträge, Signaturen, Slots und Zeitfunktionen.
- `e2e/`: Integrationstests zwischen Hauptsystem und Portal.

Zuerst `README.md` und die README des betroffenen Teilsystems lesen. Anforderungen
stehen in `docs/superpowers/specs/2026-09-05-beachhub-design.md`, ergänzende Designs
und Umsetzungspläne unter `docs/superpowers/`. Betriebsanleitungen liegen unter
`docs/betrieb/`. Bestehende Betreiberentscheidungen und dokumentierte Einstellungen
bei Änderungen berücksichtigen; ein Plan beschreibt geplante Arbeit und ist kein
Nachweis, dass sie bereits umgesetzt wurde.

## Architektur und Konventionen

- Das Hauptsystem entscheidet über Verfügbarkeit, Preise, Storno, Rechnungen und PINs.
  Das Portal zeigt den signierten Lesestand und übermittelt Anfragen. Fachliche
  Entscheidungen gehören nicht ins Portal.
- Das Hauptsystem baut die Portalverbindung selbst auf; der Hallendienst arbeitet
  mit signierten Plänen und kann bis zu deren Ablauf offline arbeiten.
- Gerätezuordnungen zu Home-Assistant-Entitäten gehören ausschließlich in den Hallendienst.
- Python ab 3.12; SQLAlchemy 2 und Alembic für die PostgreSQL-Systeme.
- Bestehende Oberfläche mit Jinja2 und CSS weiterführen; kein zusätzlicher
  Frontend-Build ohne fachlichen Anlass. Keine Inline-Skripte.
- Geldwerte als `Decimal`, keine Gleitkommazahlen. Fachliche Zeitregeln und
  vorhandene Uhrabstraktionen beachten.
- Änderungen an Kunden, Buchungen, Rechnungen und Einstellungen nach dem bestehenden
  Muster auditieren. Mails, Rechnungs-PDFs und externe Effekte erst nach erfolgreichem Commit.
- Portal-Nutzlasten als nicht vertrauenswürdig behandeln; die Kundenzuordnung erfolgt
  im Hauptsystem über die bestehende Verbindung zum Portalkonto.
- Schemaänderungen brauchen Alembic-Migrationen und Migrationstests. Vor dem Anlegen
  einer Migration die vorhandene Revisionskette und den jeweiligen Plan prüfen.
- Vorhandene Claude-Unterlagen als Projekthistorie erhalten. Werkzeugspezifische
  Vorgaben aus alten Plänen an die aktuelle Umgebung anpassen; keine fremde
  Agentenattribution in neue Commits übernehmen.
- Geheimnisse aus `.env`, Schlüsseln oder lokalen Konfigurationen nicht in Ausgaben
  oder Versionsverwaltung übernehmen.

## Entwicklung und Prüfung

Die bestehende virtuelle Umgebung liegt in `.venv/`. CI-Vorgaben stehen in
`.github/workflows/ci.yml`; Pakete bei Bedarf mit folgenden Extras installieren:

```bash
pip install -e 'shared[dev]' -e 'core[dev]' -e 'hall[dev]' -e 'portal[dev]'
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
(cd shared && ../.venv/bin/pytest -q)
(cd hall && ../.venv/bin/pytest -q)
(cd core && ../.venv/bin/pytest -q)
(cd portal && ../.venv/bin/pytest -q)
.venv/bin/pytest -q e2e
```

Tests für `core`, `portal` und `e2e` benötigen echte PostgreSQL-Testdatenbanken:

```bash
export TEST_DATABASE_URL=postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub_test
export TEST_PORTAL_DATABASE_URL=postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub_portal_test
```

Die Fixtures löschen und erstellen das Testschema vor jedem Test. Ausschließlich
Testdatenbanken verwenden. Die Portal-Compose-Instanz nutzt abweichend Port 5433;
die URL dann entsprechend setzen. `core`, `portal` und `e2e` jeweils als getrennte
pytest-Läufe ausführen; `e2e` erst nach den anderen Datenbanktests starten, da es
beide Datenbanken verwendet. Hallentests brauchen lokale Sockets für Simulatoren.
Anwendungen über pytest/TestClient prüfen; der Start eines Anwendungsservers ist
für diese Prüfungen nicht erforderlich.

Vor Abschluss die für die Änderung relevanten Prüfungen ausführen und tatsächliche
Ergebnisse sowie verbleibende Einschränkungen nennen. Bei fachlichen Änderungen
auch betroffene Spezifikation, README und Betriebsanleitung aktualisieren.

## Ausgangsstand der Übernahme am 04.10.2026

Laut README und Git-Historie sind Stufe 1, Stufe 1a-I (Kundengruppen,
Mitgliedschaft, Rechnungskunden), Portal-Kern und Hallendienst implementiert.

## Umsetzungsstand nach Stufe 1a-II am 05.10.2026

Stufe 1a-II ist ebenfalls implementiert: atomare Saisonrechnung mit Guthabenverrechnung,
freie Abo-Absagen, Korrekturbelege/Teil-Storno, Guthabenliste und manuell abgehakte Auszahlungen.
Bestandsabos werden nicht automatisch abgerechnet; die ausdrückliche Aktion „Saisonrechnung
erstellen“ berechnet aktive oder kostenpflichtig abgesagte unberechnete Termine und erlaubt
Neuausstellung nach Vollstorno. Kostenfreie Absagen und bereits korrigierte Termine bleiben
ausgeschlossen. Vollstorno gibt nur den verbleibenden bezahlten Anteil als Guthaben zurück;
vorherige Teilgutschriften bleiben erhalten. Freie Kundenabsagen verbrauchen Kontingent, außer
der Termin ist bereits gutgeschrieben; auch unberechnete Termine verbrauchen Kontingent.
Die Portalbestätigung zeigt das freie Absagekontingent und bedingte finanzielle Hinweise;
eine ausführlichere Abo-Übersicht bleibt für Stufe 2 offen. Die jährliche Guthabenerinnerung
läuft um 07:05 Europe/Berlin; SMTP-Fehler nach Marker-Commit werden nicht automatisch wiederholt.

## Umsetzungsstand nach Stufe 1a-III am 07.10.2026

Gutscheine und Freischaltcodes sind im Hauptsystem implementiert: beide Modelle und
Steuerzeitpunkte, Kauf über den Kanal, atomare Einlösung, Verwaltung/Serienexport, Storno/Kulanz,
Reservierungsrücknahme, Ablauf, manuelle Rückgabe und signierter eigener Bestand/offene Käufe.
Revision `0015` folgt direkt auf `0014`; keine weitere Revision für den Abschluss anlegen.
Kauf-Snapshots, Belegbasis und die getrennten Nummernkreise sind verbindlich.
Kostenfreier bestätigter Wertstorno erzeugt genau einmal Einlöserguthaben mit Gegenbeleg und
erhöht den Gutscheinrest nicht; Einheiten erhalten ihre Nutzung unter ursprünglicher Gültigkeit
zurück. Reservierungsrücknahmen stellen den ursprünglichen Bestand und reserviertes Guthaben
wieder her. Vollständig ungenutzte Kaufgutscheine werden dauerhaft gesperrt und manuell an den
ursprünglichen Käufer erstattet; dabei entsteht kein Guthaben und kein Banktransfer.
Generische Rechnungsaktionen dürfen diese Fachpfade nicht umgehen. Käufer-PDFs bleiben beim
Käufer, auch bei fremdem Einlöser oder Inhaber. Offene Kaufcodes bleiben geheim.
Die vollständige Kundenmenge wird vor Zahlungen, Gutscheinen und weiteren Schreibsperren
UUID-sortiert vorbereitet; nach Besitz-/Kontowechsel vollständig zurückrollen und neu vorbereiten.
Tarifdeckung und Buchung verwenden denselben Slotpreisstand. PDF-/Mailnachläufe erfolgen nach
Commit und isolieren einzelne Fehler, ohne automatische SMTP-Wiederholung zu versprechen.
Verkaufs- und Codeeingabeoberfläche einschließlich IP-Limit folgen in Stufe 2.
Der echte Zahlungsanbieter und die echte Hallentechnik sind noch anzubinden; E2E nutzt den
Fakeanbieter, Hallentests Simulatoren. Der aktuelle Plan vom 05.10.2026 ersetzt den historischen
Gutscheinplan vom 03.10.2026. Stand bei Folgeaufgaben erneut anhand von Code und Dokumentation prüfen.

## Nachreview Stufe 1a-II am 15.10.2026

Befunde in `docs/superpowers/reviews/2026-10-15-stufe-1a-II-nachreview.md`, alle behoben:
Vollstorno einer Einzelrechnung stellt weiter abrechenbare Buchungen sofort neu aus und verrechnet
den erstatteten Anteil; `rechnungen.storniere` liefert `Vollstorno(beleg, neu)`. Beleglose
Stornogutschriften vor 0014 werden beim Nachholen des Belegs angerechnet. Die Datenmigration
`0016_upgrade_1a_ii` folgt auf `0015_gutscheine`, markiert Konto-Dokumente und setzt den
Saisonende-Marker beim Upgrade. Sperre, Daueranlage und
Abo-Ende verschicken Stornobestätigungen für die tatsächlich erzeugten Stornos
(mit Gutschein-Rückabwicklungshinweis und Gutschein-Korrekturbelegen). Der Kanal meldet
bei Abo-Absagen `korrekturbeleg`; Portal vor Hauptsystem aktualisieren.
