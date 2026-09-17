"""Guardia unica degli oracoli che passano da Chromium (D14, Fase D).

Uno skip è legittimo SOLO per l'ambiente: Playwright o Chromium assenti,
CDN dei moduli Mermaid irraggiungibile, pagina del pre-render che non
diventa mai pronta. Tutto il resto — un errore del JS dell'oracolo, un
`getBBox` che solleva, un pre-render che torna vuoto con la pagina pronta
— deve FAR FALLIRE il test: un `try` largo intorno alla misura lascia la
suite verde mentre la garanzia che l'oracolo copre non è più provata.

`require_chromium` va chiamata PRIMA della resa: dopo di lei ogni
eccezione del browser arriva al test, che fallisce.
"""

from __future__ import annotations

import socket
from collections.abc import Callable

import pytest
import structlog.testing

_SETUP_FAILED = "mermaid_renderer_setup_failed"


def require_cdn(host: str = "cdn.jsdelivr.net", port: int = 443) -> None:
    """Salta se la CDN dei moduli Mermaid non è raggiungibile."""
    try:
        socket.create_connection((host, port), timeout=3).close()
    except OSError:  # pragma: no cover - verifica locale, non gate CI
        pytest.skip(f"{host} non raggiungibile")


def require_chromium() -> None:
    """Salta se Playwright manca o se Chromium non si avvia."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - launch di Chromium
            pytest.skip(f"Chromium non disponibile: {exc!r}"[:200])
        browser.close()


def render_batch_or_fail[T](render: Callable[[], list[T]]) -> list[T]:
    """Esegue il pre-render e distingue le due cause di un lotto vuoto: la
    pagina che non diventa pronta (`mermaid_renderer_setup_failed`: CDN o
    ambiente, skip) da qualunque altra (guasto della resa, fallimento). Le
    eccezioni non sono catturate: dopo `require_chromium` non c'è più un
    motivo legittimo per saltare."""
    with structlog.testing.capture_logs() as logs:
        out = render()
    if out and all(item is None for item in out):
        if any(entry.get("event") == _SETUP_FAILED for entry in logs):  # pragma: no cover
            pytest.skip("pagina di rendering non pronta: moduli Mermaid non caricati dalla CDN")
        pytest.fail("pre-render vuoto con la pagina pronta: guasto della resa Mermaid")
    return out
