# Beachhub – Anforderungen und Systemdesign

Stand: 2026-09-05 · Status: Entwurf zur Abstimmung · Zielgruppe: Entwicklungsteam

## 1. Ziel

Beachhub bucht, rechnet ab und steuert eine Beachvolleyballhalle im Winterbetrieb.
Kunden buchen Felder über ein Internetportal, der Betreiber verwaltet Felder, Preise,
Dauerbuchungen und Rechnungen, und die Halle schaltet Licht, Heizung und Tür
automatisch passend zu den Buchungen – auch dann, wenn die Internetverbindung
mehrere Tage ausfällt.

Beteiligte: Betreiber der Halle (Auftraggeber, Administrator), zwei Entwickler,
Kunden (Privatpersonen, Vereine, Mitglieder) und deren Mitspieler.

## 2. Systemübersicht

Drei Systeme, ein Integrationsprinzip: **Das Hauptsystem baut alle Verbindungen
selbst auf. Kein System verbindet sich zum Hauptsystem.**

| System | Ort | Hält | Entscheidet |
|---|---|---|---|
| **Buchungsportal** (`portal/`) | Internet, öffentlicher Webserver | Konten (E-Mail, Anzeigename, Kundengruppe), Login-Codes, Anfragetabelle, signierter Lesestand, Gruppenverwaltung | Fachlich nichts. Login, Eingabevalidierung, Gruppenverwaltung |
| **Hauptsystem** (`core/`) | Cloud-VM (z. B. Hetzner), nur per VPN erreichbar | Alle Stammdaten, Tarife, Buchungen, Rechnungen, Zahlungen, Guthaben, PINs, Ereignisse, Audit-Log | Verfügbarkeit, Preis, Storno, Rechnung, PIN, Alarme |
| **Hallendienst** (`hall/`) | Rechner in der Halle, neben Home Assistant | Betriebsplan der nächsten 7 Tage (Buchungs-ID, Feld, Zeit, PIN-Hash), Ereignis-Warteschlange | Licht, Heizung, Türfreigabe, Präsenzbewertung; autonom bis 7 Tage |

```
 Kunde ──HTTPS──▶ Buchungsportal ◀──WebSocket/mTLS (ausgehend)── Hauptsystem ──WireGuard (ausgehend vom Hallendienst)──▶ Hallendienst ──REST/WS──▶ Home Assistant
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
- **A-FELD-4** Je Feld sind die zugehörigen Home-Assistant-Entitäten (Licht, Präsenzsensor) als Konfiguration des Hallendienstes hinterlegt, nicht im Code.

### 3.2 Kunden und Kundengruppen

- **A-KUND-1** Kunde: Name, E-Mail (Schlüssel zum Portalkonto), Rechnungsadresse, Kundengruppe, Zahlungsart (online / Rechnung), Guthaben.
- **A-KUND-2** Kundengruppen (Privat, Verein, Mitglied, …) sind konfigurierbar und steuern Tarif und Standard-Zahlungsart.
- **A-KUND-3** Neue Portalkonten sind zunächst Kundengruppe „Privat“ mit Onlinezahlung. Der Betreiber ordnet Kunden im Admin-UI anderen Gruppen zu.

### 3.3 Tarife

- **A-TARIF-1** Preisregeln mit Gültigkeit nach Feld, Wochentag, Uhrzeitbereich, Kundengruppe und Zeitraum (von/bis). Preis je Slot, in Euro mit zwei Nachkommastellen, inklusive USt.
- **A-TARIF-2** Auflösung: die spezifischste passende Regel gewinnt (mehr gesetzte Kriterien = spezifischer); bei Gleichstand die neuere. Gibt es keine Regel, ist der Slot nicht buchbar und der Betreiber wird gewarnt.
- **A-TARIF-3** Der Preis wird zum Zeitpunkt der Buchung ermittelt und an der Buchung gespeichert. Spätere Tarifänderungen ändern bestehende Buchungen nicht.

### 3.4 Buchungen

- **A-BUCH-1** Einzelbuchung: ein Feld, ein zusammenhängender Zeitraum aus einem oder mehreren aufeinanderfolgenden Slots des Feldes.
- **A-BUCH-2** Statuskette: `angefragt → reserviert → bestätigt → durchgeführt | nicht_erschienen | storniert`. Zusätzlich `abgelehnt` (aus `angefragt`) und `verfallen` (aus `reserviert`, Zahlungsfrist abgelaufen).
- **A-BUCH-3** Keine Überschneidung zweier Buchungen oder Sperren auf demselben Feld. Die Prüfung erfolgt im Hauptsystem innerhalb einer Datenbanktransaktion mit Exklusionsconstraint.
- **A-BUCH-4** Jede bestätigte Buchung erhält eine PIN (6-stellig, zufällig, je Buchung eindeutig innerhalb ihres Zeitfensters ± Vorlauf). Dauerbuchungen erhalten eine PIN für alle Termine.
- **A-BUCH-5** **Buchungsfenster:** Einzelbuchungen im Portal sind nur möglich von `fenster_tage` vor dem Termin (konfigurierbar, Start: 14 Tage) bis `mindestvorlauf_minuten` vor Slotbeginn (konfigurierbar, Start: 60). Dauerbuchungen und Sperren unterliegen dem Fenster nicht.
- **A-BUCH-6** Der Betreiber kann im Admin-UI Buchungen ohne Fenster und ohne Zahlungsschritt anlegen, ändern und stornieren (mit Auditeintrag).

### 3.5 Dauerbuchungen

- **A-DAUER-1** Regel: Feld, Wochentag, Zeitfenster, Zeitraum von/bis, Kunde. Bei Anlage werden alle Einzeltermine als Buchungen erzeugt (Status `bestätigt`, Zahlungsart des Kunden). Kollisionen mit bestehenden Buchungen/Sperren werden vor der Anlage gelistet; der Betreiber entscheidet je Termin (auslassen oder bestehende Buchung stornieren).
- **A-DAUER-2** Anlage nur durch den Betreiber. Kunden können im Portal eine Dauerbuchung *anfragen* (Freitext + Wunschzeit); der Betreiber legt sie im Admin-UI an.
- **A-DAUER-3** Einzeltermine einer Dauerbuchung kann der Kunde im Portal stornieren; es gilt die Stornoregel (3.7).
- **A-DAUER-4** Betreiber kann eine Dauerbuchung ab einem Datum beenden; künftige Termine werden storniert (kostenfrei).

### 3.6 Sperren

- **A-SPERR-1** Betreiber-Sperren: Feld(er), Zeitraum, Grund (Turnier, Wartung, Eigenbedarf, Feiertag). Sperren blockieren Buchungen, erzeugen keine Kosten und erscheinen im Portal als „nicht verfügbar“ ohne Grund.
- **A-SPERR-2** Sperren über bestehenden Buchungen erfordern eine Entscheidung je betroffener Buchung (behalten oder kostenfrei stornieren mit Benachrichtigung).

### 3.7 Storno und Nachbuchung

- **A-STORNO-1** Kostenfreies Storno bis `storno_frist_stunden` vor Slotbeginn (konfigurierbar, Start: 24 h). Der Slot wird sofort wieder frei.
- **A-STORNO-2** Storno nach der Frist: Zahlungspflicht bleibt bestehen, der Slot wird trotzdem freigegeben. Das Storno merkt sich `nachbuchung_offen = true`.
- **A-STORNO-3** Wird der freigegebene Zeitraum (ganz) bis Slotbeginn von einem *anderen* Kunden bestätigt gebucht, wird das Storno rückwirkend kostenfrei: Onlinezahler erhalten den Betrag als Guthaben, bei Rechnungskunden entfällt die Position. Teilweise Nachbuchung (z. B. 1 von 2 Stunden) macht anteilig kostenfrei.
- **A-STORNO-4** Der Betreiber kann jedes Storno manuell auf kostenfrei setzen (Kulanz), mit Grund und Auditeintrag.
- **A-STORNO-5** Storno durch den Kunden ist nur bis Slotbeginn möglich.

### 3.8 Zahlung

- **A-ZAHL-1** Zahlungsarten je Kunde: `online` (Stripe Checkout) oder `rechnung` (Sammelrechnung).
- **A-ZAHL-2** Online: Nach `reserviert` erzeugt das Hauptsystem eine Stripe-Checkout-Session (Betrag, Buchungs-ID als Referenz, Ablauf `zahlungsfrist_minuten`, Start: 15). Der Kunde zahlt im Portal. Der Stripe-Webhook trifft im Portal ein, wird als Anfrage `zahlung_eingegangen` gespeichert; das Hauptsystem verifiziert die Webhook-Signatur selbst (Stripe-Secret liegt nur im Hauptsystem) und setzt die Buchung auf `bestätigt`.
- **A-ZAHL-3** Verfällt die Zahlungsfrist, wird die Buchung `verfallen` und der Slot frei. Trifft die Zahlung dennoch später ein, entsteht Guthaben und der Betreiber wird informiert.
- **A-ZAHL-4** Guthaben: je Kunde ein Guthabenkonto in Euro. Guthaben wird bei Onlinebuchungen automatisch verrechnet (Restbetrag über Stripe; ist das Guthaben ausreichend, entfällt der Zahlungsschritt). Das System überweist nie Geld zurück. Der Betreiber kann Auszahlungen manuell veranlassen und im Admin-UI abhaken (Guthaben wird gebucht).
- **A-ZAHL-5** Das Zahlungsmodul ist eine austauschbare Schnittstelle (`PaymentProvider`: Session erzeugen, Ereignis verifizieren, Status abfragen). Stufe 1: Stripe. Kandidat für später: PayPal (nur lesend über Transaktionsabfrage).
- **A-ZAHL-6** Das System löst keine Erstattungen, Auszahlungen oder Lastschriften aus.

### 3.9 Rechnungen

- **A-RECH-1** Rechnungen nach § 14 UStG: fortlaufende, lückenlose Nummer (`JJJJ-NNNNN`), Rechnungsdatum, Leistungsdatum/-zeitraum, Betreiberdaten inkl. USt-IdNr./Steuernummer, Kundendaten, Positionen mit Netto, USt-Satz, USt, Brutto.
- **A-RECH-2** Onlinezahler: eine Rechnung je bestätigter Buchung, erzeugt bei Zahlungseingang, Status `bezahlt`.
- **A-RECH-3** Rechnungskunden: Sammelrechnung je Kalendermonat mit allen durchgeführten, nicht erschienenen und kostenpflichtig stornierten Terminen; erzeugt automatisch am konfigurierbaren Tag des Folgemonats, Status `offen`. Betreiber setzt manuell auf `bezahlt` (kein Bankabgleich in Stufe 1).
- **A-RECH-4** Rechnungs-PDFs werden unveränderbar archiviert (Datei + SHA-256 in der Datenbank). Korrekturen nur per Stornorechnung (Gutschrift) und Neuausstellung.
- **A-RECH-5** Kunden laden Rechnungen im Portal herunter. Das Portal speichert keine Rechnungen; es fordert das PDF über die Anfragetabelle an und stellt es über einen einmaligen, 10 Minuten gültigen Link bereit.
- **A-RECH-6** Export: CSV aller Rechnungen und Positionen je Zeitraum für die externe Buchhaltung.

### 3.10 Hallensteuerung

- **A-HALLE-1** Heizung: Sollwert `spiel_temperatur` ab `heiz_vorlauf_minuten` (Start: 60) vor der ersten Buchung eines Blocks bis Ende der letzten; sonst `grund_temperatur`. Hallenweit, konfigurierbar. Ob je Feld geheizt werden kann, ist eine Konfigurationsoption (Zonen).
- **A-HALLE-2** Licht je Feld: an ab Slotbeginn minus `licht_vorlauf_minuten` (Start: 5) bis Slotende plus `licht_nachlauf_minuten` (Start: 5). Zwischen direkt aufeinanderfolgenden Buchungen bleibt es an.
- **A-HALLE-3** Tür: PIN einer Buchung öffnet von Slotbeginn minus `zutritt_vorlauf_minuten` (Start: 15) bis Slotende. Ein Betreiber-Master-PIN (nur lokal konfiguriert, nicht im Hauptsystem) öffnet immer. Nach 5 Fehleingaben in 5 Minuten wird das Tastenfeld 15 Minuten gesperrt und ein Ereignis gemeldet.
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
- **A-MAIL-2** Hauptsystem versendet: Buchungsbestätigung mit PIN, Ablehnung, Zahlungsfrist abgelaufen, Stornobestätigung (mit Hinweis auf mögliche Nachbuchung), Nachbuchung erfolgt (Guthaben), Rechnung, Erinnerung 24 h vor Termin mit PIN, Kontolöschung. Betreiber-Alarme (3.10) ebenfalls per E-Mail.
- **A-MAIL-3** Alle Mails sind Templates mit Betreiberdaten aus der Konfiguration.

### 3.13 Admin-UI (Hauptsystem)

- **A-ADM-1** Belegungsplan (Kalender je Feld, Woche/Tag) mit Anlegen/Ändern/Stornieren von Buchungen, Dauerbuchungen, Sperren per Klick.
- **A-ADM-2** Stammdaten: Felder, Betriebszeiten, Kundengruppen, Tarife, Konfigurationswerte (alle in dieser Spezifikation als konfigurierbar bezeichneten Größen).
- **A-ADM-3** Kunden: Liste, Suche, Gruppe/Zahlungsart ändern, Guthaben buchen, Auszahlung abhaken, Historie.
- **A-ADM-4** Rechnungen: Liste, Filter, PDF, auf bezahlt setzen, Stornorechnung, CSV-Export, Monatslauf manuell auslösen.
- **A-ADM-5** Halle: Live-Status (Planversion, Kontakt, HA-Status, Licht/Heizung/Tür je Feld), Ereignisliste, Alarme, Handbetrieb.
- **A-ADM-6** Stornos mit `nachbuchung_offen`, Klärungsliste (unzuordenbare Zahlungen, Präsenz ohne Buchung), Audit-Log.
- **A-ADM-7** Login mit Benutzername/Passwort und TOTP-2FA; mehrere Admin-Benutzer mit Rollen `admin` und `lesend`.

## 4. Nichtfunktionale Anforderungen

- **N-1 Sicherheit:** Hauptsystem ohne öffentliche Erreichbarkeit; alle Verbindungen zum Portal und zur Halle werden vom Hauptsystem bzw. Hallendienst ausgehend aufgebaut. Portal-Endpunkte für das Hauptsystem nur mit Client-Zertifikat (mTLS). Alle Lesestände und Betriebspläne sind Ed25519-signiert.
- **N-2 Datenminimierung:** Portal ohne Postadressen, Zahlungsdaten, Rechnungen. Halle ohne Personendaten. Siehe Abschnitt 9.
- **N-3 Autonomie Halle:** ≥ 72 h ohne Verbindung, Ziel 7 Tage.
- **N-4 Verfügbarkeit Portal:** Bei Ausfall des Hauptsystems bleibt das Portal lesbar (letzter Lesestand, Gruppenverwaltung voll nutzbar); Anfragen werden gesammelt und nach Rückkehr verarbeitet.
- **N-5 Konfigurierbarkeit:** Alle Fristen, Vorläufe, Temperaturen, Fenster und Preise sind Daten, kein Code.
- **N-6 Nachvollziehbarkeit:** Audit-Log für alle Änderungen an Buchungen, Tarifen, Rechnungen, Guthaben, Konfiguration (wer, wann, was, vorher/nachher).
- **N-7 Geldbeträge** durchgehend `Decimal`, nie Fließkomma. Zeitstempel in UTC gespeichert, Anzeige in `Europe/Berlin`.
- **N-8 Sprache:** Oberflächen auf Deutsch, mobil zuerst; Admin-UI für Desktop.
- **N-9 Skala:** 3–6 Felder, wenige hundert Kunden, wenige tausend Buchungen je Saison. Keine horizontale Skalierung nötig.
- **N-10 Backup:** Hauptsystem täglich verschlüsselt an zweiten Ort; Wiederherstellung dokumentiert und vor Saisonstart getestet. Portal täglich (Gruppendaten sind dort Master).

## 5. Datenmodell Hauptsystem (`core/`)

PostgreSQL. Alle Tabellen mit `id` (UUID), `created_at`, `updated_at`.

| Tabelle | Wesentliche Felder |
|---|---|
| `feld` | name, aktiv, reihenfolge, ha_licht_entity, ha_praesenz_entity, heizzone |
| `feld_raster` | feld_id, wochentag (0–6 oder null = alle), modus (`dauer`/`fenster`), slot_minuten, fenster_json (Liste von [start, ende]) |
| `betriebszeit` | wochentag, oeffnet, schliesst, gueltig_von, gueltig_bis |
| `ausnahmetag` | datum, geschlossen (bool), oeffnet, schliesst, grund |
| `kundengruppe` | name, standard_zahlungsart |
| `kunde` | name, email (unique, lower), adresse_*, kundengruppe_id, zahlungsart, guthaben (Decimal), portal_konto_id, stripe_customer_id, anonymisiert_am |
| `tarif` | name, preis, feld_id?, wochentag?, uhrzeit_von?, uhrzeit_bis?, kundengruppe_id?, gueltig_von?, gueltig_bis?, aktiv |
| `dauerbuchung` | kunde_id, feld_id, wochentag, start, ende, gueltig_von, gueltig_bis, pin_hash, pin_klar (verschlüsselt), beendet_am |
| `buchung` | feld_id, kunde_id, beginn, ende (tstzrange-Exklusion je feld_id für Status ≠ storniert/verfallen/abgelehnt), status, preis, zahlungsart, pin_hash, pin_klar (verschlüsselt), dauerbuchung_id?, anfrage_id (Portal-Anfrage, unique), reserviert_bis?, rechnung_position_id?, anwesenheit (`unbekannt`/`bestaetigt`/`nicht_erschienen`) |
| `sperre` | feld_id?, beginn, ende, grund (tstzrange-Exklusion gegen buchung) |
| `storno` | buchung_id, zeitpunkt, durch (`kunde`/`betreiber`/`system`), kostenfrei, grund, nachbuchung_offen, nachbuchung_buchung_id?, freigestellt_betrag |
| `zahlung` | kunde_id, buchung_id?, provider, provider_ref (unique), betrag, status, empfangen_am, rohdaten_json |
| `guthaben_buchung` | kunde_id, betrag (±), art (`storno_gutschrift`/`verrechnung`/`auszahlung`/`manuell`/`ueberzahlung`), bezug_id?, notiz, admin_user_id? |
| `rechnung` | nummer (unique, lückenlos), kunde_id, datum, leistung_von, leistung_bis, netto, ust, brutto, status, pdf_pfad, pdf_sha256, storniert_durch_id? |
| `rechnung_position` | rechnung_id, buchung_id?, text, menge, einzelpreis_brutto, ust_satz |
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

**Kanal.** Das Hauptsystem öffnet eine WebSocket-Verbindung zu `wss://portal/.well-known/beachhub-core` mit Client-Zertifikat (mTLS, eigene interne CA; Portal-Reverse-Proxy erzwingt das Zertifikat nur auf diesem Pfad). Nachrichten sind JSON mit `typ`, `id`, `nutzlast`. Bei Abbruch: Reconnect mit Backoff; nach Verbindung holt das Hauptsystem alle Anfragen mit `status = offen` nach. Unabhängig davon fragt es alle 60 s per HTTPS (ebenfalls mTLS) `GET /core/anfragen?status=offen` ab, als Rückfall und Kontrolle.

**Anfragetypen** (Portal → Hauptsystem):

| Typ | Nutzlast | Antwort |
|---|---|---|
| `konto_angelegt` | email, anzeigename | kunde_id |
| `konto_geaendert` | anzeigename | ok |
| `konto_loeschen` | – | ok (Anonymisierung terminiert) |
| `buchung_anfragen` | feld_id, beginn, ende | `reserviert` (buchung_id, preis, guthaben_verrechnet, checkout_url?, reserviert_bis) oder `bestaetigt` (Rechnungskunde / voll aus Guthaben) oder `abgelehnt` (grund: belegt, außerhalb_fenster, außerhalb_betriebszeit, kein_tarif, konto_gesperrt) |
| `buchung_stornieren` | buchung_id | ok (kostenfrei ja/nein, nachbuchung_offen) oder abgelehnt |
| `dauerbuchung_anfragen` | wunsch_text, feld_id?, wochentag?, zeit? | ok (Ticket für Betreiber) |
| `zahlung_eingegangen` | provider, rohdaten, signatur_header | ok / ignoriert |
| `rechnung_anfordern` | rechnung_nr | pdf_base64 oder abgelehnt |

Das Hauptsystem behandelt jede Nutzlast als nicht vertrauenswürdig: Konto muss existieren, Buchung muss dem Konto gehören, Zeiten werden gegen Raster geprüft, Webhook-Signatur wird mit dem Stripe-Secret im Hauptsystem verifiziert.

**Idempotenz.** `anfrage_verarbeitet.anfrage_id` ist unique. Eine bereits verarbeitete Anfrage wird mit der gespeicherten Antwort beantwortet, ohne erneute Verarbeitung.

**Lesestand** (Hauptsystem → Portal), jedes Dokument als `{dokument, version, erzeugt_am, inhalt}` mit Ed25519-Signatur über die kanonische JSON-Serialisierung. Das Portal prüft die Signatur mit dem hinterlegten öffentlichen Schlüssel und übernimmt nur höhere Versionen.

| Dokument | Inhalt | Wann |
|---|---|---|
| `belegung` | je Feld: Liste belegter Zeiträume im Buchungsfenster (nur beginn/ende, ohne Kunde), Betriebszeiten, Raster, Fenster-Parameter | nach jeder Änderung (debounced 2 s), nächtlich voll |
| `tarife` | Preisregeln je Kundengruppe (nur die für Kunden sichtbaren) | bei Änderung, nächtlich |
| `konto:<id>` | Kundengruppe, Zahlungsart, Guthaben, eigene Buchungen (id, feld, beginn, ende, status, preis, pin_klar, storno-Info), Rechnungsliste (nummer, datum, brutto, status), offene Reservierungen mit checkout_url | nach jeder Antwort auf eine Anfrage des Kontos, bei Statusänderungen (Anwesenheit, Nachbuchung, Rechnung), nächtlich |

**Fehlerfälle.** Hauptsystem nicht erreichbar → Portal zeigt „Anfrage gesendet, Bestätigung folgt per E-Mail“; nach `antwort_hinweis_sekunden` (Start: 120) ein Hinweis. Reservierungen mit Zahlungsfrist werden vom Hauptsystem beim Verfall bereinigt, auch wenn der Kunde nie zurückkam. Signaturfehler oder Versionsrückschritt im Lesestand → Dokument verwerfen, Alarm an Betreiber.

### 8.2 Hauptsystem ↔ Hallendienst

**Kanal.** WireGuard-Tunnel, initiiert vom Hallendienst (Keepalive), Hauptsystem lauscht nur auf der WireGuard-Schnittstelle. Darüber HTTPS mit mTLS.

| Aufruf (vom Hallendienst) | Inhalt |
|---|---|
| `GET /hall/plan?ab=<version>` | Betriebsplan 7 Tage: Buchungen (buchung_id, feld_id, beginn, ende, pin_hash), Sperren, Felder, Konfiguration; signiert; `304` wenn unverändert. Alle 5 Minuten. |
| `POST /hall/ereignisse` | Liste `{seq, typ, zeitpunkt, feld_id?, buchung_id?, daten}`; Antwort: höchste bestätigte `seq`. Sofort bei neuem Ereignis, sonst alle 60 s. |
| `POST /hall/status` | Planversion, letzter Abruf, HA erreichbar, Zustand je Feld, Handbetrieb. Alle 60 s, huckepack mit Ereignissen. |

Das Hauptsystem erzeugt den Plan neu, sobald sich eine Buchung im 7-Tage-Fenster ändert (Version steigt). Der Hallendienst holt zusätzlich sofort, wenn das Hauptsystem über den Status-Aufruf `plan_neu = true` zurückmeldet.

**Ereignistypen:** `pin_akzeptiert`, `pin_abgelehnt`, `tastenfeld_gesperrt`, `praesenz_start`, `praesenz_ende`, `praesenz_ohne_buchung`, `tuer_offen_ausserhalb`, `licht_geschaltet`, `heizung_gesetzt`, `ha_nicht_erreichbar`, `aktor_fehler`, `plan_verworfen`, `handbetrieb_an/aus`, `dienst_gestartet`.

**Home Assistant.** Der Hallendienst nutzt die HA-REST-API (Dienste aufrufen) und den HA-WebSocket (Zustandsänderungen abonnieren) mit einem Long-Lived Access Token. Keine Custom Component. Erwartete HA-Entitäten je Konfiguration: `light.*` je Feld, `climate.*` je Heizzone, `lock.*`/`switch.*` für den Türöffner, `binary_sensor.*` für Präsenz je Feld und Türkontakt, ein Eingabekanal für das Tastenfeld (z. B. `event.*` oder MQTT-Topic). Der Sollzustand wird alle 30 s aus dem Plan neu berechnet und mit dem Ist abgeglichen (idempotente Steuerung). Präsenzalarm-Erkennung in HA selbst nur als Rückfall, falls der Dienst ausfällt (einfache HA-Automation: Licht aus außerhalb Betriebszeit).

**Sicherheit Halle.** Kein Kundenname, keine E-Mail. PIN nur als Argon2id-Hash. Master-PIN lokal in der Konfiguration. Bei Diebstahl des Hallenrechners sind ausschließlich Buchungszeiten und Hashes betroffen.

## 9. Abläufe

**Einzelbuchung online.** Kunde wählt Slot im Portal (nur innerhalb des Fensters sichtbar) → `buchung_anfragen` → Hauptsystem: Fenster, Betriebszeit, Raster, Kollision (Exklusion in Transaktion), Tarif, Guthaben → Antwort `reserviert` mit `checkout_url` und `reserviert_bis` → Portal leitet zu Stripe → Stripe-Webhook trifft im Portal ein → `zahlung_eingegangen` → Hauptsystem verifiziert, setzt `bestätigt`, vergibt PIN, erzeugt Rechnung, mailt Bestätigung → Lesestand `konto:<id>` aktualisiert → Portal zeigt Buchung mit PIN. Zahlungsfrist verfallen: `verfallen`, Slot frei, Mail.

**Einzelbuchung Rechnungskunde.** Wie oben, Antwort direkt `bestätigt`, keine Zahlung; Position landet in der Monatsrechnung.

**Dauerbuchung.** Betreiber legt im Admin-UI an → Kollisionsliste → Termine erzeugt (`bestätigt`, gemeinsame PIN) → Mail an Kunden → Lesestand. Kunde kann Termine einzeln stornieren.

**Storno nach Frist mit Nachbuchung.** Kunde storniert (T-10 h, Frist 24 h) → Storno kostenpflichtig, `nachbuchung_offen` → Slot im Lesestand frei → anderer Kunde bucht und bestätigt → Hauptsystem erkennt beim Bestätigen: Zeitraum deckt offenes Storno → Storno kostenfrei, Guthaben-Buchung `storno_gutschrift` (Onlinezahler) bzw. Position aus Monatsrechnung entfernt (Rechnungskunde) → Mail an beide. Bleibt der Slot bis Beginn frei: Storno bleibt kostenpflichtig. Nachbuchung durch denselben Kunden zählt nicht.

**Hallentag.** 17:55 Heizung auf Spieltemperatur (erste Buchung 19:00, Vorlauf 60 min, Plan lokal) → 18:45 PIN-Freigabe → 18:55 Licht Feld 2 an → 19:02 PIN eingegeben, Tür öffnet, Ereignis `pin_akzeptiert` → Präsenz Feld 2 erkannt → 21:05 Licht aus, keine Folgebuchung → Heizung Grundtemperatur → Ereignisse gehen gebündelt ans Hauptsystem, Buchung wird `durchgeführt`.

**Internetausfall Halle (3 Tage).** Plan vom letzten Abruf gilt (7 Tage). Alle Abläufe laufen weiter; Ereignisse sammeln sich (`ereignis_queue`). Betreiber erhält nach 60 min „Halle ohne Kontakt“. Neue Buchungen in dieser Zeit erreichen die Halle nicht: Das Hauptsystem vergleicht die von der Halle zuletzt gemeldete Planversion mit dem aktuellen Plan und markiert betroffene Buchungen im Admin-UI als „Halle nicht informiert“. Der Betreiber entscheidet, ob er dem Kunden anderweitig Zutritt verschafft (Master-PIN, persönlich) oder die Buchung kostenfrei storniert. Nach Rückkehr: Plan aktualisiert, Ereignisse nachgeliefert, Anwesenheiten abgeleitet.

**Ausfall Hauptsystem.** Portal zeigt letzten Lesestand, sammelt Anfragen, Gruppenverwaltung uneingeschränkt. Nach Rückkehr verarbeitet das Hauptsystem die Anfragen in Reihenfolge; Reservierungen, deren Zahlungsfrist inzwischen abgelaufen wäre, werden mit neuer Frist erneut angeboten (Kunde bekommt Mail).

## 10. Sicherheit und Datenschutz

- Verbindungen: Kunde→Portal TLS 1.2+; Hauptsystem→Portal WebSocket/HTTPS mit mTLS über interne CA; Halle→Hauptsystem WireGuard + mTLS. Zertifikate 1 Jahr, Rotation dokumentiert.
- Signaturen: Ed25519-Schlüsselpaar des Hauptsystems; öffentlicher Schlüssel in Portal und Halle hinterlegt. Signatur über kanonisches JSON (sortierte Schlüssel, keine Leerzeichen, UTF-8).
- Portal: Magic Link 15 min gültig, einmalig; Antwort auf Login-Versuch immer gleich; Rate-Limit je IP und je E-Mail; Sessions serverseitig, Cookie `HttpOnly; Secure; SameSite=Lax`; CSRF-Token als Router-Abhängigkeit; Content-Security-Policy ohne Inline-Skripte; Abhängigkeits-Scan im CI.
- Stripe-Secret und Webhook-Secret nur im Hauptsystem. Das Portal reicht Webhooks blind durch.
- Hauptsystem: Festplattenverschlüsselung, Firewall nur WireGuard-Port, SSH nur per Schlüssel über WireGuard, Admin-UI nur auf WireGuard-Interface, TOTP-2FA, Passwörter Argon2id.
- Datenminimierung je System wie in Abschnitt 2. Gruppenverwaltung: Mitspieler-E-Mails nur im Portal.
- Löschkonzept: `konto_loeschen` → Portal löscht Gruppendaten und Konto sofort; Hauptsystem setzt Kunden auf „Löschung angefordert“, anonymisiert Stammdaten sofort (Name → „Gelöschter Kunde“, E-Mail → Hash), Rechnungen bleiben 10 Jahre (§ 147 AO) mit den Rechnungsdaten, danach Löschung per Jobs.
- Aufbewahrung Ereignisse Halle: 90 Tage im Detail, danach nur Anwesenheitsstatus je Buchung.
- Datenschutzerklärung, AV-Vertrag mit Hoster und Stripe, Verzeichnis der Verarbeitungstätigkeiten: Aufgabe des Betreibers, das System liefert die technischen Angaben.

## 11. Technik und Vorgehen

- **Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Jinja2 (serverseitig gerendert, kein Frontend-Build, eine `style.css` mit Design-Tokens, Hell/Dunkel, PWA-fähig – wie im SportAbo-Manager). PostgreSQL 16 für Portal und Hauptsystem, SQLite für die Halle. `httpx`/`websockets` für die Kanäle, `pynacl` für Ed25519, `argon2-cffi`, `stripe`, `weasyprint` für Rechnungs-PDFs, `APScheduler` für Jobs.
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
- **Tests:** Fachlogik (Tarifauflösung, Fenster, Kollision, Storno/Nachbuchung, Rechnungsnummern, Kostenteilung) als reine Unit-Tests; Integrationstests je App gegen temporäre Datenbank; Vertragstests: Nachrichten aus `shared` werden von Sender und Empfänger validiert; Hallendienst gegen einen HA-Simulator (Fake-REST/WS) inkl. Offline-Szenario (Verbindung kappen, 72 h simulierte Uhr). Uhr überall injizierbar (`clock`).
- **Betrieb:** Portal hinter Caddy (automatisches TLS); Hauptsystem hinter Caddy auf WireGuard-IP; Halle als Container neben HA (HA OS: als Add-on-Container möglich, sonst Docker). Logs strukturiert (JSON), Alarme per E-Mail; einfache Health-Endpunkte.
- **Ausbaustufen:**
  1. `core`: Datenmodell, Admin-UI (Felder, Betriebszeiten, Tarife, Kunden, Belegungsplan, Sperren, Dauerbuchungen), Rechnungen, Signatur/Lesestand-Erzeugung.
  2. `portal`: Konten, Magic Link, Anfragetabelle, Kanal, Lesestand-Anzeige, Buchung/Storno, Stripe.
  3. `hall`: Plan-Abruf, Licht/Heizung/Tür über HA, PIN-Prüfung, Ereignisse, Offline-Betrieb, Handbetrieb.
  4. `portal`: Gruppenverwaltung (Muster SportAbo-Manager).
  5. Präsenz-Ableitung, Alarme, Klärungslisten, CSV-Export, Feinschliff.

## 12. Offene Punkte und Annahmen (Betreiber bestätigt)

| Nr | Annahme / Frage | Vorschlag |
|---|---|---|
| O-1 | Heizung: hallenweit oder je Zone? Heizungstyp und HA-Anbindung vorhanden? | Hallenweit, `climate`-Entität |
| O-2 | Heizvorlauf 60 min, Spieltemperatur/Grundtemperatur | 60 min, Werte vom Betreiber |
| O-3 | Zutritt: Tastenfeld-Hardware und Türöffner, HA-Integration vorhanden? | Tastenfeld mit HA-Anbindung (z. B. Zigbee/MQTT), elektrischer Türöffner als `switch` |
| O-4 | Präsenzsensoren je Feld: vorhanden oder anzuschaffen? | mmWave-Sensoren je Feld |
| O-5 | Buchungsfenster 14 Tage, Mindestvorlauf 60 min | Werte bestätigen |
| O-6 | Stornofrist 24 h | bestätigen |
| O-7 | Zahlungsfrist 15 min | bestätigen |
| O-8 | Monatsrechnung: Erstellungstag, Zahlungsziel | 3. des Folgemonats, 14 Tage |
| O-9 | USt-Satz auf Feldmiete (19 %), Kleinunternehmerregelung? | 19 %, Betreiber klärt mit Steuerberater |
| O-10 | Dürfen Kunden Dauerbuchungen selbst anfragen? | Ja, als Wunsch an den Betreiber |
| O-11 | Mehrere Felder in einer Buchung (Turniere durch Kunden)? | Stufe 1 nein, Betreiber legt Sperre/Buchungen an |
| O-12 | Teilweise Nachbuchung anteilig kostenfrei? | Ja (A-STORNO-3) |
| O-13 | VPN-Lösung für Admin-Zugang (WireGuard vs. Tailscale) | WireGuard, wie zur Halle |
| O-14 | Betreiber-Alarme: nur E-Mail oder auch Push/Telegram? | Stufe 1 E-Mail |
| O-15 | Anzeige fremder Belegung: nur „belegt“ ohne Namen | ja |
