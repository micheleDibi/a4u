"""Costo della misura geometrica limitato per costruzione (giro 3 di WP5).

Oracolo che falliva prima della correzione (V3-F1, codice di 3d71f85): la
misura Python dei DOT enumerava tutte le celle da 16 unità del riquadro di
ogni etichetta PRIMA di ogni tetto. `fontsize=4000` costava 1,8 s e
371 MB, `fontsize=8000` 7,7 s e 1,3 GB, `fontsize=1000000` e
`size="3000,3000!"` superavano 2 GB (processo ucciso), e
`render_figure_map([BAD, GOOD])` perdeva anche la figura buona. I casi
girano qui in processi figli con un tetto di tempo e di memoria: sul
codice di prima i figli superano i limiti (o sono uccisi) e il test
fallisce senza portare giù la suite.

- I sei casi obbligatori, in `validate(deep=True)` e all'export: meno di
  2 s e meno di 300 MB ciascuno, entrambe le figure rese.
- Forme dei nodi enormi (riquadro esatto delle Bézier, non campionato) e
  un fascio di 100 archi per un punto (raggruppamento a piano: 2,6 s
  prima, un incrocio ora in una frazione).
- La misura JS in Chromium sugli equivalenti (V3-F1 nel JS: un arco
  scalato 5000 volte finiva in «Map maximum size exceeded» dopo 8 s; un
  intervallo con due salti tracciava un segmento di un milione di unità e
  non finiva in 60 s).
- V1-F2 (giro 1 della coda, presente già in f397a9f): un DOT con `id=` o
  `class=` fatto di `&#` e più di 4.300 cifre faceva sollevare
  `ValueError` a `validate(deep=True)` e faceva perdere all'export anche
  la figura buona (`render_figure_map` dava `{}`). Ora la lettura lascia
  il valore com'è, e un'eccezione imprevista nel batch DOT costa la sola
  figura che l'ha sollevata.
- Proprietà della griglia (al più `MAX_GRID_CELLS` celle su qualunque
  tela, passo 16 sui diciotto modelli), riquadri esatti delle curve,
  raggruppamento uguale al riferimento quadratico, geometria non finita,
  lavoro eseguito sottratto al batch anche quando la misura è saltata.
"""

from __future__ import annotations

import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest
import structlog

from app.services import figure_geometry as fg
from app.services import figure_render_service as frs
from app.services import mermaid_prerender as mp
from app.services import svg_normalize
from tests.test_frontend_figure_templates import DOT

_BACKEND = Path(__file__).resolve().parents[1]
_SECONDS = 2.0
_RSS_MB = 300.0
# Oltre questi limiti il figlio è fermato: il codice di prima arrivava a
# 7 GB e oltre 40 s su un solo caso.
_KILL_SECONDS = 30.0
_KILL_RSS_MB = 1500.0
_GOOD = "digraph { a -> b }"
# Cifre oltre il limite di conversione degli interi (4.300 di default).
_DIGITS = (sys.get_int_max_str_digits() or 4300) + 100


def _many_labels() -> str:
    nodes = " ".join(
        f'n{i} [label="Etichetta media numero {i:02d}" fontsize=1500];' for i in range(30)
    )
    edges = " ".join(f"n{i} -> n{i + 10};" for i in range(20))
    return f"digraph {{ {nodes} {edges} }}"


def _concurrent(count: int, radius: float) -> str:
    """`count` archi rettilinei per il centro di un cerchio (neato, nodi
    fissati): tutti gli incroci cadono nello stesso punto."""
    nodes: list[str] = []
    edges: list[str] = []
    for k in range(count):
        angle = math.pi * k / count
        x, y = radius * math.cos(angle), radius * math.sin(angle)
        size = "width=0.05 height=0.05"
        nodes.append(f'p{k} [pos="{x:.4f},{y:.4f}!" label="" {size}];')
        nodes.append(f'q{k} [pos="{-x:.4f},{-y:.4f}!" label="" {size}];')
        edges.append(f"p{k} -> q{k};")
    head = "digraph { layout=neato; splines=line; node [shape=point]; edge [arrowhead=none];"
    return f"{head} {' '.join(nodes)} {' '.join(edges)} }}"


COST_CASES: dict[str, str] = {
    "fontsize_4000": (
        'digraph { a [label="Nodo con un testo abbastanza lungo" fontsize=4000]; b; a -> b }'
    ),
    "fontsize_8000": (
        'digraph { a [label="Nodo con un testo abbastanza lungo" fontsize=8000]; b; a -> b }'
    ),
    "fontsize_1000000": 'digraph { a [label="Nodo" fontsize=1000000]; }',
    "size_3000_bang": 'digraph { graph [size="3000,3000!"]; a [label="Nodo con testo"]; }',
    # Trenta etichette medie: nessuna enorme, la somma sì (5,5 s e 970 MB).
    "many_labels": _many_labels(),
    # Arco scalato ~1000 volte con etichetta: 30.000 segmenti, sotto il
    # tetto dei segmenti, e tre riquadri da milioni di celle.
    "long_scaled_edge": (
        'digraph { graph [size="3000,3000!"]; a -> b [label="arco con etichetta lunga"]; }'
    ),
    # Giro 1 della coda (V1-F2): Graphviz ricopia il riferimento in `id` e
    # `class`, e `html.unescape` della lettura sollevava `ValueError`.
    "entita_id": 'digraph { a [id="&#' + "9" * _DIGITS + ';"]; a -> b }',
    "entita_class": 'digraph { a [class="&#' + "9" * _DIGITS + ';"]; a -> b }',
}

_CHILD = r"""
import asyncio, json, os, resource, sys, threading, time

mode, source, good, kill_rss_mb = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])


def rss_mb():
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return raw / 2**20 if sys.platform == "darwin" else raw / 2**10


def watchdog():
    while True:
        if rss_mb() > kill_rss_mb:
            print("COST " + json.dumps({"killed": f"rss {rss_mb():.0f} MB"}), flush=True)
            os._exit(3)
        time.sleep(0.02)


from app.services import figure_geometry as fg
from app.services import figure_render_service as frs
from app.services import mermaid_prerender as mp

threading.Thread(target=watchdog, daemon=True).start()
dot = frs.REGISTRY["dot"]
if mode == "validate":
    started = time.perf_counter()
    result = list(dot.validate(source, deep=True))
    seconds = time.perf_counter() - started
elif mode == "export":
    assets = [
        {"asset_id": "BAD", "format": "dot", "content": source},
        {"asset_id": "GOOD", "format": "dot", "content": good},
    ]
    started = time.perf_counter()
    result = sorted(asyncio.run(frs.render_figure_map(assets, language="it")))
    seconds = time.perf_counter() - started
elif mode == "measure":
    svg = dot._render_or_raise(dot.sanitize(source))
    started = time.perf_counter()
    report = fg.measure_dot_svg(svg)
    seconds = time.perf_counter() - started
    result = [report.crossings, report.skipped, report.work]
else:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content("<html><body></body></html>")
        script = f"([s, o]) => ({mp.MEASURE_SVG_GEOMETRY_JS})(s, o)"
        options = mp.geometry_measure_options(texts=False)
        started = time.perf_counter()
        raw = page.evaluate(script, [source, options])
        seconds = time.perf_counter() - started
        browser.close()
    result = [raw["crossings"], raw["skipped"], raw["segments"]]
out = {"result": result, "seconds": seconds, "rss_mb": rss_mb()}
print("COST " + json.dumps(out), flush=True)
os._exit(0)
"""


def _rss_mb(pid: int) -> float | None:
    """Memoria residente del figlio vista da fuori (`/proc` o `ps`)."""
    statm = Path(f"/proc/{pid}/statm")
    if statm.is_file():
        try:
            pages = int(statm.read_text().split()[1])
        except (OSError, ValueError, IndexError):
            return None
        return pages * os.sysconf("SC_PAGE_SIZE") / 2**20
    ps = shutil.which("ps")
    if ps is None:
        return None
    out = subprocess.run(
        [ps, "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, check=False
    ).stdout.strip()
    return int(out) / 1024 if out.isdigit() else None


def _run_child(mode: str, source: str) -> dict[str, Any]:
    """Il caso in un processo figlio, fermato oltre `_KILL_SECONDS` o
    `_KILL_RSS_MB` (anche dal figlio stesso); ritorna `result`, `seconds`
    e `rss_mb` (picco dell'intero processo), oppure `killed`."""
    args = [sys.executable, "-c", _CHILD, mode, source, _GOOD, str(_KILL_RSS_MB)]
    with tempfile.TemporaryFile() as out:
        proc = subprocess.Popen(args, cwd=_BACKEND, stdout=out, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + _KILL_SECONDS
        killed = None
        while proc.poll() is None:
            rss = _rss_mb(proc.pid)
            if rss is not None and rss > _KILL_RSS_MB:
                killed = f"rss {rss:.0f} MB"
            elif time.monotonic() > deadline:
                killed = f"oltre {_KILL_SECONDS:g} s"
            if killed is not None:
                proc.kill()
                proc.wait()
                return {"killed": killed}
            time.sleep(0.05)
        out.seek(0)
        text = out.read().decode("utf-8", errors="replace")
    lines = [line for line in text.splitlines() if line.startswith("COST ")]
    if not lines:
        return {"killed": f"uscita {proc.returncode}: {text[-300:]}"}
    return dict(json.loads(lines[-1][5:]))


def _assert_cheap(run: dict[str, Any]) -> None:
    assert "killed" not in run, run
    assert run["seconds"] < _SECONDS, run
    assert run["rss_mb"] < _RSS_MB, run


@pytest.mark.parametrize("name", list(COST_CASES))
def test_huge_labels_and_scales_are_measured_within_the_bound(name: str) -> None:
    """V3-F1: `validate(deep=True)` e l'export di `[BAD, GOOD]` restano
    sotto 2 s e 300 MB e rendono entrambe le figure."""
    validated = _run_child("validate", COST_CASES[name])
    _assert_cheap(validated)
    assert validated["result"] == [True, ""]
    exported = _run_child("export", COST_CASES[name])
    _assert_cheap(exported)
    assert exported["result"] == ["BAD", "GOOD"]


def test_huge_node_shapes_are_boxed_without_sampling() -> None:
    """Un riquadro arrotondato da un milione di punti tipografici: prima il
    tracciato era campionato a passo 2 (milioni di punti) per il solo
    riquadro della forma."""
    run = _run_child(
        "measure",
        'digraph { node [shape=box style=rounded]; a [label="Nodo" fontsize=1000000]; }',
    )
    _assert_cheap(run)
    assert run["result"][:2] == [0, None]


def test_concurrent_edges_are_clustered_without_quadratic_cost() -> None:
    """Cento archi per un punto solo (neato, fuori dalle soglie editoriali
    come gli storici): prima 2,6 s con il lavoro contato sotto il tetto,
    perché ogni punto d'incrocio era confrontato con tutti i coincidenti."""
    run = _run_child("measure", _concurrent(100, 2.0))
    _assert_cheap(run)
    crossings, skipped, work = run["result"]
    assert (crossings, skipped) == (1, None)
    assert work < fg.MAX_MEASURE_WORK
    assert run["seconds"] < 1.5, run


_SCALED_DIAGONALS = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 500000 500000">'
    '<g transform="scale(5000)"><g class="edge"><path d="M0,0 L100,100"/></g>'
    '<g class="edge"><path d="M0,100 L100,0"/></g></g></svg>'
)
# Tre sotto-tracciati da mezza unità dentro un solo intervallo di
# campionamento: due salti, il secondo lungo un milione di unità.
_DOUBLE_JUMPS = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000000 1000000">'
    + "".join(
        f'<g class="edge"><path d="M{k},0 l0.5,0 M999000,999000 l0.5,0 M0,999{k:03d} l0.5,0"/></g>'
        for k in range(200)
    )
    + "</svg>"
)


def test_browser_measure_counts_scaled_segments_in_root_units() -> None:
    """V3-F1 nel JS: l'arco scalato è saltato per segmenti (in unità della
    radice), come in Python, invece di enumerare milioni di celle."""
    run = _run_child("js", _SCALED_DIAGONALS)
    assert "killed" not in run, run
    assert run["seconds"] < _SECONDS, run
    crossings, skipped, segments = run["result"]
    python = fg.measure_svg(_SCALED_DIAGONALS)
    assert (crossings, skipped) == (None, fg.SKIP_FIGURE_CAP)
    assert (python.skipped, python.segments) == (fg.SKIP_FIGURE_CAP, segments)


def test_browser_measure_skips_every_jump_of_an_interval() -> None:
    run = _run_child("js", _DOUBLE_JUMPS)
    assert "killed" not in run, run
    assert run["seconds"] < _SECONDS, run
    assert run["result"][:2] == [0, None]
    assert fg.measure_svg(_DOUBLE_JUMPS).crossings == 0


# ---------------------------------------------------------------------------
# Proprietà della griglia e dei suoi conteggi
# ---------------------------------------------------------------------------


def _cells_of(grid: Any) -> int:
    columns = math.floor((grid.x1 - grid.x0) / grid.size) + 1
    return columns * grid.rows


@pytest.mark.parametrize(
    "canvas",
    [
        (0.0, 0.0, 100.0, 100.0),
        (0.0, 0.0, 8000.0, 8000.0),
        (0.0, 0.0, 7968.0, 7968.0),
        (-1e6, -1e6, 1e6, 1e6),
        (0.0, 0.0, 1e9, 1.0),
        (0.0, 0.0, 1.0, 1e9),
        (0.0, 0.0, 1e300, 1e-300),
        (5.0, 5.0, 5.0, 5.0),
        (0.0, 0.0, 216000.0, 66725.0),
    ],
)
def test_the_grid_never_exceeds_max_cells(canvas: tuple[float, float, float, float]) -> None:
    grid = fg._Grid.over(canvas)
    assert grid.size >= fg.GRID_CELL
    assert _cells_of(grid) <= fg.MAX_GRID_CELLS
    # Un riquadro enorme, fuori dalla tela, occupa al più la tela intera.
    (span,) = grid.spans([(-1e308, -1e308, 1e308, 1e308)])
    assert fg._cell_count([span]) == _cells_of(grid)
    outside = grid.spans([(1e308, 1e308, 1e308, 1e308)])
    assert fg._cell_count(outside) == 1


def test_models_keep_the_16_unit_step(dot_viewboxes: list[tuple[float, ...]]) -> None:
    assert len(dot_viewboxes) == 18
    for x, y, w, h in dot_viewboxes:
        assert fg._Grid.over((x, y, x + w, y + h)).size == fg.GRID_CELL


@pytest.fixture(scope="module")
def dot_viewboxes() -> list[tuple[float, ...]]:
    renderer = frs.DotRenderer()
    out: list[tuple[float, ...]] = []
    for tpl in DOT:
        root, _elements = fg._parse_svg(renderer._render_or_raise(renderer.sanitize(tpl.code)))
        assert root is not None
        vb = fg._viewbox(root)
        assert vb is not None
        out.append(vb)
    return out


def test_counted_cells_match_the_enumeration() -> None:
    rng = random.Random(3)
    grid = fg._Grid.over((0.0, 0.0, 300.0, 200.0))
    boxes = []
    for _ in range(200):
        x, y = rng.uniform(-50, 350), rng.uniform(-50, 250)
        boxes.append((x, y, x + rng.uniform(0, 120), y + rng.uniform(0, 80)))
    spans = grid.spans(boxes)
    index = fg._Index.build(grid, spans)
    assert fg._cell_count(spans) == sum(len(v) for v in index.cells.values())
    # Ogni coppia che condivide una cella compare una volta sola.
    pairs = list(index.pairs())
    assert len(pairs) == len(set(pairs))
    expected = {
        (i, j)
        for i, a in enumerate(spans)
        for j, b in enumerate(spans)
        if i < j and max(a[0], b[0]) <= min(a[2], b[2]) and max(a[1], b[1]) <= min(a[3], b[3])
    }
    assert set(pairs) == expected


def test_piece_boxes_are_exact() -> None:
    """Il riquadro dalle radici della derivata contiene il campionamento
    fitto e non lo supera oltre l'arrotondamento."""
    rng = random.Random(11)
    for _ in range(300):
        count = rng.choice((2, 3, 4))
        piece = tuple((rng.uniform(-100, 100), rng.uniform(-100, 100)) for _ in range(count))
        box = fg._piece_box(piece)
        points = [fg._bezier(piece, i / 4000) for i in range(4001)]
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        assert box[0] <= min(xs) + 1e-9 and box[2] >= max(xs) - 1e-9
        assert box[1] <= min(ys) + 1e-9 and box[3] >= max(ys) - 1e-9
        assert box[0] >= min(xs) - 1e-3 and box[2] <= max(xs) + 1e-3
        assert box[1] >= min(ys) - 1e-3 and box[3] <= max(ys) + 1e-3


def _reference_cluster(hits: list[Any], radius: float) -> tuple[int, int]:
    """Riferimento quadratico: componenti del grafo «distanza ≤ raggio»."""
    parent = list(range(len(hits)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(hits)):
        for j in range(i):
            if math.dist(hits[i][0], hits[j][0]) <= radius:
                parent[find(i)] = find(j)
    pairs: dict[int, set[tuple[int, int]]] = defaultdict(set)
    for i, (_point, a, b) in enumerate(hits):
        pairs[find(i)].add((a, b))
    return len(pairs), sum(len(v) for v in pairs.values())


def test_cluster_plan_matches_the_quadratic_reference() -> None:
    rng = random.Random(20260917)
    for _ in range(300):
        spread = rng.choice((2.0, 5.0, 10.0, 40.0, 200.0))
        hits: list[Any] = []
        for _k in range(rng.randint(0, 150)):
            if hits and rng.random() < 0.3:
                px, py = hits[rng.randrange(len(hits))][0]
                point = (px + rng.uniform(-3.5, 3.5), py + rng.uniform(-3.5, 3.5))
            else:
                point = (rng.uniform(-spread, spread), rng.uniform(-spread, spread))
            if rng.random() < 0.1:  # distanze esatte sul raggio
                point = (float(round(point[0])), float(round(point[1])))
            a, b = rng.randrange(6), rng.randrange(6)
            hits.append((point, min(a, b), max(a, b)))
        assert fg._cluster(hits, fg.CLUSTER_RADIUS) == _reference_cluster(hits, fg.CLUSTER_RADIUS)
    # Ventimila punti coincidenti: nessun confronto punto per punto.
    same = [((100 + 1e-12 * k, 100.0), 0, 1 + k % 7) for k in range(20_000)]
    plan = fg._cluster_plan(same, fg.CLUSTER_RADIUS)
    assert plan.work < 3 * len(same)
    assert fg._cluster(same, fg.CLUSTER_RADIUS, plan) == (1, 7)


def test_non_finite_geometry_is_skipped_not_raised() -> None:
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">{}</svg>'
    inf_edge = svg.format('<g class="edge"><path d="M0,0 L1e999,5"/></g>')
    report = fg.measure_svg(inf_edge)
    assert (report.crossings, report.skipped) == (None, fg.SKIP_RANGE)
    huge_text = svg.format(
        '<g class="node"><polygon points="0,0 1e999,0 1e999,5 0,5"/>'
        '<text x="1" y="4" font-size="1e999">Testo</text></g>'
        '<g class="edge"><path d="M0,0 L10,10"/></g>'
    )
    measured = fg.measure_dot_svg(huge_text)
    assert (measured.crossings, measured.skipped, measured.defects) == (0, None, ())
    no_viewbox = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1e999 5">{}</svg>'
    edges = '<g class="edge"><path d="M0,0 L10,10"/></g><g class="edge"><path d="M0,10 L10,0"/></g>'
    assert fg.measure_svg(no_viewbox.format(edges)).crossings == 1


def test_skipped_measures_charge_the_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Il lavoro eseguito da una misura saltata (qui la griglia di un
    bipartito 16 × 16) è sottratto al residuo: la figura dopo resta senza
    incroci per il batch, dove prima avrebbe trovato il residuo intatto."""
    renderer = frs.DotRenderer()
    dense = "digraph G { " + " ".join(f"s{i} -> t{j};" for i in range(16) for j in range(16))
    dense += " }"
    hashtable = next(t.code for t in DOT if t.id == "hashTable")
    skipped = fg.measure_dot_svg(renderer._render_or_raise(dense))
    measured = fg.measure_dot_svg(renderer._render_or_raise(hashtable))
    assert skipped.skipped == fg.SKIP_WORK_CAP and 0 < skipped.spent < skipped.work
    assert measured.skipped is None and measured.spent == measured.work > 0
    frs.clear_svg_cache()
    monkeypatch.setattr(fg, "MAX_BATCH_MEASURE_WORK", skipped.spent + measured.work - 1)
    with structlog.testing.capture_logs() as logs:
        figures = renderer.render_figure_batch([dense, hashtable], asset_ids=["D", "H"])
    assert all(f is not None and f.svg for f in figures)
    reasons = [(e["asset_id"], e["reason"]) for e in logs if e["event"] == "figure_measure_skipped"]
    assert reasons == [("D", fg.SKIP_WORK_CAP), ("H", fg.SKIP_BATCH_WORK_CAP)]
    frs.clear_svg_cache()


def test_entity_values_are_read_without_raising() -> None:
    """V1-F2: il riferimento oltre il limite delle cifre resta com'è nella
    lettura del corpo (`svg_base_font_px`) e nella geometria."""
    ref = "&#" + "9" * _DIGITS + ";"
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        f'<g id="{ref}" class="node {ref}"><polygon points="0,0 40,0 40,20 0,20"/>'
        f'<text x="2" y="14" font-size="12" class="{ref}">{ref}</text></g>'
        f'<g class="edge" id="{ref}"><path d="M0,0 L10,10"/></g>'
        '<g class="edge"><path d="M0,10 L10,0"/></g></svg>'
    )
    metrics = svg_normalize.svg_base_font_px(svg)
    assert (metrics.font_px_min, metrics.text_count, metrics.source) == (12.0, 1, "parsed")
    report = fg.measure_dot_svg(svg)
    assert (report.crossings, report.skipped) == (1, None)
    for unescape in (svg_normalize._unescape, fg._unescape):
        assert unescape(ref) == ref
        assert unescape("&amp;#1; &lt;") == "&#1; <"


async def test_an_unexpected_error_costs_only_its_figure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V1-F2, confine per figura del batch DOT: prima un'eccezione di una
    figura risaliva a `render_figure_map`, che segnava fallite tutte."""
    original = frs.DotRenderer._render_figure

    def _flaky(
        self: frs.DotRenderer, content: str, *, asset_id: str, work_left: int | None
    ) -> tuple[frs.RenderedFigure | None, int]:
        if asset_id == "BAD":
            raise ValueError("lettura rotta")
        return original(self, content, asset_id=asset_id, work_left=work_left)

    monkeypatch.setattr(frs.DotRenderer, "_render_figure", _flaky)
    frs.clear_svg_cache()
    frs.available_formats.cache_clear()
    assets = [
        {"asset_id": "GOOD", "format": "dot", "content": _GOOD},
        {"asset_id": "BAD", "format": "dot", "content": "digraph { x -> y }"},
        {"asset_id": "OTHER", "format": "dot", "content": "digraph { p -> q }"},
    ]
    with structlog.testing.capture_logs() as logs:
        figures = await frs.render_figure_map(assets, language="it")
    assert sorted(figures) == ["GOOD", "OTHER"]
    failed = [(e["asset_id"], e["reason"]) for e in logs if e["event"] == "figure_render_failed"]
    assert failed == [("BAD", "ValueError: lettura rotta")]
    frs.clear_svg_cache()
    frs.available_formats.cache_clear()


def test_page_reports_work_and_its_options_carry_the_caps() -> None:
    options = mp.geometry_measure_options()
    assert options["maxWork"] == fg.MAX_BROWSER_MEASURE_WORK
    assert options["batchWork"] == fg.MAX_BATCH_BROWSER_MEASURE_WORK
    assert (options["cell"], options["maxCells"]) == (fg.GRID_CELL, fg.MAX_GRID_CELLS)
    html = mp.build_mermaid_renderer_html(version="11.17.2")
    assert "window.__measureWorkBudget -= geometry.spent || 0;" in html
    report = mp._geometry_from_page(
        {"crossings": 1, "pairs": 1, "edges": 2, "segments": 9, "defects": [],
         "skipped": None, "work": 40, "spent": 40},
        preview="p",
    )  # fmt: skip
    assert report == fg.GeometryReport(1, 1, 2, 9, work=40, spent=40)
    skipped = mp._geometry_from_page(
        {"crossings": None, "pairs": None, "edges": 2, "segments": 9, "defects": [],
         "skipped": fg.SKIP_WORK_CAP, "work": 1e21, "spent": float("nan")},
        preview="p",
    )  # fmt: skip
    assert skipped == fg.GeometryReport(
        None, None, 2, 9, skipped=fg.SKIP_WORK_CAP, work=10**21, spent=0
    )


def test_mermaid_page_shares_the_work_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """La pagina del pre-render sottrae il lavoro eseguito da ogni misura:
    con un residuo pari al lavoro della prima figura la seconda è saltata
    per il batch; con un tetto per figura di un'unità sotto, la prima."""
    from tests.test_figure_geometry import _cdn_or_fail
    from tests.test_frontend_figure_templates import MERMAID

    _cdn_or_fail()
    code = MERMAID[0].code
    (alone,) = mp._prerender_mermaid_batch_sync([code])
    assert alone is not None and alone.geometry is not None
    work = alone.geometry.work
    assert alone.geometry.skipped is None and work == alone.geometry.spent > 0
    monkeypatch.setattr(fg, "MAX_BATCH_BROWSER_MEASURE_WORK", work)
    first, second = mp._prerender_mermaid_batch_sync([code, code])
    assert first is not None and first.geometry is not None
    assert second is not None and second.geometry is not None
    assert (first.geometry.skipped, second.geometry.skipped) == (None, fg.SKIP_BATCH_WORK_CAP)
    monkeypatch.setattr(fg, "MAX_BROWSER_MEASURE_WORK", work - 1)
    (only,) = mp._prerender_mermaid_batch_sync([code])
    assert only is not None and only.geometry is not None
    assert (only.geometry.skipped, only.geometry.work) == (fg.SKIP_WORK_CAP, work)
