"""Client per il servizio TTS XTTS-v2 su RunPod Serverless.

Sostituisce il vecchio `xtts_voice_clone_service` in-process: la sintesi
vocale gira ora su GPU remota (vedi la cartella `XTTS/` del repo). Questo
modulo e' un client HTTP puro — nessuna dipendenza torch/coqui.

Flusso: invia 1 job per video (tutti i segment), consuma lo stream
incrementale di RunPod (1 risultato per segment, FLAC base64), decodifica
in array numpy float32 @ 24000 Hz — stesso contratto del vecchio TTS.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import soundfile as sf

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.tts_languages import normalize_language_code

log = get_logger("app.runpod_tts")

SAMPLE_RATE = 24000
# Stati RunPod che indicano un job terminato in errore.
_FAILED_STATES = {"FAILED", "CANCELLED", "TIMED_OUT"}


class RunpodTtsError(Exception):
    """Errore generico del client TTS RunPod (recuperabile)."""


class RunpodNotConfiguredError(RunpodTtsError):
    """RUNPOD_API_KEY / RUNPOD_TTS_ENDPOINT_ID non configurati."""


class RunpodJobFailedError(RunpodTtsError):
    """Il job RunPod e' terminato in errore."""


class RunpodTimeoutError(RunpodTtsError):
    """Il job RunPod ha superato il timeout configurato."""


def is_configured() -> bool:
    """True se le credenziali RunPod TTS sono entrambe presenti."""
    settings = get_settings()
    return bool(settings.runpod_api_key and settings.runpod_tts_endpoint_id)


def _endpoint_base() -> tuple[str, str]:
    """Ritorna (base_url_endpoint, api_key) o solleva RunpodNotConfiguredError."""
    settings = get_settings()
    if not is_configured():
        raise RunpodNotConfiguredError(
            "RunPod TTS non configurato: impostare RUNPOD_API_KEY e "
            "RUNPOD_TTS_ENDPOINT_ID."
        )
    base = settings.runpod_base_url.rstrip("/")
    return f"{base}/v2/{settings.runpod_tts_endpoint_id}", settings.runpod_api_key or ""


def _decode_segment_audio(audio_b64: str) -> np.ndarray:
    """Decodifica un blob FLAC base64 in array float32 mono 1-D."""
    raw = base64.b64decode(audio_b64)
    data, _sr = sf.read(io.BytesIO(raw), dtype="float32")
    arr = np.asarray(data, dtype=np.float32)
    if arr.ndim > 1:
        # Se per qualche motivo arriva stereo, prende il primo canale.
        arr = arr[:, 0]
    return arr.reshape(-1)


async def synthesize_lesson_audio(
    *,
    speech_raw: dict[str, Any],
    voice_sample_path: Path,
    language_code: str,
    on_segment_progress: Callable[[int, int], Awaitable[None]] | None = None,
) -> tuple[dict[str, np.ndarray], int]:
    """Sintetizza l'audio di tutti i segment della lezione via RunPod.

    Args:
        speech_raw: dict con `speech_segments` (lista di {segment_id, text, ...}).
        voice_sample_path: file audio di riferimento dell'avatar assegnatario.
        language_code: codice lingua (normalizzato internamente).
        on_segment_progress: callback(done, total) per il progress UI.

    Returns:
        (audio_per_segment, sample_rate) — `audio_per_segment` e' un dict
        {segment_id: ndarray float32 mono}, sample_rate = 24000.

    Raises:
        RunpodNotConfiguredError, RunpodJobFailedError, RunpodTimeoutError,
        RunpodTtsError.
    """
    endpoint_base, api_key = _endpoint_base()

    # Lista segment: solo {segment_id, text} non vuoti (stesso filtro del
    # vecchio loop TTS in-process).
    segments: list[dict[str, str]] = []
    for seg in speech_raw.get("speech_segments") or []:
        if not isinstance(seg, dict):
            continue
        sid = str(seg.get("segment_id") or "")
        text = (seg.get("text") or "").strip()
        if sid and text:
            segments.append({"segment_id": sid, "text": text})

    if not segments:
        return {}, SAMPLE_RATE

    if not voice_sample_path.is_file():
        raise RunpodTtsError(f"Voice sample non trovato: {voice_sample_path}")

    voice_b64 = base64.b64encode(voice_sample_path.read_bytes()).decode("ascii")
    voice_fmt = voice_sample_path.suffix.lstrip(".").lower() or "wav"
    language = normalize_language_code(language_code)

    payload = {
        "input": {
            "language_code": language,
            "voice_sample_b64": voice_b64,
            "voice_sample_format": voice_fmt,
            "segments": segments,
        }
    }
    headers = {"Authorization": f"Bearer {api_key}"}

    settings = get_settings()
    timeout_s = max(60, int(settings.runpod_tts_timeout_seconds))
    poll_s = max(1, int(settings.runpod_tts_poll_interval_seconds))
    total = len(segments)
    deadline = time.monotonic() + timeout_s

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        job_id = await _submit_job(client, endpoint_base, headers, payload)
        log.info(
            "runpod_tts_job_submitted",
            job_id=job_id,
            segments=total,
            language=language,
        )
        outputs = await _collect_outputs(
            client,
            endpoint_base=endpoint_base,
            headers=headers,
            job_id=job_id,
            total=total,
            deadline=deadline,
            poll_s=poll_s,
            on_segment_progress=on_segment_progress,
        )

    audio_per_segment: dict[str, np.ndarray] = {}
    for out in outputs:
        if not isinstance(out, dict):
            continue
        if out.get("error"):
            raise RunpodJobFailedError(f"Worker TTS RunPod: {out['error']}")
        sid = str(out.get("segment_id") or "")
        audio_b64 = out.get("audio_b64")
        if not sid or not audio_b64:
            continue
        audio_per_segment[sid] = _decode_segment_audio(audio_b64)

    if not audio_per_segment:
        raise RunpodJobFailedError("Il job RunPod non ha prodotto alcun audio.")

    log.info(
        "runpod_tts_job_done",
        job_id=job_id,
        segments_received=len(audio_per_segment),
    )
    return audio_per_segment, SAMPLE_RATE


async def _submit_job(
    client: httpx.AsyncClient,
    endpoint_base: str,
    headers: dict[str, str],
    payload: dict[str, Any],
) -> str:
    """POST /run → job id."""
    try:
        resp = await client.post(
            f"{endpoint_base}/run", json=payload, headers=headers
        )
    except httpx.HTTPError as exc:
        raise RunpodTtsError(f"RunPod /run non raggiungibile: {exc}") from exc
    if resp.status_code != 200:
        raise RunpodTtsError(
            f"RunPod /run HTTP {resp.status_code}: {resp.text[:300]}"
        )
    data = resp.json()
    job_id = data.get("id")
    if not job_id:
        raise RunpodTtsError(f"RunPod /run senza job id: {data}")
    return str(job_id)


async def _collect_outputs(
    client: httpx.AsyncClient,
    *,
    endpoint_base: str,
    headers: dict[str, str],
    job_id: str,
    total: int,
    deadline: float,
    poll_s: int,
    on_segment_progress: Callable[[int, int], Awaitable[None]] | None,
) -> list[Any]:
    """Raccoglie gli output del job.

    Usa `/stream` (incrementale → progress per-segment); se `/stream`
    fallisce, passa a `/status` polling (output completo a job concluso).
    """
    collected: list[Any] = []
    use_stream = True

    async def _bump() -> None:
        # CancelledError (BaseException) NON viene soppressa da
        # suppress(Exception): propaga e interrompe lo stream quando il
        # job video viene annullato.
        if on_segment_progress is not None:
            with contextlib.suppress(Exception):  # pragma: no cover
                await on_segment_progress(min(len(collected), total), total)

    while True:
        if time.monotonic() > deadline:
            raise RunpodTimeoutError(
                f"Job RunPod {job_id} oltre il timeout configurato."
            )

        if use_stream:
            try:
                resp = await client.get(
                    f"{endpoint_base}/stream/{job_id}", headers=headers
                )
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                log.warning(
                    "runpod_tts_stream_fallback",
                    job_id=job_id,
                    error=str(exc),
                )
                use_stream = False
                continue
            data = resp.json()
            for item in data.get("stream") or []:
                out = item.get("output") if isinstance(item, dict) else None
                if out is not None:
                    collected.append(out)
            await _bump()
            status = data.get("status")
            if status == "COMPLETED":
                return collected
            if status in _FAILED_STATES:
                raise RunpodJobFailedError(
                    f"Job RunPod {status}: {data.get('error') or data}"
                )
        else:
            try:
                resp = await client.get(
                    f"{endpoint_base}/status/{job_id}", headers=headers
                )
            except httpx.HTTPError as exc:
                raise RunpodTtsError(
                    f"RunPod /status non raggiungibile: {exc}"
                ) from exc
            if resp.status_code != 200:
                raise RunpodTtsError(
                    f"RunPod /status HTTP {resp.status_code}: {resp.text[:300]}"
                )
            data = resp.json()
            status = data.get("status")
            if status == "COMPLETED":
                output = data.get("output")
                if isinstance(output, list):
                    return output
                return [output] if output is not None else collected
            if status in _FAILED_STATES:
                raise RunpodJobFailedError(
                    f"Job RunPod {status}: {data.get('error') or data}"
                )

        await asyncio.sleep(poll_s)
