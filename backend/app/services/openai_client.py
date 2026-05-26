"""Modulo condiviso per chiamate OpenAI.

Fornisce:
- gerarchia di errori comune (`OpenAIError`, `OpenAINotConfiguredError`),
- factory `get_client(timeout)` che costruisce un `httpx.AsyncClient`
  precompilato con base URL + Authorization,
- helper `parse_chat_message(resp_data)` per estrarre il `content`
  dell'unico choice di una Chat Completions, con gestione errori uniforme,
- helper `apply_reasoning_effort(body, model, effort)` per inserire il
  parametro `reasoning_effort` nel body solo se il modello supporta
  reasoning (gpt-5.x, o1, o3, o4) — sui modelli classici l'API rifiuta
  il parametro con 400.

Usato sia da `openai_translate_service` (traduzione UI / tassonomie) sia
dal nuovo `openai_summarize_service` (riassunto strutturato dei documenti
di corso).
"""
from __future__ import annotations

from typing import Any

import httpx

from app.core.config import get_settings


# Modelli reasoning OpenAI: prefissi che identificano la famiglia.
# `o1`, `o3`, `o4` sono la serie "thinking"; `gpt-5*` ha reasoning
# integrato per default. Ogni nuovo modello reasoning va aggiunto qui.
_REASONING_MODEL_PREFIXES = ("o1", "o3", "o4", "gpt-5")

# o1 / o1-mini / o1-preview NON accettano `minimal` come effort
# (introdotto solo con gpt-5.x). Su quei modelli normalizziamo a `low`.
_O1_PREFIXES = ("o1",)


def _is_reasoning_model(model: str) -> bool:
    name = (model or "").strip().lower()
    return name.startswith(_REASONING_MODEL_PREFIXES)


def apply_reasoning_effort(
    body: dict[str, Any], model: str, effort: str | None
) -> dict[str, Any]:
    """Aggiunge `reasoning_effort` a `body` se il modello lo supporta.

    No-op se il modello non è reasoning, o se `effort` è None / vuoto.
    Su o1* normalizza `minimal` → `low` (non supportato da quella famiglia).
    Ritorna lo stesso `body` (mutato in-place) per chaining ergonomico.
    """
    if not effort:
        return body
    if not _is_reasoning_model(model):
        return body
    normalized = effort.strip().lower()
    name = (model or "").strip().lower()
    if name.startswith(_O1_PREFIXES) and normalized == "minimal":
        normalized = "low"
    body["reasoning_effort"] = normalized
    return body


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


# Client httpx SINGLETON condiviso da tutti i servizi OpenAI.
# Prima costruivamo un AsyncClient nuovo ad ogni chiamata, il che con
# 150+ chiamate concorrenti (duplicazione corso) significava:
#   - 150 TLS handshake ridondanti (~50-200ms ciascuno persi)
#   - 150 pool di connessioni separati, niente connection reuse
#   - Saturazione facile dei socket / file descriptor
#   - `httpx.ReadTimeout` o `RemoteProtocolError` frequenti con
#     status=None (i log mostrano "[OpenAI None] Errore HTTP verso OpenAI: ")
#
# Singleton + Limits ampi + keepalive_expiry lungo:
#   - 1 sola istanza httpx.AsyncClient riusata
#   - Connection pool max 300 connessioni totali, 100 keepalive
#   - Keepalive expiry 60s -> connessioni TLS riusate per chiamate
#     consecutive verso lo stesso host (api.openai.com)
#   - Pool acquire timeout 60s -> evita PoolTimeout silenti
_shared_client: httpx.AsyncClient | None = None


class _SharedClientProxy:
    """Context manager che ritorna il client httpx singleton senza
    chiuderlo all'uscita dal `async with`. Mantiene compatibilita' con
    i call-site esistenti che usano `async with get_client() as c:`."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._client

    async def __aexit__(self, *_args: Any) -> None:
        # NO close: il client e' condiviso a livello processo.
        return None


def get_client(timeout: float = 600.0) -> _SharedClientProxy:
    """Ritorna un context manager attorno al client httpx singleton.

    Il parametro `timeout` e' mantenuto per backward compat ma e'
    IGNORATO: il timeout effettivo del client e' `read=600s` (sufficiente
    per output JSON lunghi di lesson_content/slides/speech). Per servizi
    che vogliono timeout piu' stretti, passare `timeout=N` direttamente
    a `client.post(url, json=body, timeout=N)`.
    """
    global _shared_client
    settings = get_settings()
    if not settings.openai_api_key:
        raise OpenAINotConfiguredError()
    if _shared_client is None:
        _shared_client = httpx.AsyncClient(
            base_url=settings.openai_base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(
                connect=10.0,
                read=600.0,
                write=30.0,
                pool=60.0,
            ),
            limits=httpx.Limits(
                max_connections=300,
                max_keepalive_connections=100,
                keepalive_expiry=60.0,
            ),
        )
    _ = timeout  # parametro accettato ma non usato (backward compat)
    return _SharedClientProxy(_shared_client)
