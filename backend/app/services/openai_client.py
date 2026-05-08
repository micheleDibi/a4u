"""Modulo condiviso per chiamate OpenAI.

Fornisce:
- gerarchia di errori comune (`OpenAIError`, `OpenAINotConfiguredError`),
- factory `get_client(timeout)` che costruisce un `httpx.AsyncClient`
  precompilato con base URL + Authorization,
- helper `parse_chat_message(resp_data)` per estrarre il `content`
  dell'unico choice di una Chat Completions, con gestione errori uniforme.

Usato sia da `openai_translate_service` (traduzione UI / tassonomie) sia
dal nuovo `openai_summarize_service` (riassunto strutturato dei documenti
di corso).
"""
from __future__ import annotations

from typing import Any

import httpx

from app.core.config import get_settings


class OpenAIError(Exception):
    """Errore generico di chiamata a OpenAI (HTTP error, malformed JSON, ecc.)."""

    def __init__(
        self, status: int | None, message: str, *, payload: Any = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.payload = payload

    def __str__(self) -> str:
        return f"[OpenAI {self.status}] {self.message}"


class OpenAINotConfiguredError(OpenAIError):
    """OPENAI_API_KEY mancante: la feature AI è disabilitata fino a configurazione."""

    def __init__(self) -> None:
        super().__init__(
            status=None,
            message=(
                "OPENAI_API_KEY non configurata: la funzionalità AI è "
                "disabilitata. Imposta OPENAI_API_KEY nel file .env."
            ),
        )


def get_client(timeout: float = 120.0) -> httpx.AsyncClient:
    """Costruisce un client HTTP per OpenAI. Solleva se la API key manca."""
    settings = get_settings()
    if not settings.openai_api_key:
        raise OpenAINotConfiguredError()
    return httpx.AsyncClient(
        base_url=settings.openai_base_url.rstrip("/"),
        headers={
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        },
        timeout=timeout,
    )
