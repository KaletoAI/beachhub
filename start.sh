#!/usr/bin/env bash
# Startet Hauptsystem oder Portal lokal zum Entwickeln (je ein Terminal):
#
#   ./start.sh core      Hauptsystem, Admin-UI unter http://<IP>:8000/admin/login
#   ./start.sh portal    Portal unter http://<IP>:8001
#
# Der Container hat keinen Bildschirm: Beide lauschen auf allen Schnittstellen, und alle
# Adressen, die ein Browser sieht, nutzen die IP des Containers (erste von `hostname -I`,
# abweichend mit ADRESSE=… ./start.sh …). Der Kanal zwischen Hauptsystem und Portal bleibt
# auf 127.0.0.1, weil beide im selben Container laufen.
#
# Vor dem Start bringt das Skript die jeweilige Datenbank auf den neuesten Stand
# (alembic upgrade head). Beim ersten Portal-Start legt es portal/.env an und trägt den
# Kanal (PORTAL_URL, PORTAL_OEFFENTLICHE_URL, KANAL_TOKEN) in core/.env ein; danach das
# Hauptsystem einmal neu starten, damit es den Kanal aufbaut.
#
# Andere Ports: CORE_PORT=… bzw. PORTAL_PORT=… voranstellen.
set -euo pipefail

WURZEL="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$WURZEL/.venv/bin"
HOST="${HOST:-0.0.0.0}"
ADRESSE="${ADRESSE:-$(hostname -I | awk '{print $1}')}"
[ -n "$ADRESSE" ] || ADRESSE="127.0.0.1"
CORE_PORT="${CORE_PORT:-8000}"
PORTAL_PORT="${PORTAL_PORT:-8001}"

fehler() {
    echo "Fehler: $*" >&2
    exit 1
}

[ -x "$VENV/uvicorn" ] || fehler "kein venv unter $WURZEL/.venv (siehe README, Abschnitt Entwicklung)"

zufall() {
    "$VENV/python" -c "import secrets; print(secrets.token_urlsafe(32))"
}

# Setzt SCHLUESSEL=WERT in einer .env-Datei, falls der Schlüssel dort noch fehlt.
ergaenze() {
    local datei="$1" schluessel="$2" wert="$3"
    if ! grep -q "^${schluessel}=" "$datei"; then
        # Ohne abschließenden Zeilenumbruch hinge der neue Eintrag sonst an der letzten Zeile.
        [ -z "$(tail -c 1 "$datei")" ] || echo >>"$datei"
        echo "${schluessel}=${wert}" >>"$datei"
        echo "  ${datei#"$WURZEL"/}: ${schluessel} eingetragen"
    fi
}

portal_einrichten() {
    local core_env="$WURZEL/core/.env" portal_env="$WURZEL/portal/.env"
    [ -f "$core_env" ] || fehler "core/.env fehlt – zuerst das Hauptsystem einrichten (core/README.md)"
    [ -f "$portal_env" ] && return 0

    echo "Erster Portal-Start: richte portal/.env und den Kanal in core/.env ein …"
    local schluessel
    schluessel="$(cd "$WURZEL/core" && "$VENV/python" -c \
        "from beachhub_core.services import lesestand; print(lesestand.oeffentlicher_schluessel())" \
        2>/dev/null)" ||
        fehler "Signaturschlüssel des Hauptsystems fehlt – einmal 'cd core && ../.venv/bin/beachhub-core keygen' ausführen"

    # Ein schon vorhandenes Kanal-Token im Hauptsystem weiterverwenden, sonst ein neues erzeugen.
    local token
    token="$(sed -n 's/^KANAL_TOKEN=//p' "$core_env" | head -n 1)"
    [ -n "$token" ] || token="$(zufall)"

    sed -e "s|^PORTAL_SECRET_KEY=.*|PORTAL_SECRET_KEY=$(zufall)|" \
        -e "s|^PORTAL_BASE_URL=.*|PORTAL_BASE_URL=http://${ADRESSE}:${PORTAL_PORT}|" \
        -e "s|^PORTAL_KANAL_TOKEN=.*|PORTAL_KANAL_TOKEN=${token}|" \
        -e "s|^PORTAL_CORE_PUBLIC_KEY=.*|PORTAL_CORE_PUBLIC_KEY=${schluessel}|" \
        "$WURZEL/portal/.env.example" >"$portal_env"
    echo "  portal/.env angelegt"

    ergaenze "$core_env" PORTAL_URL "http://127.0.0.1:${PORTAL_PORT}"
    # Rückkehradresse nach der Zahlung – sie öffnet der Browser, also die IP des Containers.
    ergaenze "$core_env" PORTAL_OEFFENTLICHE_URL "http://${ADRESSE}:${PORTAL_PORT}"
    ergaenze "$core_env" KANAL_TOKEN "$token"
    echo "Fertig. Läuft das Hauptsystem schon, bitte einmal neu starten (Kanal zum Portal)."
}

case "${1:-}" in
core)
    cd "$WURZEL/core"
    [ -f .env ] || fehler "core/.env fehlt – 'cp core/.env.example core/.env' und anpassen (core/README.md)"
    "$VENV/alembic" upgrade head
    echo "Admin-UI: http://${ADRESSE}:${CORE_PORT}/admin/login"
    exec "$VENV/uvicorn" beachhub_core.main:app --reload --host "$HOST" --port "$CORE_PORT"
    ;;
portal)
    portal_einrichten
    cd "$WURZEL/portal"
    "$VENV/alembic" upgrade head
    echo "Portal: http://${ADRESSE}:${PORTAL_PORT}"
    exec "$VENV/uvicorn" beachhub_portal.main:app --reload --host "$HOST" --port "$PORTAL_PORT"
    ;;
*)
    echo "Aufruf: ./start.sh core | portal" >&2
    exit 2
    ;;
esac
