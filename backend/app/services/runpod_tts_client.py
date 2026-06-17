"""Client per il servizio TTS XTTS-v2 su RunPod Serverless.

Sostituisce il vecchio `xtts_voice_clone_service` in-process: la sintesi
vocale gira ora su GPU remota (vedi la cartella `XTTS/` del repo). Questo
modulo e' un client HTTP puro — nessuna dipendenza torch/coqui.

Flusso: invia 1 job per video (tutti i segment), consuma lo stream
incrementale di RunPod (l'handler invia l'audio per CHUNK, FLAC base64),
ricompone i chunk per segment e decodifica in array numpy float32
@ 24000 Hz — stesso contratto del vecchio TTS.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import time
from collections.abc import Awaitable, Callable
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
    voice_sample_url: str,
    language_code: str,
    on_segment_progress: Callable[[int, int], Awaitable[None]] | None = None,
) -> tuple[dict[str, np.ndarray], int]:
    """Sintetizza l'audio di tutti i segment della lezione via RunPod.

    Args:
        speech_raw: dict con `speech_segments` (lista di {segment_id, text, ...}).
        voice_sample_url: URL pubblico del campione vocale dell'assegnatario,
            scaricabile via HTTP dal worker RunPod GPU (su OVH è l'URL del
            file sullo storage; in locale `{public_base_url}/uploads/...`).
            Viaggia come URL — non inline base64 — perché un audio di pochi
            MB sforerebbe il limite di payload di `/run`.
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

    if not voice_sample_url:
        raise RunpodTtsError("Voice sample URL mancante.")

    language = normalize_language_code(language_code)

    # Il voice sample viaggia come URL: il worker RunPod lo scarica. Il
    # payload del job resta leggero (solo URL + testo dei segment).
    payload = {
        "input": {
            "language_code": language,
            "voice_sample_url": voice_sample_url,
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

    # Gli output sono CHUNK audio (più chunk per `segment_id`, ciascuno
    # con `chunk_index`). Raggruppa per segment, ordina i chunk e
    # concatena. Retro-compatibile con l'handler vecchio (1 output per
    # segment, senza `chunk_index` → trattato come chunk 0).
    chunks_by_segment: dict[str, list[tuple[int, np.ndarray]]] = {}
    for out in outputs:
        if not isinstance(out, dict):
            continue
        if out.get("error"):
            raise RunpodJobFailedError(f"Worker TTS RunPod: {out['error']}")
        sid = str(out.get("segment_id") or "")
        audio_b64 = out.get("audio_b64")
        if not sid or not audio_b64:
            continue
        chunk_index = int(out.get("chunk_index") or 0)
        chunks_by_segment.setdefault(sid, []).append(
            (chunk_index, _decode_segment_audio(audio_b64))
        )

    audio_per_segment: dict[str, np.ndarray] = {}
    for sid, parts in chunks_by_segment.items():
        parts.sort(key=lambda p: p[0])
        audio_per_segment[sid] = np.concatenate([a for _idx, a in parts])

    if not audio_per_segment:
        raise RunpodJobFailedError("Il job RunPod non ha prodotto alcun audio.")

    # Completezza: ogni segment richiesto DEVE avere audio. Un audio
    # incompleto darebbe un video monco e desincronizzato → meglio
    # fallire qui (il worker fa auto-retry) che produrre un video rotto.
    requested_ids = {s["segment_id"] for s in segments}
    missing_ids = requested_ids - set(audio_per_segment)
    if missing_ids:
        raise RunpodJobFailedError(
            f"Audio TTS incompleto: ricevuti {len(audio_per_segment)}/"
            f"{len(requested_ids)} segmenti "
            f"(mancanti: {sorted(missing_ids)[:8]})."
        )

    log.info(
        "runpod_tts_job_done",
        job_id=job_id,
        segments_received=len(audio_per_segment),
        segments_requested=len(requested_ids),
        chunks_received=len(outputs),
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
    """Raccoglie gli output (chunk audio) del job.

    Usa `/stream` (incrementale → progress per-segment). A job
    `COMPLETED` continua a drenare `/stream` finché una risposta torna
    senza nuovi item: lo stream è incrementale e a job concluso può
    restare ancora output bufferizzato — uscire al primo `COMPLETED`
    perderebbe i chunk finali. Se `/stream` fallisce, passa a `/status`
    polling (output completo a job concluso).
    """
    collected: list[Any] = []
    seen_segments: set[str] = set()
    use_stream = True

    def _track(out: Any) -> None:
        collected.append(out)
        if isinstance(out, dict):
            sid = out.get("segment_id")
            if sid:
                seen_segments.add(str(sid))

    async def _bump() -> None:
        # Progress sui SEGMENT distinti visti (gli output sono chunk:
        # più chunk per segment). CancelledError (BaseException) NON
        # viene soppressa da suppress(Exception): propaga e interrompe
        # lo stream quando il job video viene annullato.
        if on_segment_progress is not None:
            with contextlib.suppress(Exception):  # pragma: no cover
                await on_segment_progress(min(len(seen_segments), total), total)

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
            stream_items = data.get("stream") or []
            for item in stream_items:
                out = item.get("output") if isinstance(item, dict) else None
                if out is not None:
                    _track(out)
            await _bump()
            status = data.get("status")
            if status in _FAILED_STATES:
                raise RunpodJobFailedError(
                    f"Job RunPod {status}: {data.get('error') or data}"
                )
            if status == "COMPLETED":
                # Job concluso: continua a drenare `/stream` finché una
                # risposta non torna senza nuovi item — solo allora
                # tutti i chunk sono stati raccolti.
                if not stream_items:
                    return collected
                await asyncio.sleep(0.5)
                continue
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
