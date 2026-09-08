"""Geometria delle figure nel frontend (WP5, giro 2 della verifica): le due
regressioni trovate erano di sola cascata CSS, invisibili ai test di
sorgente.

1. `.lesson-prose .figure img` (index.css) ha specificità (0,2,1) e vince su
   `mx-auto` (0,1,0): con `margin: 0` le immagini caricate e le figure
   `function` più strette della colonna finivano a sinistra sotto una
   didascalia centrata. La regola deve avere `margin: 0 auto`.
2. `MermaidDiagram` rende l'SVG a larghezza piena (`width: 100%`); un
   `max-height` incondizionato lo fa scalare in `meet` e i diagrammi
   verticali (sequence, flowchart TD, class) diventano illeggibili. Il tetto
   (`fullWidthSvgMaxHeightPx` in `lib/figureFormats.ts`) vale solo per i
   diagrammi orizzontali e mai sotto l'altezza naturale.

La geometria è misurata in Chromium (Playwright) con la regola reale di
index.css e gli equivalenti CSS delle utility Tailwind coinvolte (le
classi non sono compilate nei test): salta con motivo esplicito senza
Playwright o senza Chromium. La funzione di policy è eseguita davvero con
Node (`--experimental-strip-types`), come la copia di `figureNumbering`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

_FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
_INDEX_CSS = _FRONTEND / "index.css"
_FIGURE_FORMATS = _FRONTEND / "lib" / "figureFormats.ts"
_MERMAID_DIAGRAM = _FRONTEND / "components" / "shared" / "MermaidDiagram.tsx"

_FIGURE_IMG_RULE_RE = re.compile(r"\.lesson-prose \.figure img\s*\{([^}]*)\}")


def _read(path: Path) -> str:
    if not path.is_file():
        pytest.skip(f"sorgente frontend assente: {path}")
    return path.read_text(encoding="utf-8")


def _figure_img_rule() -> str:
    m = _FIGURE_IMG_RULE_RE.search(_read(_INDEX_CSS))
    assert m, "regola `.lesson-prose .figure img` assente da index.css"
    return m.group(1)


def test_figure_img_rule_centers_horizontally() -> None:
    """`margin: 0 auto`, non `margin: 0`: la regola annulla `mx-auto`."""
    rule = _figure_img_rule()
    assert re.search(r"margin\s*:\s*0\s+auto\s*;", rule), rule
    assert re.search(r"border-radius\s*:\s*0\s*;", rule), rule


def test_mermaid_height_cap_is_conditional_in_component() -> None:
    src = _read(_MERMAID_DIAGRAM)
    # Tetto incondizionato (87a30e0): regressione sui diagrammi verticali.
    assert "max-h-[28rem]" not in src
    assert "fullWidthSvgMaxHeightPx(svgIntrinsicSize(" in src
    assert "--mermaid-max-h" in src
    assert "[&_svg]:!w-full" in src  # geometria a larghezza piena conservata


# --- policy eseguita con Node ------------------------------------------------

_SIZE_CASES: list[dict[str, Any]] = [
    {
        "name": "sequence (verticale): nessun tetto",
        "svg": '<svg id="m" width="100%" xmlns="http://www.w3.org/2000/svg" style=""'
        ' viewBox="-50 -10 650 907" role="graphics-document document"><g/></svg>',
        "size": {"width": 650, "height": 907},
        "cap": None,
    },
    {
        "name": "torta 524×450: tetto = altezza naturale (450 > 448)",
        "svg": '<svg viewBox="0 0 524.28125 450"><g/></svg>',
        "size": {"width": 524.28125, "height": 450},
        "cap": 450,
    },
    {
        "name": "flowchart LR 484×158: tetto 28rem",
        "svg": '<svg viewBox="0,0,483.6875,158"/>',
        "size": {"width": 483.6875, "height": 158},
        "cap": 448,
    },
    {
        "name": "quadrato: orizzontale (larghezza ≥ altezza)",
        "svg": '<svg viewBox="0 0 300 300"/>',
        "size": {"width": 300, "height": 300},
        "cap": 448,
    },
    {
        "name": "senza viewBox, width/height numerici",
        "svg": '<svg width="300px" height="120"/>',
        "size": {"width": 300, "height": 120},
        "cap": 448,
    },
    {
        "name": "solo width percentuale: dimensioni ignote, nessun tetto",
        "svg": '<svg width="100%"/>',
        "size": None,
        "cap": None,
    },
    {"name": "non SVG", "svg": "<div/>", "size": None, "cap": None},
    {"name": "viewBox degenere", "svg": '<svg viewBox="0 0 0 10"/>', "size": None, "cap": None},
]

_NODE_RUNNER = """
import * as ff from {module!r};
const cases = {cases};
const out = cases.map((c) => {{
  const size = ff.svgIntrinsicSize(c.svg);
  return {{ size, cap: ff.fullWidthSvgMaxHeightPx(size) }};
}});
process.stdout.write(JSON.stringify(out));
"""


def test_svg_size_policy_via_node() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node non disponibile: policy frontend non eseguibile")
    if not _FIGURE_FORMATS.is_file():
        pytest.skip(f"modulo frontend assente: {_FIGURE_FORMATS}")
    script = _NODE_RUNNER.format(
        module=str(_FIGURE_FORMATS),
        cases=json.dumps([{"svg": c["svg"]} for c in _SIZE_CASES]),
    )
    proc = subprocess.run(
        [node, "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0 and "strip-types" in proc.stderr:
        pytest.skip(f"node senza --experimental-strip-types: {proc.stderr.strip()[:200]}")
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)
    for case, fe in zip(_SIZE_CASES, got, strict=True):
        assert fe["size"] == case["size"], case["name"]
        assert fe["cap"] == case["cap"], case["name"]


# --- geometria in Chromium ---------------------------------------------------

# Equivalenti delle utility Tailwind usate da FigureFrame, VisualAssetBody,
# FunctionFigure e MermaidDiagram (le classi arbitrarie `[&_svg]:…` sono
# compilate da Tailwind in `.<classe> svg { … }`).
_UTILITY_CSS = """
.mx-auto { margin-left: auto; margin-right: auto; }
.block { display: block; }
.w-full { width: 100%; }
.h-auto { height: auto; }
.max-w-full { max-width: 100%; }
.svg-full svg { width: 100% !important; max-width: none !important; height: auto; }
.svg-cap svg { max-height: var(--mermaid-max-h); }
"""

_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
body, figure {{ margin: 0; }}
.lesson-prose img {{ max-width: 100%; height: auto; border-radius: 0.5rem; margin: 1rem 0; }}
.lesson-prose .figure img {{{rule}}}
{utilities}
</style></head><body>
<div class="lesson-prose" style="width: 900px">
  <figure class="figure" id="f-img"><div class="figure-body w-full">
    <img id="img" class="mx-auto block h-auto max-w-full" src="{image}">
  </div></figure>
  <figure class="figure"><div class="figure-body w-full">
    <div class="svg-full" id="portrait">
      <svg width="100%" viewBox="0 0 300 1000"><rect width="300" height="1000"/></svg>
    </div>
  </div></figure>
  <figure class="figure"><div class="figure-body w-full">
    <div class="svg-full svg-cap" id="portrait-capped" style="--mermaid-max-h: 448px">
      <svg width="100%" viewBox="0 0 300 1000"><rect width="300" height="1000"/></svg>
    </div>
  </div></figure>
  <figure class="figure"><div class="figure-body w-full">
    <div class="svg-full svg-cap" id="landscape" style="--mermaid-max-h: 450px">
      <svg width="100%" viewBox="0 0 524 450"><rect width="524" height="450"/></svg>
    </div>
  </div></figure>
</div></body></html>"""

_IMAGE_400x300 = (
    "data:image/svg+xml;utf8,"
    "<svg xmlns='http://www.w3.org/2000/svg' width='400' height='300'></svg>"
)

_MEASURE_JS = """
async () => {
  const img = document.getElementById("img");
  if (!img.complete) await img.decode();
  const p = img.parentElement.getBoundingClientRect(), r = img.getBoundingClientRect();
  const out = { img: { width: r.width, left: r.left - p.left, right: p.right - r.right } };
  for (const id of ["portrait", "portrait-capped", "landscape"]) {
    const svg = document.querySelector(`#${id} svg`);
    const b = svg.getBoundingClientRect();
    out[id] = { boxW: b.width, boxH: b.height, scale: svg.getScreenCTM().a };
  }
  return out;
}
"""


@pytest.fixture(scope="module")
def measured() -> Iterator[dict[str, Any]]:
    sync_api = pytest.importorskip("playwright.sync_api")
    rule = _figure_img_rule()
    html = _PAGE.format(rule=rule, utilities=_UTILITY_CSS, image=_IMAGE_400x300)
    try:
        with sync_api.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1200, "height": 900})
            page.set_content(html)
            result = page.evaluate(_MEASURE_JS)
            browser.close()
    except Exception as exc:  # launch di Chromium: verifica locale, non gate CI
        pytest.skip(f"Chromium non disponibile: {exc!r}"[:300])
    yield result


def test_uploaded_image_is_centered_in_lesson_prose(measured: dict[str, Any]) -> None:
    img = measured["img"]
    assert img["width"] == pytest.approx(400, abs=1)
    assert img["left"] == pytest.approx(250, abs=2)
    assert abs(img["left"] - img["right"]) < 2, img


def test_portrait_svg_keeps_full_width_geometry(measured: dict[str, Any]) -> None:
    """Senza tetto: scala = larghezza colonna / larghezza naturale (900/300)."""
    box = measured["portrait"]
    assert box["scale"] == pytest.approx(3.0, abs=0.01)
    assert box["boxH"] == pytest.approx(3000, abs=2)


def test_unconditional_cap_would_shrink_portrait_svg(measured: dict[str, Any]) -> None:
    """Controllo: il tetto a 28rem con `width: 100%` scala in `meet` un
    diagramma verticale a meno della metà (è la regressione di 87a30e0);
    per questo la policy non assegna alcun tetto ai verticali."""
    box = measured["portrait-capped"]
    assert box["boxH"] == pytest.approx(448, abs=1)
    assert box["scale"] < 0.5


def test_landscape_svg_is_capped_at_natural_height(measured: dict[str, Any]) -> None:
    """Torta 524×450 con tetto 450: mostrata a scala 1, non dilatata a 900."""
    box = measured["landscape"]
    assert box["boxH"] == pytest.approx(450, abs=1)
    assert box["scale"] == pytest.approx(1.0, abs=0.01)


# ---------------------------------------------------------------------------
# Tipografia e superficie delle figure (Fase D: TIP-1, TIP-2, TIP-9)
# ---------------------------------------------------------------------------

_SHARED = _FRONTEND / "components" / "shared"
_FIGURE_FRAME = _SHARED / "FigureFrame.tsx"


def test_vegalite_and_dot_text_has_a_sans_fallback_in_the_browser() -> None:
    """L'SVG di Vega-Lite e di `dot` dichiara la sola famiglia «Noto Sans»
    (il tema deve restare a una famiglia: vl-convert misura il testo con la
    prima e una lista cambierebbe la geometria del server). Nel browser del
    docente quella famiglia spesso non c'è e Chromium ripiega su Times: la
    regola di `index.css` aggiunge il ripiego sans solo a schermo (TIP-1)."""
    css = _read(_INDEX_CSS)
    m = re.search(r"\.figure--vegalite svg text,\s*\.figure--dot svg text\s*\{([^}]*)\}", css)
    assert m, "regola di ripiego dei font delle figure assente da index.css"
    family = m.group(1)
    assert "Noto Sans" in family and "sans-serif" in family, family
    assert "serif" not in re.sub(r"sans-serif", "", family), family
    # Il webfont serve la stessa famiglia dichiarata dal tema, così a
    # schermo le metriche coincidono con il PDF.
    assert "Noto+Sans" in _read(_FRONTEND.parent / "index.html")


def test_every_figure_renderer_paints_on_a_light_surface() -> None:
    """Il tema D3 è inchiostro scuro su fondo trasparente (identico a PDF,
    slide e frame): sul fondo scuro del frontend le figure sarebbero nere su
    nero. La superficie chiara è unica e fissa, non legata al tema (TIP-2)."""
    frame = _read(_FIGURE_FRAME)
    m = re.search(r'FIGURE_SURFACE = "([^"]*)"', frame)
    assert m, "FIGURE_SURFACE assente da FigureFrame.tsx"
    assert "bg-white" in m.group(1), m.group(1)
    for name in ("VegaLiteDiagram.tsx", "DotDiagram.tsx", "FunctionFigure.tsx"):
        assert "FIGURE_SURFACE" in _read(_SHARED / name), name
    mermaid = _read(_MERMAID_DIAGRAM)
    assert "bg-background" not in mermaid and "bg-white" in mermaid


def test_figure_frame_separates_the_computed_tail_with_a_full_stop() -> None:
    """Mirror di `figure_markup`: la coda calcolata è un periodo autonomo e
    senza punto si fonderebbe con la didascalia del docente (TIP-9)."""
    frame = _read(_FIGURE_FRAME)
    m = re.search(r"CAPTION_END_RE = /(\[[^/]*\])\$/", frame)
    assert m, "CAPTION_END_RE assente da FigureFrame.tsx"
    for ch in ".!?:":
        assert ch in m.group(1), m.group(1)
    assert re.search(r"\$\{text\}\$\{stop\}", frame), "punto non applicato alla didascalia"
