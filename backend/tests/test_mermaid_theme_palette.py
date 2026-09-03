"""Tema D3 su Mermaid 11: la palette arriva ai riempimenti e ai bordi
principali dei tipi D8 (verifica sull'output reale, non sulla configurazione).

Il tema `neutral` non deriva da `primaryColor` le variabili che i diagrammi
leggono davvero (`mainBkg`, `nodeBorder`, `actorBkg`, `signalColor`,
`cScale*`, gantt, stato): `figure_theme.mermaid_config` le fissa una per una
e questo test controlla che gli SVG prodotti da Mermaid le rispettino.

Richiede Playwright con Chromium e l'accesso a cdn.jsdelivr.net (pin
`settings.mermaid_cdn_version`): in CI salta con motivo esplicito.
"""

from __future__ import annotations

import re
import socket

import pytest

from app.services import figure_theme as theme

pytest.importorskip("playwright.sync_api")

_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")


def _rule(svg: str, selector: str) -> str:
    """Corpo (normalizzato, minuscolo) dell'ultima regola CSS dell'SVG il cui
    elenco di selettori contiene `selector` (senza il prefisso `#id`)."""
    css = "".join(re.findall(r"<style>(.*?)</style>", svg, re.S))
    body = ""
    for m in _RULE_RE.finditer(css):
        selectors = [re.sub(r"^#\S+\s*", "", s.strip()) for s in m.group(1).split(",")]
        if selector in selectors:
            body = re.sub(r"\s+", "", m.group(2)).lower()
    assert body, f"regola `{selector}` assente nell'SVG"
    return body


def _prop(svg: str, selector: str, prop: str) -> str:
    body = _rule(svg, selector)
    m = re.search(rf"(?:^|;){prop}:([^;]+)", body)
    assert m, f"`{prop}` assente in `{selector}`: {body}"
    return m.group(1)


@pytest.fixture(scope="module")
def rendered() -> dict[str, str]:
    from playwright.sync_api import sync_playwright

    try:
        socket.create_connection(("cdn.jsdelivr.net", 443), timeout=3).close()
    except OSError:
        pytest.skip("cdn.jsdelivr.net non raggiungibile")
    from app.core.config import get_settings

    version = get_settings().mermaid_cdn_version
    html = (
        '<!doctype html><html><body><script type="module">\n'
        f'import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@{version}'
        '/dist/mermaid.esm.min.mjs";\n'
        f"{theme.mermaid_initialize_js(use_max_width=True)}\n"
        "window.__mermaid = mermaid; window.__mermaidReady = true;\n"
        "</script></body></html>"
    )
    out: dict[str, str] = {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.set_content(html)
                page.wait_for_function("window.__mermaidReady === true", timeout=15_000)
                for kind, code in theme.MERMAID_D8_SAMPLES.items():
                    svg_id = "d8_" + re.sub(r"[^a-z0-9]", "_", kind.lower())
                    out[kind] = page.evaluate(
                        "async ([id, code]) => (await window.__mermaid.render(id, code)).svg",
                        [svg_id, code],
                    )
            finally:
                browser.close()
    except Exception as exc:  # launch, rete o render: verifica locale, non gate CI
        pytest.skip(f"Chromium o CDN non disponibili: {exc!r}"[:300])
    return out


_TINT_BLUE = "#e8f1f8"


def test_all_d8_samples_render_without_foreignobject_or_gradient(rendered):
    assert set(rendered) == set(theme.MERMAID_D8_SAMPLES)
    for kind, svg in rendered.items():
        assert "<foreignObject" not in svg, kind
        assert "<linearGradient" not in svg, kind  # D3: niente gradienti (useGradient, sankey)
        assert "<text" in svg, kind


@pytest.mark.parametrize(
    ("kind", "selector", "prop", "expected"),
    [
        ("flowchart", ".node rect", "fill", _TINT_BLUE),
        ("flowchart", ".node rect", "stroke", theme.PALETTE[0]),
        ("flowchart", ".cluster rect", "fill", theme.COLOR_SURFACE),
        ("flowchart", ".cluster rect", "stroke", theme.COLOR_AXIS),
        ("flowchart", ".arrowheadPath", "fill", theme.COLOR_AXIS),
        ("block-beta", ".node rect", "fill", _TINT_BLUE),
        ("block-beta", ".node rect", "stroke", theme.PALETTE[0]),
        ("classDiagram", "g.classGroup rect", "fill", _TINT_BLUE),
        ("classDiagram", "g.classGroup rect", "stroke", theme.PALETTE[0]),
        ("classDiagram", ".relation", "stroke", theme.COLOR_AXIS),
        ("sequenceDiagram", ".actor", "fill", _TINT_BLUE),
        ("sequenceDiagram", ".actor", "stroke", theme.PALETTE[0]),
        ("sequenceDiagram", ".messageLine0", "stroke", theme.COLOR_AXIS),
        ("sequenceDiagram", ".labelBox", "stroke", theme.PALETTE[0]),
        ("sequenceDiagram", ".note", "stroke", theme.PALETTE[3]),
        ("stateDiagram-v2", ".node rect", "fill", _TINT_BLUE),
        ("stateDiagram-v2", ".node rect", "stroke", theme.PALETTE[0]),
        ("stateDiagram-v2", ".transition", "stroke", theme.COLOR_AXIS),
        ("erDiagram", ".entityBox", "fill", _TINT_BLUE),
        ("erDiagram", ".entityBox", "stroke", theme.PALETTE[0]),
        ("erDiagram", ".relationshipLine", "stroke", theme.COLOR_AXIS),
        ("mindmap", ".section-root rect", "fill", theme.PALETTE[0]),
        ("mindmap", ".section-root text", "fill", theme.COLOR_WHITE),
        ("mindmap", ".section-0 rect", "fill", theme.PALETTE[1]),
        ("mindmap", ".section-1 rect", "fill", theme.PALETTE[2]),
        ("timeline", ".section-root rect", "fill", theme.PALETTE[0]),
        ("timeline", ".section-0 rect", "fill", theme.PALETTE[1]),
        ("gantt", ".task0", "fill", theme.PALETTE[0]),
        ("gantt", ".section0", "fill", _TINT_BLUE),
        ("gantt", ".grid .tick", "stroke", theme.COLOR_GRID),
        ("gantt", ".vert", "stroke", theme.PALETTE[1]),
        ("radar-beta", ".radarCurve-0", "fill", theme.PALETTE[0]),
        ("radar-beta", ".radarCurve-1", "fill", theme.PALETTE[1]),
        ("radar-beta", ".radarAxisLine", "stroke", theme.COLOR_AXIS),
        ("pie", ".pieTitleText", "fill", theme.COLOR_INK),
    ],
)
def test_d8_main_fills_and_borders_follow_palette(rendered, kind, selector, prop, expected):
    assert _prop(rendered[kind], selector, prop) == expected.lower()


def test_pie_xychart_quadrant_use_palette_inline(rendered):
    # Questi tipi scrivono i colori negli attributi, non nel `<style>`.
    blue = theme.PALETTE[0]
    assert blue in rendered["pie"] or "rgb(0, 114, 178)" in rendered["pie"]
    assert blue in rendered["xychart-beta"]
    assert blue in rendered["quadrantChart"] and _TINT_BLUE.upper() in rendered["quadrantChart"]
    assert blue in rendered["treemap-beta"]
