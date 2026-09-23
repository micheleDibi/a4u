"""Lato padre del sottoprocesso di estrazione (J-Q3).

- Ambiente in allowlist (:func:`child_env`): nessun segreto dell'app; HOME e
  TMPDIR nella cartella temporanea; thread BLAS/OpenMP limitati; Hugging
  Face offline; `PYTHONPATH` uguale al `sys.path` del padre.
- `start_new_session=True` e kill del gruppo di processi su timeout, RSS
  oltre soglia o chiusura.
- Protocollo a righe JSON (:mod:`.child`): un blocco di pagine per volta,
  così il padre controlla la memoria disponibile prima di ogni blocco e
  ricicla il figlio ogni `pages_per_child` pagine.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CHILD_MODULE = "app.services.document_figures.child"
# Righe finali di stderr conservate per la diagnosi.
STDERR_TAIL_BYTES = 4000
_RSS_POLL_SECONDS = 0.5


class ExtractionChildError(Exception):
    """Il figlio non ha completato il lavoro. `code` è un valore di
    `FIGURES_ERROR_CODES` (engine_unavailable, encrypted, corrupt,
    unsupported_format, timeout, oom, crashed)."""

    def __init__(self, code: str, message: str, *, stderr_tail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.stderr_tail = stderr_tail


@dataclass(frozen=True)
class ChildConfig:
    engine: str
    artifacts_path: str | None
    threads: int = 1
    block_timeout_seconds: float = 900.0
    probe_timeout_seconds: float = 180.0
    max_rss_mb: int = 2048


def child_python_path() -> str:
    """Il `sys.path` del padre (cartelle esistenti), perché il figlio con
    HOME diversa troverebbe altrimenti un site-packages diverso."""
    seen: list[str] = []
    for entry in sys.path:
        if entry and entry not in seen and Path(entry).is_dir():
            seen.append(entry)
    return os.pathsep.join(seen)


def child_env(workdir: Path, *, threads: int, artifacts_path: str | None) -> dict[str, str]:
    """Ambiente del figlio: solo ciò che serve, niente variabili dell'app."""
    threads_s = str(max(1, threads))
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(workdir),
        "TMPDIR": str(workdir),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONPATH": child_python_path(),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "OMP_NUM_THREADS": threads_s,
        "MKL_NUM_THREADS": threads_s,
        "OPENBLAS_NUM_THREADS": threads_s,
        "TORCH_NUM_THREADS": threads_s,
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "MPLBACKEND": "Agg",
    }
    if artifacts_path:
        env["DOCLING_ARTIFACTS_PATH"] = artifacts_path
    return env


def mem_available_mb() -> int | None:
    """MemAvailable da /proc/meminfo (Linux); None se non disponibile."""
    try:
        with open("/proc/meminfo", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError):
        return None
    return None


def process_rss_mb(pid: int) -> int | None:
    """VmRSS del processo (Linux); None se non disponibile."""
    try:
        with open(f"/proc/{pid}/status", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError):
        return None
    return None


@dataclass
class BlockResult:
    figures: list[dict[str, Any]] = field(default_factory=list)
    pages_done: list[int] = field(default_factory=list)


class ChildSession:
    """Un figlio vivo, che elabora blocchi di pagine finché non viene chiuso."""

    def __init__(self, config: ChildConfig, workdir: Path) -> None:
        self.config = config
        self.workdir = workdir
        self.proc: asyncio.subprocess.Process | None = None
        self.pages_total = 0
        self.pages_processed = 0
        self._stderr = bytearray()
        self._stderr_task: asyncio.Task[None] | None = None

    async def _spawn(self, *extra_args: str) -> asyncio.subprocess.Process:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            CHILD_MODULE,
            *extra_args,
            cwd=str(self.workdir),
            env=child_env(
                self.workdir,
                threads=self.config.threads,
                artifacts_path=self.config.artifacts_path,
            ),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            limit=4 * 1024 * 1024,
        )
        self.proc = proc
        self._stderr_task = asyncio.create_task(self._drain_stderr(proc))
        return proc

    async def _drain_stderr(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stderr is not None
        while chunk := await proc.stderr.read(4096):
            self._stderr.extend(chunk)
            if len(self._stderr) > 4 * STDERR_TAIL_BYTES:
                del self._stderr[:-STDERR_TAIL_BYTES]

    def stderr_tail(self) -> str:
        return bytes(self._stderr[-STDERR_TAIL_BYTES:]).decode("utf-8", "replace")

    async def _send(self, payload: dict[str, Any]) -> None:
        assert self.proc is not None and self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        await self.proc.stdin.drain()

    async def _read_event(self, deadline: float) -> dict[str, Any]:
        """Prossimo evento, con watchdog di tempo e memoria."""
        assert self.proc is not None and self.proc.stdout is not None
        read = asyncio.ensure_future(self.proc.stdout.readline())
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ExtractionChildError("timeout", "tempo massimo del blocco superato")
                done, _ = await asyncio.wait({read}, timeout=min(_RSS_POLL_SECONDS, remaining))
                if done:
                    break
                rss = process_rss_mb(self.proc.pid)
                if rss is not None and rss > self.config.max_rss_mb:
                    raise ExtractionChildError("oom", f"RSS del figlio {rss} MB oltre la soglia")
        finally:
            if not read.done():
                read.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await read
        line = read.result()
        if not line:
            await self._wait_exit()
            raise self._exit_failure()
        try:
            event = json.loads(line)
        except ValueError as exc:
            raise ExtractionChildError(
                "crashed", f"riga di protocollo non valida: {line[:200]!r}"
            ) from exc
        if event.get("event") == "error":
            await self._wait_exit()
            raise ExtractionChildError(
                str(event.get("code") or "crashed"),
                str(event.get("message") or ""),
                stderr_tail=self.stderr_tail(),
            )
        return event

    async def _wait_exit(self) -> None:
        if self.proc is None:
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.proc.wait(), timeout=10)
        if self._stderr_task is not None:
            with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(self._stderr_task, timeout=2)

    def _exit_failure(self) -> ExtractionChildError:
        code = self.proc.returncode if self.proc else None
        tail = self.stderr_tail()
        if code is not None and code < 0:
            sig = -code
            if sig == signal.SIGILL:
                # Istruzioni CPU non supportate (es. torch senza AVX sulla VM).
                return ExtractionChildError(
                    "engine_unavailable", "SIGILL nel figlio", stderr_tail=tail
                )
            if sig == signal.SIGKILL:
                return ExtractionChildError("oom", "figlio ucciso (SIGKILL)", stderr_tail=tail)
            return ExtractionChildError(
                "crashed", f"figlio terminato dal segnale {sig}", stderr_tail=tail
            )
        return ExtractionChildError("crashed", f"figlio uscito con codice {code}", stderr_tail=tail)

    async def start(self, *, source_name: str, mime: str) -> int:
        await self._spawn()
        await self._send(
            {
                "source": source_name,
                "mime": mime,
                "engine": self.config.engine,
                "artifacts_path": self.config.artifacts_path,
                "threads": self.config.threads,
            }
        )
        deadline = time.monotonic() + self.config.probe_timeout_seconds
        event = await self._read_event(deadline)
        if event.get("event") != "ready":
            raise ExtractionChildError("crashed", f"evento inatteso all'avvio: {event}")
        self.pages_total = int(event.get("pages") or 0)
        return self.pages_total

    async def run_block(self, first: int, last: int) -> BlockResult:
        await self._send({"pages": [first, last]})
        deadline = time.monotonic() + self.config.block_timeout_seconds
        result = BlockResult()
        while True:
            event = await self._read_event(deadline)
            kind = event.get("event")
            if kind == "figure":
                result.figures.append(event)
            elif kind == "page_done":
                result.pages_done.append(int(event["page"]))
            elif kind == "block_done":
                self.pages_processed += last - first + 1
                return result

    async def probe(self) -> dict[str, Any]:
        await self._spawn("--probe")
        await self._send(
            {
                "engine": self.config.engine,
                "artifacts_path": self.config.artifacts_path,
                "threads": self.config.threads,
            }
        )
        deadline = time.monotonic() + self.config.probe_timeout_seconds
        try:
            event = await self._read_event(deadline)
        finally:
            await self.close()
        if event.get("event") != "probe_ok":
            raise ExtractionChildError("engine_unavailable", f"sonda fallita: {event}")
        return event

    async def close(self) -> None:
        """Chiude stdin (uscita ordinata); se il figlio non esce, kill del gruppo."""
        proc = self.proc
        if proc is None:
            return
        if proc.returncode is None and proc.stdin is not None:
            with contextlib.suppress(Exception):
                proc.stdin.close()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=10)
        self.kill()
        await self._wait_exit()
        self.proc = None

    def kill(self) -> None:
        proc = self.proc
        if proc is None:
            return
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
