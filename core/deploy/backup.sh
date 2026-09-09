#!/usr/bin/env bash
# Tägliches verschlüsseltes Backup: Datenbank-Dump + data/ (Rechnungs-PDFs, Lesestand, Signaturschlüssel)
#
# Entspricht einem auf der VM in Docker laufenden Postgres (siehe docker-compose.yml).
# Läuft Postgres stattdessen als Host-Installation, ersetze die pg_dump-Zeile unten durch:
#   pg_dump -U beachhub beachhub | gzip > "$TMP/db-$STAMP.sql.gz"
#
# Einmalige Voraussetzungen (siehe docs/betrieb/hauptsystem.md, Abschnitt 1/6):
# - öffentlicher GPG-Schlüssel des Betreibers auf der VM importiert (gpg --import betreiber.pub)
# - SSH-Schlüssel für den Backup-Host hinterlegt (ssh-copy-id) und dessen Host-Key einmalig akzeptiert
set -euo pipefail
ZIEL=${BACKUP_ZIEL:?z.B. user@backup-host:/srv/beachhub}
GPG_EMPFAENGER=${BACKUP_GPG:?GPG-Key-ID des Betreibers}
STAMP=$(date +%Y%m%d-%H%M)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
chmod 700 "$TMP"
docker compose exec -T db pg_dump -U beachhub beachhub | gzip > "$TMP/db-$STAMP.sql.gz"
tar czf "$TMP/data-$STAMP.tgz" -C "$(dirname "$0")/.." data
tar cf - -C "$TMP" . | gpg --batch --yes --trust-model always --encrypt --recipient "$GPG_EMPFAENGER" > "$TMP/beachhub-$STAMP.tar.gpg"
scp -o BatchMode=yes "$TMP/beachhub-$STAMP.tar.gpg" "$ZIEL/"
echo "Backup $STAMP übertragen"
