"""XTTS-v2 voice cloning service per la Fase 6 (generazione video MP4).

Replica fedelmente lo script di riferimento
`C:\\Users\\michele\\Downloads\\XTTS-v2-cloning-voice-test\\batch_generate.py`:
i parametri di inference e di chunking sono allineati 1:1 (temperature 0.65,
niente length/repetition/top params override, rstrip punteggiatura,
silenzio 250ms tra chunk, parametri di config a `get_conditioning_latents`).

Architettura:
- **Singleton lazy**: il modello (~1.8 GB) viene caricato una sola volta
  alla prima richiesta. Stati condivisi tra worker concurrent.
- **Latents cache in-memory**: `gpt_cond_latent + speaker_embedding`
  estratti via `_ensure_latents()` sono cached per sha256(voice_sample)
  così run consecutivi della stessa voce non re-estraggono.
- **Latents cache su disco**: `extract_latents_to_file()` salva i tensori
  in `*.pt` — usato dal worker pre-training (`avatar_tts_latents_worker`)
  al momento dell'upload audio. Il worker video carica via
  `load_latents_from_file()` e li passa a `synthesize_text()` come
  `precomputed_latents`, saltando del tutto `_ensure_latents()`.
- **Auto-detect device**: `cuda → mps → cpu`. Su CUDA RTF ≈ 0.2× (1:1
  facilmente raggiunto). Su CPU RTF ≈ 5-10× — l'utente vedrà ETA reale.
- **Reset periodico** ogni `xtts_reset_after_jobs` (default 50): mitiga
  memory growth Coqui-TTS noto su long-running.

Output: WAV float32 24000 Hz mono (sample rate nativo XTTS-v2).
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
# Lingue supportate (16, come SUPPORTED_LANGUAGES in clone_voice.py)
# ---------------------------------------------------------------------------

# Lista TASSATIVAMENTE allineata a `clone_voice.py:14-17` dello script
# di riferimento. NON aggiungere `hi` (non supportato dallo script).
XTTS_SUPPORTED_LANGUAGES: frozenset[str] = frozenset(
    {
        "it", "en", "es", "fr", "de", "pt", "pl", "tr",
        "ru", "nl", "cs", "ar", "zh-cn", "ja", "hu", "ko",
    }
)


def is_language_supported(code: str | None) -> bool:
    """True se `code` (post-normalize) è in XTTS_SUPPORTED_LANGUAGES."""
    if not code:
        return False
    return normalize_language_code(code) in XTTS_SUPPORTED_LANGUAGES


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
# Chunking — replica `batch_generate.py:28-51`
# ---------------------------------------------------------------------------

# Regex pattern dello script originale.
_STRONG_SPLIT_RE = re.compile(r"(?<=[.!?:])\s+")
_SOFT_SPLIT_RE = re.compile(r"(?<=,)\s+")
# rstrip applicato a ogni chunk (anti-"punto" del normalizer XTTS).
_CHUNK_TRIM_CHARS = ".:;,"


def split_into_chunks(text: str, max_chars: int) -> list[str]:
    """Divide il testo in chunk ≤ `max_chars` rispettando i terminatori
    forti (.!?:). Per chunk > max_chars, split ulteriore su `,`.

    Identico a `chunk_text()` di `batch_generate.py:28-51` dello script
    di riferimento. Rimuove la punteggiatura finale di ogni chunk per
    evitare che il normalizer italiano XTTS legga `.` come "punto".
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
        # Sotto-split su virgola.
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
    modello (~1.8 GB → cache locale `$TTS_HOME` o `~/.local/share/tts/`).

    Thread-safe rispetto al singleton (lock), ma le sintesi inference
    sono sequenziali sul device: un solo `inference()` alla volta.
    """

    _instance: "XTTSService | None" = None
    _init_lock = asyncio.Lock()

    def __init__(self) -> None:
        self._model: Any = None
        self._device: str | None = None
        self._latents_cache: dict[str, tuple[Any, Any]] = {}
        self._job_counter: int = 0
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
        """Distrugge il singleton (modello + cache latents)."""
        async with cls._init_lock:
            if cls._instance is not None:
                cls._instance._shutdown()
            cls._instance = None

    def _shutdown(self) -> None:
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except Exception:  # pragma: no cover
            pass
        self._latents_cache.clear()
        self._model = None
        try:
            import torch

            if self._device == "cuda" and torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:  # pragma: no cover
            pass

    async def _load_model_async(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._load_model_sync)

    def _load_model_sync(self) -> None:
        settings = get_settings()
        try:
            import torch  # noqa: F401
            # NNPACK è una lib di acceleration Conv2d che funziona solo su
            # alcune CPU (ARM / x86 con flag specifici). Su VM cloud
            # generiche stampa `Could not initialize NNPACK! Reason:
            # Unsupported hardware.` ad ogni primo uso di Conv2d → rumore
            # nei log. Lo disabilitiamo: PyTorch usa il backend C++/oneDNN
            # (più lento del 10-20% ma uniforme su qualunque CPU).
            try:
                torch.backends.nnpack.enabled = False
            except Exception:  # pragma: no cover
                pass
            from TTS.api import TTS  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover
            missing = getattr(exc, "name", None) or "TTS/torch"
            error_str = str(exc)
            log.error(
                "xtts_import_failed",
                missing_module=missing,
                error=error_str,
                exc_type=type(exc).__name__,
                exc_info=True,
            )
            raise XTTSNotAvailableError(
                f"Stack TTS non importabile: {error_str}. "
                f"Modulo segnalato: `{missing}`. "
                f"Su Docker: `docker compose build backend --no-cache`."
            ) from exc

        self._device = self._detect_device()
        log.info(
            "xtts_loading_model",
            model=settings.xtts_model_name,
            device=self._device,
        )
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

    def _get_tts_model(self) -> Any:
        """Restituisce il sotto-modello effettivo (`tts.synthesizer.tts_model`).
        Solleva XTTSError se il modello non è caricato."""
        if self._model is None:
            raise XTTSError("Modello XTTS non caricato.")
        return self._model.synthesizer.tts_model

    def extract_latents(self, voice_sample_path: Path) -> tuple[Any, Any]:
        """Estrae i conditioning latents per una voce SENZA cache.

        Replica `batch_generate.py:74-79`: passa esplicitamente
        `gpt_cond_len`, `gpt_cond_chunk_len`, `max_ref_length` dal config
        del modello (vs default Coqui che possono cambiare tra release).

        Restituisce `(gpt_cond_latent, speaker_embedding)`. Usato dal
        worker pre-training (`avatar_tts_latents_service`).
        """
        tts_model = self._get_tts_model()
        config = tts_model.config
        try:
            gcl, se = tts_model.get_conditioning_latents(
                audio_path=[str(voice_sample_path)],
                gpt_cond_len=config.gpt_cond_len,
                gpt_cond_chunk_len=config.gpt_cond_chunk_len,
                max_ref_length=config.max_ref_len,
            )
        except Exception as exc:
            raise XTTSVoiceSampleError(
                f"Estrazione latents fallita: {exc}"
            ) from exc
        return gcl, se

    def _ensure_latents(self, voice_sample_path: Path) -> tuple[Any, Any]:
        """Versione cached (sha256 in-memory) di `extract_latents()`.
        Usata da `synthesize_segment()` quando il caller non ha i latents
        pre-computati (fallback legacy)."""
        sha = sha256_file(voice_sample_path)
        hit = self._latents_cache.get(sha)
        if hit is not None:
            return hit
        log.info(
            "xtts_compute_latents",
            voice_sha=sha[:12],
            voice_path=str(voice_sample_path.name),
        )
        gcl, se = self.extract_latents(voice_sample_path)
        self._latents_cache[sha] = (gcl, se)
        return gcl, se

    async def synthesize_segment(
        self,
        *,
        text: str,
        language: str,
        voice_sample_path: Path | None = None,
        precomputed_latents: tuple[Any, Any] | None = None,
        on_chunk_progress: Callable[[int, int], None] | None = None,
    ) -> np.ndarray:
        """Sintetizza testo TTS con voice cloning.

        Args:
            text: testo da pronunciare. Chunking interno su `.!?:`.
            language: ISO 639-1 normalizzato (`it`, `en`, ..., `zh-cn`).
            voice_sample_path: WAV/MP3/OGG di riferimento (legacy fallback).
                Richiesto se `precomputed_latents=None`.
            precomputed_latents: `(gpt_cond_latent, speaker_embedding)`
                già estratti (es. caricati da `*.pt` salvato dal worker
                pre-training). Quando fornito, salta l'estrazione e va
                direttamente all'inferenza — ~5-15s di saving al primo job.
            on_chunk_progress: callback(done, total) per progress UI.

        Returns:
            ndarray float32 1D mono a `xtts_sample_rate` (24000 Hz).
            Tra un chunk e l'altro è inserito 250 ms di silenzio
            (`SILENCE_BETWEEN_CHUNKS_MS = 250` dello script originale).
        """
        settings = get_settings()
        chunks = split_into_chunks(text, settings.xtts_max_chars_per_chunk)
        if not chunks:
            return np.zeros(0, dtype=np.float32)

        loop = asyncio.get_running_loop()

        # Risolvi i latents: precomputed se forniti, altrimenti estrai
        # dal sample (con cache in-memory).
        if precomputed_latents is not None:
            gcl, se = precomputed_latents
        else:
            if voice_sample_path is None or not voice_sample_path.is_file():
                raise XTTSVoiceSampleError(
                    "Né `precomputed_latents` né `voice_sample_path` valido — "
                    "impossibile procedere."
                )
            gcl, se = await loop.run_in_executor(
                self._executor, self._ensure_latents, voice_sample_path
            )

        # 250ms di silenzio tra chunk, allineato a batch_generate.py:26.
        sr = settings.xtts_sample_rate
        silence = np.zeros(int(sr * 0.25), dtype=np.float32)

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
            if i < total - 1:
                results.append(silence)
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
        """Inference sincrona di un singolo chunk.

        Parametri allineati 1:1 a `batch_generate.py:84-91`:
        - `temperature=0.65` (non 0.7)
        - `enable_text_splitting=False` (chunking gestito da noi)
        - NIENTE override di `length_penalty`/`repetition_penalty`/`top_k`/
          `top_p`/`speed`: lo script originale usa i default Coqui per
          tutti questi. Modificarli ha portato a degradi qualitativi.

        Il `rstrip(".:;,")` è già applicato a monte da `split_into_chunks`,
        ma lo ripetiamo qui difensivamente.
        """
        text = (text or "").strip().rstrip(_CHUNK_TRIM_CHARS)
        out = self._model.synthesizer.tts_model.inference(
            text=text,
            language=language,
            gpt_cond_latent=gpt_cond_latent,
            speaker_embedding=speaker_embedding,
            temperature=0.65,
            enable_text_splitting=False,
        )
        wav = out.get("wav") if isinstance(out, dict) else out
        return np.asarray(wav, dtype=np.float32).reshape(-1)


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------


async def synthesize_text(
    *,
    text: str,
    language: str,
    voice_sample_path: Path | None = None,
    precomputed_latents: tuple[Any, Any] | None = None,
    on_chunk_progress: Callable[[int, int], None] | None = None,
) -> tuple[np.ndarray, int]:
    """Helper one-shot per il worker video.

    Preferisce `precomputed_latents` se forniti (più veloce: skip estrazione).
    Restituisce `(audio_float32, sample_rate)`.
    """
    settings = get_settings()
    svc = await XTTSService.get()
    audio = await svc.synthesize_segment(
        text=text,
        voice_sample_path=voice_sample_path,
        precomputed_latents=precomputed_latents,
        language=language,
        on_chunk_progress=on_chunk_progress,
    )
    return audio, settings.xtts_sample_rate


async def extract_latents_to_file(
    voice_sample_path: Path, output_path: Path
) -> None:
    """Estrae i conditioning latents e li serializza in `output_path` (.pt).

    Sicurezza: il file viene salvato come dict
    `{"gpt_cond_latent": Tensor, "speaker_embedding": Tensor}` per
    permettere `torch.load(weights_only=True)` al re-loading.
    """
    import torch  # type: ignore[import-untyped]

    svc = await XTTSService.get()
    loop = asyncio.get_running_loop()
    gcl, se = await loop.run_in_executor(
        svc._executor, svc.extract_latents, voice_sample_path
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {"gpt_cond_latent": gcl, "speaker_embedding": se},
        str(output_path),
    )
    log.info(
        "xtts_latents_saved",
        path=str(output_path),
        size_bytes=output_path.stat().st_size,
    )


async def load_latents_from_file(
    latents_path: Path,
) -> tuple[Any, Any]:
    """Carica i conditioning latents salvati da `extract_latents_to_file`.

    Usato dal worker video per skippare l'estrazione (~5-15s) ad ogni job.
    Usa `weights_only=True` quando supportato (PyTorch >=2.1) per safety.
    """
    import torch  # type: ignore[import-untyped]

    if not latents_path.is_file():
        raise XTTSVoiceSampleError(
            f"File latents non trovato: {latents_path}"
        )

    loop = asyncio.get_running_loop()

    def _load() -> tuple[Any, Any]:
        try:
            data = torch.load(
                str(latents_path), map_location="cpu", weights_only=True
            )
        except TypeError:  # pragma: no cover — torch <2.1
            data = torch.load(str(latents_path), map_location="cpu")
        gcl = data["gpt_cond_latent"]
        se = data["speaker_embedding"]
        return gcl, se

    return await loop.run_in_executor(None, _load)


def normalize_language_code(language_code: str) -> str:
    """Normalizza un codice lingua per XTTS-v2.

    Regole (allineate a `clone_voice.py` + comportamento osservato Coqui):
    - lowercase
    - rimuove country code (`it-IT` → `it`)
    - speciale `zh*` → `zh-cn`
    - se non in `XTTS_SUPPORTED_LANGUAGES`, fallback a `it`
    """
    code = (language_code or "it").strip().lower()
    if code.startswith("zh"):
        return "zh-cn"
    if "-" in code:
        code = code.split("-")[0]
    if code not in XTTS_SUPPORTED_LANGUAGES:
        return "it"
    return code
