# Beachhub – Anforderungen und Systemdesign

Stand: 2026-09-23 (Betreiber-Antworten Runde 2) · Status: Entwurf zur Abstimmung · Zielgruppe: Entwicklungsteam

Diese Fassung arbeitet die Antworten des Betreibers aus dem Fragenkatalog 2 vom 23. September 2026 ein. Leitlinie dabei: **Betreiberentscheidungen werden Vorgabewerte, keine fest eingebauten Regeln** (N-5, Abschnitt 3.14). Wo der Betreiber anders entschieden hat als vorgeschlagen, bleibt die bisherige Variante als Einstellung erhalten; der Zahlungsanbieter ist ein Plugin. Wesentlich geändert in dieser Runde: Gutschein über eine Buchungseinheit statt über einen Eurobetrag, mit Umsatzsteuer bereits beim Kauf (3.9a), Freischaltcodes standardmäßig einmalig (A-GUT-2), bis zu drei kostenfreie Absagen im Abo mit Guthaben (A-DAUER-3), Rechnungskunden ohne Onlinebuchung (A-KUND-7), Saisonabo nur für DJK-Mitglieder (A-DAUER-5), Mitgliedschaft bis 30. April mit Abgleich am 31. August (A-KUND-4/5), Zahlungsanbieter als Plugin mit Stripe als Start (A-ZAHL-5). Offene Punkte stehen in 12.2 und im Dokument „Rückfragen zur dritten Runde“.

Die Fassung vom 11. September 2026 arbeitete die Antworten vom 10. September ein. Damals wesentlich geändert: zwei Kundengruppen mit eigenem Umsatzsteuersatz (3.2), Mitgliedsstatus mit Freischaltung und Jahresprüfung (3.2), Gutscheine und Freischaltcodes (3.9a), Saisonrechnung statt Monatsrechnung (3.9), Zahlungsanbieter offen hinter einem Adapter (3.8), Türcode ohne Sperre (3.10), Heizwerte des Betreibers (3.10), Gerätezuordnung nur noch im Hallendienst (3.1), die Hosting-Entscheidung für das Portal (11) und der ersatzlose Wegfall der Nachbuchung (3.7).

## 1. Ziel

Beachhub bucht, rechnet ab und steuert eine neue Beachvolleyballhalle im Winterbetrieb, die Ende 2027 eröffnet. Das System soll ab Eröffnung im Einsatz sein; die Hallentechnik (Tür, Tastenfeld, Sensoren, Heizung) wird während der Bauphase mit Blick auf die Anbindung an Home Assistant festgelegt.
Kunden buchen Felder über ein Internetportal, der Betreiber verwaltet Felder, Preise,
Dauerbuchungen und Rechnungen, und die Halle schaltet Licht, Heizung und Tür
automatisch passend zu den Buchungen – auch dann, wenn die Internetverbindung
mehrere Tage ausfällt.

Beteiligte: Betreiber der Halle (Auftraggeber, Administrator), zwei Entwickler,
Kunden (DJK-Mitglieder und Nicht-Mitglieder) und deren Mitspieler.

## 2. Systemübersicht

Drei Systeme, ein Integrationsprinzip: **Das Hauptsystem baut alle Verbindungen
selbst auf. Kein System verbindet sich zum Hauptsystem.**

| System | Ort | Hält | Entscheidet |
|---|---|---|---|
| **Buchungsportal** (`portal/`) | Eigener Container auf demselben Proxmox-Host, aus dem Internet erreichbar (Abschnitt 11) | Konten (E-Mail, Anzeigename, Kundengruppe), Login-Codes, Anfragetabelle, signierter Lesestand, Gruppenverwaltung | Fachlich nichts. Login, Eingabevalidierung, Gruppenverwaltung |
| **Hauptsystem** (`core/`) | Eigener Container auf dem Proxmox-Host, nur per VPN erreichbar | Alle Stammdaten, Tarife, Buchungen, Rechnungen, Zahlungen, Guthaben, PINs, Ereignisse, Audit-Log | Verfügbarkeit, Preis, Storno, Rechnung, PIN, Alarme |
| **Hallendienst** (`hall/`) | Rechner in der Halle, neben Home Assistant | Betriebsplan der nächsten 7 Tage (Buchungs-ID, Feld, Zeit, PIN-Hash), Ereignis-Warteschlange | Licht, Heizung, Türfreigabe, Präsenzbewertung; autonom bis 7 Tage |

```
 Kunde ──HTTPS──▶ Buchungsportal ◀──Long-Poll/mTLS (ausgehend)── Hauptsystem ──WireGuard (ausgehend vom Hallendienst)──▶ Hallendienst ──REST/WS──▶ Home Assistant
                  (Anfragetabelle,                                 (Master, Admin-UI                                         (SQLite, 7-Tage-Plan)       (Licht, Heizung,
                   Lesestand, Gruppen)                              nur im VPN)                                                                            Tür, Sensoren)
```

Das Admin-UI ist Teil des Hauptsystems und ausschließlich über VPN erreichbar.

## 3. Fachliche Anforderungen

Nummerierung `A-<Bereich>-<Nr>` zur Referenz in Tickets und Tests.

### 3.1 Felder und Betriebszeiten

- **A-FELD-1** Anzahl Felder ist konfigurierbar (Start: 3). Felder haben Name, Aktiv-Flag, Reihenfolge.
- **A-FELD-2** Je Feld ist ein Slot-Raster konfigurierbar: entweder eine Slot-Dauer in Minuten (30, 60, 90, 120 …) oder eine feste Liste von Zeitfenstern (z. B. 19–21, 21–23). Ein Feld kann je Wochentag unterschiedliche Raster haben.
- **A-FELD-3** Hallenweite Betriebszeiten: Öffnung/Schließung je Wochentag, Saison von/bis, Ausnahmetage (geschlossen oder Sonderzeiten). Slots außerhalb der Betriebszeiten sind nicht buchbar.
- **A-FELD-4** Das Hauptsystem kennt kein Gerät. Welche Lampe, welcher Präsenzsensor und welche Heizzone zu einem Feld gehören, steht ausschließlich in der Konfiguration des Hallendienstes auf dem Home-Assistant-Server. Das Hauptsystem liefert je Feld nur den Belegungsplan und die Schaltparameter (Vorläufe, Temperaturen); die Zuordnung zu Entitäten macht der Hallendienst. Felder werden zwischen beiden Systemen über ihre UUID verbunden.

### 3.2 Kunden und Kundengruppen

- **A-KUND-1** Kunde: Name, E-Mail (Schlüssel zum Portalkonto), Rechnungsadresse, Guthaben, Mitgliedsstatus (A-KUND-4), Kennzeichen **Rechnungskunde** (A-KUND-7). Die Zahlungsart steht an der **Buchung** (A-ZAHL-1); am Kunden steht nur, ob er Rechnungskunde ist. Das Kennzeichen ersetzt das Feld `zahlungsart` (`online`/`rechnung`) aus Stufe 1.
- **A-KUND-2** Es gibt genau zwei Kundengruppen: **DJK-Mitglied** und **Nicht-Mitglied**. Jede Gruppe trägt ihren Umsatzsteuersatz (Mitglied 7 %, Nicht-Mitglied 19 %, beide konfigurierbar) und bestimmt über die Tarife den Preis. Die Gruppen sind fest; der Betreiber kann sie umbenennen und ihre Sätze ändern, aber keine dritte anlegen.
- **A-KUND-3** Neue Portalkonten sind Nicht-Mitglied. Wer den Freischaltprozess nicht durchläuft, sieht dauerhaft die Preise für Nicht-Mitglieder.
- **A-KUND-4** **Mitgliedsstatus.** Der Kunde beantragt im Portal die Freischaltung als DJK-Mitglied (Anfragetyp `mitgliedschaft_beantragen`, Freitext für Mitgliedsnummer oder Name in der Mitgliederliste). Der Betreiber prüft gegen die Vereinsverwaltung und setzt im Admin-UI `mitglied_bis` (vorbelegt mit dem nächsten Ablauftag, Einstellung `mitgliedschaft_ablauf`, Vorgabe **30. April**: Ende der Wintermitgliedschaft). Geprüft wird immer nur die buchende Person; Mitglied können nur Privatpersonen sein, der Nachweis läuft über die Mitgliedsnummer im Antrag (Betreiber, 23.09.2026). Der Status läuft damit von selbst ab; es gibt keinen Job, der Gruppen umschreibt. Entzieht der Betreiber den Status vorzeitig, bleiben bereits bestätigte Buchungen zu ihren festgeschriebenen Konditionen bestehen (A-TARIF-3); betroffene künftige Termine erscheinen in der Klärungsliste, der Betreiber entscheidet einzeln.
- **A-KUND-5** **Jahresabgleich.** Am Stichtag `mitglieder_abgleich` (Tag und Monat, Vorgabe **31. August**, Betreiber 23.09.2026) erzeugt das System eine Prüfliste aller Kunden, deren Mitgliedschaft zum letzten Ablauftag ausgelaufen ist oder bis zum nächsten ausläuft (Admin-Seite und CSV), mit den Sammelaktionen „bis zum nächsten Ablauftag verlängern“ und „beenden“. Spätere Mitgliedschaften laufen über den Antrag (A-KUND-4) und werden laufend eingepflegt. Der Stichtag muss **vor** dem ersten Buchungsfenster der Saison liegen (Saisonstart minus `fenster_tage`), sonst buchen Mitglieder die ersten Termine zu Nicht-Mitglied-Preisen; das Admin-UI warnt, wenn die Einstellungen das verletzen. Bei 14 Tagen Fenster passt der 31. August für jeden Saisonstart ab dem 14. September.
- **A-KUND-6** **Gruppe zum Leistungsdatum.** Preis und Steuersatz richten sich nach der Kundengruppe, die zum *Termin* gilt, nicht zum Buchungszeitpunkt: Bucht ein Mitglied mit `mitglied_bis = 31.03.` am 20.03. einen Termin am 05.04., gelten die Konditionen für Nicht-Mitglieder. Die Auflösung liefert `kunden.effektive_gruppe(kunde, leistungsdatum)`.
- **A-KUND-7** **Rechnungskunden.** Der Betreiber markiert Kunden als Rechnungskunde – typisch Abo-Kunden und Veranstalter anderer Events. Deren Buchungen legt der Betreiber an (Dauerbuchung mit Saisonrechnung, sonst Buchung mit Zahlungsart `manuell` und Rechnung). Einstellung `rechnungskunden_online_buchen` (Vorgabe **nein**, Betreiber 23.09.2026): Ist sie aus, lehnt das Hauptsystem `buchung_anfragen` und `gutschein_kaufen` eines Rechnungskunden mit Grund `rechnungskunde` ab, und das Portal blendet Buchen und Gutscheinkauf aus (Lesestand `konto:<id>` trägt `rechnungskunde`). Termine sehen, Rechnungen laden und Abo-Termine absagen (A-DAUER-3) bleibt möglich. Ist sie an, buchen Rechnungskunden Einzeltermine wie alle anderen und zahlen online – die bisherige Lesart. Eine Zahlung „auf Rechnung“ im Portal gibt es in keinem Fall.

### 3.3 Tarife

- **A-TARIF-1** Preisregeln mit Gültigkeit nach Feld, Wochentag, Uhrzeitbereich, Kundengruppe und Zeitraum (von/bis). Preis je Slot, in Euro mit zwei Nachkommastellen, inklusive USt. Der Steuersatz steht **nicht** am Tarif, sondern an der Kundengruppe (A-KUND-2); aus dem Bruttopreis und dem Satz der Gruppe ergeben sich Netto und Steuer.
- **A-TARIF-2** Auflösung: die spezifischste passende Regel gewinnt (mehr gesetzte Kriterien = spezifischer); bei Gleichstand die neuere. Gibt es keine Regel, ist der Slot nicht buchbar und der Betreiber wird gewarnt.
- **A-TARIF-3** Preis **und Steuersatz** werden zum Zeitpunkt der Buchung ermittelt und an der Buchung gespeichert. Spätere Tarif-, Steuersatz- oder Statusänderungen ändern bestehende Buchungen nicht. Der Satz muss mitgeschrieben werden, weil sich mit zwei Kundengruppen sonst nicht mehr rekonstruieren lässt, welche Steuer zu einer alten Buchung gehört.

### 3.4 Buchungen

- **A-BUCH-1** Einzelbuchung: ein Feld, ein zusammenhängender Zeitraum aus einem oder mehreren aufeinanderfolgenden Slots des Feldes.
- **A-BUCH-2** Statuskette: `angefragt → reserviert → bestätigt → durchgeführt | nicht_erschienen | storniert`. Zusätzlich `abgelehnt` (aus `angefragt`) und `verfallen` (aus `reserviert`, Zahlungsfrist abgelaufen).
- **A-BUCH-3** Keine Überschneidung zweier Buchungen oder Sperren auf demselben Feld. Die Prüfung erfolgt im Hauptsystem innerhalb einer Datenbanktransaktion mit Exklusionsconstraint.
- **A-BUCH-4** Jede bestätigte Buchung erhält eine PIN (6-stellig, zufällig, je Buchung eindeutig innerhalb ihres Zeitfensters ± Vorlauf). Dauerbuchungen erhalten eine PIN für alle Termine.
- **A-BUCH-5** **Buchungsfenster:** Einzelbuchungen im Portal sind nur möglich von `fenster_tage` vor dem Termin (konfigurierbar, Start: 14 Tage) bis `mindestvorlauf_minuten` vor Slotbeginn (konfigurierbar, Start: 60). Dauerbuchungen und Sperren unterliegen dem Fenster nicht.
- **A-BUCH-6** Der Betreiber kann im Admin-UI Buchungen ohne Fenster und ohne Zahlungsschritt anlegen, ändern und stornieren (mit Auditeintrag).

### 3.5 Dauerbuchungen

- **A-DAUER-1** Regel: Feld, Wochentag, Zeitfenster, Zeitraum von/bis, Kunde. Bei Anlage werden alle Einzeltermine als Buchungen erzeugt (Status `bestätigt`, Zahlungsart `saison`). Kollisionen mit bestehenden Buchungen/Sperren werden vor der Anlage gelistet; der Betreiber entscheidet je Termin (auslassen oder bestehende Buchung stornieren).
- **A-DAUER-2** Anlage nur durch den Betreiber und nur für Rechnungskunden (das Anlageformular setzt das Kennzeichen auf Nachfrage mit). Ein Abo lässt sich **nicht online abschließen**; Kunden fragen es per E-Mail an. Das Portal bietet dafür keinen Anfragetyp, sondern nur einen Hinweis mit der Kontaktadresse des Betreibers. (Der frühere Anfragetyp `dauerbuchung_anfragen` entfällt.)
- **A-DAUER-3** **Absagen im Abo.** Das Saisonabo ist ein Festpreis und wird vorab komplett bezahlt (Saisonrechnung, A-RECH-3). Einzeltermine sagt der Kunde im Portal ab; der Slot wird sofort frei. Kostenfrei sind je Dauerbuchung bis zu `abo_freie_absagen` Termine (Vorgabe **3**, Betreiber 23.09.2026; `0` heißt „Saison fest bezahlt“, der frühere Vorschlag), und zwar nur innerhalb der Stornofrist (A-STORNO-1, Annahme Ⓞ-22). Eine kostenfreie Absage erzeugt eine Teil-Stornorechnung über den Termin (A-RECH-7); ist die Saisonrechnung bezahlt, wird der Betrag Guthaben (A-ZAHL-4), sonst sinkt die Forderung. Jede weitere Absage und jede Absage nach der Frist gibt den Slot frei, ändert aber nichts am Preis. Gezählt werden nur kostenfreie Absagen durch den Kunden (`storno.freie_absage`); Kulanz (A-STORNO-4), Sperren und das Beenden durch den Betreiber zählen nicht. Das Portal zeigt je Abo die verbleibenden freien Absagen. Der Wert eines Termins ist sein an der Buchung festgeschriebener Preis; die Saisonrechnung ist die Summe dieser Preise.
- **A-DAUER-4** Betreiber kann eine Dauerbuchung ab einem Datum beenden; künftige Termine werden storniert (kostenfrei).
- **A-DAUER-5** **Saisonabo nur für Mitglieder.** Einstellung `abo_nur_mitglieder` (Vorgabe **ja**, Betreiber 23.09.2026: für ein Saisonabo ist die DJK-Mitgliedschaft zwingend, alle Abo-Termine laufen damit zu 7 %). Ist sie an, lehnt das Admin-UI die Anlage ab, wenn die Mitgliedschaft des Kunden nicht bis zum letzten Termin reicht, und bietet an, sie zuerst zu verlängern. Endet die Mitgliedschaft später vorzeitig, gilt A-KUND-4 (betroffene Termine in die Klärungsliste). Ist sie aus, bekommen auch Nicht-Mitglieder Abos zu ihren Konditionen.

### 3.6 Sperren

- **A-SPERR-1** Betreiber-Sperren: Feld(er), Zeitraum, Grund (Turnier, Wartung, Eigenbedarf, Feiertag). Sperren blockieren Buchungen, erzeugen keine Kosten und erscheinen im Portal als „nicht verfügbar“ ohne Grund.
- **A-SPERR-2** Sperren über bestehenden Buchungen erfordern eine Entscheidung je betroffener Buchung (behalten oder kostenfrei stornieren mit Benachrichtigung).

### 3.7 Storno

- **A-STORNO-1** Kostenfreies Storno bis `storno_frist_stunden` vor Slotbeginn (konfigurierbar, Start: 24 h). Der Slot wird sofort wieder frei.
- **A-STORNO-2** Storno nach der Frist: Die Zahlungspflicht bleibt bestehen, der Slot wird trotzdem sofort freigegeben. Bucht danach ein anderer Kunde diesen Zeitraum, ändert das am fälligen Betrag nichts.
- **A-STORNO-3** *(entfallen)* **Keine Nachbuchung.** Ein nach der Frist storniertes Entgelt bleibt fällig, unabhängig davon, ob der Platz anschließend anderweitig belegt wird. Die frühere Regel – ein anderer Kunde bucht den Zeitraum, das Storno wird rückwirkend (anteilig) kostenfrei – ist auf Entscheidung des Betreibers ersatzlos gestrichen, nachdem er anmerkte, den Begriff nicht zuordnen zu können. Damit entfallen die Felder `nachbuchung_offen`, `nachbuchung_buchung_id` und `freigestellt_betrag` am Storno, der Hook in `buchungen.NACH_ANLAGE` und die anteilige Verrechnung. Der einzige Weg, ein kostenpflichtiges Storno nachträglich freizustellen, ist die Kulanz des Betreibers (A-STORNO-4).
- **A-STORNO-4** Der Betreiber kann jedes Storno manuell auf kostenfrei setzen (Kulanz), mit Grund und Auditeintrag.
- **A-STORNO-5** Storno durch den Kunden ist nur bis Slotbeginn möglich.
- **A-STORNO-6** **Jede Gutschrift braucht einen Korrekturbeleg.** Wird eine Buchung kostenfrei (Frist oder Kulanz), deren Rechnung bereits gestellt ist, entsteht das Guthaben ausschließlich zusammen mit einer (Teil-)Stornorechnung über die betroffene Position. Ohne sie bliebe Umsatzsteuer auf eine nicht erbrachte Leistung abgeführt (§ 17 UStG). Guthabenbuchungen der Art `storno_gutschrift` dürfen deshalb nur über den Dienst entstehen, der zuvor die Stornoposition erzeugt. *Dies korrigiert eine bestehende Lücke in `services/storno.py`, die mit einem einheitlichen Satz von 19 % nur formal falsch war und mit zwei Sätzen nicht mehr tragbar ist.*
- **A-STORNO-7** Der mit einem Gutschein oder Freischaltcode gedeckte Teil einer Buchung erzeugt im Modell `einheit` (A-GUT-1) bei Storno **niemals** Guthaben. Ist das Storno kostenfrei, wird die Einlösung rückgängig gemacht und der Code wieder gültig (A-GUT-6); ist es kostenpflichtig, bleibt er verbraucht. Ein Freischaltcode hatte nie einen Geldwert und erzeugt in keinem Modell Guthaben.

### 3.8 Zahlung

- **A-ZAHL-1** Die Zahlungsart gehört zur **Buchung**: `online` (Einzelbuchung, sofort über den Zahlungsdienst), `saison` (Termin einer Dauerbuchung, über die Saisonrechnung per Überweisung), `manuell` (vom Betreiber im Admin-UI angelegt, Rechnung `offen`) oder `gutschein` (vollständig durch Gutschein oder Freischaltcode gedeckt). Im Portal gibt es die Zahlungsart „auf Rechnung“ nicht. Ob Rechnungskunden überhaupt online buchen, regelt A-KUND-7 (Betreiber 23.09.2026: nein).
- **A-ZAHL-2** Online: Nach `reserviert` erzeugt das Hauptsystem über den Zahlungsadapter (A-ZAHL-5) eine Bezahlsitzung (Betrag, Buchungs-ID als Referenz, Ablauf `zahlungsfrist_minuten`, Start: 15). Der Kunde zahlt im Portal. Die Rückmeldung des Anbieters trifft im Portal ein und wird als Anfrage `zahlung_eingegangen` gespeichert; das Hauptsystem stellt den Zahlungseingang **selbst** fest – entweder durch Prüfung der Signatur (Stripe) oder durch aktive Rückfrage beim Anbieter (Mollie meldet nur eine Zahlungs-ID und erwartet, dass der Händler den Status abruft). Das Portal bleibt in beiden Fällen ein reiner Briefkasten ohne Geheimnisse. Erst danach wird die Buchung `bestätigt`.
- **A-ZAHL-3** Verfällt die Zahlungsfrist, wird die Buchung `verfallen` und der Slot frei. Trifft die Zahlung dennoch später ein, entsteht Guthaben und der Betreiber wird informiert.
- **A-ZAHL-4** Guthaben: je Kunde ein Guthabenkonto in Euro. Guthaben wird bei Onlinebuchungen automatisch verrechnet (Restbetrag über den Zahlungsdienst; ist das Guthaben ausreichend, entfällt der Zahlungsschritt). Rechnungskunden buchen in der Regel nicht online; ihr Guthaben – meist aus freien Abo-Absagen – wird deshalb bei der nächsten **Saisonrechnung** verrechnet (Einstellung `guthaben_auf_saisonrechnung`, Vorgabe ja): Die Rechnung weist den vollen Betrag aus und darunter „verrechnetes Guthaben“ und „offener Betrag“; die Verrechnung ist eine Zahlung (`provider = guthaben`), keine Preisminderung. Zum **Saisonende** (Einstellung `saisonende_guthabenliste`, Vorgabe letzter Tag der Saison) erzeugt das System eine Liste aller Kunden mit Guthaben; der Betreiber zahlt auf Wunsch des Kunden aus und hakt ab (Betreiber 23.09.2026: „verrechnen oder zum Saisonende auszahlen“). Das System überweist nie selbst Geld zurück.
- **A-ZAHL-5** **Zahlungsanbieter als Plugin.** Jeder Anbieter ist ein Plugin, das die Schnittstelle `PaymentProvider` erfüllt (Session erzeugen, Rückmeldung verifizieren, Status abfragen; Portal-Kern-Design § 5) und sich über den Entry-Point-Gruppennamen `beachhub.zahlung` registriert. Mitgeliefert werden `stripe`, `mollie` und `fake` (nur Entwicklung und Test); ein weiterer Anbieter ist ein eigenes Paket, keine Änderung am Kern. Welches Plugin **neue** Bezahlsitzungen erzeugt, legt die Einstellung `zahlung_anbieter` fest (Vorgabe **`stripe`**, Betreiber 23.09.2026: „zunächst Stripe, sofern später ein Wechsel möglich ist“). Zugangsdaten stehen je Plugin in der Umgebung des Hauptsystems (`ZAHLUNG_<NAME>_*`), nie in der Datenbank. Bei einem Wechsel bleiben alle installierten Plugins geladen: Jede Zeile `zahlung` trägt ihren `provider`, und Rückmeldungen und Statusabfragen laufen immer über das Plugin, bei dem die Zahlung begonnen hat. Das Portal nimmt Rückmeldungen je Anbieter unter `/zahlung/rueckmeldung/<provider>` an, ohne sie zu verstehen. Stand der Prüfung (September 2026, Beispiel 40 € Feldmiete):

  | Anbieter | Karte EU | Wero | PayPal | SEPA-Überweisung | Grundgebühr |
  |---|---|---|---|---|---|
  | Stripe | 1,5 % + 0,25 € = 0,85 € | **nicht verfügbar** | ja | – | keine |
  | Mollie | 1,8 % + 0,25 € = 0,97 € | ~0,90 % + 0,25 € = 0,61 € | PayPal-Gebühr + 0,10 € | 0,25 € | keine |

  Stripe ist bei Kartenzahlung um 12 Cent je Buchung günstiger, führt aber kein Wero. Mollie deckt alle vier vom Betreiber genannten Wege (PayPal, Wero, Sofortüberweisung, Karte) in einem Vertrag ab. Der Betreiber startet mit Stripe und verzichtet damit vorerst auf Wero. Die beiden unterscheiden sich in der Art der Rückmeldung (A-ZAHL-2: Signatur bei Stripe, aktive Statusabfrage bei Mollie); das kapselt jeweils das Plugin, der Kern ruft in beiden Fällen `verifiziere` und `status` auf. Welche Zahlungswege Kunden sehen, legt das Plugin anhand der Einstellung des Anbieterkontos fest, nicht der Kern.
- **A-ZAHL-6** Das System löst keine Erstattungen, Auszahlungen oder Lastschriften aus.
- **A-ZAHL-7** Saisonrechnungen werden per Banküberweisung beglichen. Es gibt keinen Bankabgleich; der Betreiber hakt den Eingang im Admin-UI ab.

### 3.9 Rechnungen

- **A-RECH-1** Rechnungen nach § 14 UStG: fortlaufende, lückenlose Nummer (`JJJJ-NNNNN`), Rechnungsdatum, Leistungsdatum/-zeitraum, Betreiberdaten inkl. USt-IdNr./Steuernummer, Kundendaten, Positionen mit Netto, USt-Satz, USt, Brutto.
- **A-RECH-2** Onlinezahler: eine Rechnung je bestätigter Buchung, erzeugt bei Zahlungseingang, Status `bezahlt`.
- **A-RECH-3** **Saisonrechnung statt Monatsrechnung.** Eine Monatsrechnung wird nicht angeboten. Stattdessen erhält jede Dauerbuchung bei ihrer Anlage genau eine Saisonrechnung als Vorausrechnung über alle Termine (Leistungszeitraum erster bis letzter Termin, Status `offen`), die sofort per Mail an den Kunden geht; Zahlungsziel `saison_zahlungsziel_tage` (Vorgabe **14**, Betreiber 23.09.2026). Der Betreiber setzt den Zahlungseingang manuell auf `bezahlt`. Ist die Rechnung bis zum ersten Termin nicht bezahlt, bleiben die Termine und die PIN gültig; der Betreiber sieht offene Rechnungen in der Übersicht und mahnt außerhalb des Systems (Betreiber 23.09.2026). Der bisherige Monatslauf – Job, Marker `monatslauf_letzter`, Konfigurationswert `rechnung_tag_im_folgemonat`, CLI-Befehl und Admin-Schaltfläche – entfällt ersatzlos.
- **A-RECH-7** **Änderungen an einer Saisonrechnung** laufen ausschließlich über Teil-Stornorechnungen, nie über nachträgliche Änderung der ursprünglichen Rechnung: Wird eine Dauerbuchung ab einem Datum beendet oder ein Termin kostenfrei storniert (freie Absage nach A-DAUER-3 oder Kulanz), entsteht eine Stornorechnung über die betroffenen Positionen. Ist die Saisonrechnung bereits bezahlt, wird der Betrag zu Guthaben; ist sie noch offen, verringert sich die Forderung. Kommen Termine hinzu (Verlängerung, zweiter Wochentag), entsteht eine **neue** Dauerbuchung mit eigener Saisonrechnung; die bestehende bleibt unberührt. Abschlags- und Schlussrechnungen werden bewusst nicht verwendet: Sie erforderten den gesonderten Abzug der Abschlagssteuer nach § 14 Abs. 5 S. 2 UStG und damit einen weiteren Belegtyp, der sich bei einer festen Terminliste nicht lohnt.
- **A-RECH-8** **Zwei Steuersätze.** Eine Rechnung kann Positionen mit unterschiedlichen Sätzen enthalten (7 % und 19 %), etwa wenn sich der Mitgliedsstatus während einer Saison ändert. Der Satz steht deshalb an der Position, nicht am Rechnungskopf; Netto- und Steuerbeträge werden **je Satz** summiert und im PDF je Satz ausgewiesen. Gerundet wird je Position, nicht auf die Bruttosumme.
- **A-RECH-9** **Steuersatz bei Betreiberbuchungen.** Legt der Betreiber eine Buchung selbst an (Event, Zahlungsart `manuell`), ist der Satz mit dem der Kundengruppe zum Leistungsdatum vorbelegt und im Formular änderbar (mit Auditeintrag). Hintergrund: Nach Angabe des Betreibers laufen „andere Events“ zu 19 % – ob das auch gilt, wenn ein Mitglied bucht, klärt der Steuerberater (Ⓞ-8). Die Umstellung ist dann eine Vorbelegung, kein Umbau.
- **A-RECH-4** Rechnungs-PDFs werden unveränderbar archiviert (Datei + SHA-256 in der Datenbank). Korrekturen nur per Stornorechnung (Gutschrift) und Neuausstellung.
- **A-RECH-5** Kunden laden Rechnungen im Portal herunter. Das Portal speichert keine Rechnungen; es fordert das PDF über die Anfragetabelle an und stellt es über einen einmaligen, 10 Minuten gültigen Link bereit.
- **A-RECH-6** Export: CSV aller Rechnungen und Positionen je Zeitraum für die externe Buchhaltung.

### 3.9a Gutscheine und Freischaltcodes

Zwei Vorgänge, die der Betreiber unterscheidet: Jemand **kauft** einen Slot in der Zukunft ohne Datum und Zeit, und jemand **löst** einen Code bei der Platzbuchung ein – wobei der Code entweder gekauft oder vom Betreiber ausgestellt sein kann.

Der Betreiber hat sich am 23.09.2026 für einen Gutschein entschieden, der eine **Buchungseinheit** deckt, unabhängig vom dann gültigen Preis. Der frühere Vorschlag (Eurobetrag mit Restwert) bleibt als Einstellung erhalten; beide Modelle teilen Code, Einlösung in der Buchungstransaktion, Zustände und Missbrauchsschutz.

- **A-GUT-1** **Gutscheinmodell** (Einstellung `gutschein_modell`):
  - **`einheit`** (Vorgabe): Ein gekaufter Gutschein deckt genau eine Buchungseinheit – ein Feld für bis zu `gutschein_minuten` (Vorgabe **120**) –, egal auf welchem Feld, an welchem Tag und zu welcher Uhrzeit, und egal, was dieser Slot bei der Einlösung kostet. Preiserhöhungen zwischen Kauf und Einlösung trägt der Betreiber. Es gibt keinen Restwert: Eine kürzere Buchung verbraucht den Gutschein ganz. Ist die Buchung länger, deckt der Gutschein die ersten `gutschein_minuten`, und die übrigen Slots zahlt der Kunde zu ihrem Tarif online. Je Buchung höchstens `gutscheine_je_buchung` Codes (Vorgabe **1**; Annahme Ⓞ-20).
  - **`wert`**: Der Gutschein trägt einen Euro-Betrag; reicht er nicht, zahlt der Einlösende die Differenz online, und ein Rest bleibt als Restwert auf dem Gutschein (Stand 11.09.2026).
- **A-GUT-1a** **Kaufpreis.** Im Modell `einheit` hat der Gutschein je Kundengruppe einen festen Preis (`gutschein_preis_mitglied`, `gutschein_preis_nichtmitglied`; die Beträge sind offen, Ⓞ-18). Es gilt die Gruppe des **Käufers** am Kaufdatum (Betreiber 23.09.2026); der Gutschein speichert Gruppe, Preis und Steuersatz des Kaufs. Im Modell `wert` wählt der Käufer einen Betrag aus einer konfigurierbaren Liste.
- **A-GUT-1b** **Einlösbarkeit.** Einstellung `mitgliedsgutschein_nur_mitglieder` (Vorgabe **ja**, Annahme Ⓞ-19): Ein zum Mitgliedspreis gekaufter Gutschein ist nur von Mitgliedern einlösbar (Ablehnungsgrund `gutschein_nur_mitglieder`), ein zum Nicht-Mitglied-Preis gekaufter von allen. Ohne diese Regel könnte ein Mitglied beliebig viele günstige Gutscheine für Nicht-Mitglieder kaufen, und die beim Kauf festgelegten 7 % (A-GUT-5) stünden auf einer Leistung, die tatsächlich ein Nicht-Mitglied nutzt.
- **A-GUT-2** **Freischaltcode** (`art = frei`): eigener Typ ohne Geldwert, vom Betreiber ausgestellt (Werbung, Entschädigung, Eröffnungsaktion). Er schaltet wie ein gekaufter Gutschein eine Buchungseinheit frei (ein Feld, bis zu `gutschein_minuten`), kein Guthaben. Je Code: `max_einloesungen` (Vorgabe **1**, Betreiber 23.09.2026: „jeder Code gilt nur ein einziges Mal“; höhere Werte bleiben für Aktionscodes möglich), Gültigkeit (Vorgabe `freicode_gueltig_tage` = 90) und optional Feld und Zeitraum (Annahme Ⓞ-21: ohne Vorgabe wählt der Einlösende). Eine Serie besteht aus vielen Einzelcodes, exportierbar als CSV.
- **A-GUT-3** **Einlösung ist Teil der Buchung, nicht ein eigener Schritt.** `buchung_anfragen` trägt ein optionales Feld `gutschein_code`. Das Hauptsystem sperrt die Gutscheinzeile innerhalb der Buchungstransaktion (`SELECT … FOR UPDATE`), reserviert den Slot, verrechnet erst den Gutschein, dann Guthaben, und gibt eine Bezahlsitzung nur über den Rest zurück. Zwei getrennte Schritte („Code prüfen“, dann „buchen“) würden ein Wettrennen zweier Kunden mit demselben Code erlauben.
- **A-GUT-4** **Zustände:** `aktiv → eingeloest` (Modell `wert` zusätzlich `teilweise_eingeloest`), daneben `verfallen` (Nachtjob bei Ablauf) und `gesperrt` (Betreiber, etwa bei Missbrauch oder nach Rückzahlung). Gültigkeit gekaufter Gutscheine: `gutschein_gueltig_tage` ab Kauf, Vorgabe **730** (Betreiber 23.09.2026). *Rechtlicher Hinweis, dem Betreiber mitgeteilt:* Gekaufte Gutscheine unterliegen der regelmäßigen Verjährung (drei Jahre ab Jahresende, §§ 195, 199 BGB); eine kürzere Frist in AGB haben Gerichte bei einem Jahr als unangemessen verworfen, für zwei Jahre ist das nicht abschließend geklärt. Das System lässt deshalb zu, dass der Betreiber einen verfallenen Gutschein mit neuem Ablaufdatum wieder aktiviert (Auditeintrag). Freischaltcodes dürfen kurz befristet sein.
- **A-GUT-5** **Umsatzsteuer** (Einstellung `gutschein_steuer`):
  - **`beim_kauf`** (Vorgabe, Betreiber 23.09.2026: „Gutscheine enthalten beim Kauf MwSt., 7 % bei einem DJK-Mitglied, 19 % bei einem Nicht-Mitglied“): **Einzweckgutschein** nach § 3 Abs. 14 UStG. Die Leistung gilt mit der Ausgabe als erbracht; beim Kauf entsteht eine reguläre Rechnung (`art = gutschein`, normaler Nummernkreis, Leistungsdatum = Kaufdatum) mit dem Satz der Käufergruppe. Die Einlösung löst keine Steuer und keine weitere Rechnung aus; die Buchung erhält Zahlungsart `gutschein` und eine Buchungsbestätigung ohne Steuerausweis. Nur ein online gezahlter Aufpreis (längere Buchung) wird regulär mit dem Satz des Einlösenden abgerechnet. Verfall ändert nichts mehr an der Steuer. Voraussetzung ist, dass der Satz bei Ausgabe feststeht und zur späteren Leistung passt – deshalb A-GUT-1b.
  - **`bei_einloesung`** (Stand 11.09.2026): **Mehrzweckgutschein** nach § 3 Abs. 15 UStG. Der Kaufbeleg weist **keine** Umsatzsteuer aus (sonst nach § 14c UStG geschuldet), läuft in einem eigenen Nummernkreis und trägt einen Hinweis; die Rechnung entsteht bei der Einlösung mit dem Satz des Einlösenden. Diese Variante braucht A-GUT-1b nicht.

  **Vom Steuerberater bestätigen lassen** (Ⓞ-8), bevor der erste Gutschein verkauft wird: Ein Wechsel der Einstellung gilt nur für danach verkaufte Gutscheine; jeder Gutschein speichert, nach welcher Variante er verkauft wurde.
- **A-GUT-6** **Storno einer mit Gutschein gedeckten Buchung.** Modell `einheit`: Wird das Storno kostenfrei, wird die Einlösung rückgängig gemacht (`gutschein_einloesung.rueckgaengig_am`) und der Gutschein wieder `aktiv`, mit seinem ursprünglichen Ablaufdatum und dem Einlösenden als Inhaber (`inhaber_kunde_id`), damit er ihn im Portal wiederfindet. Es entsteht kein Guthaben und bei `beim_kauf` kein Korrekturbeleg, weil die Einlösung keine Steuer ausgelöst hat. Ein online gezahlter Aufpreis läuft den normalen Weg (A-STORNO-6). Nach der Frist bleibt der Gutschein verbraucht. Modell `wert`: Der Wertanteil wird dem Einlösenden als Guthaben gutgeschrieben, zusammen mit Korrekturbeleg (A-STORNO-6). Freischaltcodes: A-STORNO-7.
- **A-GUT-7** **Missbrauchsschutz:** Codes mit mindestens 60 Bit Entropie aus einem Alphabet ohne verwechselbare Zeichen (kein 0/O, 1/I); Rate-Limit auf Einlöseversuche je Konto und IP.
- **A-GUT-8** Der Kauf setzt ein Portalkonto voraus (Betreiber 23.09.2026). Einstellung `gutschein_gastkauf` (Vorgabe nein) ist für einen späteren Kauf ohne Anmeldung vorgesehen, aber nicht Teil von Stufe 2.
- **A-GUT-9** **Rückgabe.** Zahlt der Betreiber einen gekauften Gutschein zurück (etwa nach Widerruf), sperrt er ihn im Admin-UI; bei `beim_kauf` entsteht dabei eine Stornorechnung zur Kaufrechnung. Das Geld überweist der Betreiber selbst (A-ZAHL-6).

### 3.10 Hallensteuerung

- **A-HALLE-1** Heizung: Sollwert `spiel_temperatur` (Start: **18 °C**) ab `heiz_vorlauf_minuten` (Start: **30**) vor der ersten Buchung eines Blocks bis Ende der letzten; sonst `grund_temperatur` (Start: **0 °C**). **Hallenweit** – Heizzonen je Feld entfallen. Zwei Hinweise zu diesen Werten: Der Sollwert 0 °C muss in Home Assistant dem Frostschutzmodus des Geräts entsprechen, nicht „aus". Und ob eine Traglufthalle mit Sandfläche in 30 Minuten von 0 °C auf 18 °C kommt, ist eine Frage an den Heizungsplaner, nicht an die Software; der Betreiber hat den Vorschlag angenommen, mit 30 Minuten zu starten und nach den ersten Wochen nachzujustieren (23.09.2026). Das System protokolliert deshalb neben dem Sollwert auch die gemessene Temperatur, damit der Betreiber den Vorlauf nach den ersten Betriebswochen nachjustieren kann.
- **A-HALLE-2** Licht je Feld: an ab Slotbeginn minus `licht_vorlauf_minuten` (Start: 5) bis Slotende plus `licht_nachlauf_minuten` (Start: 5). Zwischen direkt aufeinanderfolgenden Buchungen bleibt es an.
- **A-HALLE-3** Tür: PIN einer Buchung öffnet von Slotbeginn minus `zutritt_vorlauf_minuten` (Start: 15) bis Slotende. Ein Betreiber-Master-PIN (nur lokal konfiguriert, nicht im Hauptsystem) öffnet immer. **Nach fünf Fehleingaben wird der Betreiber benachrichtigt, das Tastenfeld bleibt aber aktiv – keine Sperrung.** Niemand soll vor verschlossener Tür stehen, weil jemand vor ihm falsch getippt hat. Damit ein sechsstelliger Code ohne jede Bremse nicht einfach durchprobiert werden kann, verzögert das Tastenfeld ab dem fünften Fehlversuch die Annahme um wenige Sekunden; das ist keine Sperre und für den Nutzer kaum spürbar (Betreiber einverstanden, 23.09.2026). Ereignistyp: `tastenfeld_fehlversuche`.
- **A-HALLE-4** Präsenz: Sensoren je Feld melden Belegung. Präsenz ohne Buchung > 10 Minuten erzeugt ein Alarm-Ereignis. Präsenz während einer Buchung bestätigt „durchgeführt“.
- **A-HALLE-5** Anwesenheit einer Buchung gilt als bestätigt, wenn ihr PIN im Zeitfenster akzeptiert wurde *oder* Präsenz auf ihrem Feld während des Slots erkannt wurde. Sonst `nicht_erschienen`. Die Ableitung macht das Hauptsystem aus den Ereignissen.
- **A-HALLE-6** Autonomie: Der Hallendienst arbeitet ausschließlich aus seiner lokalen Datenbank und funktioniert ohne Verbindung mindestens 72 h; durch den 7-Tage-Plan faktisch bis zum Ende des Plans. Ereignisse werden lokal gepuffert und nachgeliefert.
- **A-HALLE-7** Handbetrieb: Schalter im Hallendienst und als HA-Entität; im Handbetrieb steuert der Dienst nichts, meldet aber weiter Ereignisse. Rückkehr zum Automatikbetrieb stellt den Sollzustand sofort her.
- **A-HALLE-8** Der Hallendienst meldet seinen Zustand (Planversion, letzter Abruf, HA erreichbar) an das Hauptsystem; > 60 Minuten ohne Kontakt erzeugt einen Betreiber-Alarm.

### 3.11 Gruppenverwaltung (nur Portal)

Übernahme des Musters aus dem SportAbo-Manager. **Existiert ausschließlich im Portal. Das Hauptsystem hat dafür keine Tabelle, keinen Anfragetyp, keinen Lesestand.**

- **A-GRUP-1** Jedes Portalkonto kann Gruppen besitzen. Eine Gruppe umfasst Buchungen des Kontoinhabers (typisch eine Dauerbuchung, aber auch beliebige Einzelbuchungen).
- **A-GRUP-2** Mitspieler werden per E-Mail eingeladen; die Einladung wird beim ersten Login angenommen. Rollen: `mitspieler`, `mitverwalter` (entspricht Super-Mitglied: Zusagen anderer sehen, abrechnen, Fristen-Ausnahmen genehmigen). Ein Konto kann in beliebig vielen Gruppen Mitspieler und gleichzeitig selbst Kunde sein.
- **A-GRUP-3** Teilnahme je Termin und Mitspieler: `zugesagt`, `abgesagt`, `warteliste`, optional mit Anzahl Begleitpersonen. Maximale und minimale Teilnehmerzahl je Gruppe. Warteliste rückt automatisch nach.
- **A-GRUP-4** Gäste ohne Konto tragen sich über einen geheimen Termin-Link ein (Name, E-Mail, Anzahl).
- **A-GRUP-5** Fristen je Gruppe: freies Abmelden bis X h vorher, danach nur mit Genehmigung eines Mitverwalters, danach gar nicht.
- **A-GRUP-6** Kostenteilung: Budget eines Termins = Preis der Buchung aus dem Lesestand (oder ein vom Kontoinhaber gesetzter Gruppenpreis). Nach dem Termin wird das Budget auf die Anwesenden verteilt; jeder Mitspieler hat ein internes Guthabenkonto; der Kontoinhaber bucht Einzahlungen ein. Gäste erhalten eine Zahlungsaufforderung per E-Mail, deren Eingang abgehakt wird.
- **A-GRUP-7** Die Gruppenverwaltung liest den Lesestand und schreibt niemals in die Anfragetabelle. Ein Storno beim Betreiber bleibt eine bewusste Aktion des Kontoinhabers; bei Unterschreiten der Mindestteilnehmerzahl zeigt das Portal nur einen Hinweis.
- **A-GRUP-8** Das Portal versendet für die Gruppenverwaltung eigene E-Mails: Einladung, Erinnerung vor dem Termin, Anteil nach Abrechnung, Zahlungsaufforderung an Gäste.
- **A-GRUP-9** Die Gruppenkasse ist reine Buchführung unter den Mitspielern. Sie hat keinen Bezug zu Rechnungen, Zahlungen oder Guthaben des Hauptsystems.

### 3.12 Benachrichtigungen

- **A-MAIL-1** Portal versendet nur: Login-Codes, Gruppen-Mails (A-GRUP-8).
- **A-MAIL-2** Hauptsystem versendet: Buchungsbestätigung mit PIN, Ablehnung, Zahlungsfrist abgelaufen, Stornobestätigung (mit Angabe, ob kostenfrei, und bei Abos den verbleibenden freien Absagen), Rechnung, Saisonrechnung, Gutscheincode mit Kaufrechnung bzw. Kaufbeleg (A-GUT-5), Gutschein nach Storno wieder gültig, Mitgliedschaft freigeschaltet bzw. beendet, Erinnerung vor Ablauf der Mitgliedschaft, Erinnerung 24 h vor Termin mit PIN, Kontolöschung. Betreiber-Alarme (3.10) ebenfalls per E-Mail.
- **A-MAIL-3** Alle Mails sind Templates mit Betreiberdaten aus der Konfiguration.

### 3.13 Admin-UI (Hauptsystem)

- **A-ADM-1** Belegungsplan (Kalender je Feld, Woche/Tag) mit Anlegen/Ändern/Stornieren von Buchungen, Dauerbuchungen, Sperren per Klick.
- **A-ADM-2** Stammdaten: Felder, Betriebszeiten, Kundengruppen, Tarife, Konfigurationswerte (alle in dieser Spezifikation als konfigurierbar bezeichneten Größen).
- **A-ADM-3** Kunden: Liste, Suche, Rechnungskunde markieren (A-KUND-7), Mitgliedsstatus freischalten/verlängern/beenden, Guthaben buchen, Auszahlung abhaken, Historie. Dazu die Prüfliste des Jahresabgleichs (A-KUND-5) mit Sammelaktionen und CSV sowie die Guthabenliste zum Saisonende (A-ZAHL-4).
- **A-ADM-4** Rechnungen: Liste, Filter, PDF, auf bezahlt setzen, Storno- und Teil-Stornorechnung, CSV-Export. Kein Monatslauf mehr (A-RECH-3).
- **A-ADM-8** Gutscheine: Liste mit Status, Käufer, Inhaber und (Modell `wert`) Restwert, Freischaltcodes ausstellen (einzeln oder als Serie mit CSV-Export) mit Einschränkungen und Gültigkeit, Code sperren, verfallenen Gutschein reaktivieren (A-GUT-4), Rückgabe (A-GUT-9), Einlösungen je Gutschein einsehen.
- **A-ADM-5** Halle: Live-Status (Planversion, Kontakt, HA-Status, Licht/Heizung/Tür je Feld), Ereignisliste, Alarme, Handbetrieb (nur Anzeige; geschaltet wird in HA, weil das Hauptsystem die Halle nicht erreicht).
- **A-ADM-6** Liste der kostenpflichtigen Stornos (Arbeitsliste für Kulanzentscheidungen), Klärungsliste (unzuordenbare Zahlungen, Präsenz ohne Buchung), Audit-Log.
- **A-ADM-7** Login mit Benutzername/Passwort und TOTP-2FA; mehrere Admin-Benutzer mit Rollen `admin` und `lesend`.
- **A-ADM-9** Einstellungen: alle Werte aus 3.14 auf einer Seite, gruppiert nach Bereich, mit Vorgabewert, Erklärung und Auditeintrag bei jeder Änderung.

### 3.14 Einstellungen statt fester Regeln

Wo der Betreiber zwischen Varianten entschieden hat, baut das System beide und macht seine Antwort zum Vorgabewert. Das hält Entscheidungen umkehrbar, ohne Entwickler, und macht die Antworten des Steuerberaters (Ⓞ-8) zu einer Einstellung statt zu einem Umbau. Alle Werte liegen in der Tabelle `konfiguration` und sind im Admin-UI änderbar (A-ADM-9). Werte, die bereits entstandene Belege oder Buchungen betreffen, wirken nur auf danach entstehende; was galt, steht an der Buchung, am Gutschein oder an der Rechnung.

| Einstellung | Vorgabe | Alternativen | Anforderung |
|---|---|---|---|
| `zahlung_anbieter` | `stripe` | jedes installierte Plugin (`mollie`, `fake` …) | A-ZAHL-5 |
| `rechnungskunden_online_buchen` | nein | ja | A-KUND-7 |
| `abo_nur_mitglieder` | ja | nein | A-DAUER-5 |
| `abo_freie_absagen` | 3 | 0 (Saison fest bezahlt) bis beliebig | A-DAUER-3 |
| `guthaben_auf_saisonrechnung` | ja | nein (nur manuelle Verrechnung) | A-ZAHL-4 |
| `saison_zahlungsziel_tage` | 14 | beliebig | A-RECH-3 |
| `mitgliedschaft_ablauf` | 30.04. | beliebiger Tag im Jahr | A-KUND-4 |
| `mitglieder_abgleich` | 31.08. | beliebiger Tag im Jahr | A-KUND-5 |
| `gutschein_modell` | `einheit` | `wert` | A-GUT-1 |
| `gutschein_minuten` | 120 | beliebig | A-GUT-1 |
| `gutscheine_je_buchung` | 1 | beliebig | A-GUT-1 |
| `gutschein_preis_mitglied`, `gutschein_preis_nichtmitglied` | offen (Ⓞ-18) | – | A-GUT-1a |
| `mitgliedsgutschein_nur_mitglieder` | ja | nein | A-GUT-1b |
| `gutschein_steuer` | `beim_kauf` | `bei_einloesung` | A-GUT-5 |
| `gutschein_gueltig_tage` | 730 | beliebig | A-GUT-4 |
| `freicode_gueltig_tage`, `max_einloesungen` je Code | 90, 1 | beliebig | A-GUT-2 |
| `gutschein_gastkauf` | nein | ja (spätere Stufe) | A-GUT-8 |

Dazu kommen die schon bisher konfigurierbaren Fristen, Vorläufe, Temperaturen und Fenster (N-5). Die Verzögerung am Tastenfeld (A-HALLE-3) ist eine lokale Einstellung des Hallendienstes (`verzoegerung_sekunden`, Vorgabe 3), weil sie auch ohne Verbindung zum Hauptsystem gelten muss.

## 4. Nichtfunktionale Anforderungen

- **N-1 Sicherheit:** Hauptsystem ohne öffentliche Erreichbarkeit; alle Verbindungen zum Portal und zur Halle werden vom Hauptsystem bzw. Hallendienst ausgehend aufgebaut. Portal-Endpunkte für das Hauptsystem nur mit Client-Zertifikat (mTLS). Alle Lesestände und Betriebspläne sind Ed25519-signiert.
- **N-2 Datenminimierung:** Portal ohne Postadressen, Zahlungsdaten, Rechnungen. Halle ohne Personendaten. Siehe Abschnitt 9.
- **N-3 Autonomie Halle:** ≥ 72 h ohne Verbindung, Ziel 7 Tage.
- **N-4 Verfügbarkeit Portal:** Bei Ausfall des Hauptsystems bleibt das Portal lesbar (letzter Lesestand, Gruppenverwaltung voll nutzbar); Anfragen werden gesammelt und nach Rückkehr verarbeitet.
- **N-5 Konfigurierbarkeit:** Alle Fristen, Vorläufe, Temperaturen, Fenster und Preise sind Daten, kein Code. Entscheidungen zwischen fachlichen Varianten ebenso (3.14); Anbindungen an Fremddienste (Zahlung) sind Plugins.
- **N-6 Nachvollziehbarkeit:** Audit-Log für alle Änderungen an Buchungen, Tarifen, Rechnungen, Guthaben, Konfiguration (wer, wann, was, vorher/nachher).
- **N-7 Geldbeträge** durchgehend `Decimal`, nie Fließkomma. Zeitstempel in UTC gespeichert, Anzeige in `Europe/Berlin`.
- **N-8 Sprache:** Oberflächen auf Deutsch, mobil zuerst; Admin-UI für Desktop.
- **N-9 Skala:** 3–6 Felder, wenige hundert Kunden, wenige tausend Buchungen je Saison. Keine horizontale Skalierung nötig.
- **N-10 Backup:** Hauptsystem täglich verschlüsselt an zweiten Ort; Wiederherstellung dokumentiert und vor Saisonstart getestet. Portal täglich (Gruppendaten sind dort Master).

## 5. Datenmodell Hauptsystem (`core/`)

PostgreSQL. Alle Tabellen mit `id` (UUID), `created_at`, `updated_at`.

| Tabelle | Wesentliche Felder |
|---|---|
| `feld` | name, aktiv, reihenfolge (keine Gerätezuordnung, siehe A-FELD-4) |
| `feld_raster` | feld_id, wochentag (0–6 oder null = alle), modus (`dauer`/`fenster`), slot_minuten, fenster_json (Liste von [start, ende]) |
| `betriebszeit` | wochentag, oeffnet, schliesst, gueltig_von, gueltig_bis |
| `ausnahmetag` | datum, geschlossen (bool), oeffnet, schliesst, grund |
| `kundengruppe` | name, ust_satz (Decimal), ist_mitglied (bool) – genau zwei Zeilen, siehe A-KUND-2 |
| `kunde` | name, email (unique, lower), adresse_*, guthaben (Decimal), **rechnungskunde** (bool, ersetzt `zahlungsart`), portal_konto_id, provider_kunde_id, anonymisiert_am, **mitglied_bis?**, mitglied_antrag_am?, mitglied_antrag_hinweis, mitglied_freigeschaltet_am?, mitglied_freigeschaltet_von?, mitglied_beendet_am?, mitglied_beendet_grund? |
| `tarif` | name, preis, feld_id?, wochentag?, uhrzeit_von?, uhrzeit_bis?, kundengruppe_id?, gueltig_von?, gueltig_bis?, aktiv |
| `dauerbuchung` | kunde_id, feld_id, wochentag, start, ende, gueltig_von, gueltig_bis, pin_hash, pin_klar (verschlüsselt), beendet_am |
| `buchung` | feld_id, kunde_id, beginn, ende (tstzrange-Exklusion je feld_id für Status ≠ storniert/verfallen/abgelehnt), status, preis, **ust_satz** (festgeschrieben, A-TARIF-3), **kundengruppe_id** (zum Leistungsdatum, A-KUND-6), zahlungsart (`online`/`saison`/`manuell`/`gutschein`), pin_hash, pin_klar (verschlüsselt), dauerbuchung_id?, anfrage_id (Portal-Anfrage, unique), reserviert_bis?, rechnung_position_id?, anwesenheit (`unbekannt`/`bestaetigt`/`nicht_erschienen`) |
| `sperre` | feld_id?, beginn, ende, grund (tstzrange-Exklusion gegen buchung) |
| `storno` | buchung_id, zeitpunkt, durch (`kunde`/`betreiber`/`system`), kostenfrei, **freie_absage** (bool, A-DAUER-3), grund |
| `zahlung` | kunde_id, buchung_id?, rechnung_id? (Guthabenverrechnung auf eine Saisonrechnung), provider (Name des Zahlungs-Plugins, `guthaben` oder `gutschein`), provider_ref (unique), betrag, status, empfangen_am, rohdaten_json. Beim Gutscheinkauf bleibt `buchung_id` leer; bei der Einlösung entsteht eine Zeile mit `provider = gutschein`, so dass Guthaben + Gutschein + Restzahlung einer Buchung stets ihren Preis ergeben |
| `guthaben_buchung` | kunde_id, betrag (±), art (`storno_gutschrift`/`verrechnung`/`auszahlung`/`manuell`/`ueberzahlung`/`rueckbuchung`), bezug_id?, notiz, admin_user_id? |
| `rechnung` | nummer (unique, lückenlos), **art** (`einzel`/`saison`/`storno`/`gutschein`/`gutschein_beleg`; letzterer nur bei `bei_einloesung`, eigener Nummernkreis), kunde_id, datum, leistung_von, leistung_bis, netto, ust, brutto, status, pdf_pfad, pdf_sha256, storniert_durch_id?, **dauerbuchung_id?** (bei `saison`), **korrigiert_rechnung_id?** (bei `storno`, auch für Teil-Stornos) |
| `rechnung_position` | rechnung_id, buchung_id?, text, menge, einzelpreis_brutto, ust_satz (Summen je Satz, A-RECH-8) |
| `gutschein` | code (unique, ≥ 60 Bit Entropie), art (`kauf`/`frei`), **modell** (`einheit`/`wert`, beim Anlegen festgeschrieben), **steuer** (`beim_kauf`/`bei_einloesung`, festgeschrieben), minuten (Modell `einheit`), nennwert_brutto?, restwert? (Modell `wert`), kaufpreis_brutto?, kundengruppe_id? und ust_satz? (des Käufers), nur_mitglieder (bool), feld_id?, zeitraum_von?, zeitraum_bis?, max_einloesungen, kaeufer_kunde_id?, inhaber_kunde_id?, zahlung_id?, rechnung_id? (Kaufrechnung bzw. Kaufbeleg), ausgestellt_von_admin_id?, ausgestellt_am, gueltig_bis, status (`aktiv`/`teilweise_eingeloest`/`eingeloest`/`verfallen`/`gesperrt`), empfaenger_email?, notiz |
| `gutschein_einloesung` | gutschein_id, buchung_id, kunde_id, minuten_gedeckt?, betrag (gedeckter Tarifwert bzw. verbrauchter Wert), zeitpunkt, rueckgaengig_am?, rueckgaengig_grund? |
| `ereignis` | quelle (`halle`/`portal`/`admin`/`system`), typ, zeitpunkt, feld_id?, buchung_id?, daten_json, halle_seq (Idempotenz) |
| `anfrage_verarbeitet` | anfrage_id (unique), ergebnis_json, verarbeitet_am |
| `lesestand_version` | dokument (`belegung`/`tarife`/`konto:<id>`), version, signiert_am |
| `konfiguration` | schluessel, wert, typ |
| `admin_user` | name, passwort_hash, totp_secret, rolle |
| `audit` | zeitpunkt, admin_user_id?, quelle, objekt_typ, objekt_id, vorher_json, nachher_json |

PIN-Klartext ist nötig für Bestätigungs- und Erinnerungsmails; er wird mit einem Schlüssel aus der Konfiguration verschlüsselt gespeichert. An die Halle geht nur der Hash (Argon2id mit hallenweitem Salt, damit die Halle lokal prüfen kann).

## 6. Datenmodell Portal (`portal/`)

PostgreSQL. Zwei klar getrennte Bereiche (eigene Schemas `spiegel` und `gruppen`).

**Schema `spiegel`** (Konten, Anfragen, Lesestand)

| Tabelle | Wesentliche Felder |
|---|---|
| `konto` | email (unique, lower), anzeigename, kundengruppe (aus Lesestand), aktiv, erstellt_am |
| `login_token` | konto_id, token_hash, code, laeuft_ab, verwendet_am |
| `session` | konto_id, token_hash, laeuft_ab |
| `anfrage` | id (UUID, vom Portal), typ, konto_id?, nutzlast_json, erstellt_am, status (`offen`/`abgeholt`/`beantwortet`), abgeholt_am, antwort_json, beantwortet_am |
| `lesestand` | dokument, version, signatur, inhalt_json, empfangen_am |
| `rechnung_link` | konto_id, rechnung_nr, token_hash, pdf_tmp_pfad, laeuft_ab |
| `webhook_eingang` | provider, rohdaten, signatur_header, empfangen_am, anfrage_id |

**Schema `gruppen`** (nur Portal, kein Bezug zum Hauptsystem)

| Tabelle | Wesentliche Felder |
|---|---|
| `gruppe` | inhaber_konto_id, name, max_teilnehmer, min_teilnehmer, frist_frei_stunden, frist_anfrage_stunden, gruppenpreis?, beschreibung |
| `gruppe_buchung` | gruppe_id, buchung_ref (Buchungs-ID aus dem Lesestand), datum, beginn, ende, feld_name, budget, abgerechnet_am, abgesagt |
| `mitgliedschaft` | gruppe_id, konto_id, rolle, guthaben (Decimal), eingeladen_am, angenommen_am, aktiv |
| `einladung` | gruppe_id, email, token_hash, laeuft_ab |
| `teilnahme` | gruppe_buchung_id, mitgliedschaft_id, status, begleitpersonen, abmeldung_angefragt_am |
| `warteliste` | gruppe_buchung_id, mitgliedschaft_id, erstellt_am |
| `gast_teilnahme` | gruppe_buchung_id, name, email, anzahl, token_hash, bezahlt_am, betrag |
| `kassen_buchung` | mitgliedschaft_id, betrag (±), art (`anteil`/`einzahlung`/`korrektur`), gruppe_buchung_id?, notiz |

`gruppe_buchung` entsteht, wenn der Inhaber eine seiner Buchungen aus dem Lesestand in eine Gruppe aufnimmt (für Dauerbuchungen: alle Termine mit einem Klick, künftige Termine automatisch). Verschwindet die Buchung aus dem Lesestand (storniert), wird `abgesagt` gesetzt und Mitspieler werden informiert.

## 7. Datenmodell Hallendienst (`hall/`)

SQLite, eine Datei, WAL-Modus.

| Tabelle | Wesentliche Felder |
|---|---|
| `plan_meta` | version, signiert_am, empfangen_am, gueltig_bis |
| `plan_buchung` | buchung_id, feld_id, beginn, ende, pin_hash, art (`buchung`/`sperre`) |
| `plan_feld` | feld_id, name, ha_licht_entity, ha_praesenz_entity, heizzone |
| `plan_konfig` | schluessel, wert (Vorläufe, Temperaturen) |
| `ereignis_queue` | seq (autoincrement), typ, zeitpunkt, feld_id?, buchung_id?, daten_json, gesendet_am? |
| `zustand` | feld_id?, licht_soll, licht_ist, heizung_soll, tuer_freigabe_bis, handbetrieb |

Der Plan wird bei jedem Abruf als Ganzes ersetzt (Version steigt monoton). Ein Abruf mit ungültiger Signatur oder älterer Version wird verworfen und als Ereignis gemeldet.

## 8. Integration

### 8.1 Portal ↔ Hauptsystem

**Kanal.** *(geändert 2026-09-23: Long-Polling statt WebSocket, Details in `2026-09-23-portal-kern-design.md` § 2)* Das Hauptsystem ruft per HTTPS mit Client-Zertifikat (mTLS, eigene interne CA; der Portal-Reverse-Proxy erzwingt das Zertifikat nur auf `/core/*`) und zusätzlichem Bearer-Token `GET /core/anfragen?warten=25` auf. Das Portal hält die Anfrage offen, bis eine Anfrage eintrifft oder 25 s vergangen sind, und antwortet dann sofort. Antworten gehen per `POST /core/antworten`, Lesestände per `POST /core/lesestand` zurück. Abgeholte, aber nach 60 s noch unbeantwortete Anfragen liefert das Portal erneut aus. Damit gibt es einen einzigen Transportweg: Rückfall und Normalbetrieb sind derselbe Code.

**Anfragetypen** (Portal → Hauptsystem):

| Typ | Nutzlast | Antwort |
|---|---|---|
| `konto_angelegt` | email, anzeigename | kunde_id |
| `konto_geaendert` | anzeigename | ok |
| `konto_loeschen` | – | ok (Anonymisierung terminiert) |
| `buchung_anfragen` | feld_id, beginn, ende, **gutschein_code?** | `reserviert` (buchung_id, preis, guthaben_verrechnet, gutschein_verrechnet, checkout_url?, reserviert_bis) oder `bestaetigt` (voll aus Guthaben und/oder Gutschein) oder `abgelehnt` (grund: belegt, außerhalb_fenster, außerhalb_betriebszeit, kein_tarif, konto_gesperrt, rechnungskunde, gutschein_ungueltig, gutschein_nur_mitglieder) |
| `buchung_stornieren` | buchung_id | ok (kostenfrei ja/nein, freie_absage ja/nein, verbleibende_freie_absagen?, gutschein_wieder_gueltig?) oder abgelehnt |
| `mitgliedschaft_beantragen` | hinweis_text | ok (Antrag vermerkt, Betreiber entscheidet) |
| `gutschein_kaufen` | empfaenger_email?, betrag? (nur Modell `wert`) | `reserviert` (checkout_url, preis) oder abgelehnt (rechnungskunde, kein_gutscheinpreis) |
| `zahlung_eingegangen` | provider, rohdaten, signatur_header? | ok / ignoriert |
| `rechnung_anfordern` | rechnung_nr | pdf_base64 oder abgelehnt |

Der frühere Typ `dauerbuchung_anfragen` entfällt: Abos werden per E-Mail angefragt (A-DAUER-2).

Das Hauptsystem behandelt jede Nutzlast als nicht vertrauenswürdig: Konto muss existieren, Buchung muss dem Konto gehören, Zeiten werden gegen Raster geprüft, der Zahlungseingang wird vom Hauptsystem selbst festgestellt (A-ZAHL-2), und ein Gutscheincode wird innerhalb der Buchungstransaktion gesperrt und geprüft (A-GUT-3).

**Idempotenz.** `anfrage_verarbeitet.anfrage_id` ist unique. Eine bereits verarbeitete Anfrage wird mit der gespeicherten Antwort beantwortet, ohne erneute Verarbeitung.

**Lesestand** (Hauptsystem → Portal), jedes Dokument als `{dokument, version, erzeugt_am, inhalt}` mit Ed25519-Signatur über die kanonische JSON-Serialisierung. Das Portal prüft die Signatur mit dem hinterlegten öffentlichen Schlüssel und übernimmt nur höhere Versionen.

| Dokument | Inhalt | Wann |
|---|---|---|
| `belegung` | je Feld: Liste belegter Zeiträume im Buchungsfenster (nur beginn/ende, ohne Kunde), Betriebszeiten, Raster, Fenster-Parameter | nach jeder Änderung (debounced 2 s), nächtlich voll |
| `tarife` | Preisregeln je Kundengruppe (nur die für Kunden sichtbaren) | bei Änderung, nächtlich |
| `konto:<id>` | Kundengruppe, **Mitgliedsstatus (`mitglied_bis`, Antrag offen ja/nein)**, **rechnungskunde**, Guthaben, **eigene Gutscheine (Code, gültig bis, nur_mitglieder, Restwert bei Modell `wert`)**, Gutscheinpreis der eigenen Gruppe, **je Abo die verbleibenden freien Absagen**, eigene Buchungen (id, feld, beginn, ende, status, preis, pin_klar, storno-Info), Rechnungsliste (nummer, datum, brutto, status), offene Reservierungen mit checkout_url | nach jeder Antwort auf eine Anfrage des Kontos, bei Statusänderungen (Anwesenheit, Rechnung, Freischaltung), nächtlich |

**Fehlerfälle.** Hauptsystem nicht erreichbar → Portal zeigt „Anfrage gesendet, Bestätigung folgt per E-Mail“; nach `antwort_hinweis_sekunden` (Start: 120) ein Hinweis. Reservierungen mit Zahlungsfrist werden vom Hauptsystem beim Verfall bereinigt, auch wenn der Kunde nie zurückkam. Signaturfehler oder Versionsrückschritt im Lesestand → Dokument verwerfen, Alarm an Betreiber.

### 8.2 Hauptsystem ↔ Hallendienst

**Kanal.** WireGuard-Tunnel, initiiert vom Hallendienst (Keepalive), Hauptsystem lauscht nur auf der WireGuard-Schnittstelle. Darüber HTTPS mit mTLS.

| Aufruf (vom Hallendienst) | Inhalt |
|---|---|
| `GET /hall/plan?ab=<version>` | Betriebsplan 7 Tage: Buchungen (buchung_id, feld_id, beginn, ende, pin_hash), Sperren, Felder, Konfiguration; signiert; `304` wenn unverändert. Alle 5 Minuten. |
| `POST /hall/ereignisse` | Liste `{seq, typ, zeitpunkt, feld_id?, buchung_id?, daten}`; Antwort: höchste bestätigte `seq`. Sofort bei neuem Ereignis, sonst alle 60 s. |
| `POST /hall/status` | Planversion, letzter Abruf, HA erreichbar, Zustand je Feld, Handbetrieb. Alle 60 s, huckepack mit Ereignissen. |

Das Hauptsystem erzeugt den Plan neu, sobald sich eine Buchung im 7-Tage-Fenster ändert (Version steigt). Der Hallendienst holt zusätzlich sofort, wenn das Hauptsystem über den Status-Aufruf `plan_neu = true` zurückmeldet.

**Ereignistypen:** `pin_akzeptiert`, `pin_abgelehnt`, `tastenfeld_fehlversuche`, `praesenz_start`, `praesenz_ende`, `praesenz_ohne_buchung`, `tuer_offen_ausserhalb`, `licht_geschaltet`, `heizung_gesetzt`, `ha_nicht_erreichbar`, `aktor_fehler`, `plan_verworfen`, `handbetrieb_an/aus`, `dienst_gestartet`.

**Home Assistant.** Home Assistant wird für die neue Halle eingeplant (noch nicht vorhanden). Der Hallendienst nutzt die HA-REST-API (Dienste aufrufen) und den HA-WebSocket (Zustandsänderungen abonnieren) mit einem Long-Lived Access Token. Keine Custom Component: Seinen Zustand schreibt der Dienst als `sensor.beachhub_*` per REST nach HA, den Handbetrieb schaltet ein Helfer `input_boolean.beachhub_handbetrieb`, das Tastenfeld liefert ein konfigurierbares HA-Ereignis mit dem eingegebenen Code (Details in `2026-09-23-hallendienst-design.md`). Sperren schalten weder Licht noch Heizung (Ⓞ-16). Erwartete HA-Entitäten je Konfiguration: `light.*` je Feld, `climate.*` je Heizzone, `lock.*`/`switch.*` für den Türöffner, `binary_sensor.*` für Präsenz je Feld und Türkontakt, ein Eingabekanal für das Tastenfeld (z. B. `event.*` oder MQTT-Topic). Der Sollzustand wird alle 30 s aus dem Plan neu berechnet und mit dem Ist abgeglichen (idempotente Steuerung). Präsenzalarm-Erkennung in HA selbst nur als Rückfall, falls der Dienst ausfällt (einfache HA-Automation: Licht aus außerhalb Betriebszeit).

**Sicherheit Halle.** Kein Kundenname, keine E-Mail. PIN nur als Argon2id-Hash. Master-PIN lokal in der Konfiguration. Bei Diebstahl des Hallenrechners sind ausschließlich Buchungszeiten und Hashes betroffen.

## 9. Abläufe

**Einzelbuchung online.** Kunde wählt Slot im Portal (nur innerhalb des Fensters sichtbar) → `buchung_anfragen` → Hauptsystem: Fenster, Betriebszeit, Raster, Kollision (Exklusion in Transaktion), Tarif, Guthaben → Antwort `reserviert` mit `checkout_url` und `reserviert_bis` → Portal leitet zum Zahlungsdienst → dessen Rückmeldung trifft im Portal ein → `zahlung_eingegangen` → Hauptsystem stellt den Eingang selbst fest (A-ZAHL-2), setzt `bestätigt`, vergibt PIN, erzeugt Rechnung, mailt Bestätigung → Lesestand `konto:<id>` aktualisiert → Portal zeigt Buchung mit PIN. Zahlungsfrist verfallen: `verfallen`, Slot frei, Mail.

**Einzelbuchung mit Gutschein** (Vorgaben). Kunde gibt beim Buchen seinen Code ein → `buchung_anfragen` mit `gutschein_code` → Hauptsystem prüft in derselben Transaktion Slot und Gutschein (gültig, nicht verbraucht, bei Mitgliedsgutschein Einlösender Mitglied zum Leistungsdatum), sperrt die Gutscheinzeile → der Gutschein deckt die ersten 120 Minuten → Buchung bis 2 h: direkt `bestätigt`, Zahlungsart `gutschein`, Buchungsbestätigung ohne Steuer (A-GUT-5, `beim_kauf`) → längere Buchung: `reserviert` mit Bezahlsitzung über die übrigen Slots, Rechnung nur über diesen Aufpreis.

**Gutscheinkauf** (Vorgaben). Kunde (kein Rechnungskunde) wählt im Portal „Gutschein über zwei Stunden“ zum Preis seiner Gruppe → `gutschein_kaufen` → Bezahlsitzung → Zahlungseingang → Hauptsystem legt den Gutschein an (730 Tage gültig, Gruppe und Satz des Käufers, bei Mitgliedern `nur_mitglieder`), erzeugt die **Kaufrechnung mit Umsatzsteuer** (A-GUT-5) und mailt Code und Rechnung an den Käufer, den Code auf Wunsch auch an einen genannten Empfänger.

**Abo (Dauerbuchung).** Kunde fragt **per E-Mail** an → Betreiber legt im Admin-UI an (Kunde wird Rechnungskunde; Mitgliedschaft muss bis zum letzten Termin reichen, A-DAUER-5) → Kollisionsliste → Termine erzeugt (`bestätigt`, gemeinsame PIN, Zahlungsart `saison`) → **eine Saisonrechnung über alle Termine**, vorhandenes Guthaben verrechnet, Status `offen`, Zahlungsziel 14 Tage → Mail an Kunden mit Rechnung und PIN → Lesestand. Beendet der Betreiber das Abo vorzeitig, entsteht eine Teil-Stornorechnung (A-RECH-7).

**Abo-Absage.** Kunde sagt im Portal einen Abo-Termin ab (T-3 Tage, Frist 24 h, zwei von drei freien Absagen schon genutzt) → `buchung_stornieren` → Hauptsystem: dritte freie Absage, Slot frei, Teil-Stornorechnung über den Termin → Saisonrechnung bezahlt: Betrag wird Guthaben; noch offen: Forderung sinkt → Mail mit „keine freien Absagen mehr“. Die nächste Absage gibt nur noch den Slot frei. Das Guthaben verrechnet die nächste Saisonrechnung, oder der Betreiber zahlt es zum Saisonende auf Wunsch aus.

**Mitgliedschaft.** Kunde beantragt im Portal → Klärungsliste und Mail an den Betreiber → Betreiber prüft gegen die Vereinsverwaltung und setzt `mitglied_bis` (Vorgabe 30. April) → Mail an Kunden, Lesestand aktualisiert, Portal zeigt ab sofort Mitgliedspreise. Am 31. August erzeugt das System die Prüfliste für den Jahresabgleich.

**Storno nach Frist.** Kunde storniert (T-10 h, Frist 24 h) → Storno kostenpflichtig, der Betrag bleibt fällig → Slot im Lesestand sofort wieder frei → bucht ihn ein anderer Kunde, ist das ein davon unabhängiger Vorgang. Der Betreiber sieht das Storno in der Liste der kostenpflichtigen Stornos und kann es aus Kulanz freistellen; dann entstehen Stornorechnung und Guthaben zusammen (A-STORNO-6).

**Hallentag.** 17:55 Heizung auf Spieltemperatur (erste Buchung 19:00, Vorlauf 60 min, Plan lokal) → 18:45 PIN-Freigabe → 18:55 Licht Feld 2 an → 19:02 PIN eingegeben, Tür öffnet, Ereignis `pin_akzeptiert` → Präsenz Feld 2 erkannt → 21:05 Licht aus, keine Folgebuchung → Heizung Grundtemperatur → Ereignisse gehen gebündelt ans Hauptsystem, Buchung wird `durchgeführt`.

**Internetausfall Halle (3 Tage).** Plan vom letzten Abruf gilt (7 Tage). Alle Abläufe laufen weiter; Ereignisse sammeln sich (`ereignis_queue`). Betreiber erhält nach 60 min „Halle ohne Kontakt“. Neue Buchungen in dieser Zeit erreichen die Halle nicht: Das Hauptsystem vergleicht die von der Halle zuletzt gemeldete Planversion mit dem aktuellen Plan und markiert betroffene Buchungen im Admin-UI als „Halle nicht informiert“. Der Betreiber entscheidet, ob er dem Kunden anderweitig Zutritt verschafft (Master-PIN, persönlich) oder die Buchung kostenfrei storniert. Nach Rückkehr: Plan aktualisiert, Ereignisse nachgeliefert, Anwesenheiten abgeleitet.

**Ausfall Hauptsystem.** Portal zeigt letzten Lesestand, sammelt Anfragen, Gruppenverwaltung uneingeschränkt. Nach Rückkehr verarbeitet das Hauptsystem die Anfragen in Reihenfolge; Reservierungen, deren Zahlungsfrist inzwischen abgelaufen wäre, werden mit neuer Frist erneut angeboten (Kunde bekommt Mail).

## 10. Sicherheit und Datenschutz

- Verbindungen: Kunde→Portal TLS 1.2+; Hauptsystem→Portal WebSocket/HTTPS mit mTLS über interne CA; Halle→Hauptsystem WireGuard + mTLS. Zertifikate 1 Jahr, Rotation dokumentiert.
- Signaturen: Ed25519-Schlüsselpaar des Hauptsystems; öffentlicher Schlüssel in Portal und Halle hinterlegt. Signatur über kanonisches JSON (sortierte Schlüssel, keine Leerzeichen, UTF-8).
- Portal: Magic Link 15 min gültig, einmalig; Antwort auf Login-Versuch immer gleich; Rate-Limit je IP und je E-Mail; Sessions serverseitig, Cookie `HttpOnly; Secure; SameSite=Lax`; CSRF-Token als Router-Abhängigkeit; Content-Security-Policy ohne Inline-Skripte; Abhängigkeits-Scan im CI.
- Die Zugangsdaten des Zahlungsdienstes liegen ausschließlich im Hauptsystem. Das Portal reicht Rückmeldungen blind durch und kann sie weder lesen noch fälschen.
- Hauptsystem: Festplattenverschlüsselung, Firewall nur WireGuard-Port, SSH nur per Schlüssel über WireGuard, Admin-UI nur auf WireGuard-Interface, TOTP-2FA, Passwörter Argon2id.
- Datenminimierung je System wie in Abschnitt 2. Gruppenverwaltung: Mitspieler-E-Mails nur im Portal.
- Löschkonzept: `konto_loeschen` → Portal löscht Gruppendaten und Konto sofort; Hauptsystem setzt Kunden auf „Löschung angefordert“, anonymisiert Stammdaten sofort (Name → „Gelöschter Kunde“, E-Mail → Hash), Rechnungen bleiben 10 Jahre (§ 147 AO) mit den Rechnungsdaten, danach Löschung per Jobs.
- Aufbewahrung Ereignisse Halle: 90 Tage im Detail, danach nur Anwesenheitsstatus je Buchung.
- Datenschutzerklärung, AV-Vertrag mit Hoster und Zahlungsdienst, Verzeichnis der Verarbeitungstätigkeiten: Aufgabe des Betreibers, das System liefert die technischen Angaben.

## 11. Technik und Vorgehen

- **Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Jinja2 (serverseitig gerendert, kein Frontend-Build, eine `style.css` mit Design-Tokens, Hell/Dunkel, PWA-fähig – wie im SportAbo-Manager). PostgreSQL 16 für Portal und Hauptsystem, SQLite für die Halle. `httpx` für die Kanäle, `aiohttp` für den HA-WebSocket, `pynacl` für Ed25519, `argon2-cffi`, `stripe`, `weasyprint` für Rechnungs-PDFs, `APScheduler` für Jobs.
- **Monorepo:**
  ```
  beachhub/
    portal/      FastAPI-App Portal (spiegel + gruppen), Dockerfile
    core/        FastAPI-App Hauptsystem inkl. Admin-UI, Dockerfile
    hall/        Hallendienst, Dockerfile (arm64/amd64)
    shared/      Nachrichtenschemata (pydantic), Signatur, kanonisches JSON, Zeit-/Slot-Logik
    deploy/      docker-compose je Ziel, Caddy-Konfigurationen, WireGuard-Beispiele
    docs/        Spezifikation, Betreiberdokument, Betriebshandbuch
  ```
  Ein Python-Projekt je Verzeichnis mit eigenem `pyproject.toml`; `shared` als lokales Paket. Ein Workflow in GitHub Actions: Lint (ruff), Typen (mypy), Tests (pytest) je Teil, Vertragstests über `shared`.
- **Tests:** Fachlogik (Tarifauflösung, Fenster, Kollision, Storno, Rechnungsnummern, Kostenteilung) als reine Unit-Tests; Integrationstests je App gegen temporäre Datenbank; Vertragstests: Nachrichten aus `shared` werden von Sender und Empfänger validiert; Hallendienst gegen einen HA-Simulator (Fake-REST/WS) inkl. Offline-Szenario (Verbindung kappen, 72 h simulierte Uhr). Uhr überall injizierbar (`clock`).
- **Betrieb:** Portal hinter Caddy (automatisches TLS); Hauptsystem hinter Caddy auf WireGuard-IP; Halle als Container neben HA (HA OS: als Add-on-Container möglich, sonst Docker). Logs strukturiert (JSON), Alarme per E-Mail; einfache Health-Endpunkte.
- **Hosting des Portals.** Geprüft wurde die Vorgabe, das Portal auf einem Hetzner-Webhosting-Paket zu betreiben. Ergebnis: **Weder S noch M tragen dieses Portal.**

  | | S | M | L | XL |
  |---|---|---|---|---|
  | SSH | nein | nein | ja | ja |
  | Speicher / Datenbanken / Cronjobs | 10 GB / 1 / 1 | 50 GB / 5 / 5 | 100 GB / 20 / 10 | 300 GB / 50 / 20 |
  | Arbeitsspeicher, max. Laufzeit | 192 MB, 120 s | 256 MB, 180 s | 384 MB, 240 s | 512 MB, 360 s |
  | Von Hetzner als „für Webshop geeignet" eingestuft | nein | nein | bedingt | ja |

  Webhosting ist reines PHP-Hosting: kein Python, kein dauerhaft laufender Prozess, damit weder die WebSocket-Verbindung zum Hauptsystem noch ein gemeinsames Paket `shared` mit dem übrigen System. M unterscheidet sich von S nur in der Menge derselben Ressourcen, nicht in der Art – SSH gibt es erst ab L. Ein Buchungsportal mit Online-Zahlung ist zudem faktisch ein Webshop, wovon Hetzner bei S und M ausdrücklich abrät. Das Portal in PHP neu zu schreiben, hieße ein zweites Team-Wissen, eine zweite Codebasis und den Verlust der gemeinsamen Nachrichtenschemata.

  **Entscheidung:** Das Portal läuft als eigener Container (LXC) auf demselben Proxmox-Host wie das Hauptsystem. Damit die Trennung aus N-1 erhalten bleibt, gilt: zwei getrennte Container, das Portal in einer eigenen Netzzone hinter dem Reverse Proxy, das Hauptsystem ohne jede eingehende Route aus dem Internet und ohne Portweiterleitung – die Verbindung baut weiterhin ausschließlich das Hauptsystem auf. Ein gemeinsamer Host bedeutet allerdings, dass ein Einbruch in den Hypervisor beide Systeme trifft; die Trennung ist dadurch schwächer als bei getrennter Hardware. Das ist eine bewusst getragene Abwägung zugunsten der Betriebskosten (Ⓞ-1).
- **Ausbaustufen:**
  1. `core`: Datenmodell, Admin-UI (Felder, Betriebszeiten, Tarife, Kunden, Belegungsplan, Sperren, Dauerbuchungen), Rechnungen, Signatur/Lesestand-Erzeugung.
  1a. `core`: Umstellung nach Betreiber-Feedback – zwei Kundengruppen mit eigenem Steuersatz, Mitgliedsstatus mit Freischaltung und Jahresabgleich, Rechnungskunden, Saisonrechnung statt Monatslauf mit Guthabenverrechnung, freie Abo-Absagen, Teil-Stornorechnung mit Korrekturbeleg, Gutscheine und Freischaltcodes in beiden Modellen, Einstellungsseite (3.14).
  2. `portal`: Konten, Magic Link, Anfragetabelle, Kanal, Lesestand-Anzeige, Buchung/Storno, Zahlungs-Plugins (Stripe als erstes echtes Plugin), Gutscheinkauf. *Der Kern ohne 1a-Anteile (ohne Mitgliedschaft, Gutscheine, echten Zahlungsanbieter) wird vorgezogen: `2026-09-23-portal-kern-design.md`.*
  3. `hall`: Plan-Abruf, Licht/Heizung/Tür über HA, PIN-Prüfung, Ereignisse, Offline-Betrieb, Handbetrieb. *Design: `2026-09-23-hallendienst-design.md`.*
  4. `portal`: Gruppenverwaltung (Muster SportAbo-Manager).
  5. Präsenz-Ableitung, Alarme, Klärungslisten, CSV-Export, Feinschliff.

## 12. Entscheidungen des Betreibers

### 12.1 Beantwortet (Stand 10. September 2026)

| Nr | Frage | Antwort |
|---|---|---|
| O-1 | Buchungsfenster 14 Tage, Mindestvorlauf 60 min | bestätigt |
| O-2 | Stornofrist 24 h | bestätigt |
| O-3 | Zahlungsfrist 15 min | bestätigt |
| O-4 | Kundengruppen | **DJK-Mitglied und Nicht-Mitglied** (statt Privat/Verein/Mitglied) |
| O-5 | Umsatzsteuersatz | **7 % für Mitglieder, 19 % für Nicht-Mitglieder** |
| O-6 | Monatsrechnung | **wird nicht angeboten**; Abos über Saisonrechnung, Einzelbuchungen über den Zahlungsdienst |
| O-7 | Dauerbuchungen im Portal anfragen | nein, **per E-Mail** |
| O-8 | Heizung hallenweit oder je Zone | **hallenweit** |
| O-9 | Heizvorlauf, Spiel-, Grundtemperatur | **30 min, 18 °C, 0 °C** |
| O-10 | Tastenfeld und Türöffner mit HA-Anbindung | bestätigt; Schnittstellen mit dem Hallenhersteller (Hupfauer) klären |
| O-11 | Präsenzsensoren je Feld | bestätigt |
| O-12 | Zahlungsdienst | offen, „TBC ob Stripe"; PayPal, Wero und Sofortüberweisung genügen |
| O-13 | Türcode nach fünf Fehleingaben | **Meldung an den Betreiber, keine Sperrung** |
| O-14 | Betreiber-Alarme | Stufe 1 nur E-Mail |
| O-15 | Datenschutzerklärung, AV-Verträge, Verarbeitungsverzeichnis | Betreiber, vor Stufe 2 |
| O-16 | Serverstandort Deutschland | bestätigt |
| O-17 | Nachbuchung nach verspätetem Storno | **entfällt ersatzlos** – ein nach der Frist storniertes Entgelt bleibt fällig |

Neu hinzugekommen: Gutscheincodes (Kauf und Einlösung, Abschnitt 3.9a) und ein Freischaltprozess für die Vereinsmitgliedschaft (A-KUND-4 bis A-KUND-6).

### 12.1a Beantwortet (Fragenkatalog 2, Stand 23. September 2026)

Quelle: `docs/betreiber/Fragenkatalog 2_BeachAugsburg Buchungssystem.docx`. Nummern in Klammern: Frage im Katalog.

| Nr | Frage | Antwort | Umsetzung |
|---|---|---|---|
| Ⓞ-5 (1, 2) | Gutschein: Eurobetrag oder eine Stunde? Restbetrag? | **eine Buchungseinheit von zwei Stunden, losgelöst vom dann gültigen Preis**; damit kein Restbetrag | `gutschein_modell = einheit` (A-GUT-1) |
| Ⓞ-6 (3) | Gutscheinkauf ohne Konto? | nein; es gelten die Preise des Käufers (Mitglied/Nicht-Mitglied) | A-GUT-8, A-GUT-1a |
| – (4) | Freischaltcodes einmal oder mehrfach? | **jeder Code nur einmal** | `max_einloesungen = 1` (A-GUT-2) |
| – (5) | Was schaltet ein Code frei? | ein Buchungsfenster: ein Feld für zwei Stunden | A-GUT-2 |
| – (6) | Gültigkeit gekaufter Gutscheine | **730 Tage** | `gutschein_gueltig_tage` (A-GUT-4), rechtlicher Hinweis dort |
| Ⓞ-3 (7) | Abo-Absagen mit Geld zurück? | Festpreis, vorab bezahlt; **bis zu drei Termine kostenlos stornierbar**, Betrag als Guthaben für künftige Abos oder Auszahlung zum Saisonende | `abo_freie_absagen = 3` (A-DAUER-3, A-ZAHL-4) |
| Ⓞ-12 (8, 9) | Saisonrechnung: wann, Zahlungsziel, Nichtzahlung | nach der Buchung, **14 Tage**; Termine bleiben gültig, Mahnung außerhalb | A-RECH-3 |
| Ⓞ-2 (10) | „Rechnungskunden ausschließen“ | **Rechnungskunden buchen nicht online**; Abos und Events legt der Admin an | `rechnungskunden_online_buchen = nein` (A-KUND-7) |
| Ⓞ-7 (11) | Wer kann Mitglied sein, Nachweis | nur Privatpersonen, geprüft wird die buchende Person, Nachweis über die Mitgliedsnummer | A-KUND-4 |
| Ⓞ-8 (12) | Steuer | a) 7 % für Mitglieder; b) Gutscheine mit MwSt. beim Kauf nach Käufergruppe; c) Gratis-Codes steuerfrei; d) Saisonabo 7 %, da Mitgliedschaft zwingend, andere Events 19 % | `gutschein_steuer = beim_kauf` (A-GUT-5), `abo_nur_mitglieder` (A-DAUER-5), A-RECH-9; Bestätigung des Steuerberaters noch offen (12.2) |
| Ⓞ-11 (13) | Saisonende, Prüfliste | Wintermitgliedschaft endet **30. April**; Abgleich zum **31. August**, spätere Mitgliedschaften ad hoc | A-KUND-4, A-KUND-5 |
| O-10 (14) | Gespräch mit Hupfauer | Betreiber organisiert einen Termin | – |
| Ⓞ-9 (15) | Heizvorlauf 30 min | Vorschlag angenommen | A-HALLE-1 |
| Ⓞ-10 (16) | Verzögerung am Tastenfeld | angenommen | A-HALLE-3 |
| Ⓞ-13 (17) | Stripe oder Mollie | **zunächst Stripe**, sofern ein späterer Wechsel möglich ist | Plugin, `zahlung_anbieter = stripe` (A-ZAHL-5) |

### 12.2 Noch offen

| Nr | Frage | Annahme, mit der wir arbeiten |
|---|---|---|
| Ⓞ-1 | Portal und Hauptsystem als getrennte Container auf demselben Proxmox-Host – die Trennung ist damit schwächer als bei getrennter Hardware | akzeptiert zugunsten der Betriebskosten (Abschnitt 11) |
| Ⓞ-8 | **Steuerberater.** Stammen die Angaben aus Frage 12 vom Steuerberater? Zu bestätigen: 7 % für Mitglieder (Zweckbetrieb, § 12 Abs. 2 Nr. 8a UStG); Gutschein als Einzweckgutschein mit dem Satz des Käufers – nur tragfähig, wenn ein Mitgliedsgutschein nicht an Nicht-Mitglieder geht (A-GUT-1b); Events zu 19 % auch bei Mitgliedern?; Saisonrechnung im Voraus | Vorgaben wie in A-GUT-5, A-GUT-1b, A-RECH-9; jede Antwort ist eine Einstellung |
| Ⓞ-18 | Was kostet ein Gutschein über zwei Stunden, für Mitglieder und für Nicht-Mitglieder? | je Gruppe ein fester Preis in den Einstellungen; ohne Wert kein Verkauf (A-GUT-1a) |
| Ⓞ-19 | Darf ein zum Mitgliedspreis gekaufter Gutschein von einem Nicht-Mitglied eingelöst werden? | nein, `mitgliedsgutschein_nur_mitglieder = ja` (A-GUT-1b) |
| Ⓞ-20 | Gutschein bei längeren Buchungen: mehrere Gutscheine je Buchung? | einer je Buchung; weitere Slots online (A-GUT-1) |
| Ⓞ-21 | Freischaltcode: Feld und Uhrzeit frei wählbar oder vom Betreiber vorgegeben? | frei wählbar, Vorgabe je Code optional; Gültigkeit 90 Tage (A-GUT-2) |
| Ⓞ-22 | Abo-Absagen: drei je Abo oder je Kunde? Gilt die 24-h-Frist? Sagt der Kunde selbst im Portal ab? | je Abo, Frist gilt, Absage im Portal (A-DAUER-3) |
| Ⓞ-23 | Guthaben aus Abo-Absagen: automatisch mit der nächsten Saisonrechnung verrechnen, Auszahlung nur auf Wunsch? | ja (A-ZAHL-4) |
| Ⓞ-24 | Rechnungskunden im Portal: nur Termine sehen und absagen, kein Gutscheinkauf? | ja (A-KUND-7) |
| Ⓞ-14 | Mehrere Felder in einer Buchung (Turniere durch Kunden) | Stufe 1 nein, Betreiber legt Sperre/Buchungen an |
| Ⓞ-15 | Anzeige fremder Belegung: nur „belegt" ohne Namen | ja |
| Ⓞ-16 | Sollen Sperren (etwa ein Turnier) Licht und Heizung automatisch schalten? | nein; der Betreiber nutzt Handbetrieb und Master-PIN (Hallendienst-Design § 1) |
