"""Geometria delle figure nel frontend.

1. `.lesson-prose .figure img` (index.css) ha specificità (0,2,1) e vince su
   `mx-auto` (0,1,0): con `margin: 0` le immagini caricate e le figure
   `function` più strette della colonna finivano a sinistra sotto una
   didascalia centrata. La regola deve avere `margin: 0 auto`.
2. `MermaidDiagram` non rende più l'SVG a larghezza piena: misura nel DOM il
   corpo del testo più piccolo (`measureSvgFontPx`, stesso JS di
   `MEASURE_SVG_FONT_PX_JS` del pre-render backend) e applica con
   `fitFigureWidthMm` (mirror di `figure_scale.py`, senza box) la larghezza
   a cui quel testo cade nella banda del web, 8-11 pt (D10/D11), come
   `width: min(100%, Wpx)` su un wrapper interno senza padding. Il vecchio
   tetto d'altezza «solo per i diagrammi orizzontali» è superato: a
   larghezza piena il flowchart D8 usciva a 18,3 pt in una colonna di 900 px
   e un verticale 300×1000 a 31 pt (controprova conservata qui sotto).

Tre livelli: pin di sorgente; la copia frontend eseguita con Node
(`--experimental-strip-types`) sulla fixture condivisa
`figure_scale_cases.json`; Chromium (Playwright) con il modulo VERO compilato
dall'esbuild del frontend, per la geometria del wrapper e per la parità del
JS di misura con `window.__measureSvgFontPx` della pagina di pre-render
(CDN). Il `try` sta solo su `chromium.launch()`: la misura è fuori.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import socket
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.services import figure_scale as fs
from app.services import figure_theme as theme
from app.services.mermaid_prerender import PRERENDER_ALLOWED_PREFIX, build_mermaid_renderer_html
from app.services.svg_normalize import svg_base_font_px

_FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
_INDEX_CSS = _FRONTEND / "index.css"
_FIGURE_FORMATS = _FRONTEND / "lib" / "figureFormats.ts"
_MERMAID_DIAGRAM = _FRONTEND / "components" / "shared" / "MermaidDiagram.tsx"
_FIXTURES = Path(__file__).parent / "fixtures"
_SCALE_CASES = _FIXTURES / "figure_scale_cases.json"
_FLOWCHART_V11 = _FIXTURES / "mermaid11_flowchart.svg"

_FIGURE_IMG_RULE_RE = re.compile(r"\.lesson-prose \.figure img\s*\{([^}]*)\}")
# Lo strip del `max-width` inline fatto dal componente (stessa regex).
_MAX_WIDTH_RE = re.compile(r"max-width\s*:\s*[\d.]+px\s*;?", re.IGNORECASE)


def _read(path: Path) -> str:
    if not path.is_file():
        pytest.skip(f"sorgente frontend assente: {path}")
    return path.read_text(encoding="utf-8")


_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"(?<![:\"'])//[^\n]*")


def _code_only(source: str) -> str:
    """Sorgente senza commenti (`/* */`, `{/* */}` di JSX, `//`): i pin sui
    simboli vietati valgono sul codice, non su chi li cita per spiegare."""
    return _LINE_COMMENT_RE.sub("", _BLOCK_COMMENT_RE.sub("", source))


def _figure_img_rule() -> str:
    m = _FIGURE_IMG_RULE_RE.search(_read(_INDEX_CSS))
    assert m, "regola `.lesson-prose .figure img` assente da index.css"
    return m.group(1)


def _web_width_px(width_mm: float) -> float:
    """Conversione mm → px del componente (`fittedWidthPx`): per difetto al
    centesimo di px, come i mm del PDF."""
    return math.floor(width_mm / fs.MM_PER_PX * 100) / 100


def test_figure_img_rule_centers_horizontally() -> None:
    """`margin: 0 auto`, non `margin: 0`: la regola annulla `mx-auto`."""
    rule = _figure_img_rule()
    assert re.search(r"margin\s*:\s*0\s+auto\s*;", rule), rule
    assert re.search(r"border-radius\s*:\s*0\s*;", rule), rule


# --- pin di sorgente -----------------------------------------------------------

# Classi del wrapper INTERNO: nessun padding (la larghezza `min(100%, Wpx)`
# è il contenuto, senza aritmetica su `p-2`), SVG a larghezza piena del
# wrapper e proporzioni conservate.
_INNER_WRAPPER_CLASSES = "mx-auto [&_svg]:!w-full [&_svg]:!max-w-none [&_svg]:h-auto"


def test_mermaid_component_uses_the_readability_fit() -> None:
    src = _read(_MERMAID_DIAGRAM)
    for needle in (
        "svgIntrinsicBox(",
        "measureSvgFontPx(",
        "fitFigureWidthMm(",
        "MERMAID_FALLBACK_FONT_PX",
        'variant: "lesson"',
        "boxWMm: null",
        "boxHMm: null",
        "min(100%,",
        "html: cleaned",  # pin di test_frontend_mermaid_sanitize
        "[&_svg]:!w-full",
        _INNER_WRAPPER_CLASSES,
        "bg-white",
    ):
        assert needle in src, needle
    code = _code_only(src)
    for banned in (
        "fullWidthSvgMaxHeightPx",
        "svgIntrinsicSize(",
        "--mermaid-max-h",
        "max-h-[",
        "clientWidth",
        "ResizeObserver",
        "box-content",
    ):
        assert banned not in code, banned
    # La larghezza sta sul wrapper interno, non sul contenitore con `p-2`.
    m = re.search(
        r'className="' + re.escape(_INNER_WRAPPER_CLASSES) + r'"\s*style=\{\{\s*width:',
        src,
    )
    assert m, "la larghezza `min(100%, Wpx)` non sta sul wrapper interno"
    assert "p-" not in _INNER_WRAPPER_CLASSES


def test_figure_formats_exposes_the_fit_and_the_measure() -> None:
    src = _read(_FIGURE_FORMATS)
    for needle in (
        "export function fitFigureWidthMm(",
        "export function svgIntrinsicBox(",
        "export function formatMm(",
        "export function measureSvgFontPx(",
        "export const READABILITY_BANDS_PT",
        "export const MERMAID_FALLBACK_FONT_PX = 14",
        "export const MM_PER_PX = 25.4 / 96",
        "export const PT_PER_PX = 0.75",
    ):
        assert needle in src, needle
    assert "fullWidthSvgMaxHeightPx" not in src and "FULL_WIDTH_SVG_CAP_PX" not in src
    # Stesso corpo del JS backend: host fuori schermo SENZA `visibility:hidden`
    # (azzererebbe i testi), filtro solo su `display: none`, host rimosso in
    # `finally`.
    m = re.search(r"export function measureSvgFontPx\([^)]*\)[^{]*\{(.*?)\n\}", src, re.DOTALL)
    assert m, "measureSvgFontPx assente"
    body = m.group(1)
    assert "position:absolute;left:-100000px;top:0;width:1000px" in body
    assert "visibility" not in body
    assert 'cs.display === "none"' in body
    assert "finally" in body and "host?.remove()" in body
    # Stessi arrotondamenti del Python: floor al centesimo e half-up con
    # `Math.floor(x·10^n + 0.5)/10^n`, mai `toFixed`/`Math.round` sui numeri
    # confrontati con la fixture.
    # Fino alla graffa di chiusura della funzione (quella dei parametri
    # destrutturati è seguita da `: FigureFitInput`, non da un a capo).
    fit = re.search(r"export function fitFigureWidthMm\(.*?\n\}\n", src, re.DOTALL)
    assert fit, "fitFigureWidthMm assente"
    assert "Math.floor(refWMm * scale * 100) / 100" in fit.group(0)
    assert "toFixed" not in fit.group(0) and "Math.round" not in fit.group(0)
    assert "Math.floor(value * factor + 0.5) / factor" in src


# --- la copia frontend eseguita con Node ------------------------------------

_NODE_RUNNER = """
import * as ff from {module!r};
import {{ readFileSync }} from "node:fs";
const data = JSON.parse(readFileSync({fixture!r}, "utf8"));
const v11 = readFileSync({v11!r}, "utf8");
const web = [];
for (const c of data.fit) {{
  const i = c.input;
  if (i.box_w_mm !== null || i.box_h_mm !== null) continue;
  const fit = ff.fitFigureWidthMm({{
    vbW: i.vb_w, vbH: i.vb_h, baseFontPx: i.base_font_px, boxWMm: null,
    boxHMm: null, variant: i.variant, intrinsicWPx: i.intrinsic_w_px,
  }});
  web.push({{
    name: c.name, fit,
    widthPx: fit ? Math.floor((fit.widthMm / ff.MM_PER_PX) * 100) / 100 : null,
    style: fit ? ff.formatMm(fit.widthMm) : null,
  }});
}}
let measureInNode;
try {{ measureInNode = ff.measureSvgFontPx("<svg><text>a</text></svg>"); }}
catch (e) {{ measureInNode = "threw: " + String(e); }}
process.stdout.write(JSON.stringify({{
  web,
  bands: ff.READABILITY_BANDS_PT,
  mmPerPx: ff.MM_PER_PX,
  ptPerPx: ff.PT_PER_PX,
  fallback: ff.MERMAID_FALLBACK_FONT_PX,
  measureInNode,
  v11Size: ff.svgIntrinsicSize(v11),
  v11Box: ff.svgIntrinsicBox(v11),
  unknownVariant: (() => {{
    try {{ ff.fitFigureWidthMm({{ vbW: 1, vbH: 1, baseFontPx: 14, boxWMm: null,
      boxHMm: null, variant: "poster", intrinsicWPx: null }}); return "no throw"; }}
    catch (e) {{ return "threw"; }}
  }})(),
}}));
"""


def test_frontend_fit_on_the_web_via_node() -> None:
    """I due casi «web senza box» della fixture, eseguiti con la copia
    frontend, danno le larghezze in px che il componente mette in
    `min(100%, Wpx)` (532 e 314 px); le costanti coincidono con
    `figure_scale`; `measureSvgFontPx` senza DOM torna `null` (fallback del
    tema, non un'eccezione) e il modulo è caricabile con
    `--experimental-strip-types` (solo import di tipo)."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node non disponibile: copia frontend non eseguibile")
    if not _FIGURE_FORMATS.is_file():
        pytest.skip(f"modulo frontend assente: {_FIGURE_FORMATS}")
    script = _NODE_RUNNER.format(
        module=str(_FIGURE_FORMATS), fixture=str(_SCALE_CASES), v11=str(_FLOWCHART_V11)
    )
    proc = subprocess.run(
        [node, "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)
    data = json.loads(_SCALE_CASES.read_text(encoding="utf-8"))
    web_cases = [
        c for c in data["fit"] if c["input"]["box_w_mm"] is None and c["input"]["box_h_mm"] is None
    ]
    assert len(web_cases) == 2, [c["name"] for c in web_cases]
    for case, fe in zip(web_cases, got["web"], strict=True):
        exp = case["expected"]
        assert fe["fit"]["widthMm"] == pytest.approx(exp["width_mm"], abs=1e-9), case["name"]
        assert fe["fit"]["textPt"] == pytest.approx(exp["text_pt"], abs=1e-9), case["name"]
        assert fe["fit"]["inBand"] is exp["in_band"], case["name"]
        assert fe["widthPx"] == pytest.approx(_web_width_px(exp["width_mm"]), abs=1e-9)
        assert fe["style"] == fs.format_mm(exp["width_mm"]), case["name"]
    # 140,76 mm = 532 px per il flowchart D8; 83,15 mm = 314 px per il
    # verticale 300×1000 (a HEAD: 900 px, scala 3).
    px = [fe["widthPx"] for fe in got["web"]]
    assert round(px[0]) == 532 and round(px[1]) == 314, px
    assert got["bands"] == {k: list(v) for k, v in fs.READABILITY_BANDS_PT.items()}
    assert got["mmPerPx"] == pytest.approx(fs.MM_PER_PX, abs=1e-15)
    assert got["ptPerPx"] == fs.PT_PER_PX
    assert got["fallback"] == fs.FALLBACK_BASE_FONT_PX["mermaid"]
    assert got["measureInNode"] is None, got["measureInNode"]
    assert got["v11Size"] == {"width": 507.828125, "height": 158}
    assert got["v11Box"] == {
        "vbW": 507.828125,
        "vbH": 158,
        "widthPx": None,
        "heightPx": None,
        "pxPerUnit": 1,
    }
    assert got["unknownVariant"] == "threw"


# --- Chromium: bundle del modulo vero ----------------------------------------


def _bundle_figure_formats() -> str:
    """`lib/figureFormats.ts` compilato in un IIFE con l'esbuild del
    frontend (`window.A4U_FF`), come test_frontend_mermaid_sanitize."""
    esbuild = _FRONTEND.parent / "node_modules" / ".bin" / "esbuild"
    if not esbuild.is_file() or shutil.which("node") is None:
        pytest.skip("esbuild del frontend o node non disponibili")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "ff.js"
        proc = subprocess.run(
            [
                str(esbuild),
                str(_FIGURE_FORMATS),
                "--bundle",
                "--format=iife",
                "--global-name=A4U_FF",
                "--platform=browser",
                "--target=es2020",
                f"--outfile={out}",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        return out.read_text(encoding="utf-8")


# Equivalenti delle utility Tailwind del componente (le classi arbitrarie
# `[&_svg]:…` sono compilate da Tailwind in `.<classe> svg { … }`) e di
# FigureFrame/VisualAssetBody per l'immagine caricata.
_UTILITY_CSS = """
.mx-auto { margin-left: auto; margin-right: auto; }
.block { display: block; }
.w-full { width: 100%; }
.h-auto { height: auto; }
.max-w-full { max-width: 100%; }
.outer { overflow-x: auto; padding: 0.5rem; box-sizing: content-box; }
.svg-full svg { width: 100% !important; max-width: none !important; height: auto; }
"""

_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
body, figure {{ margin: 0; }}
.lesson-prose img {{ max-width: 100%; height: auto; border-radius: 0.5rem; margin: 1rem 0; }}
.lesson-prose .figure img {{{rule}}}
{utilities}
</style><script>{bundle}</script></head><body>
<div class="lesson-prose" style="width: 900px">
  <figure class="figure" id="f-img"><div class="figure-body w-full">
    <img id="img" class="mx-auto block h-auto max-w-full" src="{image}">
  </div></figure>
  <div class="outer" style="width: 884px"><div class="mx-auto svg-full" id="flow"></div></div>
  <div class="outer" style="width: 884px"><div class="mx-auto svg-full" id="portrait"></div></div>
  <div class="outer" style="width: 884px"><div class="mx-auto svg-full" id="notext"></div></div>
  <div class="outer" style="width: 884px"><div class="svg-full" id="before"></div></div>
</div>
<div class="lesson-prose" style="width: 400px">
  <div class="outer" style="width: 400px"><div class="mx-auto svg-full" id="narrow"></div></div>
</div>
</body></html>"""

_IMAGE_400x300 = (
    "data:image/svg+xml;utf8,"
    "<svg xmlns='http://www.w3.org/2000/svg' width='400' height='300'></svg>"
)

_PORTRAIT_SVG = (
    '<svg width="100%" viewBox="0 0 300 1000" xmlns="http://www.w3.org/2000/svg">'
    '<rect width="300" height="1000" fill="none" stroke="#000"/>'
    '<text x="10" y="40" font-size="14">a</text></svg>'
)
_NOTEXT_SVG = (
    '<svg width="100%" viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg">'
    '<rect width="20" height="20"/></svg>'
)

# La pipeline del componente, eseguita con le funzioni VERE del bundle:
# sanificazione → strip del `max-width` → misura → fit senza box →
# `min(100%, Wpx)` sul wrapper; poi la misura di ciò che il lettore vede.
_BUILD_AND_MEASURE_JS = """
async ({ svgs, maxWidthRe }) => {
  const ff = window.A4U_FF;
  const strip = (s) => s.replace(new RegExp(maxWidthRe, "gi"), "");
  const fitted = {};
  for (const [id, raw] of Object.entries(svgs)) {
    const cleaned = strip(ff.sanitizeMermaidSvg(raw));
    const box = ff.svgIntrinsicBox(cleaned);
    const m = ff.measureSvgFontPx(cleaned);
    const baseFontPx = m === null ? ff.MERMAID_FALLBACK_FONT_PX : m.count === 0 ? null : m.min;
    const fit = box ? ff.fitFigureWidthMm({
      vbW: box.vbW, vbH: box.vbH, baseFontPx, boxWMm: null, boxHMm: null,
      variant: "lesson", intrinsicWPx: box.widthPx,
    }) : null;
    const widthPx = fit ? Math.floor((fit.widthMm / ff.MM_PER_PX) * 100) / 100 : null;
    const el = document.getElementById(id);
    if (id !== "before" && widthPx !== null) el.style.width = `min(100%, ${widthPx}px)`;
    el.innerHTML = cleaned;
    fitted[id] = { measured: m, fit, widthPx, cleaned };
  }
  const img = document.getElementById("img");
  if (!img.complete) await img.decode();
  const p = img.parentElement.getBoundingClientRect(), r = img.getBoundingClientRect();
  const out = { img: { width: r.width, left: r.left - p.left, right: p.right - r.right } };
  for (const id of Object.keys(svgs)) {
    const wrapper = document.getElementById(id);
    const outer = wrapper.parentElement;
    const svg = wrapper.querySelector("svg");
    const b = svg.getBoundingClientRect();
    const scale = svg.getScreenCTM().a;
    const text = svg.querySelector("text");
    const fontUu = text ? parseFloat(getComputedStyle(text).fontSize) : null;
    out[id] = {
      boxW: b.width, boxH: b.height, scale, fontUu,
      renderedPx: fontUu === null ? null : fontUu * scale,
      wrapperW: wrapper.getBoundingClientRect().width,
      overflow: outer.scrollWidth - outer.clientWidth,
      measured: fitted[id].measured, fit: fitted[id].fit, widthPx: fitted[id].widthPx,
    };
  }
  return out;
}
"""


@pytest.fixture(scope="module")
def fitted_geometry() -> Iterator[dict[str, Any]]:
    sync_api = pytest.importorskip("playwright.sync_api")
    v11 = _FLOWCHART_V11.read_text(encoding="utf-8")
    html = _PAGE.format(
        rule=_figure_img_rule(),
        utilities=_UTILITY_CSS,
        bundle=_bundle_figure_formats(),
        image=_IMAGE_400x300,
    )
    payload = {
        "svgs": {
            "flow": v11,
            "narrow": v11,
            "portrait": _PORTRAIT_SVG,
            "notext": _NOTEXT_SVG,
            "before": v11,
        },
        "maxWidthRe": _MAX_WIDTH_RE.pattern,
    }
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # launch di Chromium: verifica locale, non gate CI
            pytest.skip(f"Chromium non disponibile: {exc!r}"[:300])
        try:
            page = browser.new_page(viewport={"width": 1200, "height": 900})
            page.set_content(html)
            result = page.evaluate(_BUILD_AND_MEASURE_JS, payload)
        finally:
            browser.close()
    yield result


def test_uploaded_image_is_centered_in_lesson_prose(fitted_geometry: dict[str, Any]) -> None:
    img = fitted_geometry["img"]
    assert img["width"] == pytest.approx(400, abs=1)
    assert img["left"] == pytest.approx(250, abs=2)
    assert abs(img["left"] - img["right"]) < 2, img


def test_flowchart_lands_at_11pt_in_a_wide_column(fitted_geometry: dict[str, Any]) -> None:
    """Fixture v11 (min 14 uu) in una colonna di 884 px di contenuto: il
    modulo vero misura 14/6 testi, fitta a 140,76 mm = 532 px, scala
    1,0476 e il testo reso vale 14,67 px CSS = 11 pt (tetto della banda)."""
    flow = fitted_geometry["flow"]
    assert flow["measured"] == {"min": 14, "median": 14, "count": 6}
    parsed = svg_base_font_px(_FLOWCHART_V11.read_text(encoding="utf-8"))
    assert (parsed.font_px_min, parsed.text_count) == (14.0, 6)
    assert flow["fit"]["widthMm"] == pytest.approx(140.76, abs=1e-9)
    assert flow["fit"]["textPt"] == 11.0 and flow["fit"]["inBand"] is True
    assert flow["widthPx"] == pytest.approx(_web_width_px(140.76), abs=1e-9)
    assert flow["boxW"] == pytest.approx(532, abs=1)
    assert flow["scale"] == pytest.approx(1.0476, abs=0.005)
    assert flow["renderedPx"] == pytest.approx(14.67, abs=0.05)
    assert 8.0 <= flow["renderedPx"] * fs.PT_PER_PX <= 11.0 + 1e-6
    assert flow["overflow"] == 0


def test_narrow_column_wins_over_the_fit(fitted_geometry: dict[str, Any]) -> None:
    """Colonna di 400 px: `min(100%, 532px)` dà il 100 % senza scorrimento
    (nessun `clientWidth`: è il browser ad applicare la colonna)."""
    narrow = fitted_geometry["narrow"]
    assert narrow["widthPx"] == fitted_geometry["flow"]["widthPx"]
    assert narrow["boxW"] == pytest.approx(400, abs=1)
    assert narrow["wrapperW"] == pytest.approx(400, abs=1)
    assert narrow["scale"] == pytest.approx(400 / 507.828125, abs=0.005)
    assert narrow["overflow"] == 0


def test_portrait_svg_is_no_longer_stretched_to_the_column(
    fitted_geometry: dict[str, Any],
) -> None:
    """Verticale 300×1000 con testo a 14 px: 83,15 mm = 314 px (a HEAD era
    dilatato a tutta colonna, scala 3, testo 31 pt)."""
    portrait = fitted_geometry["portrait"]
    assert portrait["fit"]["widthMm"] == pytest.approx(83.15, abs=1e-9)
    assert portrait["boxW"] == pytest.approx(314, abs=1)
    assert portrait["boxH"] == pytest.approx(314 * 1000 / 300, abs=4)
    assert portrait["scale"] == pytest.approx(1.0476, abs=0.005)
    assert portrait["renderedPx"] == pytest.approx(14.67, abs=0.05)


def test_svg_without_text_keeps_its_natural_scale(fitted_geometry: dict[str, Any]) -> None:
    """Senza testo: scala naturale, non si dilata a colonna (20 uu → 20 px)."""
    notext = fitted_geometry["notext"]
    assert notext["measured"] == {"min": None, "median": None, "count": 0}
    assert notext["fit"]["textPt"] == 0 and notext["fit"]["inBand"] is True
    assert notext["boxW"] == pytest.approx(20, abs=0.5)
    assert notext["scale"] == pytest.approx(1.0, abs=0.01)


def test_the_full_width_geometry_of_head_was_out_of_band(
    fitted_geometry: dict[str, Any],
) -> None:
    """Controprova: la stessa fixture a larghezza piena (geometria di HEAD,
    senza wrapper) esce a 24,4 px CSS = 18,3 pt, fuori dalla banda 8-11."""
    before = fitted_geometry["before"]
    assert before["boxW"] == pytest.approx(884, abs=1)
    assert before["renderedPx"] * fs.PT_PER_PX > 11.0
    assert before["renderedPx"] * fs.PT_PER_PX == pytest.approx(18.3, abs=0.2)


# --- parità del JS di misura con la pagina di pre-render --------------------


def _require_cdn() -> None:
    try:
        socket.create_connection(("cdn.jsdelivr.net", 443), timeout=3).close()
    except OSError:
        pytest.skip("cdn.jsdelivr.net non raggiungibile")


_PARITY_JS = """
([svg, maxWidthRe]) => {
  const ff = window.A4U_FF;
  const before = document.body.children.length;
  const fe = ff.measureSvgFontPx(svg);
  const be = window.__measureSvgFontPx(svg);
  const cleaned = ff.sanitizeMermaidSvg(svg).replace(new RegExp(maxWidthRe, "gi"), "");
  const feCleaned = ff.measureSvgFontPx(cleaned);
  return { fe, be, feCleaned, hostLeft: document.body.children.length - before };
}
"""


def test_measure_js_parity_with_frontend() -> None:
    """`measureSvgFontPx` del bundle e `window.__measureSvgFontPx` della
    pagina di pre-render danno `{min, median, count}` identici sui 15
    campioni D8 resi da Mermaid e sulla fixture v11; la misura sull'SVG
    sanificato (il percorso del componente) conserva il minimo, perché la
    sanificazione non tocca `<style>`; nessun host resta nel documento."""
    sync_api = pytest.importorskip("playwright.sync_api")
    _require_cdn()
    bundle = _bundle_figure_formats()
    v11 = _FLOWCHART_V11.read_text(encoding="utf-8")
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--no-sandbox"])
        except Exception as exc:  # launch di Chromium: verifica locale, non gate CI
            pytest.skip(f"Chromium non disponibile: {exc!r}"[:300])
        try:
            page = browser.new_page()
            page.route(
                "**/*",
                lambda route: (
                    route.continue_()
                    if route.request.url.lower().startswith(PRERENDER_ALLOWED_PREFIX)
                    else route.abort()
                ),
            )
            page.set_content(build_mermaid_renderer_html(), wait_until="domcontentloaded")
            page.wait_for_function("window.__mermaidReady === true", timeout=20_000)
            page.add_script_tag(content=bundle)
            results: dict[str, dict[str, Any]] = {}
            for n, (kind, code) in enumerate(theme.MERMAID_D8_SAMPLES.items()):
                svg = page.evaluate(
                    "([id, c]) => window.__renderMermaid(id, c)", [f"parity-{n}", code]
                )
                assert isinstance(svg, str) and svg.startswith("<svg"), kind
                results[kind] = page.evaluate(_PARITY_JS, [svg, _MAX_WIDTH_RE.pattern])
            results["fixture_v11"] = page.evaluate(_PARITY_JS, [v11, _MAX_WIDTH_RE.pattern])
        finally:
            browser.close()
    assert len(results) == 16
    for kind, r in results.items():
        assert r["fe"] == r["be"], (kind, r)
        assert r["fe"]["count"] > 0 and r["fe"]["min"] > 0, (kind, r)
        assert r["feCleaned"]["min"] == r["be"]["min"], (kind, r)
        assert r["feCleaned"]["count"] == r["be"]["count"], (kind, r)
        assert r["hostLeft"] == 0, kind
    assert results["fixture_v11"]["be"] == {"min": 14, "median": 14, "count": 6}
    assert results["flowchart"]["be"]["min"] == 14
    assert results["sequenceDiagram"]["be"]["min"] == 16


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
