FROM python:3.12-slim
WORKDIR /app
COPY shared /app/shared
COPY portal /app/portal
RUN pip install --no-cache-dir /app/shared /app/portal \
    && groupadd --system --gid 10001 portal \
    && useradd --system --uid 10001 --gid 10001 --home /app portal \
    && mkdir -p /app/data && chown portal:portal /app/data
WORKDIR /app/portal
USER portal
# Caddy reicht die Client-IP als X-Forwarded-For durch; der App-Port ist nur an 127.0.0.1 gebunden.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/health').status == 200 else 1)"
CMD ["sh", "-c", "alembic upgrade head && uvicorn beachhub_portal.main:app --host 0.0.0.0 --port 8001 --proxy-headers --forwarded-allow-ips='*'"]
