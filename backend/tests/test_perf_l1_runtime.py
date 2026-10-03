"""Prestazioni L1, task B1: JIT spento, monitor del lag dell'event loop, access log.

- Le connessioni dell'app (engine di `app/db/session.py`) hanno `jit=off`.
- Il monitor (`core/loop_monitor.py`) segnala `event_loop_lag` quando una chiamata sincrona
  ferma il loop e scrive il riepilogo `event_loop_lag_summary`.
- `http_request` (middleware/access_log.py) riporta `response_bytes` da `Content-Length`.
- `uvicorn.access` è a WARNING: resta una sola riga di log per request.
"""

from __future__ import annotations

import asyncio
import logging
import time

import structlog
from httpx import AsyncClient
from sqlalchemy import text
from starlette.responses import JSONResponse, StreamingResponse

# Catturati all'import (fase di raccolta, prima di ogni fixture): la fixture `client`
# di conftest sostituisce `session.engine` e `session.async_session_factory` con quelli
# del test, mentre qui si vuole proprio la factory di produzione.
from app.db.session import async_session_factory as app_session_factory
from app.db.session import engine as app_engine


async def test_app_session_has_jit_off() -> None:
    try:
        async with app_session_factory() as session:
            assert (await session.execute(text("SHOW jit"))).scalar_one() == "off"
    finally:
        await app_engine.dispose()


async def test_test_engine_has_jit_off(db) -> None:
    """Anche l'engine dei test (conftest) usa gli stessi parametri di connessione."""
    assert (await db.execute(text("SHOW jit"))).scalar_one() == "off"


def test_monitor_constants_match_contract() -> None:
    from app.core import loop_monitor

    assert loop_monitor.LOOP_LAG_INTERVAL_S == 0.5
    assert loop_monitor.LOOP_LAG_THRESHOLD_MS == 100
    assert loop_monitor.LOOP_LAG_SUMMARY_INTERVAL_S == 60.0


async def test_monitor_detects_blocking_call() -> None:
    from app.core.loop_monitor import start_monitor, stop_monitor

    with structlog.testing.capture_logs() as logs:
        # Intervallo corto: il blocco da 0,3 s copre per forza il risveglio di un campione.
        start_monitor(interval_s=0.05, threshold_ms=100, summary_interval_s=0.2)
        try:
            await asyncio.sleep(0.12)
            time.sleep(0.3)  # chiamata sincrona che ferma il loop
            await asyncio.sleep(0.3)
        finally:
            await stop_monitor()

    lag = [e for e in logs if e["event"] == "event_loop_lag"]
    assert lag, logs
    assert all(e["log_level"] == "warning" and e["threshold_ms"] == 100 for e in lag)
    assert all(isinstance(e["lag_ms"], int) for e in lag)
    assert max(e["lag_ms"] for e in lag) >= 200

    summaries = [e for e in logs if e["event"] == "event_loop_lag_summary"]
    assert summaries, logs
    assert summaries[0]["log_level"] == "info"
    assert set(summaries[0]) >= {"samples", "p50_ms", "p99_ms", "max_ms"}
    assert max(s["max_ms"] for s in summaries) >= 200
    assert all(s["samples"] >= 1 and s["p50_ms"] <= s["p99_ms"] <= s["max_ms"] for s in summaries)


async def test_monitor_quiet_without_blocking_and_restartable() -> None:
    from app.core.loop_monitor import start_monitor, stop_monitor

    with structlog.testing.capture_logs() as logs:
        start_monitor(interval_s=0.02, threshold_ms=100, summary_interval_s=60)
        start_monitor(interval_s=0.02)  # idempotente: nessun secondo task
        await asyncio.sleep(0.1)
        await stop_monitor()
        await stop_monitor()  # già fermo: nessun errore
    assert not [e for e in logs if e["event"] == "event_loop_lag"]
    assert not [t for t in asyncio.all_tasks() if t.get_name() == "event_loop_lag_monitor"]


async def test_http_request_log_has_response_bytes(client: AsyncClient) -> None:
    with structlog.testing.capture_logs() as logs:
        resp = await client.get("/api/v1/system/health")
    assert resp.status_code == 200
    entries = [e for e in logs if e["event"] == "http_request"]
    assert len(entries) == 1
    assert entries[0]["response_bytes"] == len(resp.content) > 0
    assert entries[0]["path"] == "/api/v1/system/health"


def test_content_length_helper() -> None:
    from app.middleware.access_log import _content_length

    assert _content_length(JSONResponse({"a": 1})) == len(b'{"a":1}')
    # Streaming: nessun Content-Length → null nel log.
    assert _content_length(StreamingResponse(iter([b"x"]))) is None


def test_uvicorn_access_logger_at_warning(monkeypatch) -> None:
    """`configure_logging` porta `uvicorn.access` a WARNING; `uvicorn.error` resta a INFO.

    `structlog.configure` è neutralizzato e i logger stdlib toccati sono ripristinati:
    il test non cambia la configurazione di log degli altri test."""
    from app.core.config import get_settings
    from app.core.logging import configure_logging

    names = ["", "uvicorn", "uvicorn.access", "uvicorn.error", "sqlalchemy.engine"]
    names += ["httpx", "httpcore"]
    saved = {
        n: (
            logging.getLogger(n).handlers[:],
            logging.getLogger(n).level,
            logging.getLogger(n).propagate,
        )
        for n in names
    }
    monkeypatch.setattr(structlog, "configure", lambda **_: None)
    try:
        configure_logging(get_settings())
        assert logging.getLogger("uvicorn.access").level == logging.WARNING
        assert logging.getLogger("uvicorn.error").level == logging.INFO
        assert logging.getLogger("uvicorn").level == logging.INFO
    finally:
        for n, (handlers, level, propagate) in saved.items():
            lg = logging.getLogger(n)
            lg.handlers = handlers
            lg.setLevel(level)
            lg.propagate = propagate
