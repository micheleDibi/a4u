"""Soglie editoriali dei grafi (D13, D14) di `figure_compute.graph_rules`.

Test puro sul modello di `test_vegalite_rules.py`: per ogni costante un caso
alla soglia (passa) e la controprova appena sopra (fallisce), i conteggi
per tipo sui modelli degli editor (i 15 Mermaid e i 18 DOT passano, i
conteggi DOT coincidono con i nodi e gli archi dell'SVG reso), il
messaggio editoriale distinto da quello dei tetti di risorsa e l'aggancio
nei due `validate` del registro.

Oracolo che falliva prima di WP5: un flowchart da 150 archi passava
`MermaidRenderer.validate` (nessun tetto editoriale).

Correzioni del giro 1 della verifica: gli incroci misurati non rifiutano
più la figura DOT in `validate(deep=True)` (un percettrone multistrato
3-4-2 andava al fix e la lezione era rigenerata), il conteggio DOT espande
gli operandi sottografo e deduplica le coppie di `strict`, il consiglio
sugli incroci è nella sintassi del formato.

Correzione del giro 2 (V2-F2): dove Mermaid manda a capo da solo (forme e
collegamenti del flowchart, state, mindmap, timeline, relazioni e note di
class ed ER, blocchi della sequence) `MAX_LABEL_CHARS` vale per la parola;
prima un evento di timeline da 85 caratteri, reso su più righe, andava al
fix AI tre volte e la lezione era rigenerata.
"""

from __future__ import annotations

import csv
import re
import socket
from dataclasses import replace
from itertools import pairwise

import pytest
import structlog

from app.core.config import get_settings
from app.core.errors import ValidationAppError
from app.services import asset_validation_service as avs
from app.services import figure_render_service as frs
from app.services import figure_theme
from app.services import mermaid_prerender as mp
from app.services import openai_asset_fix_service as fix_service
from app.services.figure_compute import graph_rules as gr
from app.services.figure_compute.graph_rules import (
    COUNTED_MERMAID_TYPES,
    GRAPH_TOO_DENSE,
    MAX_EDGE_CROSSINGS,
    MAX_GRAPH_EDGES,
    MAX_GRAPH_LINES,
    MAX_GRAPH_NODES,
    MAX_LABEL_CHARS,
    MAX_MERMAID_SOURCE_CHARS,
    MAX_TITLE_CHARS,
    check_graph_rules,
    crossings_violation,
    format_graph_violations,
    graph_source_metrics,
)
from tests.course_builders import build_lesson_content_output
from tests.test_frontend_figure_templates import DOT, MERMAID, Template


def _chain(n_nodes: int) -> str:
    """Flowchart a catena con `n_nodes` nodi e `n_nodes - 1` archi."""
    lines = ["flowchart TD"]
    lines += [f"  N{i} --> N{i + 1}" for i in range(n_nodes - 1)]
    return "\n".join(lines)


def _complete(n_nodes: int, extra: int = 0) -> str:
    """Flowchart con tutte le coppie `i < j` (n·(n−1)/2 archi) più `extra`
    archi ripetuti."""
    pairs = [(i, j) for i in range(n_nodes) for j in range(i + 1, n_nodes)]
    pairs += pairs[:extra]
    return "flowchart LR\n" + "\n".join(f"  A{i} --> A{j}" for i, j in pairs)


def _dot_chain(n_nodes: int) -> str:
    body = " ".join(f"n{i} -> n{i + 1};" for i in range(n_nodes - 1))
    return f"digraph G {{ {body} }}"


def _measure(kind: str, source: str) -> gr.GraphSourceMetrics:
    metrics = graph_source_metrics(kind, source)
    assert metrics is not None
    return metrics


def _without_figure_review(monkeypatch: pytest.MonkeyPatch) -> None:
    """I test del percorso del worker guardano il fix: il revisore di WP6
    resta spento, anche con una chiave OpenAI nell'ambiente."""
    settings = get_settings().model_copy(update={"figure_review_enabled": False})
    monkeypatch.setattr(avs, "get_settings", lambda: settings)


def _too_dense(errors: list[str], what: str) -> list[str]:
    return [e for e in errors if e.startswith(f"{GRAPH_TOO_DENSE}: {what} ")]


def test_the_failing_oracle_is_now_rejected() -> None:
    """Prima di WP5 `validate` rispondeva `(True, "")`."""
    ok, msg = frs.REGISTRY["mermaid"].validate(_chain(151))
    assert ok is False
    assert msg.startswith(f"{GRAPH_TOO_DENSE}: ")
    assert f"archi 150 > {MAX_GRAPH_EDGES}" in msg
    assert f"nodi 151 > {MAX_GRAPH_NODES}" in msg
    assert msg.count("qui semplificare è la correzione richiesta") == 1


# ---------------------------------------------------------------------------
# Una costante, un caso, una controprova
# ---------------------------------------------------------------------------


def test_max_graph_nodes() -> None:
    assert _measure("mermaid", _chain(MAX_GRAPH_NODES)).nodes == MAX_GRAPH_NODES
    assert not _too_dense(check_graph_rules("mermaid", _chain(MAX_GRAPH_NODES)), "nodi")
    errors = check_graph_rules("mermaid", _chain(MAX_GRAPH_NODES + 1))
    assert _too_dense(errors, "nodi") == [
        f"{GRAPH_TOO_DENSE}: nodi 31 > 30 — riduci i nodi a 30 o meno accorpando quelli "
        "secondari o dividendo la figura in due"
    ]
    assert not _too_dense(check_graph_rules("dot", _dot_chain(MAX_GRAPH_NODES)), "nodi")
    assert _too_dense(check_graph_rules("dot", _dot_chain(MAX_GRAPH_NODES + 1)), "nodi")


def test_max_graph_edges() -> None:
    at_limit = _complete(10)  # 45 archi, 10 nodi
    assert _measure("mermaid", at_limit).edges == MAX_GRAPH_EDGES
    assert check_graph_rules("mermaid", at_limit) == []
    errors = check_graph_rules("mermaid", _complete(10, extra=1))
    assert [e.split(" — ")[0] for e in errors] == [f"{GRAPH_TOO_DENSE}: archi 46 > 45"]
    dot_at_limit = "digraph { " + " ".join(f"a{i % 9} -> b{i % 5};" for i in range(45)) + " }"
    assert check_graph_rules("dot", dot_at_limit) == []
    dot_over = dot_at_limit.replace(" }", " a0 -> b0; }")
    assert _too_dense(check_graph_rules("dot", dot_over), "archi")


def test_max_label_chars_counts_the_longest_line() -> None:
    fits = "x" * MAX_LABEL_CHARS
    assert check_graph_rules("mermaid", f"flowchart TD\n  A[{fits}] --> B") == []
    errors = check_graph_rules("mermaid", f"flowchart TD\n  A[{fits}y] --> B")
    assert _too_dense(errors, "caratteri dell'etichetta")
    assert "«xxxxxxxx" in errors[0] and "sposta il dettaglio nel testo" in errors[0]
    # Due righe da 60 caratteri: conta la riga, non l'etichetta intera.
    two_lines = "a" * 60 + "\\n" + "b" * 60
    assert check_graph_rules("dot", f'digraph {{ n [label="{two_lines}"]; }}') == []
    long_dot = "c" * (MAX_LABEL_CHARS + 1)
    assert _too_dense(
        check_graph_rules("dot", f'digraph {{ n [label="{long_dot}"]; }}'),
        "caratteri dell'etichetta",
    )


def test_max_title_chars_is_separate_from_the_label_threshold() -> None:
    title = "T" * MAX_TITLE_CHARS
    source = f"---\ntitle: {title}\n---\nflowchart TD\n  A --> B"
    assert check_graph_rules("mermaid", source) == []
    over = source.replace(title, title + "T")
    assert _too_dense(check_graph_rules("mermaid", over), "caratteri del titolo")
    # Il titolo del grafo DOT al primo livello non è un'etichetta.
    dot = f'digraph {{ label="{title}"; a -> b }}'
    assert check_graph_rules("dot", dot) == []
    assert _too_dense(
        check_graph_rules("dot", dot.replace(title, title + "T")), "caratteri del titolo"
    )
    # Dentro un cluster la stessa `label` è un'etichetta.
    cluster = f'digraph {{ subgraph cluster_a {{ label="{title}"; a }} }}'
    assert _too_dense(check_graph_rules("dot", cluster), "caratteri dell'etichetta")


def test_max_mermaid_source_chars_applies_to_mermaid_only() -> None:
    head = "flowchart TD\n  A --> B\n%% "
    at_limit = head + "x" * (MAX_MERMAID_SOURCE_CHARS - len(head))
    assert len(at_limit) == MAX_MERMAID_SOURCE_CHARS
    assert check_graph_rules("mermaid", at_limit) == []
    errors = check_graph_rules("mermaid", at_limit + "x")
    assert [e.split(" — ")[0] for e in errors] == [
        f"{GRAPH_TOO_DENSE}: caratteri del sorgente 3001 > 3000"
    ]
    dot = "digraph { a -> b }\n// " + "x" * MAX_MERMAID_SOURCE_CHARS
    assert check_graph_rules("dot", dot) == []  # per DOT il tetto è di risorsa


def test_max_graph_lines() -> None:
    comments = ["%% nota"] * (MAX_GRAPH_LINES - 2)
    at_limit = "\n".join(["flowchart TD", "  A --> B", *comments])
    assert _measure("mermaid", at_limit).lines == MAX_GRAPH_LINES
    assert check_graph_rules("mermaid", at_limit) == []
    # Le righe vuote non contano.
    assert check_graph_rules("mermaid", at_limit + "\n\n\n") == []
    errors = check_graph_rules("mermaid", at_limit + "\n%% una di troppo")
    assert [e.split(" — ")[0] for e in errors] == [f"{GRAPH_TOO_DENSE}: righe 121 > 120"]


def test_max_edge_crossings() -> None:
    for fmt in ("dot", "mermaid"):
        assert crossings_violation(None, fmt=fmt) is None
        assert crossings_violation(0, fmt=fmt) is None
        assert crossings_violation(MAX_EDGE_CROSSINGS, fmt=fmt) is None
    msg = crossings_violation(MAX_EDGE_CROSSINGS + 1, fmt="dot")
    assert msg is not None
    assert msg.startswith(f"{GRAPH_TOO_DENSE}: incroci fra archi 5 > 4 — ")
    assert "`rank=same`" in msg
    # Il consiglio è nella sintassi del formato: niente attributi DOT per
    # Mermaid, un consiglio generico per un formato senza voce propria.
    mermaid = crossings_violation(MAX_EDGE_CROSSINGS + 1, fmt="mermaid")
    assert mermaid is not None and mermaid.startswith(f"{GRAPH_TOO_DENSE}: incroci fra archi 5")
    assert "rank" not in mermaid and "`LR` o `TD`" in mermaid
    other = crossings_violation(MAX_EDGE_CROSSINGS + 1, fmt="altro")
    assert other is not None and "rank" not in other and "LR" not in other
    # Un modello ufficiale dell'editor (`hashTable`) ha un incrocio: la
    # soglia non può essere zero.
    assert MAX_EDGE_CROSSINGS >= 1


def test_unknown_formats_have_no_rules() -> None:
    assert check_graph_rules("vegalite", "x" * 10_000) == []
    assert check_graph_rules("function", "{}") == []
    assert graph_source_metrics("image", "/uploads/x.png") is None
    assert format_graph_violations([]) == ""


# ---------------------------------------------------------------------------
# I modelli degli editor passano; i conteggi per tipo
# ---------------------------------------------------------------------------

# (nodi, archi) attesi sui modelli Mermaid: tabella 2(d) del piano, con lo
# xychart contato sulle categorie dell'asse x (5) invece che sui punti (10).
_MERMAID_COUNTS = {
    "flowchart": (5, 5),
    "sequence": (2, 2),
    "state": (4, 4),
    "class": (2, 1),
    "er": (4, 3),
    "block": (4, 3),
    "mindmap": (7, 6),
    "timeline": (6, 0),
    "treemap": (7, 6),
    "pie": (4, 0),
    "xychart": (5, 0),
    "radar": (4, 0),
    "sankey": (6, 5),
    "gantt": (3, 2),
    "quadrant": (4, 0),
}


@pytest.mark.parametrize("tpl", MERMAID, ids=[t.id for t in MERMAID])
def test_mermaid_templates_pass_and_are_counted_by_type(tpl: Template) -> None:
    metrics = _measure("mermaid", tpl.code.strip())
    assert (metrics.nodes, metrics.edges) == _MERMAID_COUNTS[tpl.id], tpl.id
    assert metrics.label_chars <= MAX_LABEL_CHARS / 1.4
    assert check_graph_rules("mermaid", tpl.code.strip()) == [], tpl.id
    assert frs.REGISTRY["mermaid"].validate(tpl.code) == (True, "")


def test_every_allowed_mermaid_type_has_a_counting_rule() -> None:
    """I quindici tipi di D8 e gli alias storici hanno una regola; i
    modelli coprono i quindici tipi, uno per modello."""
    assert set(figure_theme.MERMAID_ALLOWED_TYPES) <= COUNTED_MERMAID_TYPES
    kinds = {_measure("mermaid", t.code.strip()).kind for t in MERMAID}
    assert len(kinds) == len(MERMAID) == len(_MERMAID_COUNTS)
    assert kinds <= COUNTED_MERMAID_TYPES


_SVG_NODE_RE = re.compile(r'<g id="node\d+" class="node"')
_SVG_EDGE_RE = re.compile(r'<g id="edge\d+" class="edge"')


@pytest.mark.parametrize("tpl", DOT, ids=[t.id for t in DOT])
def test_dot_templates_pass_and_source_counts_match_the_render(tpl: Template) -> None:
    """Nodi e archi contati sul sorgente coincidono con i gruppi `node` ed
    `edge` dell'SVG reso da `dot` (la verità del layout)."""
    renderer = frs.REGISTRY["dot"]
    assert renderer.available()
    metrics = _measure("dot", tpl.code)
    svg = renderer.render_svg(tpl.code, asset_id=tpl.id)
    assert svg is not None
    assert metrics.nodes == len(_SVG_NODE_RE.findall(svg)), tpl.id
    assert metrics.edges == len(_SVG_EDGE_RE.findall(svg)), tpl.id
    assert check_graph_rules("dot", tpl.code) == [], tpl.id
    assert renderer.validate(tpl.code, deep=True) == (True, ""), tpl.id


def test_editor_templates_keep_the_calibration_margin() -> None:
    """Regola di calibrazione: il massimo osservato sui modelli sta sotto il
    60 % di ogni soglia (con un margine così la soglia non boccia figure
    normali)."""
    measured = [graph_source_metrics("mermaid", t.code.strip()) for t in MERMAID]
    measured += [graph_source_metrics("dot", t.code) for t in DOT]
    rows = [m for m in measured if m is not None]
    assert max(m.nodes for m in rows) <= 0.6 * MAX_GRAPH_NODES
    assert max(m.edges for m in rows) <= 0.6 * MAX_GRAPH_EDGES
    assert max(m.label_chars for m in rows) <= 0.6 * MAX_LABEL_CHARS
    assert max(m.title_chars for m in rows) <= 0.6 * MAX_TITLE_CHARS
    assert max(m.lines for m in rows) <= 0.6 * MAX_GRAPH_LINES
    assert max(m.chars for m in rows[: len(MERMAID)]) <= 0.6 * MAX_MERMAID_SOURCE_CHARS


def test_flowchart_syntax_variants_are_counted() -> None:
    source = """flowchart LR
  A["Testo con (parentesi) e [quadre]"] --> B{{"Esagono"}}
  A -- testo breve --> C
  A -.-> D
  A ==> E
  A --o F
  A <--> G
  A ~~~ H
  I & J --> K & L
  subgraph S1 ["Titolo del sottografo"]
    M
  end
  N@{ shape: rect, label: "Etichetta via shape" }
  classDef foo fill:#f9f
  class A foo
  O:::foo --> P
  style A fill:#f9f,stroke:#333
  linkStyle 0 stroke:#ff3
  R-->S
  T--testo-->U
  V["Valore &lt; 0?"]; W --> X
  Y -->|"con pipe"| Z
"""
    metrics = graph_source_metrics("mermaid", source)
    assert metrics is not None
    assert metrics.nodes == 25
    # 7 archi da A, 4 dal prodotto `I & J --> K & L`, poi O, R, T, W, Y.
    assert metrics.edges == 16
    # Il testo delle forme va a capo (misurato per parola, giro 2): la riga
    # più lunga è il titolo del subgraph, che resta su una riga.
    assert metrics.longest_label == "Titolo del sottografo"
    wrapped = gr._flowchart(gr._mermaid_body(source)[0]).wrapped
    assert "Testo con (parentesi) e [quadre]" in wrapped
    assert "Titolo del sottografo" not in wrapped


def test_dot_syntax_variants_are_counted() -> None:
    source = """digraph G {
  graph [label="Titolo del grafo"];
  node [shape=record];
  a [label="{campo uno|campo due|<p> campo tre}"];
  b [label=<<b>grassetto</b><br/>seconda riga>];
  "nodo con nome lungo" -> a:p -> b;
  subgraph cluster_x { label="Cluster"; c; d }
  c -> {d e};
  a -> c [xlabel="x"]
  f g h
}"""
    metrics = graph_source_metrics("dot", source)
    assert metrics is not None
    # `c -> {d e}` vale 2 archi, la catena con la porta 2, `a -> c` 1.
    assert (metrics.nodes, metrics.edges) == (9, 5)
    # I campi del record sono etichette distinte; il titolo è a parte.
    assert metrics.longest_label == "nodo con nome lungo"
    assert metrics.title_chars == len("Titolo del grafo")


@pytest.mark.parametrize(
    ("source", "nodes", "edges", "longest"),
    [
        (
            "sequenceDiagram\n  participant A as Alice\n  actor B\n  A->>+B: Ciao\n"
            "  B-->>-A: Risposta molto lunga\n  Note over A,B: Una nota\n"
            "  loop Ogni minuto\n    A-)B: ping\n  end\n  A-xB: stop",
            2,
            4,
            "Risposta molto lunga",
        ),
        (
            'sankey-beta\n"Fonte, con virgola",Destinazione,3\nDestinazione,Fine,2',
            3,
            2,
            "Fonte, con virgola",
        ),
        (
            "gantt\n  title Piano\n  section Fase\n  Primo :a1, 2025-01-01, 3d\n"
            "  Secondo :a2, after a1, 2d\n  Terzo :after a1 a2, 1d",
            3,
            3,
            "Secondo",
        ),
        (
            "classDiagram\n  class Veicolo\n  Veicolo <|-- Auto : estende\n  Auto *-- Motore",
            3,
            2,
            "Veicolo",
        ),
    ],
    ids=["sequence", "sankey", "gantt", "class"],
)
def test_other_mermaid_types_are_counted(source: str, nodes: int, edges: int, longest: str) -> None:
    metrics = graph_source_metrics("mermaid", source)
    assert metrics is not None
    assert (metrics.nodes, metrics.edges, metrics.longest_label) == (nodes, edges, longest)


# ---------------------------------------------------------------------------
# Messaggio editoriale contro tetti di risorsa; aggancio nel registro
# ---------------------------------------------------------------------------


def test_editorial_message_is_typed_and_distinct_from_resource_caps() -> None:
    renderer = frs.REGISTRY["dot"]
    too_many = "digraph { " + " ".join(f"a{i} -> b{i};" for i in range(frs.DOT_MAX_EDGES + 1))
    ok, resource = renderer.validate(too_many + " }")
    assert ok is False
    assert resource == f"troppi archi ({frs.DOT_MAX_EDGES + 1} > {frs.DOT_MAX_EDGES})"
    assert frs.error_type_for(resource) == frs.FIGURE_INVALID
    dense = "digraph { " + " ".join(f"a{i % 9} -> b{i % 5};" for i in range(46)) + " }"
    ok, editorial = renderer.validate(dense)
    assert ok is False
    assert editorial.startswith(f"{GRAPH_TOO_DENSE}: archi 46 > 45 — ")
    assert frs.error_type_for(editorial) == GRAPH_TOO_DENSE
    assert GRAPH_TOO_DENSE not in resource


def test_mermaid_rules_run_after_the_static_gate() -> None:
    """Un tipo non ammesso resta `mermaid_type_not_allowed` anche se è
    enorme: il gate statico viene prima delle soglie."""
    huge = "journey\n" + "x" * (MAX_MERMAID_SOURCE_CHARS + 10)
    ok, msg = frs.REGISTRY["mermaid"].validate(huge)
    assert ok is False and msg.startswith(frs.MERMAID_TYPE_NOT_ALLOWED)


async def test_manual_patch_gets_a_typed_422() -> None:
    assets = [{"asset_id": "F1", "format": "mermaid", "content": _chain(40)}]
    with pytest.raises(ValidationAppError) as err:
        await frs.validate_visual_assets_or_raise(
            assets, previous=None, loc_root="visual_assets", code="lesson_content_invalid"
        )
    errors = err.value.meta["errors"]
    assert [(e["asset_id"], e["type"]) for e in errors] == [("F1", GRAPH_TOO_DENSE)]
    assert errors[0]["msg"].startswith(f"{GRAPH_TOO_DENSE}: nodi 40 > 30")


def _layers(*sizes: int) -> str:
    """Percettrone multistrato: strati completi, archi da ogni nodo di uno
    strato a ogni nodo del successivo."""
    names = [[f"l{i}_{k}" for k in range(n)] for i, n in enumerate(sizes)]
    lines = ["digraph MLP {", "  rankdir=LR;"]
    for tails, heads in pairwise(names):
        lines += [f"  {t} -> {h};" for t in tails for h in heads]
    return "\n".join([*lines, "}"])


_K33 = "graph K33 { " + " ".join(f"{s} -- {t};" for s in "abc" for t in "xyz") + " }"
_K44 = "digraph K44 { " + " ".join(f"{s} -> {t};" for s in "abcd" for t in "wxyz") + " }"


@pytest.mark.parametrize(
    "source",
    [_layers(3, 3, 2), _layers(3, 4, 2), _layers(4, 5, 3), _K33, _K44],
    ids=["mlp-3-3-2", "mlp-3-4-2", "mlp-4-5-3", "k33", "k44"],
)
def test_dot_deep_validation_reports_crossings_without_rejecting(source: str) -> None:
    """V1-F1: grafi normali sotto le soglie del sorgente ma con incroci
    inevitabili (strati completi) passano anche la validazione profonda;
    gli incroci oltre soglia sono un warning e una voce fra i difetti della
    figura in cache, che il report dell'export elenca."""
    frs.clear_svg_cache()
    renderer = frs.REGISTRY["dot"]
    assert renderer.validate(source) == (True, "")
    with structlog.testing.capture_logs() as logs:
        assert renderer.validate(source, deep=True) == (True, "")
    events = [e for e in logs if e["event"] == "figure_geometry_defects"]
    assert len(events) == 1 and events[0]["crossings"] > MAX_EDGE_CROSSINGS
    fig = renderer.render_figure(source, asset_id="X")
    assert fig is not None and fig.metrics is not None
    assert fig.metrics.crossings == events[0]["crossings"]
    assert re.match(
        rf"{GRAPH_TOO_DENSE}: incroci fra archi \d+ > {MAX_EDGE_CROSSINGS} — riordina i nodi",
        fig.metrics.defects[0],
    )


async def test_a_layered_network_never_reaches_the_asset_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V1-F1, percorso del worker: prima il 3-4-2 andava al fix AI tre
    volte e poi `AssetFixUnresolvedError` (lezione rigenerata)."""

    async def _no_fix(**kwargs: object) -> object:
        raise AssertionError(f"fix chiamato: {kwargs.get('error_message')}")

    monkeypatch.setattr(fix_service, "fix_asset", _no_fix)
    _without_figure_review(monkeypatch)
    frs.clear_svg_cache()
    output = build_lesson_content_output(
        visual_assets=[
            {"asset_id": "mlp", "format": "dot", "content": _layers(3, 4, 2), "caption": "c"}
        ]
    )
    out, _usage = await avs.validate_and_fix_content_assets(output, language_code="it")
    assert out.visual_assets[0].content == _layers(3, 4, 2)


@pytest.mark.parametrize(
    ("source", "nodes", "edges"),
    [
        ("digraph G { a -> b; a -> c; b -> d; c -> d; d -> {e f}; e -> g; f -> g; }", 7, 8),
        ("digraph C { {a b} -> {c d} -> e; subgraph s { f -> g } -> h; x -> {}; }", 9, 9),
        ("digraph S { a -> subgraph cl { b; c -> d } }", 4, 4),
        ("strict graph U { a -- b; b -- a; a -- b; b -- c; }", 3, 2),
        ("strict graph V { a -- a; a -- b; b -- a; {a b} -- {a b}; }", 2, 3),
        ("strict digraph D { a -> a; a -> a; a -> b; b -> a; a -> b [label=x]; }", 2, 3),
    ],
    ids=["gruppo", "catena", "sottografo-con-archi", "strict", "strict-gruppi", "digraph"],
)
def test_dot_edge_count_expands_groups_like_graphviz(source: str, nodes: int, edges: int) -> None:
    """V1-F5: `a -> {b c}` vale 2 archi (prima 1) e in un grafo `strict`
    una coppia ripetuta vale 1 (prima tante quante le ripetizioni); i
    conteggi coincidono con i gruppi `edge` dell'SVG reso."""
    metrics = _measure("dot", source)
    assert (metrics.nodes, metrics.edges) == (nodes, edges)
    svg = frs.REGISTRY["dot"].render_svg(source, asset_id="G")
    assert svg is not None
    assert len(_SVG_NODE_RE.findall(svg)) == nodes
    assert len(_SVG_EDGE_RE.findall(svg)) == edges


def test_a_group_hub_no_longer_slips_past_the_edge_threshold() -> None:
    """Trenta nodi (al limite) e 225 archi scritti con un solo operatore."""
    left = " ".join(f"a{i}" for i in range(15))
    right = " ".join(f"b{i}" for i in range(15))
    source = f"digraph H {{ {{{left}}} -> {{{right}}} }}"
    metrics = _measure("dot", source)
    assert (metrics.nodes, metrics.edges) == (30, 225)
    ok, msg = frs.REGISTRY["dot"].validate(source)
    assert ok is False
    assert msg.startswith(f"{GRAPH_TOO_DENSE}: archi 225 > {MAX_GRAPH_EDGES} — ")
    # Oltre il tetto di enumerazione di `strict` il conteggio resta il prodotto.
    big = " ".join(f"n{i}" for i in range(150))
    strict = f"strict digraph B {{ {{{big}}} -> {{{big}}} }}"
    assert _measure("dot", strict).edges == 150 * 150


def test_messages_fit_the_payload_and_fix_caps() -> None:
    worst = format_graph_violations(check_graph_rules("mermaid", _chain(300)))
    assert worst.count(GRAPH_TOO_DENSE) == 4
    assert len(worst) < 1_600  # `_ERROR_CAP` del fix AI
    assert gr.MAX_GRAPH_NODES < frs.DOT_MAX_EDGES


@pytest.mark.parametrize(
    ("kind", "source"),
    [
        ("dot", "digraph {" + "{" * 6000 + "}" * 6000 + "}"),
        ("dot", "digraph {" + "subgraph {" * 1500 + "}" * 1500 + "}"),
        ("dot", "digraph {" + "a -> {" * 2000 + "}" * 2000 + "}"),
        ("dot", 'digraph { a [label="aperta ] ; b -> c'),
        ("dot", "digraph { a [label=" + "<" * 6000 + "] }"),
        ("mermaid", "sequenceDiagram\n" + "A->" * 4000),
        ("mermaid", "flowchart TD\n" + "A[" * 6000),
        ("mermaid", "classDiagram\n" + "A<|--" * 2400),
        ("mermaid", "quadrantChart\n" + "a:::" * 3000),
        ("mermaid", "sankey-beta\n" + '"' * 12_000),
        ("mermaid", ""),
    ],
    ids=[
        "dot-graffe",
        "dot-sottografi",
        "dot-archi-annidati",
        "dot-stringa-aperta",
        "dot-html-aperto",
        "sequence-frecce",
        "flowchart-parentesi",
        "class-relazioni",
        "quadrant-classi",
        "sankey-virgolette",
        "vuoto",
    ],
)
def test_pathological_sources_never_raise(kind: str, source: str) -> None:
    """Il gate gira nel PATCH e nel worker: un sorgente patologico (fino al
    tetto dello schema) produce misure, mai un'eccezione né un tempo lungo
    (nessuna ricorsione oltre `_MAX_DOT_DEPTH`)."""
    metrics = graph_source_metrics(kind, source)
    assert metrics is not None
    assert isinstance(check_graph_rules(kind, source, metrics=metrics), list)


def test_comments_inside_dot_strings_are_text() -> None:
    source = (
        'digraph { a [label="uno // due /* tre */ # quattro"]; b -> c // commento\n'
        " d # altro\n /* blocco\n e */ }"
    )
    metrics = _measure("dot", source)
    assert (metrics.nodes, metrics.edges) == (4, 1)
    assert metrics.longest_label == "uno // due /* tre */ # quattro"


# ---------------------------------------------------------------------------
# Etichette che Mermaid manda a capo da solo (giro 2, V2-F2)
# ---------------------------------------------------------------------------

# 84 caratteri, 10 parole: la più lunga è «addestrabile» (12).
_LONG = "Rosenblatt presenta il percettrone primo modello addestrabile di neurone artificiale"
_WORD = "x" * (MAX_LABEL_CHARS + 1)

_WRAPPED_CONTEXTS: dict[str, str] = {
    "flow_node": "flowchart TD\n  A[{L}] --> B[x]",
    "flow_node_quoted": 'flowchart TD\n  A["{L}"] --> B[x]',
    "flow_node_markdown": 'flowchart TD\n  A["`{L}`"] --> B[x]',
    "flow_node_round": "flowchart TD\n  A({L}) --> B[x]",
    "flow_node_shape": 'flowchart TD\n  A@{{ shape: rect, label: "{L}" }}\n  A --> B',
    "flow_edge_pipe": "flowchart LR\n  A -->|{L}| B",
    "flow_edge_text": "flowchart TD\n  A -- {L} --> B",
    "state_description": "stateDiagram-v2\n  S1 : {L}\n  [*] --> S1",
    "state_alias": 'stateDiagram-v2\n  state "{L}" as S1\n  [*] --> S1',
    "state_transition": "stateDiagram-v2\n  S1 --> S2 : {L}",
    "state_note": "stateDiagram-v2\n  S1 --> S2\n  note right of S1 : {L}",
    "state_note_block": "stateDiagram-v2\n  S1 --> S2\n  note right of S1\n    {L}\n  end note",
    "mindmap_node": "mindmap\n  root((Radice))\n    {L}",
    "timeline_event": "timeline\n  title T\n  1958 : {L}",
    "timeline_period": "timeline\n  title T\n  {L} : evento",
    "timeline_section": "timeline\n  title T\n  section {L}\n  1958 : evento",
    "class_relation": "classDiagram\n  A --> B : {L}",
    "class_note": 'classDiagram\n  class A\n  note for A "{L}"',
    "er_relation": 'erDiagram\n  A ||--o{{ B : "{L}"',
    "sequence_loop": "sequenceDiagram\n  participant C\n  participant S\n  loop {L}\n"
    "    C->>S: x\n  end",
    "sequence_alt": "sequenceDiagram\n  participant C\n  participant S\n  alt {L}\n"
    "    C->>S: x\n  else {L}\n    S->>C: y\n  end",
}
_SINGLE_LINE_CONTEXTS: dict[str, str] = {
    "flow_subgraph_title": "flowchart TD\n  subgraph s1 [{L}]\n    A --> B\n  end",
    "block_label": 'block-beta\n  columns 1\n  a["{L}"]',
    "sequence_message": "sequenceDiagram\n  participant C\n  participant S\n  C->>S: {L}",
    "sequence_note": "sequenceDiagram\n  participant C\n  participant S\n  Note over C,S: {L}",
    "sequence_alias": "sequenceDiagram\n  participant C as {L}\n  participant S\n  C->>S: x",
    "sequence_box": "sequenceDiagram\n  box Aqua {L}\n  participant C\n  end\n"
    "  participant S\n  C->>S: x",
    "class_member": "classDiagram\n  class A {{\n    +String {L}\n  }}",
    "class_label": 'classDiagram\n  class A["{L}"]',
    "er_attribute_comment": 'erDiagram\n  A {{\n    string nome "{L}"\n  }}',
    "er_alias": 'erDiagram\n  A["{L}"] {{\n    string nome\n  }}',
    "treemap": 'treemap-beta\n"{L}"\n  "x": 10\n  "y": 20',
    "gantt_task": "gantt\n  dateFormat YYYY-MM-DD\n  section S\n  {L} :a1, 2024-01-01, 3d",
    "pie_slice": 'pie\n  "{L}" : 10\n  "b" : 20',
    "quadrant_point": "quadrantChart\n  x-axis Basso --> Alto\n  y-axis Basso --> Alto\n"
    "  {L}: [0.3, 0.6]",
    "radar_axis": 'radar-beta\n  axis a["{L}"], b["x"], c["y"]\n  curve c1{{1,2,3}}',
    "xychart_category": 'xychart-beta\n  x-axis ["{L}", b]\n  y-axis 0 --> 10\n  bar [3, 5]',
    "sankey_node": "sankey-beta\n{L},B,10\nB,C,5",
}


@pytest.mark.parametrize("name", list(_WRAPPED_CONTEXTS))
def test_wrapped_labels_are_measured_by_word(name: str) -> None:
    source = _WRAPPED_CONTEXTS[name].format(L=_LONG)
    metrics = _measure("mermaid", source)
    assert (metrics.label_chars, metrics.longest_label) == (12, "addestrabile"), name
    assert check_graph_rules("mermaid", source) == [], name
    assert frs.REGISTRY["mermaid"].validate(source) == (True, ""), name
    # Una parola oltre soglia non va a capo: resta un rifiuto.
    long_word = _WRAPPED_CONTEXTS[name].format(L=f"Etichetta {_WORD} finale")
    assert _too_dense(check_graph_rules("mermaid", long_word), "caratteri dell'etichetta"), name


@pytest.mark.parametrize("name", list(_SINGLE_LINE_CONTEXTS))
def test_single_line_labels_keep_the_line_threshold(name: str) -> None:
    source = _SINGLE_LINE_CONTEXTS[name].format(L=_LONG)
    errors = check_graph_rules("mermaid", source)
    assert _too_dense(errors, "caratteri dell'etichetta"), (name, errors)
    assert _measure("mermaid", source).label_chars >= len(_LONG), name


def _cdn_or_fail() -> None:
    try:
        socket.create_connection(("cdn.jsdelivr.net", 443), timeout=3).close()
    except OSError:  # pragma: no cover - verifica locale, non gate CI
        pytest.skip("cdn.jsdelivr.net non raggiungibile")


# Vero se una riga resa contiene sia la prima sia l'ultima parola di `_LONG`:
# righe = i `<tspan>` figli diretti di un `<text>` (Mermaid ne crea uno per
# riga quando va a capo), altrimenti il `<text>` intero.
_SINGLE_ROW_JS = """(svg) => {
  const host = document.createElement("div");
  host.innerHTML = svg;
  document.body.appendChild(host);
  try {
    const rows = [];
    for (const t of host.querySelectorAll("svg text")) {
      const outer = [...t.children].filter((c) => c.tagName.toLowerCase() === "tspan");
      for (const r of (outer.length ? outer : [t])) rows.push(r.textContent);
    }
    const found = rows.some((r) => r.includes("Rosenblatt"));
    return found ? rows.some((r) => r.includes("Rosenblatt") && r.includes("artificiale"))
                 : null;
  } finally {
    host.remove();
  }
}"""


def test_mermaid_really_wraps_only_the_exempted_contexts() -> None:
    """La taratura della regola, in Chromium con la versione pinnata: nei
    contesti esentati l'etichetta di 84 caratteri è resa su più righe, negli
    altri su una riga sola."""
    _cdn_or_fail()
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    contexts = {**_WRAPPED_CONTEXTS, **_SINGLE_LINE_CONTEXTS}
    names = list(contexts)
    renderer = frs.REGISTRY["mermaid"]
    codes = [renderer.sanitize(contexts[n].format(L=_LONG)) for n in names]
    rendered = mp._prerender_mermaid_batch_sync(codes)
    assert [n for n, r in zip(names, rendered, strict=True) if r is None] == []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content("<html><body></body></html>")
            single = {
                n: page.evaluate(_SINGLE_ROW_JS, r.svg)
                for n, r in zip(names, rendered, strict=True)
                if r is not None
            }
        finally:
            browser.close()
    assert {n: single[n] for n in _WRAPPED_CONTEXTS} == dict.fromkeys(_WRAPPED_CONTEXTS, False)
    assert {n: single[n] for n in _SINGLE_LINE_CONTEXTS} == dict.fromkeys(
        _SINGLE_LINE_CONTEXTS, True
    )


async def test_a_descriptive_timeline_never_reaches_the_asset_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V2-F2, percorso del worker: prima «graph_too_dense: caratteri
    dell'etichetta 85 > 64», tre chiamate al fix e `AssetFixUnresolvedError`."""

    async def _no_fix(**kwargs: object) -> object:
        raise AssertionError(f"fix chiamato: {kwargs.get('error_message')}")

    monkeypatch.setattr(fix_service, "fix_asset", _no_fix)
    _without_figure_review(monkeypatch)
    timeline = (
        "timeline\n    title Storia delle reti neurali\n"
        "    1958 : Rosenblatt presenta il percettrone, primo modello addestrabile di "
        "neurone artificiale\n"
        "    1986 : Rumelhart, Hinton e Williams diffondono la retropropagazione dell'errore\n"
    )
    output = build_lesson_content_output(
        visual_assets=[{"asset_id": "t", "format": "mermaid", "content": timeline, "caption": "c"}]
    )
    out, _usage = await avs.validate_and_fix_content_assets(output, language_code="it")
    assert out.visual_assets[0].content == timeline


# ---------------------------------------------------------------------------
# Coda di WP5, giro 1 (V1-F1): `\r` isolato nel CSV del sankey
# ---------------------------------------------------------------------------

_SANKEY_CR = "sankey-beta\nA,B,10\rB,C,5"


@pytest.mark.parametrize(
    "source",
    [
        _SANKEY_CR,
        "sankey-beta\nSorgente\rA,Destinazione,10",
        "sankey-beta\rA,B,10\rB,C,5",
        'sankey-beta\n"A\r",B,10\r,\r\r',
        "sankey-beta\n" + "A,B,1\r" * 2_000,
    ],
    ids=["riga", "dentro-il-campo", "intestazione", "virgolette", "al-tetto"],
)
def test_a_lone_carriage_return_never_raises(source: str) -> None:
    """Prima `csv.Error: new-line character seen in unquoted field` da
    `_sankey` (500 nel PATCH, lezione rigenerata dal worker)."""
    metrics = _measure("mermaid", source)
    assert metrics.kind == "sankey-beta"
    ok, _msg = frs.REGISTRY["mermaid"].validate(source)
    assert isinstance(ok, bool)


def test_lines_split_on_carriage_return_like_mermaid() -> None:
    """`cleanupText` di Mermaid 11 porta `\\r\\n?` a `\\n` prima del parser:
    un `\\r` isolato chiude la riga per ogni tipo, un CRLF vale un a capo."""
    for source in (_SANKEY_CR, "flowchart LR\nA --> B\rB --> C\r\nC --> D"):
        with_lf = source.replace("\r\n", "\n").replace("\r", "\n")
        # Tutto uguale tranne i caratteri, che contano il sorgente com'è.
        assert replace(_measure("mermaid", source), chars=0) == replace(
            _measure("mermaid", with_lf), chars=0
        )
    sankey = _measure("mermaid", _SANKEY_CR)
    assert (sankey.nodes, sankey.edges) == (3, 2)
    flow = _measure("mermaid", "flowchart LR\nA --> B\rB --> C")
    assert (flow.nodes, flow.edges) == (3, 2)
    assert _measure("mermaid", "sankey-beta\r\nA,B,10\r\nB,C,5\r\n").edges == 2


def test_sankey_reader_errors_are_contained() -> None:
    """Con un `csv.field_size_limit` abbassato altrove nel processo la misura
    non solleva: le righe lette prima dell'errore restano contate."""
    previous = csv.field_size_limit(8)
    try:
        acc = gr._sankey(["sankey-beta", "A,B,1", '"' + "x" * 40 + '",C,1', "D,E,1"])
    finally:
        csv.field_size_limit(previous)
    assert (list(acc.nodes), acc.edges) == (["A", "B"], 1)
    assert csv.field_size_limit() > gr.MAX_MERMAID_MEASURED_CHARS


async def test_patch_gate_answers_422_or_accepts_a_carriage_return() -> None:
    """Percorso del PATCH: prima `_csv.Error` usciva da
    `validate_visual_assets_or_raise` (500 del gestore generico)."""
    await frs.validate_visual_assets_or_raise(
        [{"asset_id": "S", "format": "mermaid", "content": _SANKEY_CR}],
        previous=[],
        loc_root="visual_assets",
        code="lesson_content_invalid",
    )
    dense = "sankey-beta\n" + "".join(f"A{i},B{i},1\r" for i in range(50))
    with pytest.raises(ValidationAppError) as err:
        await frs.validate_visual_assets_or_raise(
            [{"asset_id": "D", "format": "mermaid", "content": dense}],
            previous=[],
            loc_root="visual_assets",
            code="lesson_content_invalid",
        )
    errors = err.value.meta["errors"]
    assert [(e["asset_id"], e["type"]) for e in errors] == [("D", GRAPH_TOO_DENSE)]
    assert errors[0]["msg"].startswith(f"{GRAPH_TOO_DENSE}: nodi 100 > 30")


async def test_a_sankey_with_a_carriage_return_passes_the_worker_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Percorso del worker: prima l'eccezione usciva da `_validate_slots` e
    la lezione era segnata fallita e rimessa in coda."""

    async def _no_fix(**kwargs: object) -> object:
        raise AssertionError(f"fix chiamato: {kwargs.get('error_message')}")

    monkeypatch.setattr(fix_service, "fix_asset", _no_fix)
    _without_figure_review(monkeypatch)
    asset = {"asset_id": "s", "format": "mermaid", "content": _SANKEY_CR, "caption": "c"}
    output = build_lesson_content_output(visual_assets=[asset])
    out, _usage = await avs.validate_and_fix_content_assets(output, language_code="it")
    assert out.visual_assets[0].content == _SANKEY_CR
