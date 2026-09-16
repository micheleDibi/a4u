"""Mermaid 11 (WP1), verifiche offline.

- pin unico `settings.mermaid_cdn_version` nella pagina di pre-render e in
  quella del validatore, `htmlLabels: false` al livello top in entrambe;
- re-export dei nomi storici da `course_lesson_pdf_service` (identità);
- L2 della regressione zero: `_strip_mermaid_max_width` byte-identico sulla
  fixture 10.9.4; la fixture 11.17.2 porta ancora `max-width: <px>` (la
  regex resta necessaria) e nessun `<foreignObject>`;
- sanitizer del sorgente, tipi ammessi in `openai_image_to_mermaid_service`,
  vincoli Mermaid 11 nei prompt di fix e di conversione.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.services import asset_validation_service as avs
from app.services import figure_theme as theme
from app.services import mermaid_prerender as mp
from app.services import openai_asset_fix_service as fix
from app.services import openai_image_to_mermaid_service as i2m

_FIXTURES = Path(__file__).parent / "fixtures"
_INIT_RE = re.compile(r"mermaid\.initialize\((\{.*?\})\);", re.S)


def _initialize_config(html: str) -> dict:
    m = _INIT_RE.search(html)
    assert m, "mermaid.initialize assente"
    return json.loads(m.group(1))


# ---------------------------------------------------------------------------
# Pin e inizializzazione
# ---------------------------------------------------------------------------


def test_renderer_and_validator_share_the_setting_pin():
    version = get_settings().mermaid_cdn_version
    url = f"https://cdn.jsdelivr.net/npm/mermaid@{version}/dist/mermaid.esm.min.mjs"
    for html in (mp._MERMAID_RENDERER_HTML, avs._VALIDATOR_HTML):
        assert url in html
        assert "10.9.4" not in html
        assert "__MERMAID_" not in html  # nessun segnaposto residuo
    assert version.startswith("11.")


def test_renderer_and_validator_initialize_from_figure_theme():
    renderer = _initialize_config(mp._MERMAID_RENDERER_HTML)
    validator = _initialize_config(avs._VALIDATOR_HTML)
    for cfg in (renderer, validator):
        assert cfg["htmlLabels"] is False
        assert cfg["startOnLoad"] is False
        assert cfg["securityLevel"] == "loose"
        assert cfg["theme"] == "neutral"
        assert cfg["themeVariables"]["fontFamily"] == theme.MERMAID_FONT_FAMILY
        assert cfg["flowchart"]["htmlLabels"] is False
    assert renderer["flowchart"]["useMaxWidth"] is True
    assert validator["flowchart"]["useMaxWidth"] is False
    assert renderer == theme.mermaid_config(use_max_width=True)
    assert validator == theme.mermaid_config(use_max_width=False)
    assert theme.MERMAID_FONT_FAMILY in mp._MERMAID_RENDERER_HTML  # body del mini-doc


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("https://cdn.jsdelivr.net/npm/mermaid@11.17.2/dist/mermaid.esm.min.mjs", True),
        ("https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.mjs", True),
        ("about:blank", True),
        ("data:image/png;base64,AAAA", True),
        ("blob:null/0-1", True),
        # SSRF: l'host scelto dall'autore di una figura, in ogni forma.
        ("http://127.0.0.1:8001/SSRF-PROBE.png", False),
        ("http://169.254.169.254/latest/meta-data/", False),
        ("file:///etc/hosts", False),
        ("https://esempio.invalid/x.png", False),
        # Sosia dell'origine ammessa: il prefisso comprende la barra finale.
        ("https://cdn.jsdelivr.net.esempio.invalid/x.js", False),
        ("https://CDN.JSDELIVR.NET/npm/mermaid/dist/mermaid.esm.min.mjs", True),
    ],
)
def test_prerender_network_isolation_allows_only_the_cdn(url: str, allowed: bool):
    """Difesa in profondità di SEC-1: la pagina headless può contattare solo
    il CDN da cui importa i moduli. Anche se un costrutto sfuggisse al gate
    statico, il server non eseguirebbe la richiesta verso l'host scelto."""
    assert mp.allows_prerender_url(url) is allowed


def test_all_headless_pages_install_the_network_guard():
    """Il pre-render Mermaid, il validatore e il pre-render MathJax
    (`course_lesson_pdf_service`) instradano le richieste PRIMA di
    caricare il contenuto della pagina."""
    pytest.importorskip("weasyprint")
    from app.services import course_lesson_pdf_service as pdf

    for source in (
        Path(mp.__file__).read_text(encoding="utf-8"),
        Path(avs.__file__).read_text(encoding="utf-8"),
        Path(pdf.__file__).read_text(encoding="utf-8"),
    ):
        guard = source.index("block_external_requests(page)")
        assert guard < source.index("await page.set_content(")
    # Se un giorno le pagine cambiassero CDN, la guardia le bloccherebbe:
    # ogni URL che caricano deve stare sotto il prefisso ammesso.
    for html in (mp._MERMAID_RENDERER_HTML, avs._VALIDATOR_HTML, pdf._MATHJAX_RENDERER_HTML):
        for url in re.findall(r"https?://[^'\"\s]+", html):
            assert mp.allows_prerender_url(url), url


def test_build_renderer_html_accepts_explicit_version():
    html = mp.build_mermaid_renderer_html(version="10.9.4")
    assert "mermaid@10.9.4/dist/mermaid.esm.min.mjs" in html
    assert "window.__renderMermaid" in html and "window.__mermaidReady" in html
    assert "suppressErrors: true" in html


def test_pdf_service_reexports_the_old_names():
    pytest.importorskip("weasyprint")
    from app.services import course_lesson_pdf_service as pdf

    assert pdf._strip_mermaid_max_width is mp._strip_mermaid_max_width
    assert pdf._sanitize_mermaid_code is mp._sanitize_mermaid_code
    assert pdf._prerender_mermaid_to_svg_batch is mp._prerender_mermaid_to_svg_batch
    assert pdf._prerender_mermaid_to_svg_batch_sync is mp._prerender_mermaid_to_svg_batch_sync
    assert pdf._prerender_mermaid_to_svg_batch_async is mp._prerender_mermaid_to_svg_batch_async
    assert pdf._MERMAID_RENDERER_HTML == mp._MERMAID_RENDERER_HTML
    assert pdf._MERMAID_MAX_WIDTH_RE is mp._MERMAID_MAX_WIDTH_RE
    assert pdf._MERMAID_JUNK_LINE_RE is mp._MERMAID_JUNK_LINE_RE


# ---------------------------------------------------------------------------
# L2: post-processing byte-identico
# ---------------------------------------------------------------------------


def test_strip_max_width_regex_is_the_historical_one():
    """La regex è quella di `course_lesson_pdf_service` pre-estrazione (10.9.4):
    stesso pattern, stesso flag. Il golden sotto è stato prodotto con essa."""
    assert mp._MERMAID_MAX_WIDTH_RE.pattern == r"max-width\s*:\s*[\d.]+px\s*;?"
    assert mp._MERMAID_MAX_WIDTH_RE.flags & re.IGNORECASE


def test_strip_max_width_is_byte_identical_on_v10_fixture():
    """L2 della regressione zero: SVG grezzo di Mermaid 10.9.4 (pagina di
    produzione pre-WP1) → post-processing byte-identico al golden
    `mermaid_v10_sample.stripped.svg`. L'SVG porta il `max-width` dell'attributo
    `style` della radice e quello del CSS `.edgeLabel`: entrambi rimossi, come
    sempre."""
    svg = (_FIXTURES / "mermaid_v10_sample.svg").read_text(encoding="utf-8")
    golden = (_FIXTURES / "mermaid_v10_sample.stripped.svg").read_text(encoding="utf-8")
    matches = [m.group(0) for m in mp._MERMAID_MAX_WIDTH_RE.finditer(svg)]
    assert matches and svg.startswith("<svg")
    assert all(m.lower().startswith("max-width") and m.rstrip(";").endswith("px") for m in matches)
    stripped = mp._strip_mermaid_max_width(svg)
    assert stripped == golden
    assert len(svg) - len(stripped) == sum(len(m) for m in matches)
    assert mp._MERMAID_MAX_WIDTH_RE.search(stripped) is None
    assert mp._strip_mermaid_max_width(stripped) == stripped  # idempotente
    assert "<foreignObject" not in svg  # flowchart 10.9.4 con htmlLabels:false


def test_v11_fixture_still_carries_max_width_and_no_foreignobject():
    """Fatto «da verificare in implementazione»: Mermaid 11.17.2 emette ancora
    `style="max-width: <px>px;"` sull'SVG, quindi `_strip_mermaid_max_width`
    non è un no-op."""
    svg = (_FIXTURES / "mermaid11_flowchart.svg").read_text(encoding="utf-8")
    m = mp._MERMAID_MAX_WIDTH_RE.search(svg)
    assert m is not None, "Mermaid 11 non emette più max-width: rivedere lo strip"
    assert "<foreignObject" not in svg
    assert "<text" in svg
    assert mp._MERMAID_MAX_WIDTH_RE.search(mp._strip_mermaid_max_width(svg)) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("max-width: 300px;", ""),
        # Lo spazio che segue il `;` resta: la regex consuma solo la dichiarazione.
        ('style="max-width: 300.5px; background: white"', 'style=" background: white"'),
        ("MAX-WIDTH:12px", ""),
        ("max-width: 100%;", "max-width: 100%;"),  # solo px: unità diverse restano
        ("", ""),
    ],
)
def test_strip_max_width_regex_cases(raw: str, expected: str):
    assert mp._strip_mermaid_max_width(raw) == expected


# ---------------------------------------------------------------------------
# A capo dentro i `<text>`: WeasyPrint li rimuove, Chromium li mostra
# ---------------------------------------------------------------------------


def test_text_newlines_become_a_single_space():
    """`sankey-beta` scrive nome e valore del nodo in UN solo `<text>`
    separati da un a capo letterale, senza `<tspan>`. Con `xml:space` di
    default la specifica SVG dice di RIMUOVERE i fine riga: WeasyPrint lo fa
    e nel PDF si legge «Lezioni48», Chromium è indulgente e mostra «Lezioni
    48». Il post-processing porta i due renderer a dire la stessa cosa."""
    svg = '<svg><text x="1" y="2" dy="0em">Lezioni\n48</text></svg>'
    unito = mp._join_mermaid_text_newlines(svg)
    assert unito == '<svg><text x="1" y="2" dy="0em">Lezioni 48</text></svg>'
    assert mp._join_mermaid_text_newlines(unito) == unito  # idempotente


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Nessun a capo: byte per byte lo stesso documento.
        ("<text>Lezioni 48</text>", "<text>Lezioni 48</text>"),
        # Più a capo e spazi accumulati collassano in uno solo.
        ("<text>a\n\n  b</text>", "<text>a b</text>"),
        # `<text>` con figli (`<tspan>`): gli a capo sono impaginazione del
        # documento, non testo, e non vanno toccati.
        ("<text>\n  <tspan>a</tspan>\n</text>", "<text>\n  <tspan>a</tspan>\n</text>"),
        ("", ""),
    ],
)
def test_join_text_newlines_cases(raw: str, expected: str):
    assert mp._join_mermaid_text_newlines(raw) == expected


def test_the_v10_and_v11_fixtures_are_untouched_by_the_newline_join():
    """Nessun tipo D8 oltre a sankey emette a capo dentro un `<text>`: sulle
    due fixture storiche il passaggio è un no-op byte per byte (L2)."""
    for name in ("mermaid_v10_sample.svg", "mermaid11_flowchart.svg"):
        svg = (_FIXTURES / name).read_text(encoding="utf-8")
        assert mp._join_mermaid_text_newlines(svg) == svg, name


# ---------------------------------------------------------------------------
# Sanitizer del sorgente
# ---------------------------------------------------------------------------


def test_sanitize_mermaid_code_removes_only_junk_lines():
    src = "```mermaid\nmermaid\nflowchart LR\n  A --> all\n  all[Etichetta]\nall:\n```\n"
    assert mp._sanitize_mermaid_code(src) == "flowchart LR\n  A --> all\n  all[Etichetta]"
    assert mp._sanitize_mermaid_code("") == ""
    assert mp._sanitize_mermaid_code("flowchart LR\n  A --> B") == "flowchart LR\n  A --> B"


# ---------------------------------------------------------------------------
# Tipi ammessi e prompt (D8, D10)
# ---------------------------------------------------------------------------


def test_image_to_mermaid_keywords_follow_figure_theme():
    assert i2m._MERMAID_KEYWORDS == theme.MERMAID_ALLOWED_TYPES
    for code in theme.MERMAID_D8_SAMPLES.values():
        assert i2m._is_valid_mermaid_keyword(code), code.split("\n", 1)[0]
    assert i2m._is_valid_mermaid_keyword("%%{init: {}}%%\ngraph TD\n A --> B")
    assert i2m._is_valid_mermaid_keyword("stateDiagram\n [*] --> A")
    for kind in (*theme.MERMAID_EXCLUDED_TYPES, "requirementDiagram", "C4Context"):
        assert not i2m._is_valid_mermaid_keyword(f"{kind}\n x"), kind


def _d8_types() -> list[str]:
    return [t for t in theme.MERMAID_ALLOWED_TYPES if t not in ("graph", "stateDiagram")]


@pytest.mark.parametrize("language", ["it", "en"])
def test_fix_prompt_targets_mermaid_11(language: str):
    prompt = fix._system_prompt("mermaid", language)
    assert "11.x" in prompt and "10.9" not in prompt and "neo look" not in prompt
    for kind in _d8_types():
        assert kind in prompt, kind
    for kind in theme.MERMAID_EXCLUDED_TYPES:
        assert kind in prompt, kind
    assert "%%{init: ...}%%" in prompt
    assert "<text>" in prompt
    assert len(_d8_types()) == 15


def test_image_to_mermaid_prompt_lists_allowed_types_and_label_rules():
    prompt = i2m._system_prompt("it")
    assert "Mermaid 11.x" in prompt
    for kind in _d8_types():
        assert kind in prompt, kind
    assert "Mai " + ", ".join(theme.MERMAID_EXCLUDED_TYPES) in prompt
    assert "%%{init: ...}%%" in prompt
    assert "italiano" in prompt and "inglese" in i2m._system_prompt("en-US")
