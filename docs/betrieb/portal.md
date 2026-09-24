# Betriebshandbuch: Beachhub-Buchungsportal

Das Portal läuft als eigener Container (LXC) auf demselben Proxmox-Host wie das Hauptsystem, in
einer eigenen Netzzone hinter Caddy. Es ist aus dem Internet erreichbar; das Hauptsystem nicht.
Alle Verbindungen zwischen beiden baut das Hauptsystem auf (Hauptspec § 2, N-1).

## 1. Voraussetzungen

- Container mit Docker und Compose-Plugin, öffentliche Domain mit DNS-Eintrag auf den Container.
- Firewall: `443/tcp` öffentlich; `8443/tcp` nur für die ausgehende Adresse des Hauptsystems.
- Vom Hauptsystem: öffentlicher Signaturschlüssel (Admin-UI → System) und die interne CA.
- Die öffentliche Caddy-Site begrenzt Request-Bodies auf 1 MB (`portal/deploy/Caddyfile`,
  `request_body { max_size 1MB }`) – reicht für alle Formulare des Portals (Anmeldung, Buchen,
  Storno, Testzahlung).
- **Genau ein Worker.** Rate-Limits für Anmeldeversuche, der Aufräum-Scheduler und der
  Long-Poll-Wecker des Kanals halten ihren Zustand im Prozessspeicher. Das Portal läuft deshalb
  immer mit genau einem uvicorn-Prozess (kein `--workers > 1`, keine zweite Replik derselben App).
  `portal/docker-compose.yml` startet entsprechend nur einen `app`-Dienst. Aus demselben Grund ist
  der App-Port nur an `127.0.0.1` gebunden (`ports: ["127.0.0.1:8001:8001"]`) und Caddy läuft im
  Compose-Netz bzw. per `network_mode: host` über dieselbe Loopback-Adresse: uvicorns
  `--proxy-headers --forwarded-allow-ips` vertraut dem `X-Forwarded-For`-Header nur, weil nur Caddy
  den App-Port erreichen kann – bei einem öffentlich erreichbaren App-Port könnte jede Anfrage ihre
  Client-IP selbst behaupten und damit die Rate-Limits umgehen.

## 2. Zertifikate und Token

Auf dem Hauptsystem (einmalig, erneut zur jährlichen Rotation):

```bash
docker compose -f core/docker-compose.yml exec app beachhub-core zertifikate --ziel /app/data/zertifikate
```

Das legt `ca.crt/ca.key` (bleibt bei Rotation bestehen) sowie `portal-kanal.crt/.key` und
`halle.crt/.key` an. `ca.key` verlässt das Hauptsystem nie.

- Auf das Portal kopieren: nur `ca.crt` nach `portal/data/zertifikate/ca.crt` (Caddy prüft damit).
- Im Hauptsystem eintragen (`core/.env`): `PORTAL_URL=https://<domain>:8443` (mTLS-Kanal, nur für
  das Hauptsystem selbst), `PORTAL_OEFFENTLICHE_URL=https://<domain>` (Adresse, auf die der
  Kunden-Browser nach der Zahlung zurückgeschickt wird), `PORTAL_CLIENT_CERT=/app/data/zertifikate/portal-kanal.crt`,
  `PORTAL_CLIENT_KEY=/app/data/zertifikate/portal-kanal.key`, `PORTAL_CA=` leer lassen (das Portal
  hat ein öffentliches Let's-Encrypt-Zertifikat, keines aus der internen CA – `PORTAL_CA` NICHT auf
  `ca.crt` setzen).
- Einen zufälligen Token erzeugen (`python -c "import secrets; print(secrets.token_urlsafe(32))"`)
  und als `KANAL_TOKEN` (Hauptsystem) und `PORTAL_KANAL_TOKEN` (Portal) eintragen.

**Diese Datei muss vor dem ersten `docker compose up` in `portal/data/zertifikate/` liegen**
(`docker-compose.yml` bindet `./data/zertifikate/ca.crt` einzeln in den Caddy-Container ein; fehlt
die Datei, legt Docker an ihrer Stelle ein leeres Verzeichnis an, und Caddy startet nicht).

`ca.crt` ist derselbe Vertrauensanker für beide mTLS-Kanäle des Hauptsystems: das Portal auf
`:8443` (dieses Dokument) und – sobald angebunden – den Hallendienst auf `:8444`
(`docs/betrieb/hallendienst.md`). `portal-kanal.crt` und `halle.crt` sind zwei verschiedene, von
derselben CA signierte Client-Zertifikate; die eigentliche Trennung „wer darf was“ übernehmen
nicht die Zertifikate (beide würden bei jedem der beiden Kanäle als gültig durchgehen), sondern
die unterschiedlichen Ports und die je Kanal eigenen Token (`KANAL_TOKEN`/`PORTAL_KANAL_TOKEN` vs.
`HALL_TOKEN`).

**Rotation:**

- **Client-Zertifikate** (`portal-kanal.crt`) laufen nach einem Jahr ab. Der obige Befehl erneut
  ausgeführt stellt ein neues Zertifikat aus den vorhandenen Schlüsseln aus (ohne `--name` auch ein
  neues `halle.crt`, das dann auf den Hallenrechner muss; mit `--name portal-kanal` nur dieses) – die Schlüsseldatei
  (`portal-kanal.key`) bleibt dabei unverändert bestehen, nur `portal-kanal.crt` wird ersetzt.
  Danach `ca.crt` nicht erneut kopieren (unverändert). Der Kanal-Client im Hauptsystem lädt
  Zertifikat und Schlüssel nur beim Start (`kanal.baue_client()`) und bemerkt eine ausgetauschte
  Datei auf der Platte nicht von selbst – **das Hauptsystem nach jedem `zertifikate`-Aufruf neu
  starten** (`docker compose restart app`), sonst verbindet es sich weiter mit dem alten,
  demnächst abgelaufenen Zertifikat.
- **Schlüsselrotation** (z. B. bei Verdacht auf Kompromittierung): vor dem erneuten Aufruf
  `portal-kanal.key` (und `portal-kanal.crt`) auf dem Hauptsystem löschen – der Befehl legt dann ein
  neues Schlüsselpaar samt Zertifikat an; danach ebenfalls das Hauptsystem neu starten.
- **Ablauf der internen CA** (`ca.crt`, gültig 5 Jahre; eine Warnung erscheint als Log-Ausgabe des
  `zertifikate`-Befehls selbst, sobald die Restlaufzeit unter einem Jahr liegt – nicht im
  laufenden Betrieb des Hauptsystems): `ca.crt` und `ca.key` gemeinsam entfernen und den Befehl
  erneut ausführen (erzeugt eine neue CA und neue Client-Zertifikate für Portal **und** Halle).
  Danach: die neue `ca.crt` erneut auf das Portal kopieren und dessen Caddy neu laden
  (`docker compose restart caddy`), das Hauptsystem neu starten (Kanal zum Portal), und – falls der
  Hallendienst bereits angebunden ist – dessen Caddy-Site (`:8444`) ebenfalls neu laden sowie
  `halle.crt`/`halle.key` neu auf den Hallenrechner kopieren (`docs/betrieb/hallendienst.md`,
  Abschnitt 2).

## 3. Installation

```bash
cd /opt/beachhub/portal
cp .env.example .env    # alle Werte setzen, PORTAL_APP_ENV=production, PORTAL_COOKIE_SECURE=true,
                        # PORTAL_FAKE_ZAHLUNG=false, PORTAL_SECRET_KEY zufällig,
                        # PORTAL_BASE_URL=https://<domain> (die öffentliche Adresse des Portals
                        # selbst – nicht zu verwechseln mit PORTAL_URL/PORTAL_OEFFENTLICHE_URL im
                        # Hauptsystem, Abschnitt 2)
mkdir -p data && sudo chown 10001:10001 data   # uid des App-Containers (Dockerfile: USER portal)
docker compose up -d --build
```

Der `app`-Container läuft als Nutzer `portal` (uid 10001, kein Root); das Bind-Mount `./data:/app/data`
überdeckt beim Start die Rechte, die das `chown` im Image beim Bauen gesetzt hat, mit denen des
Host-Verzeichnisses. Ohne den `chown`-Schritt oben kann die App weder Rechnungs-Einmal-Links
(`rechnungen_tmp/`) noch sonst etwas unter `DATA_DIR` schreiben.

Mit `PORTAL_APP_ENV=production` verweigert das Portal den Start, solange Secret, Token,
öffentlicher Schlüssel, `COOKIE_SECURE`, `BASE_URL` (muss `https://` sein und darf nicht auf
`localhost`/`127.0.0.1` zeigen), `SMTP_HOST` (ohne Mailserver kommt kein Anmeldecode an) oder die
Testzahlung nicht stimmen. Das Hauptsystem verweigert den
Start mit gesetzter `PORTAL_URL`, solange `ZAHLUNG_PROVIDER=fake` ist – der echte Anbieter (Stripe
oder Mollie) folgt nach der Entscheidung Ⓞ-13.

Die Testdatenbank `beachhub_portal_test` legt `portal/deploy/init-test-db.sql` (per
Compose-Init-Skript bzw. im CI-Schritt) explizit mit `TEMPLATE template0 ENCODING 'UTF8'` an; ein
einfaches `CREATE DATABASE … OWNER beachhub` aus `template1` liefert auf manchen Systemen
`SQL_ASCII`, woran psycopg3 beim ersten Connect abstürzt. Die eigentliche Anwendungsdatenbank
`beachhub_portal` entsteht beim ersten Start des `db`-Containers direkt über `POSTGRES_DB` (Postgres'
eigenes `initdb`, nicht über ein `CREATE DATABASE`) und ist davon nicht betroffen; wird sie
stattdessen von Hand angelegt (z. B. bei einer host-installierten PostgreSQL), gilt dieselbe
Regel (`createdb -O beachhub --template=template0 --encoding=UTF8 beachhub_portal`, siehe
`portal/README.md`).

## 4. Prüfen

- `https://<domain>/health` → `{"status":"ok"}`
- `https://<domain>/core/anfragen` → 404 (öffentlich gesperrt)
- `curl https://<domain>:8443/core/anfragen` ohne Zertifikat → TLS-Fehler
- Im Log des Hauptsystems erscheinen nach dem Start keine Warnungen „Portal nicht erreichbar“.
- Admin-UI → System: Knopf „Lesestand jetzt erzeugen“; danach zeigt `https://<domain>/` die
  Belegung.

## 5. Backup

Das Portal hält Konten, Anfragen und (ab Stufe 4) die Gruppenverwaltung, für die es Master ist.
Täglich sichern:

```bash
docker compose exec -T db pg_dump -U beachhub -Fc beachhub_portal | gpg --encrypt -r <betreiber> \
  > /var/backups/beachhub/portal-$(date +%F).dump.gpg
```

`DATA_DIR` (`portal/data/`) wird bewusst **nicht** gesichert: `rechnungen_tmp` enthält nur die
10-Minuten-Kopie einer Rechnung für den Einmal-Link (A-RECH-5, ohnehin flüchtig) und
`zertifikate/ca.crt` ist nur eine Kopie des Vertrauensankers vom Hauptsystem – beides lässt sich
jederzeit neu erzeugen bzw. erneut kopieren.

Nach einer Wiederherstellung schickt das Hauptsystem fehlende Lesestände beim nächsten Abgleich
von selbst (spätestens nach einer Stunde, sofort nach einem Neustart des Hauptsystems).

## 6. Störungen

| Anzeichen | Ursache und Abhilfe |
|---|---|
| Kunden sehen „Anfrage gespeichert, Bestätigung folgt per E-Mail“ | Hauptsystem holt nicht ab: WireGuard/Netz, Zertifikat abgelaufen, `KANAL_TOKEN` ungleich. Nach 30 min bekommt der Betreiber eine Mail. |
| Mail „Lesestand vom Portal abgelehnt“ | `PORTAL_CORE_PUBLIC_KEY` passt nicht zum Schlüssel des Hauptsystems. |
| Belegung leer mit „wird gerade geladen“ | Noch kein Lesestand angekommen: Admin-UI → System → „Lesestand jetzt erzeugen“ (Häkchen „Belegung und Tarife auch ohne Änderung neu erzeugen“ setzen). |
