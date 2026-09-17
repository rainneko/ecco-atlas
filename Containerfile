# OpenShift/Rahti-compatible image.
# Rahti runs containers as a RANDOM non-root UID that always belongs to GID 0,
# so every path the app writes to must be group-writable by root group, and the
# app must listen on a non-privileged port (>=1024).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    HOME=/app CACHE_DIR=/var/cache/ecco PORT=8080

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        libjpeg62-turbo zlib1g libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY etl/ ./etl/

# Arbitrary-UID support: group 0 owns and can write everything the app touches.
RUN mkdir -p ${CACHE_DIR} \
    && chgrp -R 0 /app ${CACHE_DIR} \
    && chmod -R g=u /app ${CACHE_DIR}

USER 1001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD curl -fsS http://localhost:8080/healthz || exit 1

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers ${WEB_CONCURRENCY:-2} --proxy-headers --forwarded-allow-ips='*'"]
