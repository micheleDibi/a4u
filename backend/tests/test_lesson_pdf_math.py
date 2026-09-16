"""Grammatica del math nel PDF delle lezioni (B3, WP0: D6, D7, D8-A).

Test puri sul renderer markdown del servizio (`pdf._md_renderer`) e su
`render_lesson_html` (stesso schema di `test_lesson_pdf_figures.py`):

- i quattro token di dollarmath (`math_inline`, `math_inline_double`,
  `math_block`, `math_block_label`) sono resi da un'unica rule nostra;
  nessuno resta al default del plugin (`<div class="math inline">` dentro
  un `<p>`, senza mappa SVG);
- flag del plugin (`_DOLLARMATH_OPTIONS`) e posizione della core rule
  `math_currency_guard` (prima di `text_join`) pinnati;
- importi in dollari (`$50 e sale a $70`, `$50/$70`, `5$, 10$`, `US$50`)
  restano prosa byte-identica; un numero fra `$` resta math;
- `$$..$$` in frase, cella o titolo e' uno `<span>` (mai `<div>` dentro un
  `<p>`) e resta su una riga nel PDF; su righe proprie e' blocco via CSS;
- il fallback MathML e' loggato (`math_render_fallback` con `reason`) e
  contato (`MathSvgMap.misses`, summary `lesson_pdf_math_fallbacks`;
  `structlog.testing.capture_logs`: il logger dell'app e' un
  `PrintLoggerFactory`, `caplog` non lo vede);
- WeasyPrint NON rende il MathML: il testo estratto dal PDF lo appiattisce;
- frase currency e `$$..$$` in frase sopravvivono al testo del PDF.
"""

from __future__ import annotations

import inspect
import re
from typing import Any

import pytest
import structlog.testing

from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.services import course_lesson_pdf_service as pdf
from tests.test_lesson_pdf_figures import _pdf_text_and_warnings, _weasyprint

_PLUGIN_MODULE = "mdit_py_plugins.dollarmath.index"
_TOKEN_TYPES = ("math_inline", "math_inline_double", "math_block", "math_block_label")

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


# ---------------------------------------------------------------------------
# T1 — le quattro rule di render sono nostre
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token_type", _TOKEN_TYPES)
def test_no_dollarmath_rule_is_left_to_the_plugin_default(token_type: str) -> None:
    """Prima del fix `math_inline_double` e `math_block_label` restavano al
    default del plugin: `<div class="math inline">` dentro un `<p>`, senza
    consultare la mappa SVG."""
    rule = pdf._md_renderer.renderer.rules[token_type]
    assert rule.__module__ != _PLUGIN_MODULE, f"{token_type}: rule di default del plugin"


def test_all_four_dollarmath_rules_are_ours() -> None:
    rules = pdf._md_renderer.renderer.rules
    ours = {t: rules[t].__func__ is pdf._render_math_token for t in _TOKEN_TYPES}
    assert ours == dict.fromkeys(_TOKEN_TYPES, True)


# ---------------------------------------------------------------------------
# T2 — flag del plugin e ordine delle core rule pinnati
# ---------------------------------------------------------------------------


def test_math_grammar_flags_and_rulers_are_pinned() -> None:
    assert pdf._DOLLARMATH_OPTIONS == {
        "allow_labels": False,
        "double_inline": True,
        "allow_space": False,
        "allow_digits": True,
    }
    core = pdf._md_renderer.core.ruler.get_active_rules()
    assert "math_currency_guard" in core
    assert core.index("math_currency_guard") < core.index("text_join")
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


@pytest.mark.parametrize(("source", "keys"), _MATH_CORPUS)
def test_currency_guard_keeps_numeric_formulas_as_math(
    source: str, keys: list[tuple[str, str]]
) -> None:
    rec = RecordingMap()
    html = pdf.render_markdown(source, rec)
    assert html.count('class="math-inline"') == len(keys)
    assert rec.seen == keys
    assert "$" not in html and "<math" not in html


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


def _fallbacks(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in logs if e["event"] == "math_render_fallback"]


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
    # Il summary e' cablato nel materialize, dopo il render della lezione.
    src = inspect.getsource(pdf.materialize_lesson_pdf)
    assert src.index("render_lesson_html") < src.index("_log_math_fallbacks(")


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
