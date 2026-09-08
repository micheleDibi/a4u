"""Registro dei renderer (`figure_render_service`, Q1) e `run_isolated`.

Checklist del brief (§7, «Delta» voce 4):
- Vega-Lite: spec valida; `data.url` anche in `layer` annidato, oltre 4.000
  caratteri, `mark image`, chiavi duplicate, schema → rifiutate; SVG senza
  `foreignObject`; INIEZIONE DEL TEMA (`font-family` ⊇ «Noto Sans», un
  esadecimale della `PALETTE` nell'SVG);
- DOT: valido / `image=` rifiutato; SVG con «Noto Sans» e i colori del tema
  (`DOT_DEFAULTS` usa i neutri `COLOR_AXIS`/`COLOR_INK`, non la palette);
  `dot` mancante (monkeypatch di `shutil.which`) → `(False, "dot_unavailable")`
  e mai pass-through;
- Mermaid: gate statico D8 (tipi, `%%{init`, HTML nelle label);
- `run_isolated` con `tests/helpers/slow_target.py` e `timeout=1` →
  `FigureTimeoutError` entro la scadenza, nessun figlio vivo; risultato da
  2 MB senza stallo; eccezione del figlio → `FigureComputeError`;
- `render_svg_map`: un batch per formato, cache LRU, cache negativa, timeout
  senza eccezioni;
- `validate_visual_assets_or_raise`: solo gli asset con `(format, content)`
  cambiati, payload 422 con `loc/asset_id/format/msg/type`.

I casi che eseguono vl-convert o `dot` saltano con motivo esplicito se la
dipendenza manca; tutto il resto è offline.
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import re
import shutil
import time
from collections.abc import Iterator, Mapping
from typing import Any

import pytest

from app.core import config
from app.core.errors import ValidationAppError
from app.services import figure_render_service as frs
from app.services import figure_theme as theme
from app.services.figure_compute.isolated import (
    FigureComputeError,
    FigureTimeoutError,
    run_isolated,
)

_VL_AVAILABLE = frs.REGISTRY["vegalite"].available()
_DOT_AVAILABLE = shutil.which("dot") is not None
needs_vl = pytest.mark.skipif(not _VL_AVAILABLE, reason="vl_convert/altair/jsonschema assenti")
needs_dot = pytest.mark.skipif(not _DOT_AVAILABLE, reason="binario `dot` (graphviz) assente")

_BAR = {
    "data": {"values": [{"k": "a", "v": 3}, {"k": "b", "v": 5}]},
    "mark": "bar",
    "encoding": {
        "x": {"field": "k", "type": "nominal", "axis": {"title": "categoria"}},
        "y": {
            "field": "v",
            "type": "quantitative",
            "scale": {"domain": [0, 6]},
            "axis": {"title": "valore"},
        },
    },
    "title": "Esempio",
}
_BAR_JSON = json.dumps(_BAR)
_DOT = 'digraph G {\n  a -> b;\n  b -> c [label="vedi url(x)"];\n}'


@pytest.fixture(autouse=True)
def _clean_caches() -> Iterator[None]:
    frs.clear_svg_cache()
    frs.available_formats.cache_clear()
    yield
    frs.clear_svg_cache()
    frs.available_formats.cache_clear()


def _patch_settings(monkeypatch: pytest.MonkeyPatch, **updates: Any) -> None:
    settings = config.get_settings().model_copy(update=updates)
    monkeypatch.setattr(frs, "get_settings", lambda: settings)
    frs.available_formats.cache_clear()


class _FakeRenderer:
    """Renderer sincrono con esiti programmabili (per l'orchestratore)."""

    def __init__(self, fmt: str, *, ok: bool = True, delay: float = 0.0) -> None:
        self.fmt = fmt
        self.ok = ok
        self.delay = delay
        self.batches: list[list[str]] = []
        self.validated: list[str] = []

    def available(self) -> bool:
        return True

    def sanitize(self, content: str) -> str:
        return content.strip()

    def validate(self, content: str, *, deep: bool = False) -> tuple[bool, str]:
        self.validated.append(content)
        return (True, "") if self.ok else (False, f"{self.fmt}: non valido")

    def render_svg(self, content: str, *, asset_id: str = "") -> str | None:
        return self.render_svg_batch([content], asset_ids=[asset_id])[0]

    def render_svg_batch(self, contents: list[str], *, asset_ids: list[str]) -> list[str | None]:
        self.batches.append(list(contents))
        if self.delay:
            time.sleep(self.delay)
        return [f"<svg>{self.fmt}:{c}</svg>" if self.ok else None for c in contents]

    def extract_translatable(self, content: str) -> dict[str, str]:
        return {}

    def apply_translations(self, content: str, tr: Mapping[str, str]) -> str:
        return content


# ---------------------------------------------------------------------------
# Registro e formati disponibili
# ---------------------------------------------------------------------------


def test_registry_and_renderable_formats():
    assert frs.RENDERABLE_FORMATS == ("mermaid", "vegalite", "dot", "function")
    assert set(frs.REGISTRY) == {"mermaid", "vegalite", "dot", "function"}
    for fmt, renderer in frs.REGISTRY.items():
        assert renderer.fmt == fmt
    assert frs.available_formats()[0] == "mermaid"
    # `function` (WP7) richiede numpy, matplotlib e sympy.
    function_available = frs.REGISTRY["function"].available()
    assert ("function" in frs.available_formats()) is function_available


def test_kill_switch_removes_a_format(monkeypatch: pytest.MonkeyPatch):
    _patch_settings(
        monkeypatch,
        figure_vegalite_enabled=False,
        figure_dot_enabled=False,
        figure_function_enabled=False,
    )
    assert frs.available_formats() == ("mermaid",)


def test_register_renderer_adds_a_format(monkeypatch: pytest.MonkeyPatch):
    fake = _FakeRenderer("function")
    monkeypatch.setitem(frs.REGISTRY, "function", fake)
    frs.available_formats.cache_clear()
    assert "function" in frs.available_formats()
    frs.register_renderer(_FakeRenderer("function"))
    assert frs.REGISTRY["function"] is not fake


def test_error_type_classification():
    assert frs.error_type_for("mermaid_type_not_allowed: x") == "mermaid_type_not_allowed"
    assert frs.error_type_for("vegalite_use_function_format: x") == frs.VEGALITE_USE_FUNCTION_FORMAT
    assert frs.error_type_for("function_spec_invalid: x") == "function_spec_invalid"
    assert frs.error_type_for("JSON non valido") == "figure_invalid"


# ---------------------------------------------------------------------------
# Mermaid — gate statico D8
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", list(theme.MERMAID_D8_SAMPLES.values()))
def test_mermaid_static_gate_accepts_the_d8_samples(code: str):
    assert frs.REGISTRY["mermaid"].validate(code) == (True, "")


@pytest.mark.parametrize(
    "code",
    [
        "%% commento\nflowchart LR\n  A --> B",
        "---\ntitle: Schema\n---\nflowchart TD\n  A --> B",
        "graph TD\n  A --> B",
        "stateDiagram\n  [*] --> A",
        "sequenceDiagram\n  A->>B: ciao\n  B-->>A: <<risposta>>",
        "sequenceDiagram\n  A<<->>B: bidirezionale",
        "```mermaid\nflowchart LR\n  A --> all\n```",
        "classDiagram-v2\n  A <|-- B",  # alias di Mermaid 11 per classDiagram
        "classDiagram\n  class A {\n    <<interface>>\n  }\n  A <|-- B",
        "graph TD;\n  A-->B",
        "flowchart LR\n  A[x <b] --> B",  # `<b` non chiuso: non è un tag
        "flowchart LR\n  A[a < b] --> B",  # `<` isolato
        "erDiagram\n  A ||--o{ B : has",
        "%% <b>commento</b>\nflowchart LR\n  A --> B",  # tag in un commento: non renderizzato
        "---\ntitle: <b>x</b>\n---\nflowchart LR\n  A --> B",  # tag nel frontmatter
        "---\ntitle: Schema\n# commento\ndisplayMode: compact\n---\ngantt\n  title x",
        '---\n\ntitle: "Corso: parte 1"\n---\nflowchart LR\n  A --> B',
    ],
)
def test_mermaid_static_gate_accepts_comments_frontmatter_aliases_and_fences(code: str):
    assert frs.REGISTRY["mermaid"].validate(code) == (True, "")


@pytest.mark.parametrize(
    ("code", "needle"),
    [
        ("journey\n  title x", "journey"),
        ("gitGraph\n  commit", "gitGraph"),
        ("requirementDiagram\n  x", "requirementDiagram"),
        ("flowchartXYZ LR\n  A --> B", "flowchartXYZ"),  # confronto esatto sul token
        ("flowchart-elk LR\n  A --> B", "flowchart-elk"),  # layout esterno, non nel CDN
        ("%%{init: {'theme': 'dark'}}%%\nflowchart LR\n  A --> B", "%%{init"),
        ("%%  { init: {'theme': 'dark'} }%%\nflowchart LR\n  A --> B", "%%{init"),
        # `initialize` è l'alias di `init` in Mermaid 11 (`detectInit`).
        ("%%{initialize: {'theme': 'dark', 'htmlLabels': true}}%%\nflowchart LR\n  A", "%%{init"),
        ("%%{INITIALIZE: {'theme': 'dark'}}%%\nflowchart LR\n  A --> B", "%%{init"),
        ("---\nconfig:\n  theme: forest\n---\nflowchart LR\n  A --> B", "config:"),
        # Forme YAML equivalenti della chiave `config` (js-yaml le legge tutte).
        ('---\n"config":\n  theme: forest\n---\nflowchart LR\n  A --> B', '"config":'),
        ("---\n'config': {theme: forest}\n---\nflowchart LR\n  A --> B", "'config'"),
        ("---\n{config: {theme: forest}}\n---\nflowchart LR\n  A --> B", "{config:"),
        ('---\n"con\\x66ig": {theme: forest}\n---\nflowchart LR\n  A --> B', "con\\x66ig"),
        ("---\ntitle: x\nconfig: {theme: forest}\n---\nflowchart LR\n  A --> B", "config:"),
        # Chiave che Mermaid ignora: rifiutata dalla lista chiusa, con il motivo.
        ("---\ntheme: forest\n---\nflowchart LR\n  A --> B", "solo `title:` e `displayMode:`"),
        ("---\n- config\n---\nflowchart LR\n  A --> B", "- config"),
        ("flowchart LR\n  A[<b>x</b>] --> B", "HTML"),
        # `<brx>` è un nome di elemento sconosciuto: il parser lo toglie in
        # silenzio e la label perde il tag senza andare a capo.
        ("flowchart LR\n  A[riga<brx>due] --> B", "HTML"),
        ('flowchart LR\n  A@{ img: "http://interno/x.png", label: "n" }\n  A --> B', "img:"),
        ('flowchart LR\n  A@{ img: "file:///etc/hosts" }\n  A --> B', "img:"),
        ('flowchart LR\n  A@{\n    shape: rect,\n    label: "https://x/y"\n  }', "http"),
        # SEC-1: una `}` DENTRO una stringa quotata non chiude la shape per
        # il lexer di Mermaid (stato `shapeDataStr`). Con la vecchia regex
        # `@\{[^}]*\}` il gate si fermava lì e non vedeva `img:`.
        (
            'flowchart LR\n  A@{ label: "}", img: "http://interno/x.png", w: 60 }\n  A --> B',
            "img:",
        ),
        ('flowchart LR\n  A@{ label: "}", x: "https://interno/y" }\n  A --> B', "http"),
        ('flowchart LR\n  A@{ label: "}}}", img: "/etc/hosts" }\n  A --> B', "img:"),
        # `\\` non è un escape nello stato `shapeDataStr`: la stringa
        # finisce comunque alla virgoletta successiva.
        ('flowchart LR\n  A@{ label: "a\\", img: "http://interno/x.png" }', "img:"),
        # `@{` senza chiusura: il lexer arriva a EOF dentro la shape.
        ('flowchart LR\n  A@{ label: "}", img: "http://interno/x.png"', "img:"),
        # REG-1 (giro 3): un `br` con un ATTRIBUTO non è un a capo — il
        # parser HTML lo serializza in chiaro nella label (`<br x="">`),
        # misurato sul pre-render in `test_mermaid_no_foreignobject.py`.
        ("flowchart LR\n  A[riga<br x>due] --> B", "HTML"),
        ('flowchart LR\n  A[riga<br class="x">due] --> B', "HTML"),
        ("flowchart LR\n  A[<script>alert(1)</script>] --> B", "<script>"),
        ("flowchart LR\n  A[<table><tr><td>x</td></tr></table>] --> B", "<table>"),
        ("flowchart LR\n  A[<h1>x</h1>] --> B", "<h1>"),
        ("flowchart LR\n  A[<svg onload=x>] --> B", "<svg"),
        ("", "vuoto"),
        ("   \n%% solo commenti\n", "?"),
        ("---\ntitle: x\nflowchart LR\n  A --> B", "?"),  # frontmatter mai chiuso
        # Chiusura con rientro diverso dall'apertura: per la `frontMatterRegex`
        # di Mermaid non chiude il frontmatter (tutto è frontmatter, corpo vuoto).
        ("---\ntitle: x\n  ---\nflowchart LR\n  A --> B", "?"),
    ],
)
def test_mermaid_static_gate_rejects(code: str, needle: str):
    ok, err = frs.REGISTRY["mermaid"].validate(code)
    assert ok is False and needle in err, err
    if code.strip():
        assert err.startswith("mermaid_type_not_allowed")


@pytest.mark.parametrize(
    "code",
    [
        "flowchart LR\n  A[Riga 1<br/>Riga 2] --> B",
        "flowchart LR\n  A[Riga 1<br>Riga 2] --> B",
        "flowchart LR\n  A[Riga 1<BR />Riga 2] --> B",
        "sequenceDiagram\n  A->>B: prima<br/>seconda",
        "flowchart LR\n  A[Riga 1<br >Riga 2] --> B",
        'flowchart LR\n  A@{ shape: rect, label: "Etichetta" } --> B',
        # Una `}` dentro la stringa non deve far rifiutare una shape sana.
        'flowchart LR\n  A@{ shape: rect, label: "insieme {a}" } --> B',
    ],
)
def test_mermaid_gate_accepts_br_line_breaks_and_plain_shapes(code: str):
    """`<br>` non è HTML reso in chiaro ma la sintassi di a capo di Mermaid,
    resa in `tspan.row` da 10.9.4 come da 11.17.2: rifiutarla bocciava
    contenuti già in DB al PATCH e mandava al fix AI diagrammi validi
    (REG-1)."""
    assert frs.REGISTRY["mermaid"].validate(code) == (True, "")


# Insieme MISURATO delle forme che Mermaid 11.17.2 rende come a capo: ognuna
# dà un SVG identico a quello di `<br>` a meno dell'id `mmd-N`
# (`test_mermaid_br_forms_render_as_a_line_break`, che le rende davvero).
# `lineBreakRegex = /<br\s*\/?>/gi` ne copre solo le prime dieci, perché la
# label passa dal parser HTML prima di quella regex: le forme con spazi dopo
# la barra e la forma di chiusura arrivano a `lineBreakRegex` già
# normalizzate. Il gate deve seguire il renderer, non la regex (REG-1,
# giro 3: il giro 2 rifiutava le ultime nove con un 422 su contenuto sano).
MERMAID_BR_LINE_BREAKS = (
    "<br>",
    "<BR>",
    "<Br>",
    "<br >",
    "<br  >",
    "<br\t>",
    "<br/>",
    "<br />",
    "<br  />",
    "<br\t/>",
    "<br/ >",
    "<br / >",
    "<br  /  >",
    "<br/\t>",
    "<br\t/\t>",
    "<br//>",
    "</br>",
    "</br >",
    "</BR>",
    "</br/>",
)


@pytest.mark.parametrize("token", MERMAID_BR_LINE_BREAKS)
def test_mermaid_gate_accepts_every_measured_line_break(token: str):
    """REG-1 (giro 3): nessuna delle venti forme che il renderer tratta da a
    capo deve ricevere un 422 dal gate."""
    code = f"flowchart LR\n  A[Riga 1{token}Riga 2] --> B"
    assert frs.REGISTRY["mermaid"].validate(code) == (True, "")


def test_mermaid_shape_blocks_close_like_the_lexer():
    """SEC-1: il blocco `@{ … }` si chiude sulla `}` FUORI dalle virgolette,
    come lo stato `shapeDataStr` del lexer di Mermaid 11.17.2."""
    blocks = frs._mermaid_shape_blocks
    assert list(blocks("A@{ shape: rect }")) == ["@{ shape: rect }"]
    assert list(blocks('A@{ label: "}", img: "u" } B')) == ['@{ label: "}", img: "u" }']
    assert list(blocks("A@{ a: 1 } B@{ b: 2 }")) == ["@{ a: 1 }", "@{ b: 2 }"]
    assert list(blocks("nessuna direttiva")) == []
    # Shape mai chiusa: si prende tutto il resto (Mermaid arriva a EOF e la
    # parse fallisce, quindi rifiutare è la scelta prudente).
    assert list(blocks('A@{ x: "aperta')) == ['@{ x: "aperta']


def test_mermaid_render_batch_refuses_an_svg_with_an_external_resource(
    monkeypatch: pytest.MonkeyPatch,
):
    """Seconda rete dopo il gate: Mermaid è esente da `normalize_svg` (A11),
    quindi un `<image href="http://…">` uscito comunque dal pre-render non
    deve raggiungere il PDF, le slide o i frame (SEC-1)."""
    svgs = [
        '<svg><image href="http://interno/x.png"/></svg>',
        '<svg><g style="fill:url(https://interno/y)"/></svg>',
        "<svg><style>@import url(https://interno/z.css);</style><g>x</g></svg>",
        "<svg><g>ok</g></svg>",
    ]
    monkeypatch.setattr(frs, "_prerender_mermaid_to_svg_batch_sync", lambda codes: list(svgs))
    out = frs.REGISTRY["mermaid"].render_svg_batch(
        ["a", "b", "c", "d"], asset_ids=["A", "B", "C", "D"]
    )
    assert out[:3] == [None, None, None]
    assert out[3] == "<svg><g>ok</g></svg>"


def test_mermaid_render_batch_keeps_labels_that_talk_about_css(
    monkeypatch: pytest.MonkeyPatch,
):
    """La scansione guarda gli ATTRIBUTI e il CSS, non il testo dei nodi:
    una label che cita `@import` o `url(https://…)` — normale in una lezione
    sul web — deve restare una figura valida in dispensa, slide e video."""
    svgs = [
        "<svg><text><tspan>Regola @import nel CSS</tspan></text></svg>",
        "<svg><text><tspan>background: url(https://cdn/x.png)</tspan></text></svg>",
        '<svg><g aria-label="url(https://cdn/x.png)"><text>ok</text></g></svg>',
    ]
    monkeypatch.setattr(frs, "_prerender_mermaid_to_svg_batch_sync", lambda codes: list(svgs))
    out = frs.REGISTRY["mermaid"].render_svg_batch(["a", "b", "c"], asset_ids=["A", "B", "C"])
    assert out == svgs


def test_mermaid_html_gate_suggests_tilde_generics_for_class_diagrams():
    r = frs.REGISTRY["mermaid"]
    ok, err = r.validate("classDiagram\n  class A {\n    List<int> items\n  }")
    assert ok is False and "(<int>)" in err and "List~int~" in err
    ok, err = frs.REGISTRY["mermaid"].validate("flowchart LR\n  A[a<b and c>d] --> B")
    assert ok is False and "<b and c>" in err and "~" not in err


def test_mermaid_static_gate_is_shared_with_the_revalidation_script():
    """Un solo gate: lo script L5 di WP1 delega a `mermaid_static_gate`."""
    from scripts.revalidate_mermaid_assets import static_gate

    assert frs.mermaid_static_gate("flowchart LR\n  A --> B") == ("", "")
    assert frs.mermaid_declared_type("---\ntitle: t\n---\n%% c\n graph TD;\n A") == "graph"
    assert static_gate("flowchart-elk LR\n  A --> B") == "mermaid_type_not_allowed:flowchart-elk"
    assert static_gate("flowchart LR\n  A[<table>x</table>]") == "mermaid_html_in_label"
    assert static_gate("---\nconfig:\n  theme: x\n---\nflowchart LR\n  A") == (
        "mermaid_init_directive"
    )
    assert static_gate('---\n"config": {theme: x}\n---\nflowchart LR\n  A') == (
        "mermaid_init_directive"
    )
    assert static_gate("%%{initialize: {'theme': 'x'}}%%\nflowchart LR\n  A") == (
        "mermaid_init_directive"
    )
    assert static_gate("flowchart LR\n  A[x <b] --> B") == ""
    assert static_gate("%% <b>c</b>\nflowchart LR\n  A --> B") == ""


def test_mermaid_translatable_is_the_whole_source():
    r = frs.REGISTRY["mermaid"]
    assert r.extract_translatable("flowchart LR\n A --> B") == {"": "flowchart LR\n A --> B"}
    assert r.apply_translations("x", {"": "y"}) == "y"
    assert r.extract_translatable("  ") == {}


# ---------------------------------------------------------------------------
# Vega-Lite — validazione offline (schema + D5) e render
# ---------------------------------------------------------------------------


@needs_vl
def test_vegalite_valid_spec_passes_shallow_validation():
    assert frs.REGISTRY["vegalite"].validate(_BAR_JSON) == (True, "")
    assert frs.REGISTRY["vegalite"].validate("```vega-lite\n" + _BAR_JSON + "\n```") == (True, "")


@needs_vl
@pytest.mark.parametrize(
    ("spec", "needle", "etype"),
    [
        ({"data": {"url": "x.csv"}, "mark": "bar"}, "data.url", "figure_invalid"),
        (
            {"layer": [{"data": {"url": "x.csv"}, "mark": "bar"}]},
            "data.url",
            "figure_invalid",
        ),
        ({"data": {"values": []}, "mark": "image"}, "mark image", "figure_invalid"),
        ({"data": {"values": []}, "mark": "bogus_mark"}, "mark", "figure_invalid"),
        (
            {
                "data": {"values": [{"k": "a", "v": 1}]},
                "transform": [
                    {
                        "lookup": "k",
                        "from": {
                            "data": {"url": "https://example.org/x.json"},
                            "key": "k",
                            "fields": ["z"],
                        },
                    }
                ],
                "mark": "bar",
                "encoding": {
                    "x": {"field": "k", "type": "nominal"},
                    "y": {"field": "v", "type": "quantitative", "scale": {"domain": [0, 2]}},
                },
            },
            "transform[0].from.data.url",
            "figure_invalid",
        ),
        (
            {
                "data": {"sequence": {"start": 0, "stop": 6, "step": 0.1, "as": "x"}},
                "transform": [{"calculate": "sin(datum.x)", "as": "y"}],
                "mark": {"type": "line", "clip": True},
                "encoding": {
                    "x": {"field": "x", "type": "quantitative", "scale": {"domain": [0, 6]}},
                    "y": {"field": "y", "type": "quantitative", "scale": {"domain": [-1, 1]}},
                },
            },
            'format="function"',
            "vegalite_use_function_format",
        ),
    ],
)
def test_vegalite_rejections(spec: dict[str, Any], needle: str, etype: str):
    ok, err = frs.REGISTRY["vegalite"].validate(json.dumps(spec))
    assert ok is False and needle in err, err
    assert frs.error_type_for(err) == etype


@needs_vl
def test_vegalite_length_duplicates_and_non_object_are_rejected():
    r = frs.REGISTRY["vegalite"]
    ok, err = r.validate("{" + " " * (frs.VEGALITE_MAX_CHARS + 10) + "}")
    assert ok is False and "oltre" in err
    ok, err = r.validate('{"mark": "bar", "mark": "line"}')
    assert ok is False and "duplicata" in err
    ok, err = r.validate("[1, 2]")
    assert ok is False and "oggetto" in err
    ok, err = r.validate("{non json")
    assert ok is False and "JSON" in err


def _and_filter_spec(levels: int) -> str:
    predicate: dict[str, Any] = {"field": "v", "gt": 0}
    for _ in range(levels):
        predicate = {"and": [predicate]}
    return json.dumps(
        {"data": {"values": [{"v": 1}]}, "transform": [{"filter": predicate}], "mark": "bar"}
    )


def test_vegalite_deeply_nested_json_is_rejected_without_recursion_error():
    """Il decoder C di `json` solleva `RecursionError` (non `ValueError`)
    oltre ~1.000 livelli entro i 4.000 caratteri; jsonschema ricorre in
    Python e cade a ~100 livelli di `and`: entrambi i casi devono uscire
    dal gate come `(False, msg)`, mai come eccezione (HTTP 500 dal PATCH,
    eccezione non gestita nel worker). Offline: il cap di annidamento
    precede lo schema."""
    r = frs.REGISTRY["vegalite"]
    ok, err = r.validate("[" * 1500 + "]" * 1500)
    assert (
        ok is False
        and err.startswith("JSON non valido")
        and frs.error_type_for(err) == ("figure_invalid")
    )
    ok, err = r.validate(_and_filter_spec(100))
    assert ok is False and "annidata oltre" in err
    nested: Any = 1
    for _ in range(40):
        nested = {"a": nested}
    ok, err = r.validate(json.dumps({"data": {"values": [nested]}, "mark": "bar"}))
    assert ok is False and "annidata oltre" in err
    # Render e localizzazione passano dallo stesso `_parse_vegalite`.
    assert r.render_svg("[" * 1500 + "]" * 1500, asset_id="A1") is None
    assert r.extract_translatable(_and_filter_spec(100)) == {}


async def test_validate_visual_assets_turns_deep_json_into_422(monkeypatch: pytest.MonkeyPatch):
    _patch_settings(monkeypatch, figure_dot_enabled=False)
    with pytest.raises(ValidationAppError) as exc:
        await frs.validate_visual_assets_or_raise(
            [{"asset_id": "A1", "format": "vegalite", "content": "[" * 1500 + "]" * 1500}],
            previous=None,
            loc_root="visual_assets",
            code="lesson_content_invalid_visual_asset",
        )
    (error,) = exc.value.meta["errors"]
    assert error["type"] == "figure_invalid" and error["msg"].startswith("JSON non valido")


@needs_vl
def test_vegalite_schema_validator_is_cached_and_cheap():
    v1 = frs._vegalite_validator()
    assert frs._vegalite_validator() is v1
    t0 = time.perf_counter()
    for _ in range(20):
        frs.REGISTRY["vegalite"].validate(_BAR_JSON)
    assert (time.perf_counter() - t0) / 20 < 0.1  # ~1 ms per spec misurato


@needs_vl
def test_vegalite_deep_validation_renders_with_theme_and_fills_the_cache():
    r = frs.REGISTRY["vegalite"]
    assert r.validate(_BAR_JSON, deep=True) == (True, "")
    key = frs.cache_key("vegalite", r.sanitize(_BAR_JSON))
    svg = frs._cache_get(key)
    assert svg is not None and svg.startswith("<svg")
    assert "<foreignObject" not in svg and "<script" not in svg
    assert "Noto Sans" in svg  # tema: font
    lower = svg.lower()
    assert any(color.lower() in lower for color in theme.PALETTE)  # tema: palette
    assert 'aria-roledescription="bar"' not in svg  # `aria: false` sui mark (config di render)
    # Hit di cache: nessun nuovo processo figlio.
    t0 = time.perf_counter()
    assert r.render_svg(_BAR_JSON, asset_id="A1") == svg
    assert time.perf_counter() - t0 < 0.05


@needs_vl
def test_vegalite_labels_with_url_href_and_handlers_are_rendered():
    """Titoli e valori dei dati finiscono nei `<text>` e negli `aria-label`
    di Vega: la scansione di `normalize_svg` non li tocca."""
    spec = json.loads(_BAR_JSON)
    spec["title"] = "vedi url(x)"
    spec["encoding"]["x"]["axis"]["title"] = "href="
    spec["data"]["values"][0]["k"] = "onload="
    r = frs.REGISTRY["vegalite"]
    assert r.validate(json.dumps(spec), deep=True) == (True, "")
    svg = r.render_svg(json.dumps(spec))
    assert svg is not None and "vedi url(x)" in svg and "href=" in svg


@needs_vl
def test_vegalite_batch_renders_valid_specs_and_none_for_failures():
    r = frs.REGISTRY["vegalite"]
    line = json.loads(_BAR_JSON)
    line["mark"] = {"type": "line", "clip": True}
    out = r.render_svg_batch(
        [_BAR_JSON, '{"mark": "bogus"}', json.dumps(line), "{non json"],
        asset_ids=["A", "B", "C", "D"],
    )
    assert out[0] is not None and out[2] is not None
    assert out[1] is None and out[3] is None
    assert out[0] != out[2]


@needs_vl
def test_vegalite_translatable_fields_round_trip():
    r = frs.REGISTRY["vegalite"]
    fields = r.extract_translatable(_BAR_JSON)
    assert fields == {
        "title": "Esempio",
        "encoding.x.axis.title": "categoria",
        "encoding.y.axis.title": "valore",
    }
    out = r.apply_translations(_BAR_JSON, {"title": "Example", "encoding.x.axis.title": "class"})
    spec = json.loads(out)
    assert spec["title"] == "Example" and spec["encoding"]["x"]["axis"]["title"] == "class"
    assert spec["data"] == _BAR["data"]  # dati intatti
    # Nessun percorso applicabile: il sorgente non viene toccato (I18N-3).
    unchanged = r.apply_translations(_BAR_JSON, {"non.esiste": "x"})
    assert unchanged == _BAR_JSON
    nested = json.dumps({"layer": [{"mark": "bar", "encoding": {"y": {"legend": {"title": "L"}}}}]})
    assert r.extract_translatable(nested) == {"layer.0.encoding.y.legend.title": "L"}


@needs_vl
def test_vegalite_translation_keeps_source_formatting():
    """I18N-3: la localizzazione sostituisce le sole stringhe tradotte nel
    sorgente. La formattazione del docente resta, il round-trip con
    traduzioni identiche è byte-identico e il tetto D5 non può essere
    superato dalla sola localizzazione."""
    r = frs.REGISTRY["vegalite"]
    padded = json.dumps(_BAR, indent=2)
    out = r.apply_translations(padded, {"title": "Example"})
    assert out == padded.replace('"Esempio"', '"Example"')
    assert out.count("\n") == padded.count("\n")  # rientri e a capo intatti
    # Round-trip identità: nemmeno un byte cambia.
    assert r.apply_translations(padded, r.extract_translatable(padded)) == padded
    # Una spec formattata al limite del tetto D5 con una traduzione più
    # lunga ricade sulla forma compatta invece di sfondarlo.
    big = json.dumps(
        {"title": "t", "mark": "bar", "data": {"values": [{"k": f"k{i}"} for i in range(109)]}},
        indent=2,
    )
    assert frs.VEGALITE_MAX_CHARS - 40 < len(big) <= frs.VEGALITE_MAX_CHARS
    localized = r.apply_translations(big, {"title": "t" * 80})
    assert len(localized) <= frs.VEGALITE_MAX_CHARS
    assert json.loads(localized)["title"] == "t" * 80


# ---------------------------------------------------------------------------
# DOT
# ---------------------------------------------------------------------------


def test_dot_static_validation_is_offline(monkeypatch: pytest.MonkeyPatch):
    r = frs.REGISTRY["dot"]
    monkeypatch.setattr(frs.shutil, "which", lambda *_a, **_k: "/usr/bin/dot")
    assert r.validate(_DOT) == (True, "")
    ok, err = r.validate('digraph { a [image="x.png"]; }')
    assert ok is False and "image=" in err
    ok, err = r.validate('digraph { a [URL="http://x"]; }')
    assert ok is False and "URL=" in err
    # Composti degli archi e delle label, e `SRC` dell'`<IMG>` HTML-like.
    for src, needle in (
        ('digraph { a -> b [labelURL="http://x", label="l"] }', "labelURL="),
        ('digraph { a -> b [headURL="http://x"] }', "headURL="),
        ('digraph { a -> b [tailtarget="_blank"] }', "tailtarget="),
        ('digraph { a -> b [edgehref="x"] }', "edgehref="),
        ('digraph { a [labelhref="x"]; }', "labelhref="),
        ('digraph { a [label=<<IMG SRC="/etc/hosts"/>>] }', "SRC="),
        ('digraph { a [label=<<IMG\n SCALE="TRUE" src="x"/>>] }', "SRC="),  # a capo, minuscolo
        ('digraph { imagepath="/etc"; a -> b }', "imagepath="),  # attributo di grafo
        # Forme del nome che lo scanner di Graphviz risolve in `image`
        # (verificate con dot 15.1.1: tutte aprono il file indicato).
        ('digraph { a ["image"="/etc/hosts"] }', "image="),
        ('digraph { a ["image" = "/etc/hosts"] }', "image="),
        ('digraph { node ["URL"="http://x"] }', "URL="),
        ('digraph { a ["ima"+"ge"="/etc/hosts"] }', "image="),  # concatenazione
        ('digraph { a ["ima" /*c*/ + "ge"="/etc/hosts"] }', "image="),
        ('digraph { a ["ima\\\nge"="/etc/hosts"] }', "image="),  # continuazione di riga
        ('digraph { a [image/*c*/="/etc/hosts"] }', "image="),  # commento prima di `=`
        ('digraph { a [<image>="/etc/hosts"] }', "image="),  # stringa HTML come nome
        ('digraph { a [label="//" image="/etc/hosts"] }', "image="),  # `//` in una stringa
        ('digraph { a [label="a\\"b" image="/etc/hosts"] }', "image="),  # `\\"` nella stringa
        ('digraph { a [IMAGE="/etc/hosts"] }', "IMAGE="),  # prudenza: dot è case-sensitive
    ):
        ok, err = r.validate(src)
        assert ok is False and needle in err, (src, err)
    # Attributi leciti con prefissi simili, e i nomi vietati come TESTO
    # (valori di label, commenti, righe `#`) restano ammessi: il confronto
    # è sul nome che precede un `=`.
    for src in (
        'digraph { a [imagescale=true]; a -> b [headlabel="h", taillabel="t"] }',
        "digraph { a [label=<<TABLE><TR><TD>x</TD></TR></TABLE>>] }",
        'digraph { a [label="vedi image=1 e URL=2"] }',
        'digraph { a [label="x" /* image="y" */ ] }',
        'digraph { a [label="x"\n#image="y"\n] }',
        "digraph { a [label=<<TABLE><TR><TD>src=1</TD></TR></TABLE>>] }",
        'digraph { a [tooltip="t", label="l"]; a -> b [edgetooltip="e"] }',
    ):
        ok, err = r.validate(src)
        assert ok is True, (src, err)
    # Forme che facevano divergere il tokenizzatore dallo scanner reale
    # (verificate contro dot 15.1.1: tutte aprono il file, SEC-2).
    for src, needle in (
        # `\\` è una coppia: leggerne solo il primo carattere faceva passare
        # la virgoletta di chiusura per un apice escapato e l'attributo
        # seguente restava dentro la «stringa».
        ('digraph { "\\\\" ; x [image="/etc/hosts"] }', "image="),
        ('digraph { a [label="c:\\\\"]; x [URL="http://x"] }', "URL="),
        # Concatenazione fra stringa HTML e stringa quotata: per Graphviz
        # è un solo ID (`<ima>+"ge"` è `image`).
        ('digraph { x [<ima>+"ge"="/etc/hosts"] }', "image="),
        ('digraph { x ["ima"+<ge>="/etc/hosts"] }', "image="),
        ('digraph { x [<ima>+<ge>="/etc/hosts"] }', "image="),
        # `#` a METÀ riga: per lo scanner di Graphviz è un commento fino a
        # fine riga come a colonna 0 (verificato: `digraph { a # -> b⏎; c }`
        # non produce archi), quindi la virgoletta che lo segue non apre
        # alcuna stringa e l'attributo dopo il capo riga resta visibile.
        ('digraph { # "\n x [image="/etc/hosts"] }', "image="),
        ('digraph { a; # commento "\n x [URL="http://x"] }', "URL="),
    ):
        ok, err = r.validate(src)
        assert ok is False and needle in err, (src, err)
    # Lo stesso sorgente con un file esistente e con uno inesistente dà lo
    # stesso esito: nessun oracolo di esistenza dei file del server (SEC-2).
    esiste = f'digraph {{ # "\n x [image="{__file__}"] }}'
    manca = 'digraph { # "\n x [image="/tmp/a4u-non-esiste.png"] }'
    assert r.validate(esiste) == r.validate(manca)
    assert r.validate(esiste, deep=True) == r.validate(manca, deep=True)
    # Il `\\\\` dentro una label resta testo: nessun falso positivo.
    assert r.validate('digraph { a [label="c:\\\\dir"]; a -> b }') == (True, "")
    # Apici singoli: non delimitano stringhe in DOT (errore di sintassi per
    # `dot`, nessun file letto): il gate statico non li tratta.
    assert frs._dot_forbidden_attribute("digraph { a ['image'='/etc/hosts'] }") is None
    ok, err = r.validate("subgraph { a -> b }")
    assert ok is False and "digraph" in err
    ok, err = r.validate("")
    assert ok is False and "vuoto" in err
    ok, err = r.validate("digraph { " + " a -> b;" * (frs.DOT_MAX_EDGES + 1) + " }")
    assert ok is False and "archi" in err


def test_dot_missing_binary_is_never_a_pass_through(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(frs.shutil, "which", lambda *_a, **_k: None)
    frs.available_formats.cache_clear()
    r = frs.REGISTRY["dot"]
    assert r.available() is False
    assert r.validate(_DOT) == (False, "dot_unavailable")
    assert r.validate(_DOT, deep=True) == (False, "dot_unavailable")
    assert r.render_svg(_DOT, asset_id="D1") is None
    assert "dot" not in frs.available_formats()


def test_dot_theme_prelude_respects_existing_blocks():
    themed = frs._dot_with_theme("digraph G {\n a -> b\n}")
    assert themed.index("{") < themed.index("graph [") < themed.index("node [")
    assert "edge [" in themed and 'fontname="Noto Sans"' in themed
    partial = frs._dot_with_theme("digraph { node [shape=circle]; a -> b }")
    assert partial.count("node [") == 1 and "graph [" in partial and "edge [" in partial
    assert frs._dot_with_theme("non dot") == "non dot"


def test_dot_translatable_labels_round_trip():
    r = frs.REGISTRY["dot"]
    src = 'digraph { a [label="Inizio"]; a -> b [label="passo 1", xlabel="x"]; b [label="Fine"]; }'
    fields = r.extract_translatable(src)
    assert fields == {"label.0": "Inizio", "label.1": "passo 1", "xlabel.2": "x", "label.3": "Fine"}
    out = r.apply_translations(src, {"label.0": "Start", "label.3": 'The "end"'})
    assert 'a [label="Start"]' in out and 'b [label="The \\"end\\""]' in out
    assert 'label="passo 1"' in out
    assert r.apply_translations(src, {}) == src


def test_dot_translatable_labels_keep_escapes_byte_for_byte():
    """`\\n` (a capo) e `\\"` devono sopravvivere al giro estrai/applica: la
    riscrittura incondizionata dei backslash li raddoppiava a ogni passata
    di localizzazione e la label finiva su una riga sola con un `\\n`
    letterale (I18N-1)."""
    r = frs.REGISTRY["dot"]
    src = 'digraph g {\n  a [label="Livello\\nclient"];\n  b [label="Base di \\"dati\\""];\n}'
    fields = r.extract_translatable(src)
    assert fields == {"label.0": "Livello\\nclient", "label.1": 'Base di "dati"'}
    assert r.apply_translations(src, fields) == src  # identità = byte identici
    twice = r.apply_translations(src, fields)
    assert r.apply_translations(twice, r.extract_translatable(twice)) == src
    out = r.apply_translations(src, {"label.0": "Level\\nclient", "label.1": 'The "data"'})
    assert 'label="Level\\nclient"' in out and 'label="The \\"data\\""' in out
    # Un backslash finale isolato escaperebbe la virgoletta di chiusura.
    assert r.apply_translations(src, {"label.0": "fine\\"}).count('"') % 2 == 0


@needs_dot
def test_dot_deep_validation_renders_with_theme_and_fills_the_cache():
    r = frs.REGISTRY["dot"]
    assert r.validate(_DOT, deep=True) == (True, "")
    key = frs.cache_key("dot", r.sanitize(_DOT))
    svg = frs._cache_get(key)
    assert svg is not None and svg.startswith("<svg")
    assert "<foreignObject" not in svg and "<?xml" not in svg
    assert "Noto Sans" in svg
    lower = svg.lower()
    assert theme.COLOR_AXIS.lower() in lower and theme.COLOR_INK.lower() in lower
    assert "vedi url(x)" in svg  # label con `url(` nel testo: accettata
    assert r.render_svg(_DOT, asset_id="D1") == svg  # hit


@needs_dot
def test_dot_syntax_error_reports_stderr():
    ok, err = frs.REGISTRY["dot"].validate("digraph { a -> ; }", deep=True)
    assert ok is False and "syntax error" in err


# ---------------------------------------------------------------------------
# run_isolated (processo figlio spawn con bersagli reali)
# ---------------------------------------------------------------------------


def test_run_isolated_returns_a_result_larger_than_the_pipe_buffer():
    t0 = time.perf_counter()
    out = run_isolated("tests.helpers.slow_target:big_result", {"size": 2_000_000}, timeout=30)
    assert len(out) == 2_000_000
    assert time.perf_counter() - t0 < 15
    assert run_isolated("tests.helpers.slow_target:echo", {"a": [1, 2]}, timeout=30) == {
        "a": [1, 2]
    }


def test_run_isolated_kills_a_hanging_child_at_the_deadline():
    t0 = time.perf_counter()
    with pytest.raises(FigureTimeoutError, match="nessuna risposta entro 1 s"):
        run_isolated("tests.helpers.slow_target:sleep_forever", None, timeout=1)
    elapsed = time.perf_counter() - t0
    assert 0.9 < elapsed < 4.0, elapsed  # deadline monotona: niente join da 5 s
    assert not multiprocessing.active_children()  # figlio ucciso e raccolto


def test_run_isolated_propagates_child_exceptions_as_compute_error():
    with pytest.raises(FigureComputeError, match="ValueError: fallimento voluto: 7"):
        run_isolated("tests.helpers.slow_target:boom", 7, timeout=30)
    with pytest.raises(FigureComputeError):
        run_isolated("tests.helpers.slow_target:non_esiste", None, timeout=30)
    with pytest.raises(FigureComputeError, match="bersaglio non valido"):
        run_isolated("senza_due_punti", None, timeout=30)


# ---------------------------------------------------------------------------
# render_svg_map
# ---------------------------------------------------------------------------


async def test_render_svg_map_groups_by_format_and_uses_the_cache(
    monkeypatch: pytest.MonkeyPatch,
):
    vl = _FakeRenderer("vegalite")
    dot = _FakeRenderer("dot")
    monkeypatch.setitem(frs.REGISTRY, "vegalite", vl)
    monkeypatch.setitem(frs.REGISTRY, "dot", dot)
    frs.available_formats.cache_clear()
    assets = [
        {"asset_id": "A1", "format": "vegalite", "content": "s1"},
        {"asset_id": "A2", "format": "dot", "content": "g1"},
        {"asset_id": "A3", "format": "vegalite", "content": "s2"},
        {"asset_id": "A4", "format": "image", "content": "/uploads/x.png"},
        {"asset_id": "A5", "format": "dot", "content": "   "},
        {"asset_id": "", "format": "dot", "content": "g2"},
    ]
    out = await frs.render_svg_map(assets, language="it")
    assert out == {
        "A1": "<svg>vegalite:s1</svg>",
        "A3": "<svg>vegalite:s2</svg>",
        "A2": "<svg>dot:g1</svg>",
    }
    assert vl.batches == [["s1", "s2"]] and dot.batches == [["g1"]]  # UN batch per formato
    again = await frs.render_svg_map(assets, language="en")
    assert again == out
    assert vl.batches == [["s1", "s2"]] and dot.batches == [["g1"]]  # tutto dalla cache


async def test_render_svg_map_never_raises_and_uses_the_negative_cache(
    monkeypatch: pytest.MonkeyPatch,
):
    bad = _FakeRenderer("vegalite", ok=False)
    monkeypatch.setitem(frs.REGISTRY, "vegalite", bad)
    frs.available_formats.cache_clear()
    assets = [{"asset_id": "A1", "format": "vegalite", "content": "s1"}]
    assert await frs.render_svg_map(assets, language="it") == {}
    assert await frs.render_svg_map(assets, language="it") == {}
    assert bad.batches == [["s1"]]  # il secondo giro non ritenta (cache negativa)

    class _Boom(_FakeRenderer):
        def render_svg_batch(
            self, contents: list[str], *, asset_ids: list[str]
        ) -> list[str | None]:
            raise RuntimeError("renderer rotto")

    monkeypatch.setitem(frs.REGISTRY, "dot", _Boom("dot"))
    assert (
        await frs.render_svg_map(
            [{"asset_id": "D", "format": "dot", "content": "g"}], language="it"
        )
        == {}
    )


async def test_render_svg_map_times_out_without_raising(monkeypatch: pytest.MonkeyPatch):
    slow = _FakeRenderer("vegalite", delay=0.6)
    monkeypatch.setitem(frs.REGISTRY, "vegalite", slow)
    _patch_settings(monkeypatch, figure_render_timeout_seconds=0.2)
    t0 = time.perf_counter()
    out = await frs.render_svg_map(
        [{"asset_id": "A1", "format": "vegalite", "content": "s1"}], language="it"
    )
    assert out == {}
    assert time.perf_counter() - t0 < 0.6
    await asyncio.sleep(0.7)  # il thread del renderer finto termina


async def test_render_svg_map_gives_the_mermaid_batch_its_own_floor_and_no_negative_cache(
    monkeypatch: pytest.MonkeyPatch,
):
    """Il batch Mermaid paga un costo fisso (Chromium + CDN) prima del primo
    render: ha un tetto minimo proprio sopra `figure_render_timeout_seconds`
    e un suo timeout non entra in cache negativa (non è attribuibile alle
    singole figure); per gli altri formati resta il comportamento di base."""
    slow = _FakeRenderer("mermaid", delay=0.35)
    monkeypatch.setitem(frs.REGISTRY, "mermaid", slow)
    monkeypatch.setitem(frs._BATCH_TIMEOUT_FLOOR_S, "mermaid", 0.8)
    _patch_settings(monkeypatch, figure_render_timeout_seconds=0.1)
    assets = [{"asset_id": "M1", "format": "mermaid", "content": "flowchart LR\n A --> B"}]
    assert frs._batch_timeout("mermaid", 0.1) == 0.8 and frs._batch_timeout("dot", 0.1) == 0.1
    assert await frs.render_svg_map(assets, language="it") == {
        "M1": "<svg>mermaid:flowchart LR\n A --> B</svg>"
    }
    monkeypatch.setitem(frs._BATCH_TIMEOUT_FLOOR_S, "mermaid", 0.1)
    frs.clear_svg_cache()
    assert await frs.render_svg_map(assets, language="it") == {}  # timeout
    assert await frs.render_svg_map(assets, language="it") == {}  # ritenta: niente cache negativa
    assert len(slow.batches) == 3
    await asyncio.sleep(0.8)  # i thread del renderer finto terminano


async def test_render_svg_map_skips_formats_disabled_by_the_kill_switch(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = _FakeRenderer("dot")
    monkeypatch.setitem(frs.REGISTRY, "dot", fake)
    _patch_settings(monkeypatch, figure_dot_enabled=False)
    out = await frs.render_svg_map(
        [{"asset_id": "D", "format": "dot", "content": "g"}], language="it"
    )
    assert out == {} and fake.batches == []


def test_lru_cache_evicts_the_oldest_entry(monkeypatch: pytest.MonkeyPatch):
    _patch_settings(monkeypatch, figure_svg_cache_size=2)
    k1, k2, k3 = (frs.cache_key("dot", f"g{i}") for i in range(3))
    frs._cache_put(k1, "1")
    frs._cache_put(k2, "2")
    assert frs._cache_get(k1) == "1"  # k1 diventa il più recente
    frs._cache_put(k3, "3")
    assert frs._cache_get(k2) is None and frs._cache_get(k1) == "1"
    assert frs.cache_key("dot", "x")[2] == theme.THEME_VERSION


# ---------------------------------------------------------------------------
# validate_visual_assets_or_raise (gate del PATCH, A15)
# ---------------------------------------------------------------------------


async def test_validate_visual_assets_checks_only_changed_assets(monkeypatch: pytest.MonkeyPatch):
    fake = _FakeRenderer("vegalite")
    monkeypatch.setitem(frs.REGISTRY, "vegalite", fake)
    frs.available_formats.cache_clear()
    previous = [
        {"asset_id": "A1", "format": "vegalite", "content": "old"},
        {"asset_id": "A2", "format": "mermaid", "content": "journey\n title x"},  # legacy in DB
    ]
    assets = [
        {"asset_id": "A1", "format": "vegalite", "content": "old"},  # invariato
        {"asset_id": "A2", "format": "mermaid", "content": "journey\n title x"},  # invariato
        {"asset_id": "A3", "format": "vegalite", "content": "new"},  # nuovo
        {"asset_id": "A4", "format": "image", "content": "/uploads/x.png"},  # non renderizzabile
    ]
    await frs.validate_visual_assets_or_raise(
        assets, previous=previous, loc_root="visual_assets", code="c"
    )
    assert fake.validated == ["new"]


async def test_validate_visual_assets_raises_422_with_per_asset_errors(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = _FakeRenderer("vegalite", ok=False)
    monkeypatch.setitem(frs.REGISTRY, "vegalite", fake)
    _patch_settings(monkeypatch, figure_dot_enabled=False)
    assets = [
        {"asset_id": "A1", "format": "mermaid", "content": "flowchart LR\n A --> B"},
        {"asset_id": "A2", "format": "vegalite", "content": "x" * 700},
        {"asset_id": "A3", "format": "dot", "content": "digraph { a -> b }"},
        {"asset_id": "A4", "format": "mermaid", "content": "journey\n title x"},
    ]
    with pytest.raises(ValidationAppError) as exc:
        await frs.validate_visual_assets_or_raise(
            assets,
            previous=None,
            loc_root="visual_assets",
            code="lesson_content_invalid_visual_asset",
        )
    err = exc.value
    assert err.status_code == 422 and err.code == "lesson_content_invalid_visual_asset"
    errors = err.meta["errors"]
    assert [e["loc"] for e in errors] == [
        ["visual_assets", 1, "content"],
        ["visual_assets", 2, "content"],
        ["visual_assets", 3, "content"],
    ]
    assert [e["asset_id"] for e in errors] == ["A2", "A3", "A4"]
    assert [e["format"] for e in errors] == ["vegalite", "dot", "mermaid"]
    assert [e["type"] for e in errors] == [
        "figure_invalid",
        "figure_format_unavailable",
        "mermaid_type_not_allowed",
    ]
    assert all(len(e["msg"]) <= 600 for e in errors)


async def test_validate_visual_assets_passes_when_everything_is_valid(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setitem(frs.REGISTRY, "vegalite", _FakeRenderer("vegalite"))
    frs.available_formats.cache_clear()
    await frs.validate_visual_assets_or_raise(
        [
            {"asset_id": "A1", "format": "mermaid", "content": "flowchart LR\n A --> B"},
            {"asset_id": "A2", "format": "vegalite", "content": "{}"},
        ],
        previous=[],
        loc_root="new_assets",
        code="lesson_slides_invalid_new_asset",
    )


def test_fence_and_control_chars_are_stripped_without_touching_the_body():
    body = '{"a": 1,\n "b": "è"}'
    assert frs._strip_fence_and_control(f"```vega-lite\n{body}\n```") == body
    assert frs._strip_fence_and_control(f"```json5 \n{body}```") == body
    assert frs._strip_fence_and_control("x\x1by") == "xy"
    assert re.sub(r"\s", "", frs._strip_fence_and_control(body)) == re.sub(r"\s", "", body)


class _WrongLengthRenderer(_FakeRenderer):
    """Renderer che viola il contratto: la lista non è parallela agli item."""

    def __init__(self, fmt: str, *, out: object) -> None:
        super().__init__(fmt)
        self.out = out

    def render_svg_batch(self, contents: list[str], *, asset_ids: list[str]) -> Any:
        self.batches.append(list(contents))
        return self.out


@pytest.mark.parametrize("out", [[], None, ["<svg>uno</svg>"], ["a", "b", "c"]])
async def test_render_svg_map_survives_a_renderer_with_a_non_parallel_list(
    monkeypatch: pytest.MonkeyPatch, out: object
):
    """`render_svg_map` non solleva mai: un renderer registrato che ritorna
    una lista di lunghezza diversa (o non una lista) deve degradare a
    fallback per figura, non far ripartire l'export dal worker (COR-1)."""
    monkeypatch.setitem(frs.REGISTRY, "dot", _WrongLengthRenderer("dot", out=out))
    frs.available_formats.cache_clear()
    assets = [
        {"asset_id": "D1", "format": "dot", "content": "digraph { a }"},
        {"asset_id": "D2", "format": "dot", "content": "digraph { b }"},
    ]
    result = await frs.render_svg_map(assets, language="it")
    assert set(result) <= {"D1", "D2"}


async def test_render_svg_map_skips_sources_emptied_by_sanitize(
    monkeypatch: pytest.MonkeyPatch,
):
    """Un sorgente che si svuota con la sanificazione (fence vuoto) non entra
    nel batch: per Mermaid significherebbe avviare Chromium e caricare la
    CDN per nulla (REG-4)."""
    calls: list[list[str]] = []

    def _never(codes: list[str]) -> list[str | None]:
        calls.append(list(codes))
        return [None] * len(codes)

    monkeypatch.setattr(frs, "_prerender_mermaid_to_svg_batch_sync", _never)
    frs.available_formats.cache_clear()
    assets = [{"asset_id": "M1", "format": "mermaid", "content": "```mermaid\n```"}]
    assert await frs.render_svg_map(assets, language="it") == {}
    assert calls == []
