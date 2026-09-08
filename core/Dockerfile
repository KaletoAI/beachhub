FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libffi8 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY shared /app/shared
COPY core /app/core
RUN pip install --no-cache-dir /app/shared /app/core
WORKDIR /app/core
CMD ["sh", "-c", "alembic upgrade head && uvicorn beachhub_core.main:app --host 0.0.0.0 --port 8000"]
