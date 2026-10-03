"""Ticker di avanzamento comune ai worker AI (Prestazioni L1, W4).

Durante la chiamata lunga di una fase (OpenAI, render del PDF) il worker
lancia un task di sfondo che fa salire la percentuale mostrata dalla UI.
Prima il ticker seguiva una curva a tempo e si fermava a `end_pct` dopo
`duration_sec`: un job più lungo della stima restava fermo per decine di
secondi. Ora:

- la curva ease-out di prima copre solo `CURVE_SHARE` dello span in
  `duration_sec`;
- poi (e ovunque la curva rallenti) il valore sale di almeno un punto ogni
  `MAX_STALL_SEC` secondi, fino al tetto `end_pct - 1`: il valore finale lo
  scrive solo il worker;
- nessuna lettura della riga: una `UPDATE` condizionata (status attivo e
  progress più basso) per ogni punto in più, e solo se non tocca righe una
  `SELECT` della SOLA colonna di status per sapere se fermarsi.

Ogni worker tiene il suo `_progress_ticker` (nome e firma usati dai test) e
lo implementa con `run_progress_ticker`, passando la propria
`async_session_factory`.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

# Quota dello span `start_pct → end_pct` coperta dalla curva ease-out in
# `duration_sec`; il resto è la coda che avanza un punto alla volta.
CURVE_SHARE = 0.85
# Secondi massimi fra due punti in più finché il job è in corso e il valore
# è sotto `end_pct - 1`.
MAX_STALL_SEC = 10.0

# Orologio e attesa del ticker: attributi di modulo perché i test li
# sostituiscono con un clock finto.
_monotonic: Callable[[], float] = time.monotonic
_sleep: Callable[[float], Any] = asyncio.sleep


def curve_target(elapsed: float, *, start_pct: int, end_pct: int, duration_sec: float) -> int:
    """Valore della curva ease-out dopo `elapsed` secondi: veloce all'inizio,
    piatta a `start_pct + CURVE_SHARE·span` da `duration_sec` in poi."""
    span = max(1, end_pct - start_pct)
    ratio = min(1.0, max(0.0, elapsed) / duration_sec) if duration_sec > 0 else 1.0
    eased = 1 - (1 - ratio) ** 2
    return start_pct + int(span * CURVE_SHARE * eased)


def next_target(
    *,
    elapsed: float,
    last: int,
    last_bump: float,
    tick_sec: float,
    start_pct: int,
    end_pct: int,
    duration_sec: float,
) -> int:
    """Valore da scrivere a questo tick (`last` se nulla cambia).

    `last` è l'ultimo valore scritto (o `start_pct`), `last_bump` l'istante
    (secondi dall'avvio) in cui è salito. Il valore non scende mai, non
    supera `end_pct - 1` e sale di un punto quando aspettare il tick
    successivo porterebbe la pausa oltre `MAX_STALL_SEC`."""
    cap = end_pct - 1
    target = max(
        last,
        curve_target(elapsed, start_pct=start_pct, end_pct=end_pct, duration_sec=duration_sec),
    )
    if target <= last and elapsed - last_bump + tick_sec >= MAX_STALL_SEC:
        target = last + 1
    return min(cap, target)


async def run_progress_ticker(
    *,
    session_factory: Callable[[], AsyncSession],
    model: Any,
    row_id: Any,
    status_attr: str,
    active_status: str,
    progress_attr: str,
    start_pct: int,
    end_pct: int,
    duration_sec: float,
    tick_sec: float,
) -> None:
    """Fa salire `model.<progress_attr>` della riga `row_id` finché
    `model.<status_attr>` vale `active_status` (vedi docstring del modulo).

    Si ferma al tetto `end_pct - 1`, quando lo status cambia o la riga
    sparisce, e quando il task viene cancellato (il worker lo cancella a
    fine chiamata). Sessione DB propria per ogni scrittura, come prima: la
    transazione del worker resta libera."""
    status_col = getattr(model, status_attr)
    progress_col = getattr(model, progress_attr)
    cap = end_pct - 1
    started = _monotonic()
    last, last_bump = start_pct, 0.0
    try:
        while last < cap:
            await _sleep(tick_sec)
            elapsed = _monotonic() - started
            target = next_target(
                elapsed=elapsed,
                last=last,
                last_bump=last_bump,
                tick_sec=tick_sec,
                start_pct=start_pct,
                end_pct=end_pct,
                duration_sec=duration_sec,
            )
            if target <= last:
                continue
            async with session_factory() as tdb:
                res = await tdb.execute(
                    update(model)
                    .where(model.id == row_id, status_col == active_status, progress_col < target)
                    .values({progress_col: target})
                    .execution_options(synchronize_session=False)
                )
                if getattr(res, "rowcount", 0) == 0:
                    # Nessuna riga toccata: job finito (o riga sparita),
                    # oppure il worker ha già scritto un valore più alto.
                    status = await tdb.scalar(select(status_col).where(model.id == row_id))
                    if status != active_status:
                        return
                await tdb.commit()
            last, last_bump = target, elapsed
    except asyncio.CancelledError:
        return
