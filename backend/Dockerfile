# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS builder
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /build
# Hatchling (build backend) richiede sia pyproject.toml che il package `app/`.
COPY pyproject.toml ./
COPY app ./app
RUN pip install --upgrade pip && pip install --no-cache-dir .

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# WeasyPrint runtime deps: Pango per il text rendering, GDK-Pixbuf per
# le immagini, libffi per cffi. Senza questi `import weasyprint`
# fallisce per `cannot load library libpango-1.0-0`.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 curl \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b \
        libgdk-pixbuf-2.0-0 libffi8 \
        fonts-dejavu-core fonts-liberation \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r app && useradd -r -g app -d /app app
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --chown=app:app . /app
RUN mkdir -p /app/uploads/organizations /app/uploads/avatars /app/uploads/templates \
    && chown -R app:app /app/uploads
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -fsS http://localhost:8000/api/v1/system/health || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
