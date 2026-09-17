"""Fase D — gli oracoli che passano da Chromium non si autoassolvono.

Un `try → pytest.skip` può coprire SOLO l'avvio del browser e la
raggiungibilità della CDN: sono le due condizioni che un altro computer può
non soddisfare. Se copre anche la resa o la misura, un errore del JS
dell'oracolo diventa uno skip silenzioso e la suite resta verde mentre la
garanzia non è più provata (difetto trovato in Fase D su `mermaid_overflow`
e sulle fixture del pre-render).

Due prove:
- comportamento: `chromium_guard.render_batch_or_fail` salta solo quando la
  pagina non è mai diventata pronta e fallisce su un lotto vuoto con la
  pagina pronta; le eccezioni passano al chiamante;
- struttura: nei file che usano Chromium nessun `try` che salta contiene la
  resa o la misura.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import structlog

from tests.chromium_guard import render_batch_or_fail

_TESTS = Path(__file__).parent
_FILES = (
    "test_frontend_figure_templates.py",
    "test_mermaid_no_foreignobject.py",
    "test_mermaid_prerender.py",
)
# Chiamate che NON possono stare dentro un `try` che salta: sono la resa e
# la misura, cioè ciò che l'oracolo deve provare.
_FORBIDDEN = {
    "_prerender_mermaid_to_svg_batch_sync",
    "_prerender_mermaid_batch_sync",
    "evaluate",
    "set_content",
    "new_page",
    "wait_for_function",
}

log = structlog.get_logger(__name__)


def test_an_empty_batch_with_the_page_ready_is_a_failure() -> None:
    with pytest.raises(pytest.fail.Exception, match="guasto della resa Mermaid"):
        render_batch_or_fail(lambda: [None, None])


def test_an_empty_batch_without_the_page_is_a_skip() -> None:
    def render() -> list[None]:
        log.warning("mermaid_renderer_setup_failed", error="timeout")
        return [None, None]

    with pytest.raises(pytest.skip.Exception):
        render_batch_or_fail(render)


def test_a_partial_batch_passes_through() -> None:
    assert render_batch_or_fail(lambda: ["<svg/>", None]) == ["<svg/>", None]


def test_an_error_of_the_render_reaches_the_caller() -> None:
    def render() -> list[str]:
        raise RuntimeError("guasto della resa")

    with pytest.raises(RuntimeError, match="guasto della resa"):
        render_batch_or_fail(render)


def _skipping_tries(tree: ast.AST) -> list[ast.Try]:
    """I `try` il cui gestore chiama `pytest.skip`."""
    out: list[ast.Try] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            calls = (
                n.func
                for n in ast.walk(handler)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            )
            if any(getattr(f, "attr", "") == "skip" for f in calls):
                out.append(node)
                break
    return out


@pytest.mark.parametrize("name", _FILES)
def test_no_skipping_try_wraps_the_render_or_the_measure(name: str) -> None:
    tree = ast.parse((_TESTS / name).read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in _skipping_tries(tree):
        for call in ast.walk(ast.Module(body=node.body, type_ignores=[])):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr in _FORBIDDEN
            ):
                offenders.append(f"riga {node.lineno}: {call.func.attr}")
    assert not offenders, f"{name}: {offenders}"
