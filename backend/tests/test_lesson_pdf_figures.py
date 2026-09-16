"""Rendering delle figure nei PDF (WP4: D4, Q2, Q3, A2, A11-L3, A12, A23).

Test puri su oggetti `Course`/`CourseLesson` non persistiti (come
`test_prompt_composition_bugs.py`): `render_lesson_html(..., pdf_template=None,
organization=None, visual_svg_map={...})` e `render_slides_html(...,
slide_template=None, enable_split=False)`.

- didascalie «Figura N.» in ordine di prima citazione, orfano in coda dopo
  la sintesi, `en` → «Figure», `de` → «Figura» (fallback it);
- rimandi testuali (D1, D2, D4): la citazione in linea resta nella frase
  («Come mostrato in Figura 1, …»), il blocco segue il paragrafo, una
  figura per asset anche con citazioni ripetute, code span/liste/fence
  intatti; la coda (punti chiave, riferimenti) riceve rimandi, mai blocchi;
- tabelle, equazioni ed esempi numerati per kind (D3, D5): «Tabella N.»,
  «Equazione N.», «Esempio N.», teorema «Lemma N.» sul contatore EQ,
  etichetta sempre presente, orfani accodati FIG → TAB → EQ → EX, forme
  non numerate nelle slide; `_labels_for` e `_slide_type_label` senza
  segnaposto (guardia D5);
- prefisso «Figura 7.» ripulito a render, caption escapata;
- `<pre class="figure-fallback">` per SVG mancante con
  `figure_render_fallback` nel log (A23; `structlog.testing.capture_logs`:
  il logger dell'app è un `PrintLoggerFactory`, `caplog` non lo vede);
- golden byte-identico del body Mermaid/image/legacy dentro il wrapper;
- slide: «Figura.» senza numero, `figure--slide`, `<img class="figure-svg">`;
- WeasyPrint 69 rende le label degli SVG Mermaid 11 (fixture
  `mermaid11_flowchart.svg`) e degli `<img data:svg>` matplotlib: testo
  estratto con pypdf, nessun warning oltre il filtro del rumore SVG
  («Delta» voce 8, verifica residua del piano).
"""

from __future__ import annotations

import io
import json
import logging
import re
from pathlib import Path
from typing import Any

import pytest
import structlog.testing
from markupsafe import Markup

from app.core.logging import _WeasyPrintSvgNoiseFilter
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.services import course_lesson_pdf_service as pdf
from app.services import course_lesson_slides_pdf_service as slides_pdf
from app.services import figure_function_service as ffs
from app.services import figure_markup
from app.services import figure_render_service as frs
from app.services.figure_theme import figure_labels
from app.services.svg_normalize import normalize_svg, svg_to_data_uri

_FIXTURES = Path(__file__).parent / "fixtures"
_TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"

SVG_A = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><text>a</text></svg>'
SVG_B = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20"><rect width="1" height="1"/></svg>'
)
PNG_DATA_URL = "data:image/png;base64,iVBORw0KGgo="

_FIGURE_RE = re.compile(
    r'<figure class="(?P<cls>visual[^"]*)" data-asset-id="(?P<id>[^"]*)"[^>]*>'
    r'\s*<div class="figure-body">(?P<body>.*?)</div>\s*'
    r'<figcaption class="figure-caption">(?P<caption>.*?)</figcaption>\s*</figure>',
    re.S,
)


def _course(language: str = "it") -> Course:
    return Course(title="Corso di prova", language_code=language, cfu=6)


def _lesson(content_raw: dict[str, Any], slides_raw: dict[str, Any] | None = None) -> CourseLesson:
    return CourseLesson(
        lesson_code="M1.L1",
        title="Lezione di prova",
        content_raw=content_raw,
        slides_raw=slides_raw,
    )


def _asset(asset_id: str, fmt: str, content: str, caption: str = "", alt: str = "") -> dict:
    return {
        "asset_id": asset_id,
        "format": fmt,
        "content": content,
        "caption": caption,
        "alt_text": alt,
    }


def _figures(html: str) -> list[dict[str, str]]:
    return [m.groupdict() for m in _FIGURE_RE.finditer(html)]


def _label(fig: dict[str, str]) -> str:
    m = re.search(r'<span class="figure-label">(.*?)</span>', fig["caption"])
    assert m, fig["caption"]
    return m.group(1)


def _render(content_raw: dict[str, Any], *, language: str = "it", **kwargs: Any) -> str:
    return pdf.render_lesson_html(
        course=_course(language),
        lesson=_lesson(content_raw),
        organization=None,
        pdf_template=None,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Numerazione e didascalie nella dispensa
# ---------------------------------------------------------------------------


def _content_three_figures() -> dict[str, Any]:
    return {
        "introduction": "Intro con [FIG:C] e poi [FIG:A].",
        "sections": [
            {"section_id": "S1", "title": "Prima", "content": "Ancora [FIG:A] e infine [FIG:B]."}
        ],
        "summary": "Sintesi della lezione.",
        "key_takeaways": ["Uno", "Due"],
        "references": [],
        "visual_assets": [
            _asset("A", "mermaid", "flowchart LR\n A --> B", "Schema A"),
            _asset("B", "vegalite", '{"mark": "bar"}', "Barre B", alt="grafico b"),
            _asset("C", "dot", "digraph { a -> b }", "Grafo C"),
            _asset("D", "dot", "digraph { d }", "Orfano D"),
        ],
    }


def _svg_map_three() -> dict[str, str]:
    return {"A": SVG_A, "B": SVG_B, "C": SVG_B, "D": SVG_B}


def test_figcaptions_follow_first_citation_and_orphans_go_last() -> None:
    html = _render(_content_three_figures(), visual_svg_map=_svg_map_three())
    figs = _figures(html)
    assert [(f["id"], _label(f)) for f in figs] == [
        ("C", "Figura 1."),
        ("A", "Figura 2."),  # resa UNA volta: la citazione ripetuta è un rimando
        ("B", "Figura 3."),
        ("D", "Figura 4."),  # mai citata: in coda, ultimo numero
    ]
    assert html.count('data-asset-id="A"') == 1
    # Le frasi restano intere, con il rimando testuale al posto del tag.
    assert "<p>Intro con Figura 1 e poi Figura 2.</p>" in html
    assert "<p>Ancora Figura 2 e infine Figura 3.</p>" in html
    assert "[FIG:" not in html
    # L'orfano segue la sintesi e precede i punti chiave.
    summary_pos = html.index("Sintesi della lezione.")
    orphan_pos = html.index('data-asset-id="D"')
    takeaways_pos = html.index("Punti chiave")
    assert summary_pos < orphan_pos < takeaways_pos


def test_number_is_bound_to_id_not_to_array_order() -> None:
    content = _content_three_figures()
    content["visual_assets"].reverse()  # ordine dell'array irrilevante
    html = _render(content, visual_svg_map=_svg_map_three())
    assert [(f["id"], _label(f)) for f in _figures(html)][:2] == [
        ("C", "Figura 1."),
        ("A", "Figura 2."),
    ]


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("it", "Figura 1."),
        ("en", "Figure 1."),
        ("en-GB", "Figure 1."),
        ("de", "Figura 1."),
        ("ja", "Figura 1."),
    ],
)
def test_label_language_with_fallback_it(language: str, expected: str) -> None:
    content = {
        "introduction": "Vedi [FIG:A].",
        "sections": [],
        "summary": "",
        "visual_assets": [_asset("A", "mermaid", "flowchart LR\n A", "Schema")],
    }
    html = _render(content, language=language, visual_svg_map={"A": SVG_A})
    assert _label(_figures(html)[0]) == expected


def test_caption_prefix_is_stripped_only_at_render() -> None:
    content = {
        "introduction": "Vedi [FIG:A].",
        "sections": [],
        "summary": "",
        "visual_assets": [_asset("A", "mermaid", "flowchart LR\n A", "Figura 7. Schema")],
    }
    html = _render(content, visual_svg_map={"A": SVG_A})
    fig = _figures(html)[0]
    assert fig["caption"] == '<span class="figure-label">Figura 1.</span> Schema'
    # Mai persistito: il contenuto della lezione resta com'era.
    assert content["visual_assets"][0]["caption"] == "Figura 7. Schema"


def test_caption_alt_and_id_are_escaped() -> None:
    content = {
        "introduction": "Vedi [FIG:A].",
        "sections": [],
        "summary": "",
        "visual_assets": [
            _asset("A", "mermaid", "flowchart LR\n A", 'Schema <b>x</b> & "y"', alt="alt <i>")
        ],
    }
    html = _render(content, visual_svg_map={"A": SVG_A})
    fig = _figures(html)[0]
    assert "<b>" not in fig["caption"]
    assert "&lt;b&gt;x&lt;/b&gt; &amp; &#34;y&#34;" in fig["caption"]
    assert 'aria-label="alt &lt;i&gt;"' in html


def test_figure_without_caption_still_carries_the_label() -> None:
    content = {
        "introduction": "Vedi [FIG:A].",
        "sections": [],
        "summary": "",
        "visual_assets": [_asset("A", "mermaid", "flowchart LR\n A", "")],
    }
    html = _render(content, visual_svg_map={"A": SVG_A})
    assert _figures(html)[0]["caption"] == '<span class="figure-label">Figura 1.</span>'


def test_key_takeaways_and_references_get_textual_references_but_no_blocks() -> None:
    """La coda non numera e non rende blocchi (C9): i tag nei punti chiave e
    nelle citazioni bibliografiche diventano rimandi testuali con il numero
    del corpo; gli asset non citati nel corpo restano orfani in coda."""
    content = {
        "introduction": "Testo senza citazioni.",
        "sections": [],
        "summary": "",
        "key_takeaways": ["Vedi [FIG:A]", "[FIG:A]"],
        "references": [
            {"citation": "Vedi [FIG:B]", "source": "suggerimento_generale"},
            "[FIG:B]",  # forma grezza: non deve far fallire il render
        ],
        "visual_assets": [
            _asset("A", "mermaid", "flowchart LR\n A", "Schema A"),
            _asset("B", "mermaid", "flowchart LR\n B", "Schema B"),
        ],
    }
    html = _render(content, visual_svg_map={"A": SVG_A, "B": SVG_A})
    assert [(f["id"], _label(f)) for f in _figures(html)] == [
        ("A", "Figura 1."),
        ("B", "Figura 2."),
    ]
    assert "<li>Vedi Figura 1</li>" in html
    assert "<li>Figura 1</li>" in html
    assert "<li>Vedi Figura 2</li>" in html
    assert "[FIG:" not in html
    assert html.count("<figure") == 2


def test_asset_ids_with_surrounding_spaces_and_case_are_matched_and_numbered() -> None:
    """Le tre normalizzazioni dell'id coincidono (`.strip().lower()`): un
    asset « A » citato come `[FIG: A ]` è reso e numerato, non «Asset non
    trovato»; lo stesso per tabelle, equazioni ed esempi."""
    content = {
        "introduction": "Vedi [FIG: A ] e [TAB:t1] e [EQ: E1 ].",
        "sections": [],
        "summary": "",
        "visual_assets": [_asset(" A ", "mermaid", "flowchart LR\n A", "Schema")],
        "tables": [{"table_id": " T1 ", "caption": "Tabella", "markdown": "| c |\n|---|\n| 1 |"}],
        "equations": [{"equation_id": "e1 ", "latex": "x=1", "label": "Eq"}],
    }
    html = _render(content, visual_svg_map={" A ": SVG_A})
    assert 'class="missing-asset"' not in html
    fig = _figures(html)[0]
    assert fig["id"] == "A" and _label(fig) == "Figura 1."  # `data-asset-id` collassato
    assert "<p>Vedi Figura 1 e Tabella 1 e Equazione 1.</p>" in html
    assert '<figcaption><span class="figure-label">Tabella 1.</span> Tabella</figcaption>' in html
    assert '<figure class="equation">' in html
    assert '<span class="figure-label">Equazione 1.</span> <span class="label">Eq</span>' in html


def test_missing_asset_reference_keeps_the_marker_and_no_number() -> None:
    content = {
        "introduction": "Vedi [FIG:ghost] e [FIG:A].",
        "sections": [],
        "summary": "",
        "visual_assets": [_asset("A", "mermaid", "flowchart LR\n A", "Schema")],
    }
    html = _render(content, visual_svg_map={"A": SVG_A})
    assert '<div class="missing-asset">Asset non trovato: [FIG:ghost]</div>' in html
    assert _label(_figures(html)[0]) == "Figura 1."
    # Il tag irrisolto resta blocco `missing-asset`; quello risolto è un rimando.
    assert "<p>e Figura 1.</p>" in html
    assert html.count('class="missing-asset"') == 1
    assert len(_figures(html)) == 1


# ---------------------------------------------------------------------------
# Rimandi testuali e ancora dopo il blocco (D1, D2, D4)
# ---------------------------------------------------------------------------


def _one_figure_content(introduction: str) -> dict[str, Any]:
    return {
        "introduction": introduction,
        "sections": [],
        "summary": "",
        "visual_assets": [_asset("A", "mermaid", "flowchart LR\n A", "Schema")],
    }


def test_inline_citation_keeps_the_sentence_and_anchors_the_figure_after_the_paragraph() -> None:
    """C2 sulla catena reale (markdown-it + Jinja): la frase resta intera e
    il blocco figura segue il paragrafo, invece di spezzarlo in due `<p>`."""
    html = _render(
        _one_figure_content("Come mostrato in [FIG:A], il sistema converge."),
        visual_svg_map={"A": SVG_A},
    )
    assert "<p>Come mostrato in Figura 1, il sistema converge.</p>" in html
    assert "[FIG:A]" not in html
    assert "<p>Come mostrato in</p>" not in html
    assert html.index("il sistema converge.</p>") < html.index('data-asset-id="A"')
    assert len(_figures(html)) == 1


def test_citation_inside_code_span_keeps_one_figure() -> None:
    """Dentro un code span il tag è una citazione: lasciarlo intatto
    produrrebbe un secondo `<figure>` o markup escapato nel `<code>`."""
    html = _render(
        _one_figure_content("Il tag `[FIG:A]` e poi [FIG:A]."), visual_svg_map={"A": SVG_A}
    )
    assert "<p>Il tag <code>Figura 1</code> e poi Figura 1.</p>" in html
    assert len(_figures(html)) == 1
    assert "&lt;figure" not in html


def test_citation_inside_tight_list_keeps_one_list() -> None:
    html = _render(
        _one_figure_content("- uno [FIG:A]\n- due\n\nDopo."), visual_svg_map={"A": SVG_A}
    )
    assert html.count("<ul>") == 1
    assert "<li>uno Figura 1</li>" in html
    assert html.index("</ul>") < html.index('data-asset-id="A"') < html.index("<p>Dopo.</p>")


def test_citation_inside_fence_never_yields_escaped_markup() -> None:
    html = _render(_one_figure_content("```\n[FIG:A]\n```"), visual_svg_map={"A": SVG_A})
    assert "<pre><code>Figura 1\n</code></pre>" in html
    assert "&lt;figure" not in html
    assert len(_figures(html)) == 1


@pytest.mark.parametrize(
    ("language", "introduction", "expected"),
    [
        ("it", "Vedi [FIG:A].", "<p>Vedi Figura 1.</p>"),
        ("en", "See [FIG:A].", "<p>See Figure 1.</p>"),
        ("de", "Vedi [FIG:A].", "<p>Vedi Figura 1.</p>"),  # fallback it
    ],
)
def test_reference_label_follows_course_language(
    language: str, introduction: str, expected: str
) -> None:
    html = _render(
        _one_figure_content(introduction), language=language, visual_svg_map={"A": SVG_A}
    )
    assert expected in html


# ---------------------------------------------------------------------------
# Tabelle, equazioni ed esempi numerati (D3, D5)
# ---------------------------------------------------------------------------


def _content_four_kinds() -> dict[str, Any]:
    return {
        "introduction": "Vedi [TAB:t2], [EQ:e1], [EX:x1], [TAB:t1] e ancora [EQ:e1].",
        "sections": [
            {
                "section_id": "S1",
                "title": "Prima",
                "content": "Per [EQ:lem] e [FIG:A] vale.",
            }
        ],
        "summary": "",
        "visual_assets": [_asset("A", "mermaid", "flowchart LR\n A", "Schema")],
        "tables": [
            {"table_id": "t1", "caption": "Cap t1", "markdown": "| a |\n|---|\n| 1 |"},
            {"table_id": "t2", "caption": "Cap t2", "markdown": "| b |\n|---|\n| 2 |"},
            {"table_id": "t3", "caption": "Cap t3", "markdown": "| c |\n|---|\n| 3 |"},
        ],
        "equations": [
            {"equation_id": "e1", "latex": "x=1", "label": "Eq"},
            {"equation_id": "lem", "kind": "lemma", "latex": "y=2", "statement": "Sia $y$ dato."},
        ],
        "examples": [{"example_id": "x1", "title": "Titolo", "content": "Contenuto."}],
    }


def _positions(html: str, needles: list[str]) -> list[int]:
    return [html.index(n) for n in needles]


def test_table_equation_example_labels_follow_first_citation_per_kind() -> None:
    html = _render(_content_four_kinds(), visual_svg_map={"A": SVG_A})
    assert "<p>Vedi Tabella 1, Equazione 1, Esempio 1, Tabella 2 e ancora Equazione 1.</p>" in html
    assert "<p>Per Lemma 2 e Figura 1 vale.</p>" in html
    needles = [
        '<span class="figure-label">Tabella 1.</span> Cap t2',
        '<span class="figure-label">Equazione 1.</span> <span class="label">Eq</span>',
        '<div class="example-title"><span class="figure-label">Esempio 1.</span> Titolo</div>',
        '<span class="figure-label">Tabella 2.</span> Cap t1',
        '<div class="theorem-head">Lemm' + "a 2.</div>",  # contatore EQ condiviso
        '<span class="figure-label">Figura 1.</span>',  # contatore FIG indipendente
        '<span class="figure-label">Tabella 3.</span> Cap t3',  # orfana: in coda
    ]
    positions = _positions(html, needles)
    assert positions == sorted(positions)
    # Il blocco è reso una volta sola; la seconda citazione è il rimando nel testo.
    assert html.count('<span class="figure-label">Equazione 1.</span>') == 1
    assert html.count("Equazione 1") == 3  # etichetta + due rimandi nel paragrafo
    assert "[TAB:" not in html and "[EQ:" not in html and "[EX:" not in html


def test_uncited_tables_equations_examples_are_appended_after_summary() -> None:
    """D3 letterale: anche tabelle, equazioni ed esempi mai citati sono
    accodati dopo la sintesi, in ordine FIG → TAB → EQ → EX, prima dei
    punti chiave (cambiamento visibile sui contenuti storici: asset prima
    invisibili nel PDF ora compaiono)."""
    content = {
        "introduction": "Solo testo.",
        "sections": [],
        "summary": "Sintesi della lezione.",
        "key_takeaways": ["Uno"],
        "visual_assets": [_asset("A", "mermaid", "flowchart LR\n A", "Schema")],
        "tables": [{"table_id": "t1", "caption": "Cap", "markdown": "| a |\n|---|\n| 1 |"}],
        "equations": [{"equation_id": "e1", "latex": "x=1"}],
        "examples": [{"example_id": "x1", "title": "Titolo", "content": "Contenuto."}],
    }
    html = _render(content, visual_svg_map={"A": SVG_A})
    positions = _positions(
        html,
        [
            "Sintesi della lezione.",
            'data-asset-id="A"',
            '<span class="figure-label">Tabella 1.</span>',
            '<span class="figure-label">Equazione 1.</span>',
            '<span class="figure-label">Esempio 1.</span>',
            "Punti chiave",
        ],
    )
    assert positions == sorted(positions)


def test_equation_branch_decides_the_label_family() -> None:
    from app.services import figure_numbering as fn

    content = {
        "introduction": "[EQ:a] [EQ:b] [EQ:c] [EQ:d]",
        "sections": [],
        "summary": "",
        "equations": [
            {"equation_id": "a", "kind": "theorem", "latex": "x=1"},  # senza enunciato: EQ
            {"equation_id": "b", "kind": "formula", "latex": "x=2", "statement": "S"},
            {"equation_id": "c", "kind": "xyz", "latex": "x=3", "statement": "S"},
            {"equation_id": "d", "latex": "x=4", "proof": [{"latex": "", "text": " "}]},
        ],
    }
    html = _render(content)
    assert '<span class="figure-label">Equazione 1.</span>' in html
    assert '<div class="theorem-head">Formula 2.</div>' in html
    assert '<div class="theorem-head">Teorema 3.</div>' in html  # kind ignoto: fallback
    assert '<span class="figure-label">Equazione 4.</span>' in html
    assert html.count('<div class="theorem-head">') == 2
    assert [fn.equation_label_family(e) for e in content["equations"]] == ["EQ", "THM", "THM", "EQ"]


def test_label_is_emitted_even_without_caption_label_or_title() -> None:
    content = {
        "introduction": "[TAB:t] [EQ:e] [EX:x]",
        "sections": [],
        "summary": "",
        "tables": [{"table_id": "t", "markdown": "| a |\n|---|\n| 1 |"}],
        "equations": [{"equation_id": "e", "latex": "x=1"}],
        "examples": [{"example_id": "x", "title": "", "content": "Contenuto."}],
    }
    numbers = {("TAB", "t"): 1, ("EQ", "e"): 1, ("EX", "x"): 1}
    blocks = pdf._build_asset_html_map(content, asset_numbers=numbers)
    assert blocks[("TAB", "t")].endswith(
        '<figcaption><span class="figure-label">Tabella 1.</span></figcaption></figure>'
    )
    assert blocks[("EQ", "e")].endswith(
        '<figcaption><span class="figure-label">Equazione 1.</span></figcaption></figure>'
    )
    assert blocks[("EX", "x")].startswith(
        '<aside class="example"><div class="example-title">'
        '<span class="figure-label">Esempio 1.</span></div>'
    )
    for block in blocks.values():
        assert "\n\n" not in block  # HTML block di markdown-it


@pytest.mark.parametrize(
    ("language", "table", "equation", "example", "theorem"),
    [
        ("it", "Tabella 1.", "Equazione 1.", "Esempio 1.", "Lemma 2."),
        ("en", "Table 1.", "Equation 1.", "Example 1.", "Lemma 2."),
        ("en-GB", "Table 1.", "Equation 1.", "Example 1.", "Lemma 2."),
        ("de", "Tabella 1.", "Equazione 1.", "Esempio 1.", "Lemma 2."),
        ("ja", "Tabella 1.", "Equazione 1.", "Esempio 1.", "Lemma 2."),
    ],
)
def test_labels_language_for_tables_equations_examples(
    language: str, table: str, equation: str, example: str, theorem: str
) -> None:
    html = _render(_content_four_kinds(), language=language, visual_svg_map={"A": SVG_A})
    assert f'<span class="figure-label">{table}</span>' in html
    assert f'<span class="figure-label">{equation}</span>' in html
    assert f'<span class="figure-label">{example}</span>' in html
    assert f'<div class="theorem-head">{theorem}</div>' in html


def test_author_label_and_title_are_escaped_next_to_the_label() -> None:
    content = {
        "introduction": "[TAB:t] [EQ:e] [EX:x]",
        "sections": [],
        "summary": "",
        "tables": [{"table_id": "t", "caption": "<i>c</i>", "markdown": "| a |\n|---|\n| 1 |"}],
        "equations": [{"equation_id": "e", "latex": "x=1", "label": '<b>x</b> & "y"'}],
        "examples": [{"example_id": "x", "title": "<i>t</i>", "content": "Contenuto."}],
    }
    html = _render(content)
    assert '<span class="figure-label">Tabella 1.</span> &lt;i&gt;c&lt;/i&gt;' in html
    assert (
        '<span class="figure-label">Equazione 1.</span> '
        '<span class="label">&lt;b&gt;x&lt;/b&gt; &amp; &#34;y&#34;</span>' in html
    )
    assert '<span class="figure-label">Esempio 1.</span> &lt;i&gt;t&lt;/i&gt;' in html
    assert "<i>" not in html.split("</style>", 1)[1] and "<b>x" not in html


def test_missing_table_reference_does_not_consume_a_number() -> None:
    content = {
        "introduction": "Vedi [TAB:ghost] e [TAB:t1].",
        "sections": [],
        "summary": "",
        "tables": [{"table_id": "t1", "caption": "Cap", "markdown": "| a |\n|---|\n| 1 |"}],
    }
    html = _render(content)
    assert '<div class="missing-asset">Asset non trovato: [TAB:ghost]</div>' in html
    assert '<span class="figure-label">Tabella 1.</span> Cap' in html
    assert "<p>e Tabella 1.</p>" in html


def _flatten(node: Any, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            out.update(_flatten(value, f"{prefix}{key}."))
    elif isinstance(node, str):
        out[prefix[:-1]] = node
    return out


_LOCALES = Path(__file__).resolve().parents[2] / "frontend" / "src" / "i18n" / "locales"
_THEOREM_KINDS = (
    "definition",
    "formula",
    "identity",
    "theorem",
    "proposition",
    "lemma",
    "corollary",
)


@pytest.mark.parametrize("language", ["it", "en"])
def test_theorem_kind_words_mirror_frontend(language: str) -> None:
    """Le due metà di «Lemma 2.» (parola del kind da `_labels_for`, template
    da `courses.figures.theorem.label`) coincidono con il frontend."""
    path = _LOCALES / f"{language}.json"
    assert path.is_file(), path
    flat = _flatten(json.loads(path.read_text(encoding="utf-8")))
    labels = pdf._labels_for(language)
    for kind in _THEOREM_KINDS:
        assert labels[f"kind_{kind}"] == flat[f"courses.theorem.kind.{kind}"], kind
    assert labels["proof"] == flat["courses.theorem.proof"]


def test_labels_for_and_slide_labels_have_no_asset_label_keys() -> None:
    """Guardia D5: le etichette degli asset vivono solo in `figure_theme`;
    `_labels_for` e `_slide_type_label` non ne ospitano né interpolano."""
    expected = {
        "summary",
        "key_takeaways",
        "references",
        "module",
        "lesson",
        "cfu",
        "teacher",
        "proof",
        *(f"kind_{k}" for k in _THEOREM_KINDS),
    }
    slide_types = (
        "title",
        "agenda",
        "prerequisites",
        "concept",
        "definition",
        "diagram",
        "formula",
        "table",
        "example",
        "case_study",
        "exercise",
        "discussion",
        "summary",
        "takeaways",
        "references",
        "bibliography",
    )
    for language in ("it", "en"):
        labels = pdf._labels_for(language)
        assert set(labels) == expected
        values = list(labels.values()) + [
            slides_pdf._slide_type_label(language, t) for t in slide_types
        ]
        for value in values:
            assert "{{n}}" not in value and "{{kind}}" not in value, value


# ---------------------------------------------------------------------------
# Golden byte-identico del body dentro il wrapper (A11-L3) e formati
# ---------------------------------------------------------------------------


def test_mermaid_image_and_legacy_bodies_are_byte_identical_inside_wrapper() -> None:
    content = {
        "introduction": "[FIG:M] [FIG:I] [FIG:L] [FIG:MISSING_IMG]",
        "sections": [],
        "summary": "",
        "visual_assets": [
            _asset("M", "mermaid", "flowchart LR\n A", "Mermaid"),
            _asset("I", "image", PNG_DATA_URL, "Immagine", alt="foto & co"),
            _asset("L", "description", "descrizione <em>", "Legacy"),
            _asset("MISSING_IMG", "image", "lesson_assets/non/esiste.png", "Manca"),
        ],
    }
    html = _render(content, visual_svg_map={"M": SVG_A})
    bodies = {f["id"]: f["body"] for f in _figures(html)}
    # Stringhe del blocco precedente a WP4, carattere per carattere.
    assert bodies["M"] == f'<div class="mermaid-svg">{SVG_A}</div>'
    assert bodies["I"] == f'<img class="uploaded-image" src="{PNG_DATA_URL}" alt="foto &amp; co" />'
    assert bodies["L"] == '<div class="placeholder-image">descrizione &lt;em&gt;</div>'
    assert bodies["MISSING_IMG"] == (
        '<div class="placeholder-image">[immagine mancante: lesson_assets/non/esiste.png]</div>'
    )
    # Il wrapper conserva `.mermaid-svg` per la regola `:has(.mermaid-svg)`.
    assert "figure--mermaid" in html and "figure--image" in html and "figure--description" in html


def test_registry_formats_become_img_figure_svg_with_data_uri() -> None:
    content = {
        "introduction": "[FIG:V] [FIG:D] [FIG:F]",
        "sections": [],
        "summary": "",
        "visual_assets": [
            _asset("V", "vegalite", '{"mark": "bar"}', "Vega", alt="barre"),
            _asset("D", "dot", "digraph { a }", "Dot"),
            _asset("F", "function", '{"kind": "function_study"}', "Funzione"),
        ],
    }
    html = _render(content, visual_svg_map={"V": SVG_A, "D": SVG_B, "F": SVG_B})
    bodies = {f["id"]: f["body"] for f in _figures(html)}
    assert bodies["V"] == f'<img class="figure-svg" src="{svg_to_data_uri(SVG_A)}" alt="barre" />'
    assert bodies["D"] == f'<img class="figure-svg" src="{svg_to_data_uri(SVG_B)}" alt="" />'
    assert bodies["F"].startswith('<img class="figure-svg" src="data:image/svg+xml;base64,')
    # Nessun SVG inline per questi formati: il testo non è nel documento.
    assert "<text>a</text>" not in html


def test_missing_svg_falls_back_to_pre_and_logs_error() -> None:
    source = 'digraph {\n  a -> b [label="<x>"]\n\n  c\n}'
    content = {
        "introduction": "[FIG:D]",
        "sections": [],
        "summary": "",
        "visual_assets": [_asset("D", "dot", source, "Grafo")],
    }
    with structlog.testing.capture_logs() as logs:
        html = _render(content)  # nessuna mappa: render fallito a monte
    fig = _figures(html)[0]
    assert fig["body"].startswith('<pre class="figure-fallback">')
    assert "&lt;x&gt;" in fig["body"] and "<x>" not in fig["body"]
    assert _label(fig) == "Figura 1."
    # Il blocco resta un unico HTML block: nessun frammento del wrapper
    # trattato come markdown (la riga vuota del sorgente è neutralizzata).
    assert "&lt;/figure&gt;" not in html and "<p></figure>" not in html
    events = [e for e in logs if e["event"] == "figure_render_fallback"]
    assert len(events) == 1
    assert events[0]["log_level"] == "error"
    assert events[0]["asset_id"] == "D"
    assert events[0]["format"] == "dot"
    assert events[0]["lesson_code"] == "M1.L1"
    assert events[0]["reason"] == "svg_missing"


def test_mermaid_missing_svg_logs_error_too() -> None:
    content = {
        "introduction": "[FIG:M]",
        "sections": [],
        "summary": "",
        "visual_assets": [_asset("M", "mermaid", "flowchart LR\n A --> B", "Schema")],
    }
    with structlog.testing.capture_logs() as logs:
        html = _render(content, visual_svg_map={})
    assert '<pre class="figure-fallback">flowchart LR\n A --&gt; B</pre>' in html
    assert [e["format"] for e in logs if e["event"] == "figure_render_fallback"] == ["mermaid"]


def test_unknown_format_never_prints_content_in_clear() -> None:
    content = {
        "introduction": "[FIG:X]",
        "sections": [],
        "summary": "",
        "visual_assets": [_asset("X", "weird", "<script>alert(1)</script>", "Ignoto")],
    }
    with structlog.testing.capture_logs() as logs:
        html = _render(content)
    fig = _figures(html)[0]
    assert fig["body"] == '<pre class="figure-fallback">&lt;script&gt;alert(1)&lt;/script&gt;</pre>'
    assert "<script>" not in html
    assert "[weird]" not in html
    assert "figure--weird" in fig["cls"]
    assert [e["event"] for e in logs] == ["figure_format_unknown"]


def test_no_fallback_log_when_all_figures_render() -> None:
    with structlog.testing.capture_logs() as logs:
        _render(_content_three_figures(), visual_svg_map=_svg_map_three())
    assert not [e for e in logs if e["event"] == "figure_render_fallback"]


def test_visual_svg_map_merges_with_legacy_mermaid_svg_map() -> None:
    content = {
        "introduction": "[FIG:A] [FIG:B]",
        "sections": [],
        "summary": "",
        "visual_assets": [
            _asset("A", "mermaid", "flowchart LR\n A", "A"),
            _asset("B", "mermaid", "flowchart LR\n B", "B"),
        ],
    }
    html = _render(content, mermaid_svg_map={"A": SVG_A, "B": SVG_A}, visual_svg_map={"B": SVG_B})
    bodies = {f["id"]: f["body"] for f in _figures(html)}
    assert bodies["A"] == f'<div class="mermaid-svg">{SVG_A}</div>'
    assert bodies["B"] == f'<div class="mermaid-svg">{SVG_B}</div>'  # visual_svg_map prevale


# ---------------------------------------------------------------------------
# Didascalia calcolata di `function` (D9)
# ---------------------------------------------------------------------------


_TAIL = "Zeri in x = −1, 1."  # coda calcolata (segno meno tipografico, A17)


def test_function_extra_caption_follows_the_author_caption(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake(content: str, *, language: str | None, asset_id: str = "") -> str:
        seen["content"], seen["language"], seen["asset_id"] = content, language, asset_id
        return _TAIL

    monkeypatch.setattr(frs, "function_computed_caption", fake)
    content = {
        "introduction": "[FIG:F]",
        "sections": [],
        "summary": "",
        "visual_assets": [_asset("F", "function", '{"kind": "function_study"}', "Parabola")],
    }
    html = _render(content, visual_svg_map={"F": SVG_B})
    fig = _figures(html)[0]
    assert fig["caption"] == f'<span class="figure-label">Figura 1.</span> Parabola. {_TAIL}'
    assert seen == {"content": '{"kind": "function_study"}', "language": "it", "asset_id": "F"}


@pytest.mark.parametrize(
    ("caption", "expected"),
    [
        # Coda già copiata dal docente in fondo alla didascalia: una sola volta.
        (f"Parabola. {_TAIL}", f"Parabola. {_TAIL}"),
        (_TAIL, _TAIL),
        (f"Parabola.  {_TAIL}  ", f"Parabola. {_TAIL}"),
        # Coda presente ma non in fondo: non è una ripetizione, resta.
        # La coda è un periodo a sé: se la didascalia non chiude, il
        # partial aggiunge il punto (TIP-9).
        (f"{_TAIL} Parabola", f"{_TAIL} Parabola. {_TAIL}"),
        ("Parabola", f"Parabola. {_TAIL}"),
        ("Parabola:", f"Parabola: {_TAIL}"),
    ],
)
def test_function_extra_caption_is_not_repeated_when_the_author_already_wrote_it(
    monkeypatch: pytest.MonkeyPatch, caption: str, expected: str
) -> None:
    """Guardia anti-doppia coda (Q4): `if caption.rstrip().endswith(tail):
    tail = ""`; stessa regola in `FigureFrame.extraCaption` (WP5)."""
    monkeypatch.setattr(frs, "function_computed_caption", lambda c, *, language, asset_id="": _TAIL)
    content = {
        "introduction": "[FIG:F]",
        "sections": [],
        "summary": "",
        "visual_assets": [_asset("F", "function", '{"kind": "function_study"}', caption)],
    }
    html = _render(content, visual_svg_map={"F": SVG_B})
    assert _figures(html)[0]["caption"] == f'<span class="figure-label">Figura 1.</span> {expected}'


def test_render_figure_html_double_tail_guard_is_exact_suffix() -> None:
    kwargs: dict[str, Any] = {
        "body_html": Markup("<i/>"),
        "alt_text": "",
        "asset_id": "A",
        "fmt": "function",
        "number": 1,
        "labels": None,
        "variant": "lesson",
    }
    same = figure_markup.render_figure_html(caption="Testo. Coda.", extra_caption="Coda.", **kwargs)
    assert "Figura 1.</span> Testo. Coda.</figcaption>" in same
    other = figure_markup.render_figure_html(caption="Testo. Coda", extra_caption="Coda.", **kwargs)
    assert "Figura 1.</span> Testo. Coda. Coda.</figcaption>" in other
    empty = figure_markup.render_figure_html(caption="", extra_caption="Coda.", **kwargs)
    assert "Figura 1.</span> Coda.</figcaption>" in empty


def test_caption_and_computed_tail_are_separated_by_a_full_stop() -> None:
    """La coda calcolata è un periodo autonomo: giustapposta a una didascalia
    senza punteggiatura si leggeva «…razionale Zeri in x = −1, 1.». Il
    punto lo aggiunge il partial, stessa regola di `FigureFrame` (TIP-9)."""
    kwargs: dict[str, Any] = {
        "body_html": Markup("<i/>"),
        "alt_text": "",
        "asset_id": "A",
        "fmt": "function",
        "number": 1,
        "labels": None,
        "variant": "lesson",
    }
    for caption, expected in (
        ("Studio della funzione", "Studio della funzione. Coda."),
        ("Studio della funzione.", "Studio della funzione. Coda."),
        ("Domanda?", "Domanda? Coda."),
        ("Titolo:", "Titolo: Coda."),
        ("", "Coda."),
    ):
        html = figure_markup.render_figure_html(caption=caption, extra_caption="Coda.", **kwargs)
        assert f"Figura 1.</span> {expected}</figcaption>" in html, caption
    # Senza coda la didascalia resta quella del docente, punto compreso.
    plain = figure_markup.render_figure_html(caption="Senza coda", extra_caption="", **kwargs)
    assert "Figura 1.</span> Senza coda</figcaption>" in plain


def test_function_extra_caption_not_requested_on_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        frs,
        "function_computed_caption",
        lambda c, *, language, asset_id="": calls.append(c) or "x",
    )
    content = {
        "introduction": "[FIG:F]",
        "sections": [],
        "summary": "",
        "visual_assets": [_asset("F", "function", "{}", "Parabola")],
    }
    with structlog.testing.capture_logs():
        _render(content)
    assert calls == []


def test_function_computed_caption_reads_the_engine_cache_without_rendering() -> None:
    """Spec valida ma mai renderizzata → nessun calcolo, stringa vuota;
    spec non valida → stringa vuota."""
    ffs.clear_result_cache()
    spec = json.dumps(
        {"kind": "function_study", "expressions": [{"expr": "x**2 - 4"}], "domain": [-3, 3]}
    )
    assert frs.function_computed_caption(spec, language="it") == ""
    assert frs.function_computed_caption("{non json", language="it") == ""
    assert frs.function_computed_caption("```json\n{}\n```", language="it") == ""


@pytest.mark.skipif(not ffs.dependencies_available(), reason="numpy, matplotlib o sympy assenti")
def test_function_computed_caption_after_render_is_localized() -> None:
    ffs.clear_result_cache()
    data = {
        "kind": "function_study",
        "expressions": [{"expr": "x**2 - 1"}],
        "domain": [-3, 3],
        "show": ["zeros"],
    }
    content = json.dumps(data)
    renderer = frs.REGISTRY["function"]
    svg = renderer.render_svg(content, asset_id="F")
    assert svg is not None
    it = frs.function_computed_caption(content, language="it")
    en = frs.function_computed_caption(content, language="en")
    assert it.startswith("Zeri in x = ")
    assert en.startswith("Zeros at x = ")
    # Con fence ```json (contenuto non ancora sanificato) il risultato è lo stesso.
    assert frs.function_computed_caption(f"```json\n{content}\n```", language="it") == it


_FUNCTION_SPEC = json.dumps(
    {
        "kind": "function_study",
        "expressions": [{"expr": "x**2 - 1"}],
        "domain": [-3, 3],
        "show": ["zeros"],
    }
)


def _function_content(caption: str = "Parabola") -> dict[str, Any]:
    return {
        "introduction": "[FIG:F]",
        "sections": [],
        "summary": "",
        "visual_assets": [_asset("F", "function", _FUNCTION_SPEC, caption)],
    }


def test_function_caption_missing_from_the_result_cache_is_logged() -> None:
    """SVG in mappa (cache SVG) ma risultato del motore espulso: la coda
    manca e il fatto è nel log (`figure_caption_missing`), mai silenzioso."""
    ffs.clear_result_cache()
    with structlog.testing.capture_logs() as logs:
        html = _render(_function_content(), visual_svg_map={"F": SVG_B})
    assert _figures(html)[0]["caption"] == '<span class="figure-label">Figura 1.</span> Parabola'
    events = [e for e in logs if e["event"] == "figure_caption_missing"]
    assert len(events) == 1
    assert events[0]["log_level"] == "warning"
    assert events[0]["asset_id"] == "F"
    assert events[0]["format"] == "function"
    assert events[0]["reason"] == "result_not_cached"
    # Spec non valida: nessun log (l'SVG a monte non esiste e il fallback è già loggato).
    with structlog.testing.capture_logs() as logs:
        assert frs.function_computed_caption("{non json", language="it", asset_id="X") == ""
    assert logs == []


async def test_function_svg_cache_hit_without_engine_result_is_rerendered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sequenza reale: export → anteprime dell'editor che espellono il
    risultato dalla cache dei risultati (l'SVG resta in quella degli SVG)
    → ri-export: `render_svg_map` non serve l'hit «incompleto», rimanda la
    figura al renderer, che ricalcola e ripopola la cache dei risultati;
    la coda della didascalia torna nel PDF. Motore finto: nessuna
    dipendenza numerica richiesta."""
    calls: list[str] = []

    def fake_render_function_sync(spec: Any, *, language: str | None, **_kw: Any) -> Any:
        calls.append(language or "")
        base = ffs.FunctionRenderResult(
            svg=SVG_B,
            computed={"variable": "x", "zeros": [{"x": 1.0, "exact": "1"}]},
            latex=[],
            warnings=[],
            approximate=False,
            computed_caption="",
            content_hash=spec.content_hash(),
        )
        ffs._cache_put(ffs.result_key(spec), base)
        return ffs._with_caption(base, language)

    monkeypatch.setattr(ffs, "render_function_sync", fake_render_function_sync)
    monkeypatch.setattr(frs.FunctionRenderer, "available", lambda self: True)
    frs.available_formats.cache_clear()
    ffs.clear_result_cache()
    frs.clear_svg_cache()
    try:
        assets = _function_content()["visual_assets"]
        first = await frs.render_svg_map(assets, language="it")
        assert first == {"F": SVG_B} and calls == [""]
        # Hit completo: nessun ricalcolo.
        assert await frs.render_svg_map(assets, language="it") == first and calls == [""]
        # Eviction dalla sola cache dei risultati (anteprime dell'editor).
        ffs.clear_result_cache()
        assert frs._cache_get(frs.cache_key("function", _FUNCTION_SPEC)) == SVG_B
        with structlog.testing.capture_logs() as logs:
            again = await frs.render_svg_map(assets, language="it")
            html = _render(_function_content(), visual_svg_map=again)
        assert again == first and calls == ["", ""]
        assert _figures(html)[0]["caption"] == (
            '<span class="figure-label">Figura 1.</span> Parabola. Zeri in x = 1.'
        )
        assert not [e for e in logs if e["event"] == "figure_caption_missing"]
        # Anche `render_svg` diretto (validazione profonda, batch) ripopola.
        ffs.clear_result_cache()
        assert frs.REGISTRY["function"].render_svg(_FUNCTION_SPEC, asset_id="F") == SVG_B
        assert calls == ["", "", ""]
        assert frs.function_computed_caption(_FUNCTION_SPEC, language="en") == "Zeros at x = 1."
    finally:
        ffs.clear_result_cache()
        frs.clear_svg_cache()
        frs.available_formats.cache_clear()


@pytest.mark.skipif(not ffs.dependencies_available(), reason="numpy, matplotlib o sympy assenti")
async def test_function_caption_survives_result_cache_eviction_with_the_real_engine() -> None:
    ffs.clear_result_cache()
    frs.clear_svg_cache()
    frs.available_formats.cache_clear()
    try:
        assets = _function_content()["visual_assets"]
        svg_map = await frs.render_svg_map(assets, language="it")
        assert "F" in svg_map
        ffs.clear_result_cache()  # eviction simulata fra due export
        with structlog.testing.capture_logs() as logs:
            svg_map_again = await frs.render_svg_map(assets, language="it")
            html = _render(_function_content(), visual_svg_map=svg_map_again)
        assert svg_map_again == svg_map  # byte-identico (hashsalt fisso)
        caption = _figures(html)[0]["caption"]
        assert caption.startswith(
            '<span class="figure-label">Figura 1.</span> Parabola. Zeri in x = '
        )
        assert not [e for e in logs if e["event"] == "figure_caption_missing"]
    finally:
        ffs.clear_result_cache()
        frs.clear_svg_cache()
        frs.available_formats.cache_clear()


# ---------------------------------------------------------------------------
# Slide e frame video: «Figura.» senza numero, tutto `<img>`
# ---------------------------------------------------------------------------


def _slides_lesson() -> CourseLesson:
    content_raw = {
        "introduction": "Testo [FIG:A].",
        "sections": [],
        "summary": "",
        "visual_assets": [
            _asset("A", "mermaid", "flowchart LR\n A --> B", "Figura 3. Schema"),
            _asset("V", "vegalite", '{"mark": "bar"}', "Barre"),
        ],
    }
    slides_raw = {
        "slides": [
            {
                "slide_id": "s1",
                "type": "diagram",
                "title": "Uno",
                "bullets": [],
                "references_assets": ["A", "V", "N1", "N2"],
            }
        ],
        "new_assets": [
            _asset("N1", "dot", "digraph { n }", "Nuovo grafo"),
            _asset("N2", "dot", "digraph { m }", "Senza render"),
        ],
    }
    return _lesson(content_raw, slides_raw)


def test_slides_use_unnumbered_label_and_img_for_every_format() -> None:
    lesson = _slides_lesson()
    with structlog.testing.capture_logs() as logs:
        html = slides_pdf.render_slides_html(
            course=_course("it"),
            lesson=lesson,
            organization=None,
            slide_template=None,
            enable_split=False,
            visual_svg_map={"A": SVG_A, "V": SVG_B, "N1": SVG_B},
        )
    figs = _figures(html)
    assert [f["id"] for f in figs] == ["A", "V", "N1", "N2"]
    assert all(_label(f) == "Figura." for f in figs)
    assert all("figure--slide" in f["cls"] for f in figs)
    assert not re.search(r"Figura \d", html)
    bodies = {f["id"]: f["body"] for f in figs}
    # Mermaid nelle slide: `<img class="mermaid-svg">` byte-identico al blocco precedente.
    assert bodies["A"] == f'<img class="mermaid-svg" src="{svg_to_data_uri(SVG_A)}" alt="" />'
    assert bodies["V"] == f'<img class="figure-svg" src="{svg_to_data_uri(SVG_B)}" alt="" />'
    assert bodies["N1"].startswith('<img class="figure-svg"')
    assert bodies["N2"] == '<pre class="figure-fallback">digraph { m }</pre>'
    assert figs[0]["caption"] == '<span class="figure-label">Figura.</span> Schema'
    assert "<svg" not in html.split("</style>", 1)[1]  # nessun SVG inline nel body slide
    assert '<div class="mermaid-svg">' not in html
    fallbacks = [e for e in logs if e["event"] == "figure_render_fallback"]
    assert [(e["asset_id"], e["format"], e["lesson_code"]) for e in fallbacks] == [
        ("N2", "dot", "M1.L1")
    ]


def _slides_lesson_with_blocks() -> CourseLesson:
    content_raw = {
        "introduction": "Testo [TAB:t1], [EQ:e1], [EQ:lem] ed [EX:x1].",
        "sections": [],
        "summary": "",
        "tables": [{"table_id": "t1", "caption": "Confronto", "markdown": "| a |\n|---|\n| 1 |"}],
        "equations": [
            {"equation_id": "e1", "latex": "x=1"},
            {
                "equation_id": "lem",
                "kind": "lemma",
                "latex": "y=2",
                "label": "Weierstrass",
                "statement": "Ogni successione limitata ha una sottosuccessione convergente.",
            },
        ],
        "examples": [{"example_id": "x1", "title": "Un esempio", "content": "Contenuto."}],
    }
    slides_raw = {
        "slides": [
            {
                "slide_id": "s1",
                "type": "table",
                "title": "Blocchi",
                "bullets": [],
                "references_assets": ["t1", "e1", "lem", "x1"],
            }
        ],
        "new_assets": [],
    }
    return _lesson(content_raw, slides_raw)


@pytest.mark.parametrize(
    ("language", "table", "equation", "example"),
    [("it", "Tabella.", "Equazione.", "Esempio."), ("en", "Table.", "Equation.", "Example.")],
)
def test_slides_use_unnumbered_labels_for_tables_equations_examples(
    language: str, table: str, equation: str, example: str
) -> None:
    """Slide e frame video: forma non numerata («Tabella.», A2) e teorema
    byte-identico a prima (sola parola del kind, nessun numero)."""
    html = slides_pdf.render_slides_html(
        course=_course(language),
        lesson=_slides_lesson_with_blocks(),
        organization=None,
        slide_template=None,
        enable_split=False,
    )
    assert f'<span class="figure-label">{table}</span> Confronto' in html
    assert f'<figcaption><span class="figure-label">{equation}</span></figcaption>' in html
    assert (
        f'<div class="example-title"><span class="figure-label">{example}</span> Un esempio</div>'
        in html
    )
    assert '<div class="theorem-head">Lemma Weierstrass</div>' in html
    assert not re.search(r"(Tabella|Equazione|Esempio|Table|Equation|Example|Lemma) \d", html)


def test_slides_english_label() -> None:
    html = slides_pdf.render_slides_html(
        course=_course("en"),
        lesson=_slides_lesson(),
        organization=None,
        slide_template=None,
        enable_split=False,
        visual_svg_map={"A": SVG_A, "V": SVG_B, "N1": SVG_B, "N2": SVG_B},
    )
    assert {_label(f) for f in _figures(html)} == {"Figure."}


def test_slides_merge_legacy_mermaid_svg_map() -> None:
    html = slides_pdf.render_slides_html(
        course=_course("it"),
        lesson=_slides_lesson(),
        organization=None,
        slide_template=None,
        enable_split=False,
        mermaid_svg_map={"A": SVG_A, "V": SVG_A},
        visual_svg_map={"V": SVG_B, "N1": SVG_B, "N2": SVG_B},
    )
    bodies = {f["id"]: f["body"] for f in _figures(html)}
    assert svg_to_data_uri(SVG_A) in bodies["A"]
    assert svg_to_data_uri(SVG_B) in bodies["V"]


def test_svg_to_data_uri_is_reexported_not_copied() -> None:
    assert slides_pdf._svg_to_data_uri is svg_to_data_uri


# ---------------------------------------------------------------------------
# Pre-render: alias storici e delega al registro
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prerender_for_lesson_delegates_to_render_svg_map(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    async def fake_render_svg_map(assets, *, language):
        seen["assets"], seen["language"] = list(assets), language
        return {"A": SVG_A}

    monkeypatch.setattr(frs, "render_svg_map", fake_render_svg_map)
    content = {"visual_assets": [_asset("A", "dot", "digraph { a }"), "non-dict", None]}
    assert await pdf._prerender_visual_assets_for_lesson(content, language="en") == {"A": SVG_A}
    assert seen["language"] == "en"
    assert [a["asset_id"] for a in seen["assets"]] == ["A"]
    assert pdf._prerender_mermaid_for_lesson is pdf._prerender_visual_assets_for_lesson
    assert await pdf._prerender_visual_assets_for_lesson({"visual_assets": []}) == {}
    assert await pdf._prerender_visual_assets_for_lesson({}) == {}


@pytest.mark.asyncio
async def test_prerender_for_slides_merges_content_and_new_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    async def fake_render_svg_map(assets, *, language):
        seen["ids"], seen["language"] = [a["asset_id"] for a in assets], language
        return {}

    monkeypatch.setattr(frs, "render_svg_map", fake_render_svg_map)
    content_raw = {"visual_assets": [_asset("A", "mermaid", "flowchart LR\n A"), 3]}
    new_assets = [_asset("N1", "vegalite", "{}"), "x"]
    await slides_pdf._prerender_mermaid_for_slides(content_raw, new_assets, language="en")
    assert seen == {"ids": ["A", "N1"], "language": "en"}
    assert (
        slides_pdf._prerender_visual_assets_for_slides is slides_pdf._prerender_mermaid_for_slides
    )
    # Id in collisione con una figura delle Dispense: il nuovo asset non
    # entra nel batch, altrimenti il suo SVG sostituirebbe quello della
    # figura di Fase 3 sotto la didascalia di quest'ultima (COR-8).
    collisione = [_asset("a", "dot", "digraph { x -> y }")]
    await slides_pdf._prerender_mermaid_for_slides(content_raw, collisione, language="it")
    assert seen["ids"] == ["A"]


# ---------------------------------------------------------------------------
# `render_figure_html` e il partial
# ---------------------------------------------------------------------------


def test_render_figure_html_keeps_body_markup_and_escapes_the_rest() -> None:
    html = figure_markup.render_figure_html(
        body_html=Markup('<div class="mermaid-svg"><svg/></div>'),
        caption="Fig. 2: Testo <b>",
        alt_text='a "b"',
        asset_id='A"1',
        fmt="mermaid",
        number=2,
        labels=figure_labels("it"),
        variant="lesson",
    )
    assert '<div class="figure-body"><div class="mermaid-svg"><svg/></div></div>' in html
    assert 'data-asset-id="A&#34;1"' in html
    assert 'aria-label="a &#34;b&#34;"' in html
    assert '<span class="figure-label">Figura 2.</span> Testo &lt;b&gt;' in html
    assert "\n\n" not in html


def test_render_figure_html_fallback_neutralizes_blank_lines() -> None:
    html = figure_markup.render_figure_html(
        body_html=None,
        caption="",
        alt_text="",
        asset_id="X",
        fmt="vegalite",
        number=None,
        labels=None,
        variant="slide",
        fallback_source='{\n\n  "a": 1\r\n\n}',
    )
    assert "\n\n" not in html
    assert '<pre class="figure-fallback">{\n\u00a0\n  &#34;a&#34;: 1\n\u00a0\n}</pre>' in html
    assert '<span class="figure-label">Figura.</span></figcaption>' in html
    assert "figure--slide figure--vegalite" in html


def test_render_figure_html_removes_blank_lines_from_the_body() -> None:
    """Una riga vuota dentro l'SVG inline chiuderebbe l'HTML block di
    markdown-it: il partial la rimuove (spazio bianco fra tag). Senza righe
    vuote il body è byte-identico."""
    svg = '<svg xmlns="http://www.w3.org/2000/svg">\n\n  \n<text>x</text>\r\n\r\n</svg>'
    html = figure_markup.render_figure_html(
        body_html=Markup(f'<div class="mermaid-svg">{svg}</div>'),
        caption="c",
        alt_text="",
        asset_id="A",
        fmt="mermaid",
        number=1,
        labels=None,
        variant="lesson",
    )
    assert "\n\n" not in html and "\r\n\r\n" not in html
    assert '<svg xmlns="http://www.w3.org/2000/svg">\n<text>x</text>\r\n</svg>' in html
    assert figure_markup._body_without_blank_lines(Markup(SVG_A)) == SVG_A
    # Nella dispensa il wrapper resta un unico HTML block, parsabile.
    content = {
        "introduction": "Prima.\n\n[FIG:A]\n\nDopo.",
        "sections": [],
        "summary": "",
        "visual_assets": [_asset("A", "mermaid", "flowchart LR\n A", "Cap")],
    }
    page = _render(content, visual_svg_map={"A": svg})
    fig = _figures(page)[0]
    assert fig["body"].startswith('<div class="mermaid-svg"><svg')
    assert "<p><text>" not in page and "<p></figure>" not in page and "</div></p>" not in page


def test_render_figure_html_sanitizes_format_class_and_uses_unnumbered_label() -> None:
    html = figure_markup.render_figure_html(
        body_html=Markup("<i/>"),
        caption="c",
        alt_text="",
        asset_id="A",
        fmt='We ird"',
        number=None,
        labels=figure_labels("en"),
        variant="lesson",
        extra_caption="  extra\n\ncoda ",
    )
    assert "figure--weird" in html
    assert '<span class="figure-label">Figure.</span> c. extra coda</figcaption>' in html


def test_figure_label_interpolates_number() -> None:
    assert figure_markup.figure_label(figure_labels("it"), 12) == "Figura 12."
    assert figure_markup.figure_label(figure_labels("en"), None) == "Figure."


def test_partial_lives_in_templates_partials_and_has_no_blank_lines() -> None:
    partial = figure_markup.PARTIALS_DIR / "figure.html.j2"
    assert partial.is_file()
    assert figure_markup.PARTIALS_DIR == _TEMPLATES / "partials"


# ---------------------------------------------------------------------------
# CSS dei template
# ---------------------------------------------------------------------------


def test_lesson_template_css_for_figures() -> None:
    css = (_TEMPLATES / "lesson_pdf.html.j2").read_text(encoding="utf-8")
    assert "figure.table, figure.equation {" in css
    assert "figure.visual {" in css and "border: none" in css
    assert ".figure-caption {" in css and ".figure-label { font-weight: 700; }" in css
    assert ".figure-svg {" in css
    m = re.search(r"\n    \.figure-svg \{(.*?)\n    \}", css, re.S)
    assert m, "regola .figure-svg assente"
    svg_rule = m.group(1)
    assert "max-height: {{ max_figure_height_cm }}cm" in svg_rule
    assert re.search(r"(?m)^\s*width:", svg_rule) is None  # solo max-width, mai width: 100%
    assert ".mermaid-fallback, .figure-fallback {" in css
    assert ".missing-asset {" in css
    assert "figure.visual:has(.mermaid-svg) .figure-body" in css
    # Etichette «Tabella N.» / «Equazione N.» in tondo dentro la didascalia in corsivo (D5).
    selector = "figure.table figcaption .figure-label, figure.equation figcaption .figure-label {"
    assert selector in css
    assert "font-style: normal" in css.split(selector, 1)[1].split("}", 1)[0]
    # La card generica sulle `figure` non esiste più.
    assert re.search(r"^\s*figure \{[^}]*border:", css, re.M) is None


def test_slides_template_css_for_figures() -> None:
    raw = (_TEMPLATES / "lesson_slides_pdf.html.j2").read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)  # solo regole, senza commenti
    caption_rule = css.split(".slide-asset figcaption {", 1)[1].split("}", 1)[0]
    assert "font-size: 8pt" in caption_rule and "font-style: normal" in caption_rule
    assert ".slide-asset .figure-label {" in css
    unified = css.split(".slide-asset .figure-svg,", 1)[1].split("}", 1)[0]
    assert ".slide-asset .mermaid-svg," in unified and ".slide-asset .uploaded-image {" in unified
    assert "max-height: 80mm" in unified
    assert "max-height: 100%" not in css
    assert ".slide-asset .figure-fallback," in css
    assert ".slide-asset .missing-asset," in css


# ---------------------------------------------------------------------------
# WeasyPrint 69: label degli SVG Mermaid 11 e `<img data:svg>` (voce 8)
# ---------------------------------------------------------------------------


def _weasyprint():
    try:
        import weasyprint
    except (ImportError, OSError) as exc:  # su macOS serve DYLD_FALLBACK_LIBRARY_PATH
        pytest.skip(f"weasyprint non importabile: {exc}")
    return weasyprint


def _pdf_text_and_warnings(weasyprint: Any, html: str) -> tuple[bytes, str, list[str]]:
    """PDF, testo estratto (pypdf) e messaggi del logger `weasyprint` di
    livello WARNING o superiore sopravvissuti al filtro del rumore SVG."""
    pypdf = pytest.importorskip("pypdf")
    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Collector(level=logging.WARNING)
    handler.addFilter(_WeasyPrintSvgNoiseFilter())
    logger = logging.getLogger("weasyprint")
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        data = weasyprint.HTML(string=html).write_pdf()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
    text = "\n".join(page.extract_text() or "" for page in pypdf.PdfReader(io.BytesIO(data)).pages)
    return data, text, [r.getMessage() for r in records]


def test_weasyprint_renders_mermaid11_labels_inline() -> None:
    weasyprint = _weasyprint()
    svg = (_FIXTURES / "mermaid11_flowchart.svg").read_text(encoding="utf-8")
    labels = [t for t in re.findall(r"<tspan[^>]*>([^<]+)</tspan>", svg) if t.strip()]
    assert labels, "la fixture deve contenere label testuali"
    content = {
        "introduction": "Vedi [FIG:A].",
        "sections": [],
        "summary": "",
        "visual_assets": [_asset("A", "mermaid", "flowchart LR\n A --> B", "Schema logico")],
    }
    html = _render(content, visual_svg_map={"A": svg})
    data, text, warnings = _pdf_text_and_warnings(weasyprint, html)
    assert data.startswith(b"%PDF")
    assert "Figura 1." in text and "Schema logico" in text
    for label in labels:
        assert label in text, label
    assert warnings == []


def test_weasyprint_renders_img_data_svg_from_matplotlib() -> None:
    weasyprint = _weasyprint()
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")
    from matplotlib import rc_context
    from matplotlib.figure import Figure

    from app.services.figure_theme import MATPLOTLIB_RC

    with rc_context(MATPLOTLIB_RC):
        fig = Figure(figsize=(4, 3), dpi=100)
        ax = fig.add_subplot(111)
        ax.plot([0, 1], [0, 1])
        ax.set_xlabel("ascissa")
        ax.text(0.4, 0.6, "etichetta")
        buf = io.StringIO()
        fig.savefig(buf, format="svg", metadata={"Date": None, "Creator": None})
    normalized = normalize_svg(buf.getvalue(), max_bytes=2_000_000)
    assert normalized.width_px and normalized.height_px
    content = {
        "introduction": "Vedi [FIG:F].",
        "sections": [],
        "summary": "",
        "visual_assets": [_asset("F", "function", "{}", "Retta")],
    }
    html = _render(content, visual_svg_map={"F": normalized.svg})
    assert '<img class="figure-svg" src="data:image/svg+xml;base64,' in html
    data, text, warnings = _pdf_text_and_warnings(weasyprint, html)
    assert data.startswith(b"%PDF")
    assert "Figura 1." in text and "Retta" in text
    assert "etichetta" in text and "ascissa" in text
    assert warnings == []


@pytest.mark.skipif(
    not (frs.REGISTRY["dot"].available()), reason="binario `dot` (graphviz) assente"
)
def test_weasyprint_renders_img_data_svg_from_dot() -> None:
    weasyprint = _weasyprint()
    svg = frs.REGISTRY["dot"].render_svg(
        'digraph { rankdir=LR; Ipotesi -> Tesi [label="deduzione"] }'
    )
    assert svg
    content = {
        "introduction": "Vedi [FIG:D].",
        "sections": [],
        "summary": "",
        "visual_assets": [_asset("D", "dot", "digraph { a }", "Grafo")],
    }
    html = _render(content, visual_svg_map={"D": svg})
    _data, text, warnings = _pdf_text_and_warnings(weasyprint, html)
    assert "Ipotesi" in text and "Tesi" in text and "deduzione" in text
    assert warnings == []
