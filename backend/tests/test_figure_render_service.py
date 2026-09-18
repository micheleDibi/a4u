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
import structlog.testing

from app.core import config
from app.core.errors import ValidationAppError
from app.services import figure_render_service as frs
from app.services import figure_theme as theme
from app.services import mermaid_prerender as mp
from app.services.figure_compute.isolated import (
    FigureComputeError,
    FigureTimeoutError,
    run_isolated,
)
from app.services.figure_scale import SvgMetrics
from app.services.svg_normalize import svg_base_font_px, svg_intrinsic_box

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
        # Chiave fuori dalla lista chiusa: rifiutata prima ancora di
        # guardare il valore (giro 4).
        ('flowchart LR\n  A@{ label: "}", x: "https://interno/y" }\n  A --> B', "`x:`"),
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


# `cleanupText` normalizza `\r` in `\n` e `cleanupComments` non tratta come
# commento né una riga `%%{…` (è una direttiva) né un `%%` nudo: due
# letture del sorgente che il gate non aveva e che gli nascondevano lo
# statement o la shape che segue. Ogni sorgente qui sotto è stato reso con
# Mermaid 11.17.2 e ha prodotto un `<image href>` o un `<a xlink:href>`
# verso l'host scelto dall'autore (giri 6 e 7); i primi due hanno fatto
# arrivare la GET al listener.
@pytest.mark.parametrize(
    ("code", "needle"),
    [
        # `\r` dentro una riga di commento: il gate scartava tutta la riga
        # fisica, Mermaid ne vede due ed esegue la seconda.
        ('flowchart LR\n%%nota\r  A@{ img: "http://interno/x.png" } --> B', "img:"),
        ('flowchart LR\n  A --> B\n%%nota\rclick A href "http://interno/x"', "`click`"),
        ('flowchart LR\n  A --> B\n   %% nota\rclick A href "http://interno/x"', "`click`"),
        ('graph LR\n  A --> B\n%%n\rclick A href "http://interno/x"', "`click`"),
        ('classDiagram\n  class A\n%%n\rclick A href "http://interno/x"', "`click`"),
        ('stateDiagram-v2\n  [*] --> A\n%%n\rclick A href "http://interno/x"', "`click`"),
        (
            'sequenceDiagram\n  A->>B: x\n%%n\rproperties A: {"icon": "http://interno/x"}',
            "`properties`",
        ),
        ('sequenceDiagram\n  A->>B: x\n%%n\rlinks A: {"a": "http://interno/x"}', "`links`"),
        # `%%` nudo: per `cleanupComments` serve almeno un carattere dopo.
        ('flowchart LR\n  A --> B\n%%\rclick A href "http://interno/x"', "`click`"),
        # Il `\r` nasconde anche un tag HTML e una chiave del frontmatter.
        ('flowchart LR\n%%n\r  A["<b>x</b>"] --> B', "HTML"),
        ("---\ntitle: a\rconfig:\r  theme: dark\n---\nflowchart LR\n  A --> B", "config:"),
        # Riga `%%{…}%%`: direttiva, non commento. `removeDirectives` ne
        # toglie solo la direttiva e lo statement che segue resta.
        ('flowchart LR\n  A --> B\n%%{x}%% click A href "http://interno/x"', "`click`"),
        ('flowchart LR\n%%{x}%% A@{ img: "http://interno/x.png" } --> B', "img:"),
        ('stateDiagram-v2\n  [*] --> A\n%%{x}%% click A href "http://interno/x"', "`click`"),
        (
            'sequenceDiagram\n  A->>B: x\n%%{x}%% properties A: {"img": "http://interno/x"}',
            "`properties`",
        ),
        ('flowchart LR\n  A --> B\n%%{wrap}%% click A href "http://interno/x"', "`click`"),
        ('flowchart LR\n%%{x}%% A["<b>x</b>"] --> B', "HTML"),
        # Le stesse due letture con la chiave scritta in una forma YAML che
        # nessun pattern testuale vede (`"\x69mg"` è `img` per js-yaml).
        (
            'flowchart LR\n%%n\r  A@{ "\\x69mg": "\\x68ttp://interno/x.png" } --> B',
            "\\x69mg",
        ),
        (
            'flowchart LR\n%%{x}%% A@{ "\\x69mg": "\\x68ttp://interno/x.png" } --> B',
            "\\x69mg",
        ),
    ],
)
def test_mermaid_static_gate_rejects_cr_and_directive_lines(code: str, needle: str):
    """Giro 7: le due letture del sorgente che mancavano al gate."""
    ok, err = frs.REGISTRY["mermaid"].validate(code)
    assert ok is False and needle in err, err
    assert err.startswith("mermaid_type_not_allowed")


@pytest.mark.parametrize(
    "code",
    [
        # Controprove sane della stessa classe: la lettura fedele non deve
        # costare diagrammi legittimi.
        "flowchart LR\r\n  A --> B\r\n%% una nota\r\n",  # CRLF (editor Windows)
        "flowchart LR\n  A[100%] --> B\n%% il 50% dei casi",
        "flowchart LR\n  A --> B\n%%{wrap}%%",  # direttiva sola, senza statement
        "%%{wrap}%%\nflowchart LR\n  A --> B",
        "flowchart LR\n  A --> B\n%%",  # `%%` nudo, senza nulla dopo
        "flowchart LR\n  A --> B\n%% nota\r%% seconda nota",  # due commenti, un `\r`
        'flowchart LR\n  A["riga1\rriga2"] --> B',  # `\r` dentro una label
    ],
)
def test_mermaid_static_gate_accepts_cr_and_directive_lines_without_statements(code: str):
    assert frs.REGISTRY["mermaid"].validate(code) == (True, "")


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


# SEC-1, residuo dei giri 2 e 3: il blocco `@{ … }` finisce in js-yaml, non
# in una regex. Ogni forma qui sotto è la chiave `img` (o una chiave che
# nasconde una risorsa) scritta in un modo che i pattern testuali
# `\bimg\s*:` e `https?:` non vedono; tutte passavano il gate, `mermaid.parse`
# e il PATCH, e sette di esse facevano davvero partire la GET nel browser di
# chi apriva la lezione. La lista chiusa delle chiavi le chiude in blocco.
MERMAID_SHAPE_BYPASSES = (
    'flowchart LR\n  A@{ "\\x69mg": "http://interno/x.png" }\n  A --> B',
    'flowchart LR\n  A@{ "img": "\\x68ttp://interno/x.png" }\n  A --> B',
    'flowchart LR\n  A@{ "img": "http\\u003a//interno/x.png" }\n  A --> B',
    "flowchart LR\n  A@{ 'img': 'http://interno/x.png' }\n  A --> B",
    'flowchart LR\n  A@{\n    "img": "\\x68ttp://interno/x.png"\n  }\n  A --> B',
    'flowchart LR\n  A@{ x: &a "\\x68ttp://interno/x.png", "img": *a }\n  A --> B',
    'flowchart LR\n  A@{\n    ? img\n    : "http://interno/x.png"\n  }\n  A --> B',
    'flowchart LR\n  A@{ !!str img: "http://interno/x.png" }\n  A --> B',
    'flowchart LR\n  A@{ <<: {img: "http://interno/x.png"} }\n  A --> B',
    'flowchart LR\n  A@{ img : "http://interno/x.png" }\n  A --> B',
    'flowchart LR\n  A@{ label: "]", img: "/etc/hosts" }\n  A --> B',
    'flowchart LR\n  A@{ icon: "http://interno/x.png" }\n  A --> B',
    'flowchart LR\n  A@{\n    label: |\n      testo\n    img: "http://interno/x.png"\n  }',
)


@pytest.mark.parametrize("code", MERMAID_SHAPE_BYPASSES)
def test_mermaid_shape_gate_is_a_closed_key_list(code: str):
    """SEC-1 (giro 4): nessuna forma YAML della chiave `img` deve passare.

    Il gate non prova a decodificare gli escape — li rifiuta: una chiave che
    non è scritta in forma piana e dentro la lista ammessa non passa,
    qualunque cosa js-yaml ne farà."""
    ok, err = frs.REGISTRY["mermaid"].validate(code)
    assert ok is False, code
    assert "risorsa esterna non ammessa nella shape" in err, err


@pytest.mark.parametrize(
    "code",
    [
        # Le undici chiavi che Mermaid 11.17.2 legge davvero da `doc`: nodo,
        # arco e sottografo. Tutte si rendono (`probe4`/`gate_check`).
        'flowchart LR\n  A@{ shape: rect, label: "Etichetta", w: 60, h: 40 } --> B',
        'flowchart LR\n  A@{ shape: rect, label: "X", pos: "t", form: "square" } --> B',
        'flowchart LR\n  A@{ shape: rect, label: "**X**", labelType: "markdown" } --> B',
        'flowchart LR\n  A@{ shape: rect, label: "X", constraint: "on" } --> B',
        "flowchart LR\n  A e1@--> B\n  e1@{ animate: true }",
        "flowchart LR\n  A e1@--> B\n  e1@{ curve: basis }",
        'flowchart LR\n  subgraph s1\n    A --> B\n  end\n  s1@{ label: "X" }',
        # Chiave quotata senza escape e commento `#`: forme ammesse.
        'flowchart LR\n  A@{ "shape": "rect", "label": "X" } --> B',
        "flowchart LR\n  A@{\n    # una nota\n    shape: rect\n  } --> B",
    ],
)
def test_mermaid_shape_gate_accepts_every_key_mermaid_reads(code: str):
    """Controprova della lista chiusa: le chiavi legittime non prendono 422."""
    assert frs.REGISTRY["mermaid"].validate(code) == (True, "")


# Statement che portano un URL o un'icona nell'SVG senza passare da alcuna
# shape: `sequenceDiagram / properties A: {"icon": "http://…"}` produce un
# `<image xlink:href>` (una GET vera nel browser del lettore), gli altri un
# `<a xlink:href>` verso l'host scelto dall'autore. Nessun gate li vedeva
# fino al giro 4; l'oracolo di rendering è in `test_mermaid_no_foreignobject`.
MERMAID_URL_STATEMENTS_REJECTED = (
    'sequenceDiagram\n  participant A\n  properties A: {"icon": "http://i/x.png"}\n  A->>A: x',
    'sequenceDiagram\n  participant A\n  PROPERTIES A: {"icon": "http://i/x.png"}\n  A->>A: x',
    'sequenceDiagram\n  participant A\n  properties A: {"icon": "/rel.png"}\n  A->>A: x',
    'sequenceDiagram\n  participant A\n  details A: {"properties": {"icon": "http://i/x"}}',
    'sequenceDiagram\n  participant A\n  links A: {"D": "http://i/x.png"}\n  A->>A: x',
    "sequenceDiagram\n  participant A\n  link A: D @ http://i/x.png\n  A->>A: x",
    'classDiagram\n  class A\n  link A "http://i/x.png" "t"',
    'classDiagram\n  class A\n  click A href "http://i/x.png" "t"',
    'flowchart LR\n  A --> B\n  click A href "http://i/x.png" "t"',
    # `;` è un a capo per Mermaid: lo statement può stare in coda a un altro.
    'flowchart LR\n  A --> B; click A href "http://i/x.png" "t"',
    'graph LR\n  A --> B\n  click A href "http://i/x.png" "t"',
)


@pytest.mark.parametrize("code", MERMAID_URL_STATEMENTS_REJECTED)
def test_mermaid_gate_rejects_the_statements_that_carry_a_url(code: str):
    ok, err = frs.REGISTRY["mermaid"].validate(code)
    assert ok is False, code
    assert "non è ammesso" in err and "un URL o un'icona" in err, err


@pytest.mark.parametrize(
    "code",
    [
        # Le stesse parole fuori dalle coppie misurate restano testo: in
        # `mindmap` e `timeline` sono il testo di un nodo, in `sankey-beta` e
        # `erDiagram` il nome di un nodo/entità, in `classDiagram` il nome di
        # una classe (`Link` con la maiuscola: il lexer è case-sensitive).
        "mindmap\n  root((r))\n    click qui\n    link https://esempio.it",
        "timeline\n  title T\n  2020 : click qui",
        "sankey-beta\n\nclick,link,1",
        "erDiagram\n  click ||--|| link : r",
        "classDiagram\n  Link --> Other",
        "classDiagram\n  links --> other",
        "sequenceDiagram\n  A ->> B: properties di x",
        # `;` dentro una label non separa lo statement.
        'flowchart LR\n  A["fai clic; click qui"] --> B',
    ],
)
def test_mermaid_gate_keeps_the_keywords_that_are_only_text(code: str):
    """Il gate degli statement non deve trasformarsi in un divieto di
    parole: fuori dalle coppie (famiglia, parola chiave) misurate, `click` e
    `link` sono contenuto legittimo."""
    assert frs.REGISTRY["mermaid"].validate(code) == (True, "")


def test_mermaid_shape_entries_split_like_js_yaml():
    """Le voci del blocco seguono la biforcazione di `addVertex`: virgola in
    forma flow (una riga sola), a capo in forma blocco."""
    entries = frs._mermaid_shape_entries
    assert list(entries('@{ shape: rect, label: "a, b" }')) == ["shape: rect", 'label: "a, b"']
    assert list(entries("@{\n  shape: rect\n  w: 60\n}")) == ["shape: rect", "w: 60"]
    # Le collezioni flow annidate non separano.
    assert list(entries("@{ label: [x, y], w: 1 }")) == ["label: [x, y]", "w: 1"]
    key = frs._mermaid_shape_key
    assert key('label: "a: b"') == "label"
    assert key('"img": "u"') == "img"
    # Gli escape NON si decodificano: la chiave resta irriconoscibile e
    # quindi fuori dalla lista.
    assert key('"\\x69mg": "u"') == "\\x69mg"


def test_mermaid_shape_metadata_follows_the_lexer():
    """SEC-1 (giro 5): il gate deve segmentare il `metadata` che il lexer
    consegna ad `addVertex`, non il sorgente grezzo.

    Nello stato `shapeDataStr` la regola 10 sostituisce `/\\n\\s*/g` con
    `<br/>`, quindi un a capo dentro le virgolette sparisce e `addVertex`
    sceglie la forma FLOW: leggendo il sorgente grezzo il gate sceglieva
    la forma BLOCCO e vedeva una voce sola, con la sola chiave `label`."""
    meta = frs._mermaid_shape_metadata
    assert meta('@{ label: "a\nb", img: "u" }') == ' label: "a<br/>b", img: "u" '
    assert meta("@{\n  shape: rect\n  w: 60\n}") == "\n  shape: rect\n  w: 60\n"
    # Fuori dalle virgolette l'a capo resta: è la forma blocco vera.
    assert "\n" in meta('@{\n  label: "x"\n}')
    # Virgolette dispari: la `}` finale è dentro la stringa, non la chiude.
    assert meta('@{ x: "aperta') == ' x: "aperta'

    entries = frs._mermaid_shape_entries
    assert list(entries('@{ label: "a\nb", img: "u" }')) == ['label: "a<br/>b"', 'img: "u"']
    # Le parentesi tonde NON sono un indicatore YAML: per js-yaml
    # `label: (` è uno scalare e la virgola che segue separa davvero.
    assert list(entries('@{ label: ( , img: "u" }')) == ["label: (", 'img: "u"']
    # Negli statement, invece, `(` apre la sezione di una label di Mermaid.
    assert list(frs._mermaid_statements(["A(fai clic; qui) --> B"])) == ["A(fai clic; qui) --> B"]


# SEC-1, vettori RIAPERTI dal giro 4 (misurati end-to-end: gate verde,
# `mermaid.parse` verde, PATCH 200 e `<image href>` nell'SVG con la GET
# davvero arrivata al listener). Il giro 3 li rifiutava con la scansione
# larga `\bimg\s*:`, che il giro 4 aveva SOSTITUITO con la sola lista
# chiusa: da qui la regola dell'unione, mai dello scambio.
MERMAID_SHAPE_DESYNC_BYPASSES = (
    # a capo dentro la stringa: il gate vedeva la forma blocco, js-yaml la flow
    'flowchart LR\n  A@{ label: "a\nb", img: "http://interno/x.png", w: 40 }\n  A-->B',
    'flowchart LR\n  A@{ label: "a\nb", "\\x69mg": "\\x68ttp://interno/x.png" }\n  A-->B',
    'flowchart LR\n  A@{ label: "a\nb", icon: "\\x68ttp://interno/x.png" }\n  A-->B',
    'flowchart LR\n  A@{ label: "x\n  #", "\\x69mg": "\\x68ttp://interno/x.png" }\n  A-->B',
    'flowchart LR\n  A@{ label: "x\n  #", img: "//interno/x.png" }\n  A-->B',
    'flowchart LR\n  A@{\n    label: "x\n    #", img: "//interno/x.png"\n  }\n  A-->B',
    'flowchart LR\n  A@{ label: "x\n  shape: rect, img: "//interno/x.png" }\n  A-->B',
    # parentesi tonda: profondità per lo splitter, scalare per js-yaml
    'flowchart LR\n  A@{ label: ( , img: "\\x68ttp://interno/x.png" }\n  A-->B',
    'flowchart LR\n  A@{ label: ( , img: "http://interno/x.png" }\n  A-->B',
    # forma blocco con due chiavi sulla stessa riga (js-yaml la rifiuta, ma
    # il gate non deve dipendere da quel dettaglio per rifiutarla)
    'flowchart LR\n  A@{\n    label: "x", img: "\\x68ttp://interno/x.png"\n  }\n  A-->B',
)


@pytest.mark.parametrize("code", MERMAID_SHAPE_DESYNC_BYPASSES)
def test_mermaid_shape_gate_unions_the_closed_list_with_the_text_scan(code: str):
    """SEC-1 (giro 5): i controlli si sommano. La lista chiusa sul
    `metadata` del lexer chiude la desincronizzazione, la scansione
    `img:`/`icon:` sul blocco grezzo tiene le forme che nessuna
    segmentazione spezza."""
    ok, err = frs.REGISTRY["mermaid"].validate(code)
    assert ok is False, code
    assert "risorsa esterna non ammessa nella shape" in err, err


# `stateDiagram` non era in `MERMAID_URL_STATEMENTS` fino al giro 4:
# `click A href "http://…"` passava il gate, il PATCH e arrivava nel PDF
# come `<a xlink:href="http://…" target="_blank">` (misurato in entrambe le
# scritture del tipo; il lexer di stato è case-insensitive su `click` e
# `href`, `chunk-IMKFNOWR.mjs`).
MERMAID_STATE_URL_STATEMENTS = (
    'stateDiagram-v2\n  [*] --> A\n  click A href "http://interno/x.png"',
    'stateDiagram\n  [*] --> A\n  click A href "http://interno/x.png"',
    'stateDiagram-v2\n  [*] --> A\n  CLICK A HREF "http://interno/x.png"',
    'stateDiagram-v2\n  [*] --> A\n  click A href "http://interno/x.png" "t"',
    'stateDiagram-v2\n  [*] --> A\n  click A call cb("http://interno/x.png")',
    'stateDiagram-v2\n  [*] --> A\n  A --> B; click A href "http://interno/x.png"',
)


@pytest.mark.parametrize("code", MERMAID_STATE_URL_STATEMENTS)
def test_mermaid_gate_rejects_the_state_click_statement(code: str):
    ok, err = frs.REGISTRY["mermaid"].validate(code)
    assert ok is False, code
    assert "non è ammesso (porta un URL o un'icona" in err, err
    assert err.lower().count("`click`") == 1, err


@pytest.mark.parametrize(
    "code",
    [
        # `click` come TESTO di uno stateDiagram: lo statement non comincia
        # con quella parola, quindi passa (tutti resi con il pre-render).
        "stateDiagram-v2\n  [*] --> A\n  A --> B: click qui\n  B --> [*]",
        'stateDiagram-v2\n  state "click qui" as A\n  [*] --> A',
        "stateDiagram-v2\n  [*] --> A\n  note right of A: click qui",
    ],
)
def test_mermaid_state_gate_keeps_click_as_text(code: str):
    assert frs.REGISTRY["mermaid"].validate(code) == (True, "")


# BOM: nel sorgente dei test resta una costante con il nome, non un
# carattere invisibile in mezzo a una stringa.
_BOM = "\ufeff"

# Giro 6, prima desincronizzazione: l'apice singolo. Per il lexer dei
# flowchart è un carattere di NODE_STRING (regola
# `[A-Za-z0-9!"\#$%&'*+.`?\\_/]` di `chunk-SHT3W25Y.mjs`), per js-yaml in
# mezzo a uno scalare piano è un carattere qualsiasi — per lo splitter del
# gate era un delimitatore. Un apice DISPARI «quotava» quindi tutto ciò che
# seguiva: nel blocco `@{ … }` la virgola che separava la voce successiva,
# in una riga il `;` che separava lo statement successivo. Tutti misurati
# sul pre-render: `<image href>` con la GET arrivata al listener per il
# primo gruppo, `<a xlink:href>` per il secondo.
MERMAID_ODD_QUOTE_BYPASSES = (
    'flowchart LR\n  A@{ label: x\'y, "\\x69mg": "\\x68ttp://interno/x.png" }\n  A-->B',
    'flowchart LR\n  A@{ label: don\'t, "\\x69mg": "\\x68ttp://interno/x.png" }\n  A-->B',
    'flowchart LR\n  A@{ label: l\'a, "\\x69con": "\\x68ttp://interno/x.png" }\n  A-->B',
    'flowchart LR\n  A@{ label: x\'y, img: "http://interno/x.png" }\n  A-->B',
    'flowchart LR\n  A@{ label: x\'y\n  "\\x69mg": "\\x68ttp://interno/x.png" }\n  A-->B',
    'flowchart LR\n  A["x"] --> B[it\'s]; click A href "http://interno/x.png"',
    'flowchart LR\n  A[l\'esempio]; click A href "http://interno/x.png"',
    'stateDiagram-v2\n  A: l\'s; click A href "http://interno/x.png"',
    'sequenceDiagram\n  participant A\n  A->>A: l\'x; properties A: {"icon": "http://x"}',
    'sequenceDiagram\n  participant A\n  Note over A: l\'x; links A: {"D": "http://x"}',
)

# Giro 6, seconda e terza desincronizzazione: il whitespace. `U+FEFF` è
# l'unico carattere in cui `\s` di JavaScript — il whitespace che il lexer
# salta — è più largo dell'insieme di `str.strip()` di Python, e nascondeva
# la parola chiave iniziale di uno statement; il `\r` è un a capo per il
# lexer ma non per `str.split("\n")`, e nascondeva l'intero statement che
# lo seguiva. Ogni riga è misurata sul pre-render (sezione 14.9).
MERMAID_JS_WHITESPACE_BYPASSES = (
    "flowchart LR\n  A --> B\n" + _BOM + 'click A href "http://interno/x.png"',
    "graph LR\n  A --> B\n" + _BOM + 'click A href "http://interno/x.png"',
    "flowchart LR\n  A --> B;" + _BOM + 'click A href "http://interno/x.png"',
    "flowchart LR\n  A --> B\n  " + _BOM + ' click A href "http://interno/x.png"',
    "flowchart LR\n  A --> B\n" + _BOM * 2 + 'click A href "http://interno/x.png"',
    "---\ntitle: x\n---\nflowchart LR\n  A --> B\n" + _BOM + 'click A href "http://x"',
    "classDiagram\n  class A\n" + _BOM + 'link A "http://interno/x.png" "t"',
    "classDiagram-v2\n  class A\n" + _BOM + 'link A "http://interno/x.png" "t"',
    "stateDiagram\n  [*] --> A\n" + _BOM + 'click A href "http://interno/x.png"',
    "stateDiagram-v2\n  [*] --> A\n" + _BOM + 'click A href "http://interno/x.png"',
    "sequenceDiagram\n  participant A\n" + _BOM + 'properties A: {"icon": "http://x"}',
    "sequenceDiagram\n  participant A\n" + _BOM + 'links A: {"D": "http://x"}',
    "sequenceDiagram\n  participant A\n" + _BOM + 'details A: {"x": "http://x"}',
    'flowchart LR\n  A --> B\rclick A href "http://interno/x.png"',
    'stateDiagram-v2\n  [*] --> A\rclick A href "http://interno/x.png"',
    'sequenceDiagram\n  participant A\n  A->>A: x\rproperties A: {"icon": "http://x"}',
)


@pytest.mark.parametrize("code", MERMAID_ODD_QUOTE_BYPASSES)
def test_the_gate_segments_with_both_quotings(code: str):
    """SEC-1 (giro 6): nessuna delle segmentazioni possibili coincide con il
    parser vero, quindi il gate non ne sceglie una — le prova a coppie e
    rifiuta se una qualsiasi segnala. `_QUOTING_ANY` è quella dei giri 2-5,
    `_QUOTING_YAML` apre le stringhe solo dove può iniziare un nodo (come
    js-yaml) e `_QUOTING_DOUBLE` non tratta l'apice come delimitatore (come
    il lexer). È l'unione applicata alla segmentazione, non solo ai
    controlli."""
    assert frs.REGISTRY["mermaid"].validate(code)[0] is False, code


@pytest.mark.parametrize("code", MERMAID_JS_WHITESPACE_BYPASSES)
def test_the_gate_reads_the_whitespace_of_the_lexer(code: str):
    """SEC-1 (giro 6): il gate deve leggere il whitespace come lo legge il
    lexer, non come lo legge Python. Il BOM si toglie insieme agli spazi e
    il `\r` è un separatore di statement; il `\r` NON entra invece nella
    divisione in righe, che sposterebbe anche il riconoscimento del tipo e
    del frontmatter e riammetterebbe sorgenti oggi rifiutati."""
    assert frs.REGISTRY["mermaid"].validate(code)[0] is False, code


@pytest.mark.parametrize(
    "code",
    [
        # L'apostrofo è normale in italiano e in inglese: le label che lo
        # contengono devono continuare a passare, quotate o meno.
        "flowchart LR\n  A[\"l'esempio d'uso\"] --> B",
        "flowchart LR\n  A[l'esempio] --> B[d'oro]",
        'flowchart LR\n  A@{ label: "l\'uso" } --> B',
        "flowchart LR\n  A@{ label: l'uso } --> B",
        'flowchart LR\n  A@{ shape: rect, label: "l\'a, la b" } --> B',
        "sequenceDiagram\n  A->>B: l'esempio\n  B->>A: d'accordo",
        "stateDiagram-v2\n  [*] --> A: l'avvio\n  A --> [*]",
        # Apice a INIZIO nodo: lì è un vero delimitatore anche per js-yaml,
        # quindi `img:` dentro lo scalare quotato è testo e il sorgente si
        # rende senza alcun riferimento esterno.
        "flowchart LR\n  A@{ label: 'x' } --> B",
        # Un BOM davanti a un commento `%%` resta un commento anche per il
        # lexer: il primo token dello statement non è una parola.
        "flowchart LR\n  A --> B\n" + _BOM + "%% click qui",
        # CRLF: il `\r` di fine riga produce statement vuoti, non falsi
        # positivi.
        "flowchart LR\r\n  A --> B\r\n  B --> C",
        # `\r` dentro una label quotata non separa nulla.
        'flowchart LR\n  A["riga1\rriga2"] --> B',
    ],
)
def test_apostrophes_and_line_endings_are_not_false_positives(code: str):
    assert frs.REGISTRY["mermaid"].validate(code) == (True, ""), code


def test_split_top_level_follows_three_different_quotings():
    """Le tre modalità in isolamento, sulla stessa stringa: `any` vede una
    voce sola (l'apice le quota la virgola), `yaml` due (l'apice è in mezzo
    a uno scalare piano, non a inizio nodo) e `double` due (l'apice non è
    un delimitatore). Il gate usa `any`+`yaml` per le shape e `any`+`double`
    per gli statement."""
    testo = 'label: x\'y, img: "http://x"'
    assert len(list(frs._split_top_level(testo, ",", quoting=frs._QUOTING_ANY))) == 1
    assert len(list(frs._split_top_level(testo, ",", quoting=frs._QUOTING_YAML))) == 2
    assert len(list(frs._split_top_level(testo, ",", quoting=frs._QUOTING_DOUBLE))) == 2
    # A inizio nodo l'apice quota anche per `yaml`: è il caso in cui js-yaml
    # legge davvero uno scalare quotato.
    quotato = "label: 'x, img: y'"
    assert len(list(frs._split_top_level(quotato, ",", quoting=frs._QUOTING_YAML))) == 1


# Ogni sorgente che un giro precedente della revisione rifiutava: il gate
# di oggi deve rifiutarli TUTTI. È la rete che avrebbe colto il giro 4, che
# chiudendo gli escape YAML aveva riaperto sette vettori del giro 3.
MERMAID_HISTORICAL_VECTORS = (
    *MERMAID_SHAPE_BYPASSES,
    *MERMAID_URL_STATEMENTS_REJECTED,
    *MERMAID_SHAPE_DESYNC_BYPASSES,
    *MERMAID_STATE_URL_STATEMENTS,
    *MERMAID_ODD_QUOTE_BYPASSES,
    *MERMAID_JS_WHITESPACE_BYPASSES,
    # giro 1: `}` dentro una stringa quotata
    'flowchart LR\n  A@{ label: "}", img: "http://interno/x.png", w: 60 }\n  A-->B',
    # giro 2: URL protocol-relative e apici singoli
    'flowchart LR\n  A@{ img: "//interno/x.png" } --> B',
    "flowchart LR\n  A@{ 'img': 'http://interno/x.png' } --> B",
)


@pytest.mark.parametrize("code", MERMAID_HISTORICAL_VECTORS)
def test_the_gate_never_reopens_a_vector_a_previous_round_rejected(code: str):
    """Unione, mai scambio: la regola di metodo del giro 5. Il confronto
    completo con gli alberi `939f0a5` e `6c3e067` sui corpora dei
    verificatori sta in sezione 14.8; questa è la sua parte eseguibile."""
    assert frs.REGISTRY["mermaid"].validate(code)[0] is False, code


# Falsi positivi AGGIUNTI dal giro 5, tutti misurati sul pre-render (i
# sorgenti si rendono davvero) e dichiarati in sezione 15. Sono il prezzo
# dell'unione: la scansione `img:`/`icon:` sul blocco grezzo non distingue
# una chiave da una label che ne parla, e la segmentazione fedele a YAML
# spezza dove le parentesi tonde non proteggono.
MERMAID_GIRO5_FALSE_POSITIVES = (
    'flowchart LR\n  A@{ label: "img: la sorgente" } --> B',
    'flowchart LR\n  A@{ shape: rect, label: "icon: la sua icona" } --> B',
    "flowchart LR\n  A@{ label: f(x, y) } --> B",
    "stateDiagram-v2\n  [*] --> A\n  note right of A\n    click qui\n  end note",
)


@pytest.mark.parametrize("code", MERMAID_GIRO5_FALSE_POSITIVES)
def test_the_false_positives_added_by_the_union_are_declared(code: str):
    """Non nascosti dal codice: ognuno prende un 422 esplicito, con la
    ragione nel messaggio. Le forme equivalenti che NON li innescano stanno
    nel test successivo."""
    ok, err = frs.REGISTRY["mermaid"].validate(code)
    assert ok is False, code
    assert err.startswith("mermaid_type_not_allowed"), err


@pytest.mark.parametrize(
    "code",
    [
        # Le stesse frasi fuori da un blocco `@{ … }`, o con le virgolette
        # al posto giusto, passano: il costo si evita riscrivendo la label.
        'flowchart LR\n  A["img: sorgente"] --> B["icon: simbolo"]',
        'flowchart LR\n  A@{ label: "f(x, y)", shape: rect } --> B',
        "stateDiagram-v2\n  [*] --> A\n  note right of A: click qui",
    ],
)
def test_the_equivalent_healthy_forms_still_pass(code: str):
    assert frs.REGISTRY["mermaid"].validate(code) == (True, "")


def test_a_quoted_at_brace_in_a_label_is_a_declared_false_positive():
    """Limite dichiarato in sezione 15: `_mermaid_shape_blocks` considera
    shape OGNI `@{` del corpo, anche uno citato dentro una label, perché
    seguire le virgolette di primo livello farebbe sparire il gate su un
    sorgente con una virgoletta non chiusa. Con la lista chiusa (giro 4)
    basta una chiave sconosciuta per il 422, dove prima serviva un URL:
    è l'unico esito che cambia su contenuto sano, ed è fissato qui."""
    assert frs.REGISTRY["mermaid"].validate('flowchart LR\n  A["Sintassi: @{"] --> B')[0] is False
    assert frs.REGISTRY["mermaid"].validate('flowchart LR\n  A["insieme @{a}"] --> B')[0] is False
    # Con una chiave della lista il sorgente passa, `@{` citato compreso.
    passa = 'flowchart LR\n  A["esempio @{shape: rect}"] --> B'
    assert frs.REGISTRY["mermaid"].validate(passa) == (True, "")


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
    monkeypatch.setattr(
        frs,
        "_prerender_mermaid_batch_sync",
        lambda codes: [mp.MermaidPrerender(s, None) for s in svgs],
    )
    out = frs.REGISTRY["mermaid"].render_svg_batch(
        ["a", "b", "c", "d"], asset_ids=["A", "B", "C", "D"]
    )
    assert out[:3] == [None, None, None]
    assert out[3] == "<svg><g>ok</g></svg>"


@pytest.mark.parametrize(
    ("svg", "rifiutato"),
    [
        ('<svg><a xlink:href="http://interno/x">t</a></svg>', True),
        ('<svg><a href="https://interno/x" target="_blank">t</a></svg>', True),
        ("<svg><a href=http://interno/x>t</a></svg>", True),  # valore non quotato
        ('<svg><a xlink:href="  http://interno/x">t</a></svg>', True),
        # Un ancoraggio interno non punta fuori: resta.
        ('<svg><a href="#nodo1">t</a></svg>', False),
        # `<animate>` comincia per `a` ma non è un `<a>`: lo prende la regola
        # degli elementi, non questa (qui la scansione non deve confondersi).
        ('<svg><g class="a href=x">t</g></svg>', False),
        ('<svg><text>vedi a href="http://interno/x"</text></svg>', False),
    ],
)
def test_svg_scan_catches_an_external_anchor(svg: str, rifiutato: bool):
    """SEC-1 (giro 5): `<a xlink:href="http://…">` è il costrutto che
    `click`, `link` e `links` producono. Non è una GET immediata, ma è un
    collegamento verso l'host scelto dall'autore dentro il PDF consegnato e
    dentro la vista lezione. Fino al giro 4 la scansione non lo cercava,
    delegando al gate degli statement — e `stateDiagram` ci passava."""
    trovato = frs._svg_external_ref(svg)
    assert (trovato is not None) is rifiutato, trovato


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
    monkeypatch.setattr(
        frs,
        "_prerender_mermaid_batch_sync",
        lambda codes: [mp.MermaidPrerender(s, None) for s in svgs],
    )
    out = frs.REGISTRY["mermaid"].render_svg_batch(["a", "b", "c"], asset_ids=["A", "B", "C"])
    assert out == svgs


def test_mermaid_figure_batch_keeps_measured_metrics_or_falls_back_with_a_warning(
    monkeypatch: pytest.MonkeyPatch,
):
    """`render_figure_batch` porta le metriche misurate nella pagina accanto
    all'SVG (D10); senza misura le legge dagli attributi/regola radice e lo
    dice (`mermaid_font_measure_missing`); `render_svg_batch` è la
    proiezione `.svg`."""
    root = '<svg id="m" width="100%" viewBox="0 0 10 10"><style>#m{font-size:14px}</style>'
    svgs = [root + "<text>a</text></svg>", "<svg><text>b</text></svg>"]
    measured = SvgMetrics(16.0, 16.0, 3, "measured")
    monkeypatch.setattr(
        frs,
        "_prerender_mermaid_batch_sync",
        lambda codes: [mp.MermaidPrerender(svgs[0], measured), mp.MermaidPrerender(svgs[1], None)],
    )
    with structlog.testing.capture_logs() as logs:
        out = frs.REGISTRY["mermaid"].render_figure_batch(["a", "b"], asset_ids=["A", "B"])
    assert out[0] == frs.RenderedFigure(svgs[0], measured)
    assert out[1] is not None and out[1].svg == svgs[1]
    assert out[1].metrics == SvgMetrics(None, None, 1, "unresolved")
    missing = [e for e in logs if e["event"] == "mermaid_font_measure_missing"]
    assert [(e["asset_id"], e["source"]) for e in missing] == [("B", "unresolved")]
    assert frs.REGISTRY["mermaid"].render_svg_batch(["a", "b"], asset_ids=["A", "B"]) == svgs


@needs_dot
@needs_vl
def test_registry_svgs_expose_the_theme_font():
    """Sentinella sui renderer reali: il corpo del testo si legge dagli
    attributi degli SVG normalizzati (DOT 10 uu = pt × 4/3, Vega-Lite 11 px,
    matplotlib 9 pt → 12 px). Un cambio di canale del tema o del
    normalizzatore cadrebbe in `unresolved` e farebbe fallire il test."""
    dot = frs.REGISTRY["dot"].render_svg('digraph { a -> b; b -> c [label="arco"] }')
    assert dot
    box = svg_intrinsic_box(dot)
    metrics = svg_base_font_px(dot)
    assert box is not None and box.px_per_unit == pytest.approx(4 / 3, abs=1e-3)
    assert metrics.source == "parsed" and metrics.text_count >= 4
    assert metrics.font_px_min == pytest.approx(10 * box.px_per_unit)  # etichetta d'arco
    vl = frs.REGISTRY["vegalite"].render_svg(_BAR_JSON)
    assert vl
    box = svg_intrinsic_box(vl)
    metrics = svg_base_font_px(vl)
    assert box is not None and box.px_per_unit == 1.0
    assert (metrics.font_px_min, metrics.source) == (11.0, "parsed")
    spec = json.dumps(
        {
            "kind": "function_study",
            "expressions": [{"expr": "x**2 - 1"}],
            "domain": [-3, 3],
            "show": ["zeros"],
        }
    )
    fn = frs.REGISTRY["function"].render_svg(spec)
    assert fn
    box = svg_intrinsic_box(fn)
    metrics = svg_base_font_px(fn)
    assert box is not None and box.px_per_unit == pytest.approx(4 / 3)
    assert metrics.source == "parsed"
    assert metrics.font_px_min == pytest.approx(12.0)  # 9 uu × 4/3: mai 8


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

    def _never(codes: list[str]) -> list[mp.MermaidPrerender | None]:
        calls.append(list(codes))
        return [None] * len(codes)

    monkeypatch.setattr(frs, "_prerender_mermaid_batch_sync", _never)
    frs.available_formats.cache_clear()
    assets = [{"asset_id": "M1", "format": "mermaid", "content": "```mermaid\n```"}]
    assert await frs.render_svg_map(assets, language="it") == {}
    assert calls == []


class _MeasuredRenderer(_FakeRenderer):
    """Renderer che espone `render_figure_batch` (metriche accanto all'SVG)."""

    def render_figure_batch(
        self, contents: list[str], *, asset_ids: list[str]
    ) -> list[frs.RenderedFigure | None]:
        self.batches.append(list(contents))
        return [
            frs.RenderedFigure(f"<svg>{self.fmt}:{c}</svg>", SvgMetrics(16.0, 16.0, 2, "measured"))
            for c in contents
        ]


async def test_render_figure_map_keeps_metrics_and_render_svg_map_projects(
    monkeypatch: pytest.MonkeyPatch,
):
    """Dispatch con `getattr`: un renderer senza `render_figure_batch` passa
    per `RenderedFigure.from_svg` (metriche parsate), uno che lo espone
    consegna il record tale e quale; la cache tiene il record
    (`_cache_get_figure`) e `_cache_get` resta la stringa; `render_svg_map`
    è la proiezione `.svg` e il secondo giro viene dalla cache."""
    plain = _FakeRenderer("dot")
    measured = _MeasuredRenderer("vegalite")
    monkeypatch.setitem(frs.REGISTRY, "dot", plain)
    monkeypatch.setitem(frs.REGISTRY, "vegalite", measured)
    frs.available_formats.cache_clear()
    text = '<text font-size="9">x</text>'
    assets = [
        {"asset_id": "D1", "format": "dot", "content": text},
        {"asset_id": "V1", "format": "vegalite", "content": "s1"},
    ]
    figs = await frs.render_figure_map(assets, language="it")
    assert set(figs) == {"D1", "V1"}
    assert figs["D1"].svg == f"<svg>dot:{text}</svg>"
    assert figs["D1"].metrics == SvgMetrics(9.0, 9.0, 1, "parsed")
    assert figs["V1"] == frs.RenderedFigure(
        "<svg>vegalite:s1</svg>", SvgMetrics(16.0, 16.0, 2, "measured")
    )
    key_v = frs.cache_key("vegalite", "s1")
    cached = frs._cache_get_figure(key_v)
    assert cached is not None and cached.metrics is not None
    assert cached.metrics.source == "measured"
    assert frs._cache_get(key_v) == "<svg>vegalite:s1</svg>"
    key_d = frs.cache_key("dot", text)
    cached_d = frs._cache_get_figure(key_d)
    assert cached_d is not None and cached_d.metrics is not None
    assert cached_d.metrics.source == "parsed"
    assert await frs.render_svg_map(assets, language="it") == {k: v.svg for k, v in figs.items()}
    assert plain.batches == [[text]] and measured.batches == [["s1"]]  # dalla cache
    # Una stringa messa in cache a mano continua a funzionare (contratto storico).
    frs._cache_put(key_d, "1")
    assert frs._cache_get(key_d) == "1"
    one = frs._cache_get_figure(key_d)
    assert one is not None and one.metrics == SvgMetrics(None, None, 0, "no_text")


# ---------------------------------------------------------------------------
# D15: il riconoscimento della catena legge il sorgente SANIFICATO
# ---------------------------------------------------------------------------

# Catena orizzontale di dodici nodi, lo stesso caso del docente ridotto ai
# soli id: il riconoscimento non guarda le etichette.
_CHAIN_LR = "flowchart LR\n" + "\n".join(f"    N{i} --> N{i + 1}" for i in range(11))
# SVG largo e basso come quello che il motore dà per quella catena
# (2923 × 62 uu): nel box della dispensa il corpo cade a 2,28 pt, sotto il
# pavimento della banda, quindi la figura è candidata alla variante.
_WIDE_SVG = (
    '<svg viewBox="0 0 2923 62" width="2923" height="62"><text font-size="14">x</text></svg>'
)


@pytest.mark.asyncio
async def test_the_chain_is_recognised_on_the_source_the_renderer_draws(
    monkeypatch: pytest.MonkeyPatch,
):
    """Il candidato alla variante si decide sul sorgente SANIFICATO, cioè
    quello che `render_figure_map` rende davvero.

    Le tre righe spurie qui sotto passano il gate statico del salvataggio
    (`mermaid_static_gate` → `("", "")`) e la sanificazione le toglie: sul
    grezzo il riconoscimento le leggeva come nodi e rinunciava, così la
    pagina teneva la catena orizzontale illeggibile mentre la vista web,
    che sanifica prima, ribaltava."""
    sources = {
        "pulita": _CHAIN_LR,
        "fence": _CHAIN_LR + "\n```",
        "segnaposto": _CHAIN_LR.replace("    N0 --> N1", "    all\n    N0 --> N1", 1),
        "controllo": _CHAIN_LR.replace("    N0 --> N1", "    N0 --> N1\x01", 1),
    }
    renderer = frs.REGISTRY["mermaid"]
    for name, source in sources.items():
        assert frs.mermaid_static_gate(renderer.sanitize(source)) == ("", ""), name
    assets = [{"asset_id": k, "format": "mermaid", "content": v} for k, v in sources.items()]
    figures = {k: frs.RenderedFigure.from_svg(_WIDE_SVG) for k in sources}

    seen: list[list[Mapping[str, Any]]] = []

    async def _fake_map(items: Any, *, language: str) -> dict[str, frs.RenderedFigure]:
        seen.append(list(items))
        return {}

    monkeypatch.setattr(frs, "render_figure_map", _fake_map)
    await frs.render_chain_variants(
        assets, figures, box_mm=(168.0, 242.0), variant="lesson", language="it"
    )
    assert len(seen) == 1
    assert [c["asset_id"] for c in seen[0]] == list(sources)
    # Tutte e quattro danno la STESSA variante verticale: la sanificazione
    # le riporta allo stesso sorgente, quindi la resa in più si paga una
    # volta sola (una chiave di cache sola).
    flipped = "flowchart TB\n" + "\n".join(f"    N{i} --> N{i + 1}" for i in range(11))
    assert {c["content"] for c in seen[0]} == {flipped}
