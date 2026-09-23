"""Trasporto condiviso `openai_http.post_chat_with_retry`.

Retry solo sui transient (errori di rete, 429 con Retry-After, 5xx), 4xx
terminali con l'errore del servizio chiamante, timeout per chiamata
passato a `client.post`, `OpenAINotConfiguredError` propagata senza retry.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.services import openai_http
from app.services.openai_client import OpenAIError, OpenAINotConfiguredError


class _ServiceError(OpenAIError):
    pass


class _Resp:
    def __init__(
        self, status: int, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> None:
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.text = ""

    def json(self) -> dict[str, Any]:
        return self._payload


class _Client:
    """`get_client` finto con una coda di esiti (risposta o eccezione)."""

    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.timeouts: list[float | None] = []

    def __call__(self, timeout: float = 600.0) -> _Client:
        return self

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def post(
        self, url: str, json: dict[str, Any] | None = None, timeout: float | None = None
    ) -> _Resp:
        assert url == "/chat/completions"
        self.timeouts.append(timeout)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, _Resp)
        return outcome


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(openai_http.asyncio, "sleep", fake_sleep)
    return slept


async def test_success_passes_timeout_to_post(
    monkeypatch: pytest.MonkeyPatch, no_sleep: list[float]
) -> None:
    client = _Client(_Resp(200, {"ok": True}))
    monkeypatch.setattr(openai_http, "get_client", client)
    data = await openai_http.post_chat_with_retry(
        {"model": "m"}, timeout=42.0, label="t", max_attempts=3
    )
    assert data == {"ok": True}
    assert client.timeouts == [42.0]
    assert no_sleep == []


async def test_retries_429_honouring_retry_after(
    monkeypatch: pytest.MonkeyPatch, no_sleep: list[float]
) -> None:
    client = _Client(
        _Resp(429, {}, {"retry-after": "7"}),
        _Resp(503, {}),
        _Resp(200, {"ok": 1}),
    )
    monkeypatch.setattr(openai_http, "get_client", client)
    data = await openai_http.post_chat_with_retry({}, timeout=5.0, label="t", max_attempts=3)
    assert data == {"ok": 1}
    assert len(no_sleep) == 2
    assert no_sleep[0] >= 7.0


async def test_terminal_4xx_raises_caller_error_with_payload(
    monkeypatch: pytest.MonkeyPatch, no_sleep: list[float]
) -> None:
    payload = {"error": {"message": "schema non valido"}}
    client = _Client(_Resp(400, payload))
    monkeypatch.setattr(openai_http, "get_client", client)
    with pytest.raises(_ServiceError) as info:
        await openai_http.post_chat_with_retry(
            {}, timeout=5.0, label="t", max_attempts=4, error_cls=_ServiceError
        )
    assert info.value.status == 400
    assert info.value.message == "schema non valido"
    assert info.value.payload == payload
    assert no_sleep == []


async def test_transient_http_error_exhausts_attempts(
    monkeypatch: pytest.MonkeyPatch, no_sleep: list[float]
) -> None:
    client = _Client(httpx.ReadTimeout("lento"), httpx.ReadTimeout("lento"))
    monkeypatch.setattr(openai_http, "get_client", client)
    with pytest.raises(_ServiceError) as info:
        await openai_http.post_chat_with_retry(
            {}, timeout=5.0, label="t", max_attempts=2, error_cls=_ServiceError
        )
    assert info.value.status is None
    assert len(no_sleep) == 1


async def test_not_configured_propagates_without_retry(
    monkeypatch: pytest.MonkeyPatch, no_sleep: list[float]
) -> None:
    def refuse(timeout: float = 600.0) -> _Client:
        raise OpenAINotConfiguredError()

    monkeypatch.setattr(openai_http, "get_client", refuse)
    with pytest.raises(OpenAINotConfiguredError):
        await openai_http.post_chat_with_retry({}, timeout=5.0, label="t", max_attempts=5)
    assert no_sleep == []
