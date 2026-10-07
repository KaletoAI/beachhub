# Gesamt-Review: Stufe 1a-III – Gutscheine

Datum: 07.10.2026. Ursprünglicher Gesamtpass: `8c484b9..d744fe0` auf `feat/stufe-1a-III`,
HEAD `d744fe042c4a90851fc3e14cb66eb3555859fa01`; 13 Commits, 75 geänderte Dateien.
Gezieltes abschließendes Fix-Re-Review: `d744fe0..74d6e62`, elf Fixdateien,
HEAD `74d6e62e3884375db4630b0cf487b1cad9a04a0d`.
Fixcommit: `74d6e62` – `fix(core): Abschlussbefunde der Gutscheinpruefung beheben`.
Implementierung: GPT-6.1-sol. Unabhängiges Review: GPT-6-astra, ohne weitere Unteragenten.

## Aktuelles Urteil

**Spezifikationsprüfung bestanden. Qualitätsprüfung bestanden. Bereit zum Merge.**
Das Urteil gilt für `74d6e62` im vereinbarten Umfang. Alle fünf bestätigten Gesamtbefunde
sind behoben; das gezielte Re-Review fand keine verbleibenden zwingenden Findings oder
neu eingeführten Fehler in der Fixintegration. Kein erneutes breites Branchreview:
Das Abschlussurteil verbindet den ursprünglichen Gesamtpass mit der Prüfung seiner Fixes.

Das ursprüngliche Urteil für `d744fe0` lautete „noch Änderungen nötig“: drei Important-
und zwei Minor-Findings, keine Critical-Findings. Ihre Befundhistorie bleibt unten erhalten.
Die Freigabe ersetzt keine Anbindung/Abnahme der ausdrücklich noch offenen Produktivdienste.

## Abschluss der Korrekturen

| Finding | Geprüfte Behebung in `74d6e62` | Status |
| --- | --- | --- |
| GR-1 | Optionale Gegenbeleg-ID vor Commit gesichert; bestehender isolierter PDF-/Belegnachlauf danach, nur für den Käufer und nur bei neuem Beleg | Geschlossen |
| GR-2 | Validierte Pagination mit 500 Treffern, Zählung, UUID-Sortierung und filtertreuen Vor-/Zurücklinks; 1000er-Serie vollständig erreichbar | Geschlossen |
| GR-3 | Vollständiger Transaktionshelper mit höchstens drei Versuchen, äußerem Rollback und frischer Vorbereitung an allen fünf Admin-/drei Jobgrenzen | Geschlossen |
| GR-4 | Datenmodelltabelle beschreibt Deckung in `GutscheinEinloesung` und die getrennte Kaufzahlung statt erfundener Gutschein-Zahlungszeilen | Geschlossen |
| GR-5 | Käufer-/Inhaberspalten mit Kundenlinks und Platzhaltern; eine gemeinsame Kundenabfrage pro Seite | Geschlossen |

Bei GR-3 wurden alle ursprünglichen Grenzen erneut betrachtet: Buchungsstorno, Kulanz,
Sperranlage, verdrängende Daueranlage, Dauerbeendigung, Reservierungsverfall,
Gutschein-Nachtverfall und Kaufabbruch. Jeder erfolgreiche Versuch committet vollständig;
PDFs/Mails bleiben außerhalb des Helpers und folgen erst danach. Rollbacks verwerfen
fehlgeschlagene Mutationen und invalidieren Sessionmodelle; die Callbacks laden
Modelle/Batchmengen erneut. Kulanz ermittelt den Ausgangsstatus je Versuch neu,
Daueranlage wiederholt einschließlich Rechnungskundenkennzeichen und Finanzobjekten.
Erschöpfte Adminversuche ergeben eine verständliche Meldung; Jobs protokollieren nach
dem letzten vollständigen Rollback. Andere Exceptions werden nicht pauschal wiederholt.

Die neuen Regressionen prüfen tatsächliche Käufer-PDFs und Kanalabrufe, fremde
Ablehnung, Versandwiederholung, Fehler nach sichtbarem Commit, vollständige Pagination
und geheime Codes auf mehreren Seiten. Alle acht Transaktionsgrenzen werden nach
echten Mutationen auf vollständigen Rollback und begrenzte Wiederholung geprüft.
Ein zusätzlicher Zwei-Session-Freicode-Test erzeugt während beobachtetem Kundenlockwait
einen weiteren Einlöser: Die produktive Beziehungsprüfung löst den Retry aus, die neue
Kundenlockmenge enthält diesen Einlöser, der Adminvorgang gelingt genau einmal.

## Historische Findings am Stand `d744fe0` – sämtlich geschlossen

Die folgenden Fundstellen und Fehlerbeschreibungen beziehen sich auf den ursprünglichen
Reviewstand. Die jeweils verlangte Korrektur ist durch den oben beschriebenen Fix erledigt.

### GR-1 · Important: Gegenbeleg der manuellen Rückgabe bleibt ohne PDF

`core/beachhub_core/routes/gutscheine.py:230` erzeugt über `gib_zurueck` einen
Gegenbeleg, verwendet aber ausschließlich den Erstattungsbetrag. Nach dem Commit in
Zeile 235 folgt bis Zeile 249 kein PDF-/Belegnachlauf. Nach einer erfolgreichen Rückgabe
erscheint die Stornorechnung beim Käufer, kann über `rechnung_anfordern` jedoch wegen
fehlendem `pdf_pfad` nicht abgerufen werden; die übliche Belegmail fehlt ebenfalls.

**Korrektur:** Die optionale Gegenbeleg-ID vor Commit sichern und nach erfolgreichem
Commit den bestehenden isolierten `benachrichtigung.belege_versenden`-Weg aufrufen.
Empfänger bleibt der ursprüngliche Käufer, auch bei abweichendem Inhaber. Wiederholung
ohne neuen Beleg darf keinen neuen Versand starten. Eine neue Mailvorlage ist unnötig.
Gezielt beide Steuerzeitpunkte, Käuferabruf/fremde Ablehnung, Wiederholung sowie
PDF-/SMTPfehler nach Commit prüfen.

Die frühere Task-8-Abgrenzung „keine neue automatische Rückgabemail“ ist kein Grund,
den bereits vorhandenen PDF-/Belegweg auszulassen. Diese Belegzugänglichkeit wurde im
Gesamtreview ausdrücklich neu bewertet und mit dem Controller abgeglichen.

### GR-2 · Important: Nicht alle gültig erzeugten Gutscheine sind verwaltbar

`core/beachhub_core/routes/gutscheine.py:37`–`49` begrenzt die Liste auf 500 Treffer.
Die einzigen Filter sind Art, Status und Serie; Pagination und Codesuche fehlen.
Eine erlaubte Serie von 1000 aktiven Codes besitzt identische Filterwerte. Ihre zweite
Hälfte ist über die Oberfläche nicht erreichbar. Der CSV-Export enthält weder
Detail-UUID noch Links. Das verhindert gezieltes Sperren/Verlängern; bei entsprechend
vielen Kaufgutscheinen auch das Auffinden zur Rückgabe.

**Korrektur:** Stabile Pagination mit erhaltenen Filtern, alternativ ein eindeutiger
normalisierter Codezugang außerhalb des Limits. Das Limit nur zu erhöhen reicht nicht.
Mit mehr als 500 gleich gefilterten Codes vollständige Erreichbarkeit und weiterhin
verborgene offene Kaufcodes prüfen.

### GR-3 · Important: Interne Neupräparation ist an Admin-Transaktionsgrenzen unbehandelt

`core/beachhub_core/services/gutschein_sperren.py:107` wirft bei während des Lockwaits
hinzugekommenen Kunden/Zahlungen korrekt `GutscheinVorbereitungWiederholen`.
Die Buchungsverwaltung behandelt diesen Typ jedoch nicht; er fehlt in
`core/beachhub_core/routes/belegung.py:81` (`FORM_FEHLER`). Beispielsweise genügt ein
weiterer Einlöser eines noch nutzbaren Freicodes/Wertrests, um einen gleichzeitig
vorbereiteten Admin-Storno mit HTTP 500 zu beenden. Die Session wird letztlich
zurückgerollt; teilweise committierte Gelder wurden dabei nicht festgestellt.

| Betroffener Einstieg | Vorbereitung | Grenze am geprüften HEAD |
| --- | --- | --- |
| `routes/belegung.py:315`, Buchungsstorno | `storno.storniere:206` | Unbehandelte Ausnahme, HTTP 500 |
| `routes/belegung.py:362`, Kulanz | `storno.kulanz:279` | Unbehandelte Ausnahme, HTTP 500 |
| `routes/belegung.py:420`, Sperranlage | `sperren.lege_an:61` | Unbehandelte Ausnahme, HTTP 500 |
| `routes/belegung.py:604`, verdrängende Daueranlage | `dauerbuchungen.lege_an:123` | Unbehandelte Ausnahme, HTTP 500 |
| `routes/belegung.py:746`, Dauerbeendigung | `dauerbuchungen.beende:214`, zusätzliche Terminprüfung Zeile 220 | Unbehandelte Ausnahme, HTTP 500 |
| `jobs.py:28`, Reservierungsverfall | `online_buchung.verfalle_abgelaufene:421` | Generisch geloggt, Rollback am Sessionende, nächster Minutenlauf |
| `jobs.py:36`, Gutscheinverfall | `gutscheine.verfalle:190` | Generisch geloggt, nächster regulärer Lauf erst am nächsten Tag |
| `jobs.py:43`, Kaufabbruch | `gutschein_kauf.brich_abgelaufene_ab:289` | Generisch geloggt, nächster Minutenlauf |

Die Jobs erzeugen keine HTTP-500-Antworten; auch ihnen fehlt aber ein unmittelbarer
begrenzter Retry der normalen Konkurrenzsituation. Frische Gültigkeitsprüfungen
verhindern unabhängig vom ausgefallenen Nachtbatch unzulässige Einlösungen.

**Korrektur:** Jeweils den vollständigen Vorgang am Transaktionsrand höchstens dreimal
neu vorbereiten. Nach vollständigem Rollback Modelle und Batchmenge frisch laden;
keine nachträglichen Kundensperren und kein bloßer Savepoint-Retry. Nach erschöpften
Versuchen verständlich ablehnen. Bei Daueranlage auch Kundenkennzeichen und sämtliche
neu angelegten Objekte zurückrollen; bei Kulanz den Ausgangsstatus frisch bestimmen.
PDFs/Mails bleiben nach erfolgreichem Commit. Alle fünf Admin- und drei Jobgrenzen
gezielt auf erfolgreichen Retry, begrenzten Abbruch und einmalige Nebenwirkungen prüfen;
mindestens ein echter Zwei-Session-Test für die hinzugekommene Kundenmenge.

Bereits korrekt: Der Kanal wiederholt vollständig bis dreimal. Die direkte manuelle
Gutscheinrückgabe wiederholt ebenfalls und meldet finale Fehler verständlich.
Gutschein-Sperre/Gültigkeitsänderung verlangen nach Rollback bereits verständlich eine
manuelle Wiederholung; das ist dokumentiert und kein HTTP-500-Befund. Interne Dienste
dürfen den Kontrollflusstyp an transaktionsbewusste Aufrufer weiterreichen.

### GR-4 · Minor: Falsche Zahlungsdarstellung in der Spezifikation

`docs/superpowers/specs/2026-09-05-beachhub-design.md:301` behauptet pro Einlösung eine
Zahlung mit `provider = gutschein`. Tatsächlich speichert `GutscheinEinloesung` die
Deckung; eine solche Zahlungszeile entsteht nicht. Die Tabellenzeile muss den
implementierten Vertrag beschreiben, damit künftige Auswertungen Gutscheinverbrauch
nicht als neuen Geldeingang behandeln. Keine künstlichen monetären Zahlungen ergänzen.

### GR-5 · Minor: Käufer/Inhaber fehlen in der Übersicht

`core/beachhub_core/templates/gutscheine/liste.html:11`–`15` zeigt weder Käufer noch
Inhaber, obwohl A-ADM-8 diese Zuordnungen in der Liste verlangt. Sie sind nur auf den
Einzelkarten vorhanden. Beide Spalten ergänzen und ihre Kundendaten gesammelt laden;
fehlende Zuordnungen bei Freicodes als leeren Platzhalter darstellen.

## Nachvollzogene Stärken und frühere Korrekturen

Der gemeinsame Slotpreisstand schützt Buchungspreis und Gutscheindeckung vor
konkurrierender Tarifpflege. Kumulative Centkorrekturen, getrennte Kauf-/Einlösungs-/
Geldanteilbelege und vollständige Kundensperrmengen tragen die verschiedenen Modelle.
Bestätigter Wertstorno schreibt den Einlöser gut, ohne den Gutscheinrest zu erhöhen;
Einheiten/Freicodes bekommen Nutzung unter ursprünglicher Gültigkeit zurück.
Reservierungsrücknahmen stellen Bestand und Wallet wieder her. Allgemeine
Rechnungsaktionen umgehen diese Fachwege nicht.

Kauf-Snapshots und gespeicherte Fristen, verifizierte Zahlungen, Unter-/Über-/Spätzahlung,
Käufer-PDF-Rechte, Kontobindung unter Lock, Fehlversuchsdeduplizierung und sichere
Lesestanddefaults wurden zusammen geprüft. Migration 0015 erhält die bisherigen
Nummernkreise und verweigert ein datenvernichtendes Downgrade bei Gutscheinbestand.

Die früheren Task-Findings wurden am aktuellen Code und ihren Regressionen erneut
nachvollzogen:

- Task 5, `a3963ec`: gemeinsamer Slotpreisstand statt getrennter Tarifabfragen.
- R6-1, `bcffbc6`: zentraler einmaliger Wallet-Rücklauf beim Betreiber-/Batchabbruch.
- R7-1, `5dacf09`: frischer exakter Vergleich der erwarteten Portalbindung beim Kauf.
- Task 4 N1, `4c17ef5`: Freicode-Nutzungslimit auf 2147483647 begrenzt.
- Task 3/Testgrenze: Task 5 ergänzt echte Freicode-Verbrauchsrennen mit beobachtetem
  Lockwait statt allein durch gemeinsame Kundenlocks serialisierter Vorbereitung.

## Testevidenz

**Abschlussstand nach Fix:** Vom Implementer/Controller berichtete finale Prüfung für
`74d6e62`: Core **904 bestanden**, E2E **14 bestanden**, zusammen **918 frisch**.
Ruff sauber, Ruff-Format **245 Dateien** unverändert, Mypy **73 Quelldateien** sauber.
Shared **59**, Hall **158** und Portal **171** stammen aus der unveränderten Baseline
`d744fe0` und wurden in der Fixwelle nicht nochmals ausgeführt. Insgesamt sind damit
**1306 bestandene Tests dokumentiert**, ausdrücklich keine neue vollständige
All-Suite-Ausführung. Bekannte Bibliothekswarnungen blieben unverändert.

Gezielte Fixevidenz laut vollständig gelesenem Implementerbericht: GR-1/2/5
**6 failed → 6 passed**; GR-3 **16 failed** für die acht Grenzen und **1 failed** im echten
Konkurrenzfall; anschließend **47 passed** gezielt und **84 passed** in der breiteren
betroffenen Regression. Separat berichtete Fixturefehler zählen nicht als Produkt-RED.

Das Fix-Re-Review las die elf geänderten Dateien, die neuen Regressionen und ihre
betroffenen Aufrufer. Selbst ausgeführt: HEAD-Prüfung und
`git diff --check d744fe0..74d6e62`, Exit 0. Keine eigenen zusätzlichen DB-/Suiteläufe.
Nur Berichte wurden aktualisiert, keine Produktivmigration oder externen Aktionen.

**Historische Gesamtprüfung vor Fix:** Vom Controller/Implementer berichteter Stand
`d744fe0`:

| Prüfung | Ergebnis |
| --- | --- |
| Shared | 59 bestanden |
| Hall | 158 bestanden |
| Core | 881 bestanden |
| Portal | 171 bestanden |
| E2E | 14 bestanden |
| Summe | 1283 bestanden |
| Ruff / Ruff-Format | sauber / 244 Dateien unverändert |
| Mypy | 73 Quelldateien sauber |

Bekannte AnyIO-/pytest-asyncio-Warnungen gehören zur Baseline.

Der Reviewer führte eigene Readonly-Quellcode-/Diffpässe und den Abgleich mit
Spezifikation, Plan, AGENTS/READMEs und gemeinsamen Verträgen durch. Gezielt gelesen
wurden auch die relevanten Finanz-, Migrations-, Nebenläufigkeits- und E2E-Regressionen.
Selbst ausgeführt: `git diff --check 8c484b9..d744fe0`, Exit 0, sowie HEAD-/Rangeprüfung.
Keine erneute Suite und kein eigener DB-RED-Lauf; die neuen Findings sind konkrete
statische Abfrage-/Kontrollflussbefunde. Keine Produktivmigration oder realen Zahlungs-,
SMTP- oder Hardwareaktionen während des Reviews.

## Nicht bewertet

- Echter Zahlungsanbieter einschließlich externer Checkout-Idempotenz und produktiver
  Webhooks: ausdrücklich noch offen; geprüft wurde der aktuelle Fake-/Adaptervertrag.
- Reale Hallenhardware/Home Assistant: nicht Teil dieser Gutscheinimplementierung;
  die Hallentests stellen keine Hardwareabnahme dar.
- Verkaufs-/Codeeingabeoberfläche, deren IP-Limit und Gastkauf: ausdrücklich spätere
  Stufe. Aktuelle Portalverbraucher und das Kontolimit wurden dagegen bewertet.
- Allgemeine Kontoumhängungsrennen vorhandener Portalaktionen außerhalb des neuen
  Kaufhandlers: kein vollständiges Altsystem-Authaudit; R7-1 am neuen Kauf ist geprüft.
  Daraus folgt keine Sicherheitsfreigabe aller historischen Kontoaktionen.
- Rechtliche Neubewertung der bestätigten Betreiber-/Steuerberaterentscheidungen:
  fachliche Umsetzung geprüft, keine neue Rechts-/Steuerprüfung vorgenommen.
- Universelle SMTP-Zustellgarantie, historische Wiederholungsmails und automatische
  Bankerstattung: ausdrücklich nicht beauftragt. GR-1 betrifft dagegen einen fehlenden
  bestehenden Belegnachlauf und bleibt ein Finding.
- Unbegrenzte Langzeitlast und hypothetische Konfigurationen mit Milliarden Minuten/
  Tagen: kein allgemeines Last-/Konfigurationshärtungsprojekt. Das innerhalb einer
  erlaubten 1000er-Serie erreichbare Listenproblem wurde ausdrücklich bewertet.

Diese sieben Abgrenzungen wurden vom Controller akzeptiert und im Fix-Re-Review
unverändert beibehalten. Im Fixumfang wurde kein zusätzliches Verhalten ausgenommen.
Merge, Push, Veröffentlichung und Produktivmigration sind nicht Bestandteil dieses
Reviews. Das aktuelle Freigabeurteil gilt für den geprüften Fix-HEAD `74d6e62`.
