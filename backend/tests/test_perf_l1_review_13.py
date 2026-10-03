"""Regressione del task G1 (Livello 1 prestazioni): cache HTTP del frontend.

`frontend/nginx.conf` tiene in cache per un anno gli asset di Vite (nome con
l'hash del contenuto) e fa rivalidare sempre la pagina. Questi test pinnano i
tre invarianti che, se rotti, fanno danni in silenzio dopo un deploy:
- `/assets/` non ricade mai su `/index.html` (un HTML in cache per un anno al
  posto di un chunk JS di un build vecchio);
- `/index.html` esce con `expires -1` (`Cache-Control: no-cache`);
- il fallback della SPA punta proprio a `/index.html`, cioè passa dal
  `location` esatto senza cache.
L'assenza di `add_header` nei `location` è già pinnata da
`test_frontend_csp_header.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def _location(name: str) -> str:
    """Corpo del blocco `location <name> { … }` di `nginx.conf`."""
    testo = (FRONTEND / "nginx.conf").read_text(encoding="utf-8")
    match = re.search(rf"^\s*location\s+{re.escape(name)}\s*\{{([^{{}}]*)\}}", testo, re.M)
    assert match, f"location {name} assente"
    return match.group(1)


def test_assets_in_cache_lunga_senza_fallback_html() -> None:
    blocco = _location("/assets/")
    assert re.search(r"^\s*expires\s+1y;", blocco, re.M), blocco
    try_files = re.search(r"^\s*try_files\s+([^;]+);", blocco, re.M)
    assert try_files, blocco
    assert try_files.group(1).split()[-1] == "=404", try_files.group(1)
    assert "index.html" not in blocco


def test_index_html_sempre_rivalidato() -> None:
    assert re.search(r"^\s*expires\s+-1;", _location("= /index.html"), re.M)


def test_fallback_spa_passa_dal_location_senza_cache() -> None:
    try_files = re.search(r"^\s*try_files\s+([^;]+);", _location("/"), re.M)
    assert try_files and try_files.group(1).split()[-1] == "/index.html"


def test_build_senza_sourcemap() -> None:
    testo = (FRONTEND / "vite.config.ts").read_text(encoding="utf-8")
    assert re.search(r"^\s*sourcemap:\s*false,", testo, re.M), "sourcemap pubblicate da nginx"
