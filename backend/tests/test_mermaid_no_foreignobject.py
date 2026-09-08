"""D8 sull'output reale: Mermaid 11 (pin `settings.mermaid_cdn_version`) con
`htmlLabels: false` al livello top emette `<text>` puro — nessun
`<foreignObject>` — per tutti i quindici tipi ammessi, con la pagina di
rendering di produzione (`mermaid_prerender._prerender_mermaid_to_svg_batch_sync`).

Caso di controllo: `journey` emette `<foreignObject>` (anche in 10.9.4) ed è
escluso da `figure_theme.MERMAID_EXCLUDED_TYPES`; il gate statico del
registro dei renderer (WP2b) lo rifiuterà, qui si asserisce solo il
foreignObject e l'esclusione.

Richiede Playwright con Chromium e l'accesso a cdn.jsdelivr.net: in CI salta
con motivo esplicito, va eseguito in locale o nel container prima del merge.
Per rigenerare la fixture v11: `A4U_WRITE_FIXTURES=1 pytest tests/test_mermaid_no_foreignobject.py`.
"""

from __future__ import annotations

import os
import re
import socket
from pathlib import Path

import pytest

from app.services import figure_theme as theme
from app.services import mermaid_prerender as mp
from app.services.figure_render_service import REGISTRY, _svg_external_ref
from tests.test_figure_render_service import MERMAID_BR_LINE_BREAKS

_BR_CODE = "flowchart LR\n  A[Riga 1%sRiga 2] --> B"
_MERMAID_ID_RE = re.compile(r"mmd-\d+")


def theme_gate(code: str) -> tuple[bool, str]:
    return REGISTRY["mermaid"].validate(code)


def _strip_mermaid_id(svg: str) -> str:
    """Toglie l'id progressivo del batch (`mmd-0`, `mmd-1`, …), unico punto in
    cui due SVG della stessa figura resi in posizioni diverse differiscono."""
    return _MERMAID_ID_RE.sub("mmd-X", svg)


pytest.importorskip("playwright.sync_api")

_FIXTURE_V11 = Path(__file__).parent / "fixtures" / "mermaid11_flowchart.svg"
_JOURNEY = "journey\n  title Percorso\n  section Inizio\n    Passo: 5: Studente"


@pytest.fixture(scope="module")
def rendered() -> dict[str, str | None]:
    try:
        socket.create_connection(("cdn.jsdelivr.net", 443), timeout=3).close()
    except OSError:
        pytest.skip("cdn.jsdelivr.net non raggiungibile")
    kinds = [*theme.MERMAID_D8_SAMPLES, "journey"]
    codes = [*theme.MERMAID_D8_SAMPLES.values(), _JOURNEY]
    try:
        svgs = mp._prerender_mermaid_to_svg_batch_sync(codes)
    except Exception as exc:  # launch o rete: verifica locale, non gate CI
        pytest.skip(f"Chromium o CDN non disponibili: {exc!r}"[:300])
    if all(s is None for s in svgs):
        pytest.skip("pagina di rendering non pronta (__mermaidReady) o CDN non caricata")
    return dict(zip(kinds, svgs, strict=True))


@pytest.mark.parametrize("kind", list(theme.MERMAID_D8_SAMPLES))
def test_d8_samples_render_as_text_without_foreignobject(rendered, kind: str):
    svg = rendered[kind]
    assert svg is not None, kind
    assert "<foreignObject" not in svg, kind
    assert "<text" in svg, kind


@pytest.mark.parametrize("kind", list(theme.MERMAID_D8_SAMPLES))
def test_the_svg_scan_has_no_false_positive_on_the_d8_samples(rendered, kind: str):
    """Controprova della scansione dell'SVG, allargata agli `<a href>`
    esterni nel giro 5: nessuno dei quindici campioni D8 reso dal
    pre-render la fa scattare, quindi allargarla non fa sparire figure sane
    dall'export."""
    svg = rendered[kind]
    assert svg is not None, kind
    assert _svg_external_ref(svg) is None, kind


def test_br_in_a_label_is_a_line_break_not_html(rendered):
    """`<br>` è sintassi di Mermaid (`lineBreakRegex`), non HTML: 11.17.2 lo
    rende in due `tspan.row` senza `<foreignObject>` e senza lasciarlo in
    chiaro nel `<text>`. Il gate lo accetta (REG-1)."""
    code = "flowchart LR\n  A[Riga 1<br/>Riga 2] --> B[Riga 3<br>Riga 4]"
    assert theme_gate(code) == (True, "")
    svg = mp._prerender_mermaid_to_svg_batch_sync([code])[0]
    assert svg is not None, "render non disponibile"
    assert "<foreignObject" not in svg
    assert "&lt;br" not in svg and "<br" not in svg
    # Due righe per nodo: `<tspan class="text-outer-tspan row">` per riga
    # (Mermaid spezza poi ogni riga in un `tspan` per parola).
    assert svg.count('class="text-outer-tspan row"') >= 4, svg[:400]
    for needle in ("Riga", " 1", " 2", " 3", " 4"):
        assert f">{needle}</tspan>" in svg, needle


@pytest.fixture(scope="module")
def br_rendered() -> dict[str, str]:
    """Rende `A[Riga 1<TOKEN>Riga 2] --> B` per ogni forma `<br…>` misurata."""
    try:
        socket.create_connection(("cdn.jsdelivr.net", 443), timeout=3).close()
    except OSError:
        pytest.skip("cdn.jsdelivr.net non raggiungibile")
    codes = [_BR_CODE % token for token in MERMAID_BR_LINE_BREAKS]
    try:
        svgs = mp._prerender_mermaid_to_svg_batch_sync(codes)
    except Exception as exc:  # launch o rete: verifica locale, non gate CI
        pytest.skip(f"Chromium o CDN non disponibili: {exc!r}"[:300])
    if all(s is None for s in svgs):
        pytest.skip("pagina di rendering non pronta (__mermaidReady) o CDN non caricata")
    return dict(zip(MERMAID_BR_LINE_BREAKS, svgs, strict=True))


@pytest.mark.parametrize("token", MERMAID_BR_LINE_BREAKS)
def test_mermaid_br_forms_render_as_a_line_break(br_rendered, token: str):
    """REG-1 (giro 3): l'insieme delle forme che Mermaid 11.17.2 rende come a
    capo è un SOPRAINSIEME di `lineBreakRegex = /<br\\s*\\/?>/gi`, perché la
    label passa dal parser HTML prima di quella regex: `<br/ >`, `<br / >` e
    `</br>` arrivano già normalizzati in `<br>`. Oracolo reale, non una
    regex: si rende ogni forma e si confronta l'SVG con quello di `<br>`
    (identico a meno dell'id `mmd-N` assegnato dal batch). Il gate deve
    accettarle tutte — il giro 2 ne rifiutava nove con un 422 su un sorgente
    che si rendeva correttamente."""
    svg = br_rendered[token]
    assert svg is not None, f"render non disponibile per {token!r}"
    atteso = _strip_mermaid_id(br_rendered["<br>"] or "")
    assert _strip_mermaid_id(svg) == atteso, token
    assert theme_gate(_BR_CODE % token) == (True, ""), token


def test_mermaid_br_with_an_attribute_is_not_a_line_break(br_rendered):
    """Controprova dell'insieme misurato: `<br x>` NON è un a capo (il parser
    lo serializza in chiaro come `<br x="">` dentro la label), quindi il gate
    deve continuare a rifiutarlo."""
    codes = [_BR_CODE % '<br class="x">']
    svg = mp._prerender_mermaid_to_svg_batch_sync(codes)[0]
    assert svg is not None, "render non disponibile"
    assert _strip_mermaid_id(svg) != _strip_mermaid_id(br_rendered["<br>"] or "")
    assert "&lt;br" in svg
    ok, err = theme_gate(codes[0])
    assert ok is False and "HTML nelle label" in err, err


# SEC-1 (giro 4) — oracolo di rendering delle vie che portano una risorsa
# esterna nell'SVG, per non gattare forme innocue: ognuna di queste, PRIMA
# del giro 4, passava il gate statico, `mermaid.parse` e il PATCH.
# La shape `img` usa un data URI: con un URL http la guardia di rete del
# pre-render annulla la richiesta e Mermaid non emette nulla, mentre il
# data URI è inerte (`allows_prerender_url`) e prova comunque che la chiave
# scritta `"\x69mg"` arriva a js-yaml come `img`. Gli statement usano un
# host riservato `.invalid`: la guardia annulla la richiesta ma l'elemento
# resta nell'SVG, che è esattamente quello che finirebbe nel browser di chi
# apre la lezione.
_PIXEL_DATA_URI = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAA"
    "DUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
_INVALID_URL = "http://esempio.invalid/icona.png"
_ANCHOR = f'<a xlink:href="{_INVALID_URL}"'
_SEQ_HEAD = "sequenceDiagram\n  participant A\n  "
_EXTERNAL_RESOURCE_SOURCES: dict[str, tuple[str, str]] = {
    # nome -> (sorgente, frammento atteso nell'SVG reso)
    "shape_img_escaped": (
        'flowchart LR\n  A@{ "\\x69mg": "' + _PIXEL_DATA_URI + '", w: 20, h: 20 }\n  A --> B',
        "<image",
    ),
    "shape_img_plain": (
        'flowchart LR\n  A@{ img: "' + _PIXEL_DATA_URI + '", w: 20, h: 20 }\n  A --> B',
        "<image",
    ),
    "seq_properties_icon": (
        _SEQ_HEAD + 'properties A: {"icon": "' + _INVALID_URL + '"}\n  A->>A: x',
        f'<image x="130" y="171" xlink:href="{_INVALID_URL}"',
    ),
    "seq_links": (
        _SEQ_HEAD + 'links A: {"D": "' + _INVALID_URL + '"}\n  A->>A: x',
        _ANCHOR,
    ),
    "seq_link": (
        _SEQ_HEAD + "link A: D @ " + _INVALID_URL + "\n  A->>A: x",
        _ANCHOR,
    ),
    "class_link": (
        'classDiagram\n  class A\n  link A "' + _INVALID_URL + '" "t"',
        _ANCHOR,
    ),
    "class_click_href": (
        'classDiagram\n  class A\n  click A href "' + _INVALID_URL + '" "t"',
        _ANCHOR,
    ),
    "flowchart_click_href": (
        'flowchart LR\n  A --> B\n  click A href "' + _INVALID_URL + '" "t"',
        _ANCHOR,
    ),
    # --- giro 5: le vie che il giro 4 aveva riaperto o non vedeva
    "state_v2_click_href": (
        'stateDiagram-v2\n  [*] --> A\n  click A href "' + _INVALID_URL + '"',
        _ANCHOR,
    ),
    "state_v1_click_href": (
        'stateDiagram\n  [*] --> A\n  click A href "' + _INVALID_URL + '"',
        _ANCHOR,
    ),
    # A capo DENTRO la stringa: il lexer lo sostituisce con `<br/>`, quindi
    # js-yaml legge la forma flow e la chiave `img`, mentre il gate del
    # giro 4 leggeva il sorgente grezzo e vedeva la sola chiave `label`.
    "shape_img_after_newline_in_string": (
        'flowchart LR\n  A@{ label: "a\nb", img: "' + _PIXEL_DATA_URI + '", w: 20, h: 20 }\n'
        "  A --> B",
        "<image",
    ),
    # Parentesi tonda non bilanciata: profondità per lo splitter del gate,
    # scalare per js-yaml.
    "shape_img_after_open_paren": (
        'flowchart LR\n  A@{ label: ( , img: "' + _PIXEL_DATA_URI + '", w: 20, h: 20 }\n  A --> B',
        "<image",
    ),
}


@pytest.fixture(scope="module")
def external_resource_rendered() -> dict[str, str | None]:
    try:
        socket.create_connection(("cdn.jsdelivr.net", 443), timeout=3).close()
    except OSError:
        pytest.skip("cdn.jsdelivr.net non raggiungibile")
    names = list(_EXTERNAL_RESOURCE_SOURCES)
    codes = [_EXTERNAL_RESOURCE_SOURCES[n][0] for n in names]
    try:
        svgs = mp._prerender_mermaid_to_svg_batch_sync(codes)
    except Exception as exc:  # launch o rete: verifica locale, non gate CI
        pytest.skip(f"Chromium o CDN non disponibili: {exc!r}"[:300])
    if all(s is None for s in svgs):
        pytest.skip("pagina di rendering non pronta (__mermaidReady) o CDN non caricata")
    return dict(zip(names, svgs, strict=True))


@pytest.mark.parametrize("name", list(_EXTERNAL_RESOURCE_SOURCES))
def test_the_gate_rejects_sources_that_really_emit_an_external_resource(
    external_resource_rendered, name: str
):
    """SEC-1 (giro 4): ogni via chiusa dal gate è misurata sul renderer, non
    dedotta. Il sorgente si rende e l'SVG contiene davvero il riferimento
    esterno — quindi il 422 non è un falso positivo — e il gate lo rifiuta.

    Prima del giro 4 il gate era una lista di pattern TESTUALI sul blocco
    `@{ … }`, che Mermaid passa invece a js-yaml: `"\\x69mg"` è la chiave
    `img` per js-yaml e non lo è per una regex. Gli statement
    (`properties`, `links`, `link`, `click href`) non passavano da alcuna
    shape e nessun gate li guardava."""
    code, needle = _EXTERNAL_RESOURCE_SOURCES[name]
    svg = external_resource_rendered[name]
    assert svg is not None, f"render non disponibile per {name}"
    assert needle in svg, f"{name}: atteso {needle!r} nell'SVG reso"
    ok, err = theme_gate(code)
    assert ok is False, f"{name}: il gate accetta un sorgente che emette una risorsa esterna"
    assert "non caricano file né URL" in err, err


def test_a_plain_shape_still_renders_and_passes_the_gate():
    """Controprova della lista chiusa: una shape con le sole chiavi che
    Mermaid legge si rende e passa (nessun 422 su contenuto sano)."""
    code = 'flowchart LR\n  A@{ shape: rect, label: "Etichetta", w: 60, h: 40 } --> B'
    assert theme_gate(code) == (True, "")
    svg = mp._prerender_mermaid_to_svg_batch_sync([code])[0]
    assert svg is not None, "render non disponibile"
    assert "<image" not in svg and "Etichetta" in svg


def test_cjk_label_is_emitted_as_text(rendered):
    svg = rendered["mindmap"]
    assert svg is not None
    assert "定理" in svg and "<foreignObject" not in svg


def test_journey_is_excluded_because_it_emits_foreignobject(rendered):
    svg = rendered["journey"]
    assert svg is not None, "journey non renderizzato: impossibile verificare il controllo"
    assert "<foreignObject" in svg
    assert "journey" in theme.MERMAID_EXCLUDED_TYPES
    assert "journey" not in theme.MERMAID_ALLOWED_TYPES


def test_max_width_is_stripped_from_v11_output(rendered):
    """La pagina applica già `_strip_mermaid_max_width`: nessun `max-width:
    <px>` residuo. La presenza della dichiarazione nell'output grezzo di
    Mermaid 11.17.2 è documentata dalla fixture `mermaid11_flowchart.svg`."""
    for kind, svg in rendered.items():
        assert svg is not None
        assert mp._MERMAID_MAX_WIDTH_RE.search(svg) is None, kind


def test_v11_render_is_deterministic(rendered):
    """Due sessioni distinte, stesso sorgente e stesso id (`mmd-0`, primo del
    batch): SVG byte-identici e uguali a quello della fixture di modulo (dove
    il flowchart è anch'esso primo)."""
    code = theme.MERMAID_D8_SAMPLES["flowchart"]
    a = mp._prerender_mermaid_to_svg_batch_sync([code])[0]
    b = mp._prerender_mermaid_to_svg_batch_sync([code])[0]
    assert a is not None and a == b
    assert a == rendered["flowchart"]


def test_write_or_compare_v11_fixture(rendered):
    """Con `A4U_WRITE_FIXTURES=1` riscrive la fixture grezza (prima dello
    strip) per la versione corrente; altrimenti verifica che la fixture in
    repo, una volta strippata, coincida con l'output odierno."""
    if os.environ.get("A4U_WRITE_FIXTURES") == "1":
        raw = _render_raw_flowchart()
        _FIXTURE_V11.write_text(raw, encoding="utf-8")
        pytest.skip(f"fixture scritta: {_FIXTURE_V11}")
    if not _FIXTURE_V11.exists():
        pytest.skip("fixture mermaid11_flowchart.svg assente")
    fixture = _FIXTURE_V11.read_text(encoding="utf-8")
    assert mp._strip_mermaid_max_width(fixture) == rendered["flowchart"]


def _render_raw_flowchart() -> str:
    """SVG grezzo (senza `_strip_mermaid_max_width`) del campione flowchart,
    con la stessa pagina di produzione."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        try:
            page = browser.new_page()
            page.set_content(mp._MERMAID_RENDERER_HTML, wait_until="domcontentloaded")
            page.wait_for_function("window.__mermaidReady === true", timeout=15_000)
            svg = page.evaluate(
                "([id, code]) => window.__renderMermaid(id, code)",
                ["mmd-0", theme.MERMAID_D8_SAMPLES["flowchart"]],
            )
        finally:
            browser.close()
    assert isinstance(svg, str) and svg
    return svg
