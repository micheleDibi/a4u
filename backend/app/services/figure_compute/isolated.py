"""Esecuzione isolata di un calcolo CPU-bound in un processo figlio (A13).

`vl_convert` (Deno in-process) e `sympy.solve` non sono interrompibili da
un thread: un `asyncio.wait_for` lascerebbe un thread orfano che gira per
sempre e, sulla VM a 2 core, satura la macchina. L'unica cosa uccidibile
in Python 3.12 è un processo figlio: `run_isolated` avvia un `Process`
con contesto `spawn` (nessuna copia dello stato del worker uvicorn:
loop, connessioni, thread), attende il risultato entro una scadenza
monotona e, allo scadere, `kill()`.

Il figlio importa il bersaglio dal disco (`"pacchetto.modulo:funzione"`)
e ricostruisce `sys.path` dal padre (`multiprocessing.spawn`): un
monkeypatch nel processo padre NON lo raggiunge (per i test serve un
bersaglio reale importabile, es. `tests/helpers/slow_target.py`).

Il figlio è `daemon=True`: non può a sua volta creare processi (nessun
bersaglio ne ha bisogno) e viene terminato con il padre.

Costo misurato attraverso il figlio: 0,29-0,47 s con l'import di
`vl_convert` (che paga altri 361 ms alla prima chiamata di ogni
processo); attenuato da cache e debounce nei chiamanti. Un processo caldo
dedicato è un'ottimizzazione futura documentata.
"""

from __future__ import annotations

import contextlib
import importlib
import multiprocessing
import time
from multiprocessing.connection import Connection
from typing import Any

# Attesa massima di una singola `join` di raccolta del figlio: dopo `kill()`
# il processo muore subito, la join serve solo a non lasciare zombie.
_JOIN_GRACE_S = 1.0


class FigureTimeoutError(TimeoutError):
    """Il processo figlio non ha risposto entro il timeout ed è stato ucciso."""


class FigureComputeError(RuntimeError):
    """Il processo figlio ha sollevato un'eccezione o è terminato senza risultato."""


def _child_main(fn_path: str, payload: Any, conn: Connection) -> None:
    """Entry-point del figlio: importa il bersaglio e invia `(esito, valore)`."""
    try:
        module_name, _, fn_name = fn_path.partition(":")
        if not module_name or not fn_name:
            raise ValueError(f"bersaglio non valido: {fn_path!r} (atteso 'modulo:funzione')")
        module = importlib.import_module(module_name)
        fn = getattr(module, fn_name)
        result = fn(payload)
        conn.send(("ok", result))
    except BaseException as exc:  # ogni errore del figlio torna al padre come testo
        # Pipe già chiusa dal padre (timeout): l'errore non ha più destinatario.
        with contextlib.suppress(Exception):
            conn.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        conn.close()


def _reap(proc: multiprocessing.process.BaseProcess, *, remaining: float) -> None:
    """Raccoglie il figlio senza superare la scadenza: join breve, poi
    `kill()` e una join di grazia."""
    proc.join(max(0.0, min(_JOIN_GRACE_S, remaining)))
    if proc.is_alive():
        proc.kill()
        proc.join(_JOIN_GRACE_S)


def run_isolated(fn_path: str, payload: Any, *, timeout: float) -> Any:
    """Esegue `fn_path(payload)` in un processo figlio `spawn`.

    `payload` e il risultato devono essere picklabili (dict/list/str di
    JSON). Solleva `FigureTimeoutError` allo scadere di `timeout` secondi
    (deadline monotona che copre avvio, calcolo e raccolta: il figlio
    viene ucciso) e `FigureComputeError` se il figlio solleva o muore
    senza rispondere. Bloccante: il chiamante async la avvolge in
    `asyncio.to_thread`.
    """
    deadline = time.monotonic() + max(0.0, timeout)

    def remaining() -> float:
        return max(0.0, deadline - time.monotonic())

    ctx = multiprocessing.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child_main, args=(fn_path, payload, child_conn), daemon=True)
    proc.start()
    child_conn.close()
    try:
        # `poll` prima di `recv`: `join(timeout)` seguito da `recv` andrebbe
        # in stallo con risultati più grandi del buffer della pipe (il
        # figlio resta bloccato in `send` finché il padre non legge). Dopo
        # un `poll` positivo `recv` legge il messaggio mentre il figlio lo
        # scrive: termina appena il figlio ha finito di inviare o, se il
        # figlio muore, con EOF.
        if not parent_conn.poll(remaining()):
            proc.kill()
            _reap(proc, remaining=_JOIN_GRACE_S)
            raise FigureTimeoutError(f"{fn_path}: nessuna risposta entro {timeout:g} s")
        try:
            status, value = parent_conn.recv()
        except (EOFError, OSError) as exc:
            _reap(proc, remaining=remaining())
            raise FigureComputeError(
                f"{fn_path}: processo figlio terminato senza risultato (exitcode={proc.exitcode})"
            ) from exc
    finally:
        parent_conn.close()
        _reap(proc, remaining=remaining())
    if status != "ok":
        raise FigureComputeError(str(value))
    return value


__all__ = ["FigureComputeError", "FigureTimeoutError", "run_isolated"]
