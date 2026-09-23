"""Figlio finto per i test del runner delle figure (`-m tests.fake_figure_child`).

Il comportamento arriva da `A4U_FAKE_CHILD_MODE` (aggiunta all'ambiente
dai test): `env` (emette i nomi e i valori delle variabili ricevute),
`sigill`, `exit` (esce senza eventi), `hang` (avvia un nipote e resta
fermo, scrivendo il pid del nipote in `grandchild.pid`), `crash_block:N`
(muore quando riceve il blocco che contiene la pagina N), `ok` (pagine
vuote).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time


def _emit(event: dict) -> None:
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def main() -> int:
    mode = os.environ.get("A4U_FAKE_CHILD_MODE", "ok")
    job = json.loads(sys.stdin.readline() or "{}")
    if mode == "env":
        _emit({"event": "ready", "pages": 1, "env": dict(os.environ), "job": job})
        return 0
    if mode == "sigill":
        os.kill(os.getpid(), signal.SIGILL)
    if mode == "exit":
        return 1
    if mode == "hang":
        grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
        with open("grandchild.pid", "w", encoding="ascii") as handle:
            handle.write(str(grandchild.pid))
        _emit({"event": "ready", "pages": 5})
        for _ in sys.stdin:
            time.sleep(600)
        return 0
    _emit({"event": "ready", "pages": int(job.get("fake_pages") or 5)})
    crash_page = int(mode.split(":")[1]) if mode.startswith("crash_block:") else None
    for raw in sys.stdin:
        first, last = json.loads(raw)["pages"]
        if crash_page is not None and first <= crash_page <= last:
            os._exit(9)
        for page in range(first, last + 1):
            _emit({"event": "page_done", "page": page})
        _emit({"event": "block_done", "pages": [first, last]})
    return 0


if __name__ == "__main__":
    sys.exit(main())
