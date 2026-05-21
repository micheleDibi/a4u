"""Client MiniMax per generazione clip image-to-video.

Documentazione di riferimento:
https://platform.minimax.io/docs/guides/video-generation

Flusso:
  1. POST /v1/video_generation con `first_frame_image` (image-to-video)
     e `prompt`. Ritorna `task_id`.
  2. Polling GET /v1/query/video_generation?task_id=... ogni 10s.
     Quando lo status è `success`, la risposta contiene `file_id`.
  3. GET /v1/files/retrieve?file_id=... ritorna il `download_url` reale.
  4. Stream del binario MP4.

Errori → MinimaxError(status, message). Le funzioni sono asincrone.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger("app.minimax")


class MinimaxError(Exception):
    def __init__(self, status: int | None, message: str, *, payload: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.payload = payload

    def __str__(self) -> str:
        return f"[MiniMax {self.status}] {self.message}"


class MinimaxNotConfiguredError(MinimaxError):
    def __init__(self) -> None:
        super().__init__(
            status=None,
            message="MINIMAX_API_KEY non configurata: la generazione clip è disabilitata.",
        )


@dataclass(frozen=True)
class TaskStatus:
    status: str  # 'preparing' | 'processing' | 'success' | 'failed' | 'unknown'
    file_id: str | None
    raw: dict[str, Any]


def _client(timeout: float = 60.0) -> httpx.AsyncClient:
    settings = get_settings()
    if not settings.minimax_api_key:
        raise MinimaxNotConfiguredError()
    return httpx.AsyncClient(
        base_url=settings.minimax_base_url.rstrip("/"),
        headers={
            "Authorization": f"Bearer {settings.minimax_api_key}",
            "Content-Type": "application/json",
        },
        timeout=timeout,
    )


def _extract_status_code(payload: dict[str, Any]) -> tuple[int | None, str | None]:
    base = payload.get("base_resp") if isinstance(payload, dict) else None
    if isinstance(base, dict):
        return base.get("status_code"), base.get("status_msg")
    return None, None


async def start_video_generation(
    *,
    image_url: str,
    prompt: str,
    duration: int | None = None,
) -> str:
    """Avvia un job MiniMax image-to-video dal `first_frame_image`.

    Niente `last_frame_image`: MiniMax-Hailuo-2.3 non supporta la
    modalità First-and-Last-Frame-Video. La loopabilità della clip è
    guidata dal prompt ("seamless looping animation").
    """
    settings = get_settings()
    body = {
        "model": settings.minimax_video_model,
        "prompt": prompt[:1990],
        "first_frame_image": image_url,
        "duration": duration if duration is not None else settings.minimax_clip_duration,
        "resolution": settings.minimax_clip_resolution,
    }
    async with _client() as client:
        try:
            resp = await client.post("/v1/video_generation", json=body)
        except httpx.HTTPError as exc:
            raise MinimaxError(None, f"Errore di rete: {exc}") from exc
    if resp.status_code >= 500:
        raise MinimaxError(resp.status_code, f"Errore server MiniMax: {resp.text[:200]}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise MinimaxError(resp.status_code, "Risposta non JSON") from exc

    code, msg = _extract_status_code(data)
    task_id = data.get("task_id")
    if resp.status_code >= 400 or (code not in (0, None) and not task_id):
        raise MinimaxError(
            resp.status_code,
            msg or f"Avvio task fallito: {data}",
            payload=data,
        )
    if not task_id:
        raise MinimaxError(resp.status_code, "task_id mancante nella risposta", payload=data)
    log.info("minimax_task_started", task_id=task_id)
    return str(task_id)


async def query_task_status(task_id: str) -> TaskStatus:
    """Polling status di un task. Non rilancia su 4xx 'task_not_found'."""
    async with _client(timeout=30.0) as client:
        try:
            resp = await client.get(
                "/v1/query/video_generation", params={"task_id": task_id}
            )
        except httpx.HTTPError as exc:
            raise MinimaxError(None, f"Errore di rete: {exc}") from exc
    if resp.status_code >= 500:
        raise MinimaxError(resp.status_code, f"Errore server MiniMax: {resp.text[:200]}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise MinimaxError(resp.status_code, "Risposta non JSON") from exc

    raw_status = str(data.get("status") or "unknown").lower()
    # MiniMax usa: "Preparing" | "Queueing" | "Processing" | "Success" | "Fail"
    if raw_status in {"preparing", "queueing", "queued"}:
        status = "preparing"
    elif raw_status == "processing":
        status = "processing"
    elif raw_status == "success":
        status = "success"
    elif raw_status in {"fail", "failed"}:
        status = "failed"
    else:
        status = "unknown"

    file_id = data.get("file_id") or None
    return TaskStatus(status=status, file_id=str(file_id) if file_id else None, raw=data)


async def download_file(file_id: str) -> bytes:
    """Recupera l'URL di download e scarica il file binario."""
    async with _client(timeout=30.0) as client:
        try:
            meta_resp = await client.get(
                "/v1/files/retrieve", params={"file_id": file_id}
            )
        except httpx.HTTPError as exc:
            raise MinimaxError(None, f"Errore di rete: {exc}") from exc
    if meta_resp.status_code >= 400:
        raise MinimaxError(
            meta_resp.status_code,
            f"Recupero file fallito: {meta_resp.text[:200]}",
        )
    try:
        meta = meta_resp.json()
    except ValueError as exc:
        raise MinimaxError(meta_resp.status_code, "Risposta non JSON") from exc

    file_meta = meta.get("file") if isinstance(meta, dict) else None
    download_url = (
        (file_meta or {}).get("download_url")
        if isinstance(file_meta, dict)
        else meta.get("download_url")
    )
    if not download_url:
        raise MinimaxError(meta_resp.status_code, "download_url mancante", payload=meta)

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, read=120.0)) as plain:
        try:
            file_resp = await plain.get(download_url)
        except httpx.HTTPError as exc:
            raise MinimaxError(None, f"Errore download: {exc}") from exc
    if file_resp.status_code >= 400:
        raise MinimaxError(
            file_resp.status_code,
            f"Download fallito: {file_resp.text[:200]}",
        )
    log.info("minimax_file_downloaded", file_id=file_id, size=len(file_resp.content))
    return file_resp.content
