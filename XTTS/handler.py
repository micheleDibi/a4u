"""RunPod Serverless handler — XTTS-v2 voice cloning TTS su GPU.

Porting fedele dello script di riferimento `batch_generate.py`:
- `temperature=0.65`, `enable_text_splitting=False`
- chunking a 180 caratteri con rstrip della punteggiatura finale
- 250 ms di silenzio tra un chunk e l'altro
- `get_conditioning_latents` con i parametri presi dal config del modello
- output FLAC lossless @ 24000 Hz mono

NESSUNA modifica al modello (no deepspeed, no fp16): l'unico cambiamento
rispetto alla versione CPU e' il device `cuda`.

Schema input del job:
    {"input": {
        "language_code": "it",
        "voice_sample_url": "<URL pubblico del file audio di riferimento>",
        "segments": [{"segment_id": "...", "text": "..."}]
    }}
(`voice_sample_b64` inline e' ancora accettato come fallback.)

Output — un `yield` per CHUNK audio (~12s, payload piccolo che lo
stream di RunPod consegna in modo affidabile; un intero segmento come
blob unico sforerebbe i limiti dello stream e verrebbe perso). Il
client ricompone i chunk per `segment_id`, ordinati per `chunk_index`:
    {"segment_id": "...", "chunk_index": 0, "chunk_total": 4,
     "audio_b64": "<FLAC base64>", "sample_rate": 24000}

Errore fatale:
    {"error": "messaggio"}
"""
from __future__ import annotations

import base64
import io
import os
import re
import subprocess
import tempfile
import traceback
import urllib.request

# La licenza Coqui CPML va accettata PRIMA di importare TTS.
os.environ.setdefault("COQUI_TOS_AGREED", "1")
os.environ.setdefault("TTS_HOME", "/models/tts")

import numpy as np  # noqa: E402
import runpod  # noqa: E402
import soundfile as sf  # noqa: E402
import torch  # noqa: E402
from TTS.api import TTS  # noqa: E402

# ---------------------------------------------------------------------------
# Costanti — allineate 1:1 a batch_generate.py
# ---------------------------------------------------------------------------

MODEL_NAME = "tts_models/multilingual/multi-dataset/xtts_v2"
SAMPLE_RATE = 24000
MAX_CHARS = 180
SILENCE_MS = 250

# 16 lingue supportate da XTTS-v2 (come SUPPORTED_LANGUAGES di clone_voice.py).
XTTS_SUPPORTED_LANGUAGES = frozenset(
    {
        "it", "en", "es", "fr", "de", "pt", "pl", "tr",
        "ru", "nl", "cs", "ar", "zh-cn", "ja", "hu", "ko",
    }
)

_STRONG_SPLIT_RE = re.compile(r"(?<=[.!?:])\s+")
_SOFT_SPLIT_RE = re.compile(r"(?<=,)\s+")
_CHUNK_TRIM_CHARS = ".:;,"


def normalize_language_code(language_code: str) -> str:
    """Normalizza un codice lingua per XTTS-v2 (lowercase, no country
    code, `zh*` -> `zh-cn`, fallback `it`)."""
    code = (language_code or "it").strip().lower()
    if code.startswith("zh"):
        return "zh-cn"
    if "-" in code:
        code = code.split("-")[0]
    if code not in XTTS_SUPPORTED_LANGUAGES:
        return "it"
    return code


def split_into_chunks(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    """Divide il testo in chunk <= `max_chars` rispettando i terminatori
    forti (.!?:); per chunk troppo lunghi, split ulteriore su `,`.

    Identico a `chunk_text()` di `batch_generate.py`. Rimuove la
    punteggiatura finale di ogni chunk (anti-"punto" del normalizer XTTS).
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return []

    parts = _STRONG_SPLIT_RE.split(cleaned)
    out: list[str] = []
    for p in parts:
        p = p.strip().rstrip(_CHUNK_TRIM_CHARS)
        if not p:
            continue
        if len(p) <= max_chars:
            out.append(p)
            continue
        sub = _SOFT_SPLIT_RE.split(p)
        buf = ""
        for s in sub:
            s = s.strip().rstrip(_CHUNK_TRIM_CHARS)
            if not s:
                continue
            if buf and len(buf) + 1 + len(s) > max_chars:
                out.append(buf)
                buf = s
            else:
                buf = f"{buf} {s}".strip() if buf else s
        if buf:
            out.append(buf)
    return out


# ---------------------------------------------------------------------------
# Caricamento modello — una sola volta per worker (cold start)
# ---------------------------------------------------------------------------

_DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
print(f"[xtts] loading {MODEL_NAME} on {_DEVICE} ...", flush=True)
_tts = TTS(model_name=MODEL_NAME, progress_bar=False).to(_DEVICE)
_model = _tts.synthesizer.tts_model
_config = _model.config
print(f"[xtts] model ready on {_DEVICE}", flush=True)


# ---------------------------------------------------------------------------
# Sintesi
# ---------------------------------------------------------------------------


def _ffmpeg_to_wav(src_path: str) -> str:
    """Normalizza qualunque formato audio a WAV mono 24kHz via ffmpeg.

    Garantisce che `get_conditioning_latents` legga il sample a
    prescindere dal formato di upload (webm da MediaRecorder, mp3, ogg,
    wav, ...). Non altera il modello: e' solo decodifica del contenitore.
    """
    dst_path = f"{src_path}.norm.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-i", src_path, "-ac", "1", "-ar", str(SAMPLE_RATE), dst_path],
        check=True,
        capture_output=True,
    )
    return dst_path


def _infer_chunk(text: str, language: str, gpt_cond_latent, speaker_embedding) -> np.ndarray:
    """Inference di un singolo chunk. Parametri 1:1 con batch_generate.py:
    `temperature=0.65`, `enable_text_splitting=False`, nessun override di
    length/repetition/top_k/top_p/speed (default Coqui)."""
    text = (text or "").strip().rstrip(_CHUNK_TRIM_CHARS)
    out = _model.inference(
        text=text,
        language=language,
        gpt_cond_latent=gpt_cond_latent,
        speaker_embedding=speaker_embedding,
        temperature=0.65,
        enable_text_splitting=False,
    )
    wav = out.get("wav") if isinstance(out, dict) else out
    return np.asarray(wav, dtype=np.float32).reshape(-1)


def _synthesize_segment_chunks(text: str, language: str, gpt_cond_latent, speaker_embedding):
    """Sintetizza un segment chunk per chunk. Generator: yield un dict
    `{index, total, audio}` per ciascun chunk.

    Ogni chunk include 250 ms di silenzio finale tranne l'ultimo, cosi'
    il client deve solo concatenare i chunk in ordine per ottenere
    l'audio del segment (identico alla vecchia sintesi per-segmento).
    Inviare l'audio per chunk (~12s, payload piccolo) invece che come
    unico blob per segment evita che i segmenti lunghi sforino i limiti
    dello stream di RunPod e vengano persi.

    Se il testo non produce alcun chunk, yield un singolo chunk di
    silenzio breve cosi' il segment resta comunque rappresentato.
    """
    chunks = split_into_chunks(text, MAX_CHARS)
    if not chunks:
        yield {
            "index": 0,
            "total": 1,
            "audio": np.zeros(int(SAMPLE_RATE * 0.1), dtype=np.float32),
        }
        return
    silence = np.zeros(int(SAMPLE_RATE * SILENCE_MS / 1000), dtype=np.float32)
    total = len(chunks)
    for i, chunk in enumerate(chunks):
        audio = _infer_chunk(chunk, language, gpt_cond_latent, speaker_embedding)
        if i < total - 1:
            audio = np.concatenate([audio, silence]).astype(np.float32, copy=False)
        yield {"index": i, "total": total, "audio": audio}


def _encode_flac_b64(audio: np.ndarray) -> str:
    """Codifica un array float32 mono in FLAC lossless, base64."""
    buf = io.BytesIO()
    sf.write(buf, audio, SAMPLE_RATE, format="FLAC")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def handler(job):
    """Generator handler RunPod: un `yield` per CHUNK audio.

    Estrae i conditioning latents dal voice sample UNA sola volta, poi
    sintetizza ogni segment chunk per chunk. Ogni chunk e' un payload
    piccolo: lo stream di RunPod lo consegna in modo affidabile, mentre
    un intero segmento lungo come blob unico verrebbe perso.
    """
    job_input = job.get("input") or {}
    tmp_paths: list[str] = []
    try:
        language = normalize_language_code(job_input.get("language_code") or "it")
        voice_url = job_input.get("voice_sample_url")
        voice_b64 = job_input.get("voice_sample_b64")
        segments = job_input.get("segments") or []

        if not voice_url and not voice_b64:
            yield {"error": "voice_sample_url o voice_sample_b64 mancante"}
            return
        if not segments:
            yield {"error": "nessun segment fornito"}
            return

        # Recupera il sample di riferimento. Preferito: download da URL
        # (payload del job leggero, robusto). Fallback: base64 inline.
        if voice_url:
            try:
                with urllib.request.urlopen(voice_url, timeout=120) as resp:
                    audio_bytes = resp.read()
            except Exception as exc:
                yield {"error": f"download del voice sample fallito: {exc}"}
                return
        else:
            audio_bytes = base64.b64decode(voice_b64)
        if not audio_bytes:
            yield {"error": "voice sample vuoto"}
            return

        # Scrivi il sample su file temp (ffmpeg rileva il formato da solo).
        raw_path = tempfile.NamedTemporaryFile(suffix=".audio", delete=False).name
        tmp_paths.append(raw_path)
        with open(raw_path, "wb") as f:
            f.write(audio_bytes)

        # Normalizza a WAV mono via ffmpeg (robustezza formati).
        try:
            wav_path = _ffmpeg_to_wav(raw_path)
            tmp_paths.append(wav_path)
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or b"").decode("utf-8", "replace")[-600:]
            yield {"error": f"normalizzazione audio fallita: {stderr}"}
            return

        # Estrai i conditioning latents UNA SOLA VOLTA (parametri dal
        # config del modello, come batch_generate.py).
        gpt_cond_latent, speaker_embedding = _model.get_conditioning_latents(
            audio_path=[wav_path],
            gpt_cond_len=_config.gpt_cond_len,
            gpt_cond_chunk_len=_config.gpt_cond_chunk_len,
            max_ref_length=_config.max_ref_len,
        )

        total = len(segments)
        for i, seg in enumerate(segments):
            seg_id = str((seg or {}).get("segment_id") or "")
            text = ((seg or {}).get("text") or "").strip()
            if not seg_id or not text:
                continue
            try:
                runpod.serverless.progress_update(job, f"segment {i + 1}/{total}")
            except Exception:
                pass
            # Sintesi e invio PER CHUNK: ogni chunk e' un payload
            # piccolo (~12s) consegnato in modo affidabile dallo stream.
            for chunk in _synthesize_segment_chunks(
                text, language, gpt_cond_latent, speaker_embedding
            ):
                yield {
                    "segment_id": seg_id,
                    "chunk_index": chunk["index"],
                    "chunk_total": chunk["total"],
                    "audio_b64": _encode_flac_b64(chunk["audio"]),
                    "sample_rate": SAMPLE_RATE,
                }
    except Exception as exc:  # pragma: no cover
        yield {
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc()[-1200:],
        }
    finally:
        for p in tmp_paths:
            try:
                os.unlink(p)
            except OSError:
                pass


runpod.serverless.start({"handler": handler})
