# Hallendienst (Stufe 3) – Design

Stand: 2026-09-23 · Status: abgestimmt, zur Planung · Bezug: Hauptspec `2026-09-05-beachhub-design.md` (§ 3.10, § 7, § 8.2, § 9 „Hallentag“, „Internetausfall Halle“)

## 1. Ziel und Abgrenzung

Der Hallendienst läuft auf einem Rechner in der Halle neben Home Assistant. Er schaltet Licht,
Heizung und Tür passend zu den Buchungen, prüft PINs am Tastenfeld, meldet Ereignisse ans
Hauptsystem und arbeitet bei Verbindungsausfall mindestens 72 h, faktisch bis zum Ende seines
7-Tage-Plans, allein weiter.

Architektur wie Hauptspec § 8.2: **eigenständiger Dienst, keine Custom Component.** HA wird über
REST (Dienste aufrufen, Zustände lesen und schreiben) und WebSocket (Ereignisse abonnieren) mit einem
Long-Lived Access Token angesprochen.

**Enthalten:**

- `hall/`: der Dienst mit SQLite, Plan-Abruf, Steuerung, PIN-Prüfung, Präsenzmeldungen,
  Ereignispuffer, Handbetrieb und Status-Sensoren in HA, dazu Dockerfile (arm64/amd64) und ein
  HA-Simulator für Tests.
- `core/`: `GET /hall/plan`, `POST /hall/ereignisse`, `POST /hall/status`, die Tabellen `ereignis`
  und `hallen_status`, der Plan als signiertes Dokument, Betreiber-Alarme, eine Admin-Seite „Halle“
  (nur Anzeige) und die Heizwerte des Betreibers als neue Vorgaben.

**Nicht enthalten** (Stufe 5): Ableitung der Anwesenheit aus Ereignissen (A-HALLE-5),
Klärungsliste für Präsenz ohne Buchung, Markierung einzelner Buchungen als „Halle nicht informiert“.
Die Ereignisse werden schon vollständig gespeichert, und die Admin-Seite warnt hallenweit, wenn die
Halle nicht die aktuelle Planversion hat.

**Festlegung Sperren:** Sperren schalten weder Licht noch Heizung und öffnen keine Tür. Für Turniere
nutzt der Betreiber den Handbetrieb und den Master-PIN. Als Rückfrage an den Betreiber in die
nächste Runde aufgenommen (Hauptspec § 12.2).

## 2. Hauptsystem: Schnittstelle `/hall`

Router `core/beachhub_core/routes/hall.py`, ohne CSRF und ohne Admin-Session. Caddy erzwingt auf einer
eigenen Site (Port 8444) ein Client-Zertifikat der internen CA und reicht dort nur `/hall/*` durch. Das Hauptsystem prüft zusätzlich
`Authorization: Bearer <HALL_TOKEN>` (`hmac.compare_digest`). Ist `HALL_TOKEN` leer, antwortet der
Router mit 404 (Halle nicht eingerichtet).

| Aufruf | Anfrage | Antwort |
|---|---|---|
| `GET /hall/plan?ab=<version>` | – | `200` mit `Dokument` (`dokument = "hallenplan"`), `304`, wenn `ab` der aktuellen Version entspricht |
| `POST /hall/ereignisse` | `{"dienst_id": UUID, "ereignisse": [HallenEreignis…], "status": HallenStatus?}` | `{"bestaetigt_bis": seq, "plan_neu": bool}` |
| `POST /hall/status` | `HallenStatus` | `{"plan_neu": bool}` |

**Plan** (`shared/beachhub_shared/hallenplan.py`, `HallenplanInhalt`):

```
gueltig_ab, gueltig_bis                     # erzeugt_am bis erzeugt_am + 7 Tage
felder:    [{id, name, aktiv}]
buchungen: [{buchung_id, feld_id, beginn, ende, pin_hash}]   # nur status = bestaetigt
sperren:   [{feld_id | null, beginn, ende}]                  # informativ, schalten nichts
konfig:    {heiz_vorlauf_minuten, spiel_temperatur, grund_temperatur,
            licht_vorlauf_minuten, licht_nachlauf_minuten, zutritt_vorlauf_minuten,
            praesenz_alarm_minuten}
pin:       {verfahren: "argon2id", salt_b64, time_cost, memory_cost, parallelism, hash_len}
```

Das Hauptsystem erzeugt ihn über den bestehenden Lesestand-Mechanismus als Dokument `hallenplan`
(neuer Zweig in `lesestand._inhalt`, signiert mit demselben Schlüssel). Die Version steigt:

- überall dort, wo heute `belegung` markiert wird (Anlage, Statuswechsel, Storno, Sperre,
  Dauerbuchung, Stammdaten), über eine Hilfsfunktion, die beide markiert;
- bei Änderung eines Konfigurationswerts aus `konfig`;
- täglich um 00:05, weil das 7-Tage-Fenster weiterwandert.

Dauerbuchungs-Termine erscheinen mit dem PIN-Hash ihrer Buchung. Der ist gleich dem der
Dauerbuchung, weil alle Termine dieselbe PIN tragen.

`pin.salt()` wird aus `pin.hash()` herausgelöst, damit der Plan das Salt ausliefern kann. Das Salt
ist kein Geheimnis im Sinne des PIN-Schlüssels: Es erlaubt nur das Nachrechnen von Hashes, nicht
das Entschlüsseln der PIN-Klartexte. Bekannte Grenze (in der Hauptspec § 8.2 bereits so getragen):
Wer den Hallenrechner stiehlt, kann 10⁶ Kandidaten gegen die Hashes durchprobieren. Mit den
Argon2-Parametern (64 MiB, t=2) dauert das je Hash Stunden, und die PINs laufen nach ihrem Termin
ab.

**Ereignis** (`HallenEreignis`): `{seq: int, typ: str, zeitpunkt: datetime, feld_id?: UUID,
buchung_id?: UUID, daten: dict}`. Das Hauptsystem speichert jedes Ereignis in `ereignis`
(`quelle = "halle"`, unique über `(halle_dienst_id, halle_seq)`). Ein Duplikat überspringt es stillschweigend. `halle_dienst_id` ist eine UUID, die der Hallendienst beim Anlegen seiner Datenbank einmal erzeugt und in jeder Ereignislieferung mitschickt; wird die SQLite-Datei neu angelegt (Hardwaretausch, Neuinstallation), beginnt `seq` wieder bei 1, ohne dass neue Ereignisse als Duplikate verworfen werden.
`bestaetigt_bis` ist die höchste `seq`, bis zu der alle Ereignisse gespeichert sind. Weil die
Halle immer ab ihrer niedrigsten unbestätigten `seq` liefert, zieht das Hauptsystem seine Marke
vorher auf „kleinste gelieferte `seq` − 1“ nach (nur anheben) – sonst bliebe sie nach einer
Wiederherstellung aus einem älteren Backup für immer stehen. Bestätigt das Hauptsystem von einer
Lieferung nichts, wartet der Melder mit Backoff, statt sofort erneut zu liefern. Bei diesen
Typen bekommt der Betreiber eine Alarm-Mail (A-MAIL-2): `tastenfeld_fehlversuche`,
`praesenz_ohne_buchung`, `aktor_fehler`, `ha_nicht_erreichbar`, `plan_verworfen`,
`tuer_offen_ausserhalb`. Stammt ein alarmierendes Ereignis aus der Nachlieferung nach einem
Ausfall (älter als 6 h), fasst das Hauptsystem die Alarme einer Lieferung in einer Mail zusammen.

**Status** (`HallenStatus`): `{planversion, letzter_abruf, ha_erreichbar, handbetrieb,
felder: [{feld_id, licht_ist, praesenz}], heizung: {soll, ist?}, tuer: {verriegelt?, offen?},
warteschlange: int, version_dienst}`. Das Hauptsystem speichert ihn in `hallen_status` (eine Zeile,
JSONB und `empfangen_am`). `plan_neu = (planversion ≠ aktuelle Version von hallenplan)`.
Fordert die Halle mit `ab` eine höhere Version an, als das Hauptsystem gespeichert hat (nach einer
Wiederherstellung aus einem Backup), veröffentlicht `GET /hall/plan` den Plan neu – mit
`max(alt + 1, Unixzeit in ms)` liegt die neue Version über `ab`, statt dass die Halle den alten
Plan als `version_alt` verwirft.

**Alarm „Halle ohne Kontakt“:** Ein APScheduler-Job prüft alle 5 min. Liegt `empfangen_am` mehr als
60 min zurück, geht einmal eine Mail raus (Marker in `app_setting`). Beim nächsten Kontakt folgt
eine Mail „Halle wieder verbunden“ mit der Dauer des Ausfalls.

**Neue Tabellen** (Migration `0009_halle`): `ereignis` wie Hauptspec § 5 (`quelle`, `typ`,
`zeitpunkt`, `feld_id?`, `buchung_id?`, `daten_json`, `halle_dienst_id?`, `halle_seq?`, unique über beide) sowie
`hallen_status` (`id = 1`, `daten_json`, `empfangen_am`).

**Konfiguration:** Die Vorgaben ändern sich auf `heiz_vorlauf_minuten = 30`,
`spiel_temperatur = 18.0` und `grund_temperatur = 0.0` (Betreiber, O-9). Neu kommt
`praesenz_alarm_minuten = 10` (Gruppe „Halle“) hinzu. Bereits in der Tabelle gespeicherte Werte
bleiben unangetastet. Neue Einstellung: `hall_token`.

**Admin-Seite „Halle“** (`/admin/halle`, Menüpunkt unter System, beide Rollen lesend): Sie zeigt
den letzten Kontakt (mit Warnfarbe ab 60 min), die Planversion der Halle im Vergleich zur aktuellen
(Warnung „Die Halle hat noch nicht den aktuellen Plan – neue Buchungen seit Version N kennt sie
nicht“), HA erreichbar, Handbetrieb, je Feld Licht und Präsenz, Heizung Soll/Ist, Türzustand,
Länge der Warteschlange und die letzten 100 Ereignisse mit Filter nach Typ. Handbetrieb schalten
geht hier nicht: Der Kanal ist einseitig, Handbetrieb gibt es in HA.

## 3. Hallendienst: Aufbau

```
hall/
  beachhub_hall/
    __main__.py        Start: Konfiguration laden, DB öffnen, Aufgaben starten, Signale
    config.py          Umgebung (CORE_URL, HALL_TOKEN, Zertifikate, HA_URL, HA_TOKEN,
                       DATA_DIR, CORE_PUBLIC_KEY) + hall.toml (Zuordnung, Master-PIN-Hash)
    clock.py           injizierbare Uhr (echt / simuliert für Tests)
    db.py              SQLite (WAL) über SQLAlchemy 2.0, Tabellen § 4
    plan.py            Plan laden, prüfen, ersetzen; Abfragen „aktive Buchungen um t“
    soll.py            reine Funktion sollzustand(plan, konfig, zuordnung, jetzt, handbetrieb)
    pin.py             Hashen mit Plan-Parametern, Master-PIN, Fehlversuchsserie
    ha.py              HA-Client: REST (Dienste, Zustände, /api/states schreiben) + WebSocket
    ereignisse.py      in Warteschlange schreiben (seq), Signal an den Melder
    aufgaben/
      plan_abruf.py    alle 5 min + bei plan_neu
      steuerung.py     alle 30 s + bei relevanten Zustandsänderungen
      ha_zuhoerer.py   WebSocket-Abo, Reconnect
      melder.py        Ereignisse + Status ans Hauptsystem
      status_ha.py     sensor.beachhub_* nach HA
    health.py          kleiner HTTP-Server nur für GET /health (aiohttp)
  tests/
    ha_simulator.py    Fake-HA: REST + WebSocket (aiohttp), Entitäten im Speicher, Dienstaufrufe protokolliert
    core_simulator.py  Fake-Hauptsystem: signierte Pläne, nimmt Ereignisse an
  hall.toml.example, Dockerfile, docker-compose.yml, .env.example, README.md, pyproject.toml
```

Abhängigkeiten: `aiohttp` (HA-WebSocket, REST, Health, Simulator), `httpx` (Hauptsystem mit mTLS),
`sqlalchemy`, `argon2-cffi`, `pydantic-settings`, `beachhub-shared`. Keine FastAPI, weil keine
Oberfläche nötig ist.

**`hall.toml`** (Zuordnung Feld → Entität, A-FELD-4):

```toml
master_pin_hash = "$argon2id$v=19$m=65536,t=2,p=1$…"   # Standard-argon2-Hash mit eigenem Salt

[felder."3f0c…-uuid"]
licht    = "light.feld_1"
praesenz = "binary_sensor.praesenz_feld_1"

[heizung]
entity = "climate.halle"
ist_sensor = "sensor.halle_temperatur"   # optional; sonst Attribut current_temperature

[tuer]
entity = "lock.eingang"          # lock.* → lock.unlock; switch.* → turn_on + Impuls
impuls_sekunden = 5              # nur switch.*
kontakt = "binary_sensor.tuer"   # optional

[tastenfeld]
ereignis = "esphome.beachhub_pin"   # HA-Ereignistyp
feld = "code"                        # Schlüssel in event.data
verzoegerung_sekunden = 3            # ab 5. Fehlversuch der Serie

[handbetrieb]
entity = "input_boolean.beachhub_handbetrieb"
```

Ein Feld aus dem Plan ohne Eintrag in `hall.toml` wird nicht geschaltet. Beim ersten Plan meldet
der Dienst das einmal als `aktor_fehler` mit `grund = "feld_nicht_zugeordnet"`. Ein
Master-PIN-Hash ist Pflicht, sonst startet der Dienst nicht. `beachhub-hall master-pin` fragt die
PIN ab (8 bis 12 Ziffern – sie öffnet immer, auch ohne Buchung) und gibt den Hash aus.

## 4. Hallendienst: Datenmodell (SQLite, WAL)

| Tabelle | Felder |
|---|---|
| `plan_meta` | version, erzeugt_am, gueltig_bis, empfangen_am, dokument_json (ganzes signiertes Dokument) |
| `plan_buchung` | buchung_id, feld_id, beginn, ende, pin_hash |
| `plan_sperre` | feld_id?, beginn, ende |
| `plan_feld` | feld_id, name, aktiv |
| `plan_konfig` | schluessel, wert |
| `ereignis_queue` | seq (autoincrement), typ, zeitpunkt, feld_id?, buchung_id?, daten_json, gesendet_am? |
| `zustand` | schluessel, wert_json (Dienst-ID, Handbetrieb, Fehlversuchsserie, letzter Abruf, gemeldete Präsenzen, ausgelöste Präsenzalarme) |

Der Plan wird in einer Transaktion vollständig ersetzt. Die Zuordnung zu Entitäten steht nicht in
der Datenbank, sondern nur in `hall.toml` (abweichend von Hauptspec § 7 `plan_feld`). Bestätigte
Ereignisse werden nach 90 Tagen gelöscht (Hauptspec § 10). Unbestätigte bleiben, bis sie
ausgeliefert sind.

## 5. Hallendienst: Verhalten

**Plan-Abruf.** Alle 5 min, beim Start und sofort bei `plan_neu = true` ruft der Dienst
`GET /hall/plan?ab=<version>` auf. Auf `304` folgt nichts. Bei `200` prüft er die Signatur mit
`CORE_PUBLIC_KEY`, dann `version > gespeicherte` und `dokument = "hallenplan"`. Erst danach ersetzt
er den Plan und stößt die Steuerung sofort an. Scheitert eine Prüfung, verwirft er den Plan und
meldet `plan_verworfen` (Grund `signatur`, `version_alt` oder `schema`). Netzfehler sind kein
Ereignis. Sie zeigen sich über den Status und den Alarm im Hauptsystem.

**Sollzustand** (`soll.py`, reine Funktion, stündlich über 7 Tage testbar):

- **Licht je Feld:** an, wenn `jetzt` in einem Intervall `[beginn − licht_vorlauf, ende + licht_nachlauf)`
  einer Buchung dieses Felds liegt. Überlappende oder berührende Intervalle werden vorher
  zusammengelegt, so bleibt das Licht zwischen direkt aufeinanderfolgenden Buchungen an (A-HALLE-2).
- **Heizung hallenweit:** `spiel_temperatur`, wenn `jetzt` in einem Intervall
  `[beginn − heiz_vorlauf, ende)` irgendeiner Buchung liegt (ebenfalls zusammengelegt), sonst
  `grund_temperatur` (A-HALLE-1).
- **Zutritt:** Eine Buchung ist zutrittsberechtigt, solange `beginn − zutritt_vorlauf ≤ jetzt < ende`
  gilt. Das wird nicht geschaltet, sondern bei der PIN-Prüfung abgefragt.
- **Kein gültiger Plan** (keiner vorhanden oder `jetzt ≥ gueltig_bis`): Licht aus,
  Grundtemperatur, Zutritt nur mit Master-PIN.
- **Handbetrieb:** Die Funktion liefert „nichts steuern“.

**Steuerung.** Alle 30 s und sofort bei einem neuen Plan, beim Ende des Handbetriebs oder bei einer
Zustandsänderung einer gesteuerten Entität von außen: Soll gegen Ist aus HA vergleichen und nur bei
Abweichung schalten (`light.turn_on/off`, `climate.set_temperature`). Jede Schaltung erzeugt
`licht_geschaltet` bzw. `heizung_gesetzt` (mit `soll`, `ist_temperatur`). Scheitert ein
Dienstaufruf oder weicht der Zustand nach 3 Versuchen noch ab, entsteht `aktor_fehler` einmal je
Entität und Störung.

**HA-Zuhörer.** Der WebSocket abonniert `state_changed` (gefiltert auf die zugeordneten
Entitäten) und den Ereignistyp des Tastenfelds. Bei Verbindungsverlust verbindet er sich mit
Backoff (1 s bis 60 s) neu. Sind 2 min ohne Verbindung vergangen, meldet er einmal
`ha_nicht_erreichbar`. Nach der Wiederverbindung liest er alle Zustände neu ein und stößt die
Steuerung an.
Lehnt HA das Abo des Tastenfeld-Ereignistyps wegen fehlender Rechte ab (Token ohne
Administratorrechte), ist das kein Ausfall: Der Dienst loggt einen Fehler („HA-Token braucht
Administratorrechte“) und arbeitet mit `state_changed` weiter; ein eigenes Ereignis dafür gibt es
nicht.

**PIN-Prüfung** (auf das Tastenfeld-Ereignis):

1. Die Eingabe muss aus 4 bis 12 Ziffern bestehen, sonst gilt sie als Fehlversuch.
2. Läuft eine Fehlversuchsserie mit mindestens 5 Versuchen, wartet der Dienst
   `verzoegerung_sekunden`, bevor er die Eingabe prüft (A-HALLE-3, keine Sperre). Die Wartezeit
   blockiert nur diese Eingabe, nicht den Dienst.
3. Master-PIN (`argon2.PasswordHasher().verify`): Tür öffnen, `pin_akzeptiert` mit
   `daten.master = true`. Das geht immer, auch ohne Plan und im Handbetrieb.
4. Sonst mit den Parametern aus dem Plan hashen und eine Buchung mit gleichem `pin_hash` suchen,
   deren Zutrittsfenster `jetzt` enthält. Gefunden: Tür öffnen, `pin_akzeptiert` mit `buchung_id`
   und `feld_id`. Auch im Handbetrieb öffnet die Tür bei gültiger PIN, denn der Handbetrieb betrifft
   Licht und Heizung, nicht den Zutritt.
5. Nicht gefunden: `pin_abgelehnt`, Serie +1. Die PIN selbst wird nicht protokolliert. Beim 5.
   Fehlversuch einer Serie wird `tastenfeld_fehlversuche` gemeldet, einmal je Serie.
6. Die Serie endet mit einer akzeptierten PIN oder nach 15 min ohne Eingabe.

Das Hashen (etwa 0,1 bis 0,5 s auf kleiner Hardware) läuft in einem Thread-Pool, damit die übrigen
Aufgaben weiterlaufen.

**Tür öffnen.** Bei `lock.*` ruft der Dienst `lock.unlock` auf (die Wiederverriegelung übernimmt das
Schloss bzw. eine HA-Automation). Bei `switch.*` schaltet er ein und nach `impuls_sekunden` wieder
aus. Meldet der Türkontakt „offen“, entsteht `tuer_offen_ausserhalb` nur, wenn zugleich
(1) keine Buchung ein Fenster `[beginn − zutritt_vorlauf, ende + licht_nachlauf)` hat – weiter als
das Zutrittsfenster der PIN, weil die Spieler die Halle nach dem Ende verlassen –, (2) kein
Master-PIN in den letzten 5 min akzeptiert wurde und (3) kein Feld gerade Präsenz meldet. Das
Zutrittsfenster für die PIN-Prüfung bleibt `[beginn − zutritt_vorlauf, ende)`.

**Präsenz.** Wechselt ein Präsenzsensor auf `on`, entsteht `praesenz_start` (mit `buchung_id`, wenn
eine Buchung dieses Felds gerade im Intervall `[beginn, ende)` liegt). Beim Wechsel auf `off`
entsteht `praesenz_ende`. Ist Präsenz ohne laufende Buchung länger als `praesenz_alarm_minuten`
anhaltend, meldet der Dienst `praesenz_ohne_buchung`, einmal je Präsenzphase. Die Steuerung prüft
das bei jedem Lauf.

**Handbetrieb.** Maßgeblich ist der Zustand der HA-Entität `input_boolean.beachhub_handbetrieb`.
Legt der Betreiber sie in HA als Helfer an, lässt sie sich im Dashboard schalten. Jeder Wechsel
erzeugt `handbetrieb_an` bzw. `handbetrieb_aus`. Beim Ausschalten stellt die Steuerung den
Sollzustand sofort her (A-HALLE-7). Ist HA nicht erreichbar, gilt der zuletzt bekannte Zustand
(in `zustand` gespeichert).

**Melder.** Neue Ereignisse wecken ihn sofort, sonst läuft er alle 60 s. Er sendet bis zu 200
unbestätigte Ereignisse in `seq`-Reihenfolge und den Status an `POST /hall/ereignisse`, setzt
`gesendet_am` bis `bestaetigt_bis` und wiederholt, solange weitere warten. Bei Fehlern gibt es
Backoff bis 60 s, und nichts geht verloren. Bei `plan_neu = true` weckt er den Plan-Abruf.

**Status nach HA** (alle 60 s und bei Änderung, über `POST /api/states/<entity_id>`):
`sensor.beachhub_planversion` (Attribute: gültig bis, empfangen am),
`sensor.beachhub_letzter_kontakt` (Zeitstempel, `device_class: timestamp`),
`binary_sensor.beachhub_verbunden` (Hauptsystem erreicht innerhalb der letzten 10 min),
`sensor.beachhub_warteschlange` (Anzahl unbestätigter Ereignisse). Diese Zustände überleben
keinen HA-Neustart; der Dienst schreibt sie nach der Wiederverbindung sofort neu.

**Start.** Nach dem Start meldet der Dienst `dienst_gestartet` (mit Version). Er arbeitet ab der
ersten Sekunde aus dem gespeicherten Plan, noch bevor Hauptsystem oder HA erreichbar sind.

**Ereignistypen** (Hauptspec § 8.2 angepasst): `pin_akzeptiert`, `pin_abgelehnt`,
`tastenfeld_fehlversuche` (ersetzt `tastenfeld_gesperrt`), `praesenz_start`, `praesenz_ende`,
`praesenz_ohne_buchung`, `tuer_offen_ausserhalb`, `licht_geschaltet`, `heizung_gesetzt`,
`ha_nicht_erreichbar`, `aktor_fehler`, `plan_verworfen`, `handbetrieb_an`, `handbetrieb_aus`,
`dienst_gestartet`.

## 6. Home Assistant (Beispiel-Einrichtung)

Die Dokumentation `docs/betrieb/hallendienst.md` beschreibt:

- einen Long-Lived Access Token für einen eigenen HA-Benutzer „beachhub“ **mit
  Administratorrechten**: HA erlaubt Nicht-Administratoren per WebSocket nur Ereignistypen aus
  seiner `SUBSCRIBE_ALLOWLIST` (der eigene Tastenfeld-Typ gehört nicht dazu) und
  `POST /api/states/<entity_id>` (Status-Sensoren) gar nicht; der Token ist deshalb sicher zu
  verwahren,
- den Helfer `input_boolean.beachhub_handbetrieb`,
- bei `lock.*` die Wiederverriegelung als Pflicht (Auto-Lock des Schlosses oder Beispiel-Automation
  „`unlocked` seit 10 s → `lock.lock`“) samt Prüfpunkt in der Checkliste vor der Inbetriebnahme,
- das Entprellen der Präsenzsensoren (`delay_off`),
- dass `hall.sqlite` nie aus einer Sicherung zurückgespielt, sondern gelöscht wird (sonst gleiche
  Dienst-ID mit altem `seq`-Stand, neue Ereignisse würden als Duplikate verworfen),
- ein Beispiel-Dashboard mit den `beachhub`-Sensoren und dem Handbetrieb-Schalter,
- die Rückfall-Automation aus Hauptspec § 8.2 (Licht aus außerhalb der Betriebszeit, falls der
  Dienst ausfällt),
- ein Tastenfeld-Beispiel mit ESPHome, das nach Eingabe von `#` das Ereignis
  `esphome.beachhub_pin` mit `code` auslöst (die tatsächliche Hardware klärt der Hallenhersteller,
  O-10),
- einen **Probelauf im eigenen Homelab-HA** mit Dummy-Entitäten (Template-Lights,
  `input_boolean` als Präsenz, `input_number` als Thermostat über `climate`-Template bzw.
  `generic_thermostat`) und dem Aufruf des Ereignisses über die Entwicklerwerkzeuge.

## 7. Tests

- **`soll.py`** (rein): Vorlauf und Nachlauf, zusammengelegte Intervalle bei direkt folgenden
  Buchungen, Lücke kleiner als Vor- plus Nachlauf, Heizblöcke über mehrere Felder, Sperren schalten
  nichts, abgelaufener Plan, kein Plan, Handbetrieb.
- **`pin.py`:** Treffer im Fenster, vor dem Fenster, nach dem Ende, falsches Feld spielt keine
  Rolle, Master-PIN, Hash identisch mit `core`-`pin.hash()` (Vertragstest über festes Salt),
  Fehlversuchsserie mit Verzögerung und einmaliger Meldung, Ende der Serie nach 15 min.
- **Plan:** Signatur falsch, Version alt, falscher Dokumentname, atomarer Ersatz.
- **Integration gegen HA-Simulator und Core-Simulator:** Hallentag aus Hauptspec § 9 mit simulierter
  Uhr (Heizung, Licht, PIN, Präsenz, Ereignisse beim Hauptsystem); HA-Verbindungsabbruch und
  Wiederverbindung; Handbetrieb an/aus; Offline-Szenario: Hauptsystem weg, Uhr 72 h weiter,
  Schaltungen laufen weiter, danach werden alle Ereignisse lückenlos in `seq`-Reihenfolge
  nachgeliefert.
- **Hauptsystem:** Planaufbau (nur bestätigte Buchungen, 7-Tage-Fenster, Konfiguration, Salt),
  Versionserhöhung bei Buchungsänderung, `304`, Token-Prüfung, Ereignisse idempotent über
  `halle_seq`, `bestaetigt_bis`, Alarm-Mails, `plan_neu`, Kontakt-Alarm-Job (einmalig, Entwarnung),
  Admin-Seite.

## 8. Betrieb

- `hall/Dockerfile` (python:3.12-slim, Multi-Arch arm64/amd64, Nutzer ohne Root, Volume `DATA_DIR`
  für SQLite).
- `hall/docker-compose.yml` für den Betrieb neben HA (Docker oder HA OS mit Container-Unterstützung).
- WireGuard: Der Hallendienst ist Client mit `PersistentKeepalive`, und das Hauptsystem lauscht nur
  auf dem WireGuard-Interface. Das Beispiel steht bereits in `core/deploy`.
- Caddy des Hauptsystems: eigene Site auf Port 8444 mit `client_auth { mode require_and_verify }` (interne CA), die nur `/hall/*` durchreicht; auf der Admin-Site liefert `/hall/*` 404 (Client-Zertifikate lassen sich nicht pro Pfad erzwingen). Die
  Zertifikate erzeugt `beachhub-core zertifikate` (siehe Portal-Spec § 12).
- Lokale Entwicklung: `CORE_URL=http://127.0.0.1:8000` ohne mTLS, `HA_URL` auf das Homelab-HA oder
  den Simulator (`python -m tests.ha_simulator`).
