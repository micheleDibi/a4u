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
from app.services.figure_render_service import REGISTRY
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
