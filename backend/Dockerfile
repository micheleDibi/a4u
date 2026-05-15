# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS builder
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /build
# Hatchling (build backend) richiede sia pyproject.toml che il package `app/`.
COPY pyproject.toml ./
COPY app ./app
# Pre-installa torch + torchaudio CPU-only (~200 MB invece di ~2 GB CUDA).
# Per server CON GPU CUDA, sostituire l'index URL con il default pip o
# `https://download.pytorch.org/whl/cu121` (CUDA 12.1).
# Poi installa il pacchetto + extra `[video]` (coqui-tts pesa ~300 MB con
# le sue deps gruut/spacy/etc.). torch è già lì → pip lo lascia stare.
RUN pip install --upgrade pip \
    && pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
        "torch>=2.1.0" "torchaudio>=2.1.0" \
    && pip install --no-cache-dir ".[video]"

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    # === Cache dir scrivibili per stack TTS/scientific Python ===
    # Numba (JIT di librosa, dep di Coqui-TTS) di default scrive il cache
    # delle funzioni dentro site-packages/, che è read-only per l'utente
    # non-root → "no locator available for file". Reindirizziamo a /tmp.
    NUMBA_CACHE_DIR=/tmp/numba_cache \
    # MPLCONFIGDIR evita warning di matplotlib su HOME non writable.
    MPLCONFIGDIR=/tmp/matplotlib \
    # HOME=/app (default per useradd -d /app) — TTS scarica il modello
    # XTTS-v2 (~1.8 GB) in $TTS_HOME se settato, altrimenti in
    # $HOME/.local/share/tts. Esplicitiamo TTS_HOME su una subdir writable
    # del volume /app/uploads così sopravvive al restart del container.
    HOME=/app \
    TTS_HOME=/app/.tts_cache \
    XDG_CACHE_HOME=/tmp/cache \
    # Accetta automaticamente la licenza Coqui Public Model License (CPML)
    # per XTTS-v2 — senza, al primo download il modello chiede [y/n] via
    # input() che in container non-TTY restituisce EOF e fa fallire il job.
    # CPML = uso non-commerciale gratuito. Per uso commerciale: licensing@coqui.ai.
    # Vedi: https://coqui.ai/cpml
    COQUI_TOS_AGREED=1

# Runtime deps in due gruppi:
#  - WeasyPrint (Pango, GDK-Pixbuf, HarfBuzz, libffi, fonts) per il
#    rendering finale del PDF.
#  - Chromium di Playwright (libnss, libgbm, libasound, ecc.) per il
#    pre-render dei diagrammi Mermaid in SVG.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 curl \
        # WeasyPrint
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b \
        libgdk-pixbuf-2.0-0 libffi8 \
        fonts-dejavu-core fonts-liberation \
        # Playwright Chromium
        libnss3 libnspr4 libgbm1 libasound2 \
        libxshmfence1 libxrandr2 libxkbcommon0 \
        libxcomposite1 libxdamage1 libxfixes3 libxext6 \
        libcups2 libatk1.0-0 libatk-bridge2.0-0 libatspi2.0-0 \
        libdrm2 libxss1 \
        # Video generation (Fase 6 §9): ffmpeg per encoding MP4
        # + libsndfile1 per soundfile (lettura/scrittura WAV).
        ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r app && useradd -r -g app -d /app app

WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Scarica il browser Chromium di Playwright in /ms-playwright (path
# condiviso tra utenti, settato via env var sopra). Eseguito come root
# perché playwright richiede di scrivere lì; chown a `app` dopo per
# permessi di lettura runtime.
RUN python -m playwright install chromium \
    && chown -R app:app /ms-playwright

COPY --chown=app:app . /app
RUN mkdir -p /app/uploads/organizations /app/uploads/avatars /app/uploads/templates \
             /app/uploads/courses /app/uploads/lesson_assets /app/uploads/lesson_videos \
             /app/generated_pdfs \
             /app/.tts_cache \
             /tmp/numba_cache /tmp/matplotlib /tmp/cache \
    && chown -R app:app /app/uploads /app/generated_pdfs /app/.tts_cache \
                       /tmp/numba_cache /tmp/matplotlib /tmp/cache
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -fsS http://localhost:8000/api/v1/system/health || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
