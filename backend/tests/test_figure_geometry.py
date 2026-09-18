"""Geometria delle figure rese (D14): `figure_geometry`, la misura nella
pagina del pre-render Mermaid e il loro percorso fino al report.

- Oracolo sugli SVG a incroci noti: due archi che si tagliano = 1, tre a
  stella = 3, fascio parallelo = 0, punto triplo = 1 incrocio e 3 coppie,
  estremi condivisi e tracciati dello stesso arco esclusi.
- Controprova: il conteggio Python coincide con quello di Chromium
  (`MEASURE_SVG_GEOMETRY_JS`) sui diciotto modelli DOT dell'editor
  (`hashTable` = 1, gli altri 0) e sugli SVG sintetici, compresi un
  tracciato con due sotto-tracciati e due archi ellittici (giro 1 della
  verifica, V1-F4: prima il JS contava il salto come segmento e Python
  usava la corda dell'arco).
- I quattro difetti di lettura DOT misurati in Python: nessuno sui diciotto
  modelli, l'arco che attraversa l'etichetta sul vecchio `layers`.
- Tetti di segmenti (figura e batch) con `figure_measure_skipped`, incroci
  oltre soglia come warning e voce del report (mai un rifiuto nel
  pre-render), `crossings` e `defects` nel `FigureFitEntry`.

Oracolo che falliva prima di WP5: `hashTable` ha un incrocio e nessuna
misura lo contava (`SvgMetrics` non aveva `crossings`).

Giro 2 della verifica:
- V2-F1: tre DOT storici da 256 archi (sotto `DOT_MAX_EDGES`, mai passati
  dal gate editoriale) più uno banale perdevano TUTTE le figure all'export:
  la misura Python costava 7 s a figura dentro il timeout di 20 s del
  batch. Ora il lavoro (coppie candidate) è contato prima dei confronti,
  con un tetto per figura e uno per batch.
- V2-F3: gli archi con `tooltip` (`g#a_edgeN > a > path`) non erano
  contati, né in Python né nel JS.
- V2-F4: il testo in Times (etichette HTML-like, blocchi `node [...]` del
  sorgente) era stimato con Noto Sans: falsi `text_outside_owner`.
- V2-N2: il warning di geometria della validazione aveva `asset_id` vuoto.

Giro 3 della verifica (i casi di costo sono in
`tests/test_figure_geometry_cost.py`):
- V3-N1: il JS campionava a passo LOCALE; con una scala la gobba stretta di
  `scala_gobba` valeva 0 incroci in Chromium e 2 in Python. Ora il passo
  locale è diviso per l'allungamento della trasformazione: stessa parità
  anche su `scala_gobba` e sul K4,4 scalato di Graphviz.
"""

from __future__ import annotations

import math
import random
import socket
import time
from typing import Any

import pytest
import structlog

from app.core.config import get_settings
from app.services import asset_validation_service as avs
from app.services import course_lesson_pdf_service as pdf
from app.services import figure_geometry as fg
from app.services import figure_render_service as frs
from app.services import mermaid_prerender as mp
from app.services.figure_geometry import GeometryReport
from app.services.figure_scale import SvgMetrics
from tests.test_frontend_figure_templates import DOT, MERMAID


def _edge(d: str) -> str:
    return f'<g class="edge"><path d="{d}"/></g>'


def _svg(*body: str, viewbox: str = "0 0 100 100") -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}">{"".join(body)}</svg>'


SYNTHETIC: dict[str, tuple[str, int, int]] = {
    "due_archi": (_svg(_edge("M0,0 L10,10"), _edge("M0,10 L10,0")), 1, 1),
    "stella_a_tre": (
        _svg(_edge("M0,0 L100,60"), _edge("M0,60 L100,0"), _edge("M10,-10 L10,100")),
        3,
        3,
    ),
    "fascio_parallelo": (_svg(*(_edge(f"M0,{y} L100,{y}") for y in range(0, 50, 5))), 0, 0),
    "punto_triplo": (
        _svg(_edge("M0,0 L20,20"), _edge("M0,20 L20,0"), _edge("M10,0 L10,20")),
        1,
        3,
    ),
    "estremo_condiviso": (_svg(_edge("M0,0 L10,10"), _edge("M10,10 L20,0")), 0, 0),
    "stesso_arco": (
        _svg('<g class="edge"><path d="M0,0 L10,10"/><path d="M0,10 L10,0"/></g>'),
        0,
        0,
    ),
    "curva_e_retta": (
        _svg(_edge("M0,50 C30,-20 70,120 100,50"), _edge("M0,40 L100,60")),
        3,
        3,
    ),
    "relativo_e_quadratica": (
        _svg(_edge("m0,0 l20,20"), _edge("M0,20 Q10,-10 20,20"), _edge("M0,10 H20")),
        4,
        4,
    ),
    # Il salto fra i due sotto-tracciati attraverserebbe la verticale.
    "salto_fra_sottotracciati": (
        _svg(
            _edge("M0,20 L60,20 M140,180 L200,180"),
            _edge("M100,0 L100,200"),
            viewbox="0 0 200 200",
        ),
        0,
        0,
    ),
    # Semicerchio per (100, 0): la verticale lo tocca nel proprio estremo;
    # la corda (y = 100) la taglierebbe.
    "arco_sopra_la_verticale": (
        _svg(
            _edge("M0,100 A100,100 0 0 1 200,100"),
            _edge("M100,0 L100,200"),
            viewbox="0 0 200 200",
        ),
        0,
        0,
    ),
    # Arco di Graphviz con `tooltip`: il tracciato sta in `g#a_edge1 > a`.
    "involucro_collegamento": (
        _svg(
            '<g class="edge"><g id="a_edge1"><a xlink:title="t"><path d="M0,0 L10,10"/></a>'
            "</g></g>",
            _edge("M0,10 L10,0"),
        ),
        1,
        1,
    ),
    # Semicerchio relativo di raggio 50 tagliato due volte da y = 60 (x = 20,
    # 80); arco grande, antiorario e ruotato tagliato una volta da x = 60.
    "archi_tagliati": (
        _svg(
            _edge("M0,100 a50,50 0 0 1 100,0"),
            _edge("M0,60 L100,60"),
            _edge("M0,150 A60,40 30 1 0 120,150"),
            _edge("M60,120 L60,240"),
            viewbox="0 0 200 250",
        ),
        3,
        3,
    ),
    # Giro 3, V3-N1: gobba stretta scalata 10 volte, tagliata due volte dalla
    # retta y = 9,45 (x = 14,2 e 25,8 nella radice). Il JS campionava a 2
    # unità LOCALI (20 della radice) e la gobba spariva: 0 in Chromium.
    "scala_gobba": (
        _svg(
            '<g transform="scale(10)">'
            + _edge("M0,10 Q2,8.8 4,10")
            + _edge("M-1,9.45 L5,9.45")
            + "</g>",
            viewbox="0 0 60 120",
        ),
        2,
        2,
    ),
}


@pytest.mark.parametrize(("name", "expected"), [(k, v[1:]) for k, v in SYNTHETIC.items()])
def test_known_crossings(name: str, expected: tuple[int, int]) -> None:
    report = fg.measure_svg(SYNTHETIC[name][0])
    assert (report.crossings, report.crossing_pairs) == expected, report
    assert report.skipped is None


def test_transforms_are_applied_before_measuring() -> None:
    """Il secondo arco è spostato fuori dal primo da un `translate`: niente
    incrocio; lo stesso arco con `scale(1)` invece lo taglia."""
    moved = _svg(
        _edge("M0,0 L10,10"),
        '<g transform="translate(50 0)">' + _edge("M0,10 L10,0") + "</g>",
    )
    assert fg.measure_svg(moved).crossings == 0
    kept = moved.replace("translate(50 0)", "scale(1) rotate(0)")
    assert fg.measure_svg(kept).crossings == 1
    rotated = _svg(
        _edge("M-10,0 L10,0"),
        '<g transform="rotate(90)">' + _edge("M-10,1 L10,1") + "</g>",
    )
    assert fg.measure_svg(rotated).crossings == 1


def test_parse_transform_composes_left_to_right() -> None:
    assert fg.parse_transform("translate(10 20) scale(2)") == (2.0, 0.0, 0.0, 2.0, 10.0, 20.0)
    a, b, c, d, e, f = fg.parse_transform("rotate(90 5 5)")
    assert (round(a, 9), round(b, 9), round(c, 9), round(d, 9)) == (0.0, 1.0, -1.0, 0.0)
    assert (round(e, 9), round(f, 9)) == (10.0, 0.0)
    assert fg.parse_transform("bogus(1)") == (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def test_mermaid_edge_classes_and_blind_spots() -> None:
    """`messageLine` (sequence) e le classi del renderer unificato sono
    archi; i `<line>` senza classe (assi del quadrant, griglia del gantt) e
    i link del sankey no."""
    message = '<line x1="0" y1="5" x2="10" y2="5" class="messageLine0"/>'
    flow = '<path class=" edge-thickness-normal flowchart-link" d="M5,0 L5,10"/>'
    assert fg.measure_svg(_svg(message, flow)).crossings == 1
    axes = '<line x1="0" y1="5" x2="10" y2="5"/><line x1="5" y1="0" x2="5" y2="10"/>'
    assert fg.measure_svg(_svg(axes)) == GeometryReport(0, 0, 0, 0)
    sankey = (
        '<g class="link"><path d="M0,0 L10,10"/></g><g class="link"><path d="M0,10 L10,0"/></g>'
    )
    assert fg.measure_svg(_svg(sankey)).crossings == 0
    polyline = '<polyline class="transition" points="0,0 10,10"/>'
    other = '<polyline class="relation" points="0,10 10,0"/>'
    assert fg.measure_svg(_svg(polyline, other)).crossings == 1
    assert fg.is_edge_class("edge-thickness-thick") and not fg.is_edge_class("edgeLabel")


def test_segment_cap_skips_the_measure() -> None:
    svg = SYNTHETIC["curva_e_retta"][0]
    report = fg.measure_svg(svg, max_segments=10)
    assert report.crossings is None and report.crossing_pairs is None
    assert report.skipped == fg.SKIP_FIGURE_CAP
    assert report.segments > 10 and report.edges == 2


def test_malformed_input_never_raises() -> None:
    assert fg.measure_svg("").skipped == "no_svg"
    assert fg.measure_svg("<g class='edge'><path d='M0,0 L1,1'/></g>").skipped == "no_svg"
    broken = _svg(_edge("M0,0 L10"), _edge("Z Z 3"), '<g class="edge"><path/></g>')
    assert fg.measure_svg(broken) == GeometryReport(0, 0, 0, 0)
    assert fg.segment_intersection((0, 0), (1, 1), (2, 2), (3, 3)) is None  # collineari
    # Coordinate al limite dei float (nota V2-N1 della coda di WP5): nessuna
    # eccezione, nessun punto non finito.
    underflow = _svg(_edge("M0 0 A1 1 0 0 1 1e-320 0"))
    assert fg.measure_svg(underflow).skipped is None
    huge = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 30000 30000">'
        '<g class="edge"><path d="M1.5e308 1.5e308 L1.5e308 1.5e308"/></g>'
        '<g class="edge"><path d="M10000 10000 L30000 30000"/></g></svg>'
    )
    fg.measure_svg(huge)
    big = 1.5e308
    assert fg.segment_intersection((-big, -big), (big, big), (-big, big), (big, -big)) is None


def test_elliptic_arcs_are_converted_to_cubics() -> None:
    """L'arco (SVG 1.1, F.6.5) passa per il punto atteso: semicerchio
    orario di raggio 100 da (0, 100) a (200, 100) con il vertice in
    (100, 0); raggi nulli = segmento; estremi coincidenti = nessun pezzo."""
    curves = fg._arc_curves((0.0, 100.0), (200.0, 100.0), 100, 100, 0, False, True)
    assert len(curves) == 2 and curves[0][0] == (0.0, 100.0) and curves[-1][-1] == (200.0, 100.0)
    top = fg._bezier(curves[0], 1.0)
    assert top == pytest.approx((100.0, 0.0))
    mid = fg._bezier(curves[0], 0.5)
    assert math.hypot(mid[0] - 100, mid[1] - 100) == pytest.approx(100, rel=3e-4)
    assert fg._arc_curves((0.0, 0.0), (10.0, 0.0), 0, 5, 0, False, True) == [
        ((0.0, 0.0), (10.0, 0.0))
    ]
    assert fg._arc_curves((1.0, 1.0), (1.0, 1.0), 5, 5, 0, False, True) == []
    # Raggi troppo piccoli: scalati fino a toccare gli estremi (semicerchio).
    small = fg._arc_curves((0.0, 0.0), (10.0, 0.0), 1, 1, 0, False, False)
    assert fg._bezier(small[0], 1.0) == pytest.approx((5.0, 5.0))


def test_text_width_uses_noto_sans_advances() -> None:
    assert fg.estimate_text_width("", 11) == 0
    assert fg.estimate_text_width("i", 1000) == 258
    assert fg.estimate_text_width("W", 10) == pytest.approx(9.3)
    # Lettere accentate come la base, ideogrammi a un em.
    assert fg.estimate_text_width("è", 10) == fg.estimate_text_width("e", 10)
    assert fg.estimate_text_width("字", 10) == 10


# ---------------------------------------------------------------------------
# Difetti di lettura DOT (Python)
# ---------------------------------------------------------------------------


def _dot_svg(source: str) -> str:
    renderer = frs.DotRenderer()
    return renderer._render_or_raise(renderer.sanitize(source))


@pytest.fixture(scope="module")
def dot_svgs() -> dict[str, str]:
    assert frs.REGISTRY["dot"].available(), "il binario `dot` serve a questa suite"
    return {tpl.id: _dot_svg(tpl.code) for tpl in DOT}


def test_dot_templates_have_no_python_defects_and_hashtable_has_one_crossing(
    dot_svgs: dict[str, str],
) -> None:
    reports = {name: fg.measure_dot_svg(svg) for name, svg in dot_svgs.items()}
    assert {n: r.defects for n, r in reports.items() if r.defects} == {}
    crossings = {n: r.crossings for n, r in reports.items()}
    assert crossings.pop("hashTable") == 1
    assert set(crossings.values()) == {0}


def test_python_defects_catch_an_edge_across_a_cluster_label() -> None:
    """Il vecchio `layers` (controprova di `test_frontend_figure_templates`):
    la freccia che entra nel livello attraversa il suo nome."""
    prima = """digraph architettura_a_livelli {
  rankdir=TB;
  subgraph cluster_applicazione {
    label="Livello applicativo";
    api [label="Servizi REST"];
    dominio [label="Logica di dominio"];
  }
  subgraph cluster_persistenza {
    label="Livello di persistenza";
    mappatura [label="Mappatura oggetti"];
  }
  api -> dominio;
  dominio -> mappatura;
}"""
    defects = fg.measure_dot_svg(_dot_svg(prima)).defects
    assert any(d.startswith(f"{fg.DEFECT_EDGE_CROSSES_LABEL}: ") for d in defects), defects


def test_python_defects_on_explicit_coordinates() -> None:
    """Testo fuori dalla tela, fuori dal proprio nodo, etichette sovrapposte:
    coordinate esplicite come in un SVG di Graphviz."""
    node = (
        '<g class="node"><polygon points="0,0 20,0 20,20 0,20 0,0"/>'
        '<text text-anchor="middle" x="10" y="14" font-size="10">Etichetta lunga</text></g>'
    )
    outside = '<g class="graph"><text x="95" y="50" font-size="10">Bordo</text></g>'
    overlap = (
        '<g class="edge"><path d="M0,90 L10,90"/>'
        '<text x="30" y="60" font-size="10">Primo</text></g>'
        '<g class="edge"><path d="M0,95 L10,95"/>'
        '<text x="32" y="62" font-size="10">Secondo</text></g>'
    )
    defects = fg.measure_dot_svg(_svg(node, outside, overlap)).defects
    codes = [d.split(":")[0] for d in defects]
    assert fg.DEFECT_TEXT_OUTSIDE_OWNER in codes
    assert fg.DEFECT_TEXT_OUTSIDE_CANVAS in codes
    assert fg.DEFECT_LABELS_OVERLAP in codes
    assert "«Etichetta lunga» fuori dal suo nodo di" in " ".join(defects)
    assert len(defects) <= fg.MAX_DEFECTS


# ---------------------------------------------------------------------------
# Controprova Chromium: stesso conteggio del JS di produzione
# ---------------------------------------------------------------------------


def _chromium_geometry(svgs: dict[str, str], *, texts: bool = False) -> dict[str, Any]:
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    options = mp.geometry_measure_options(texts=texts)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content("<html><body></body></html>")
            script = f"([s, o]) => ({mp.MEASURE_SVG_GEOMETRY_JS})(s, o)"
            return {name: page.evaluate(script, [svg, options]) for name, svg in svgs.items()}
        finally:
            browser.close()


_TOOLTIP_DOT = 'digraph G { a -> x [tooltip="t"]; b -> y [tooltip="u"]; a -> y; b -> x; }'
# Con `splines=curved` Graphviz emette il self-loop come tracciato degenere
# (`M116,-18C116,-18 116,-18 116,-18`, `getTotalLength()` 0): resta un arco
# e va contato da entrambi i lati.
_DEGENERATE_LOOP_DOT = (
    "digraph { splines=curved; v0 -> v1; v1 -> v2; v2 -> v0; "
    "v0 -> v2; v2 -> v0; v2 -> v2; v1 -> v2; }"
)
# K4,4 portato a 40 pollici (`scale(≈11)` sul gruppo del grafo): V3-N1.
_SCALED_DOT = (
    'digraph { graph [size="40,40!"]; '
    + " ".join(f"a{i} -> b{j};" for i in range(4) for j in range(4))
    + " }"
)


def test_python_count_matches_chromium_on_dot_templates_and_synthetic(
    dot_svgs: dict[str, str],
) -> None:
    svgs = {
        **dot_svgs,
        **{k: v[0] for k, v in SYNTHETIC.items()},
        "tooltip": _dot_svg(_TOOLTIP_DOT),
        "k44_scalato": _dot_svg(_SCALED_DOT),
        "cappio_degenere": _dot_svg(_DEGENERATE_LOOP_DOT),
    }
    assert 'transform="scale(1 1) ' not in svgs["k44_scalato"]
    assert "C116,-18 116,-18 116,-18" in svgs["cappio_degenere"]
    js = _chromium_geometry(svgs)
    python = {name: fg.measure_svg(svg) for name, svg in svgs.items()}
    assert {n: (r["crossings"], r["pairs"]) for n, r in js.items()} == {
        n: (r.crossings, r.crossing_pairs) for n, r in python.items()
    }
    assert {n: r["edges"] for n, r in js.items()} == {n: r.edges for n, r in python.items()}
    # Con la scala il JS campiona in unità della radice come Python.
    assert js["scala_gobba"]["segments"] == python["scala_gobba"].segments
    assert python["k44_scalato"].crossings > 0
    assert js["hashTable"]["crossings"] == 1
    assert (js["tooltip"]["edges"], js["tooltip"]["crossings"]) == (4, 1)
    assert {n: r["crossings"] for n, r in js.items() if n in dot_svgs and n != "hashTable"} == {
        n: 0 for n in dot_svgs if n != "hashTable"
    }
    assert all(r["skipped"] is None for r in js.values())


def test_chromium_caps_skip_early() -> None:
    svg = SYNTHETIC["curva_e_retta"][0]
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content("<html><body></body></html>")
            script = f"([s, o]) => ({mp.MEASURE_SVG_GEOMETRY_JS})(s, o)"
            figure = page.evaluate(script, [svg, {"maxSegments": 10}])
            batch = page.evaluate(script, [svg, {"budget": 10}])
        finally:
            browser.close()
    assert (figure["skipped"], figure["crossings"]) == (fg.SKIP_FIGURE_CAP, None)
    assert (batch["skipped"], batch["crossings"]) == (fg.SKIP_BATCH_CAP, None)


# ---------------------------------------------------------------------------
# Pre-render Mermaid: la misura nella stessa pagina
# ---------------------------------------------------------------------------


def _cdn_or_fail() -> None:
    try:
        socket.create_connection(("cdn.jsdelivr.net", 443), timeout=3).close()
    except OSError:  # pragma: no cover - verifica locale, non gate CI
        pytest.skip("cdn.jsdelivr.net non raggiungibile")


_CROSSED_FLOWCHART = "flowchart TD\n" + "\n".join(
    f"  {s} --> {t}" for s in ("A", "B", "C", "D") for t in ("W", "X", "Y", "Z")
)


def test_mermaid_prerender_measures_geometry_without_touching_the_svg() -> None:
    _cdn_or_fail()
    codes = [MERMAID[0].code, _CROSSED_FLOWCHART]
    measured = mp._prerender_mermaid_batch_sync(codes)
    plain = mp._prerender_mermaid_to_svg_batch_sync(codes)
    assert [m.svg if m else None for m in measured] == plain
    assert all(m is not None and m.geometry is not None for m in measured)
    flow, crossed = (m.geometry for m in measured if m is not None)
    assert flow is not None and crossed is not None
    assert (flow.crossings, flow.edges, flow.skipped) == (0, 5, None)
    assert crossed.edges == 16 and crossed.crossings is not None and crossed.crossings > 0
    # Stesso numero di Python sullo stesso SVG.
    second = measured[1]
    assert second is not None
    assert crossed.crossings == fg.measure_svg(second.svg).crossings


def test_mermaid_batch_budget_is_shared_by_the_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """Con un tetto di batch minuscolo la prima figura consuma il residuo e
    la seconda è saltata; con un tetto per figura minuscolo lo sono
    entrambe."""
    _cdn_or_fail()
    code = MERMAID[0].code
    monkeypatch.setattr(fg, "MAX_BATCH_MEASURE_SEGMENTS", 300)
    first, second = mp._prerender_mermaid_batch_sync([code, code])
    assert first is not None and first.geometry is not None
    assert first.geometry.skipped is None
    assert second is not None and second.geometry is not None
    assert second.geometry.skipped == fg.SKIP_BATCH_CAP
    monkeypatch.setattr(fg, "MAX_MEASURE_SEGMENTS", 5)
    only = mp._prerender_mermaid_batch_sync([code])[0]
    assert only is not None and only.geometry is not None
    assert only.geometry.skipped == fg.SKIP_FIGURE_CAP


def test_geometry_from_page_handles_missing_and_malformed_results() -> None:
    ok = mp._geometry_from_page(
        {"crossings": 2, "pairs": 3, "edges": 4, "segments": 50, "defects": ["x: y"],
         "skipped": None},
        preview="p",
    )  # fmt: skip
    assert ok == GeometryReport(2, 3, 4, 50, ("x: y",))
    skipped = mp._geometry_from_page(
        {"crossings": None, "pairs": None, "edges": 9, "segments": 60000, "defects": [],
         "skipped": fg.SKIP_FIGURE_CAP},
        preview="p",
    )  # fmt: skip
    assert skipped == GeometryReport(None, None, 9, 60000, (), fg.SKIP_FIGURE_CAP)
    with structlog.testing.capture_logs() as logs:
        assert mp._geometry_from_page(None, preview="flowchart LR") is None
        assert mp._geometry_from_page({"crossings": "x"}, preview="p") is None
        bad = {"crossings": 1, "pairs": 1, "edges": 1, "segments": 1, "defects": [3]}
        assert mp._geometry_from_page({**bad, "skipped": None}, preview="p") is None
    assert [e["event"] for e in logs] == ["mermaid_geometry_measure_failed"] * 3


def test_page_options_come_from_the_python_constants() -> None:
    html = mp.build_mermaid_renderer_html(version="11.17.2")
    assert "window.__measureSvg = (svg, opts) =>" in html
    assert '"maxSegments": 50000' in html and '"batchSegments": 150000' in html
    assert "__MERMAID_GEOMETRY" not in html
    options = mp.geometry_measure_options()
    assert options["step"] == fg.SAMPLE_STEP and options["clusterRadius"] == fg.CLUSTER_RADIUS


# ---------------------------------------------------------------------------
# Dal renderer al report
# ---------------------------------------------------------------------------


def _fake_batch(geometry: GeometryReport | None) -> Any:
    svg = '<svg id="m" width="100%" viewBox="0 0 10 10"><text font-size="14">a</text></svg>'
    measured = SvgMetrics(14.0, 14.0, 1, "measured")
    return lambda codes: [mp.MermaidPrerender(svg, measured, geometry) for _ in codes]


def test_mermaid_crossings_over_threshold_are_a_warning_not_a_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _fake_batch(GeometryReport(6, 7, 9, 400))
    monkeypatch.setattr(frs, "_prerender_mermaid_batch_sync", fake)
    with structlog.testing.capture_logs() as logs:
        (fig,) = frs.REGISTRY["mermaid"].render_figure_batch(["x"], asset_ids=["M1"])
    assert fig is not None and fig.metrics is not None
    assert fig.metrics.crossings == 6
    assert fig.metrics.defects[0].startswith("graph_too_dense: incroci fra archi 6 > 4 — ")
    assert "rank" not in fig.metrics.defects[0]  # consiglio nella sintassi Mermaid
    events = [e for e in logs if e["event"] == "figure_geometry_defects"]
    assert [(e["asset_id"], e["format"], e["crossings"]) for e in events] == [("M1", "mermaid", 6)]


def test_skipped_measure_is_logged_and_leaves_crossings_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = GeometryReport(None, None, 120, 61_000, skipped=fg.SKIP_FIGURE_CAP)
    monkeypatch.setattr(frs, "_prerender_mermaid_batch_sync", _fake_batch(report))
    with structlog.testing.capture_logs() as logs:
        (fig,) = frs.REGISTRY["mermaid"].render_figure_batch(["x"], asset_ids=["M2"])
    assert fig is not None and fig.metrics is not None
    assert (fig.metrics.crossings, fig.metrics.defects) == (None, ())
    assert [
        (e["asset_id"], e["reason"], e["segments"], e["edges"])
        for e in logs
        if e["event"] == "figure_measure_skipped"
    ] == [("M2", fg.SKIP_FIGURE_CAP, 61_000, 120)]


async def test_dot_figures_carry_geometry_through_the_render_map(
    dot_svgs: dict[str, str],
) -> None:
    frs.clear_svg_cache()
    frs.available_formats.cache_clear()
    code = next(t.code for t in DOT if t.id == "hashTable")
    assets = [{"asset_id": "H", "format": "dot", "content": code}]
    first = await frs.render_figure_map(assets, language="it")
    again = await frs.render_figure_map(assets, language="it")
    for figs in (first, again):
        metrics = figs["H"].metrics
        assert metrics is not None
        assert (metrics.crossings, metrics.defects) == (1, ())
        assert metrics.source == "parsed"
    assert frs.REGISTRY["dot"].validate(code, deep=True) == (True, "")


class _MeasuringRenderer:
    """Renderer esterno che restituisce stringhe ed espone `measure`."""

    fmt = "dot"

    def available(self) -> bool:
        return True

    def sanitize(self, content: str) -> str:
        return content

    def render_svg_batch(self, contents: list[str], *, asset_ids: list[str]) -> list[str | None]:
        return [f'<svg viewBox="0 0 10 10"><text font-size="9">{c}</text></svg>' for c in contents]

    def measure(self, svg: str) -> GeometryReport:
        return GeometryReport(2, 2, 3, 30, ("labels_overlap: «a»/«b» sovrapposte di 1.0",))


async def test_optional_measure_is_dispatched_with_getattr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(frs.REGISTRY, "dot", _MeasuringRenderer())
    frs.clear_svg_cache()
    frs.available_formats.cache_clear()
    figs = await frs.render_figure_map(
        [{"asset_id": "X", "format": "dot", "content": "k"}], language="it"
    )
    metrics = figs["X"].metrics
    assert metrics == SvgMetrics(
        9.0, 9.0, 1, "parsed", crossings=2, defects=("labels_overlap: «a»/«b» sovrapposte di 1.0",)
    )
    frs.available_formats.cache_clear()


class _BrokenMeasure(_MeasuringRenderer):
    def measure(self, svg: str) -> GeometryReport:
        raise RuntimeError("misura rotta")


async def test_a_failing_measure_never_costs_the_figure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(frs.REGISTRY, "dot", _BrokenMeasure())
    frs.clear_svg_cache()
    frs.available_formats.cache_clear()
    with structlog.testing.capture_logs() as logs:
        figs = await frs.render_figure_map(
            [{"asset_id": "Y", "format": "dot", "content": "k"}], language="it"
        )
    assert figs["Y"].metrics == SvgMetrics(9.0, 9.0, 1, "parsed")
    assert [(e["asset_id"], e["error"]) for e in logs if e["event"] == "figure_measure_failed"] == [
        ("Y", "misura rotta")
    ]
    frs.available_formats.cache_clear()


def test_fit_entry_carries_crossings_and_defects() -> None:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="100%" viewBox="0 0 600 200">'
        '<text font-size="14">a</text></svg>'
    )
    metrics = SvgMetrics(14.0, 14.0, 1, "measured", crossings=6, defects=("graph_too_dense: x",))
    report: list[Any] = []
    with structlog.testing.capture_logs() as logs:
        pdf._figure_width_style(
            frs.RenderedFigure(svg, metrics),
            fmt="mermaid",
            variant="lesson",
            box=(170.0, 240.0),
            asset_id="A",
            lesson_code="M1.L1",
            fit_report=report,
        )
    assert [(e.crossings, e.defects) for e in report] == [(6, ("graph_too_dense: x",))]
    assert [e["crossings"] for e in logs if e["event"] == "figure_fit"] == [6]


# ---------------------------------------------------------------------------
# Giro 2: tetto di lavoro, involucri, famiglia del font, asset_id
# ---------------------------------------------------------------------------


def _bipartite(tails: int, heads: int, name: str = "G") -> str:
    body = " ".join(f"s{i} -> t{j};" for i in range(tails) for j in range(heads))
    return f"digraph {name} {{ {body} }}"


def test_work_is_counted_before_comparing_and_capped() -> None:
    svg = SYNTHETIC["curva_e_retta"][0]
    full = fg.measure_svg(svg)
    assert full.skipped is None and full.work > 0
    capped = fg.measure_svg(svg, max_work=full.work - 1)
    assert (capped.crossings, capped.skipped, capped.work) == (None, fg.SKIP_WORK_CAP, full.work)
    assert (capped.edges, capped.segments) == (full.edges, full.segments)
    batch = fg.measure_svg(svg, work_left=full.work - 1)
    assert (batch.crossings, batch.skipped) == (None, fg.SKIP_BATCH_WORK_CAP)
    assert fg.measure_svg(svg, work_left=full.work) == full
    # Residuo esaurito: nemmeno il parsing.
    assert fg.measure_svg("<svg", work_left=0) == GeometryReport(
        None, None, 0, 0, skipped=fg.SKIP_BATCH_WORK_CAP
    )
    # I modelli dell'editor stanno tre ordini di grandezza sotto il tetto.
    assert fg.MAX_MEASURE_WORK < fg.MAX_BATCH_MEASURE_WORK


def test_dense_dot_is_skipped_by_work_not_by_segments() -> None:
    """Il bipartito 16×16 ha meno segmenti del tetto Chromium ma 12,5 milioni
    di coppie candidate: la misura è saltata in una frazione del suo costo
    (7,4 s prima della correzione)."""
    svg = _dot_svg(_bipartite(16, 16))
    started = time.perf_counter()
    report = fg.measure_dot_svg(svg)
    elapsed = time.perf_counter() - started
    assert report.segments < fg.MAX_MEASURE_SEGMENTS
    assert report.skipped == fg.SKIP_WORK_CAP and report.crossings is None
    assert report.work > 10 * fg.MAX_MEASURE_WORK
    assert elapsed < 1.0


async def test_dense_historical_dot_figures_survive_the_export_batch() -> None:
    """Oracolo V2-F1: prima «20.0s rendered: [] failed: [mlp0, mlp1, mlp2,
    small]», tutte in cache negativa. Ora le quattro sono rese; le tre dense
    senza incroci (`figure_work_cap`), la banale misurata."""
    frs.clear_svg_cache()
    frs.available_formats.cache_clear()
    assets: list[dict[str, str]] = [
        {"asset_id": f"mlp{k}", "format": "dot", "content": _bipartite(16, 16, f"G{k}")}
        for k in range(3)
    ]
    assets.append({"asset_id": "small", "format": "dot", "content": "digraph { a -> b }"})
    started = time.perf_counter()
    with structlog.testing.capture_logs() as logs:
        figures = await frs.render_figure_map(assets, language="it")
    elapsed = time.perf_counter() - started
    assert sorted(figures) == ["mlp0", "mlp1", "mlp2", "small"]
    assert [e for e in logs if e["event"] == "figure_render_failed"] == []
    skipped = [(e["asset_id"], e["reason"]) for e in logs if e["event"] == "figure_measure_skipped"]
    assert skipped == [(f"mlp{k}", fg.SKIP_WORK_CAP) for k in range(3)]
    crossings = {k: f.metrics.crossings for k, f in figures.items() if f.metrics is not None}
    assert crossings == {"mlp0": None, "mlp1": None, "mlp2": None, "small": 0}
    assert elapsed < float(get_settings().figure_render_timeout_seconds) / 4


def test_dot_batch_shares_one_work_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un solo tetto per batch: esaurito, le figure seguenti escono con l'SVG
    ma senza incroci (`batch_work_cap`)."""
    frs.clear_svg_cache()
    code = next(t.code for t in DOT if t.id == "hashTable")
    work = fg.measure_dot_svg(_dot_svg(code)).work
    assert work > 0
    monkeypatch.setattr(fg, "MAX_BATCH_MEASURE_WORK", work + work // 2)
    contents = [f"{code}\n// copia {k}" for k in range(3)]
    with structlog.testing.capture_logs() as logs:
        figures = frs.REGISTRY["dot"].render_figure_batch(contents, asset_ids=["a", "b", "c"])
    assert all(f is not None and f.svg for f in figures)
    assert [f.metrics.crossings for f in figures if f is not None and f.metrics] == [1, None, None]
    skipped = [(e["asset_id"], e["reason"]) for e in logs if e["event"] == "figure_measure_skipped"]
    assert skipped == [("b", fg.SKIP_BATCH_WORK_CAP), ("c", fg.SKIP_BATCH_WORK_CAP)]


def test_link_wrappers_do_not_hide_edges_labels_or_shapes() -> None:
    """V2-F3: prima 2 archi su 4 (quelli con `tooltip` mancavano)."""
    svg = _dot_svg(_TOOLTIP_DOT)
    assert '<g id="a_edge1"><a' in svg
    report = fg.measure_dot_svg(svg)
    assert (report.edges, report.crossings, report.defects) == (4, 1, ())
    wrapped_node = (
        '<g class="node"><g id="a_node1"><a xlink:title="n">'
        '<polygon points="40,0 60,0 60,20 40,20 40,0"/>'
        '<text text-anchor="middle" x="50" y="14" font-size="10">Etichetta lunga</text>'
        "</a></g></g>"
    )
    defects = fg.measure_dot_svg(_svg(wrapped_node)).defects
    assert [d.split(":")[0] for d in defects] == [fg.DEFECT_TEXT_OUTSIDE_OWNER]


def test_text_width_tables_follow_the_font_family() -> None:
    assert fg.font_kind("") == fg.font_kind("Noto Sans") == fg.FONT_SANS
    assert fg.font_kind('"Noto Sans", "DejaVu Sans", sans-serif') == fg.FONT_SANS
    assert fg.font_kind("Times,serif") == fg.font_kind("Times-Roman") == fg.FONT_SERIF
    assert fg.font_kind("serif") == fg.FONT_SERIF
    assert fg.font_kind("Courier,monospace") == fg.font_kind("monospace") == fg.FONT_MONO
    assert fg.font_kind("Helvetica,sans-Serif") is None
    assert fg.estimate_text_width("i", 1000, kind=fg.FONT_SERIF) == 278
    assert fg.estimate_text_width("W", 1000, kind=fg.FONT_SERIF) == 944
    assert fg.estimate_text_width("iW", 1000, kind=fg.FONT_MONO) == 1200
    assert fg.estimate_text_width("è", 10, kind=fg.FONT_SERIF) == pytest.approx(4.44)


def test_times_table_matches_chromium() -> None:
    """Le larghezze Times-Roman coincidono con il Times di Chromium (il font
    con cui l'SVG di Graphviz è reso nell'export)."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    chars = "".join(chr(c) for c in range(32, 127))
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            widths = page.evaluate(
                """(s) => { const c = document.createElement("canvas").getContext("2d");
                c.font = "1000px Times"; return [...s].map((ch) => c.measureText(ch).width); }""",
                chars,
            )
        finally:
            browser.close()
    estimated = [fg.estimate_text_width(ch, 1000, kind=fg.FONT_SERIF) for ch in chars]
    assert [(ch, round(w)) for ch, w, e in zip(chars, widths, estimated, strict=True)
            if abs(w - e) > 5] == []  # fmt: skip


_ER_TABLE_DOT = """digraph ER {
  node [shape=plaintext];
  studente [label=<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0">
    <TR><TD><B>Studente</B></TD></TR>
    <TR><TD>matricola: INTEGER PK</TD></TR>
    <TR><TD>nome: VARCHAR(80)</TD></TR>
    <TR><TD>email: VARCHAR(120)</TD></TR>
    <TR><TD>anno_corso: SMALLINT</TD></TR>
  </TABLE>>];
  esame [label=<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0">
    <TR><TD><B>Esame</B></TD></TR>
    <TR><TD>codice: CHAR(6) PK</TD></TR>
    <TR><TD>voto: SMALLINT</TD></TR>
  </TABLE>>];
  studente -> esame [label="sostiene"];
}"""


def test_times_labels_and_long_theme_labels_have_no_false_overflow() -> None:
    """V2-F4: prima «text_outside_owner» di 6,5 / 0,8 / 8,8 / 3,5 sull'entità
    in Times (Chromium: al più 0,9 reali) e di 5,0 sui nodi da 60 caratteri."""
    long_label = "etichetta lunga sessantaquattro caratteri per allargare il n"
    nodes = "\n".join(f'  a{i} [label="{long_label} {i}"];' for i in range(3))
    long_nodes = f"graph G {{\n{nodes}\n  a0 -- b0;\n}}"
    er_svg = _dot_svg(_ER_TABLE_DOT)
    assert 'font-family="Times,serif"' in er_svg
    assert fg.measure_dot_svg(er_svg).defects == ()
    assert fg.measure_dot_svg(_dot_svg(long_nodes)).defects == ()


def test_unknown_font_families_are_not_estimated() -> None:
    """Punto cieco dichiarato: senza tabella per la famiglia, nessun controllo."""
    node = (
        '<g class="node"><polygon points="40,0 60,0 60,20 40,20 40,0"/>'
        '<text text-anchor="middle" x="50" y="14" font-family="Helvetica,sans-Serif" '
        'font-size="10">Etichetta lunga</text></g>'
    )
    assert fg.measure_dot_svg(_svg(node)).defects == ()
    times = node.replace("Helvetica,sans-Serif", "Times,serif")
    assert [d.split(":")[0] for d in fg.measure_dot_svg(_svg(times)).defects] == [
        fg.DEFECT_TEXT_OUTSIDE_OWNER
    ]


def _pairwise_overlaps(labels: list[Any]) -> list[tuple[str, str, float]]:
    """Riferimento: la scansione a coppie per ascissa di prima del giro 2."""
    ordered = sorted(labels, key=lambda lab: lab.box[0])
    out = []
    for i, a in enumerate(ordered):
        for b in ordered[i + 1 :]:
            if b.box[0] >= a.box[2] - fg.TEXT_TOLERANCE:
                break
            overlap = min(
                min(a.box[2], b.box[2]) - max(a.box[0], b.box[0]),
                min(a.box[3], b.box[3]) - max(a.box[1], b.box[1]),
            )
            if overlap > fg.TEXT_TOLERANCE:
                out.append((a.text, b.text, overlap))
    return out


def test_overlaps_on_the_grid_match_the_pairwise_scan() -> None:
    rng = random.Random(20260917)
    labels = []
    for i in range(300):
        x, y = rng.uniform(0, 400), rng.uniform(0, 400)
        w, h = rng.uniform(2, 120), rng.uniform(2, 20)
        labels.append(fg._Label(f"t{i}", (x, y, x + w, y + h), i, "edge", None))
    expected = _pairwise_overlaps(labels)
    assert len(expected) > 50
    found = fg._overlaps(labels, fg._label_index(labels))
    assert [(a.text, b.text, o) for a, b, o in found] == expected


async def test_validation_logs_carry_the_asset_id() -> None:
    """V2-N2: `validate(deep=True)` non conosce l'asset; il servizio di
    validazione lo passa nel contesto (prima `asset_id=` vuoto)."""
    frs.clear_svg_cache()
    frs.available_formats.cache_clear()
    source = _bipartite(5, 9)  # 45 archi: entro il gate, 81 incroci
    with structlog.testing.capture_logs() as logs:
        checks = await avs.validate_assets_for_test([("asset:B59", "dot", source)])
    assert [(c.id, c.ok) for c in checks] == [("asset:B59", True)]
    events = [e for e in logs if e["event"] == "figure_geometry_defects"]
    assert [(e["asset_id"], e["crossings"]) for e in events] == [("B59", 81)]
    frs.clear_svg_cache()
    with structlog.testing.capture_logs() as logs:
        assert frs.REGISTRY["dot"].validate(source, deep=True) == (True, "")
    assert [e["asset_id"] for e in logs if e["event"] == "figure_geometry_defects"] == [""]
