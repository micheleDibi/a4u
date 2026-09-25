"""Esecuzione del sottoprocesso di estrazione nei test (come in produzione:
processo separato, cartella temporanea, ambiente ridotto)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.services.document_figures.runner import child_env

BACKEND = Path(__file__).resolve().parents[1]


def run_child(
    workdir: Path,
    *,
    source: str,
    mime: str,
    engine: str,
    blocks: list[list[int]],
    artifacts_path: str | None = None,
    probe: bool = False,
    timeout: float = 900,
    crop_version: int | None = None,
) -> tuple[int, list[dict[str, Any]], str]:
    job: dict[str, Any] = {
        "source": source,
        "mime": mime,
        "engine": engine,
        "artifacts_path": artifacts_path,
        "threads": 1,
    }
    if crop_version is not None:
        job["crop_version"] = crop_version
    lines = [json.dumps(job), *(json.dumps({"pages": block}) for block in blocks)]
    # Stesso ambiente che usa il worker (allowlist, niente segreti).
    env = child_env(workdir, threads=1, artifacts_path=artifacts_path)
    args = [sys.executable, "-m", "app.services.document_figures.child"]
    if probe:
        args.append("--probe")
    proc = subprocess.run(
        args,
        input="\n".join(lines) + "\n",
        capture_output=True,
        text=True,
        cwd=workdir,
        env=env,
        timeout=timeout,
    )
    events = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    return proc.returncode, events, proc.stderr


def run_recrop(
    workdir: Path,
    *,
    source: str,
    mime: str,
    figures: list[dict[str, Any]],
    identity_max_distance: int = 4,
    timeout: float = 900,
) -> tuple[int, list[dict[str, Any]], str]:
    """Comando `recrop` del figlio (ri-ritaglio delle figure già estratte)."""
    job = {
        "command": "recrop",
        "source": source,
        "mime": mime,
        "figures": figures,
        "identity_max_distance": identity_max_distance,
        "crop_version": 2,
    }
    env = child_env(workdir, threads=1, artifacts_path=None)
    proc = subprocess.run(
        [sys.executable, "-m", "app.services.document_figures.child"],
        input=json.dumps(job) + "\n",
        capture_output=True,
        text=True,
        cwd=workdir,
        env=env,
        timeout=timeout,
    )
    events = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    return proc.returncode, events, proc.stderr
