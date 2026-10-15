# Nachreview Stufe 1a-II – Vollstorno, Altgutschriften, Upgrade

- **Geprüfter Umfang:** `5592a9f..8c484b9` (Stufe 1a-II einschließlich der Korrekturen `9a4f1d0` und `fc309f2`)
- **Datum:** 2026-10-15
- **Vorheriges Review:** [2026-10-05-stufe-1a-II-review.md](2026-10-05-stufe-1a-II-review.md); dessen Befunde 1 bis 10 und 7a sind nachgerechnet und behoben.
- **Teststand vor den Korrekturen:** shared 41, hall 158, core 555, portal 168, e2e 8 Tests grün; mypy und Ruff ohne Befund.

Alle Befunde sind in diesem Branch mit zuvor roten Tests behoben; die Befunde 1 und 2 in `bde6581`, 3 bis 10 im Folgecommit. Den Teststand nennt der Abschnitt „Nachprüfung“.

## Behoben

### 1. Vollstorno einer Einzelrechnung erstattet, ohne neu zu berechnen (hoch)

**Wo:** `core/beachhub_core/services/rechnungen.py` (`storniere`)

Das Vollstorno galt für jede Rechnung, eine Neuausstellung gab es aber nur für Saisonrechnungen. Bei einer bezahlten Onlinebuchung bekam der Kunde den Betrag als Guthaben, die Buchung blieb bestätigt und unberechnet. Bei einer offenen Betreiberrechnung erlosch die Forderung.

**Korrektur:** Bei Einzelrechnungen erhält jede weiter abrechenbare Buchung (aktiv oder kostenpflichtig abgesagt) in derselben Transaktion eine neue Einzelrechnung mit aktuellen Kundendaten. Der eben erstattete Anteil wird darauf verrechnet (`verrechne_guthaben(..., hoechstens=...)`), anderes Guthaben bleibt unberührt. `storniere` liefert `Vollstorno(beleg, neu)`; die Route versendet Beleg und neue Rechnungen nach dem Commit über `belege_versenden`. Saisonrechnungen werden weiterhin ausdrücklich neu ausgestellt.

**Tests:** `test_review2_1_*` in `core/tests/test_korrektur.py`, `test_vollstorno_einzelrechnung_versendet_beleg_und_neue_rechnung` in `test_ui_rechnungen.py`. Die bisherigen Tests, die das alte Verhalten festhielten, sind angepasst.

### 2. Beleglose Gutschriften aus der Zeit vor 0014 werden doppelt erstattet (mittel)

**Wo:** `core/beachhub_core/services/storno.py` (`gutschreiben_positionen`)

Vor 0014 schrieb ein kostenfreies Storno einer Onlinebuchung Guthaben ohne Korrekturbeleg gut (Bezug auf das Storno). Holte der Betreiber den Beleg per Teil- oder Vollstorno nach, entstand das Guthaben ein zweites Mal.

**Korrektur:** `_altgutschriften` ermittelt solche Gutschriften über den Bezug auf das Storno der betroffenen Buchungen und rechnet sie an. Der Korrekturbeleg entsteht weiterhin. Eine Migration ist nicht nötig, weil neue Stornogutschriften auf ihren Beleg verweisen.

**Tests:** `test_review2_2_altgutschrift_wird_beim_nachgeholten_beleg_angerechnet`.

## Behoben im Folgecommit

| Befund | Korrektur | Tests |
|---|---|---|
| 3 Kontodokumente nach dem Upgrade | Datenmigration `0016_upgrade_1a_ii` markiert alle veröffentlichten Konto-Dokumente und die aller Abo-Kunden | `test_review2_3_5_migration_0016_*` |
| 4 Ausrollreihenfolge | Betriebshandbuch Abschnitt 7: Portal vor Hauptsystem; neues Portal toleriert altes Hauptsystem | Dokumentation |
| 5 Saisonende-Mail nach Upgrade | `0016` setzt den Marker für das laufende Jahr, wenn der Stichtag vorbei ist (Datum mit Admin-Override) | `test_review2_3_5_migration_0016_*` |
| 6 Stornobestätigung | `sperren.lege_an`, `dauerbuchungen.lege_an` und `beende` sammeln die erzeugten Stornos; `benachrichtigung.stornos_versenden` schickt je Kunde eine Bestätigung und danach nur deren Belege. `_belege_der_buchungen` entfällt | `test_review2_6_*` |
| 7 Gutgeschriebener Abo-Termin | Bereits gutgeschriebene Termine sind bei der Kundenabsage immer kostenfrei, ohne Kontingent und auch nach der Frist | `test_review2_7_*` |
| 8 Portalhinweise | Kanalfeld `korrekturbeleg`; Portalmeldungen „bereits gutgeschrieben“ und „nicht berechnet“; Stornoseite nennt das Fristende des Abo-Termins | `test_review2_8_*` (core, portal) |
| 9 Audit | `admin_user_id` für Saisonrechnung, Verrechnung und Neuausstellung; `korrigiert_rechnung_id` im Anlage-Audit des Belegs; Audit der Buchungsfreigabe beim Vollstorno; Verknüpfung Storno–Beleg als Änderungsaudit | `test_review2_9_*` |
| 10 Auszahlung | Formular sendet den angezeigten Stand; weicht er ab, wird nichts gebucht | `test_review2_10_*` |

Beim Rebase auf `main` (Stufe 1a-III bereits umgesetzt) wurde die Datenmigration als `0016_upgrade_1a_ii` hinter `0015_gutscheine` eingereiht; der umgesetzte Gutscheinplan bleibt unverändert.

## Ursprüngliche Beschreibung der Befunde 3 bis 10

### 3. Kontodokumente nach dem Upgrade veraltet (mittel)

`0014` erzeugt die `konto:*`-Dokumente nicht neu. Abo-Kunden sehen im Portal keinen Link „Stornieren“, bis sich ihr Konto ändert. Umgehung: `abo_freie_absagen` einmal neu speichern (`konfiguration.py` ruft dann `lesestand.markiere_abo_kunden`). Richtung: einmaliger Schritt beim Upgrade oder Hinweis im Betriebshandbuch.

### 4. Ausrollreihenfolge Portal und Hauptsystem (mittel)

Neues Hauptsystem mit altem Portal kündigt kostenfreie Abo-Absagen an, die das Hauptsystem berechnet. Richtung: „Portal vor Hauptsystem“ im Betriebshandbuch festhalten.

### 5. Saisonende-Mail direkt nach dem Upgrade (niedrig)

Ohne Marker `guthabenliste_letzter` und nach dem Stichtag verschickt der erste Lauf mitten in der Saison „Die Saison ist zu Ende“. Richtung: Marker beim Upgrade setzen oder dokumentieren.

### 6. Stornorechnung ohne Stornobestätigung (niedrig)

Bei Sperre, Daueranlage und Beenden geht an verdrängte Kunden die Stornorechnung, aber keine Stornomail (A-MAIL-2). `_belege_der_buchungen` liest Belege aus den Formular-IDs statt aus den tatsächlich erzeugten Belegen und kann alte Belege erneut verschicken.

### 7. Gutgeschriebener Abo-Termin ohne Kontingent wird kostenpflichtig (niedrig)

`storno.py`: Bei aufgebrauchtem Kontingent wird die Absage eines bereits gutgeschriebenen Termins als kostenpflichtig gespeichert, entgegen A-DAUER-3. Geld fließt nicht falsch.

### 8. Portalhinweise zu Abo-Absagen (niedrig)

„Die offene Forderung sinkt …“ steht auch, wenn der Termin schon gutgeschrieben oder noch nicht berechnet war. Für Abo-Termine fehlt der Fristhinweis.

### 9. Audit unvollständig (niedrig)

Saisonrechnung und Guthabenverrechnung ohne Admin-Zuordnung; `korrigiert_rechnung_id` des Korrekturbelegs fehlt im Anlage-Audit; das Vollstorno löst Positionen ohne Audit von ihrer Buchung.

### 10. „Auszahlung abhaken“ nicht idempotent (niedrig)

Ein Doppelklick mit Teilbetrag bucht zweimal ab.

## Nachprüfung

**Teststand nach allen Korrekturen:** shared 41, hall 158, core 575, portal 173, e2e 8 Tests grün; mypy und Ruff ohne Befund. Die neuen Tests zu den Befunden 6, 7, 9 und 10 waren vor der Korrektur rot. `test_ui_beenden_versendet_einen_korrekturbeleg` erwartet jetzt zusätzlich die Stornobestätigung vor der Stornorechnung.
