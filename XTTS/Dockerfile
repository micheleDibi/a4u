# RunPod Serverless worker — XTTS-v2 voice cloning TTS su GPU NVIDIA.
#
# Build (anche da Windows/Mac, serve solo Docker — non la GPU):
#   docker build --platform linux/amd64 -t <registry>/a4u-xtts:latest .
#   docker push <registry>/a4u-xtts:latest
#
# Poi creare un endpoint RunPod Serverless da questa immagine (vedi README.md).
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive \
    # Coqui XTTS-v2 richiede l'accettazione della licenza CPML: senza,
    # il primo load chiede [y/n] via input() e in container non-TTY fallisce.
    COQUI_TOS_AGREED=1 \
    # Cache del modello XTTS-v2, cotto nell'immagine al build time.
    TTS_HOME=/models/tts

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# torch + torchaudio build CUDA 12.1 dall'index dedicato (variante GPU).
RUN pip3 install --no-cache-dir \
        "torch>=2.1.0" "torchaudio>=2.1.0" \
        --index-url https://download.pytorch.org/whl/cu121

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Pre-scarica il modello (~1.8 GB) -> cotto nell'immagine, niente
# download al cold start del worker.
COPY download_model.py .
RUN python3 download_model.py

COPY handler.py .

CMD ["python3", "-u", "handler.py"]
