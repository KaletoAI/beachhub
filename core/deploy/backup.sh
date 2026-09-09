#!/usr/bin/env bash
# Tägliches verschlüsseltes Backup: Datenbank-Dump + data/ (Rechnungs-PDFs, Lesestand, Signaturschlüssel)
#
# Entspricht einem auf der VM in Docker laufenden Postgres (siehe docker-compose.yml).
# Läuft Postgres stattdessen als Host-Installation, überschreibe PG_DUMP_CMD, z. B.:
#   PG_DUMP_CMD="pg_dump -U beachhub beachhub"
#
# Einmalige Voraussetzungen (siehe docs/betrieb/hauptsystem.md, Abschnitt 1/6):
# - öffentlicher GPG-Schlüssel des Betreibers auf der VM importiert (gpg --import betreiber.pub)
# - SSH-Schlüssel für den Backup-Host hinterlegt (ssh-copy-id) und dessen Host-Key einmalig akzeptiert
set -euo pipefail
cd "$(dirname "$0")/.."
ZIEL=${BACKUP_ZIEL:?z.B. user@backup-host:/srv/beachhub}
GPG_EMPFAENGER=${BACKUP_GPG:?GPG-Key-ID des Betreibers}
PG_DUMP_CMD=${PG_DUMP_CMD:-"docker compose exec -T db pg_dump -U beachhub beachhub"}
STAMP=$(date +%Y%m%d-%H%M)
TMP=$(mktemp -d)
OUT=$(mktemp -d)
trap 'rm -rf "$TMP" "$OUT"' EXIT
chmod 700 "$TMP" "$OUT"
eval "$PG_DUMP_CMD" | gzip > "$TMP/db-$STAMP.sql.gz"
tar czf "$TMP/data-$STAMP.tgz" data
tar cf - -C "$TMP" "db-$STAMP.sql.gz" "data-$STAMP.tgz" \
  | gpg --batch --yes --trust-model always --encrypt --recipient "$GPG_EMPFAENGER" > "$OUT/beachhub-$STAMP.tar.gpg"
scp -o BatchMode=yes "$OUT/beachhub-$STAMP.tar.gpg" "$ZIEL/"
echo "Backup $STAMP übertragen"
