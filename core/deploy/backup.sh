#!/usr/bin/env bash
# Tägliches verschlüsseltes Backup: Datenbank-Dump + data/ (Rechnungs-PDFs, Lesestand, Signaturschlüssel)
#
# Entspricht einem auf der VM in Docker laufenden Postgres (siehe docker-compose.yml).
# Läuft Postgres stattdessen als Host-Installation, ersetze die pg_dump-Zeile unten durch:
#   pg_dump -U beachhub beachhub | gzip > "$TMP/db-$STAMP.sql.gz"
set -euo pipefail
ZIEL=${BACKUP_ZIEL:?z.B. user@backup-host:/srv/beachhub}
GPG_EMPFAENGER=${BACKUP_GPG:?GPG-Key-ID des Betreibers}
STAMP=$(date +%Y%m%d-%H%M)
TMP=$(mktemp -d)
docker compose exec -T db pg_dump -U beachhub beachhub | gzip > "$TMP/db-$STAMP.sql.gz"
tar czf "$TMP/data-$STAMP.tgz" -C "$(dirname "$0")/.." data
tar cf - -C "$TMP" . | gpg --encrypt --recipient "$GPG_EMPFAENGER" > "$TMP/beachhub-$STAMP.tar.gpg"
scp "$TMP/beachhub-$STAMP.tar.gpg" "$ZIEL/"
rm -rf "$TMP"
echo "Backup $STAMP übertragen"
