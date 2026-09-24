# Multi-Arch (arm64/amd64): docker buildx build --platform linux/arm64,linux/amd64 -f hall/Dockerfile .
FROM python:3.12-slim
# /config nimmt nur den Mountpoint für die schreibgeschützt eingebundene hall.toml auf
# (docker-compose.yml: "./hall.toml:/config/hall.toml:ro"); chown auf halle, damit der Zugriff
# nicht zufällig von den Standardrechten des mkdir abhängt.
RUN useradd --create-home --uid 1000 halle && mkdir -p /data /config && chown halle /data /config
WORKDIR /app
COPY shared /app/shared
COPY hall /app/hall
RUN pip install --no-cache-dir /app/shared /app/hall
USER halle
ENV DATA_DIR=/data HALL_TOML=/config/hall.toml
VOLUME ["/data"]
# Geordnetes Herunterfahren: beachhub-hall fängt SIGTERM selbst ab (siehe
# beachhub_hall.__main__._laufen_bis_signal) und schaltet dabei u. a. einen offenen
# switch.*-Türöffner aus, bevor der Prozess endet. STOPSIGNAL ist ohnehin die Vorgabe von Debian
# Slim, hier zur Klarheit explizit gesetzt.
STOPSIGNAL SIGTERM
# Prüft immer 127.0.0.1, unabhängig von HEALTH_HOST: der Dienst lauscht im Container per
# Vorgabe nur dort (siehe .env.example), auf 0.0.0.0 zusätzlich auch auf 127.0.0.1 erreichbar.
# Den Port liest die Prüfung wie der Dienst aus HEALTH_PORT (Vorgabe 8099) – sonst schlüge sie nach
# einer Änderung des Ports in .env dauerhaft fehl.
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import os,sys,urllib.request; port = os.environ.get('HEALTH_PORT') or '8099'; sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=4).status == 200 else 1)"
CMD ["beachhub-hall", "start"]
