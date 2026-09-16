"""Grammatica del math nel PDF delle lezioni (B3: WP0 D6, D7, D8-A; WP2 D6
punti 4-6, D8-A completo, D9, L11).

Test puri sulle due istanze markdown-it del servizio (`pdf._md_renderer`,
`pdf._md_inline_renderer`), su `render_lesson_html` e su
`render_slides_html` (stesso schema di `test_lesson_pdf_figures.py`):

- i quattro token di dollarmath (`math_inline`, `math_inline_double`,
  `math_block`, `math_block_label`) sono resi da un'unica rule nostra su
  ENTRAMBE le istanze; nessuno resta al default del plugin;
- flag del plugin (`_DOLLARMATH_OPTIONS`), posizione della core rule
  `math_currency_guard` (prima di `text_join`) e ordine delle rule inline
  (`math_inline` < `math_bsdelim` < `escape`; istanza zero = `text`,
  `math_inline`, `math_bsdelim`) pinnati;
- importi in dollari (`$50 e sale a $70`, `$50/$70`, `5$, 10$`, `US$50`)
  restano prosa byte-identica; un numero fra `$` resta math;
- `$$..$$` in frase, cella o titolo e' uno `<span>` (mai `<div>` dentro un
  `<p>`) e resta su una riga nel PDF; su righe proprie e' blocco via CSS;
- il fallback MathML e' loggato (`math_render_fallback` con `reason`) e
  contato (`MathSvgMap.misses`, summary `lesson_pdf_math_fallbacks` in
  dispensa, slide e video; `structlog.testing.capture_logs`: il logger
  dell'app e' un `PrintLoggerFactory`, `caplog` non lo vede);
- WeasyPrint NON rende il MathML: il testo estratto dal PDF lo appiattisce;
- frase currency e `$$..$$` in frase sopravvivono al testo del PDF;
- parita' collector/renderer per UGUAGLIANZA (`RecordingMap`), anche sulle
  slide (`_math_content_for_slides`);
- la fixture `fixtures/math_grammar_cases.json` pinna token e chiavi;
- la rule `math_bsdelim` rispetta fence, code span, citazioni `\\[1\\]`,
  tag `\\[FIG:x\\]` e il `posMax` dei link; nessun pre-processing testuale
  sopravvive (L11 chiuso anche sul corpo assemblato);
- `render_markdown_inline` senza math == `markupsafe.escape`; i campi
  inline (didascalie, titoli, label, punti chiave, citazioni) ricevono il
  math e restano escapati (D9);
- l'HTML di un esempio con fence e riga vuota resta UN html block;
- la pagina MathJax usa il pin `settings.mathjax_cdn_version` e la guardia
  di rete.
"""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path
from typing import Any

import markupsafe
import pytest
import structlog.testing

from app.core.config import get_settings
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.services import course_lesson_pdf_service as pdf
from app.services import course_lesson_slides_pdf_service as slides_pdf
from app.services import figure_markup
from app.services import lesson_slides_video_render_service as video
from app.services import mermaid_prerender as mp
from tests.test_lesson_pdf_figures import SVG_A, _figures, _pdf_text_and_warnings, _weasyprint

_PLUGIN_MODULE = "mdit_py_plugins.dollarmath.index"
_TOKEN_TYPES = ("math_inline", "math_inline_double", "math_block", "math_block_label")
_FIXTURES = Path(__file__).parent / "fixtures"

# Stub di un SVG MathJax: occupa spazio in riga (larghezza esplicita) ma non
# contiene testo, cosi' il testo estratto dal PDF resta quello della prosa.
STUB = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="12pt" height="8pt" viewBox="0 0 12 8">'
    '<rect width="12" height="8"/></svg>'
)
SVG_B = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20"><rect width="1" height="1"/></svg>'
)


class RecordingMap(dict):
    """Mappa «completa» col contratto `dict` dei chiamanti storici: registra
    ogni chiave cercata (`seen`, in ordine di rendering) e la rende con lo
    stub, cosi' nessuna formula ricade sul MathML."""

    def __init__(self) -> None:
        super().__init__()
        self.seen: list[tuple[str, str]] = []

    def __bool__(self) -> bool:  # il renderer interroga solo mappe non vuote
        return True

    def get(self, key: Any, default: Any = None) -> str:
        self.seen.append(key)
        return STUB


def _math_tokens(source: str) -> list[Any]:
    """Token math (di blocco e inline) dopo le core rule, guardia compresa."""
    return [
        t
        for tok in pdf._md_renderer.parse(source)
        for t in (tok, *(tok.children or []))
        if t.type.startswith("math_")
    ]


def _keys(source: str, mode: str) -> list[list[Any]]:
    """`[[tipo, [sorgente, display]], …]` dei token math di un testo, con
    l'istanza e il parse della modalita' (`block`: `_md_renderer.parse`,
    `inline`: `_md_inline_renderer.parseInline`), come fa il collector."""
    if mode == "inline":
        tokens = pdf._md_inline_renderer.parseInline(source, {})
    else:
        tokens = pdf._md_renderer.parse(source, {})
    return [[t.type, list(k)] for t in pdf._walk_tokens(tokens) if (k := pdf._token_math_key(t))]


def _render(content_raw: dict[str, Any], **kwargs: Any) -> str:
    return pdf.render_lesson_html(
        course=Course(title="Corso di prova", language_code="it", cfu=6),
        lesson=CourseLesson(lesson_code="M1.L1", title="Lezione di prova", content_raw=content_raw),
        organization=None,
        pdf_template=None,
        **kwargs,
    )


def _lines(text: str) -> list[str]:
    return [" ".join(line.split()) for line in text.splitlines() if line.strip()]


def _fallbacks(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in logs if e["event"] == "math_render_fallback"]


# ---------------------------------------------------------------------------
# T1 — le quattro rule di render sono nostre, su entrambe le istanze
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token_type", _TOKEN_TYPES)
def test_no_dollarmath_rule_is_left_to_the_plugin_default(token_type: str) -> None:
    """Prima del fix `math_inline_double` e `math_block_label` restavano al
    default del plugin: `<div class="math inline">` dentro un `<p>`, senza
    consultare la mappa SVG."""
    for md in (pdf._md_renderer, pdf._md_inline_renderer):
        rule = md.renderer.rules[token_type]
        assert rule.__module__ != _PLUGIN_MODULE, f"{token_type}: rule di default del plugin"


def test_all_four_dollarmath_rules_are_ours() -> None:
    for md in (pdf._md_renderer, pdf._md_inline_renderer):
        rules = md.renderer.rules
        ours = {t: rules[t].__func__ is pdf._render_math_token for t in _TOKEN_TYPES}
        assert ours == dict.fromkeys(_TOKEN_TYPES, True)
    assert pdf._md_inline_renderer.renderer.rules["text"].__func__ is pdf._render_text_token


# ---------------------------------------------------------------------------
# T2 — flag del plugin e ordine delle rule pinnati
# ---------------------------------------------------------------------------


def test_math_grammar_flags_and_rulers_are_pinned() -> None:
    assert pdf._DOLLARMATH_OPTIONS == {
        "allow_labels": False,
        "double_inline": True,
        "allow_space": False,
        "allow_digits": True,
    }
    for md in (pdf._md_renderer, pdf._md_inline_renderer):
        core = md.core.ruler.get_active_rules()
        assert "math_currency_guard" in core
        assert core.index("math_currency_guard") < core.index("text_join")
    inline = pdf._md_renderer.inline.ruler.get_active_rules()
    assert inline.index("math_inline") < inline.index("math_bsdelim") < inline.index("escape")
    assert pdf._md_inline_renderer.inline.ruler.get_active_rules() == [
        "text",
        "math_inline",
        "math_bsdelim",
    ]
    assert [t.type for t in _math_tokens("Sia $$E$$ qui.")] == ["math_inline_double"]
    assert _math_tokens("Sia $ x $ qui.") == []  # allow_space=False (L4)
    (tok,) = _math_tokens("la base 2$^{10}$")  # allow_digits=True
    assert (tok.type, tok.content) == ("math_inline", "^{10}")


# ---------------------------------------------------------------------------
# T3 — la guardia anti-currency declassa solo gli importi
# ---------------------------------------------------------------------------

_CURRENCY_PROSE = [
    "Il costo e $50 e sale a $70 al mese.",
    "$50/$70",
    "$5-$10",
    "5$/10$",
    "5$, 10$",
    "5$,10$",
    "US$50 e US$70",
    "50$-70$ euro",
    "5$-10$",
    "$2$3",
    "$50→$70",
    "$ x_0 $",
]

_MATH_CORPUS: list[tuple[str, list[tuple[str, str]]]] = [
    ("$0$", [("0", "inline")]),
    ("$-1$", [("-1", "inline")]),
    ("$3/4$", [("3/4", "inline")]),
    ("$2+2$", [("2+2", "inline")]),
    ("$5$", [("5", "inline")]),
    ("$15{,}9$", [("15{,}9", "inline")]),
    ("$0{,}866$", [("0{,}866", "inline")]),
    ("$2^{10}$", [("2^{10}", "inline")]),
    ("$10^{-3}$", [("10^{-3}", "inline")]),
    ("$1,5$", [("1,5", "inline")]),
    ("la base 2$^{10}$", [("^{10}", "inline")]),
    ("2$\\pi$", [("\\pi", "inline")]),
    ("$1$-$2$", [("1", "inline"), ("2", "inline")]),
    ("$x$2", [("x", "inline")]),
    ("$a$1", [("a", "inline")]),
    ("$30^\\circ$", [("30^\\circ", "inline")]),
]


@pytest.mark.parametrize("source", _CURRENCY_PROSE)
def test_currency_guard_keeps_dollar_amounts_as_prose(source: str) -> None:
    rec = RecordingMap()
    html = pdf.render_markdown(source, rec)
    assert html == f"<p>{source}</p>\n"
    assert 'class="math' not in html
    assert rec.seen == []
    # Stessa core rule sull'istanza inline (parseInline).
    assert pdf.render_markdown_inline(source, rec) == source
    assert rec.seen == []


@pytest.mark.parametrize(("source", "keys"), _MATH_CORPUS)
def test_currency_guard_keeps_numeric_formulas_as_math(
    source: str, keys: list[tuple[str, str]]
) -> None:
    rec = RecordingMap()
    html = pdf.render_markdown(source, rec)
    assert html.count('class="math-inline"') == len(keys)
    assert rec.seen == keys
    assert "$" not in html and "<math" not in html
    inline_rec = RecordingMap()
    inline = pdf.render_markdown_inline(source, inline_rec)
    assert inline.count('class="math-inline"') == len(keys)
    assert inline_rec.seen == keys


# ---------------------------------------------------------------------------
# T4 — `math_inline_double` e' uno span, mai un div
# ---------------------------------------------------------------------------


def test_math_inline_double_is_a_span_never_a_div() -> None:
    rec = RecordingMap()
    sentence = pdf.render_markdown("Sia $$E = mc^2$$ la relazione.", rec)
    assert sentence == f'<p>Sia <span class="math-inline">{STUB}</span> la relazione.</p>\n'
    cell = pdf.render_markdown("| a |\n|---|\n| $$x^2$$ |", rec)
    assert f'<td><span class="math-inline">{STUB}</span></td>' in cell
    heading = pdf.render_markdown("## Titolo $$y$$", rec)
    assert heading == f'<h2>Titolo <span class="math-inline">{STUB}</span></h2>\n'
    # Delimitatori su righe proprie senza riga vuota: blocco via CSS, dentro il `<p>`.
    tight = pdf.render_markdown("Testo.\n$$\nE = mc^2\n$$\nAltro.", rec)
    assert tight == f'<p>Testo.\n<span class="math-block">{STUB}</span>\nAltro.</p>\n'
    for html in (sentence, cell, heading, tight):
        assert 'class="math inline"' not in html
        assert '<div class="math' not in html
    block = pdf.render_markdown("Testo.\n\n$$\nE = mc^2\n$$", rec)
    assert block.startswith(f'<p>Testo.</p>\n<div class="math-block">{STUB}</div>')
    # `$$..$$` ha sempre chiave block: un solo SVG per formula, qualunque sia il markup.
    assert rec.seen == [
        ("E = mc^2", "block"),
        ("x^2", "block"),
        ("y", "block"),
        ("E = mc^2", "block"),
        ("E = mc^2", "block"),
    ]


def test_math_inline_double_in_a_sentence_stays_on_one_pdf_line() -> None:
    """Lo `<span class="math-inline">` resta in riga nel PDF; lo
    `<span class="math-block">` (delimitatori su righe proprie) va a capo
    per la regola `span.math-block { display: block; }` del template."""
    weasyprint = _weasyprint()
    content = {
        "introduction": "Sia $$E = mc^2$$ la relazione.\n\nTesto.\n$$\nE = mc^2\n$$\nAltro.",
        "sections": [],
        "summary": "",
    }
    html = _render(content, math_svg_map=RecordingMap())
    assert '<span class="math-block">' in html and '<div class="math' not in html
    _, text, warnings = _pdf_text_and_warnings(weasyprint, html)
    lines = _lines(text)
    assert any(line.startswith("Sia") and line.endswith("la relazione.") for line in lines), lines
    assert "Testo." in lines and "Altro." in lines, lines
    assert warnings == []


# ---------------------------------------------------------------------------
# T5 — fallback loggato con `reason` e contato (D7)
# ---------------------------------------------------------------------------


def test_math_render_fallback_is_logged_with_reason_and_counted() -> None:
    empty = pdf.MathSvgMap()
    with structlog.testing.capture_logs() as logs:
        html = pdf.render_markdown("$x$", empty)
    assert "<math" in html
    events = _fallbacks(logs)
    assert len(events) == 1
    assert events[0]["log_level"] == "warning"
    assert (events[0]["reason"], events[0]["display"], events[0]["latex"]) == (
        "svg_map_empty",
        "inline",
        "x",
    )
    assert empty.misses == [("x", "inline")]

    partial = pdf.MathSvgMap({("x", "inline"): SVG_B})
    with structlog.testing.capture_logs() as logs:
        html = pdf.render_markdown("$x$ e $y$", partial)
    assert html.count(SVG_B) == 1 and "<math" in html
    assert [(e["reason"], e["latex"]) for e in _fallbacks(logs)] == [("svg_missing", "y")]
    assert partial.misses == [("y", "inline")]

    with structlog.testing.capture_logs() as logs:
        pdf.render_markdown("$x$")  # nessuna mappa: chiamante storico
    assert [e["reason"] for e in _fallbacks(logs)] == ["svg_map_missing"]

    full = pdf.MathSvgMap({("x", "inline"): SVG_B, ("y", "inline"): SVG_B})
    with structlog.testing.capture_logs() as logs:
        html = pdf.render_markdown("$x$ e $y$", full)
    assert html.count(SVG_B) == 2 and "<math" not in html
    assert _fallbacks(logs) == []
    assert full.misses == []


def test_lesson_pdf_math_fallbacks_summary_is_one_event_per_lesson() -> None:
    svg_map = pdf.MathSvgMap(requested=9)
    with structlog.testing.capture_logs():
        pdf.render_markdown(" ".join(f"$x_{i}$" for i in range(7)), svg_map)
    assert len(svg_map.misses) == 7
    with structlog.testing.capture_logs() as logs:
        pdf._log_math_fallbacks(lesson_code="M1.L1", svg_map=svg_map)
    events = [e for e in logs if e["event"] == "lesson_pdf_math_fallbacks"]
    assert len(events) == 1
    event = events[0]
    assert event["log_level"] == "error"
    assert (event["lesson_code"], event["count"], event["requested"], event["rendered"]) == (
        "M1.L1",
        7,
        9,
        0,
    )
    assert event["sample"] == svg_map.misses[:5]
    # Nessun miss, nessun evento: anche con una mappa `dict` storica o assente.
    for quiet in (pdf.MathSvgMap({("x", "inline"): SVG_B}, requested=1), {}, None):
        with structlog.testing.capture_logs() as logs:
            pdf._log_math_fallbacks(lesson_code="M1.L1", svg_map=quiet)
        assert logs == []
    # Il summary e' cablato dopo il render: dispensa, PDF slide e video
    # (una riga per sito).
    for fn, render_name in (
        (pdf.materialize_lesson_pdf, "render_lesson_html"),
        (slides_pdf.materialize_lesson_slides_pdf, "render_slides_html"),
        (video.render_slides_to_png, "render_slides_html"),
    ):
        src = inspect.getsource(fn)
        assert src.index(render_name) < src.index("_log_math_fallbacks("), fn.__name__


async def test_prerender_math_for_lesson_returns_an_empty_math_svg_map() -> None:
    svg_map = await pdf._prerender_math_for_lesson({})
    assert isinstance(svg_map, pdf.MathSvgMap)
    assert svg_map == {} and svg_map.requested == 0 and svg_map.misses == []


# ---------------------------------------------------------------------------
# T6 — WeasyPrint non rende il MathML
# ---------------------------------------------------------------------------


def test_weasyprint_non_rende_mathml() -> None:
    """Il MathML di latex2mathml e' solo un fallback: WeasyPrint lo stampa
    piatto e in silenzio (apice e frazione perdono la struttura). Se una
    futura WeasyPrint rendesse il MathML questo test fallisce e le docstring
    di `_render_math_by_key`/`_convert_math_to_mathml` vanno riviste."""
    weasyprint = _weasyprint()
    mathml = pdf._convert_math_to_mathml("x^{2} + \\frac{a}{b}", display="inline")
    _, text, _ = _pdf_text_and_warnings(weasyprint, f"<p>Sia {mathml} la formula.</p>")
    assert "x2" in text and "ab" in text, text
    assert "<" not in text
    _, text_svg, _ = _pdf_text_and_warnings(weasyprint, f"<p>Sia {SVG_B} la formula.</p>")
    assert "x2" not in text_svg and "ab" not in text_svg
    block = pdf._convert_math_to_mathml("x", display="block")
    assert block.count("display=") == 1 and 'display="block"' in block


# ---------------------------------------------------------------------------
# T7 — prosa currency e `$$..$$` in frase nel testo del PDF
# ---------------------------------------------------------------------------


def test_currency_sentence_and_dollar_math_survive_to_pdf_text() -> None:
    weasyprint = _weasyprint()
    content = {
        "introduction": (
            "Il costo e $50 e sale a $70 al mese.\n\nRange tra $50-$70 e US$50 e US$70."
        ),
        "sections": [
            {
                "title": "Formule",
                "content": "Vale $x^2$ e $15{,}9$. Sia $$E = mc^2$$ la relazione.",
            }
        ],
        "summary": "",
    }
    rec = RecordingMap()
    html = _render(content, math_svg_map=rec)
    assert "<math" not in html
    _, text, warnings = _pdf_text_and_warnings(weasyprint, html)
    flat = " ".join(text.split())
    # Prima del fix: «Il costo e 50esalea70 al mese.» (MathML piatto).
    assert "Il costo e $50 e sale a $70 al mese." in flat
    assert "Range tra $50-$70 e US$50 e US$70." in flat
    assert re.search(r"\\[a-zA-Z]+|\$\$|\{,\}|\^\{|_\{", text) is None
    assert warnings == []
    assert rec.seen == [("x^2", "inline"), ("15{,}9", "inline"), ("E = mc^2", "block")]


# ---------------------------------------------------------------------------
# T8 — parita' collector/renderer per uguaglianza (dispensa e slide)
# ---------------------------------------------------------------------------


def _asset(asset_id: str, caption: str) -> dict[str, Any]:
    return {
        "asset_id": asset_id,
        "format": "mermaid",
        "content": "flowchart LR\n A --> B",
        "caption": caption,
        "alt_text": "",
    }


def _corpus_content() -> dict[str, Any]:
    """Lezione con TUTTI i campi resi: corpo (intro, sezioni con titolo,
    sintesi), tabelle, esempi, equazioni (semplice e teorema con
    dimostrazione), figure, punti chiave, riferimenti (dict e stringa)."""
    return {
        "introduction": (
            "Il costo e $50 e sale a $70 al mese. Sia $x_0$ dato e \\(\\beta\\) noto. "
            "Range tra $50-$70 e US$50 e US$70."
        ),
        "sections": [
            {
                "title": "Sezione su $\\alpha$ e \\(\\gamma\\)",
                "content": (
                    "Testo.\n\n$$\nE = mc^2\n$$\n\nAltro.\n$$\nF = ma\n$$\nFine. "
                    "Sia $$G = H$$ la relazione e \\[I = J\\] pure.\n\n"
                    "$$ a &= b \\\\ c &= d $$\n\n"
                    "Vale $15{,}9$, $0{,}866$, $30^\\circ$ e la base 2$^{10}$.\n\n"
                    '```python\nprint("a\\[0\\]") $x$\n```\n\n'
                    "Vedi [FIG:A], [TAB:t1], [EQ:e1], [EQ:thm] ed [EX:x1]."
                ),
            }
        ],
        "summary": "Sintesi con $\\zeta$ e $a [FIG:A] b$ a cavallo del tag.",
        "tables": [
            {
                "table_id": "t1",
                "caption": "Valori di $\\theta$",
                "markdown": "| a |\n|---|\n| $$x^2$$ |",
            }
        ],
        "examples": [
            {
                "example_id": "x1",
                "title": "Esempio con $\\epsilon$",
                "content": '```python\nx = 1\n\nprint("a\\[0\\]") $x$\n```\n\nTesto con $\\delta$.',
            }
        ],
        "equations": [
            {
                "equation_id": "e1",
                "latex": "x=1",
                "label": "Con $\\lambda$",
                "explanation": "Spiega $\\mu$.",
            },
            {
                "equation_id": "thm",
                "kind": "lemma",
                "latex": "y=2",
                "label": "Weierstrass $\\nu$",
                "statement": "Enunciato $\\xi$.",
                "proof": [{"latex": "z=3", "text": "Passo $\\pi$."}],
                "explanation": "Nota $\\rho$.",
            },
        ],
        "visual_assets": [_asset("A", "Figura 3: angolo $30^\\circ$ e $\\sigma$")],
        "key_takeaways": ["Vale $x^2$", "Vedi [FIG:A]", "Vale $a [FIG:A] b$ qui"],
        "references": [
            {"citation": "Rudin, W. (1976). *Principles*, $\\epsilon$-$\\delta$."},
            "Grezza $\\tau$",
        ],
    }


def test_collector_and_renderer_see_the_same_formulas() -> None:
    """Oracolo di parita': ogni chiave cercata dal renderer e' stata
    raccolta (nessun fallback) e ogni chiave raccolta e' cercata (nessuna
    formula sprecata). Fra WP0 e WP2 fallisce: `$15{,}9$`, `$0{,}866$`,
    le didascalie, i titoli, le label, i punti chiave e le citazioni non
    erano raccolti. `$a [FIG:A] b$` (sintesi e punto chiave) e' raccolto
    con il rimando gia' riscritto, come lo cerca il renderer."""
    content = _corpus_content()
    collected = set(pdf._collect_math_from_content(content))
    rec = RecordingMap()
    with structlog.testing.capture_logs() as logs:
        html = _render(content, visual_svg_map={"A": SVG_A}, math_svg_map=rec)
    assert set(rec.seen) == collected
    assert _fallbacks(logs) == []
    assert "<math" not in html and 'class="math-error"' not in html
    for key in (
        ("15{,}9", "inline"),
        ("0{,}866", "inline"),
        ("30^\\circ", "inline"),
        ("^{10}", "inline"),
        ("\\alpha", "inline"),
        ("\\gamma", "inline"),
        ("\\beta", "inline"),
        ("E = mc^2", "block"),
        ("F = ma", "block"),
        ("G = H", "block"),
        ("I = J", "block"),
        ("\\begin{aligned} a &= b \\\\ c &= d \\end{aligned}", "block"),
        ("x=1", "block"),
        ("y=2", "block"),
        ("z=3", "block"),
        ("\\theta", "inline"),
        ("x^2", "block"),
        ("\\epsilon", "inline"),
        ("\\delta", "inline"),
        ("\\lambda", "inline"),
        ("\\mu", "inline"),
        ("\\nu", "inline"),
        ("\\xi", "inline"),
        ("\\pi", "inline"),
        ("\\rho", "inline"),
        ("\\sigma", "inline"),
        ("x^2", "inline"),
        ("\\tau", "inline"),
        ("\\zeta", "inline"),
        ("a Figura 1 b", "inline"),
    ):
        assert key in collected, key
    # Il fence dell'esempio e quello della sezione non producono chiavi;
    # la formula a cavallo del tag non e' mai raccolta col tag grezzo.
    assert ("x", "inline") not in collected
    assert ("a [FIG:A] b", "inline") not in collected


def _straddling_content() -> dict[str, Any]:
    """Formula a cavallo di un tag asset nel corpo e nella coda, e una a
    cavallo dell'ancora su righe proprie (spezzata dal blocco)."""
    return {
        "introduction": "Sia $a [FIG:A] b$ nel corpo.\n\nTesto $c\n[FIG:A]\nd$ fine.",
        "sections": [],
        "summary": "",
        "visual_assets": [_asset("A", "cap")],
        "key_takeaways": ["Vale $a [FIG:A] b$ qui"],
        "references": [],
    }


@pytest.mark.parametrize(("language", "expected"), [("it", "a Figura 1 b"), ("en", "a Figure 1 b")])
def test_collector_parses_the_rewritten_citations_like_the_renderer(
    language: str, expected: str
) -> None:
    """Il collector vede il corpo e la coda DOPO `normalize_asset_refs` /
    `cite_asset_refs`, nella lingua del corso, e con le ancore sostituite
    da un HTML block: `$a [FIG:A] b$` e' la chiave `a Figura 1 b` (o
    `a Figure 1 b`) su entrambi i lati e la formula spezzata dall'ancora
    non produce chiavi. Con la mappa reale costruita dalle chiavi raccolte
    non c'e' alcun `math_render_fallback` ne' MathML nell'HTML."""
    content = _straddling_content()
    keys = pdf._collect_math_from_content(content, language=language)
    assert keys == [(expected, "inline")]
    svg_map = pdf.MathSvgMap(dict.fromkeys(keys, SVG_B), requested=len(keys))
    with structlog.testing.capture_logs() as logs:
        html = pdf.render_lesson_html(
            course=Course(title="Corso di prova", language_code=language, cfu=6),
            lesson=CourseLesson(lesson_code="M1.L1", title="L", content_raw=content),
            organization=None,
            pdf_template=None,
            visual_svg_map={"A": SVG_A},
            math_svg_map=svg_map,
        )
    assert svg_map.misses == [] and _fallbacks(logs) == []
    assert "<math" not in html and html.count('class="math-inline"') == 2
    assert "[FIG:A]" not in html and "Testo $c" in html and "d$ fine." in html


def _slides_corpus() -> tuple[dict[str, Any], dict[str, Any]]:
    content_raw = _corpus_content()
    slides_raw = {
        "slides": [
            {
                "slide_id": "s1",
                "type": "concept",
                "title": "Tutto",
                "bullets": [],
                "references_assets": ["A", "t1", "e1", "thm", "x1", "N1", "t2", "e2", "x2"],
            }
        ],
        "new_assets": [_asset("N1", "Nuova con $\\omega$")],
        "new_tables": [
            {"table_id": "t2", "caption": "Nuova $\\phi$", "markdown": "| $y$ |\n|---|\n| 1 |"}
        ],
        "new_equations": [{"equation_id": "e2", "latex": "w=4", "label": "Nuova $\\chi$"}],
        "new_examples": [{"example_id": "x2", "title": "Nuovo $\\psi$", "content": "Con $\\eta$."}],
    }
    return content_raw, slides_raw


def test_collector_and_renderer_see_the_same_formulas_in_slides() -> None:
    content_raw, slides_raw = _slides_corpus()
    content = slides_pdf._math_content_for_slides(content_raw, slides_raw)
    collected = set(pdf._collect_math_from_content(content))
    rec = RecordingMap()
    with structlog.testing.capture_logs() as logs:
        html = slides_pdf.render_slides_html(
            course=Course(title="Corso di prova", language_code="it", cfu=6),
            lesson=CourseLesson(
                lesson_code="M1.L1",
                title="Lezione di prova",
                content_raw=content_raw,
                slides_raw=slides_raw,
            ),
            organization=None,
            slide_template=None,
            visual_svg_map={"A": SVG_A, "N1": SVG_A},
            math_svg_map=rec,
            enable_split=False,
        )
    assert set(rec.seen) == collected
    assert _fallbacks(logs) == []
    assert "<math" not in html
    for key in (("\\omega", "inline"), ("\\phi", "inline"), ("y", "inline"), ("w=4", "block")):
        assert key in collected, key
    for key in (
        ("\\chi", "inline"),
        ("\\psi", "inline"),
        ("\\eta", "inline"),
        ("\\sigma", "inline"),
    ):
        assert key in collected, key


def test_misses_of_an_empty_map_equal_the_collected_keys() -> None:
    """Oracolo alternativo senza sottoclasse: con una `MathSvgMap` vuota i
    `misses` sono esattamente le chiavi raccolte."""
    content = _corpus_content()
    empty = pdf.MathSvgMap()
    with structlog.testing.capture_logs():
        _render(content, visual_svg_map={"A": SVG_A}, math_svg_map=empty)
    assert set(empty.misses) == set(pdf._collect_math_from_content(content))


# ---------------------------------------------------------------------------
# T9 — fixture della grammatica
# ---------------------------------------------------------------------------


def _grammar_cases() -> list[dict[str, Any]]:
    return json.loads((_FIXTURES / "math_grammar_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "case", _grammar_cases(), ids=[f"{c['mode']}:{c['markdown'][:28]!r}" for c in _grammar_cases()]
)
def test_grammar_tokens_match_fixture(case: dict[str, Any]) -> None:
    """Per ogni caso `{markdown, mode, expected}` i token math (tipo e
    chiave) sono quelli attesi; `html`/`html_contains` pinnano il markup
    (con `{svg}` = stub della mappa). Qualunque cambio di flag, guardia o
    rule fa fallire il confronto."""
    assert _keys(case["markdown"], case["mode"]) == case["expected"]
    rec = RecordingMap()
    if case["mode"] == "inline":
        html = pdf.render_markdown_inline(case["markdown"], rec)
    else:
        html = pdf.render_markdown(case["markdown"], rec)
    assert [list(k) for k in rec.seen] == [k for _t, k in case["expected"]]
    if "html" in case:
        assert html == case["html"].replace("{svg}", STUB)
    for needle in case.get("html_contains", []):
        assert needle.replace("{svg}", STUB) in html


def test_fixture_covers_both_modes_and_the_currency_corpus() -> None:
    cases = _grammar_cases()
    assert {c["mode"] for c in cases} == {"block", "inline"}
    markdowns = {c["markdown"] for c in cases}
    for prose in (_CURRENCY_PROSE[0], "US$50 e US$70", "5$, 10$ e US$50 e US$70"):
        assert prose in markdowns, prose
    currency = [c for c in cases if c["markdown"] in _CURRENCY_PROSE]
    assert len(currency) >= 3 and all(c["expected"] == [] for c in currency)


# ---------------------------------------------------------------------------
# T10 — la rule `math_bsdelim` rispetta fence, code span, citazioni e link
# ---------------------------------------------------------------------------


def test_bsdelim_rule_respects_fences_code_spans_citations_and_links() -> None:
    rec = RecordingMap()
    fence = pdf.render_markdown('```python\nprint("a\\[0\\]") \\(x\\)\n```', rec)
    assert (
        fence
        == '<pre><code class="language-python">print(&quot;a\\[0\\]&quot;) \\(x\\)\n</code></pre>\n'
    )
    tilde = pdf.render_markdown("~~~\n\\[0\\] \\(x\\)\n~~~", rec)
    assert tilde == "<pre><code>\\[0\\] \\(x\\)\n</code></pre>\n"
    span = pdf.render_markdown("`a\\[0\\]` e `\\(x\\)`", rec)
    assert span == "<p><code>a\\[0\\]</code> e <code>\\(x\\)</code></p>\n"
    cites = pdf.render_markdown("\\[1\\], \\[2, 3\\], \\[12\u201314\\], \\[FIG:x\\]", rec)
    assert cites == "<p>[1], [2, 3], [12\u201314], [FIG:x]</p>\n"
    escaped = pdf.render_markdown("\\\\(x\\\\)", rec)
    assert escaped == "<p>\\(x\\)</p>\n"
    paragraphs = pdf.render_markdown("para1 \\[a\n\nb\\] para2", rec)
    assert paragraphs == "<p>para1 [a</p>\n<p>b] para2</p>\n"
    assert rec.seen == [] and "math-" not in fence + tilde + span + cites + escaped + paragraphs
    span_html = f'<span class="math-inline">{STUB}</span>'
    link = pdf.render_markdown("[testo \\(x\\)](http://a.b) e poi \\)", rec)
    assert link == f'<p><a href="http://a.b">testo {span_html}</a> e poi )</p>\n'
    both = pdf.render_markdown("Sia \\(x\\) e \\[y\\] qui.", rec)
    assert both == f"<p>Sia {span_html} e {span_html} qui.</p>\n"
    heading = pdf.render_markdown("## Sezione \\(\\beta\\)", rec)
    assert heading == f'<h2>Sezione <span class="math-inline">{STUB}</span></h2>\n'
    assert rec.seen == [("x", "inline"), ("x", "inline"), ("y", "block"), ("\\beta", "inline")]


# ---------------------------------------------------------------------------
# T11 — nessun pre-processing testuale sopravvive (L11)
# ---------------------------------------------------------------------------


def test_no_textual_math_preprocessing_remains() -> None:
    """`_normalize_math_delimiters` era applicata due volte e riscriveva
    `print("a\\[0\\]")` in `a$$0$$` gia' al render del campo (esempio) e sul
    corpo assemblato (`<pre>` dell'esempio reiniettato): con la rule inline
    un html_block o un fence non producono token."""
    for name in (
        "_normalize_math_delimiters",
        "_LATEX_DISPLAY_BSBRACK_RE",
        "_LATEX_INLINE_BSPAREN_RE",
    ):
        assert not hasattr(pdf, name), name
    rec = RecordingMap()
    block = pdf._render_example_block(
        {"title": "T", "content": '```python\nprint("a\\[0\\]")\n```'}, math_svg_map=rec
    )
    assert "print(&quot;a\\[0\\]&quot;)" in block and "math-" not in block
    aside = '<aside class="example"><pre><code>print(&quot;a\\[0\\]&quot;) $x$</code></pre></aside>'
    html = pdf.render_markdown(f"Intro.\n\n{aside}\n\nFine.", rec)
    assert html == f"<p>Intro.</p>\n{aside}\n<p>Fine.</p>\n"
    assert rec.seen == []


# ---------------------------------------------------------------------------
# T12 — `render_markdown_inline` senza math == `markupsafe.escape`
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        'Schema <b>x</b> & "y"',
        "l'energia & *x* <b> _a_ [1] http://a.b/c \\$5",
        "Vedi [FIG:A]",
        "Rudin, W. (1976). *Principles*.",
        "5$, 10$",
        "Il costo e $50 e sale a $70 al mese.",
        "",
    ],
)
def test_render_markdown_inline_equals_markupsafe_escape_without_math(text: str) -> None:
    assert pdf.render_markdown_inline(text) == str(markupsafe.escape(text))


@pytest.mark.parametrize(
    ("text", "expected"),
    [("a\r\nb", "a\nb"), ("a\rb", "a\nb"), ("a\x00b", "a�b")],
)
def test_render_markdown_inline_keeps_the_markdown_it_input_normalization(
    text: str, expected: str
) -> None:
    """Le sole eccezioni all'identita' con `markupsafe.escape`: la core
    rule `normalize` di markdown-it (attiva anche nel preset `zero`)
    porta CRLF/CR a LF e NUL a U+FFFD, sull'istanza usata anche dal
    collector (stessa chiave su entrambi i lati)."""
    assert pdf.render_markdown_inline(text) == expected
    assert pdf.render_markdown_inline(text) != str(markupsafe.escape(text))


def test_render_markdown_inline_renders_only_math() -> None:
    rec = RecordingMap()
    assert pdf.render_markdown_inline("Angolo $30^\\circ$", rec) == (
        f'Angolo <span class="math-inline">{STUB}</span>'
    )
    assert pdf.render_markdown_inline("Sia $$E$$ qui", rec) == (
        f'Sia <span class="math-inline">{STUB}</span> qui'
    )
    assert rec.seen == [("30^\\circ", "inline"), ("E", "block")]
    assert "<p>" not in pdf.render_markdown_inline("Riga 1\n\nRiga 2 $x$", rec)


# ---------------------------------------------------------------------------
# T13 — D9: i campi inline ricevono il math e restano escapati
# ---------------------------------------------------------------------------


def test_inline_fields_receive_math_and_stay_escaped() -> None:
    content = {
        "introduction": "Vedi [FIG:A], [TAB:t1], [EQ:e1], [EQ:thm] ed [EX:x1].",
        "sections": [],
        "summary": "",
        "visual_assets": [_asset("A", 'Figura 7. Angolo $30^\\circ$ <b>x</b> & "y"')],
        "tables": [
            {
                "table_id": "t1",
                "caption": "Valori di $\\theta$ <i>",
                "markdown": "| a |\n|---|\n| 1 |",
            }
        ],
        "examples": [
            {"example_id": "x1", "title": "Esempio $\\epsilon$ & co", "content": "Testo."}
        ],
        "equations": [
            {"equation_id": "e1", "latex": "x=1", "label": 'Con $\\lambda$ "q"'},
            {
                "equation_id": "thm",
                "kind": "lemma",
                "latex": "y=2",
                "label": "Weierstrass $\\nu$ <b>",
                "statement": "Enunciato.",
            },
        ],
        "key_takeaways": ["Vale $x^2$", "Vedi [FIG:A]", 'Con "virgolette" & co'],
        "references": [{"citation": "Rudin $\\epsilon$"}, "Grezza $\\tau$"],
    }
    rec = RecordingMap()
    html = _render(content, visual_svg_map={"A": SVG_A}, math_svg_map=rec)
    span = f'<span class="math-inline">{STUB}</span>'
    fig = _figures(html)[0]
    assert fig["caption"] == (
        f'<span class="figure-label">Figura 1.</span> Angolo {span} '
        "&lt;b&gt;x&lt;/b&gt; &amp; &#34;y&#34;"
    )
    assert 'aria-label="Angolo $30^\\circ$ &lt;b&gt;x&lt;/b&gt; &amp; &#34;y&#34;"' in html
    assert (
        f'<figcaption><span class="figure-label">Tabella 1.</span> Valori di {span} &lt;i&gt;'
        "</figcaption>" in html
    )
    assert f'<span class="figure-label">Esempio 1.</span> Esempio {span} &amp; co</div>' in html
    assert f'<span class="label">Con {span} &#34;q&#34;</span>' in html
    assert f'<div class="theorem-head">Lemma 2. Weierstrass {span} &lt;b&gt;</div>' in html
    assert f"<li>Vale {span}</li>" in html
    assert "<li>Vedi Figura 1</li>" in html
    assert "<li>Con &#34;virgolette&#34; &amp; co</li>" in html
    assert f"<li>Rudin {span}</li>" in html
    assert f"<li>Grezza {span}</li>" in html
    for key in (
        ("30^\\circ", "inline"),
        ("\\theta", "inline"),
        ("\\epsilon", "inline"),
        ("\\lambda", "inline"),
        ("\\nu", "inline"),
        ("x^2", "inline"),
        ("\\tau", "inline"),
    ):
        assert key in rec.seen, key
    assert set(rec.seen) == set(pdf._collect_math_from_content(content))


def test_figure_caption_without_renderer_is_byte_identical() -> None:
    """`caption_renderer` assente: il partial rende esattamente come prima
    (golden di `test_lesson_pdf_figures`), e `caption_text` e' l'unico
    pre-trattamento della didascalia."""
    kwargs: dict[str, Any] = {
        "body_html": markupsafe.Markup("<i/>"),
        "alt_text": "",
        "asset_id": "A",
        "fmt": "mermaid",
        "number": 2,
        "labels": None,
        "variant": "lesson",
    }
    plain = figure_markup.render_figure_html(caption="Fig. 2: Testo <b> $x$", **kwargs)
    assert '<span class="figure-label">Figura 2.</span> Testo &lt;b&gt; $x$</figcaption>' in plain
    rendered = figure_markup.render_figure_html(
        caption="Fig. 2: Testo <b> $x$",
        caption_renderer=lambda s: markupsafe.Markup(pdf.render_markdown_inline(s, RecordingMap())),
        **kwargs,
    )
    assert (
        '<span class="figure-label">Figura 2.</span> Testo &lt;b&gt; '
        f'<span class="math-inline">{STUB}</span></figcaption>' in rendered
    )
    assert figure_markup.caption_text("Figura 7.  Angolo $a\n b$ ") == "Angolo $a b$"
    assert "caption_text" in figure_markup.__all__


# ---------------------------------------------------------------------------
# T14 — l'HTML di un esempio con fence e riga vuota resta UN html block
# ---------------------------------------------------------------------------


def test_injected_asset_html_with_blank_lines_stays_one_html_block() -> None:
    content = {
        "introduction": "Vedi [EX:e1].",
        "sections": [],
        "summary": "",
        "examples": [
            {
                "example_id": "e1",
                "title": "T",
                "content": '```python\nx = 1\n\nprint("a\\[0\\]") $x$\n```',
            }
        ],
    }
    rec = RecordingMap()
    html = _render(content, math_svg_map=rec)
    aside = html[html.index("<aside") : html.index("</aside>") + len("</aside>")]
    assert (
        '<pre><code class="language-python">x = 1\n\u00a0\nprint(&quot;a\\[0\\]&quot;) $x$\n'
        "</code></pre>" in aside
    )
    assert "<p>" not in aside and "math-inline" not in aside
    assert rec.seen == [] and pdf._collect_math_from_content(content) == []
    # Identita' sul partial figura (gia' senza righe vuote) e sul golden.
    fig = figure_markup.render_figure_html(
        body_html=markupsafe.Markup('<div class="mermaid-svg"><svg/></div>'),
        caption="Fig. 2: Testo <b>",
        alt_text='a "b"',
        asset_id='A"1',
        fmt="mermaid",
        number=2,
        labels=None,
        variant="lesson",
    )
    assert pdf._neutralize_blank_lines(fig) == fig
    assert pdf._neutralize_blank_lines("<div>\n\n</div>\n<pre>a\n\nb</pre>\n\n<p>x</p>") == (
        "<div>\n</div>\n<pre>a\n\u00a0\nb</pre>\n<p>x</p>"
    )
    # CRLF/CR come in `figure_markup._fallback_text`: una riga `\r` dentro
    # il `<pre>` e' vuota per markdown-it e chiuderebbe l'HTML block.
    assert pdf._neutralize_blank_lines("<pre>a\r\n\r\nb</pre>\r\n\r\n<p>x</p>") == (
        "<pre>a\n\u00a0\nb</pre>\n<p>x</p>"
    )
    crlf = '<aside class="example"><pre>a\r\n\r\nb</pre></aside>'
    assert pdf.render_markdown(
        "Intro.\n\n" + pdf._substitute_asset_refs("[EX:z]", {("EX", "z"): crlf})
    ) == ('<p>Intro.</p>\n<aside class="example"><pre>a\n\u00a0\nb</pre></aside>\n')


# ---------------------------------------------------------------------------
# T15 — slide: didascalie raccolte e mappa math passata alla figura
# ---------------------------------------------------------------------------


def test_slides_collect_captions_and_pass_math_map_to_figures() -> None:
    a, b = _asset("A", "Uno"), _asset("B", "Due")
    merged = slides_pdf._math_content_for_slides(
        {"visual_assets": [a], "equations": [{"equation_id": "e"}], "tables": [], "examples": []},
        {
            "new_assets": [b],
            "new_equations": [{"equation_id": "n"}],
            "new_tables": [{"table_id": "t"}],
        },
    )
    assert merged["visual_assets"] == [a, b]
    assert merged["equations"] == [{"equation_id": "e"}, {"equation_id": "n"}]
    assert merged["tables"] == [{"table_id": "t"}] and merged["examples"] == []
    assert slides_pdf._math_content_for_slides(None, None) == {
        "equations": [],
        "tables": [],
        "examples": [],
        "visual_assets": [],
    }
    rec = RecordingMap()
    html = slides_pdf._build_slide_asset_html(
        _asset("A", "Angolo $30^\\circ$"),
        kind="visual",
        visual_svg_map={"A": SVG_A},
        math_svg_map=rec,
        language="it",
    )
    assert (
        f'<span class="figure-label">Figura.</span> Angolo <span class="math-inline">{STUB}</span>'
        in html
    )
    assert rec.seen == [("30^\\circ", "inline")]


# ---------------------------------------------------------------------------
# T16 — corpus math nel testo del PDF: nessun residuo LaTeX
# ---------------------------------------------------------------------------


def test_math_corpus_pdf_text_has_no_latex_residue() -> None:
    weasyprint = _weasyprint()
    content = {
        "introduction": (
            "Il costo e $50 e sale a $70 al mese. Sia $x_0$ dato e \\(\\beta\\) noto."
        ),
        "sections": [
            {
                "title": "Sezione su $\\alpha$",
                "content": (
                    "Testo.\n\n$$\nE = mc^2\n$$\n\nAltro.\n$$\nF = ma\n$$\nFine. "
                    "Sia $$G = H$$ la relazione e \\[I = J\\] pure.\n\n"
                    "$$ a &= b \\\\ c &= d $$\n\n"
                    "Vale $15{,}9$, $0{,}866$, $30^\\circ$ e la base 2$^{10}$.\n\n"
                    "Vedi [FIG:A], [TAB:t1] ed [EQ:e1]."
                ),
            }
        ],
        "summary": "Sintesi con $\\zeta_{1}$.",
        "tables": [
            {
                "table_id": "t1",
                "caption": "Valori di $\\theta$",
                "markdown": "| a |\n|---|\n| $$x^2$$ |",
            }
        ],
        "equations": [{"equation_id": "e1", "latex": "x^{2}=1", "label": "Con $\\lambda$"}],
        "visual_assets": [_asset("A", "Figura 3: angolo $30^\\circ$")],
        "key_takeaways": ["Vale $x^{2}$"],
        "references": [{"citation": "Rudin, $\\epsilon$-$\\delta$."}],
    }
    rec = RecordingMap()
    with structlog.testing.capture_logs() as logs:
        html = _render(content, visual_svg_map={"A": SVG_A}, math_svg_map=rec)
    assert _fallbacks(logs) == [] and "<math" not in html
    assert set(rec.seen) == set(pdf._collect_math_from_content(content))
    _, text, warnings = _pdf_text_and_warnings(weasyprint, html)
    assert re.search(r"\\[a-zA-Z]+|\$\$|\{,\}|\^\{|_\{", text) is None, text
    assert "Il costo e $50 e sale a $70 al mese." in " ".join(text.split())
    assert warnings == []


# ---------------------------------------------------------------------------
# D6(6) — pin MathJax dal setting e guardia di rete
# ---------------------------------------------------------------------------


def test_mathjax_renderer_uses_the_setting_pin_and_only_the_cdn() -> None:
    version = get_settings().mathjax_cdn_version
    html = pdf._MATHJAX_RENDERER_HTML
    assert f"https://cdn.jsdelivr.net/npm/mathjax@{version}/es5/tex-svg.js" in html
    assert "__MATHJAX_" not in html and "window.__renderMath" in html
    assert version == "3.2.2"
    explicit = pdf.build_mathjax_renderer_html(version="3.2.1")
    assert "mathjax@3.2.1/es5/tex-svg.js" in explicit
    for url in re.findall(r"https?://[^'\"\s]+", html):
        assert mp.allows_prerender_url(url), url
    source = Path(pdf.__file__).read_text(encoding="utf-8")
    guard = source.index("await _mermaid_prerender.block_external_requests(page)")
    assert guard < source.index("await page.set_content(")
