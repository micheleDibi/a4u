# syntax=docker/dockerfile:1.7

# Tag Debian fisso (trixie): il tag mobile `python:3.12-slim` cambia
# distribuzione senza preavviso.
FROM python:3.12-slim-trixie AS builder
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /build
# Hatchling (build backend) richiede sia pyproject.toml che il package `app/`.
COPY pyproject.toml ./
COPY app ./app
# Il TTS XTTS-v2 gira su RunPod Serverless (cartella `XTTS/` del repo):
# il backend non ha coqui. torch serve solo al layout di Docling per le
# figure di fonte (extra `figures`, docs/courses/18-literature-figures.md):
# torch/torchvision CPU dall'indice di PyTorch PRIMA dell'extra, altrimenti
# su x86_64 pip porta le ruote CUDA (GB inutili).
ARG TORCH_VERSION=2.14.0
ARG TORCHVISION_VERSION=0.29.0
RUN pip install --upgrade pip \
    && pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
        "torch==${TORCH_VERSION}" "torchvision==${TORCHVISION_VERSION}" \
    && pip install --no-cache-dir ".[figures]"
# Modello di layout di Docling (heron, variante torch) scaricato in build:
# a runtime HF_HUB_OFFLINE=1, nessun download. La variante ONNX non serve.
RUN python -c "from pathlib import Path; from docling.utils.model_downloader import download_models; download_models(output_dir=Path('/opt/docling-models'), with_layout=True, with_tableformer=False, with_code_formula=False, with_picture_classifier=False, with_rapidocr=False, with_easyocr=False)" \
    && rm -rf /opt/docling-models/*-onnx

FROM python:3.12-slim-trixie AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    # Docling (figure di fonte) legge i modelli da FIGURE_DOCLING_ARTIFACTS_PATH
    # e non scarica nulla a runtime.
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    # HOME=/app (default per useradd -d /app).
    HOME=/app \
    XDG_CACHE_HOME=/tmp/cache \
    # matplotlib (figure `function`): cache dei font in una directory
    # scrivibile dall'utente `app` e backend Agg senza display.
    MPLCONFIGDIR=/tmp/cache/matplotlib \
    MPLBACKEND=Agg

# Runtime deps in due gruppi:
#  - WeasyPrint (Pango, GDK-Pixbuf, HarfBuzz, libffi, fonts) per il
#    rendering finale del PDF.
#  - Chromium di Playwright (libnss, libgbm, libasound, ecc.) per il
#    pre-render dei diagrammi Mermaid in SVG.
#  - Graphviz (`dot`) per il render server-side delle figure DOT.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 curl graphviz \
        # WeasyPrint
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b \
        libgdk-pixbuf-2.0-0 libffi8 \
        # Font: latini (DejaVu/Liberation) + Noto per ampia copertura Unicode.
        # fonts-noto-cjk → giapponese/cinese/coreano; fonts-noto-core →
        # cirillico, greco, arabo, ebraico, devanagari, thai, ecc. Necessari sia
        # per WeasyPrint (PDF) sia per il Chromium di Playwright (frame
        # slide→video e label Mermaid) dei corsi in lingue non-latine, altrimenti
        # il testo viene reso come quadratini ("tofu").
        fonts-dejavu-core fonts-liberation fonts-noto-core fonts-noto-cjk \
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
COPY --from=builder /opt/docling-models /opt/docling-models

# Scarica il browser Chromium di Playwright in /ms-playwright (path
# condiviso tra utenti, settato via env var sopra). Eseguito come root
# perché playwright richiede di scrivere lì; chown a `app` dopo per
# permessi di lettura runtime.
RUN python -m playwright install chromium \
    && chown -R app:app /ms-playwright

COPY --chown=app:app . /app
RUN mkdir -p /app/uploads/organizations /app/uploads/avatars /app/uploads/templates \
             /app/uploads/courses /app/uploads/lesson_assets /app/uploads/lesson_videos \
             /app/generated_pdfs /tmp/cache /tmp/cache/matplotlib \
    && chown -R app:app /app/uploads /app/generated_pdfs /tmp/cache
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -fsS http://localhost:8000/api/v1/system/health || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]

# Stage `test`: i test che richiedono Docling (e le guardie sui pacchetti di
# sistema) girano nell'immagine reale, senza skip ambientali:
#   docker build --target test -t a4u-backend:test backend
#   docker run --rm -e A4U_REQUIRED_DEPS=docling a4u-backend:test \
#       python -m pytest tests/test_source_figure_extraction.py tests/test_no_agpl_dependencies.py -q
FROM runtime AS test
USER root
RUN pip install --no-cache-dir "pytest>=8.3.0" "pytest-asyncio>=0.24.0"
USER app

# Ultimo stage = immagine di produzione (docker compose non indica `target`).
FROM runtime
