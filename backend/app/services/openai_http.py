"""Trasporto condiviso verso POST /chat/completions con retry sui transient.

Estratto da `openai_summarize_service._post_chat_with_retry` perché serve
anche ai servizi nuovi delle figure (descrizione Vision, revisore delle
ridondanze, verifica di pertinenza). Errori, tetto dei tentativi e prefisso
dei log sono parametri: ogni servizio mantiene i propri.

Il timeout è per chiamata e va passato a `client.post`: il parametro di
`get_client(timeout=...)` è un no-op (il client condiviso ha read=600 s).
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx

from app.core.logging import get_logger
from app.services.openai_client import (
    OpenAIError,
    OpenAINotConfiguredError,
    get_client,
)

log = get_logger("app.openai_http")

RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


def retry_after_seconds(resp: httpx.Response) -> float | None:
    """Secondi indicati da `Retry-After` (solo la forma numerica)."""
    value = resp.headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


async def post_chat_with_retry(
    body: dict[str, Any],
    *,
    timeout: float,
    label: str,
    max_attempts: int,
    error_cls: type[OpenAIError] = OpenAIError,
    log_prefix: str = "openai_http",
) -> dict[str, Any]:
    """POST /chat/completions con retry esponenziale + jitter sui soli
    errori transient (rete/timeout, 429 onorando Retry-After, 5xx).

    Gli altri 4xx sono terminali immediati e sollevano `error_cls` con il
    payload. `OpenAINotConfiguredError` si propaga senza retry.
    """
    attempts = max(1, int(max_attempts))
    delay = 1.0
    for attempt in range(1, attempts + 1):
        try:
            async with get_client() as client:
                resp = await client.post("/chat/completions", json=body, timeout=timeout)
        except OpenAINotConfiguredError:
            raise
        except httpx.HTTPError as exc:
            if attempt < attempts:
                sleep_for = delay + random.uniform(0, delay / 2)
                log.warning(
                    f"{log_prefix}_retry_transient",
                    label=label,
                    attempt=attempt,
                    error=str(exc),
                    sleep=round(sleep_for, 1),
                )
                await asyncio.sleep(sleep_for)
                delay *= 2
                continue
            raise error_cls(status=None, message=f"Errore HTTP verso OpenAI: {exc}") from exc

        if resp.status_code < 400:
            data: dict[str, Any] = resp.json()
            return data

        if resp.status_code in RETRYABLE_STATUSES and attempt < attempts:
            retry_after = retry_after_seconds(resp)
            sleep_for = max(delay, retry_after or 0.0) + random.uniform(0, delay / 2)
            log.warning(
                f"{log_prefix}_retry_status",
                label=label,
                attempt=attempt,
                status=resp.status_code,
                sleep=round(sleep_for, 1),
            )
            await asyncio.sleep(sleep_for)
            delay *= 2
            continue

        try:
            payload = resp.json()
        except Exception:
            payload = {"text": resp.text}
        message = payload.get("error", {}).get("message") if isinstance(payload, dict) else None
        log.error(
            f"{log_prefix}_api_error",
            label=label,
            status=resp.status_code,
            message=message or "unknown",
        )
        raise error_cls(
            status=resp.status_code,
            message=message or f"OpenAI ha risposto con HTTP {resp.status_code}.",
            payload=payload,
        )
    raise error_cls(  # pragma: no cover — difensivo
        status=None, message="Tentativi OpenAI esauriti."
    )
