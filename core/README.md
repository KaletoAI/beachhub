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
- `beachhub-core demodaten` – füllt eine Entwicklungsdatenbank ohne Kunden für den manuellen
  Test. Fehlende Stammdaten werden ergänzt: Feld 1–3, Raster 60 Minuten für Feld 1 und 2 sowie
  120 Minuten für Feld 3 und ein Tarif mit 40,00 € je Slot. Die Halle öffnet täglich 15–23 Uhr;
  abweichende Betriebszeiten werden dafür ersetzt. Am 24., 25., 26. und 31.12. des laufenden
  Jahres ist geschlossen. Vorhandene Felder, Raster und Tarife bleiben unverändert. Auf Feld 3
  entstehen ab morgen:
  - Anna: Online-Buchung, über den Fakeanbieter bezahlt.
  - Ben: zwei Betreiberbuchungen mit offener Rechnung.
  - Carla: Mitglied und Rechnungskunde, Abo über acht Wochen ohne Schließtag mit Saisonrechnung.
  - Dora: 50,00 € Guthaben.

  Alle Demokunden haben Adressen `…@example.org`. Wer sich im Portal mit einer davon anmeldet,
  wird mit dem Kunden verknüpft; ohne SMTP zeigt das Portal im Dev-Betrieb den Anmeldecode an.
  Der Befehl verweigert sich bei `APP_ENV=production` und sobald Kunden existieren.

## Postausgang im Testsystem

Außerhalb von `APP_ENV=production` legt das Hauptsystem jede Mail als Originalnachricht unter
`DATA_DIR/postausgang/` ab (die neuesten 500). Unter System → Postausgang erscheinen Kopfzeilen,
der Text mit seinen Zeilenumbrüchen, die PDF-Anhänge zum Öffnen und die `.eml`-Datei zum
Herunterladen. Der Schalter **Versand** ist anfangs aus: Mails gehen dann an niemanden hinaus.
Eingeschaltet werden sie zusätzlich über SMTP verschickt, der Status zeigt „gesendet“ oder
„Versand fehlgeschlagen“. Umschalten dürfen Admins mit Rolle `admin`; es wird protokolliert.
Mails des Portals (Anmeldecodes) laufen nicht über das Hauptsystem und erscheinen dort nicht. Im
Produktivbetrieb gibt es weder Ablage noch Seite; Mails gehen wie bisher direkt per SMTP hinaus.

## Saisonrechnung und Guthaben

Neue Dauerbuchungen erhalten ihre Saisonrechnung atomar bei der Anlage; einen Monatslauf gibt es
nicht mehr. Für unberechnete Bestandsabos und die Neuausstellung nach vollständigem Rechnungsstorno
steht auf der Abo-Detailseite (Belegung → Dauerbuchungen; verlinkt von Kunden-, Buchungs- und
Rechnungsseite) die ausdrückliche Aktion **Saisonrechnung erstellen** bereit. Sie
berechnet aktive sowie kostenpflichtig abgesagte unberechnete Termine und lässt historische
Belege unverändert. Kostenfreie Absagen und bereits korrigierte Termine bleiben ausgeschlossen.
Vollstorno gibt den verbleibenden bezahlten Anteil mit Korrekturbeleg als Guthaben zurück;
vorherige Teilgutschriften werden dabei nicht doppelt erstattet. Einzelrechnungen stellt das
Vollstorno für weiter abrechenbare Buchungen in derselben Transaktion neu aus und verrechnet den
erstatteten Anteil darauf; beleglose Stornogutschriften aus der Zeit vor 0014 werden beim
Nachholen des Belegs angerechnet. Beim Upgrade
muss der Betreiber solche Bestandsabos prüfen; Migrationen rechnen sie nicht automatisch ab.
Details zu Korrekturbelegen, freien Abo-Absagen und manuellen Auszahlungen stehen im
[Betriebshandbuch](../docs/betrieb/hauptsystem.md#5-laufender-betrieb).

Bei Buchungsanlagen gilt die Sperrreihenfolge Kunde vor Feld, auch für Portal-Einzelbuchungen.
Daueranlagen sperren vorher die vollständige Menge beteiligter Kunden nach UUID; die
Fremdschlüsselprüfungen von PostgreSQL sind bei weiteren Änderungen mitzuberücksichtigen.

## Gutscheine und Freischaltcodes (Stufe 1a-III)

Revision `0015` folgt direkt auf `0014` und ergänzt Kauf-Snapshots, Einlösungszustände und die
genaue Belegbasis. Die bestehenden Rechnungszähler werden unverändert übernommen.
Offene Käufe haben noch kein Ablaufdatum; Gültigkeitsdauer und Zahlungsfrist bleiben gespeichert.
Der Rechnungskreis behält seine bisherigen Jahreszähler; steuerfreie Kaufbelege erhalten einen
eigenen Kreis `gutschein` mit Nummern `GB-JJJJ-NNNNN`. Freischaltcodes haben Steuer `keine`.
Freischaltcodes lassen sich unter **Gutscheine → Freischaltcodes** ausstellen und als Serie
exportieren. Die Übersicht und Detailseite erlauben Sperren und Gültigkeitsänderungen mit Audit.
`GET /admin/gutscheine` paginiert mit `seite` ab 1 (500 Einträge), sortiert nach
Ausstellungszeit absteigend und UUID. Art, Status und Serie bleiben in den Seitenlinks
erhalten. Käufer und Inhaber werden je Seite mit einer gemeinsamen Kundenabfrage geladen.
Reaktivierung erhält verbrauchte Einlösungen und Restwerte; zurückgegebene Kaufgutscheine bleiben
dauerhaft gesperrt. Kauf, atomare Einlösung, getrennte Buchungsbelege, Rückabwicklung und
signierter Gutscheinbestand mit offenen Käufen sind im Hauptsystem-Kanal implementiert.
Verkaufs- und Codeeingabeoberfläche sowie der echte Zahlungsanbieter folgen in Stufe 2;
die E2E-Prüfung nutzt den verifizierten Fakeanbieter und zwei PostgreSQL-Testdatenbanken.
Für Betreiberabläufe siehe [Gutscheine im Betriebshandbuch](../docs/betrieb/hauptsystem.md#5c-gutscheine-und-freischaltcodes).
Der Gutschein-Nachtjob läuft täglich um 00:20 Europe/Berlin; das Ablaufdatum gilt einschließlich.
Die internen Dienste für Codes, Vorgangsvorbereitung und Fehlversuchslimit sind vorhanden:
`gutschein_codes` normalisiert 13 Zeichen, zeigt sie als `4-4-5` an und fügt neue Codes
mit einem separaten Insert-Savepoint kollisionsfest ein. Die 32 Symbole schließen `0/O/1/I` aus.
`gutschein_sperren.bereite_vorgang_vor` sperrt die vollständige Kundenmenge, vorhandene
Zahlungen und Gutscheine jeweils UUID-sortiert und lädt deren Beziehungen frisch.
`bereite_buchungen_vor` ermittelt dieselbe Menge für Batch-Stornos vor weiteren Sperren.
Ein zwischenzeitlicher Besitzer- oder Zahlungswechsel kann einen internen Retry der gesamten
Transaktion erfordern; zusätzliche Kunden dürfen nicht nachträglich gesperrt werden.
`gutschein_sperren.fuehre_transaktion_aus(db, vorgang)` wiederholt ausschließlich
`GutscheinVorbereitungWiederholen` bis zu drei Mal nach vollständigem Rollback und committet
den erfolgreichen Versuch. Die fünf Adminaktionen Buchungsstorno, Kulanz, Sperranlage,
verdrängende Daueranlage und Dauerbeendigung laden ihre Modelle und Entscheidungen je
Versuch neu. Reservierungsverfall, Gutscheinverfall und Kaufabbruch entdecken ihre ganze
Batchmenge erneut. Erschöpfte Adminversuche ergeben einen deutschen Formularfehler;
Jobs geben nach Rollback den Fehler an ihre bestehende Protokollierung weiter.
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
nur an den Käufer. Allgemeines Bezahltsetzen sowie Voll-/Teilstorno sind für Kauf-,
Einlösungs- und Geldanteilbelege einschließlich ihrer Gegenbelege zentral gesperrt.
Auch ältere Gegenbelege ohne Rollenmarker werden über die Belegherkunft erkannt.
Die Rechnungsseite verweist auf Gutscheinrückgabe oder koordinierten Buchungsstorno.

`gutschein_kauf.anfragen` friert Modell, Minuten/Wert, Kaufpreis, Käufergruppe, Steuersatz,
Mitgliedsvoraussetzung, Gültigkeitsdauer und Zahlungsdeadline ein. Guthaben wird beim Kauf
nicht verrechnet. Der Kanal übergibt seine vertrauenswürdige Konto-ID; nach der Kundensperre
vergleicht der Kaufdienst sie mit der frisch geladenen Portalbindung, bevor er eine
Bezahlsitzung erzeugt. Ein während des Wartens neu verknüpftes Konto berechtigt das alte
Konto nicht mehr zum Kauf. Direkte interne Dienstaufrufer behalten den bisherigen Vertrag.
`online_buchung.zahlung_eingegangen` verifiziert Anbieter und Zahlungsstatus,
entdeckt den Kaufgutschein vor den Vorgangslocks und übergibt den vollständigen Sperrkontext.
Bei `now >= kauf_reserviert_bis` entsteht auch ohne vorherigen Job kein aktiver Gutschein.
Unterzahlung bricht den Kauf ab und schreibt den Eingang einmal als Käuferguthaben gut;
Überzahlung aktiviert einen Gutschein und schreibt nur den Überschuss gut. Nach Abbruch
eingehende Zahlungen bleiben Käuferguthaben mit Betreiberhinweis. Der eigene Kaufabbruchjob
läuft jede Minute in einer getrennten Transaktion vom Buchungsverfall.
`kaufdatum` und Gültigkeitsbeginn stammen vom tatsächlichen Abschluss, `ausgestellt_am`
vom Kaufwunsch. Callback-Fristentscheidung, Zahlungseingangszeit und Kaufdatum verwenden
denselben einmal frisch nach vollständiger Sperrvorbereitung gelesenen Fachuhrzeitpunkt;
ein Fristwechsel während der Beleganlage erzeugt keine widersprüchliche Entscheidung.
Die Checkout-URL steht auf `Zahlung`; offene oder abgebrochene Kaufcodes
erscheinen weder in Antworten, Code-Mails, Übersicht, CSV noch in Audit-Snapshots.
Kaufrechnung (`beim_kauf`) bzw. Kaufbeleg im GB-Kreis (`bei_einloesung`, ohne Steuerausweis)
und Code-Mails werden nach Commit erzeugt/versandt. Der Käufer erhält den Beleg;
ein Geschenkempfänger bekommt nur den Code mit Gruppen- und Gültigkeitshinweis.
Versand-/PDFfehler einzelner Nachläufe verhindern die anderen Empfänger nicht;
es gibt keine neue automatische Zustellgarantie.


`gutscheine.gib_zurueck(db, g, *, grund, admin_user_id)` gibt ausschließlich vollständig
ungenutzte gekaufte Gutscheine zurück. Keine reservierte/bestätigte Einlösung darf bestehen;
Wertrest muss dem Nennwert entsprechen, frühere Wertgutschriften bleiben Verbrauch.
Kostenfrei zurückgenommene Einheiten und abgebrochene Reservierungen sind wieder ungenutzt.
Käufer, Inhaber und historische Einlöser werden vor Zahlungen/Gutschein/Belegen gesperrt;
Besitzwechsel führen zu höchstens drei vollständigen Transaktionsversuchen.
Die Rückgabe erzeugt einen exakten Kaufgegenbeleg, dauerhafte Rückgabemarkierung mit
Betrag/Grund/Käufer und Audit. `GutscheinRueckgabe` liefert optionalen Gegenbeleg und
`manuell_zu_erstatten`; die Zahlung erfolgt manuell an den ursprünglichen Käufer.
Kein Banktransfer und kein Kundenguthaben. Wiederholung liefert null Euro ohne weiteren Audit;
Reaktivierung ist dauerhaft verboten. Schon vollständig korrigierte Kaufbasis erzeugt keinen
weiteren Gegenbeleg und keine weitere Erstattungsanweisung; Teilkorrekturen verhindern Rückgabe.
Die steuerfreie Mehrzweck-Kaufbasis wird ohne zweiten Umsatzsteuerausweis storniert.
Die Adminaktion erfordert Rolle und CSRF. Der optionale neue Rückgabegegenbeleg wird nach
Commit über `belege_versenden` als PDF archiviert und ausschließlich an den ursprünglichen
Käufer versandt. Wiederholungen ohne neuen Gegenbeleg starten keinen Nachlauf;
Nachlauffehler lassen die Rückgabe bestehen. Öffentliche Rechnungsdienste prüfen zentral
`pruefe_allgemeinen_rechnungsvorgang`; ausschließlich koordinierte Fachpfade nutzen die
privaten Korrekturhilfen. Keine Ausnahme aus HTTP-Daten. Freicode-Einlösungslimits liegen
zwischen 1 und 2147483647; größere Werte ergeben einen deutschen Formularfehler.

## Gutschein-Lesestand

`konto:<id>` enthält ausschließlich aktuell gültige aktive Gutscheine des Inhabers mit positivem
Restwert oder verbleibender Nutzung. Die Fachuhrprüfung erfolgt beim Erzeugen, unabhängig vom
Nachtjob. Besitzwechsel markieren Käufer sowie alten und neuen Inhaber. Der Käufer behält seine
Kaufbelege; Einlöser erhalten durch den Bestand keine Rechte auf fremde PDFs.
Offene Kaufwünsche stehen ausschließlich beim Käufer und nur vor der gespeicherten Zahlungsfrist,
mit persistiertem Zahlungslink und ohne Code. Das Tarifdokument enthält das aktuelle Angebot;
`gutschein_kauf` im Konto berücksichtigt Käufergruppe, positiven Einheitspreis und Berechtigung.
Der Hauptsystem-Kanal prüft den Kauf weiterhin eigenständig. Angebotsänderungen markieren
Tarifdokument und Konten. Alte Dokumente verwenden leere Bestände und `gutschein_kauf=False`.
