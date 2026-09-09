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
  des Hauptsystems (weder `8000` noch `8443`) wird öffentlich freigegeben.
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
```

`keygen` gibt den öffentlichen Signaturschlüssel (Hex) aus – diesen Wert notieren/sichern, er wird
gebraucht, um die Signatur des Lesestands später extern zu prüfen (siehe N-2 in der Spezifikation).
`create-admin` fragt interaktiv nach einem Passwort (mindestens 12 Zeichen) und gibt danach das
TOTP-Secret sowie eine `otpauth://`-URI aus, die der Betreiber mit einer Authenticator-App scannt.

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
3. **Kundengruppen** anlegen (z. B. Vereinsmitglieder, Externe).
4. **Tarife** je Kundengruppe und Zeitfenster hinterlegen.
5. **Konfiguration** prüfen/anpassen, u. a. `rechnung_tag_im_folgemonat` (Standard: 3 – Tag im
   Folgemonat, ab dem Sammelrechnungen für den Vormonat erzeugt werden) und
   `rechnung_zahlungsziel_tage` (Standard: 14 – Zahlungsziel ab Rechnungsdatum).
6. Einen **Testkunden** anlegen.
7. Eine **Testbuchung** für den Testkunden durchführen und die Bestätigungsmail (PIN) prüfen.
8. Über den Monatslauf oder manuell eine **Testrechnung** erzeugen und das PDF prüfen.
9. Testkunde, Testbuchung und Testrechnung wieder entfernen bzw. stornieren, damit sie nicht in
   den echten Betrieb einfließen; falls die interne Uhr für Tests verstellt wurde, **Uhr
   zurücksetzen** (nur im Entwicklungsmodus verfügbar – im Produktivbetrieb nicht vorhanden).

## 5. Laufender Betrieb

Der Monatslauf (Erzeugung der Sammelrechnungen für den Vormonat) läuft automatisch: Der
APScheduler-Job prüft **täglich um 06:00 Uhr**, ob der aktuelle Tag den in der Konfiguration
hinterlegten Wert `rechnung_tag_im_folgemonat` erreicht oder überschritten hat, und erzeugt dann
die fehlenden Sammelrechnungen für den Vormonat. Das Zahlungsziel jeder Rechnung ergibt sich aus
`rechnung_zahlungsziel_tage` ab Rechnungsdatum.

Der Monatslauf kann bei Bedarf auch manuell angestoßen werden (z. B. Nachlauf nach einem Ausfall):

```bash
docker compose run --rm app beachhub-core monatslauf 2026-08
```

Rechnungen sind im Admin-UI unter „Rechnungen“ einsehbar; von dort steht auch ein
**CSV-Export** (`/rechnungen/export.csv`) für den Steuerberater zur Verfügung, gefiltert nach
Zeitraum.

Logs der laufenden Anwendung:

```bash
docker compose logs -f app
```

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

## 8. Störungen

Die Compose-Services `db`, `app` und `caddy` sind mit `restart: unless-stopped` konfiguriert und
starten nach einem VM-Reboot oder einem Absturz automatisch neu, sobald der Docker-Daemon läuft.

**Startreihenfolge nach einem Reboot**: Caddy lauscht ausschließlich auf der WireGuard-Adresse
(`10.8.0.1:8443`, `network_mode: host`). Startet Docker vor WireGuard, versucht Caddy auf eine zu
diesem Zeitpunkt noch nicht existierende Adresse zu binden und schlägt fehl. Damit
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
