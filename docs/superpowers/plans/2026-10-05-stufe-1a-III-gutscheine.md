# Stufe 1a-III: Gutscheine und Freischaltcodes – aktualisierter Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gutscheinkauf und Freischaltcodes im Hauptsystem implementieren, atomar in Einzelbuchungen einlösen und bei Absage, Verfall oder Rückgabe finanziell korrekt behandeln. Der Portal-Kanal und signierte Lesestand werden erweitert; die vollständige Verkaufsoberfläche folgt mit Stufe 2.

**Architecture:** Gutscheinbestand, Einlösungen und Kaufzahlungen bleiben getrennte Zustände. Ein eigener Gutschein-Belegdienst trennt Kaufbelege, Einlösungsbelege und den monetären Buchungsrest. Alle beteiligten Kunden werden vor Zahlung, Gutscheinen, Buchungen und Rechnungen vollständig gesperrt; Belege und Guthaben entstehen in derselben Transaktion. Versand erfolgt über den bestehenden Nach-Commit-Nachlauf.

**Tech Stack:** Bestehender Python-3.12-/FastAPI-/SQLAlchemy-2-/PostgreSQL-/Alembic-/Jinja2-/WeasyPrint-Stack, keine neue Abhängigkeit.

**Spec:** `docs/superpowers/specs/2026-09-05-beachhub-design.md`, A-GUT-1 bis -9, A-STORNO-6/-7, A-KUND-7, A-ZAHL, A-MAIL-2, A-ADM-8, § 3.14, § 5 und § 8.1.

**Baseline:** `main` und `origin/main` auf `9a4f1d0`, 929 bestandene Tests. Migrationen enden bei `0014_saison_und_korrektur`; die neue Revision ist `0015_gutscheine` mit `down_revision = "0014"` (Revisionskennung vor Umsetzung im tatsächlichen Migrationsmodul bestätigen).

**Status:** Planung, noch nicht implementiert. Dieser Plan ersetzt für die Ausführung den Plan `2026-10-03-stufe-1a-III-gutscheine.md`. Dessen Codeblöcke bleiben historische Entwürfe und dürfen die folgenden Verträge nicht überschreiben.

## Zweck, Umfang und bestätigte Entscheidung

Der Betreiber verkauft Gutscheine an Portalkonten und stellt kostenlose Aktionscodes aus. Kunden können Codes verschenken und unmittelbar während der Buchung einlösen. Preis und Steuerregel werden beim Kauf eingefroren; aktuelle Tarife bestimmen lediglich die abgedeckte Buchungsleistung. Eine gescheiterte oder abgelaufene Reservierung darf weder Codes noch Geld verlieren.

**Nutzerentscheidung vom 05.10.2026:** Bei kostenfreiem Storno eines **Wertgutscheins** wird der verbrauchte Wert dem Einlöser als **Kundenguthaben** gutgeschrieben, gemäß A-GUT-6. Der Wert kehrt dabei nicht zusätzlich auf den Gutschein zurück. Einheitsgutscheine und Freischaltcodes werden wieder freigegeben und erzeugen niemals Guthaben. Die frühere Planabweichung A-5 entfällt.

Nicht enthalten: echter Zahlungsanbieter, Gastkauf, neue steuerrechtliche Festlegungen, rückwirkende Reparatur historischer Vorgänge und eine neue Portal-Verkaufsoberfläche. Beide Gutscheinmodelle und beide bereits spezifizierten Steuervarianten werden technisch berücksichtigt; die vom Betreiber bestätigte Vorgabe bleibt `einheit` / `beim_kauf`.

## Global Constraints

- Geld und Steuersätze ausschließlich `Decimal`; Cent mit `ROUND_HALF_UP`. Historische Rechnungsbeträge und archivierte PDF-Bytes bleiben unverändert.
- Das Hauptsystem entscheidet; Kundenzuordnung aus `portal_konto_id`, niemals aus einer behaupteten Portal-Kunden-ID. Deutsche Jinja-Oberfläche, keine Inline-Skripte und kein Frontend-Build.
- Alle fachlichen Änderungen auditieren. Gutschein-Audits nennen Akteur, betroffene Einlösung und Beleg; echte Statuswechsel bleiben sichtbar, künstliche Ergänzungsaudits entfallen.
- Kein Gutscheinwert als Kundenguthaben ohne passenden Korrekturbeleg; Ausnahme: Rückbuchung einer noch nicht abgeschlossenen Reservierung stellt lediglich den ursprünglichen Bestand wieder her.
- Versand, PDF-Erzeugung und Betreiber-Alarme nach Commit. Fehler eines Belegs dürfen den folgenden Nachlauf nicht verhindern; daraus folgt keine neue SMTP-Zustellungsgarantie.
- Kauf- und Einlösungsbelege bekommen vollständige Verknüpfungen bereits im Anlage-Audit. Abrechnungsregeln des aktuellen `9a4f1d0` erhalten: Forderung vor Korrektur lesen, nur bezahlten Anteil erstatten, frühere Korrekturen nicht erneut berechnen.
- Eine Migration; Tests mit realem PostgreSQL nur gegen `beachhub_test` / `beachhub_portal_test`. DB-Läufe serialisieren. Kein Anwendungsserver und keine Produktivmigration zur Verifikation.
- In Worktrees jeden Test mit explizitem `PYTHONPATH` auf deren `shared`, `core`, `portal`, `hall` ausführen; bestehende Editable-Installationen können sonst den Hauptcheckout testen.
- Keine automatische Commit-Attribution aus historischen Claude-Vorgaben. Deutsche Commitnachrichten. Merge und Push erst entsprechend der tatsächlichen Nutzerfreigabe; kein automatisches Löschen fremder Worktrees.

## Finanzielle und transaktionale Verträge

### Belegtrennung und Geldfluss

| Vorgang | Beleg | Kostenfreier bestätigter Buchungsstorno |
| --- | --- | --- |
| Einheitsgutschein, Steuer beim Kauf | Kaufrechnung an Käufer; keine Einlösungsrechnung | Code mit ursprünglicher Gültigkeit zurück; Kaufrechnung bleibt bestehen |
| Einheitsgutschein, Steuer bei Einlösung | Kaufbeleg ohne Steuer; Einlösungsrechnung an Einlöser über den Kaufpreis | Einlösungsbeleg korrigieren, Code zurück, kein Guthaben aus Gutschein |
| Wertgutschein, Steuer beim Kauf | Kaufrechnung an Käufer; Verbrauch besitzt anteilige Kaufbelegbasis | Anteilig Kaufrechnung korrigieren; Guthaben an Einlöser, nicht Käufer; Gutscheinrest nicht erhöhen |
| Wertgutschein, Steuer bei Einlösung | Kaufbeleg ohne Steuer; Einlösungsrechnung über verbrauchten Betrag | Einlösungsbeleg korrigieren; Guthaben an Einlöser; Gutscheinrest nicht erhöhen |
| Freischaltcode | Kein Geld- oder Steuerbeleg | Einlösung zurücknehmen; niemals Guthaben |
| Geld-/Guthabenanteil der Buchung | Separate normale Buchungsrechnung | Bestehender Korrektur-/Guthabenpfad nur für diesen Anteil |

`Buchung.preis` bleibt der Tarifwert. Die bestehende `Buchung.rechnung_position_id` verweist ausschließlich auf die Geldanteil-Rechnung, sofern vorhanden. Gutschein-Einlösungspositionen haben `buchung_id=None` und sind über `GutscheinEinloesung` verknüpft; damit verletzen mehrere Einlösungen nicht den bestehenden Unique-Index auf Rechnungspositionen mit Buchungsbezug.

Jede relevante Rechnung erhält `gutschein_rolle` (`kauf`, `einloesung`, `geldanteil` oder `None`). Generische Vollstorno-, Teilstorno- und Bezahltsetzen-Aktionen für Gutscheinbelege bzw. Gutscheinbuchungsrechnungen werden zentral abgewiesen und in der UI auf den passenden Fachvorgang verwiesen. Ein reiner Rechnungsstorno darf niemals Gutscheinwert in zusätzliches Guthaben verwandeln oder einen Gutschein ohne Bestandsprüfung zurückgeben. Der koordinierte Gutschein-Stornodienst bekommt einen ausschließlich internen Geldrest-Korrekturweg: Den bestehenden bezahlten-Anteil-Algorithmus aus `storno.gutschreiben_positionen` und die Belegerzeugung unterhalb des allgemeinen Aktionsguards als gemeinsame private Hilfen extrahieren. Allgemeine Dienste prüfen den Guard vor ihrem Aufruf; der Gutschein-Fachdienst ruft die privaten Hilfen erst nach vollständiger Gutscheinvorbereitung auf. Keine Umgehungsparameter in HTTP oder öffentlichen Finanzdiensten. Die normalen Rechnungsfälle bleiben unverändert.

Für `wert` / `beim_kauf` reicht `rechnungen.korrigiere` über ganze Positionen nicht aus. `services/gutschein_belege.py` erhält deshalb einen ausdrücklich begrenzten Betragskorrekturvertrag:

- Einlösung speichert Ursprungposition, verbrauchte Brutto-/Netto-/USt-Basis und einen einmaligen Korrekturbelegbezug.
- Unter gesperrtem Gutschein und Ursprungbeleg wird die kumulativ schon korrigierte Basis frisch gelesen. Neue Korrektur darf den verbleibenden Ursprungbetrag nicht überschreiten.
- Nettoanteile werden aus der **kumulativen** Bruttokorrektur anteilig am ursprünglichen Netto berechnet; der neue Nettoanteil ist die Differenz zum bereits korrigierten Netto. USt ist die Differenz von Brutto und Netto. Die vollständige Korrektur ergibt exakt das ursprüngliche Netto/USt, ohne nachträglich unabhängige Teilrundungen zu summieren.
- `Posten` bekommt optionale `netto_vorgegeben` / `ust_vorgegeben`. Beide müssen gemeinsam vorhanden sein, Centbeträge sein und zusammen `brutto` ergeben. Bestehende Aufrufer behalten ihre bisherige Rundung. Der Gutschein-Belegdienst verwendet diese exakten Gegenbeträge.
- Teilkorrektur markiert nicht die ganze Ursprungposition als erledigt. Erst vollständig korrigierte Basis setzt deren Korrekturmarker und ggf. Rechnungsstatus `storniert`. Gutschein-Finanzvorgänge verwenden keine generische Auswahl ganzer Kaufpositionen.
- Gegenbeleg gehört zum Kunden des Ursprungbelegs. Der separate Guthabeneintrag gehört zum Einlöser. Käufer, Einlöser und bisheriger Inhaber werden vorab gemeinsam gesperrt; der Einlöser bekommt keinen Zugriff auf fremde Kauf-PDFs.

**Manuelle Rückgabe eines ungenutzten Kaufgutscheins:** Gutschein sperren, passenden Kaufgegenbeleg erzeugen bzw. Kaufbeleg stornieren, extern auszuführenden Erstattungsbetrag auditieren. **Kein zusätzliches Kundenguthaben.** Nicht `rechnungen.storniere` verwenden, weil dieser Dienst seit `9a4f1d0` automatisch den bezahlten Anteil als Guthaben zurückgibt. Rückgabe ist dauerhaft gekennzeichnet und wird weder durch erneuten POST noch durch Reaktivierung eines verfallenen Codes rückgängig gemacht. Die Aktion löst keinen Banktransfer aus; die Verwaltung benennt die noch manuell auszuführende Zahlung.

### Sperren, Savepoints und Wiederholungen

Vorgangsweit gilt: vollständige Kundenmenge UUID-sortiert → bereits vorhandene Zahlungszeilen UUID-sortiert → gesamte Gutscheinmenge UUID-sortiert → weitere Buchungs-/Feld-/Rechnungs-/Nummernkreissperren gemäß bestehendem Ablauf. Neue Zahlungszeilen können nach der Vorbereitung eingefügt werden; keine weiteren Kundensperren nach Feld oder Nummernkreis erwerben.

Kundenmenge enthält Akteur, Käufer, bisherigen Inhaber und Guthabenbegünstigten aller betroffenen Gutscheine. IDs dürfen zuerst ohne Schreibsperren ermittelt werden; nach Sperrerwerb `populate_existing` / Refresh und Beziehungen revalidieren. Ergibt sich eine zusätzliche Kunden-ID, gesamten Vorgang vor externen Effekten zurückrollen und neu vorbereiten, nicht spätere Kunden nachsperren. Gutscheinverwaltung, Kaufcallback, Abbruch, Sperranlage, verdrängende Daueranlage und Batch-Storno verwenden dieselbe Vorbereitung für die vollständige Menge.

Kunden-/Ratelimit-Sperren entstehen **vor** dem Buchungs-Savepoint. Eine abgelehnte Buchung rollt Reservierung und Einlösungen zurück, der Fehlversuch wird anschließend in der äußeren Transaktion gespeichert. Doppelte Bearbeitung derselben Kanal-Anfrage zählt nicht erneut; verschiedene abgelehnte Anfragen tun es. Eine neue codehaltige abgelehnte Buchungsanfrage zählt genau einen Fehlversuch, auch bei mehreren ungültigen Codes oder einer nach Normalisierung leeren Eingabe. Zehn solche Fehlversuche je Konto in 60 Minuten, danach `zu_viele_versuche`; IP-Limit bleibt Aufgabe der Portaloberfläche.

Kaufwunsch speichert Preis, Modell, Gruppe, Steuervariante, Gültigkeitsdauer und Zahlungsablauffrist. Callback prüft die gespeicherte Frist selbst – auch wenn der Job noch nicht lief. Keine Fristberechnung aus heute geänderten Einstellungen. Zahlung nach Abbruch/Frist wird genau einmal Käuferguthaben mit Betreiberhinweis, niemals Aktivierung eines schon veröffentlichten oder abgebrochenen Codes. Vor Bestätigung wird kein Code veröffentlicht.

Reservierte Einlösungen sind explizit `reserviert`; erst abgeschlossene Buchung macht sie endgültig. Abbruch einer noch unbezahlten Reservierung gibt den ursprünglichen Gutscheinbestand zurück, einschließlich Wertgutscheinrest; er erzeugt keine zusätzliche Wertgutschein-Gutschrift. Bestätigter kostenfreier Storno folgt dagegen der Tabelle. Bereits eingegangene monetäre Zahlungen laufen weiter über die bestehenden Verfall-/Spätzahlungsregeln.

## Review Focus

1. Derselbe Code bei zwei Kunden, umgekehrte Codelisten oder gleichzeitige Rückgabe: genau ein zulässiger Verbrauch, keine Deadlocks und kein Gutschein plus zusätzliche Rückzahlung. Tests in Tasks 3, 5 und 8.
2. Wertgutschein 50 EUR, Verbrauch 30 EUR durch einen anderen Kunden, mehrfacher Storno: exakt 30 EUR Einlöserguthaben, Gutscheinrest 20 EUR, Käuferguthaben null, ein Gegenbeleg; Centreste bei vielen Kleinteilen exakt. Tests in Task 6.
3. Einheitsgutschein für 26 EUR deckt 30 EUR Tarif plus 15 EUR Geldrest: Code zurück, exakt 15 EUR Geldgutschrift, bei Mehrzweck ausschließlich 26 EUR steuerlicher Gutschein-Gegenbeleg. Tests in Tasks 5 und 6.
4. Zahlung nach gespeicherter Kaufablauffrist, Callback doppelt oder gleichzeitig mit Job: keine Aktivierung, genau eine Guthabenbuchung, kein doppelter Kaufbeleg; geänderte Einstellungen verschieben nichts. Tests in Task 7.
5. Fehlversuche nach Savepoint-Rollback und Besitzwechsel im signierten Lesestand: Rate-Limit bleibt bestehen, bisheriger Inhaber wird invalidiert, offene Kaufcodes bleiben geheim und alte Dokumente aktivieren keinen Verkauf. Tests in Tasks 3 und 9.

## Aufgaben und Schnittstellen

Die Tasks werden nacheinander umgesetzt und einzeln geprüft. Jede neue Verhaltensprüfung läuft vor der Implementierung rot, danach grün. Kleine verwandte Änderungen sind innerhalb eines Tasks gebündelt; komplette Codekörper aus dem historischen Plan sind keine Ausführungsvorgabe.

### Task 1: Einstellungen und rückwärtskompatible Kanalverträge

**Files:** `core/beachhub_core/services/konfiguration.py`, `routes/stammdaten.py`, `templates/stammdaten/konfiguration.html`, `shared/beachhub_shared/kanal.py`; Tests `core/tests/test_konfiguration.py`, `test_ui_stammdaten.py`, `shared/tests/test_kanal.py`.

**Interfaces:** `Obergrenze(str).zahl -> int | None`; `Betragsliste(str).betraege -> list[Decimal]`; `AUSWAHL`, `GUTSCHEIN_ANGEBOT`; `BuchungAnfragen.gutschein_codes: list[str]` mit Default leer, maximal zehn Codes à 1–40 Zeichen; `GutscheinKaufen(empfaenger_email: str | None, betrag: Decimal | None)`; optionale Antwortfelder `gutschein_verrechnet`, `gutschein_wieder_gueltig`.

Vorgaben: `gutschein_modell=einheit`, `gutschein_minuten=120`, `gutscheine_je_buchung=volle_einheiten`, Preise je Gruppe `0.00` (kein Verkauf), `gutschein_betraege="25,00; 50,00; 100,00"`, `mitgliedsgutschein_nur_mitglieder=True`, `gutschein_steuer=beim_kauf`, `gutschein_gueltig_tage=730`, `freicode_gueltig_tage=90`, `rechnungskunden_gutscheinkauf=True`, `gutschein_gastkauf=False`. Listenbeträge müssen positiv und endlich sein, Gruppenpreise endlich und nicht negativ; Dauer/Obergrenzen müssen positiv sein; fehlende Preise deaktivieren den Verkauf, keine stillen Gratis-Kaufgutscheine. `gruppiert` stellt bool/auswahl/text konsistent dar.

- [ ] RED: `test_gutschein_vorgaben`, `test_betragsliste_endlich_positiv`, `test_alte_buchungsnutzlast_bleibt_gueltig`; konkrete Assertions:
  ```python
  assert vorgaben.gutschein_minuten == 120
  assert liste.betraege == [Decimal("25.00"), Decimal("50.00"), Decimal("100.00")]
  assert alte_anfrage.gutschein_codes == []
  ```
- [ ] Neue Einstellungen und Verträge implementieren; Änderungen markieren betroffene Tarif-/Kontodokumente.
- [ ] Shared- und betroffene Core-Tests grün, Ruff/mypy; Commit `feat(shared): Gutscheinverträge und Einstellungen`.

### Task 2: Schema 0015, Nummernkreise und Belegbasis

**Files:** neu `core/beachhub_core/models/gutscheine.py`, `core/alembic/versions/0015_gutscheine.py`, `core/tests/hilfen_gutschein.py`; ändern `models/rechnungen.py`, `models/__init__.py`, `services/rechnungen.py`; Tests `test_migrationen.py`, `test_rechnungen.py`, neu `test_gutscheine.py`.

**Interfaces:** `Gutschein`, `GutscheinEinloesung`, `GutscheinFehlversuch`; `Nummernkreis(kreis, jahr)`; `naechste_nummer(db, jahr, kreis="rechnung") -> str`; `Posten` mit optionalen exakten Netto-/USt-Beträgen aus dem Belegvertrag. `_neue_rechnung` erhält optionale Rollen-/Ursprungverknüpfungen bereits im Konstruktor/Audit.

Gutschein speichert die bisherigen Spec-Felder und die Kauf-Snapshots für Gruppe, Preis, Modell, Minuten, Mitgliedsvoraussetzung und Steuer sowie Kaufdatum, `kauf_reserviert_bis`, `gueltig_tage_snapshot`, `zurueckgegeben_am`, Rückgabebetrag/-grund. Während eines offenen Kaufs darf `gueltig_bis` fehlen; beim Abschluss wird es aus Abschlussdatum und gespeichertem Zeitraum gesetzt. Einlösung speichert Zustand `reserviert/bestaetigt/rueckgaengig`, Deckungsbetrag, Ursprungposition, Brutto-/Netto-/USt-Belegbasis, Korrekturbelegbezug und Rückabwicklungsart `gutschein/guthaben`. Eindeutiger Korrekturbezug je Einlösung; Geldfelder und Zustände durch DB-Constraints schützen. `Rechnung.gutschein_rolle` bleibt für bestehende Rechnungen nullable.

Rechnungskreis bleibt ohne Präfix, Kaufbelege `GB-JJJJ-NNNNN`, Rechnungsnummer maximal 20 Zeichen. Migration übernimmt sämtliche vorhandenen Nummernkreisstände in `kreis="rechnung"`, ohne Nummern neu zu vergeben. Downgrade prüft vorhandene Gutscheinbelege und scheitert mit klarer Meldung statt inkompatible Daten zu löschen.

- [ ] RED: Upgrade aus 0014 mit bestehendem Jahreszähler; `test_migration_0015_erhaelt_nummernkreis`, `test_exakte_gegenbetraege`, `test_gutschein_anlageaudit_vollstaendig`.
  ```python
  assert erste_normale_nummer == "2027-00042"  # Ausgangszähler 41
  assert erster_kaufbeleg == "GB-2027-00001"
  assert gegen.netto + gegen.ust == gegen.brutto
  ```
- [ ] Modelle/Migration und exakte Betragsvalidierung implementieren. Fixture `gwelt` enthält feste Kundengruppen wie Migration 0010, Stundenpreis 15 EUR und Käuferpreise 26/30 EUR; Fabriken erzeugen vollständige Kauf-/Einlösungssnapshots.
- [ ] Migrationstest, vorhandene Rechnungstests und Ruff/mypy grün; Commit `feat(core): Gutscheinmodell und Belegbasis`.

### Task 3: Codes, Vorgangssperren und Missbrauchsschutz

**Files:** neu `services/gutschein_codes.py`, `services/gutschein_sperren.py`, `services/gutschein_typen.py`; Tests `test_gutscheine.py`, neu `test_gutschein_parallel.py`; ändern bestehende Batch-Einstiege in `services/sperren.py`, `dauerbuchungen.py`, `online_buchung.py` nur zur Vorgangsvorbereitung.

**Interfaces:** `normalisiere(roh: str) -> str`, `anzeige(code: str) -> str`, `neuer_code(db: Session) -> str`; `bereite_vorgang_vor(db, *, kunde_ids: Iterable[UUID], gutschein_ids: Iterable[UUID], zahlung_ids: Iterable[UUID] = ()) -> GutscheinSperrkontext`; Kontext enthält frisch geladene Gutscheine/Zahlungen, vollständige Kundenmenge und geprüfte Besitzerbeziehungen. `merke_fehlversuch(db, kunde_id: UUID) -> None`, `pruefe_rate_limit(db, kunde_id: UUID) -> None`.

Interne typisierte Ergebnisverträge in `gutschein_typen.py`: `GutscheinSperrkontext(kunde_ids: frozenset[UUID], gutscheine: dict[UUID, Gutschein], zahlungen: dict[UUID, Zahlung])`; `GutscheinDeckung(betrag: Decimal, einloesungen: list[GutscheinEinloesung])`; `GutscheinBuchungsbelege(geldrechnung: Rechnung | None, gutscheinbelege: list[Rechnung])`; `GutscheinRueckabwicklung(freigegebene_gutscheine: list[Gutschein], guthaben_betrag: Decimal, korrekturbelege: list[Rechnung])`; `GutscheinRueckgabe(korrekturbeleg: Rechnung | None, manuell_zu_erstatten: Decimal)`. Leere Listen und Geldbetrag null repräsentieren eine idempotente Wiederholung, ohne zusätzliche Belege oder Erstattung.

Codes aus 13 zufälligen Zeichen eines dokumentierten Alphabets ohne 0/O/1/I, mindestens 60 Bit Entropie, DB-Unique-Constraint. Kollision innerhalb eines separaten Savepoints erneut generieren, ohne bereits vorbereitete Vorgangslocks zu verlieren. Keine gültigen Codes, Checkout-Links oder Schlüssel in Diagnose-Logs.

- [ ] RED: `test_zwei_kunden_selber_code`, `test_umgekehrte_codeliste_kein_deadlock`, `test_savepoint_ablehnung_zaehlt_fehlversuch`, `test_besitzerwechsel_erfordert_neue_vorbereitung`.
  ```python
  assert bestaetigte_einloesungen == 1
  assert abgelehnte_buchungen_hinterlassen_reservierungen == 0
  assert elfter_versuch.grund == "zu_viele_versuche"
  ```
- [ ] Codes und Sperrvorbereitung implementieren; bei unbekannter zusätzlicher Kunden-ID klarer interner Retry des gesamten Vorgangs, keine Nachsperre. Alle Batch-Kunden/Zahlungen/Gutscheine vor der ersten Korrektur vorbereiten.
- [ ] Echte Zwei-Session-Tests mit beobachteten Locks und bestehende Batch-/Zahlungsregressionen grün; Commit `feat(core): Gutscheintransaktionen und Versuchslimit`.

### Task 4: Freischaltcodes, Verwaltung und Ablauf

**Files:** neu `services/gutscheine.py`, `routes/gutscheine.py`, `templates/gutscheine/liste.html`, `detail.html`, `freicodes.html`, `test_ui_gutscheine.py`; ändern `main.py`, `navigation.py`, `templating.py`, `jobs.py`; Tests `test_gutscheine.py`, `test_jobs.py`, `test_ui_navigation.py`.

**Interfaces:** `stelle_freicodes_aus(db, *, anzahl: int, gueltig_bis: date | None, max_einloesungen: int = 1, feld_id: UUID | None = None, zeitraum_von: date | None = None, zeitraum_bis: date | None = None, serie: str = "", notiz: str = "", admin_user_id: UUID | None) -> list[Gutschein]`; `sperre`, `setze_gueltigkeit`, `verfalle -> int`, `serie_csv -> str`. Änderungsaudit invalidiert Käufer, alten und neuen Inhaber.

1–1000 Codes pro Serie, frei/einheit/keine Steuer, Vorgabe 120 Minuten, eine Einlösung, 90 Tage. Freicodes bleiben Einheiten auch bei globalem Wertmodell. CSV-Injection-Schutz aus bestehendem Export verwenden. Sperre und Reaktivierung ändern niemals bereits verbrauchte Restwerte; zurückgegebene Kaufgutscheine sind nicht reaktivierbar. Nachtjob täglich 00:20 ausdrücklich `Europe/Berlin`.

- [ ] RED: Ausgabegrenzen, CSRF/lesende Rolle, Geldwert null, Rückgabe-Reaktivierungsverbot, UTC-Server/Winter/Sommer.
  ```python
  assert (code.art, code.modell, code.steuer, code.max_einloesungen) == ("frei", "einheit", "keine", 1)
  assert naechster_lauf_berlin.hour == 0 and naechster_lauf_berlin.minute == 20
  ```
- [ ] Dienste, UI, CSV und Job implementieren; statische Routen vor UUID-Detailroute.
- [ ] UI-/Admin-/Jobtests grün; Commit `feat(core): Freischaltcodes verwalten`.

### Task 5: Atomare Einlösung und getrennte Buchungsbelege

**Files:** neu `services/gutschein_einloesung.py`, `services/gutschein_belege.py`, `test_gutschein_einloesung.py`; ändern `services/tarife.py`, `online_buchung.py`, `anfragen.py`, `benachrichtigung.py`, `templates/mail/buchung_bestaetigt.txt`.

**Interfaces:** `preise_je_slot(db, *, feld_id, beginn, ende, kundengruppe_id) -> list[tuple[Slot, Decimal]] | None`; `loese_ein(db, buchung: Buchung, kunde: Kunde, codes: Sequence[str], *, sperrkontext: GutscheinSperrkontext) -> GutscheinDeckung`; Deckung enthält Tarifdeckung und vollständige Einlösungen. `bestaetige_einloesungen(db, buchung) -> None`; `rechnung_fuer_buchung(db, buchung, *, quelle: str) -> GutscheinBuchungsbelege` mit optionaler Geldrechnung und Liste der Gutschein-Einlösungsbelege. Rückgaben enthalten sämtliche Nachlauf-Beleg-IDs, nicht nur einen einzelnen Rechnungszeiger.

Jeden Code prüfen: Zustand, Einlösetag/Gültigkeit, Käufergruppen-Snapshot bei `beim_kauf`, Mitgliedsvoraussetzung ausschließlich bei `beim_kauf` anhand des gespeicherten Mitgliedsgutschein-Snapshots (bei `bei_einloesung` keine A-GUT-1b-Sperre), Feld-/Terminfenster, Nutzungszahl und Rest. Normalisierte Dubletten einmal werten. Einheiten zuerst, Wertgutscheine danach, anschließend Kundenguthaben und Onlinezahlung nur für Rest. Beispiele bei 120 Minuten: 1 h ein Gutschein, 3 h ein Gutschein plus eine Stunde, 4 h zwei Gutscheine. Belegbasis der Mehrzweck-Einheit ist Kaufpreis 26 EUR, nicht gedeckter Tarifwert 30 EUR. Mischungen unterschiedlicher Steuer-Snapshots erzeugen getrennte Belege mit nachvollziehbaren Einlösungsbezügen.

- [ ] RED: `test_einheit_3h_26_euro_gutschein_15_euro_rest`, `test_4h_zwei_einheiten`, `test_ungueltiger_zweiter_code_rollt_alles_zurueck`, `test_unterschiedliche_kaufsteuer_snapshots`.
  ```python
  assert (deckung.betrag, checkout_betrag) == (Decimal("30.00"), Decimal("15.00"))
  assert mehrzweck_beleg.brutto == Decimal("26.00")
  assert fehlgeschlagener_vorgang.einloesungen == []
  ```
- [ ] Einlösung und Belegfactory integrieren; Callbacks bereiten Gutscheine vor Bestätigung vor; alte Gutscheinzahlungen zählen nicht als erneut eingegangenes Bargeld.
- [ ] Einlösungs-, Tarif-, Online- und Paralleltests grün; Commit `feat(core): Gutscheine in Buchungen einloesen`.

### Task 6: Storno, Kulanz und Reservierungsverfall

**Files:** `services/gutschein_einloesung.py`, `gutschein_belege.py`, `storno.py`, `online_buchung.py`, `benachrichtigung.py`, `routes/belegung.py`, `jobs.py`; neu `templates/mail/gutschein_wieder_gueltig.txt`; Tests `test_gutschein_einloesung.py`, `test_gutschein_parallel.py`, `test_korrektur.py`, `test_storno.py`, `test_jobs.py`.

**Interfaces:** `storno_rueckgaengig(db, buchung, *, grund: str, quelle: str, admin_user_id: UUID | None = None, sperrkontext: GutscheinSperrkontext) -> GutscheinRueckabwicklung`; Ergebnis enthält freigegebene Codes, Guthabenbetrag und alle Gegenbelege. `korrigiere_wertanteil(db, einloesung, *, grund, quelle, admin_user_id=None) -> Rechnung` nach dem kumulativen Betragsvertrag. Kein eigenständiger Commit im Dienst.

Vor bestehendem Rechnungskorrektur-/Nummernkreispfad gesamte Gutscheinmenge sperren. Beim Einheitsstorno `inhaber_kunde_id = einloesung.kunde_id` setzen und bisherigen sowie neuen Inhaber für den Lesestand markieren. Einheits-/Freicode-Rückgabe korrigiert nur ggf. Einlösungssteuer, nicht monetären Gutscheinwert. Wert-Rückgabe korrigiert exakt einmal den verbrauchten Anteil und schreibt ihn dem Einlöser gut. Geldrest-Korrektur separat; bei einem 26-EUR-Einheitsgutschein und 15-EUR-Rest gibt es niemals 41/45 EUR Kundenguthaben. Kostenpflichtige Absage verändert Gutscheinbestand nicht. Noch reservierte, nicht bestätigte Einlösungen werden bei Verfall lediglich aufgehoben. Die ursprüngliche Gültigkeit bleibt erhalten; ein inzwischen verfallener oder gesperrter Code wird nicht automatisch nutzbar. „Wieder gültig“ nur bei tatsächlich aktueller Einlösbarkeit melden. Nachlauf sendet sämtliche Gegenbelege und zutreffende Rückgabe-/Guthabenhinweise nach Commit.

- [ ] RED: Storno/Kulanz/Wiederholung, A/B-Käuferwechsel, Centteile, gemischte Zahlung, Reservierungsverfall vs. tatsächlicher später Zahlung.
  ```python
  assert (einloeser.guthaben, kaeufer.guthaben, wertgutschein.restwert) == (Decimal("30.00"), Decimal("0.00"), Decimal("20.00"))
  assert wiederholung.korrekturbelege == []
  assert einheitsfall.guthaben_betrag == Decimal("15.00")
  assert sum(teil.netto for teil in vollstaendige_korrekturen) == ursprung.netto
  assert sum(teil.ust for teil in vollstaendige_korrekturen) == ursprung.ust
  ```
- [ ] Atomare Rückabwicklung implementieren; Verbrauchsmarker, Beleg und Guthaben gemeinsam committen oder gemeinsam zurückrollen. Keine Aktualisierung ganzer Positionsmarker nach bloßer Teilwertkorrektur.
- [ ] Finanz-, Parallel-, Verfalls- und Mailtests grün; Commit `feat(core): Gutscheinstorno ohne Wertverlust`.

### Task 7: Kauf über Kanal, Callback und Kaufabbruch

**Files:** neu `services/gutschein_kauf.py`, `test_gutschein_kauf.py`, `templates/mail/gutschein_gekauft.txt`, `gutschein_geschenkt.txt`; ändern `online_buchung.py`, `anfragen.py`, `benachrichtigung.py`, `jobs.py`, `templates/rechnung_pdf.html`.

**Interfaces:** `anfragen(db, *, kunde, empfaenger_email, betrag, rueckkehr_url) -> Ergebnis`; `zahlung_eingegangen(db, z: Zahlung, betrag: Decimal) -> Ergebnis | None`; `brich_abgelaufene_ab(db) -> int`; `abschliessen(db, g: Gutschein, *, quelle="portal") -> Rechnung`. Zunächst `offen`, bezahlt → `aktiv`, abgelaufen → `abgebrochen`. Käuferdatum/-gruppe/-preis/-Steuer und Gültigkeitsdauer beim Wunsch einfrieren; Gültigkeitsbeginn beim tatsächlichen Kaufabschluss eindeutig speichern. Kein Guthabenrabatt beim Kauf.

Kaufcallback und Job: vollständige Käufermenge vor Zahlung/Gutschein sperren, frisch lesen und gespeicherte Frist prüfen. Nur verifizierte, endliche positive Zahlungseingänge bearbeiten. Unterzahlung beendet den Kauf als `abgebrochen`, schreibt den tatsächlich eingegangenen Betrag genau einmal dem Käufer gut und erzeugt einen Betreiberhinweis; ein neuer Kauf benötigt eine neue Anfrage. Überzahlung aktiviert genau einen Gutschein zum eingefrorenen Kaufpreis und schreibt ausschließlich den Überschuss einmal gut. Bei `now >= kauf_reserviert_bis` gilt der Kauf bereits als abgelaufen, unabhängig vom Job. Ungültige oder unverifizierte Rückmeldungen aktivieren nichts und erzeugen kein Guthaben. Doppelte Rückmeldung erzeugt keinen weiteren Kaufbeleg. Code erst nach erfolgreichem Commit an Käufer, ggf. zusätzlich Beschenkten; Code-Mail enthält Gruppenhinweis. Kaufbeleg ohne USt im GB-Kreis, reguläre Kaufrechnung mit ursprünglichem Käufer-Steuersatz.

- [ ] RED: `test_callback_nach_frist_ohne_job`, `test_fristsnapshot_nach_einstellungswechsel`, `test_callback_parallel_abbruch`, `test_doppelte_zahlung_kein_zweiter_beleg`.
  ```python
  assert (g.status, kaeufer.guthaben, kaufbelege) == ("abgebrochen", Decimal("30.00"), [])
  assert len(einmalige_spaetzahlungs_guthaben) == 1
  assert empfaenger_mail.enthaelt_kaufrechnung is False
  ```
- [ ] Kauf, Zahlung, Abbruch und Nachlauf integrieren; Mitgliedschaft/Preise nach Wunschänderung nicht rückwirkend in Snapshot übernehmen.
- [ ] Kauf-/Online-/Job-/Mailtests grün; Commit `feat(core): Gutscheine kaufen und Zahlungen abschliessen`.

### Task 8: Rückgabe und Schutz allgemeiner Rechnungsaktionen

**Files:** `services/gutscheine.py`, `gutschein_belege.py`, `rechnungen.py`, `routes/gutscheine.py`, `routes/rechnungen.py`, `templates/gutscheine/detail.html`, `templates/rechnungen/detail.html`; Tests `test_gutscheine.py`, `test_ui_gutscheine.py`, `test_ui_rechnungen.py`, `test_gutschein_parallel.py`.

**Interfaces:** `gib_zurueck(db, g: Gutschein, *, grund: str, admin_user_id: UUID | None) -> GutscheinRueckgabe` mit optionalem Gegenbeleg und manuell zu erstattendem Betrag. `pruefe_allgemeinen_rechnungsvorgang(rechnung, vorgang: str) -> None` wird in allgemeinen Finanzdiensten angewandt; Gutschein-Fachpfade benutzen ihren ausdrücklichen Belegvertrag statt eines Umgehungsflags aus HTTP-Nutzlasten.

Nur vollständig ungenutzte gekaufte Gutscheine: keine endgültige oder reservierte Einlösung, Wertrest gleich ursprünglichem Nennwert, nicht schon zurückgegeben. Gegenbeleg zu Kaufrechnung oder Storno des Kaufbelegs ohne doppelte Steuer; dauerhafte Rückgabemarkierung und Audit. Die manuelle Erstattung gehört dem ursprünglichen Käufer, nicht einem späteren Inhaber. UI: „Zurückgeben – Betrag anschließend manuell erstatten“, kein Geldtransfer und kein Guthabenzugang. Bereits korrigierte Kaufbasis nicht erneut korrigieren. Fremde Rechnung-POSTs dürfen die Sperre nicht umgehen.

- [ ] RED: Rückgabe gegen Einlösung, Wiederholung, direkte Voll-/Teilstorno-POSTs, Reaktivierung nach Rückgabe.
  ```python
  assert (rueckgabe.manuell_zu_erstatten, kaeufer.guthaben) == (Decimal("30.00"), Decimal("0.00"))
  assert rueckgabe_audits == 1
  assert erneute_rueckgabe.korrekturbeleg is None
  assert erneute_rueckgabe.manuell_zu_erstatten == Decimal("0.00")
  ```
- [ ] Rückgabe und zentrale Aktionsguards implementieren; normale Rechnungsaktionen außerhalb Gutscheinpfaden unverändert.
- [ ] UI-/Finanz-/Paralleltests grün; Commit `feat(core): Gutscheinrueckgabe ohne Doppelzahlung`.

### Task 9: Signierter Lesestand und aktive Portalverbraucher

**Files:** `shared/beachhub_shared/lesestand.py`, `core/beachhub_core/services/lesestand.py`, `konfiguration.py`; Tests `shared/tests/test_lesestand_schema.py`, `core/tests/test_lesestand.py`, `portal/tests/test_buchungen.py`, ggf. bestehende Portal-Buchungs-/Bestätigungstemplates für zutreffende Stornotexte.

**Interfaces:** `KontoGutschein` mit Code, Art, Modell, Status, Gültigkeit, Minuten/Rest, Gruppenhinweis; `KontoGutscheinKauf` mit Kauf-ID, Status, Preis, gespeicherter Frist und optionaler Checkout-URL, ausdrücklich ohne Code; `KontoInhalt.gutscheine=[]`, `gutschein_kaeufe=[]`, `gutschein_kauf=False` als sichere Alt-Dokument-Vorgaben. `GutscheinAngebot` optional im Tarifdokument. Ob tatsächlich gekauft werden darf entscheidet weiterhin der Hauptsystem-Kanal.

Eigene Gutscheine nur bei aktiver Stellung, aktueller Gültigkeit und positivem Rest bzw. verbleibender Nutzung anzeigen; nicht auf den letzten Ablaufjob vertrauen. Offene Käufe nur vor der gespeicherten Frist anzeigen. Beides nur dem berechtigten Konto anzeigen. Nach Besitzwechsel alten und neuen Inhaber sowie Käufer markieren, nach Angebotsänderung alle betroffenen Konten und Tarifdokument. Checkout-Links nur dem Käufer. Keine Gutscheinverkaufs-UI allein durch neue Felder oder einen permissiven Default aktivieren. Bestehende Stornobestätigung bleibt wahr: Gutscheindeckung nicht pauschal als Geldguthaben versprechen; präzisere Verkaufs-/Code-Eingabeoberfläche bleibt Stufe 2.

- [ ] RED: Alt-Dokument, bisheriger Inhaber, Fremdkonto, offene Kaufcodes, bestehende Portal-Stornotexte.
  ```python
  assert alte_daten.gutschein_kauf is False
  assert alte_daten.gutscheine == [] and alte_daten.gutschein_kaeufe == []
  assert "code" not in offene_kaufdarstellung.model_dump()
  assert aktualisierter_altinhaber.gutscheine == []
  ```
- [ ] Verträge und Lesestand implementieren; sichtbare aktuelle Verbraucher minimal korrekt halten, kein neues Frontend.
- [ ] Shared/Core/Portal betroffene Tests grün; Commit `feat(shared): Gutscheinbestand und offene Kaeufe im Lesestand`.

### Task 10: End-to-End, Betrieb, Spezifikation und Abschlussreview

**Files:** neu `e2e/test_gutscheine.py`; ändern `README.md`, `core/README.md`, `portal/README.md`, `AGENTS.md`, `docs/betrieb/hauptsystem.md`; Spec nur präzisieren, keine bestätigte Fachentscheidung stillschweigend ersetzen.

- [ ] E2E-Test Konto → Kaufwunsch → Zahlung → signierter Bestand → Buchung → kostenfreier Storno. Zusätzlicher Käufer A / Einlöser B-Fall für Wertgutschein und fremde Belegrechte.
- [ ] Betriebsanleitung: Preise null deaktivieren Verkauf, gültige Varianten/Snapshots, Kunden-/Gutscheinsperren, Gegenbelege und Guthaben, manuell auszuführende Rückgabezahlung, offene Käufe/Spätzahlung, Versandgrenzen, ursprüngliche Gültigkeit nach Rückgabe.
- [ ] Fachliche Übersicht gegen A-GUT-1 bis -9, A-STORNO-7 und § 5 prüfen. Historischen Plan eindeutig als abgelöst kennzeichnen; keine Aussage „Gutscheine umgesetzt“ vor tatsächlicher Verifikation.
- [ ] Vollständige Suites in Reihenfolge Shared → Hall → Core → Portal → E2E, separat, explizites Worktree-PYTHONPATH und nur Testdatenbanken. `ruff check .`, `ruff format --check .`, `mypy`, `git diff --check`.
- [ ] Ein unabhängiges Gesamt-Review von Geldflüssen, FK-/Lockfolgen, Belegresten, Besitz-/Kontozuordnung und dem gesamten Änderungsumfang. Jeder bestätigte Fehler zunächst als Regression reproduzieren; kein Freigabevotum allein wegen grüner linearer Tests.
- [ ] Commit `docs: Gutscheine, Rueckgaben und Betriebsablaeufe`; Integration nach Nutzerentscheidung. Keine reale Zahlung, keine Veröffentlichung einer Verkaufsoberfläche und keine Produktivmigration durch diesen Test-/Abschlussprozess.

## Abnahme und noch ausstehende Umsetzung

Dieser Plan berücksichtigt die aktuelle Vollstorno-Erstattung und die bestätigte Wertgutschein-Regel. Vor Implementierung sind die geschriebenen Schnittstellen zusammen als Design zu prüfen; der bisherige Wunsch nach sinnvoller Subagentennutzung bleibt erhalten. Keine neue Grundsatzentscheidung zu Gutscheinmodell oder Steuerregel wird benötigt: Sie sind Betreiber-Einstellungen mit vorhandenen Vorgaben.

Die Ausführung soll pro Task RED/GREEN, Commit und unabhängiges Task-Review dokumentieren. Änderungen an den hier festgelegten Geld-/Belegverträgen werden nicht als stiller „Spec-Abgleich“ versteckt, sondern vor Ausführung ausdrücklich geklärt. Das betrifft insbesondere eine spätere gewünschte Abweichung vom bestätigten Wertstorno als Guthaben.
