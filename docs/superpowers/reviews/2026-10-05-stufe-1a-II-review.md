# Review Stufe 1a-II – Saisonrechnung, Korrekturbelege, Abo-Absagen, Guthabenliste

- **Branch:** `feat/stufe-1a-II` (Worktree `/tmp/beachhub-stufe-1a-II`)
- **Geprüfter Umfang:** `5592a9f..03bcbc6`, 11 Commits, 59 Dateien
- **Datum:** 2026-10-05
- **Plan:** `docs/superpowers/plans/2026-10-03-stufe-1a-II-saisonrechnung-abo-absagen.md`

Die Befunde 1 bis 7 sind fachliche Fehler. Bitte jeden zuerst mit einem Test belegen, der rot ist, und erst dann beheben. Die Befunde 8 bis 10 sind Aufräumarbeiten und können mit erledigt werden.

> **Hinweis:** Das lokale `main` wurde während des Reviews auf `03bcbc6` vorgespult. `origin/main` steht noch auf `5592a9f`. Vor dem Abschluss klären, ob das so gewollt war.

---

## Kritisch: Geld geht verloren oder wird falsch berechnet

### 1. Komplette Stornierung verliert verrechnetes Guthaben

**Wo:** `core/beachhub_core/services/rechnungen.py:421` (`rechnungen.storniere`)

**Fehler:** Beim kompletten Storno einer Rechnung wird nicht berücksichtigt, wenn Guthaben schon als Zahlung (`provider='guthaben'`) mit ihr verrechnet wurde. Dieses Guthaben geht nicht an den Kunden zurück und wird auch nicht auf die neu ausgestellte Rechnung übertragen.

**Ablauf:**
1. Der Kunde hat 100 € Guthaben.
2. Die Saisonrechnung R1 über 500 € wird erzeugt. `verrechne_guthaben` bucht −100 € Guthaben und legt eine `Zahlung(rechnung_id=R1)` an.
3. Der Betreiber storniert R1 komplett, um sie neu auszustellen (B-8).
4. Die Zahlung bleibt an der stornierten R1 hängen, und `kunde.guthaben` steht bei 0.
5. Die neue Saisonrechnung R2 ist wieder über 500 € offen. Die Verrechnung mit `provider_ref 'guthaben:{R2}'` findet kein Guthaben mehr.

**Folge:** Der Kunde verliert 100 €. Bei einer schon bezahlten Rechnung (`bezahlt`) passiert dasselbe: Nach der Neuausstellung schuldet der Kunde wieder den vollen Betrag.

**Richtung:** Beim kompletten Storno müssen die Zahlungen, die schon mit der Rechnung verrechnet sind, wieder als Guthaben gutgeschrieben werden (oder auf die Folgerechnung übertragen). Das muss nachvollziehbar sein und ein Audit erzeugen.

### 2. Komplettes Storno gibt schon gutgeschriebene Termine wieder frei

**Wo:** `core/beachhub_core/services/rechnungen.py:437`

**Fehler:** Die Schleife beim kompletten Storno setzt `b.rechnung_position_id = None` auch bei Buchungen, deren Position schon vorher per Teilstorno korrigiert wurde.

**Ablauf:**
1. Der Betreiber storniert einen Saisontermin teilweise, weil die Halle geschlossen ist. Die Buchung bleibt nach A-ADM-4 aktiv, der Kunde bekommt eine Gutschrift.
2. Später storniert er die ganze Rechnung, um sie neu auszustellen.
3. Die Schleife löst die Position auch von dieser Buchung.
4. `erzeuge_saisonrechnung` findet die Buchung als aktiv und nicht abgerechnet und berechnet sie erneut.

**Folge:** Der Kunde zahlt für einen Termin, für den er schon eine Gutschrift bekommen hat.

**Richtung:** Positionen, die schon korrigiert wurden, beim kompletten Storno überspringen, oder die Buchung dauerhaft als „gutgeschrieben“ kennzeichnen, damit die Neuausstellung sie nicht wieder aufnimmt.

### 3. Neu ausgestellte Saisonrechnung lässt Spätabsagen weg

**Wo:** `core/beachhub_core/services/rechnungen.py:370` (`erzeuge_saisonrechnung`)

**Fehler:** Es werden nur aktive Buchungen (`b.aktiv`) berechnet. Kostenpflichtig abgesagte Termine (nach der Frist oder ohne freie Absagen) fallen weg, sobald die Saisonrechnung neu ausgestellt oder später über „Saisonrechnung erstellen“ erzeugt wird. Der entfernte Monatslauf hatte sie noch als „Storno nach Frist“ abgerechnet.

**Ablauf:**
1. Ein Abonnent sagt 2 Termine nach Ablauf der Frist ab. Sie bleiben auf der Saisonrechnung R1 berechnet.
2. Der Betreiber storniert R1 komplett, um die Adresse zu korrigieren. Dabei werden alle Buchungen freigegeben.
3. Die Neuausstellung filtert auf `b.aktiv`, und die beiden Termine fehlen auf R2.

**Folge:** Die Gebühren, die A-DAUER-3 verlangt, gehen stillschweigend verloren.

**Richtung:** Kostenpflichtig stornierte Buchungen der Dauerbuchung mit abrechnen. Die Kennzeichnung, dass ein Termin kostenpflichtig storniert wurde, muss dafür an der Buchung ablesbar sein.

---

## Fachlich falsch

### 4. Status springt nach einer Teilkorrektur nicht auf „bezahlt“

**Wo:** `core/beachhub_core/services/rechnungen.py:171` (`korrigiere`)

**Fehler:** `korrigiere` setzt den Status nur auf `storniert`, wenn alle Positionen korrigiert sind. Den Wechsel auf `bezahlt` gibt es dort nicht, auch wenn nach der Teilkorrektur `offener_betrag` bei 0 liegt, weil die Zahlungen den Rest abdecken. In `verrechne_guthaben` ist dieser Wechsel vorhanden.

**Ablauf:** Die Saisonrechnung beträgt 500 €. Davon sind 450 € mit Guthaben bezahlt, 50 € sind offen. Der Kunde nutzt eine freie Absage für einen Termin über 50 €. Dadurch ist `gutschrift = max(0, 50-50) = 0`, und `offener_betrag` wird 0.

**Folge:** Der Status bleibt `offen`, und `bezahlt_am` ist leer. Die Rechnung erscheint weiter als offen und kann gemahnt werden.

**Richtung:** Den Statuswechsel in eine gemeinsame Hilfsfunktion ziehen, die `korrigiere` und `verrechne_guthaben` beide aufrufen.

### 5. Text im Rechnungs-PDF

**Wo:** `core/beachhub_core/templates/rechnung_pdf.html:44`

**Fehler a:** Der Hinweis „Der Betrag ist bereits beglichen. Vielen Dank.“ hängt an `offen == 0`. `offener_betrag` liefert aber auch bei stornierten Rechnungen 0.
Ablauf: Die erste PDF-Erzeugung schlägt fehl, und die Rechnung wird komplett korrigiert (Status `storniert`), bevor jemand „PDF erzeugen“ auslöst. Das gespeicherte, per Hash gesicherte PDF behauptet dann „bereits beglichen“.

**Fehler b:** Der offene Betrag zieht Korrekturen ab, die in der Summentabelle nicht aufgeführt sind.
Ablauf: brutto 500, Guthaben 100, eine Korrektur −50. Die Tabelle zeigt 500 / −100 / „Offener Betrag 350“, und das geht nicht auf.

**Richtung:** Den Hinweis am Status festmachen, nicht am Betrag. Korrekturen als eigene Zeile in die Summentabelle aufnehmen.

### 6. Storno-Mail nennt einen falschen Grund

**Wo:** `core/beachhub_core/templates/mail/storno.txt:9`

**Fehler:** Wenn ein Abo-Termin kostenpflichtig storniert wird und noch freie Absagen übrig sind, nennt die Mail immer die abgelaufene Frist als Grund. Das stimmt nicht, wenn der Betreiber vor Ablauf der Frist ausdrücklich `kostenfrei='nein'` gewählt hat.

**Ablauf:** Der Betreiber storniert einen Abo-Termin 5 Tage vorher mit „kostenpflichtig: nein“, und der Kunde hat noch 3 freie Absagen. Die Mail schreibt „Die Absagefrist war bereits abgelaufen“.

**Folge:** Die Aussage ist sachlich falsch und führt zu Rückfragen und Streit.

**Richtung:** Den tatsächlichen Grund (Frist abgelaufen, keine freien Absagen mehr, Entscheidung des Betreibers) an das Template übergeben, statt ihn dort herzuleiten.

### 7. Freie Absage wird verbraucht, obwohl sie nichts bewirkt

**Wo:** `core/beachhub_core/services/storno.py:174`

**Fehler:** Eine freie Absage wird auch dann verbraucht, wenn die Rechnungsposition des Termins schon korrigiert wurde (Teilstorno) oder der Termin gar nicht abgerechnet ist. In beiden Fällen hat die Absage keine finanzielle Wirkung.

**Ablauf:** Der Betreiber storniert einen Saisontermin teilweise (Gutschrift, Buchung bleibt aktiv). Danach sagt der Kunde diesen Termin vor der Frist im Portal ab. Dabei wird `freie_absage=True` gespeichert, und der Zähler sinkt von 3 auf 2. `_offene_position` liefert `None`, es gibt also keine Gutschrift und kein Geld. Trotzdem ist eine freie Absage weg.

**Richtung:** Die freie Absage nur verbrauchen, wenn es eine offene Position gibt, die gutgeschrieben wird.

---

## Leistung und Aufräumen

### 8. N+1-Abfragen in der Rechnungsliste

**Wo:** `core/beachhub_core/routes/rechnungen.py:66`

Für jede Zeile wird `offener_betrag` einzeln berechnet, mit je 2 Aggregat-Abfragen (Korrekturen und verrechnete Zahlungen). Bei der Liste mit 500 Zeilen sind das bis zu 1000 zusätzliche SELECTs bei jedem Aufruf von `/admin/rechnungen`.

**Richtung:** Je eine gruppierte Abfrage für die Summe der Korrekturen und für die Summe der Zahlungen, jeweils nach `rechnung_id`.

### 9. Kundensperre fünfmal von Hand geschrieben

**Wo:** `core/beachhub_core/services/storno.py:146`, außerdem `storno.kulanz`, `rechnungen.sperre`, `erzeuge_saisonrechnung` und `storniere_fuer_kunde`

Die Sperre `db.execute(select(Kunde).where(Kunde.id == ...).with_for_update())` steht fünfmal im Code. Mit diesem Branch gibt es aber schon `kunden.sperre_mehrere` für genau diesen Zweck. Fünf Kopien müssen zur dokumentierten Sperrreihenfolge (Kunde → Rechnung → Buchung) passen.

**Richtung:** Überall `kunden.sperre_mehrere(db, [kunde_id])` aufrufen.

### 10. Doppelter Audit-Eintrag bei Saisonrechnungen

**Wo:** `core/beachhub_core/services/rechnungen.py:384`

`erzeuge_saisonrechnung` setzt `dauerbuchung_id` erst, nachdem `_neue_rechnung` zurückgekehrt ist, und schreibt dafür einen zweiten Audit-Eintrag. Jede Saisonrechnung bekommt so zwei Audit-Zeilen: das Anlegen ohne `dauerbuchung_id`, dann ein „update“, das nur dieses Feld ergänzt.

**Richtung:** `_neue_rechnung` einen Parameter `dauerbuchung_id` geben (wie schon `zahlungsziel_tage`).

---

## Nachprüfung der Korrekturen (Commit `9a4f1d0`)

**Teststand:** shared 41, core 554, portal 168, hall 158 Tests grün. mypy ohne Befund, Ruff sauber.

| Befund | Ergebnis |
|---|---|
| 1 Guthaben bei komplettem Storno | behoben: läuft über `gutschreiben_positionen`, der bezahlte Anteil wird Guthaben |
| 2 schon gutgeschriebene Termine | behoben: nur die bis dahin offenen Positionen werden freigegeben |
| 3 Spätabsagen in der Saisonrechnung | behoben: `saison_abrechenbar` nimmt kostenpflichtig stornierte Termine mit |
| 4 Status „bezahlt“ nach Korrektur | behoben: `_aktualisiere_bezahlt` gemeinsam genutzt |
| 5 Text im Rechnungs-PDF | behoben: Hinweis hängt am Status, Korrekturen stehen als eigene Zeile |
| 6 Grund in der Storno-Mail | behoben: Grund wird von `benachrichtigung.storno` übergeben |
| 7 freie Absage | Lücke 7a entstanden, mit `fc309f2` behoben |
| 8 bis 10 | behoben |

### 7a. Unbegrenzt viele kostenfreie Absagen bei Terminen ohne Rechnung (neu)

**Wo:** `core/beachhub_core/services/storno.py:174-179`

**Fehler:** Die Korrektur verbraucht keine freie Absage, sobald `_offene_position` `None` liefert. Das gilt aber auch für Termine, die **noch nicht berechnet** sind (`rechnung_position_id is None`). Diese Absagen bleiben trotzdem kostenfrei. Wegen `saison_abrechenbar` werden solche Termine auch später nie berechnet. Der Zähler sinkt also nie, und jede Absage vor Ablauf der Frist ist kostenlos.

Der Fehler geht auf eine zu breite Formulierung in Befund 7 zurück: Dort stand „oder der Termin gar nicht abgerechnet ist“. Das war falsch. Bei einem noch nicht berechneten Termin hat die Absage sehr wohl eine finanzielle Wirkung, denn er wird dann nie berechnet. Der Test `test_review_7_ohne_offene_position_keinen_kontingentverbrauch[False]` legt dieses falsche Verhalten jetzt fest.

**Belegt mit zwei Tests, beide rot:**
- Altabo (vor 0014, ohne Saisonrechnung): Der Kunde sagt alle 4 Termine ab. Alle sind kostenfrei, der Zähler bleibt bei 3.
- Abo mit Saisonrechnung, dann komplettes Storno zur Neuausstellung: In der Zeit bis zur Neuausstellung sagt der Kunde alle 5 Termine ab. Alle sind kostenfrei, der Zähler bleibt bei 3, und eine Neuausstellung ist nicht mehr möglich (`keine_termine`).

**Richtung:** Kein Kontingent verbrauchen nur dann, wenn die Buchung auf eine **schon korrigierte** Position zeigt (Teilstorno, also bereits gutgeschrieben). Bei `rechnung_position_id is None` wird wie bisher Kontingent verbraucht, und ist keines mehr übrig, ist die Absage kostenpflichtig. Den Test `[False]` entsprechend umdrehen, die beiden Abläufe oben als Tests aufnehmen und die Spec- und Doku-Stellen von Commit `9a4f1d0` anpassen.

**Behoben in `fc309f2`:** Eine freie Absage verbraucht Kontingent, außer die Buchung zeigt auf eine bereits korrigierte Position. Der Test `[False]` ist entfernt, die beiden Abläufe oben sind als Tests in `test_abo_absagen.py` aufgenommen. Spec A-DAUER-3, Betriebshandbuch, Plan und AGENTS.md sind angepasst. Teststand: shared 41, core 555, portal 168, hall 158 Tests grün; mypy und Ruff sauber.
