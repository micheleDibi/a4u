"""XTTS-v2 voice cloning service per la Fase 6 (generazione video MP4).

Wrapper attorno a Coqui TTS XTTS-v2 (`tts_models/multilingual/multi-
dataset/xtts_v2`). Replica e ottimizza i pattern dello script di
riferimento `C:\\Users\\michele\\Downloads\\XTTS-v2-cloning-voice-test`:

- **Singleton lazy**: il modello (~1.8 GB) viene caricato una sola volta
  alla prima richiesta. Stati condivisi tra worker concurrent.
- **Latents cache per sha256(voice_sample)**: `gpt_cond_latent +
  speaker_embedding` sono computati una volta per voce. Riusati per
  tutte le lezioni dello stesso assegnatario (es. 10 lezioni → 1 sola
  estrazione invece di 10).
- **Auto-detect device**: `cuda → mps → cpu`. Su CUDA RTF ≈ 0.2× (1:1
  facilmente raggiunto). Su CPU RTF ≈ 5-10× — l'utente vedrà ETA reale.
- **Chunking**: testi splittati su `.!?:` con cap a `xtts_max_chars_per_chunk`
  (default 180 char come `batch_generate.py:24`) per ridurre allucinazioni
  e gestire frasi lunghe.
- **Progress callback**: ogni chunk emette un tick; il worker la usa per
  aggiornare `video_progress` con ease-out.
- **Reset periodico** ogni `xtts_reset_after_jobs` (default 50): mitiga
  memory growth Coqui-TTS noto su long-running.

Output: WAV float32 24000 Hz mono (sample rate nativo XTTS-v2). Il worker
ricampiona/aggrega via ffmpeg.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

import numpy as np

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger("app.xtts")


# ---------------------------------------------------------------------------
# Errori
# ---------------------------------------------------------------------------


class XTTSError(Exception):
    """Base class per errori XTTS — recuperabili (transitori)."""


class XTTSNotAvailableError(XTTSError):
    """Coqui-TTS o torch non installati nel venv. Non recuperabile —
    l'amministratore deve installare le dipendenze."""


class XTTSVoiceSampleError(XTTSError):
    """Sample audio assente o non leggibile. Non recuperabile auto."""


# ---------------------------------------------------------------------------
# Helpers (split chunks, sha256)
# ---------------------------------------------------------------------------


# Pattern di terminazione "forte": . ! ? : seguito da spazio o fine stringa.
# Replica `batch_generate.py:28-51` dello script di riferimento.
_TERMINATORS = (".", "!", "?", ":")
_SOFT_TERMINATORS = (",", ";")


def split_into_chunks(text: str, max_chars: int) -> list[str]:
    """Divide il testo in chunk ≤ `max_chars` rispettando i punti di
    terminazione naturali (.!?:). Se un chunk supera ancora `max_chars`,
    si splitta su `,` o `;`. Fallback brutale: split per spazi.

    Vuoto/whitespace → lista vuota (caller deve gestire).
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return []
    if len(cleaned) <= max_chars:
        return [cleaned]

    # Pass 1: split su terminatori forti.
    sentences: list[str] = []
    cursor = 0
    for i, ch in enumerate(cleaned):
        if ch in _TERMINATORS:
            sentences.append(cleaned[cursor : i + 1].strip())
            cursor = i + 1
    tail = cleaned[cursor:].strip()
    if tail:
        sentences.append(tail)

    # Pass 2: merge consecutive sentences fino a max_chars (greedy).
    chunks: list[str] = []
    buf = ""
    for s in sentences:
        if not buf:
            buf = s
            continue
        if len(buf) + 1 + len(s) <= max_chars:
            buf = f"{buf} {s}"
        else:
            chunks.append(buf)
            buf = s
    if buf:
        chunks.append(buf)

    # Pass 3: chunk ancora troppo lungo? Split su terminatori soft.
    result: list[str] = []
    for c in chunks:
        if len(c) <= max_chars:
            result.append(c)
            continue
        sub_cursor = 0
        sub_chunks: list[str] = []
        sub_buf = ""
        for i, ch in enumerate(c):
            if ch in _SOFT_TERMINATORS:
                sub_chunks.append(c[sub_cursor : i + 1].strip())
                sub_cursor = i + 1
        rest = c[sub_cursor:].strip()
        if rest:
            sub_chunks.append(rest)
        for sc in sub_chunks:
            if not sub_buf:
                sub_buf = sc
                continue
            if len(sub_buf) + 1 + len(sc) <= max_chars:
                sub_buf = f"{sub_buf} {sc}"
            else:
                result.append(sub_buf)
                sub_buf = sc
        if sub_buf:
            result.append(sub_buf)

    # Pass 4: fallback assoluto se ancora troppo lungo (split su spazi).
    final: list[str] = []
    for c in result:
        if len(c) <= max_chars:
            final.append(c)
            continue
        words = c.split()
        wbuf = ""
        for w in words:
            cand = f"{wbuf} {w}".strip()
            if len(cand) <= max_chars:
                wbuf = cand
            else:
                if wbuf:
                    final.append(wbuf)
                wbuf = w
        if wbuf:
            final.append(wbuf)
    return final


def sha256_file(path: Path, chunk_size: int = 65536) -> str:
    """Hash sha256 di un file, usato come cache key dei latents XTTS."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Singleton XTTSService
# ---------------------------------------------------------------------------


class XTTSService:
    """Singleton lazy per il modello XTTS-v2.

    Accesso via `XTTSService.get()`. La prima chiamata scarica/carica il
    modello (~1.8 GB → cache locale `~/.local/share/tts/` o `%APPDATA%/tts`).

    Thread-safe rispetto al singleton (lock), ma le sintesi inference
    sono sequenziali sul device: un solo `inference()` alla volta. Il
    worker enforced via `video_max_concurrency=1` di default.
    """

    _instance: "XTTSService | None" = None
    _init_lock = asyncio.Lock()

    def __init__(self) -> None:
        self._model: Any = None
        self._device: str | None = None
        self._latents_cache: dict[str, tuple[Any, Any]] = {}
        self._job_counter: int = 0
        # Thread pool per offloading dell'inference (operazione CPU/GPU
        # bound, sincrona). Un solo worker per evitare contention sul
        # device (XTTS non è thread-safe per device singolo).
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="xtts"
        )

    # -- lifecycle -----------------------------------------------------

    @classmethod
    async def get(cls) -> "XTTSService":
        """Restituisce l'istanza lazy. Thread/async-safe."""
        if cls._instance is None or cls._instance._model is None:
            async with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls()
                if cls._instance._model is None:
                    await cls._instance._load_model_async()
        return cls._instance

    @classmethod
    async def reset(cls) -> None:
        """Distrugge il singleton (modello + cache latents). Usato dal
        worker dopo N job per mitigare memory leak Coqui-TTS."""
        async with cls._init_lock:
            if cls._instance is not None:
                cls._instance._shutdown()
            cls._instance = None

    def _shutdown(self) -> None:
        """Libera risorse modello + cache. Sincrono."""
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except Exception:  # pragma: no cover
            pass
        self._latents_cache.clear()
        self._model = None
        # Suggerisci a torch di liberare la cache CUDA (no-op su CPU/MPS).
        try:
            import torch

            if self._device == "cuda" and torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:  # pragma: no cover
            pass

    async def _load_model_async(self) -> None:
        """Carica il modello in un thread (operazione bloccante)."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._load_model_sync)

    def _load_model_sync(self) -> None:
        settings = get_settings()
        try:
            import torch  # noqa: F401
            from TTS.api import TTS  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover
            missing = getattr(exc, "name", None) or "TTS/torch"
            log.error(
                "xtts_import_failed",
                missing_module=missing,
                error=str(exc),
                hint=(
                    "Verifica che l'immagine Docker sia stata buildata "
                    "con l'extra `[video]` (`pip install '.[video]'` nel "
                    "builder stage). Se hai aggiornato di recente, "
                    "rebuilda con `docker compose build backend --no-cache`."
                ),
            )
            raise XTTSNotAvailableError(
                f"Stack TTS non disponibile: modulo `{missing}` mancante. "
                f"Sul deploy Docker, rebuilda il container backend "
                f"(`docker compose build backend --no-cache`) — il "
                f"Dockerfile installa l'extra `[video]` automaticamente. "
                f"Per locale dev: `pip install '.[video]'` nel venv backend."
            ) from exc

        self._device = self._detect_device()
        log.info(
            "xtts_loading_model",
            model=settings.xtts_model_name,
            device=self._device,
        )
        # `gpu` legacy non più necessario in Coqui-TTS recente; .to(device).
        model = TTS(model_name=settings.xtts_model_name, progress_bar=False)
        model = model.to(self._device)
        self._model = model
        log.info("xtts_loaded", device=self._device)

    def _detect_device(self) -> str:
        settings = get_settings()
        if settings.xtts_device and settings.xtts_device != "auto":
            return settings.xtts_device
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda"
            if (
                hasattr(torch.backends, "mps")
                and torch.backends.mps.is_available()
            ):
                return "mps"
        except Exception:  # pragma: no cover
            pass
        return "cpu"

    @property
    def device(self) -> str:
        return self._device or "unknown"

    # -- voice cloning -------------------------------------------------

    def _ensure_latents(self, voice_sample_path: Path) -> tuple[Any, Any]:
        """Estrae/legge dalla cache i conditioning latents per la voce.

        Coqui-TTS XTTS-v2:
            gpt_cond_latent, speaker_embedding = model.synthesizer
                .tts_model.get_conditioning_latents(audio_path=[str(p)])
        """
        sha = sha256_file(voice_sample_path)
        hit = self._latents_cache.get(sha)
        if hit is not None:
            return hit
        if self._model is None:
            raise XTTSError("Modello XTTS non caricato.")
        log.info(
            "xtts_compute_latents",
            voice_sha=sha[:12],
            voice_path=str(voice_sample_path.name),
        )
        try:
            gcl, se = self._model.synthesizer.tts_model.get_conditioning_latents(
                audio_path=[str(voice_sample_path)]
            )
        except Exception as exc:
            raise XTTSVoiceSampleError(
                f"Impossibile estrarre conditioning latents dal campione "
                f"vocale: {exc}"
            ) from exc
        self._latents_cache[sha] = (gcl, se)
        return gcl, se

    async def synthesize_segment(
        self,
        *,
        text: str,
        voice_sample_path: Path,
        language: str,
        on_chunk_progress: Callable[[int, int], None] | None = None,
    ) -> np.ndarray:
        """Sintetizza testo TTS con voice cloning.

        Args:
            text: testo da pronunciare. Chunking interno su `.!?:`.
            voice_sample_path: WAV/MP3/OGG di riferimento (6-10s, mono).
            language: ISO 639-1 (es. "it", "en"). XTTS-v2 supporta 16.
            on_chunk_progress: callback(done, total) per progress UI.

        Returns:
            ndarray float32 1D mono a `xtts_sample_rate` (24000 Hz).
        """
        if not voice_sample_path.is_file():
            raise XTTSVoiceSampleError(
                f"Campione vocale non trovato: {voice_sample_path}"
            )
        settings = get_settings()
        chunks = split_into_chunks(text, settings.xtts_max_chars_per_chunk)
        if not chunks:
            return np.zeros(0, dtype=np.float32)

        # Offloading thread-pool: l'inference TTS è sincrono e
        # CPU/GPU-bound. asyncio.run_in_executor evita di bloccare il loop.
        loop = asyncio.get_running_loop()
        gcl, se = await loop.run_in_executor(
            self._executor, self._ensure_latents, voice_sample_path
        )

        results: list[np.ndarray] = []
        total = len(chunks)
        for i, chunk in enumerate(chunks):
            wav = await loop.run_in_executor(
                self._executor,
                self._infer_chunk,
                chunk,
                language,
                gcl,
                se,
            )
            results.append(wav)
            if on_chunk_progress is not None:
                try:
                    on_chunk_progress(i + 1, total)
                except Exception:  # pragma: no cover
                    pass

        self._job_counter += 1
        if self._job_counter >= settings.xtts_reset_after_jobs:
            log.info(
                "xtts_periodic_reset",
                jobs=self._job_counter,
                threshold=settings.xtts_reset_after_jobs,
            )
            # Reset asincrono: si schedula, non blocchiamo il chiamante.
            asyncio.create_task(self.__class__.reset())

        if not results:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(results).astype(np.float32, copy=False)

    def _infer_chunk(
        self,
        text: str,
        language: str,
        gpt_cond_latent: Any,
        speaker_embedding: Any,
    ) -> np.ndarray:
        """Inference sincrona di un singolo chunk. Eseguito nel thread
        pool del singleton."""
        settings = get_settings()
        # Coqui-TTS XTTS-v2 inference API:
        # out = model.synthesizer.tts_model.inference(text, language,
        #     gpt_cond_latent, speaker_embedding, temperature=..., speed=...)
        # out["wav"]: list[float] (Python list) → convertiamo in ndarray.
        out = self._model.synthesizer.tts_model.inference(
            text=text,
            language=language,
            gpt_cond_latent=gpt_cond_latent,
            speaker_embedding=speaker_embedding,
            temperature=0.7,
            length_penalty=1.0,
            repetition_penalty=2.0,
            top_k=50,
            top_p=0.85,
            speed=settings.xtts_speed,
            enable_text_splitting=False,  # chunking gestito da noi
        )
        wav = out.get("wav") if isinstance(out, dict) else out
        return np.asarray(wav, dtype=np.float32).reshape(-1)


# ---------------------------------------------------------------------------
# Convenience wrapper
# ---------------------------------------------------------------------------


async def synthesize_text(
    *,
    text: str,
    voice_sample_path: Path,
    language: str,
    on_chunk_progress: Callable[[int, int], None] | None = None,
) -> tuple[np.ndarray, int]:
    """Helper one-shot per il worker. Restituisce (audio, sample_rate)."""
    settings = get_settings()
    svc = await XTTSService.get()
    audio = await svc.synthesize_segment(
        text=text,
        voice_sample_path=voice_sample_path,
        language=language,
        on_chunk_progress=on_chunk_progress,
    )
    return audio, settings.xtts_sample_rate


def normalize_language_code(language_code: str) -> str:
    """XTTS-v2 supporta: it, en, es, fr, de, pt, pl, tr, ru, nl, cs,
    ar, zh-cn, hu, ko, ja, hi. Estrae il primo segmento prima di '-'
    e lowercase. Mantiene `zh-cn` come caso speciale.
    """
    code = (language_code or "it").strip().lower()
    if code.startswith("zh"):
        return "zh-cn"
    # rimuove eventuale country code (it-IT → it)
    if "-" in code:
        code = code.split("-")[0]
    if not re.match(r"^[a-z]{2}$", code):
        return "it"
    return code
