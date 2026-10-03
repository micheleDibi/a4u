"""Regressione del task W5 (Livello 1 prestazioni): `init: true` sul backend.

In produzione uvicorn come PID 1 non raccoglie i Chromium orfani di Playwright
(2529 zombie in 44 h, 03/10/2026). Il servizio `backend` di
`docker-compose.prod.yml` deve avere un init (tini) come PID 1; il test
impedisce che la riga sparisca in una delle frequenti modifiche al blocco
`environment:`.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _service_block(text: str, name: str) -> str:
    """Testo del servizio `name` (indentazione 2) fino al servizio successivo."""
    match = re.search(rf"^  {name}:\n(.*?)(?=^  \S|^\S|\Z)", text, re.MULTILINE | re.DOTALL)
    assert match, f"servizio {name} assente"
    return match.group(1)


def test_backend_service_runs_with_init_as_pid1() -> None:
    text = (_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    block = _service_block(text, "backend")
    assert re.search(r"^    init: true\s*$", block, re.MULTILINE), block[:200]
