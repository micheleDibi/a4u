"""Bersagli per i test di `run_isolated`: importabili dal figlio `spawn`."""

from __future__ import annotations

import time
from typing import Any


def sleep_forever(payload: Any) -> Any:
    """Non risponde mai: il padre deve uccidere il figlio allo scadere."""
    while True:
        time.sleep(0.2)


def echo(payload: Any) -> Any:
    return payload


def big_result(payload: Any) -> str:
    """Risultato più grande del buffer della pipe (`payload["size"]` byte):
    senza `poll` prima di `recv` il padre andrebbe in stallo."""
    return "x" * int(payload["size"])


def boom(payload: Any) -> Any:
    raise ValueError(f"fallimento voluto: {payload}")
