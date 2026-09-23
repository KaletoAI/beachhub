# Portal-Kern (Stufe 2 ohne 1a) – Design

Stand: 2026-09-23 · Status: abgestimmt, zur Planung · Bezug: Hauptspec `2026-09-05-beachhub-design.md` (§ 3, § 6, § 8.1, § 9, § 10)

## 1. Ziel und Abgrenzung

Kunden legen im Buchungsportal ein Konto an, sehen freie Zeiten, buchen und bezahlen Einzeltermine,
sehen ihre Buchungen mit PIN, stornieren und laden Rechnungen herunter. Das Hauptsystem verarbeitet
die Anfragen über einen Kanal, den es selbst aufbaut.

**Enthalten:**

- `portal/`: neue FastAPI-App mit eigener PostgreSQL-Datenbank, Schema `spiegel` (Hauptspec § 6).
- `core/`: Kanal-Client (Long-Polling), Verarbeitung der Anfragetypen `konto_angelegt`,
  `konto_geaendert`, `konto_loeschen`, `buchung_anfragen` (ohne Gutschein), `buchung_stornieren`,
  `zahlung_eingegangen` und `rechnung_anfordern`, dazu die Zahlungsschnittstelle `PaymentProvider`
  mit einem Fake-Anbieter und der Job, der Reservierungen verfallen lässt.
- `shared/`: Nachrichtenschemata für den Kanal.

**Nicht enthalten** (kommt nach dem Betreiber-Feedback zusammen mit Stufe 1a):
`mitgliedschaft_beantragen`, `gutschein_kaufen`, `gutschein_code` beim Buchen, ein echter
Zahlungsanbieter (Stripe oder Mollie, Ⓞ-13), die Gruppenverwaltung (Stufe 4, Schema `gruppen`).

## 2. Kanal Portal ↔ Hauptsystem

Entscheidung: **Long-Polling statt WebSocket.** Es gibt nur einen Transportweg. Rückfall und
Normalbetrieb sind derselbe Code, neue Anfragen kommen trotzdem sofort an, und Caddy kann mTLS pro
Pfad einfach erzwingen. Die Hauptspec § 8.1 wird entsprechend angepasst.

Alle Aufrufe gehen vom Hauptsystem aus. Caddy erzwingt auf `/core/*` ein Client-Zertifikat der
internen CA. Das Portal prüft zusätzlich den Header `Authorization: Bearer <KANAL_TOKEN>`
(Vergleich mit `hmac.compare_digest`). Ohne gültigen Token antwortet es mit 401.

| Aufruf | Anfrage | Antwort |
|---|---|---|
| `GET /core/anfragen?warten=<s>` | `warten` 0–30, Vorgabe 25 | `{"anfragen": [Anfrage…]}`, höchstens 50, älteste zuerst |
| `POST /core/antworten` | `{"antworten": [{"anfrage_id", "antwort"}]}` | `{"ok": n}` |
| `POST /core/lesestand` | `{"dokumente": [Dokument…]}` | `{"uebernommen": [...], "verworfen": [{"dokument", "grund"}]}`; `422`, wenn mindestens eine Signatur ungültig ist |
| `GET /core/lesestand/versionen` | – | `{"<dokument>": version, …}` |

**Abholen.** Das Portal liefert alle Anfragen mit `status = offen` und zusätzlich solche mit
`status = abgeholt`, deren `abgeholt_am` länger als 60 s zurückliegt (erneute Auslieferung). Die
ausgelieferten Anfragen bekommen `status = abgeholt` und `abgeholt_am = jetzt`. Liegt keine vor,
wartet das Portal bis zu `warten` Sekunden: Ein prozessinternes `asyncio.Event` weckt, sobald eine
neue Anfrage angelegt wird, und zusätzlich prüft das Portal jede Sekunde die Datenbank. Die
Datenbankprüfung ist der Rückfall für den Fall, dass eine Anfrage über einen anderen Worker kam.
Das Portal läuft mit einem Worker; die Prüfung kostet bei dieser Größe nichts.

**Anfrage** (Schema in `shared/beachhub_shared/kanal.py`):
`{anfrage_id: UUID, typ: str, konto_id: UUID | None, kunde_id: UUID | None, nutzlast: dict, erstellt_am: datetime}`.
`kunde_id` setzt das Portal aus dem Konto, sobald es sie kennt. Das Hauptsystem ermittelt den Kunden
trotzdem selbst über `kunde.portal_konto_id = konto_id` und traut dem Feld nicht.

**Antwort:** `{status: "ok" | "reserviert" | "bestaetigt" | "abgelehnt" | "fehler", …}`. Die
typspezifischen Felder stehen in § 4.

**Lesestand-Empfang.** Das Portal prüft jedes Dokument mit dem hinterlegten öffentlichen Schlüssel
(`beachhub_shared.signatur.pruefe` über `model_dump(mode="json", exclude={"signatur"})`, genau wie
das Hauptsystem signiert). Übernommen wird nur eine höhere Version als die gespeicherte. Eine
gleiche oder niedrigere Version gilt nicht als Fehler, sondern landet mit Grund `version_alt` in
`verworfen`. Das Portal nimmt nur die Dokumentnamen `belegung`, `tarife` und `konto:<uuid>` an;
alle anderen verwirft es mit Grund `unbekannt`. Ein `konto:`-Dokument zu einer unbekannten
`kunde_id` wird trotzdem gespeichert, weil es vor der Antwort auf `konto_angelegt` eintreffen kann.

## 3. Hauptsystem: Kanal-Client

Neues Modul `core/beachhub_core/kanal.py`. Beim Start im `lifespan` laufen zwei Threads, außer bei
`ENABLE_KANAL=false` oder leerer `PORTAL_URL`. Der HTTP-Client ist `httpx.Client` mit Client-Zertifikat
(`PORTAL_CLIENT_CERT`, `PORTAL_CLIENT_KEY`) und CA (`PORTAL_CA`). Für Tests lässt sich ein
`httpx`-Transport einsetzen.

1. **Abholer.** Er ruft in einer Schleife `GET /core/anfragen?warten=25` auf (Lese-Timeout 35 s).
   Jede Anfrage verarbeitet er einzeln:
   1. Liegt ein Eintrag in `anfrage_verarbeitet` vor, nimmt er die gespeicherte Antwort.
   2. Sonst ruft er `anfragen.verarbeite(db, anfrage)` in einer Transaktion auf. Die Antwort und
      `anfrage_verarbeitet` werden in derselben Transaktion geschrieben und committet.
   3. Dann veröffentlicht er alle als geändert markierten Lesestände (`lesestand.verarbeite_geaenderte`)
      und pusht sie. **Erst danach** sendet er die Antworten. So sieht der Kunde seine Buchung, sobald
      die Antwort ankommt.

   Wirft `verarbeite` eine unerwartete Ausnahme, rollt er zurück, speichert die Antwort
   `{status: "fehler"}` in eigener Transaktion, loggt die Ausnahme und schickt einen
   Betreiber-Alarm. Eine kaputte Anfrage wird so nicht endlos wiederholt. Bei Netz- oder HTTP-Fehlern
   wartet er mit Backoff 1, 2, 4 … höchstens 60 s. Der erste Erfolg setzt den Backoff zurück.
   Ein Betreiber-Alarm „Portal nicht erreichbar“ geht nach 30 min ohne erfolgreichen Abruf raus,
   einmal je Ausfall.
2. **Verteiler.** Alle 2 s ruft er `lesestand.verarbeite_geaenderte` auf und pusht alle
   veröffentlichten Dokumente mit den Namen `belegung`, `tarife` und `konto:*`. Andere Dokumente
   (etwa `hallenplan`) gehen nie ans Portal. Beim Start und danach stündlich gleicht er ab: Er holt
   `GET /core/lesestand/versionen` und pusht jedes Dokument aus `data/lesestand/`, dessen Version im
   Portal fehlt oder niedriger ist. Das deckt ein wiederhergestelltes Portal ab.

Der bisherige APScheduler-Job `_job_lesestand` bleibt für die nächtliche Neuerzeugung bestehen.

**Neue Einstellungen** (`core/beachhub_core/config.py`): `portal_url`, `kanal_token`,
`portal_client_cert`, `portal_client_key`, `portal_ca`, `enable_kanal` (Vorgabe `true`),
`zahlung_provider` (Vorgabe `fake`). Bei `app_env=production` verweigert das Hauptsystem den Start
mit `zahlung_provider=fake` oder einem leeren `kanal_token`, sofern `portal_url` gesetzt ist.

## 4. Hauptsystem: Anfragetypen

Modul `core/beachhub_core/services/anfragen.py` mit einer Tabelle `typ → Verarbeiter`. Unbekannte
Typen werden mit `{status: "abgelehnt", grund: "unbekannter_typ"}` beantwortet. Jede Nutzlast wird
mit dem pydantic-Schema aus `shared` validiert. Ist sie ungültig, lautet die Antwort
`abgelehnt/ungueltig`. Das Audit bekommt `quelle = "portal"`.

| Typ | Nutzlast | Verarbeitung | Antwort |
|---|---|---|---|
| `konto_angelegt` | `email`, `anzeigename` | Gibt es einen Kunden mit derselben E-Mail (lower), der nicht anonymisiert ist und keine andere `portal_konto_id` trägt, wird `portal_konto_id` gesetzt. Sonst entsteht ein neuer Kunde mit der Gruppe aus dem Konfigurationswert `portal_kundengruppe` (Name, Vorgabe: die erste Gruppe nach Name). Mit 1a wird daraus fest „Nicht-Mitglied“. Danach wird `konto:<kunde_id>` markiert. | `ok`, `kunde_id` |
| `konto_geaendert` | `anzeigename`, `bisher` | Den Namen nur übernehmen, wenn `kunde.name == bisher`. Hat der Betreiber den Namen inzwischen gepflegt (etwa den vollen Namen für die Rechnung), bleibt er stehen. Adressdaten ändert das Portal nie. | `ok` |
| `konto_loeschen` | – | `kunden.anonymisiere`. `portal_konto_id` wird geleert. | `ok` |
| `buchung_anfragen` | `feld_id`, `beginn`, `ende` | Siehe unten. | `reserviert` / `bestaetigt` / `abgelehnt` |
| `buchung_stornieren` | `buchung_id` | Die Buchung muss zum Kunden gehören und den Status `reserviert` oder `bestaetigt` haben, und `jetzt < beginn` muss gelten (A-STORNO-5). Eine `reserviert`e Buchung wird zu `storniert` und immer kostenfrei; verrechnetes Guthaben wird zurückgebucht (`rueckbuchung`), eine offene Bezahlsitzung verfällt beim Anbieter von selbst. Eine `bestaetigt`e Buchung läuft über `storno.storniere(..., durch="kunde")`. | `ok`, `kostenfrei` / `abgelehnt` (`nicht_gefunden`, `zu_spaet`) |
| `zahlung_eingegangen` | `provider`, `rohdaten`, `signatur_header` | Siehe unten. | `ok` / `ignoriert` |
| `rechnung_anfordern` | `rechnung_nr` | Die Rechnung muss zum Kunden gehören und ein archiviertes PDF haben. Das PDF wird gelesen und gegen `pdf_sha256` geprüft. | `ok`, `pdf_base64`, `dateiname` / `abgelehnt` (`nicht_gefunden`) |

Existiert zu `konto_id` kein Kunde, beantwortet das Hauptsystem jede Anfrage außer `konto_angelegt`
mit `abgelehnt/konto_unbekannt`. Die Reihenfolge ist sicher: Das Portal liefert Anfragen nach
`erstellt_am` aus, und `konto_angelegt` entsteht vor allen anderen Anfragen des Kontos.

**`buchung_anfragen` im Einzelnen:**

1. `buchungen.lege_an(feld_id, kunde_id, beginn, ende, quelle="portal", pruefe_fenster=True,
   status=RESERVIERT, anfrage_id=…, zahlungsart="online")`. Neu ist der Parameter `zahlungsart`.
   Ohne ihn gilt wie bisher die Zahlungsart des Kunden. Portal-Buchungen sind immer `online`
   (A-ZAHL-1).
2. Die Fehlercodes von `BuchungsFehler` werden auf die Gründe aus § 8.1 abgebildet (`belegt`,
   `ausserhalb_fenster`, `ausserhalb_betriebszeit`, `kein_tarif`, `feld_inaktiv`,
   `konto_gesperrt` für anonymisierte Kunden). Eine Verletzung des Exklusionsconstraints bei
   Gleichzeitigkeit ergibt ebenfalls `belegt`.
3. Das Guthaben wird verrechnet (`guthaben.buche(art="verrechnung")`) bis höchstens zum Preis.
4. Ist der Rest 0: Die Buchung wird `bestaetigt`, `rechnungen.erzeuge_einzelrechnung` läuft
   (Status `bezahlt`), die Bestätigungsmail mit PIN geht raus.
   Antwort: `{status: "bestaetigt", buchung_id, preis, guthaben_verrechnet}`.
5. Sonst: `provider.erzeuge_sitzung(betrag=rest, referenz=buchung_id, ablauf=jetzt+zahlungsfrist)`.
   Es entsteht eine Zeile `zahlung` mit `status = offen`, und `reserviert_bis` wird gesetzt.
   Antwort: `{status: "reserviert", buchung_id, preis, guthaben_verrechnet, zu_zahlen, checkout_url, reserviert_bis}`.

Die PIN vergibt `lege_an` schon heute bei der Anlage. Sie erscheint im Lesestand aber nur für
`bestaetigt`e Buchungen (Korrektur in `baue_konto`, das die PIN bisher für alle aktiven Buchungen
zeigt). Die Halle bekommt ebenfalls nur bestätigte Buchungen.

**`zahlung_eingegangen` im Einzelnen:**

1. Der Anbieter wird über `provider` gewählt. Ist er unbekannt oder nicht aktiv, lautet die
   Antwort `ignoriert`.
2. `provider.verifiziere(rohdaten, signatur_header)` liefert `provider_ref` oder `None`. Bei `None`
   ist die Antwort `ignoriert`, das Ergebnis wird geloggt und die Rohdaten werden als Ereignis
   gespeichert.
3. `provider.status(provider_ref)` liefert `(zustand, betrag)`. Das Hauptsystem traut nur dieser
   Abfrage, nie den Rohdaten (A-ZAHL-2, Mollie-Muster; Stripe liefert dasselbe über die Signatur).
4. Die Zeile `zahlung` wird über `provider_ref` gefunden (unique). Ist sie schon `bezahlt`, lautet
   die Antwort `ok` ohne weitere Wirkung (Idempotenz).
5. Bei `zustand = bezahlt`: Die Zahlung wird `bezahlt`.
   - Ist die Buchung `reserviert`, wird sie `bestaetigt`; Einzelrechnung und Bestätigungsmail
     folgen.
   - Ist die Buchung `verfallen` oder `storniert`, wird der Betrag als Guthaben gebucht
     (`art="ueberzahlung"`), und der Betreiber bekommt eine Mail (A-ZAHL-3).
6. Bei `zustand = fehlgeschlagen/abgebrochen`: Die Zahlung wird `abgebrochen`, und die Buchung
   bleibt bis zum Verfall `reserviert`. Der Kunde kann über die Warteseite erneut zahlen, solange
   `reserviert_bis` nicht erreicht ist.

**Verfall-Job** (APScheduler, minütlich): Buchungen mit `status = reserviert` und
`reserviert_bis < jetzt` werden `verfallen`. Verrechnetes Guthaben wird zurückgebucht
(`art="storno_gutschrift"` ist hier falsch, weil keine Rechnung existiert; neue Art `rueckbuchung`).
Der Kunde bekommt die Mail „Zahlungsfrist abgelaufen“.

## 5. Zahlungsschnittstelle

`core/beachhub_core/zahlung/` mit:

```python
class PaymentProvider(Protocol):
    name: str
    def erzeuge_sitzung(self, *, betrag: Decimal, referenz: uuid.UUID, ablauf: datetime) -> Sitzung: ...
    def verifiziere(self, rohdaten: str, signatur_header: str | None) -> str | None: ...
    def status(self, provider_ref: str) -> tuple[str, Decimal]: ...  # bezahlt | offen | abgebrochen
```

`Sitzung = (provider_ref, checkout_url)`. `anbieter()` liefert die Instanz zu `settings.zahlung_provider`.

**FakeProvider** (`name = "fake"`): `provider_ref = "fake_" + token_urlsafe(16)`, `checkout_url =
<portal_url>/test-zahlung/<provider_ref>?betrag=<betrag>`. `verifiziere` liest aus den Rohdaten das
JSON `{"ref", "ergebnis"}` und merkt sich das Ergebnis prozessintern je `ref`. `status` liefert
dieses Ergebnis und den Betrag der Zeile `zahlung`. Außerhalb von `app_env=dev` oder `test`
wirft der Konstruktor einen Fehler.

Migration `0008_portal_kanal` im Hauptsystem: Tabellen `anfrage_verarbeitet` (`anfrage_id` unique, `typ`, `antwort_json`, `verarbeitet_am`) und `zahlung`, Guthabenart `rueckbuchung`.

Neue Tabelle `zahlung` (Hauptspec § 5): `kunde_id`, `buchung_id?`, `provider`, `provider_ref`
(unique), `betrag`, `status` (`offen`/`bezahlt`/`abgebrochen`), `empfangen_am?`, `rohdaten_json?`.

## 6. Portal: Aufbau

```
portal/
  beachhub_portal/
    main.py            App, Middleware (Security-Header, CSP ohne Inline-Skript), Router, lifespan
    config.py          Settings (DATABASE_URL, SECRET_KEY, KANAL_TOKEN, CORE_PUBLIC_KEY, SMTP_*,
                       BASE_URL, APP_ENV, COOKIE_SECURE, FAKE_ZAHLUNG)
    database.py        Engine, Session, Schema "spiegel"
    models.py          konto, login_token, session, anfrage, lesestand, rechnung_link, webhook_eingang
    auth.py            Login-Token, Sessions, CSRF, Rate-Limit (wie SportAbo-Manager)
    mail.py            SMTP; ohne SMTP im Dev-Modus nur Log
    services/
      anfragen.py      anlegen (mit Event-Signal), abholen, beantworten, Stand abfragen
      lesestand.py     prüfen, speichern, laden (typisiert über beachhub_shared.lesestand)
      slots.py         freie Slots je Feld und Tag aus belegung (+ Preise aus tarife)
      tarife.py        Preis je Slotfolge für eine Kundengruppe, Auflösung wie A-TARIF-2
      rechnung_link.py Einmal-Links, Aufräumen
    routes/
      oeffentlich.py   /, /anmelden, /anmelden/code, /anmelden/link/<token>, /abmelden
      konto.py         /willkommen (Anzeigename), /konto, /konto/loeschen
      buchen.py        /buchen, /anfrage/<id>, /anfrage/<id>/stand (JSON)
      buchungen.py     /buchungen, /buchungen/<id>/stornieren
      rechnungen.py    /rechnungen, /rechnungen/<nr>/anfordern, /rechnung/<token>
      zahlung.py       /zahlung/rueckmeldung/<provider>, /zahlung/zurueck, /test-zahlung/<ref>
      kanal.py         /core/*
    templates/         base.html, je Seite eine Datei, Makros
    static/            style.css, warten.js, favicon.svg
  alembic/             eigene Migrationen, Schema spiegel
  tests/
  Dockerfile, docker-compose.yml, .env.example, README.md, pyproject.toml
  deploy/Caddyfile     TLS öffentlich; mTLS nur auf /core/*
```

Stil: ein eigenes `style.css` nach dem Muster des Admin-UI (Design-Tokens, hell/dunkel), mobil
zuerst. Die Tarifauflösung liegt im Portal in einer eigenen kleinen Funktion. Sie arbeitet auf den
`TarifInfo` des Lesestands und ist nur Anzeige: Den verbindlichen Preis bestimmt das Hauptsystem
beim Buchen.

## 7. Portal: Datenmodell (Schema `spiegel`)

Wie Hauptspec § 6 mit diesen Festlegungen:

| Tabelle | Felder |
|---|---|
| `konto` | id, email (unique, lower), anzeigename, kunde_id? (aus der Antwort auf `konto_angelegt`), erstellt_am |
| `login_token` | id, konto_email, token_hash (SHA-256), code_hash (SHA-256), fehlversuche, laeuft_ab, verwendet_am? |
| `session` | id, konto_id, token_hash, csrf_token, laeuft_ab |
| `anfrage` | id (UUID), typ, konto_id? (ohne Fremdschlüssel, damit `konto_loeschen` das gelöschte Konto überlebt), nutzlast_json, erstellt_am, status (`offen`/`abgeholt`/`beantwortet`), abgeholt_am?, antwort_json?, beantwortet_am? |
| `lesestand` | dokument (PK), version, erzeugt_am, signatur, inhalt_json, empfangen_am |
| `rechnung_link` | id, konto_id, rechnung_nr, token_hash, pdf_pfad, laeuft_ab, abgerufen_am? |
| `webhook_eingang` | id, provider, rohdaten, signatur_header?, empfangen_am, anfrage_id |
| `kanal_kontakt` | eine Zeile: letzter_abruf (für den Hinweis „Hauptsystem gerade nicht erreichbar“) |

Das Login-Token wird an die E-Mail gebunden, nicht an ein Konto. So entsteht das Konto erst nach
dem ersten erfolgreichen Login, und unbekannte Adressen lassen sich nicht von bekannten
unterscheiden.

## 8. Portal: Abläufe

**Anmelden.** `/anmelden` nimmt eine E-Mail entgegen. Das Portal erzeugt ein Token (32 Byte) und
einen 6-stelligen Code, beide 15 min gültig, und verwirft alle älteren Tokens dieser Adresse. Die
Mail enthält Link und Code. Die Antwortseite ist immer dieselbe. Rate-Limit: 5 Anforderungen je IP
in 15 min und 3 je E-Mail in 15 min. Der Code wird mit der E-Mail eingegeben. Nach 5 Fehlversuchen
ist das Token verbraucht. Beim ersten Login ohne Konto leitet das Portal auf `/willkommen` weiter.
Dort gibt der Kunde seinen Anzeigenamen an; das Konto entsteht, und die Anfrage `konto_angelegt`
wird angelegt. Mit `APP_ENV=dev` und ohne SMTP zeigt die Antwortseite Link und Code direkt an.

**Session.** 30 Tage, gleitend verlängert, Cookie `HttpOnly; Secure (per Einstellung); SameSite=Lax`.
Der CSRF-Token steht in der Session und wird als Router-Abhängigkeit für alle POST-Anfragen außer
`/core/*` und `/zahlung/rueckmeldung/*` geprüft.

**Belegung** (`/`). Tagesauswahl vom heutigen Tag bis `fenster_tage`. Je aktivem Feld zeigt die
Seite die Slots des Tages aus `beachhub_shared.slots.slots_fuer_tag`. Ein Slot ist frei, wenn er
nicht mit `belegt` überlappt, nicht vor `jetzt + mindestvorlauf` beginnt und im Fenster liegt.
Angemeldete Kunden mit bekannter Kundengruppe sehen den Preis aus `tarife`. Fehlt der Lesestand
`belegung`, zeigt die Seite „Belegung wird gerade geladen“ statt einer leeren Seite.

**Buchen.** Aus dem Slot gelangt der Kunde auf `/buchen?feld=…&beginn=…`. Die Seite bietet
zusammenhängende Folgeslots zur Verlängerung an, zeigt Preis und Stornofrist und verlangt eine
Anmeldung. Ein POST legt `buchung_anfragen` an und leitet auf `/anfrage/<id>`.
Die Stornofrist (`storno_frist_stunden`) kommt dafür neu in `BelegungInhalt`.

**Warteseite** (`/anfrage/<id>`, nur für das eigene Konto). `warten.js` fragt alle 2 s
`/anfrage/<id>/stand` ab. Ohne JavaScript lädt die Seite per `<meta http-equiv="refresh" content="3">`
neu. Die Antwort `reserviert` führt zur Zahlungsseite (`checkout_url`), `bestaetigt` zu
`/buchungen` mit Erfolgsmeldung, `abgelehnt` zeigt den Grund in Klartext, `fehler` die Bitte, es
später erneut zu versuchen. Ist die Anfrage älter als `antwort_hinweis_sekunden` (aus `belegung`,
Vorgabe 120) und noch offen, erscheint der Hinweis: „Deine Anfrage ist gespeichert. Das
Buchungssystem ist gerade nicht erreichbar; du bekommst die Bestätigung per E-Mail.“

**Zahlung.** `POST /zahlung/rueckmeldung/<provider>` ist ohne Anmeldung und ohne CSRF erreichbar
und nimmt höchstens 64 KB an. Das Portal speichert Rohdaten und den Signatur-Header (bei Stripe
`Stripe-Signature`; weitere Header per Liste in der Konfiguration) in `webhook_eingang`, legt
`zahlung_eingegangen` an und antwortet mit 200. Es prüft nichts. `/zahlung/zurueck?anfrage=<id>`
leitet auf die Warteseite, die nach dem Zahlungseingang `bestaetigt` zeigt.

**Fake-Zahlung** (nur bei `FAKE_ZAHLUNG=true`; mit `APP_ENV=production` verweigert das Portal den
Start): `/test-zahlung/<ref>` zeigt den Betrag sowie „Bezahlen“ und „Abbrechen“. Beide Knöpfe
schicken per POST `{"ref", "ergebnis": "bezahlt" | "abgebrochen"}` an den eigenen Briefkasten
`/zahlung/rueckmeldung/fake` und leiten danach auf `/zahlung/zurueck`.

**Meine Buchungen** (`/buchungen`) aus `konto:<kunde_id>`: kommende Buchungen (PIN groß, Feld,
Zeit, Preis, Status) und darunter vergangene und stornierte. „Stornieren“ öffnet eine
Bestätigungsseite. Sie zeigt, ob das Storno kostenfrei ist (`jetzt < beginn - storno_frist`) oder
der Betrag fällig bleibt (A-STORNO-2). Ein POST legt `buchung_stornieren` an und leitet auf die
Warteseite. Solange `kunde_id` noch fehlt, erscheint „Dein Konto wird gerade eingerichtet“.

**Rechnungen** (`/rechnungen`): Liste aus dem Lesestand. „Herunterladen“ legt `rechnung_anfordern`
an und leitet auf die Warteseite. Bei `ok` schreibt das Portal das PDF nach
`DATA_DIR/rechnungen_tmp/<uuid>.pdf`, erzeugt einen `rechnung_link` (10 min, einmalig) und leitet
dorthin. `/rechnung/<token>` liefert das PDF einmal aus und löscht dann Datei und Link. Ein Job im
Portal (APScheduler, alle 5 min) entfernt abgelaufene Links und Dateien, abgelaufene Login-Tokens
und Sessions sowie beantwortete Anfragen, die älter als 30 Tage sind.

**Konto** (`/konto`): Anzeigename ändern (legt `konto_geaendert` an), Abmelden, Hinweis auf Abos
per E-Mail an die Betreiberadresse (Einstellung `BETREIBER_EMAIL`). Konto löschen führt über eine
Bestätigungsseite; danach legt das Portal `konto_loeschen` an, löscht Konto, Sessions,
Rechnungslinks und den Lesestand `konto:<kunde_id>` sofort und meldet ab. Die Anfrage behält ihre
`konto_id`, über die das Hauptsystem den Kunden findet.

## 9. `shared`

- `beachhub_shared/kanal.py`: `Anfrage`, `AnfrageListe`, `Antwort` (Basis) und die Nutzlast- und
  Antwortschemata je Typ (§ 4), `AntwortListe`, `DokumentListe`, `LesestandErgebnis`.
- `beachhub_shared/lesestand.py`: `BelegungInhalt` bekommt `storno_frist_stunden` und
  `antwort_hinweis_sekunden`. `KontoBuchung.pin` bleibt, wird aber nur für bestätigte Buchungen
  gefüllt.
- Portal und Hauptsystem validieren beide gegen diese Schemata (Vertragstest).

## 10. Fehlerfälle

| Fall | Verhalten |
|---|---|
| Hauptsystem offline | Das Portal sammelt Anfragen und zeigt nach `antwort_hinweis_sekunden` den Hinweis. Belegung und Buchungen zeigt es aus dem letzten Lesestand. |
| Portal offline | Der Abholer macht Backoff. Nach 30 min bekommt der Betreiber einen Alarm. |
| Signatur ungültig | Das Portal antwortet mit 422, das Hauptsystem loggt und alarmiert. Das Dokument wird nicht übernommen. |
| Doppelte Auslieferung | `anfrage_verarbeitet` liefert die gespeicherte Antwort. |
| Doppelte Zahlungsrückmeldung | Idempotent über `zahlung.provider_ref` und deren Status. |
| Gleichzeitige Buchung desselben Slots | Der Exklusionsconstraint greift, die Antwort ist `abgelehnt/belegt`. |
| Zahlung nach Verfall | Guthaben und Mail an den Betreiber. |
| Zahlung für fremde Buchung | Nicht möglich: Die Referenz bestimmt das Hauptsystem, der Anbieter bestätigt den Betrag. |

## 11. Tests

- **Portal (pytest, DB `beachhub_portal_test`):** Login (Code, Link, Ablauf, Fehlversuche, gleiche
  Antwort, Rate-Limit), Session und CSRF, Anlegen, Abholen, erneute Auslieferung und Beantworten von
  Anfragen, Long-Poll-Wartezeit (mit injizierter Uhr und kurzem `warten`), Lesestand (Signatur,
  Version, unbekannter Name), freie Slots und Preise, Buchen bis Warteseite, Storno-Bestätigung
  (kostenfrei ja/nein), Rechnungs-Einmal-Link, Briefkasten, Fake-Zahlungsseite, Kanal-Token, Konto
  löschen.
- **Hauptsystem:** jeder Verarbeiter einschließlich Idempotenz, fremder Buchung, fremder Rechnung,
  `konto_unbekannt`, Guthabenverrechnung (voll/teilweise), Zahlung vor und nach Verfall,
  Verfall-Job mit Rückbuchung, Ausnahme ergibt `fehler`.
- **Ende-zu-Ende:** Portal-App und Hauptsystem im selben Testprozess. Der Kanal-Client des
  Hauptsystems nutzt `httpx.ASGITransport` (bzw. `TestClient`-Transport) auf die Portal-App.
  Ablauf: Login, Konto angelegt, Lesestand da, Buchung, Fake-Zahlung, Buchung bestätigt mit PIN im
  Portal, Storno, Rechnung herunterladen.

## 12. Betrieb

- `portal/Dockerfile`, `portal/docker-compose.yml` (Portal und PostgreSQL), `.env.example`, README
  mit Kurzstart.
- `portal/deploy/Caddyfile`: öffentliche Domain mit automatischem TLS; `/core/*` nur mit
  Client-Zertifikat der internen CA (`client_auth { mode require_and_verify; trust_pool file … }`).
- `core`-CLI `beachhub-core zertifikate` erzeugt die interne CA und ein Client-Zertifikat für das
  Hauptsystem (Portal) sowie eines für die Halle (siehe Hallendienst-Spec), mit `cryptography`.
- `docs/betrieb/portal.md`: Inbetriebnahme, Zertifikate, Token, öffentlicher Schlüssel, Backup
  (täglich, Gruppendaten sind später dort Master).
- Lokale Entwicklung: eigene Datenbanken `beachhub_portal` und `beachhub_portal_test` (Rolle
  `beachhub`), Portal auf Port 8001. Das Hauptsystem spricht es dann ohne mTLS über
  `PORTAL_URL=http://127.0.0.1:8001` an.
