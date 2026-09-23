FROM python:3.12-slim
WORKDIR /app
COPY shared /app/shared
COPY portal /app/portal
RUN pip install --no-cache-dir /app/shared /app/portal \
    && useradd --system --uid 10001 --home /app portal \
    && mkdir -p /app/data && chown portal /app/data
WORKDIR /app/portal
USER portal
# Caddy reicht die Client-IP als X-Forwarded-For durch; der App-Port ist nur an 127.0.0.1 gebunden.
CMD ["sh", "-c", "alembic upgrade head && uvicorn beachhub_portal.main:app --host 0.0.0.0 --port 8001 --proxy-headers --forwarded-allow-ips='*'"]
