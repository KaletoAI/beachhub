# Betriebshandbuch: Beachhub-Hauptsystem

Dieses Dokument beschreibt die Inbetriebnahme und den laufenden Betrieb des Beachhub-Hauptsystems
(`core/`) auf der Produktions-VM. Es richtet sich an den Betreiber bzw. die Person, die die VM
administriert. Das Admin-UI ist ausschließlich über WireGuard erreichbar, niemals über das
öffentliche Internet.

## 1. Voraussetzungen

- Eine VM (z. B. bei Hetzner) mit **Ubuntu 24.04 LTS**, mindestens 2 vCPU / 4 GB RAM / 40 GB Platte.
- **Festplattenverschlüsselung**: Bei Hetzner Cloud-Servern ist die Boot-Platte standardmäßig nicht
  verschlüsselt. Verschlüsselung wird über Cloud-Init beim Erststart eingerichtet (LUKS auf der
  Datenpartition) oder durch Installation eines eigenen verschlüsselten Images. Wer Hetzners
  Standard-Images nutzt, sollte mindestens das Datenverzeichnis (`/opt/beachhub/core/data`, enthält
  Rechnungs-PDFs, Signaturschlüssel, Kundendaten in der DB) auf einem LUKS-verschlüsselten Volume
  ablegen. Details zu Cloud-Init/LUKS: Hetzner-Dokumentation „Verschlüsseltes Volume einrichten“.
  Dieses Host-Verzeichnis (`/opt/beachhub/core/data`) wird von `docker compose` nach `/app/data` im
  Container gemountet; `DATA_DIR` und `SIGNATUR_PRIVATSCHLUESSEL_PFAD` werden dafür in
  `core/docker-compose.yml` fest auf `/app/data` bzw. `/app/data/signatur.key` gesetzt (die Werte in
  `.env`/`.env.example` gelten nur für die lokale Entwicklung ohne Docker).
- **Docker** und das Compose-Plugin (`docker compose version` muss funktionieren).
- **WireGuard** (`apt install wireguard`) – Konfiguration siehe `core/deploy/wireguard-beispiel.md`.
- In der Hetzner-Cloud-Firewall darf nach außen nur `51820/udp` (WireGuard) offen sein. Kein Port
  des Hauptsystems (weder `8000`, `8443` noch `8444`) wird öffentlich freigegeben.
- **Für unbeaufsichtigte Backups** (Abschnitt 6) einmalig vorbereiten:
  - Den öffentlichen GPG-Schlüssel des Betreibers auf die VM übertragen und importieren:
    `gpg --import betreiber.pub`. Das Backup-Skript verschlüsselt mit `--trust-model always`, d. h.
    GPG verlangt beim Verschlüsseln keine manuelle Vertrauensbestätigung für diesen Schlüssel (kein
    interaktives „Are you sure you want to use this key?“, das einen Cron-Lauf sonst blockieren
    würde). Das ersetzt keine Prüfung der Echtheit des Schlüssels – dieser sollte vor dem Import auf
    einem sicheren Weg (persönlich, Signal, o. ä.) vom Betreiber bestätigt worden sein.
  - Einen eigenen SSH-Schlüssel für den Backup-Vorgang erzeugen und auf dem Backup-Host hinterlegen:
    `ssh-keygen -t ed25519 -f ~/.ssh/beachhub-backup` und `ssh-copy-id -i ~/.ssh/beachhub-backup.pub
    <ziel-user>@<backup-host>`.
  - Den Host-Key des Backup-Hosts einmalig akzeptieren, damit `scp` im Cron-Lauf nicht auf eine
    interaktive Bestätigung wartet: `ssh -o StrictHostKeyChecking=accept-new <backup-host> true`.

## 2. Installation

Repository nach `/opt/beachhub` klonen:

```bash
sudo mkdir -p /opt/beachhub
sudo chown "$USER" /opt/beachhub
git clone <repo-url> /opt/beachhub
cd /opt/beachhub/core
```

`.env` aus der Vorlage erzeugen und mit echten Geheimnissen füllen:

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # für SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(48))"   # für PIN_SCHLUESSEL
```

Beide erzeugten Werte in `.env` bei `SECRET_KEY=` bzw. `PIN_SCHLUESSEL=` eintragen und `APP_ENV=production`
setzen. Solange `SECRET_KEY`/`PIN_SCHLUESSEL` noch auf dem Standardwert `change-me` stehen, verweigert
die Anwendung bei `APP_ENV=production` den Start (siehe Abschnitt 8, „Störungen“). Auch `BASE_URL`
(`https://10.8.0.1:8443`), `COOKIE_SECURE=true`, `SMTP_*` sowie `BETREIBER_NAME`/`BETREIBER_ADRESSE`/
`BETREIBER_UST_ID`/`BETREIBER_BANK` (erscheinen auf Rechnungen) in `.env` prüfen und ausfüllen.
`DATA_DIR` und `SIGNATUR_PRIVATSCHLUESSEL_PFAD` müssen in `.env` **nicht** angepasst werden: unter
`docker compose` überschreibt `core/docker-compose.yml` beide fest mit `/app/data` bzw.
`/app/data/signatur.key`, dem Pfad im Container, auf den das Host-Verzeichnis `./data`
(= `/opt/beachhub/core/data`) gemountet ist. Die `.env`-Werte (`./data`, relativ zum Arbeitsverzeichnis)
greifen nur, wenn die Anwendung ohne Docker lokal gestartet wird.

Datenbank starten, Schema einspielen, Signaturschlüssel erzeugen, ersten Admin anlegen:

```bash
docker compose up -d db
docker compose run --rm app alembic upgrade head
docker compose run --rm app beachhub-core keygen
docker compose run --rm app beachhub-core create-admin --name <name>
docker compose run --rm app beachhub-core zertifikate --ziel /app/data/zertifikate
```

`keygen` gibt den öffentlichen Signaturschlüssel (Hex) aus – diesen Wert notieren/sichern, er wird
gebraucht, um die Signatur des Lesestands später extern zu prüfen (siehe N-2 in der Spezifikation).
`create-admin` fragt interaktiv nach einem Passwort (mindestens 12 Zeichen) und gibt danach das
TOTP-Secret sowie eine `otpauth://`-URI aus, die der Betreiber mit einer Authenticator-App scannt.
`zertifikate` legt die interne CA (`ca.crt`/`ca.key`) sowie Client-Zertifikate für Portal und Halle
an (Details: `docs/betrieb/portal.md`, Abschnitt 2). **Dieser Schritt muss vor dem ersten
`docker compose up -d` erfolgen**: `core/docker-compose.yml` bindet `ca.crt` fest in den
Caddy-Container ein (`/etc/caddy/beachhub-ca.crt`, Vertrauensanker der Hallenschnittstelle `:8444`,
siehe Abschnitt 5b). Fehlt die Datei beim Start, legt Docker an ihrer Stelle ein leeres
Verzeichnis an, und Caddy startet überhaupt nicht – auch das Admin-UI auf `:8443` wäre dann nicht
erreichbar (siehe Abschnitt 8, „Caddy startet nicht: `ca.crt` ist ein Verzeichnis“).

Anwendung (inklusive Caddy als TLS-Terminierung) starten:

```bash
docker compose up -d
```

## 3. Zugriff

Der Zugriff auf das Admin-UI setzt eine aktive WireGuard-Verbindung voraus (Einrichtung siehe
`core/deploy/wireguard-beispiel.md`). Mit verbundenem WireGuard-Tunnel ist das Admin-UI erreichbar
unter:

```
https://10.8.0.1:8443/admin
```

Caddy verwendet `tls internal`, stellt also ein selbstsigniertes Zertifikat aus. Beim ersten
Aufruf zeigt der Browser eine Zertifikatswarnung – diese wird einmalig akzeptiert (bzw. das
Caddy-eigene Root-Zertifikat kann aus dem Volume `caddy_data` exportiert und im Betriebssystem/
Browser als vertrauenswürdig hinterlegt werden, um die Warnung dauerhaft zu vermeiden).

## 4. Ersteinrichtung im Admin-UI

Nach dem ersten Login (Passwort + TOTP) empfiehlt sich folgende Reihenfolge, bevor der reguläre
Betrieb beginnt:

1. **Felder** anlegen (Name, Raster in Minuten je Feld).
2. **Betriebszeiten** je Wochentag festlegen, inklusive Ausnahmetagen (z. B. Feiertage mit
   abweichenden Zeiten oder Schließung).
3. **Kundengruppen** prüfen: Es gibt genau zwei feste Gruppen, „DJK-Mitglied“ (7 %) und
   „Nicht-Mitglied“ (19 %). Name und Steuersatz lassen sich ändern, eine dritte Gruppe nicht.
   Welche Gruppe für einen Kunden gilt, ergibt sich aus seiner Mitgliedschaft am Tag des Termins.
4. **Tarife** hinterlegen – ohne Gruppe für alle, mit Gruppe für Mitglieder bzw. Nicht-Mitglieder.
5. **Einstellungen** prüfen/anpassen, u. a. `rechnung_zahlungsziel_tage` (Standard: 14),
   `event_ust_satz` (19 % – Vorbelegung für Buchungen, die der Betreiber anlegt),
   `mitgliedschaft_ablauf` (30.04.), `mitglieder_abgleich` (31.08., muss vor dem ersten
   Buchungsfenster der Saison liegen – die Seite warnt sonst), `mitglied_erinnerung_tage` (14)
   und `rechnungskunden_online_buchen` (nein). Für Abos außerdem `saison_zahlungsziel_tage` (14),
   `guthaben_auf_saisonrechnung` (ja), `abo_freie_absagen` (3), `abo_nur_mitglieder` (ja)
   und `saisonende_guthabenliste` (30.04.) prüfen.
6. Einen **Testkunden** anlegen.
7. Eine **Testbuchung** für den Testkunden durchführen und die Bestätigungsmail (PIN) prüfen.
8. Eine **Testrechnung** prüfen: Eine selbst angelegte Buchung bekommt sofort eine offene
   Rechnung; ein Testabo bekommt seine Saisonrechnung. PDF und Mail kontrollieren.
9. Testkunde, Testbuchung und Testrechnung wieder entfernen bzw. stornieren, damit sie nicht in
   den echten Betrieb einfließen; falls die interne Uhr für Tests verstellt wurde, **Uhr
   zurücksetzen** (nur im Entwicklungsmodus verfügbar – im Produktivbetrieb nicht vorhanden).

## 5. Laufender Betrieb

**Rechnungen.** Es gibt keinen Monatslauf mehr. Jede neue Dauerbuchung bekommt bei ihrer Anlage
in derselben Transaktion genau eine **Saisonrechnung** über alle erzeugten Termine
(Zahlungsziel `saison_zahlungsziel_tage`, Vorgabe 14 Tage). Vorhandenes Guthaben wird dabei
verrechnet, solange `guthaben_auf_saisonrechnung` an ist (Vorgabe ja). Die Rechnung behält den
vollen Betrag; die Verrechnung zählt als Zahlung. Bei vollständiger Deckung ist sie bezahlt,
sonst offen. Später hinzukommendes Guthaben wird nicht automatisch nachverrechnet. Den
Zahlungseingang per Überweisung haken Sie auf der Rechnungsseite ab. Selbst angelegte Buchungen
bekommen sofort eine offene Rechnung, bestätigte Onlinebuchungen eine bezahlte.

**Bestandsabos und Neuausstellung.** Beim Upgrade müssen Sie vorhandene Abos auf aktive oder
kostenpflichtig abgesagte, unberechnete Termine prüfen. Die Migration erstellt keine Rechnungen
automatisch. Auf der
Abo-Detailseite erzeugt **Saisonrechnung erstellen** auf Ihren ausdrücklichen Aufruf eine Rechnung
nur über diese Termine. Bereits berechnete oder korrigierte Termine sowie kostenfreie Absagen
bleiben ausgeschlossen; historische Belege bleiben unverändert. Es kann nur eine nicht stornierte
Saisonrechnung je Abo geben. Nach vollständigem Storno ist über dieselbe Aktion eine Neuausstellung
für freigegebene aktive oder kostenpflichtig abgesagte, unberechnete Termine möglich; auch das
geschieht nicht automatisch.

**Korrekturen.** Wird eine berechnete Buchung kostenfrei storniert (innerhalb der Frist, als freie
Abo-Absage, per Kulanz, durch eine Sperre oder weil ein Abo endet), entsteht automatisch ein
Korrekturbeleg über ihre Position. War die Rechnung bezahlt, wird der Betrag Guthaben; war sie
unbezahlt, sinkt die Forderung. Bei teilweiser Zahlung sinkt zuerst die offene Forderung; nur der
darüber hinausgehende Betrag wird Guthaben. Sind alle Positionen korrigiert, ist die Rechnung
storniert. Ist der Restbetrag vollständig gedeckt und bleiben Positionen bestehen, gilt die Rechnung
als bezahlt. Das Original-PDF bleibt unverändert. Auf der Rechnungsseite können Sie einzelne noch
nicht korrigierte Positionen mit einem Grund als Teil-Stornorechnung korrigieren. Diese manuelle
Belegkorrektur ändert den Buchungsstatus nicht und gibt keinen Platz frei. Die Spalte **Offen**
in der Rechnungsliste berücksichtigt Zahlungen und Korrekturen. Beim vollständigen Rechnungsstorno
zur Neuausstellung wird der verbleibende bereits bezahlte Anteil ebenfalls als Guthaben
zurückgegeben. Frühere Teilgutschriften werden nicht nochmals
erstattet; bereits korrigierte aktive Termine bleiben von einer Neuausstellung ausgeschlossen.
Die neuen Belege verrechnen Guthaben entsprechend Ihrer Einstellung; historische Zahlungen
bleiben an der alten Rechnung. Stornoguthaben entsteht immer zusammen mit einem Korrekturbeleg;
PDF und Mail werden erst nach erfolgreichem Commit versandt.

**Abo-Absagen.** Kunden sagen Abo-Termine im Portal bis zum Beginn ab. Bis zu `abo_freie_absagen`
(Vorgabe 3) Absagen je Abo innerhalb der Stornofrist sind kostenfrei; weitere Absagen und Absagen
nach der Frist geben den Platz frei, der Termin bleibt aber berechnet. `0` schaltet freie
Kundenabsagen aus. Jede kostenfreie Kundenabsage verbraucht Kontingent, auch für einen noch
unberechneten Termin (etwa zwischen Vollstorno und Neuausstellung): Er wird danach nicht mehr
berechnet. Nur ein bereits gutgeschriebener Termin (Teil-Stornorechnung) bleibt kostenfrei, ohne
Kontingent zu verbrauchen. Kulanz, Sperren und Beenden durch den Betreiber verbrauchen ebenfalls
kein Kontingent. Eine kostenpflichtige
Betreiberentscheidung wird in der Stornomail als solche benannt.
Die Portalbestätigung zeigt das verbleibende Kontingent aus dem signierten Kontostand und erklärt
die Bedingungen und finanziellen Folgen. Über die Absage entscheidet das Hauptsystem bei der
Verarbeitung; eine ausführlichere Abo-Übersicht im Portal bleibt für den weiteren Ausbau vorgesehen.

**Guthabenliste.** Unter Kunden → Guthabenliste sehen Sie jederzeit positive Guthaben nicht
anonymisierter Kunden und deren Gesamtsumme. Ab dem Stichtag `saisonende_guthabenliste` (Vorgabe
30.04.) prüft der tägliche Lauf um **07:05 Uhr Europe/Berlin** die Jahreserinnerung. Ein vor dem
Marker-Commit ausgefallener Lauf wird am nächsten täglichen Lauf nachgeholt. Die Mail enthält
Anzahl, Summe und einen Link zur Liste, keine vollständige Kundenliste. `BASE_URL` muss auf die
richtige Verwaltungsadresse zeigen. Der Jahresmarker wird vor dem Mailversand gespeichert:
Scheitert danach SMTP, wird der Fehler protokolliert und die Mail im selben Jahr nicht automatisch
wiederholt; die Liste bleibt erreichbar.

Auszahlungen führen Sie auf Wunsch des Kunden selbst per Überweisung aus und haken danach den
positiven Betrag in der Liste ab. Teilbeträge sind möglich, höchstens bis zum verfügbaren Guthaben.
Wurde der Kunde inzwischen anonymisiert, lehnt das Hauptsystem das Abhaken ab und meldet
„Kunde nicht gefunden“; das Guthaben bleibt unverändert.
Das Abhaken vermindert das Guthaben und wird auditiert; das System überweist selbst kein Geld.

Rechnungen sind im Admin-UI unter „Rechnungen“ einsehbar; von dort steht auch ein
**CSV-Export** (`/rechnungen/export.csv`) für den Steuerberater zur Verfügung, gefiltert nach
Zeitraum.

Logs der laufenden Anwendung:

```bash
docker compose logs -f app
```

### Mitgliedschaft

- **Anträge** aus dem Portal kommen per Mail und stehen unter Kunden → Mitgliedsanträge.
  Freischalten, Verlängern, Beenden und Verwerfen geschieht auf der Kundenseite. Der Kunde bekommt
  eine Mail beim Freischalten und Verlängern sowie beim Beenden einer noch laufenden
  Mitgliedschaft; beim Verwerfen eines Antrags geht keine Mail raus.
- **Tageslauf um 07:00 Uhr:** Am Stichtag `mitglieder_abgleich` (einmal im Jahr, bei Ausfall am
  nächsten Lauf nachgeholt) kommt eine Mail an den Betreiber; die Prüfliste steht unter Kunden →
  Mitglieder-Abgleich (mit CSV und Sammelaktionen). Kunden, deren Mitgliedschaft innerhalb von
  `mitglied_erinnerung_tage` Tagen endet, bekommen eine Erinnerung – je Ablaufdatum höchstens eine.
  Außerdem aktualisiert der Tageslauf das Konto im Portal (Mitgliedschaft und Gruppe) für Kunden,
  deren Mitgliedschaft seit dem letzten Lauf abgelaufen ist.
- **Klärungsliste** (System → Klärung Mitgliedschaft): künftige Buchungen zum Mitgliedspreis, deren
  Kunde am Termin kein Mitglied mehr ist. Die Buchungen behalten Preis und Steuersatz, bis sie
  geklärt oder storniert sind.
- **Rechnungskunden** buchen nicht online, solange `rechnungskunden_online_buchen` aus ist; ihre
  Buchungen legt der Betreiber an.

## 5a. Kanal zum Portal

Das Hauptsystem holt Anfragen des Portals per Long-Polling ab und liefert Antworten und Lesestände
aus; das Portal kann das Hauptsystem nicht erreichen. Der Kanal startet mit der Anwendung, sobald
`PORTAL_URL` gesetzt ist (`ENABLE_KANAL=false` schaltet ihn ab). Einrichtung von Zertifikaten und
Token: `docs/betrieb/portal.md`, Abschnitt 2.

`PORTAL_URL` (mit `:8443`) ist ausschließlich die mTLS-Kanaladresse für das Hauptsystem selbst;
`PORTAL_OEFFENTLICHE_URL` ist die Adresse, auf die der Kunden-Browser nach der Zahlung
zurückgeschickt wird. Im Produktivbetrieb sind `PORTAL_CLIENT_CERT` und `PORTAL_CLIENT_KEY`
Pflicht, sobald `PORTAL_URL` gesetzt ist; `PORTAL_CA` bleibt dabei in aller Regel leer – sie ist
der Vertrauensanker für das *Server*-Zertifikat des Portals, das Portal hat aber ein öffentliches
Let's-Encrypt-Zertifikat (ACME) und **nicht** eines aus der internen CA. `PORTAL_CA` **nicht** auf
die interne `ca.crt` setzen.

- Ist das Portal 30 Minuten nicht erreichbar, kommt eine Mail „Portal nicht erreichbar“, bei
  Rückkehr „Portal wieder erreichbar“.
- Reservierungen ohne Zahlung verfallen nach `zahlungsfrist_minuten`; der Kunde bekommt eine Mail.
- Bis zur Entscheidung über den Zahlungsanbieter (Ⓞ-13) gibt es nur die Testzahlung. Mit gesetzter
  `PORTAL_URL` und `APP_ENV=production` startet das Hauptsystem deshalb bewusst nicht.

## 5b. Hallendienst anbinden

Der Hallendienst in der Halle ruft das Hauptsystem über WireGuard auf Port **8444** auf; Caddy
lässt dort nur `/hall/*` durch und nur mit einem Client-Zertifikat der internen CA. Zusätzlich
prüft das Hauptsystem den Token `HALL_TOKEN`. Die Site hört auf den internen Hostnamen
`kern.beachhub.wg` statt auf die nackte IP `10.8.0.1`: Für eine IP-Adresse schicken TLS-Clients
kein SNI (RFC 6066), und ohne SNI kann Caddy die `client_auth`-Richtlinie der Site keiner
Verbindung zuordnen – einen Aufruf über die nackte IP weist Caddy deshalb mit HTTP 421
(„Misdirected Request“) ab, mit oder ohne Client-Zertifikat. Am Hostnamen greift die
mTLS-Prüfung: Ohne oder mit falschem Client-Zertifikat scheitert dort schon der TLS-Handshake
(siehe „Prüfen“ unten). Der Hallendienst löst `kern.beachhub.wg` deshalb über `extra_hosts` in
`hall/docker-compose.yml` auf die WireGuard-Adresse des Hauptsystems auf (Details:
`docs/betrieb/hallendienst.md`, Abschnitt 2). `core/deploy/Caddyfile` bindet beide Sites
zusätzlich fest an `10.8.0.1` (`bind 10.8.0.1`), damit Caddy nicht auf allen Interfaces der VM
lauscht.

1. `HALL_TOKEN` in `core/.env` auf einen langen Zufallswert setzen und denselben Wert in
   `hall/.env` eintragen. Leer bedeutet: Schnittstelle aus (404).
2. Interne CA und Client-Zertifikate entstehen bereits bei der Ersteinrichtung (Abschnitt 2,
   `beachhub-core zertifikate`). `halle.crt`/`halle.key` sowie Caddys eigene interne Root-CA
   (`CORE_CA`, Export aus dem Volume `caddy_data`, **nicht** `ca.crt`) kommen auf den
   Hallenrechner – vollständiger Ablauf inklusive der jährlichen Rotation von `halle.crt`:
   `docs/betrieb/hallendienst.md`, Abschnitt 2.
3. `docker compose up -d` – danach zeigt **System → Halle** den ersten Kontakt.

**Prüfen** (von einem Rechner mit WireGuard-Verbindung und den Dateien aus Schritt 2 –
`halle.crt`, `halle.key`, `caddy-root.crt`):

```bash
curl --resolve kern.beachhub.wg:8444:10.8.0.1 --cacert caddy-root.crt \
  --cert halle.crt --key halle.key -H "Authorization: Bearer <HALL_TOKEN>" \
  "https://kern.beachhub.wg:8444/hall/plan?ab=0"
```

Erwartet: HTTP 200 mit dem signierten Plan als JSON. Ohne `--cert`/`--key` schlägt schon der
TLS-Handshake fehl („certificate required“) – das ist beabsichtigt.

Ein Wechsel der internen CA (`ca.crt`/`ca.key` gemeinsam entfernt und `zertifikate` erneut
ausgeführt – **nicht** jeder erneute Aufruf des Befehls: ohne entfernte CA-Dateien bleiben CA und
alle Schlüssel bestehen, er stellt aber jedes Mal alle genannten Client-Zertifikate neu aus, ohne
`--name` also `portal-kanal.crt` und `halle.crt`; für die jährliche Rotation der Halle allein
deshalb `--name halle`) erfordert einen Neustart
bzw. mindestens ein Reload der Caddy-Site `:8444` (`docker compose restart caddy`), damit der neue
`trust_pool` geladen wird, sowie neue `halle.crt`/`halle.key` auf dem Hallenrechner – Details:
`docs/betrieb/hallendienst.md`, Abschnitt 2 („Rotation“).

**Backup:** Caddys eigene interne Root-CA (Docker-Volume `caddy_data`, Quelle für
`caddy-root.crt`) ist **nicht** Teil des Backups aus Abschnitt 6 (das sichert nur `data/`, keine
Docker-Volumes). Nach einer Wiederherstellung auf einer neuen VM legt Caddy beim ersten Start
automatisch eine neue interne Root-CA an; `caddy-root.crt` muss dann erneut exportiert und auf den
Hallenrechner kopiert werden (`docs/betrieb/hallendienst.md`, Abschnitt 2).

Das Hauptsystem erzeugt den Plan neu, sobald sich eine Buchung, Sperre, ein Feld oder ein
Hallenwert der Konfiguration ändert, und zusätzlich jede Nacht um 00:05. Meldet sich die Halle
60 Minuten nicht, kommt eine Mail „Halle ohne Kontakt“, bei Rückkehr „Halle wieder verbunden“.
Alarme der Halle (Fehlversuche am Tastenfeld, Geräte, Anwesenheit ohne Buchung, Tür) kommen als
Mail an `EMAIL_FROM`; nachgelieferte Alarme nach einem Ausfall gesammelt in einer Mail.

Einrichtung des Hallenrechners und von Home Assistant: `docs/betrieb/hallendienst.md`.

## 5c. Gutscheine und Freischaltcodes

Stufe 1a-III ist im Hauptsystem und den Kanalverträgen implementiert. Der Verkauf und die
Codeeingabe im Kundenportal folgen in Stufe 2; bis dahin gibt es dafür keine Kundenformulare.
Die E2E-Prüfung verwendet den Fakeanbieter. Eine echte Zahlungsanbindung und die Anbindung
an echte Hallenhardware stehen weiterhin aus.

Die Schemaänderung liegt ausschließlich in Revision `0015`, direkt nach `0014`. Bei einem
späteren Betreiberupgrade zuerst Backup und aktuelle Revisionskette prüfen und die regulären
Migrationen ausführen (Abschnitt 7). Bestehende Jahreszähler und archivierte Rechnungen bleiben
erhalten. Ein Downgrade mit Gutscheinbestand, Gutscheinbelegen oder verbrauchten
Gutschein-Nummernkreisen wird abgewiesen, statt diese Daten zu löschen.

### Freischaltcodes und Verwaltung

Unter **Gutscheine → Freischaltcodes** stellt die Admin-Rolle 1 bis 1000 Einzelcodes aus.
Die lesende Rolle kann Übersicht, Details und CSV ansehen. Die Vorgaben sind eine Einlösung
und 90 Tage Gültigkeit; Dauer und Standardgültigkeit kommen aus den Einstellungen.
Das Einlösungslimit ist zwischen 1 und 2147483647 wählbar; größere Werte werden als
Formularfehler abgewiesen.
Freischaltcodes bleiben auch im Wertgutscheinmodell Buchungseinheiten ohne Geldwert und Steuerbeleg.
Feld und Buchungszeitraum sind optional. Eine benannte Serie lässt sich über die Übersicht
als CSV exportieren; der Export enthält alle Freischaltcodes der Serie, unabhängig vom Listenlimit.

Die Detailseite zeigt Käufer, Inhaber, Einlösungen und Änderungen. **Sperren** verhindert weitere
Nutzungen. **Gültigkeit speichern** ändert allein das Ablaufdatum; die optionale Reaktivierung
steht bei gesperrten oder verfallenen Codes bereit. Verbrauch und Restwert bleiben erhalten,
auch wenn der Code bereits vollständig verbraucht ist. Zurückgegebene Kaufgutscheine können
niemals reaktiviert werden. Bei einer gleichzeitig geänderten Zuordnung wird die Aktion
vollständig zurückgerollt und fordert zum erneuten Ausführen auf.

**Ungenutzten Kaufgutschein zurückgeben:** Auf der Detailseite als Admin einen Grund angeben
und **Zurückgeben – Betrag anschließend manuell erstatten** wählen. Reservierte oder bestätigte
Einlösungen verhindern die Rückgabe. Bei Wertgutscheinen muss der gesamte ursprüngliche
Nennwert unverbraucht sein; eine frühere Wertgutschrift stellt den Gutscheinrest nicht wieder her.
Kostenfrei zurückgenommene Einheiten und abgebrochene Gutscheinreservierungen sind wieder
ungenutzt. Das System prüft Bestand und Kaufbasis gemeinsam, erzeugt den Kaufgegenbeleg und
sperrt den Gutschein dauerhaft. Bei Mehrzweckgutscheinen wird nur der steuerfreie Kaufbeleg
storniert. Betrag und Grund erscheinen am Gutschein und im Änderungsprotokoll.

Den angezeigten Betrag anschließend **manuell an den ursprünglichen Käufer** erstatten,
auch wenn inzwischen jemand anderes Inhaber ist. Diese Aktion führt keinen Banktransfer aus
und erzeugt kein Kundenguthaben. Ein erneuter Klick erzeugt keinen weiteren Gegenbeleg oder
Erstattungsauftrag. Ein bereits vollständig korrigierter Kaufbeleg wird nicht erneut korrigiert;
das System zeigt dann keine weitere Erstattung an. Teilweise korrigierte Kaufbasis muss zunächst
geklärt werden. Rückgegebene Gutscheine können dauerhaft nicht reaktiviert werden.
Bei gleichzeitig wechselnder Zuordnung wird die Rückgabe vollständig neu vorbereitet;
anhaltende Änderungen ergeben einen Fehler ohne Teilwirkung.

Allgemeine Aktionen **Als bezahlt markieren**, **Stornorechnung erzeugen** und **Teil-Storno**
sind für Gutschein-Kauf-/Einlösungs-/Geldanteilbelege und deren Gegenbelege gesperrt.
Die Rechnungsseite verweist auf die Gutscheinrückgabe oder den Buchungsstorno. Dort werden
Gutscheinbestand, Geldanteil und Belege gemeinsam geprüft. Normale Rechnungen behalten die
vorhandenen Aktionen.

Das Ablaufdatum gilt einschließlich. Täglich um **00:20 Europe/Berlin** setzt der Nachtjob
noch aktive oder teilweise eingelöste, abgelaufene Codes auf verfallen und aktualisiert die
betroffenen Kontodokumente. Er erzeugt keine Rechnung und kein Guthaben.
Die Einlösung ist im Hauptsystem-Kanal implementiert. Einheiten decken chronologisch die
im Code gespeicherten Minuten; ein angeschnittener Slot wird zeitproportional bewertet.
Tarifwert, Gutscheindeckung und Rest verwenden denselben Preisstand der Buchungsanlage,
auch wenn währenddessen neue Tarife gespeichert werden.
Erst danach werden Wertgutscheine und das Guthaben des Einlösers gegen den Rest verrechnet.
Die Buchungsbestätigung zeigt Tarifwert, Gutscheindeckung und Geld-/Guthabenanteil getrennt.
Ein Mehrzweck-Einheitsgutschein über 26 € erzeugt bei Einlösung einen Beleg über 26 €,
auch wenn er 30 € Tarifwert deckt. Ein Geldrest erhält eine eigene Rechnung.
Der Kauf, signierte Gutscheinbestand und die manuelle Kaufgutscheinrückgabe sind implementiert;
Verkaufs- und Codeeingabeoberfläche im Portal folgen in Stufe 2.
Ein Kauf benötigt ein verknüpftes Portalkonto. Rechnungskunden dürfen kaufen, solange
**Rechnungskunden dürfen Gutscheine kaufen** eingeschaltet ist; bezahlt wird ebenfalls online.
Die Portalbindung wird nach der Kundensperre frisch gegen das anfragende Konto geprüft.
Wird der Kunde während des Wartens auf ein anderes Konto umgehängt, erhält das alte Konto
keinen Kauf oder Checkout; es entsteht auch keine Bezahlsitzung beim Anbieter.
Einheitspreis **0** deaktiviert den Verkauf für die betreffende Käufergruppe. Wertgutscheine
sind nur zu den angebotenen Beträgen erhältlich. Vorhandenes Guthaben bezahlt keinen Gutscheinkauf.
In **Einstellungen → Gutscheine** sind Modell (`einheit`/`wert`), Steuerzeitpunkt
(`beim_kauf`/`bei_einloesung`), Minuten, Preise/Beträge, Mitgliedsvoraussetzung und Gültigkeit
zu prüfen. Einstellungen ändern nur neue Kaufwünsche; vorhandene Gutscheine behalten ihre
Snapshots. Die Vorgaben sind 120 Minuten, 730 Tage und Steuer beim Kauf; Einheitspreise sind
zunächst 0. Freischaltcodes bleiben davon unabhängig Einheiten ohne Geldwert und Steuer.

Kauf, Einlösung, Storno und Rückgabe sperren zuerst sämtliche beteiligten Kunden
(Akteur, Käufer, bisheriger Inhaber, Einlöser/Guthabenempfänger), anschließend vorhandene
Zahlungen und Gutscheine, jeweils nach UUID sortiert. Erst danach folgen weitere Buchungs-,
Feld- und Belegsperren. Ändert sich eine Zuordnung während des Wartens, wird der Vorgang
vollständig zurückgerollt und neu vorbereitet; es gibt keine teilweise Erstattung.

Ein Kaufwunsch speichert Preis, Käufergruppe/Steuersatz, Modell, Minuten/Wert,
Mitgliedsvoraussetzung, Gültigkeitsdauer und Zahlungsfrist. Spätere Einstellungs- oder
Mitgliedschaftsänderungen verändern diese Kaufbedingungen nicht. Die Gültigkeit beginnt erst
am tatsächlichen Kaufabschluss. Solange der Kauf offen oder abgebrochen ist, wird kein Code
angezeigt oder versandt, auch nicht über das Änderungsprotokoll.
Der eigene Kaufabbruchjob läuft jede Minute, unabhängig vom Verfall der Buchungsreservierungen.
Die Frist gilt bereits auf der Grenze als abgelaufen; der Callback prüft sie selbst.
Bei Unterzahlung wird der Kauf abgebrochen und der tatsächlich eingegangene Betrag einmal
als Käuferguthaben gebucht. Bei Überzahlung wird ein Gutschein aktiviert und nur der Überschuss
gutgeschrieben. Geld nach Abbruch/Fristablauf wird einmal Käuferguthaben, niemals ein Gutschein.
Diese Fälle melden dem Betreiber nach Commit den tatsächlichen Eingang und das Guthaben.
Ein erneuter Kauf benötigt eine neue Anfrage; eine doppelte Zahlungsrückmeldung ändert nichts.
Der signierte Lesestand zeigt offene Käufe ausschließlich dem Käufer, mit Zahlungslink und
gespeicherter Frist, ohne Code. Nach Fristablauf verschwinden sie auch vor dem nächsten Job.
Eigener Bestand enthält ausschließlich aktuell einlösbare Gutscheine des Inhabers und wird
nach Besitzwechsel auch für bisherigen Inhaber und Käufer aktualisiert. Kaufbelegrechte
bleiben beim Käufer, selbst wenn ein anderer Kunde den Gutschein einlöst.

Bei Steuer **beim Kauf** erhält der Käufer die normale Kaufrechnung mit seinem eingefrorenen
Steuersatz. Bei Steuer **bei Einlösung** erhält er einen **Kaufbeleg GB-JJJJ-NNNNN** ohne
Umsatzsteuerausweis und mit Mehrzweckhinweis. PDFs und Mails entstehen erst nach Commit.
Käufer und optionaler Geschenkempfänger erhalten den Code mit Gruppen- und Gültigkeitshinweis;
der Kaufbeleg geht ausschließlich an den Käufer. Ein Mehrzweck-Mitgliedsgutschein nennt die
Käufergruppe informativ und ist für alle einlösbar. Ein Fehler eines PDFs oder einer Code-Mail
verhindert die anderen Nachläufe nicht. SMTP-Zustellung wird dadurch nicht garantiert oder
automatisch erneut versucht; fehlgeschlagene Nachläufe sind im Log bzw. Betreiberhinweis zu prüfen.

Bei kostenfreiem Storno oder nachträglicher Kulanz gibt das Hauptsystem Einheiten und
Freischaltcodes an den Einlöser zurück. Das ursprüngliche Ablaufdatum bleibt unverändert;
ein bereits gesperrter oder verfallener Code bleibt nicht einlösbar. Mehrzweck-Einheiten
bekommen einen steuerlichen Gegenbeleg, aber kein Geldguthaben aus dem Gutschein.
Bei Wertgutscheinen erhält der Einlöser stattdessen den verbrauchten Wert als Guthaben mit
Korrekturbeleg; der Gutscheinrest steigt nicht. Ein 50-€-Wertgutschein mit 30 € Verbrauch
behält beispielsweise 20 € Rest, der Einlöser erhält 30 € Guthaben. Der Käufer erhält kein
Guthaben aus fremder Einlösung. Sein Kaufgegenbeleg wird nur ihm zugestellt.
Der separat bezahlte Geld-/Guthabenrest wird zusätzlich nach den normalen Finanzregeln
korrigiert. Beim 26-€-Einheitsgutschein mit 15 € Geldrest entstehen genau 15 € Guthaben.
Kostenpflichtige Absagen lassen den Gutscheinverbrauch bestehen. Wiederholung einer bereits
vollständigen Rückabwicklung erzeugt kein neues Guthaben und keinen neuen Beleg.
Reservierungen ohne Bestätigung geben bei Abbruch oder Verfall den ursprünglichen Bestand
zurück, einschließlich Wertgutscheinrest, ohne Wertgutschein-Gutschrift. Das zuvor reservierte
Kundenguthaben wird ebenfalls genau einmal zurückgebucht, auch bei kostenfreiem Betreiberstorno,
Sperranlage und verdrängender Daueranlage. Bei 5 € Guthaben und noch offenen 10 € Checkout
stehen die 5 € nach dem Abbruch wieder zur Verfügung; eine spätere Zahlung fügt nur ihre
tatsächlichen 10 € hinzu. Ein Zahlungseingang
nach der gespeicherten Reservierungsfrist bestätigt nichts mehr, auch vor dem Minutenjob;
der Geldbetrag wird genau einmal als Spätzahlungsguthaben verbucht. Genau auf der Frist gilt
wie im Verfallsjob noch kein Verfall. Nach dem Commit werden sämtliche Gegenbelege und
zutreffende Einlöserhinweise versandt; der Fehler eines Belegs verhindert Folgebelege nicht.

## 6. Backup und Wiederherstellung

Das Skript `core/deploy/backup.sh` erstellt ein verschlüsseltes Backup aus Datenbank-Dump und dem
Verzeichnis `data/` (Rechnungs-PDFs, Lesestand-Exporte, Signaturschlüssel) und überträgt es per
`scp` an ein Backup-Ziel. Es benötigt die Umgebungsvariablen `BACKUP_ZIEL` (z. B.
`user@backup-host:/srv/beachhub`) und `BACKUP_GPG` (GPG-Key-ID des Betreibers, mit der verschlüsselt
wird). Standardmäßig wird der Dump per `docker compose exec -T db pg_dump -U beachhub beachhub`
erzeugt (Postgres läuft in Docker, siehe `docker-compose.yml`). Läuft Postgres stattdessen als
Host-Installation, kann der Befehl per `PG_DUMP_CMD` überschrieben werden, z. B.:

```bash
PG_DUMP_CMD="pg_dump -U beachhub beachhub" BACKUP_ZIEL=user@backup-host:/srv/beachhub BACKUP_GPG=<key-id> ./deploy/backup.sh
```

Vor der ersten produktiven Nutzung das Skript einmal **manuell** ausführen und die übertragene
Datei auf dem Backup-Host prüfen, bevor der Cron-Job aktiviert wird:

```bash
cd /opt/beachhub/core
BACKUP_ZIEL=user@backup-host:/srv/beachhub BACKUP_GPG=<key-id> ./deploy/backup.sh
```

Cron-Eintrag für ein tägliches Backup um 03:15 Uhr:

```
15 3 * * * cd /opt/beachhub/core && BACKUP_ZIEL=user@backup-host:/srv/beachhub BACKUP_GPG=<key-id> ./deploy/backup.sh >> /var/log/beachhub-backup.log 2>&1
```

### Wiederherstellung (Restore)

1. App stoppen, damit während des Restores keine neuen Buchungen/Änderungen in die Datenbank
   geschrieben werden:

   ```bash
   docker compose stop app
   ```

2. Backup-Datei entschlüsseln und entpacken:

   ```bash
   gpg --decrypt beachhub-<stamp>.tar.gpg > beachhub-<stamp>.tar
   tar xf beachhub-<stamp>.tar
   ```

   Das ergibt `db-<stamp>.sql.gz` und `data-<stamp>.tgz`.

3. Zieldatenbank leeren, bevor der Dump eingespielt wird:
   - **Frische/leere Zieldatenbank** (z. B. Testlauf, siehe unten): Schritt 4 kann direkt erfolgen.
   - **Bereits befüllte Zieldatenbank** (echter Wiederherstellungsfall auf derselben VM): entweder
     den kompletten `db`-Container samt Datenvolumen verwerfen,

     ```bash
     docker compose down
     docker volume rm core_pgdata
     docker compose up -d db
     ```

     oder bei einer host-installierten PostgreSQL die Datenbank neu anlegen:

     ```bash
     dropdb -U beachhub beachhub && createdb -U beachhub -O beachhub beachhub
     ```

     Alternativ kann ein Dump mit `pg_dump --clean` erzeugt werden (im Skript die pg_dump-Zeile
     entsprechend anpassen); ein damit erzeugter Dump löscht beim Einspielen selbst zuerst die
     vorhandenen Objekte und ein vorheriges Leeren der Datenbank entfällt.

4. Dump einspielen:

   ```bash
   gunzip -c db-<stamp>.sql.gz | docker compose exec -T db psql -U beachhub beachhub
   ```

5. Das Verzeichnis `data/` (Rechnungs-PDFs, `signatur.key`) aus `data-<stamp>.tgz` an die
   ursprüngliche Stelle zurückkopieren, z. B.:

   ```bash
   tar xzf data-<stamp>.tgz -C /opt/beachhub/core
   ```

6. Anwendung neu starten (`docker compose up -d`) und im Admin-UI stichprobenartig prüfen
   (Belegungsplan, letzte Rechnung, Lesestand-Signatur). Caddy kann in den ersten Sekunden nach
   `docker compose up -d` mit `502 Bad Gateway` antworten, solange uvicorn im `app`-Container noch
   hochfährt – das ist normal und verschwindet nach wenigen Sekunden von selbst.

**Hinweis für Portal und Halle:** Das Docker-Volume `caddy_data` (Caddys eigene interne Root-CA)
ist **nicht** Teil dieses Backups – nach einer Wiederherstellung auf einer neuen VM (leeres
`caddy_data`) legt Caddy beim ersten Start automatisch eine neue Root-CA an. Portal und
Hallendienst vertrauen noch der alten und müssen die neue Root-CA erneut erhalten
(`docs/betrieb/portal.md` bzw. `docs/betrieb/hallendienst.md`, jeweils Abschnitt 2).

**Vor Saisonstart** sollte ein vollständiger Restore-Testlauf gegen eine separate Testdatenbank
durchgeführt werden, um sicherzustellen, dass Backup und Wiederherstellung tatsächlich
funktionieren (siehe Abnahmekriterium N-10 der Spezifikation).

## 7. Updates

```bash
cd /opt/beachhub
git pull
cd core
docker compose build
docker compose run --rm app alembic upgrade head
docker compose up -d
```

Vor größeren Updates (insbesondere bei neuen Alembic-Migrationen) empfiehlt sich ein Backup gemäß
Abschnitt 6.

Die vorbereitende Gutscheinmigration `0015` erhält die bisherigen Rechnungsnummern und
Jahreszähler. Sie führt einen zusätzlichen Nummernkreis für Kaufbelege ohne Steuer ein
(`GB-JJJJ-NNNNN`). Datenbasis, Codevergabe und interne Vorgangssperren sind vorbereitet;
die Buchungs- und Batch-Einstiege berücksichtigen diese Sperrreihenfolge bereits.
Die atomare Kanaleinlösung und getrennte Belege sind implementiert; Kauf, Rückabwicklung
und die Portalbedienung sind noch keine vollständigen Betriebsabläufe. Ein Downgrade auf `0014` wird bei vorhandenem
Gutscheinbestand, Gutscheinbelegen oder bereits verbrauchten Gutschein-Nummernkreisen
mit einer klaren Fehlermeldung abgewiesen; diese Daten werden dabei nicht gelöscht.

## 8. Störungen

Die Compose-Services `db`, `app` und `caddy` sind mit `restart: unless-stopped` konfiguriert und
starten nach einem VM-Reboot oder einem Absturz automatisch neu, sobald der Docker-Daemon läuft.

**Startreihenfolge nach einem Reboot**: Caddy lauscht ausschließlich auf der WireGuard-Adresse
(`10.8.0.1:8443` und `10.8.0.1:8444`, je mit `bind 10.8.0.1` in `core/deploy/Caddyfile`, zusammen
mit `network_mode: host`; ohne dieses `bind` würde Caddy auf allen Interfaces lauschen, die
Adresse in der Site dient sonst nur dem Routing/der Zertifikatsauswahl, nicht der Bindung).
Startet Docker vor WireGuard, versucht Caddy auf eine zu diesem Zeitpunkt noch nicht existierende
Adresse zu binden und schlägt fehl. Damit
`wg-quick@wg0` vor Docker aktiv ist, zusätzlich zu `systemctl enable wg-quick@wg0`
(siehe `core/deploy/wireguard-beispiel.md`) eine systemd-Drop-in-Datei für den Docker-Dienst
anlegen:

```bash
sudo mkdir -p /etc/systemd/system/docker.service.d
sudo tee /etc/systemd/system/docker.service.d/override.conf <<'EOF'
[Unit]
After=wg-quick@wg0.service
Requires=wg-quick@wg0.service
EOF
sudo systemctl daemon-reload
```

- **App startet nicht, Meldung zu unsicheren Standardwerten**: `SECRET_KEY` und/oder
  `PIN_SCHLUESSEL` stehen in `.env` noch auf `change-me`. Bei `APP_ENV=production` verweigert die
  Anwendung bewusst den Start. Abhilfe: beide Werte wie in Abschnitt 2 beschrieben neu erzeugen und
  in `.env` eintragen, danach `docker compose up -d` erneut ausführen.
- **Schlüsseldatei fehlt** (`data/signatur.key` nicht vorhanden, Fehler beim Signieren des
  Lesestands): `beachhub-core keygen` wurde nie ausgeführt oder die Datei ist beim Restore nicht
  mitgekommen. Abhilfe: `docker compose run --rm app beachhub-core keygen` ausführen (nur möglich,
  wenn die Datei wirklich fehlt – ist sie vorhanden, bricht der Befehl bewusst mit
  `FileExistsError` ab, um vorhandene Schlüssel nicht zu überschreiben) oder aus dem letzten
  Backup wiederherstellen (Abschnitt 6).
- **Postgres voll** (Meldung „no space left on device“ oder ähnliche Fehler beim Schreiben):
  Plattenbelegung der VM prüfen (`df -h`), alte Docker-Images/Volumes aufräumen
  (`docker system df`, `docker system prune`), ggf. Volume vergrößern. Nach Platzschaffung
  `docker compose restart db app`.
- **Mail geht nicht raus** (Bestätigungs-, Storno- oder Rechnungsmails kommen nicht an): Die
  `SMTP_*`-Werte in `.env` prüfen (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`,
  `EMAIL_FROM`). Anwendungslogs auf SMTP-Fehler prüfen (`docker compose logs -f app`). Ist
  `SMTP_HOST` leer, werden Mails nur geloggt und nicht tatsächlich versendet – für den
  Produktivbetrieb muss ein echter SMTP-Zugang eingetragen sein.
- **Caddy startet nicht: WireGuard-Interface fehlt** (meist nach einem Reboot, wenn die
  systemd-Abhängigkeit aus diesem Abschnitt fehlt oder `wg-quick@wg0` selbst nicht hochkam):
  `systemctl status wg-quick@wg0` prüfen und bei Bedarf starten (`systemctl start wg-quick@wg0`),
  danach `docker compose restart caddy`.
- **Caddy startet nicht: `ca.crt` ist ein Verzeichnis** (`docker compose logs caddy` zeigt einen
  Fehler beim Laden von `/etc/caddy/beachhub-ca.crt`, z. B. „is a directory“): `beachhub-core
  zertifikate` wurde vor dem allerersten `docker compose up -d` nicht ausgeführt (Abschnitt 2);
  Docker hat beim Start selbst ein leeres Verzeichnis an dieser Stelle angelegt. Abhilfe:

  ```bash
  docker compose down
  rmdir data/zertifikate/ca.crt   # nur falls Docker dort ein Verzeichnis angelegt hat
  docker compose up -d db
  docker compose run --rm app beachhub-core zertifikate --ziel /app/data/zertifikate
  docker compose up -d
  ```

### Gutscheinbestand im signierten Lesestand

Der aktuelle Inhaber sieht nur aktive, heute gültige Codes mit Restwert oder verbleibenden Nutzungen.
Ein abgelaufener Code verschwindet beim Erzeugen auch dann, wenn der Nachtjob noch nicht gelaufen
ist. Bei Einlösung wechselt der Bestand zum Einlöser; der frühere Inhaber sieht den Code anschließend
nicht mehr. Der Käufer behält ausschließlich seine eigenen Kaufbelege und offenen Kaufwünsche.
Offene Käufe stehen nur bis zur gespeicherten Zahlungsfrist beim Käufer, mit Zahlungslink und ohne
Code. Eine spätere Änderung der Zahlungsfrist verschiebt bestehende Käufe nicht. Änderungen am
Angebot oder der Kaufberechtigung veröffentlichen Tarif- und betroffene Kontodokumente neu.
Der signierte Bestand schafft noch keine Verkaufs- oder Codeeingabeoberfläche im Portal.
