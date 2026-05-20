"""Pre-scarica il modello XTTS-v2 al build time dell'immagine Docker.

Eseguito da `RUN python3 download_model.py` nel Dockerfile: il modello
(~1.8 GB) finisce in `$TTS_HOME` e viene cotto nell'immagine, cosi' il
cold start del worker RunPod non deve scaricare nulla.
"""
import os

# La licenza Coqui CPML va accettata PRIMA di importare TTS, altrimenti
# il primo accesso al modello chiede [y/n] via input() e fallisce in
# un container non interattivo.
os.environ.setdefault("COQUI_TOS_AGREED", "1")
os.environ.setdefault("TTS_HOME", "/models/tts")

from TTS.api import TTS  # noqa: E402

MODEL_NAME = "tts_models/multilingual/multi-dataset/xtts_v2"

print(f"[download] fetching {MODEL_NAME} ...", flush=True)
TTS(model_name=MODEL_NAME, progress_bar=False)
print("[download] done", flush=True)
