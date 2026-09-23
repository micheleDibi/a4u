"""Guardia delle dipendenze opzionali nei test.

Un test che richiede una dipendenza assente (Docling, Chromium, TeX…) viene
saltato con il motivo esplicito `[dep:<nome>]`; se la variabile
`A4U_REQUIRED_DEPS` (lista separata da virgole) la nomina, invece fallisce:
nel container di test nessuno skip ambientale passa inosservato.
"""

from __future__ import annotations

import importlib
import os

import pytest


def required_deps() -> set[str]:
    raw = os.environ.get("A4U_REQUIRED_DEPS", "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def require_module(dep: str, module: str) -> None:
    try:
        importlib.import_module(module)
    except Exception as exc:
        if dep in required_deps():
            pytest.fail(f"[dep:{dep}] richiesta da A4U_REQUIRED_DEPS ma non disponibile: {exc}")
        pytest.skip(f"[dep:{dep}] non installata: {exc}")


def require_binary(dep: str, *names: str) -> None:
    """Salta (o fallisce, con `A4U_REQUIRED_DEPS`) se manca un binario."""
    import shutil

    missing = [name for name in names if shutil.which(name) is None]
    if missing:
        message = f"[dep:{dep}] binari assenti: {', '.join(missing)}"
        if dep in required_deps():
            pytest.fail(f"{message} (richiesti da A4U_REQUIRED_DEPS)")
        pytest.skip(message)
