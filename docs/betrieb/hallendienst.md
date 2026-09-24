# Hallendienst – Betrieb

Der Hallendienst läuft auf dem Rechner in der Halle neben Home Assistant (HA). Er holt alle
5 Minuten den Plan vom Hauptsystem, schaltet Licht und Heizung, prüft PINs am Tastenfeld und
meldet Ereignisse zurück. Fällt das Internet aus, arbeitet er mit dem gespeicherten Plan weiter
(bis zu 7 Tage) und liefert die Ereignisse nach.

## 1. Voraussetzungen

- Home Assistant läuft auf demselben Rechner oder im selben Netz.
- WireGuard-Tunnel zum Hauptsystem (Beispiel: `core/deploy/wireguard-beispiel.md`); der
  Hallendienst ist Client mit `PersistentKeepalive = 25`.
- Docker (bei HA OS: ein Host mit Container-Unterstützung neben HA).
- Läuft `docker compose build` auf einem anderen Rechner als die Halle (z. B. amd64-Entwicklungs-
  rechner, arm64-Hallenrechner), das Image vorher per `docker buildx build --platform
  linux/arm64,linux/amd64 -f hall/Dockerfile .` für beide Architekturen bauen und veröffentlichen;
  ein `docker compose build` direkt auf dem Zielrechner baut ohnehin für dessen Architektur.

## 2. Zertifikate, Token, Schlüssel

Auf dem Hauptsystem:

1. `docker compose -f core/docker-compose.yml exec app beachhub-core zertifikate --ziel
   /app/data/zertifikate` ausführen (siehe `docs/betrieb/portal.md`) – das ist bei der
   Ersteinrichtung des Hauptsystems bereits geschehen (`docs/betrieb/hauptsystem.md`, Abschnitt 2,
   dort **vor** dem ersten `docker compose up -d`); ein erneuter Aufruf hier ist unschädlich
   (vorhandene Schlüssel bleiben erhalten, es werden nur fehlende Zertifikate nachgezogen). Der
   Befehl legt Ergebnisse **auf dem Datenvolume** ab (`/app/data/…`), nicht im beschreibbaren
   Container-Dateisystem – sonst gingen sie beim nächsten `docker compose build`/Neuanlegen des
   Containers verloren. Es entstehen dort u. a. `ca.crt`/`ca.key` (interne CA, gemeinsamer
   Vertrauensanker für beide mTLS-Kanäle des Hauptsystems: `client_auth`-`trust_pool` sowohl für
   Caddy des Portals auf `:8443` (`docs/betrieb/portal.md`, Abschnitt 2) als auch für Caddy des
   Hauptsystems auf der Hallenschnittstelle `:8444` (siehe `docs/betrieb/hauptsystem.md`
   „Hallendienst anbinden“) – `ca.crt` kommt deshalb nie auf den Hallenrechner, sie ist trust_pool
   der Caddy-Sites, nicht Ausweis eines Teilnehmers; `ca.key` verlässt das Hauptsystem ohnehin
   nie) sowie `halle.crt`/`halle.key` (Client-Zertifikat, mit dem sich die Halle bei Caddy
   ausweist – von derselben CA signiert wie `portal-kanal.crt`; die Trennung zwischen den Kanälen
   `:8443` (Portal) und `:8444` (Halle) übernehmen die unterschiedlichen Ports und die je Kanal
   eigenen Token, nicht die Zertifikate selbst).
2. **Root-Zertifikat von Caddy exportieren.** Die Hallenschnittstelle (Port 8444) nutzt
   `tls internal`, also Caddys **eigene**, interne Root-CA – eine andere CA als die `ca.crt` aus
   Schritt 1. Nur mit dieser Root-CA kann der Hallendienst das Server-Zertifikat von Caddy prüfen
   (Einstellung `CORE_CA`), das Caddy für den Hostnamen `kern.beachhub.wg` ausstellt (siehe unten,
   Schritt 3). `caddy_data` ist ein Named Volume, kein Bind-Mount, daher per `docker compose cp`
   exportieren:

   ```bash
   docker compose -f core/docker-compose.yml cp caddy:/data/caddy/pki/authorities/local/root.crt ./caddy-root.crt
   ```
3. **Hostnamen der Hallenschnittstelle eintragen.** Caddy hört auf `kern.beachhub.wg:8444`, nicht
   auf die nackte IP `10.8.0.1`: Für eine IP-Adresse schicken TLS-Clients kein SNI (RFC 6066), und
   ohne SNI kann Caddy seine `client_auth`-Richtlinie keiner Verbindung zuordnen – der Handshake
   schlägt dann für jeden Client fehl, mit oder ohne Zertifikat (Details:
   `docs/betrieb/hauptsystem.md`, Abschnitt „5b. Hallendienst anbinden“). `hall/.env.example`
   setzt bereits `CORE_URL=https://kern.beachhub.wg:8444`; in `hall/docker-compose.yml` löst
   `extra_hosts: ["kern.beachhub.wg:10.8.0.1"]` den Namen auf die WireGuard-Adresse des
   Hauptsystems auf – weicht diese ab, den Eintrag entsprechend anpassen.
4. In `core/.env` `HALL_TOKEN` auf einen langen Zufallswert setzen
   (`python -c "import secrets; print(secrets.token_urlsafe(32))"`) und das Hauptsystem neu starten.
5. Den öffentlichen Signaturschlüssel von der Seite **System** im Admin-UI kopieren.

Auf den Hallenrechner kopieren: `halle.crt`, `halle.key` (aus `core/data/zertifikate/`, Schritt 1)
sowie `caddy-root.crt` (aus Schritt 2) nach `hall/zertifikate/`. Die `ca.crt` aus Schritt 1 bleibt
auf dem Hauptsystem.

**Wichtig:** Diese drei Dateien müssen **vor dem ersten `docker compose up`** in
`hall/zertifikate/` liegen. `docker-compose.yml` bindet `./zertifikate` als Verzeichnis ein;
existiert eine der Dateien noch nicht, legt Docker beim Start selbst ein Verzeichnis mit diesem
Namen an, und der Hallendienst findet weder Client-Zertifikat noch Vertrauensanker für den Server.

**Rotation:**

- **Client-Zertifikat** (`halle.crt`) läuft nach einem Jahr ab. Schritt 1 erneut ausführen – die
  Schlüsseldatei (`halle.key`) bleibt dabei unverändert, nur `halle.crt` wird neu ausgestellt.
  Neues `halle.crt` auf den Hallenrechner kopieren (`halle.key` bleibt dort unverändert) und den
  Hallendienst neu starten (`docker compose restart hall`), sonst verbindet er sich weiter mit dem
  alten, demnächst abgelaufenen Zertifikat.
- **Schlüsselrotation** (z. B. bei Verdacht auf Kompromittierung): vor dem erneuten Aufruf von
  Schritt 1 auf dem Hauptsystem `halle.key` (und `halle.crt`) löschen – der Befehl legt dann ein
  neues Schlüsselpaar samt Zertifikat an; danach `halle.crt` **und** `halle.key` neu auf den
  Hallenrechner kopieren und den Hallendienst neu starten.
- **Wechsel der internen CA** (`ca.crt`/`ca.key` gemeinsam entfernt und Schritt 1 erneut
  ausgeführt – nicht jeder erneute Aufruf des Befehls, siehe Schritt 1): danach auf dem
  Hauptsystem die Caddy-Site `:8444` neu laden (`docker compose restart caddy`, lädt den neuen
  `trust_pool`) sowie `halle.crt`/`halle.key` (neue CA-Signatur) erneut auf den Hallenrechner
  kopieren – **nicht** `ca.crt` selbst, die bleibt auf dem Hauptsystem.
- **Caddys eigene interne Root-CA** (Quelle für `caddy-root.crt`, `CORE_CA`) ändert sich nur, wenn
  das Docker-Volume `caddy_data` neu angelegt wird, z. B. nach einer Wiederherstellung des
  Hauptsystems ohne dieses Volume (es ist nicht Teil des Backups, siehe
  `docs/betrieb/hauptsystem.md`, Abschnitt 6). Dann Schritt 2 wiederholen und `caddy-root.crt`
  erneut auf den Hallenrechner kopieren.

## 3. Home Assistant einrichten

1. **Benutzer und Token:** In HA einen eigenen Benutzer „beachhub“ anlegen und ihm
   **Administratorrechte** geben („Administrator“ beim Anlegen einschalten), mit ihm anmelden,
   unter *Profil → Sicherheit* einen Long-Lived Access Token erzeugen und als `HA_TOKEN`
   eintragen. Ohne Administratorrechte geht es nicht, weil HA zwei Dinge nur Administratoren
   erlaubt, die der Dienst braucht:
   - **Tastenfeld-Ereignis abonnieren:** Über den WebSocket (`subscribe_events`) darf ein
     Nicht-Administrator nur Ereignistypen aus HAs fester Freigabeliste abonnieren
     (`SUBSCRIBE_ALLOWLIST`, z. B. `state_changed`) – ein eigener Typ wie `esphome.beachhub_pin`
     wird mit „Unauthorized“ abgelehnt. Der Dienst schreibt dann ins Log
     „Tastenfeld abgeschaltet: … HA-Token braucht Administratorrechte …“; Licht, Heizung und
     Präsenz laufen weiter, aber **keine PIN öffnet die Tür**.
   - **Status-Sensoren schreiben:** `POST /api/states/<entity_id>` (für `sensor.beachhub_*`)
     beantwortet HA für Nicht-Administratoren mit HTTP 401; im Log steht dann
     „Status nach HA nicht geschrieben: … HTTP 401 – HA-Token ungültig oder ohne
     Administratorrechte“.

   Der Token hat damit volle Rechte über HA: nur in `hall/.env` eintragen (Dateirechte `600`),
   nicht in Tickets, Chats oder Screenshots weitergeben, und bei Verdacht auf Weitergabe unter
   *Profil → Sicherheit* des Benutzers „beachhub“ widerrufen und neu erzeugen. Mit dem Benutzer
   „beachhub“ niemanden interaktiv arbeiten lassen.
2. **Handbetrieb-Schalter:** *Einstellungen → Geräte & Dienste → Helfer → Schalter* mit dem Namen
   „beachhub_handbetrieb“ anlegen (`input_boolean.beachhub_handbetrieb`). Ist er an, schaltet der
   Dienst weder Licht noch Heizung; die Tür öffnet weiterhin mit gültiger PIN.
3. **Recorder muss das Tastenfeld-Ereignis ausschließen.** Das ist keine Empfehlung, sondern
   Pflicht: Ohne diesen Ausschluss speichert HA jedes Tastenfeld-Ereignis mitsamt der eingegebenen
   PIN im Klartext in seiner Datenbank (`home-assistant_v2.db`) und damit dauerhaft, unabhängig
   davon, was der Hallendienst selbst protokolliert (Global Constraints: „Eine eingegebene PIN wird
   nie gespeichert, geloggt oder als Ereignis gemeldet“ – das gilt auch für HA). In
   `configuration.yaml`:

   ```yaml
   recorder:
     exclude:
       event_types:
         - esphome.beachhub_pin
   ```

   Nach einer Änderung des Ereignistyps in `hall.toml` (`[tastenfeld] ereignis = …`) diesen Eintrag
   entsprechend anpassen.
4. **Tastenfeld:** Das Tastenfeld muss nach der Eingabe ein HA-Ereignis auslösen und den Code
   **als Text** senden – als Zahl gingen führende Nullen verloren (z. B. würde aus `"0123"` die
   Zahl `123`, und die PIN stimmte nicht mehr mit dem gespeicherten Hash überein). Beispiel mit
   ESPHome (die tatsächliche Hardware klärt der Hallenhersteller):

   ```yaml
   matrix_keypad:
     id: tastenfeld
     rows: [{pin: GPIO21}, {pin: GPIO19}, {pin: GPIO18}, {pin: GPIO5}]
     columns: [{pin: GPIO17}, {pin: GPIO16}, {pin: GPIO4}]
     keys: "123456789*0#"
   key_collector:
     - id: pin_eingabe
       source_id: tastenfeld
       min_length: 4
       max_length: 12
       end_keys: "#"
       clear_keys: "*"
       timeout: 10s
       on_result:
         - homeassistant.event:
             event: esphome.beachhub_pin
             data:
               code: !lambda 'return x;'
   ```

   `x` ist in ESPHome bereits ein `std::string` (Text), `!lambda 'return x;'` sendet die PIN also
   unverändert als Text mit führenden Nullen. Im ESPHome-Gerät in HA muss außerdem „Gerät darf
   Home-Assistant-Aktionen ausführen“ eingeschaltet sein.
5. **Dashboard:** Eine Karte mit `sensor.beachhub_planversion`, `sensor.beachhub_letzter_kontakt`,
   `binary_sensor.beachhub_verbunden`, `sensor.beachhub_warteschlange` und dem Schalter
   `input_boolean.beachhub_handbetrieb`. Die Sensoren schreibt der Dienst selbst; nach einem
   HA-Neustart erscheinen sie innerhalb einer Minute wieder.
6. **Rückfall-Automation** (falls der Dienst ausfällt, Hauptspec § 8.2): Licht um 23:30 aus,
   wenn kein Handbetrieb läuft.

   ```yaml
   alias: Beachhub Rückfall – Licht aus nach Betriebsschluss
   mode: single
   triggers:
     - trigger: time
       at: "23:30:00"
   conditions:
     - condition: state
       entity_id: input_boolean.beachhub_handbetrieb
       state: "off"
   actions:
     - action: light.turn_off
       target:
         entity_id: [light.feld_1, light.feld_2, light.feld_3]
   ```

   **Wichtig für die Heizung:** Die `grund_temperatur` der Hauptsystem-Konfiguration steht auf
   `0,0 °C` (A-HALLE-1). Der Hallendienst begrenzt jeden Sollwert, den er an HA schickt, auf
   `min_temp`/`max_temp` der `climate`-Entität (falls diese Attribute vorhanden sind) – schickt er
   `0 °C` an ein Gerät mit `min_temp: 5`, kommt effektiv `5 °C` an, nicht „aus“. Damit `0 °C`
   tatsächlich den gewünschten Frostschutz bedeutet, muss `min_temp` der `climate`-Entität in HA
   genau diesem Frostschutzwert entsprechen (bei `generic_thermostat` z. B. über `min_temp:` in der
   `climate`-Konfiguration einstellbar). Ein `min_temp` unterhalb des gewünschten Frostschutzes
   hielte die Heizung dauerhaft kälter als beabsichtigt.

## 4. Installation

```bash
cd hall
cp .env.example .env            # Werte aus Abschnitt 2 und 3 eintragen
cp hall.toml.example hall.toml  # Feld-UUIDs und Entitäten eintragen
mkdir -p zertifikate            # halle.crt, halle.key, caddy-root.crt aus Abschnitt 2 hierher kopieren
mkdir -p data && sudo chown 1000 data
docker compose run --rm hall beachhub-hall master-pin   # Hash in hall.toml eintragen
docker compose up -d --build
docker compose logs -f hall
```

Der Master-PIN wird **nie im Klartext** in `hall.toml` eingetragen, sondern ausschließlich als
Argon2id-Hash über `beachhub-hall master-pin` (fragt die PIN zweimal interaktiv ab und gibt den
Hash aus). Er muss aus **8 bis 12 Ziffern** bestehen – er öffnet die Tür jederzeit, auch ohne
Buchung, und muss deshalb deutlich schwerer zu erraten sein als eine Buchungs-PIN. Mit dem
Platzhalter `$argon2id$ERSETZEN` aus `hall.toml.example` startet der Dienst absichtlich nicht.

`GET http://127.0.0.1:8099/health` zeigt Planversion, HA-Verbindung und Länge der
Warteschlange. Der Endpunkt bindet über die Einstellung `HEALTH_HOST` standardmäßig nur an
`127.0.0.1` (Vorgabe in `.env.example`); der `HEALTHCHECK` im Dockerfile prüft ebenfalls immer
`127.0.0.1` und funktioniert damit unverändert. Den Port nimmt er wie der Dienst aus
`HEALTH_PORT` (Vorgabe 8099). `HEALTH_HOST` muss nur auf `0.0.0.0` gesetzt
werden, wenn der Health-Endpunkt von außerhalb des Containers direkt (nicht über
`docker compose exec`/`docker inspect`) erreichbar sein soll, z. B. für ein externes
Monitoring-System auf einem anderen Rechner – dann ist der Port nach außen zu begrenzen
(Firewall), da der Endpunkt ungeschützt ist.

## 5. Probelauf im eigenen Home Assistant

Ohne echte Hallentechnik lässt sich der Dienst gegen Test-Entitäten laufen lassen. In
`configuration.yaml` des Test-HA:

```yaml
input_boolean:
  demo_feld_1_licht: { name: Demo Feld 1 Licht }
  demo_praesenz_feld_1: { name: Demo Präsenz Feld 1 }
  demo_heizung: { name: Demo Heizung }
  demo_tuer: { name: Demo Türöffner }
  beachhub_handbetrieb: { name: Beachhub Handbetrieb }
input_number:
  demo_hallentemperatur: { name: Demo Hallentemperatur, min: -10, max: 30, step: 0.5 }

template:
  - light:
      - name: Demo Feld 1
        unique_id: demo_feld_1
        state: "{{ is_state('input_boolean.demo_feld_1_licht', 'on') }}"
        turn_on: { action: input_boolean.turn_on, target: { entity_id: input_boolean.demo_feld_1_licht } }
        turn_off: { action: input_boolean.turn_off, target: { entity_id: input_boolean.demo_feld_1_licht } }
    switch:
      - name: Demo Tueroeffner
        unique_id: demo_tueroeffner
        state: "{{ is_state('input_boolean.demo_tuer', 'on') }}"
        turn_on: { action: input_boolean.turn_on, target: { entity_id: input_boolean.demo_tuer } }
        turn_off: { action: input_boolean.turn_off, target: { entity_id: input_boolean.demo_tuer } }
    sensor:
      - name: Demo Hallentemperatur
        unique_id: demo_hallentemperatur
        unit_of_measurement: "°C"
        state: "{{ states('input_number.demo_hallentemperatur') }}"
    binary_sensor:
      - name: Demo Praesenz Feld 1
        unique_id: demo_praesenz_feld_1
        state: "{{ is_state('input_boolean.demo_praesenz_feld_1', 'on') }}"

climate:
  - platform: generic_thermostat
    name: Demo Halle
    heater: input_boolean.demo_heizung
    target_sensor: sensor.demo_hallentemperatur
    min_temp: 0
    max_temp: 25
```

Das ist die Template-Syntax ab HA 2025. Ältere Versionen schreiben Template-Lights und
-Switches als `light: - platform: template` bzw. `switch: - platform: template`. Auch hier gilt
Abschnitt 3, Punkt 3: Für dieses Test-HA ebenfalls `recorder: exclude: event_types:
[esphome.beachhub_pin]` setzen, bevor PINs simuliert werden.

Passende `hall.toml` (Feld-UUID aus dem eigenen Hauptsystem):

```toml
master_pin_hash = "…"   # beachhub-hall master-pin
[felder."<uuid-feld-1>"]
licht = "light.demo_feld_1"
praesenz = "binary_sensor.demo_praesenz_feld_1"
[heizung]
entity = "climate.demo_halle"
[tuer]
entity = "switch.demo_tueroeffner"
impuls_sekunden = 5
```

PIN-Eingaben lassen sich unter *Entwicklerwerkzeuge → Ereignisse* simulieren: Ereignistyp
`esphome.beachhub_pin`, Daten `code: "123456"` (als Text, siehe Abschnitt 3, Punkt 4).

Der Hallendienst selbst wird in diesem Probelauf genauso über `docker compose up -d --build`
gestartet wie im echten Betrieb (Abschnitt 4) – es gibt keinen separaten Testmodus. Die
automatisierten Tests in `hall/tests/` (u. a. `test_beispielkonfiguration.py`) prüfen die
Beispieldateien dieses Dokuments bereits ohne echtes HA und ohne echtes Hauptsystem.

## 6. Störungen

| Anzeige | Bedeutung | Was tun |
|---|---|---|
| `binary_sensor.beachhub_verbunden` aus | Hauptsystem seit über 10 min nicht erreicht | WireGuard prüfen; die Halle arbeitet mit dem gespeicherten Plan weiter |
| Mail „Halle ohne Kontakt“ | Hauptsystem hört seit 60 min nichts | wie oben; neue Buchungen kennt die Halle erst nach der Rückkehr |
| Mail „Gerät in der Halle reagiert nicht“ | `aktor_fehler`: Dienstaufruf scheitert oder Gerät schaltet nicht | Entität in HA prüfen, Zuordnung in `hall.toml` prüfen |
| Mail „Home Assistant nicht erreichbar“ | Dienst erreicht HA seit 2 min nicht | HA-Status, `HA_URL` und `HA_TOKEN` prüfen |
| Tastenfeld öffnet nie; Log „Tastenfeld abgeschaltet: … Administratorrechte“ oder „Status nach HA nicht geschrieben: … HTTP 401“ | HA-Benutzer „beachhub“ ist kein Administrator (oder Token widerrufen) | Benutzer in HA zum Administrator machen (Abschnitt 3, Punkt 1), danach `docker compose restart hall` |
| Mail „Halle hat den Plan verworfen“ | Signatur oder Version passt nicht | `CORE_PUBLIC_KEY` mit der System-Seite vergleichen |
| Dienst startet nicht: „master_pin_hash fehlt“ | Platzhalter in `hall.toml` | `beachhub-hall master-pin` ausführen und eintragen |
| Dienst startet nicht: Zertifikatsfehler | `halle.crt`/`halle.key`/`caddy-root.crt` fehlen oder Docker hat leere Verzeichnisse an ihrer Stelle angelegt | `docker compose down`, `hall/zertifikate/` prüfen (Abschnitt 2), danach `docker compose up -d` |
| Hauptsystem dauerhaft „nicht erreichbar“, obwohl WireGuard steht | `kern.beachhub.wg` löst nicht auf die WireGuard-Adresse des Hauptsystems auf (`extra_hosts` in `hall/docker-compose.yml` fehlt/falsch) oder `CORE_URL` weicht vom Hostnamen aus dem Caddyfile ab | `docker compose exec hall getent hosts kern.beachhub.wg` prüfen; `extra_hosts`/`CORE_URL` korrigieren (Abschnitt 2, Schritt 3), danach `docker compose up -d` |
| `docker stop`/`docker compose down` dauert ungewöhnlich lange oder Tür bleibt bei `switch.*`-Türöffnern offen | `stop_grace_period` in `docker-compose.yml` zu knapp für das geordnete Herunterfahren | `stop_grace_period` erhöhen; der Dienst fängt `SIGTERM` ab und schaltet einen offenen Türöffner beim Herunterfahren aus, braucht dafür aber Zeit |
