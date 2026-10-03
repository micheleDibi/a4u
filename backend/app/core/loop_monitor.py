"""Monitor del ritardo (lag) dell'event loop.

In produzione API e worker condividono un solo event loop: una chiamata sincrona dentro una
coroutine ferma tutte le request. Questo task misura di quanto si allunga un
`asyncio.sleep` rispetto al previsto e lo scrive nel log:

- `event_loop_lag` (WARNING) a ogni campione oltre soglia, con `lag_ms` e `threshold_ms`;
- `event_loop_lag_summary` (INFO) a intervalli fissi, con `samples`, `p50_ms`, `p99_ms`,
  `max_ms` dei campioni raccolti nell'intervallo.

Si avvia dopo `startup_complete` e si ferma nel `finally` del lifespan (`app/main.py`).
"""

from __future__ import annotations

import asyncio
import contextlib
import math

from app.core.logging import get_logger

log = get_logger("app.loop_monitor")

# Durata dello sleep di misura: ogni campione è il ritardo oltre questo valore.
LOOP_LAG_INTERVAL_S = 0.5
# Oltre questa soglia di ritardo un campione genera un WARNING `event_loop_lag`.
LOOP_LAG_THRESHOLD_MS = 100
# Ogni quanti secondi si scrive il riepilogo INFO `event_loop_lag_summary`.
LOOP_LAG_SUMMARY_INTERVAL_S = 60.0

_monitor_task: asyncio.Task[None] | None = None


def _percentile(ordered: list[int], fraction: float) -> int:
    """Percentile a rango più vicino su una lista già ordinata e non vuota."""
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def _log_summary(samples: list[int]) -> None:
    if not samples:
        return
    ordered = sorted(samples)
    log.info(
        "event_loop_lag_summary",
        samples=len(ordered),
        p50_ms=_percentile(ordered, 0.50),
        p99_ms=_percentile(ordered, 0.99),
        max_ms=ordered[-1],
    )


async def _run(interval_s: float, threshold_ms: int, summary_interval_s: float) -> None:
    loop = asyncio.get_running_loop()
    samples: list[int] = []
    last_summary = loop.time()
    while True:
        started = loop.time()
        await asyncio.sleep(interval_s)
        now = loop.time()
        lag_ms = max(0, round((now - started - interval_s) * 1000))
        samples.append(lag_ms)
        if lag_ms > threshold_ms:
            log.warning("event_loop_lag", lag_ms=lag_ms, threshold_ms=threshold_ms)
        if now - last_summary >= summary_interval_s:
            _log_summary(samples)
            samples = []
            last_summary = now


def start_monitor(
    *,
    interval_s: float = LOOP_LAG_INTERVAL_S,
    threshold_ms: int = LOOP_LAG_THRESHOLD_MS,
    summary_interval_s: float = LOOP_LAG_SUMMARY_INTERVAL_S,
) -> None:
    """Avvia il monitor sul loop corrente (idempotente). I parametri servono ai test."""
    global _monitor_task
    if _monitor_task is not None and not _monitor_task.done():
        return
    _monitor_task = asyncio.create_task(
        _run(interval_s, threshold_ms, summary_interval_s), name="event_loop_lag_monitor"
    )


async def stop_monitor() -> None:
    """Ferma il monitor (senza riepilogo finale: lo spegnimento non è un campione utile)."""
    global _monitor_task
    task, _monitor_task = _monitor_task, None
    if task is None:
        return
    task.cancel()
    # Un errore del monitor non deve interrompere lo spegnimento dell'app.
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
