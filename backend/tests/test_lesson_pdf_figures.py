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
  la forma insegnata dal prompt di Fase 3 (tag su riga propria, richiamo a
  parole) lascia la frase intatta (WP5, D17);
- punti chiave e riferimenti storici resi verbatim (3+3 `<li>`), contenuto
  nuovo deduplicato dallo schema (2+1): nessuna dedup in lettura (B5/D18);
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
from collections.abc import Iterator
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
from app.services import figure_markup, slide_geometry
from app.services import figure_render_service as frs
from app.services import mermaid_prerender as mp
from app.services.figure_scale import FigureFitEntry, SvgMetrics, fit_figure_width_mm
from app.services.figure_theme import figure_labels
from app.services.svg_normalize import normalize_svg, svg_intrinsic_box, svg_to_data_uri
from tests.course_builders import build_lesson_content_output

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


def test_the_layout_taught_by_the_phase3_prompt_leaves_the_prose_alone() -> None:
    """D17: la forma che il prompt di Fase 3 chiede (richiamo a parole, tag
    una volta su riga propria fra righe vuote) dà un solo blocco dopo il
    paragrafo e nessun rimando «Figura N» dentro la frase."""
    html = _render(
        _one_figure_content(
            "Il ciclo ha tre stati, come mostra la figura.\n\n[FIG:A]\n\n"
            "Il terzo stato chiude il ciclo."
        ),
        visual_svg_map={"A": SVG_A},
    )
    assert "<p>Il ciclo ha tre stati, come mostra la figura.</p>" in html
    assert "<p>Il terzo stato chiude il ciclo.</p>" in html
    assert [(f["id"], _label(f)) for f in _figures(html)] == [("A", "Figura 1.")]
    assert html.count("Figura 1") == 1  # solo l'etichetta della didascalia
    first, block, second = (
        html.index("come mostra la figura.</p>"),
        html.index('data-asset-id="A"'),
        html.index("<p>Il terzo stato"),
    )
    assert first < block < second


# ---------------------------------------------------------------------------
# Punti chiave e riferimenti: nessuna dedup in lettura (B5/D18)
# ---------------------------------------------------------------------------

_TAKEAWAYS_UL_RE = re.compile(r'<section class="key-takeaways pb-avoid">.*?<ul>(.*?)</ul>', re.S)
_REFERENCES_OL_RE = re.compile(r'<ol class="references-list">(.*?)</ol>', re.S)


def _tail_counts(html: str) -> tuple[int, int]:
    takeaways = _TAKEAWAYS_UL_RE.search(html)
    references = _REFERENCES_OL_RE.search(html)
    assert takeaways and references
    return takeaways.group(1).count("<li>"), references.group(1).count("<li>")


_SAME_REFERENCE = {"citation": "Rossi 2020", "source": "suggerimento_generale"}


def test_pdf_renders_historical_duplicates_verbatim() -> None:
    """Un `content_raw` scritto prima di WP5 è reso com'è: la normalizzazione
    sta solo al confine di scrittura (schema), mai in lettura. Il pre-render
    inline della coda (`_tail`) deve iterare la lista grezza e conservare il
    numero di voci."""
    content = {
        "introduction": "Testo.",
        "sections": [],
        "summary": "",
        "key_takeaways": ["Uno", "uno", "Uno "],
        "references": [dict(_SAME_REFERENCE) for _ in range(3)],
    }
    assert _tail_counts(_render(content)) == (3, 3)


def test_pdf_lists_one_li_per_unique_entry_for_new_content() -> None:
    """Contenuto nuovo: schema → `model_dump()` → template, senza altra
    dedup a valle. Prima di WP5 dava 3 e 3."""
    out = build_lesson_content_output(
        key_takeaways=["Uno", "uno", "Due"],
        references=[dict(_SAME_REFERENCE) for _ in range(3)],
    )
    assert _tail_counts(_render(out.model_dump())) == (2, 1)


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
    # Stringhe del blocco precedente a WP4, carattere per carattere; la
    # larghezza dalla banda di leggibilità (D10) è l'ULTIMO attributo del
    # wrapper: SVG_A (10×10, testo senza corpo → costante Mermaid 14 px)
    # fluido su 168×242 mm va al tetto 11 pt: 2,77 mm.
    assert bodies["M"] == f'<div class="mermaid-svg" style="width:2.77mm">{SVG_A}</div>'
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
    # `style="width:…"` è l'ultimo attributo (D10): SVG_A come vegalite
    # (costante 11 px, fluido) al tetto 11 pt → 3,52 mm; SVG_B senza testo
    # → scala naturale (20 px = 5,29 mm).
    assert bodies["V"] == (
        f'<img class="figure-svg" src="{svg_to_data_uri(SVG_A)}" alt="barre" '
        'style="width:3.52mm" />'
    )
    assert bodies["D"] == (
        f'<img class="figure-svg" src="{svg_to_data_uri(SVG_B)}" alt="" style="width:5.29mm" />'
    )
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
    assert bodies["A"] == f'<div class="mermaid-svg" style="width:2.77mm">{SVG_A}</div>'
    # visual_svg_map prevale (SVG_B senza testo: scala naturale 5,29 mm).
    assert bodies["B"] == f'<div class="mermaid-svg" style="width:5.29mm">{SVG_B}</div>'


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
    # Mermaid nelle slide: `<img class="mermaid-svg">` byte-identico al blocco
    # precedente più la larghezza della banda slide (D10, tetto 14 pt) come
    # ultimo attributo; SVG_B senza testo resta a scala naturale.
    assert bodies["A"] == (
        f'<img class="mermaid-svg" src="{svg_to_data_uri(SVG_A)}" alt="" style="width:3.52mm" />'
    )
    assert bodies["V"] == (
        f'<img class="figure-svg" src="{svg_to_data_uri(SVG_B)}" alt="" style="width:5.29mm" />'
    )
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
    """La delega va a `render_figure_map` (record con le metriche, D10);
    `render_svg_map` ne è la proiezione `.svg`."""
    seen: dict[str, Any] = {}
    fig_a = frs.RenderedFigure.from_svg(SVG_A)

    async def fake_render_figure_map(assets, *, language):
        seen["assets"], seen["language"] = list(assets), language
        return {"A": fig_a}

    monkeypatch.setattr(frs, "render_figure_map", fake_render_figure_map)
    content = {"visual_assets": [_asset("A", "dot", "digraph { a }"), "non-dict", None]}
    got = await pdf._prerender_visual_assets_for_lesson(content, language="en")
    assert got == {"A": fig_a} and got["A"].svg == SVG_A
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

    async def fake_render_figure_map(assets, *, language):
        seen["ids"], seen["language"] = [a["asset_id"] for a in assets], language
        return {}

    monkeypatch.setattr(frs, "render_figure_map", fake_render_figure_map)
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
    # Senza viewBox non c'è fit (`figure_fit_skipped`): nessun attributo `style`.
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


def test_render_figure_html_box_goes_on_the_figure_after_aria_label() -> None:
    """Il box delle slide (D12) è uno `style` sul `<figure>` DOPO
    `aria-label`, sulla stessa riga e senza righe vuote: `_FIGURE_RE`
    continua a riconoscere il blocco; senza box il markup è identico a
    prima."""
    kwargs: dict[str, Any] = {
        "body_html": Markup('<img class="mermaid-svg" src="x" alt="" />'),
        "caption": "Schema",
        "alt_text": "alt",
        "asset_id": "A",
        "fmt": "mermaid",
        "number": None,
        "labels": figure_labels("it"),
        "variant": "slide",
    }
    with_box = figure_markup.render_figure_html(**kwargs, box=figure_markup.FigureBox(255, 70))
    style = 'style="--figure-w: 255.0mm; --figure-h: 70.0mm"'
    assert f'aria-label="alt" {style}>\n<div class="figure-body">' in with_box
    assert "\n\n" not in with_box
    assert [f["id"] for f in _figures(with_box)] == ["A"]
    without = figure_markup.render_figure_html(**kwargs)
    assert without == figure_markup.render_figure_html(**kwargs, box=None)
    assert "style=" not in without and 'aria-label="alt">\n<div class="figure-body">' in without
    assert with_box.replace(f" {style}", "") == without
    # `FigureBox`: al decimo, strettamente positivo.
    assert figure_markup.FigureBox(255, 86.633).style == "--figure-w: 255.0mm; --figure-h: 86.6mm"
    for w, h in ((0, 10), (255, -1), (255, 0.04)):
        with pytest.raises(ValueError):
            figure_markup.FigureBox(w, h)


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
    # Cintura D10: un `max-height` violato non deforma l'`<img>` a larghezza fissa.
    assert "object-fit: contain" in svg_rule
    # Il wrapper Mermaid a larghezza fissa è centrato e mai oltre il body
    # (prima `margin: 0 -1mm` sfruttava il padding del body).
    wrapper = re.search(r"\n    \.mermaid-svg \{(.*?)\n    \}", css, re.S)
    assert wrapper, "regola .mermaid-svg assente"
    assert "margin: 0 auto" in wrapper.group(1) and "max-width: 100%" in wrapper.group(1)
    assert "-1mm" not in wrapper.group(1)
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
    """Il cap fisso di 80 mm non vale più per le figure (D12): il loro tetto
    è `var(--figure-h)` dal box della pagina, la larghezza del wrapper è
    `var(--figure-w)`; la regola generica ripiega sugli 80 mm solo dove la
    variabile manca (equazioni ed esempi, che non ricevono box); gli unici
    `max-height` in mm restano i loghi dell'header (14 mm) e le formule del
    teorema (40 mm); nessun cambio tipografico sulla didascalia. Il
    fallback delle slide è in `white-space: pre` con `overflow: hidden`,
    quello della dispensa resta in `pre-wrap`."""
    raw = (_TEMPLATES / "lesson_slides_pdf.html.j2").read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)  # solo regole, senza commenti
    caption_rule = css.split(".slide-asset figcaption {", 1)[1].split("}", 1)[0]
    assert "font-size: 8pt" in caption_rule and "font-style: normal" in caption_rule
    assert ".slide-asset .figure-label {" in css
    assert re.findall(r"(?<![\d.])80mm", css) == ["80mm"]
    assert "max-height: 100%" not in css
    unified = css.split(".slide-asset .figure-svg,", 1)[1].split("}", 1)[0]
    assert ".slide-asset .mermaid-svg," in unified and ".slide-asset .uploaded-image {" in unified
    assert "max-height: var(--figure-h)" in unified and "max-width: 100%" in unified
    generic = css.split(".slide-asset svg,", 1)[1].split("}", 1)[0]
    assert ".slide-asset img {" in generic
    assert re.findall(r"max-height:[^;]*;", generic) == ["max-height: var(--figure-h, 80mm);"]
    body_rule = css.split(".slide-asset .figure-body {", 1)[1].split("}", 1)[0]
    assert "width: var(--figure-w)" in body_rule
    fallback = css.split(".slide-asset .figure-fallback,", 1)[1].split("}", 1)[0]
    assert "max-height: var(--figure-h)" in fallback
    # Il fallback delle slide non va a capo (D12, settimo giro): una riga
    # sorgente è una riga resa, le righe lunghe sono tagliate a destra.
    assert re.findall(r"white-space:\s*([\w-]+);", fallback) == ["pre"]
    assert re.findall(r"overflow:\s*([\w-]+);", fallback) == ["hidden"]
    # La dispensa resta in `pre-wrap` nel flusso di pagina.
    lesson_css = (_TEMPLATES / "lesson_pdf.html.j2").read_text(encoding="utf-8")
    lesson_fallback = lesson_css.split(".mermaid-fallback, .figure-fallback {", 1)[1]
    assert re.findall(r"white-space:\s*([\w-]+);", lesson_fallback.split("}", 1)[0]) == ["pre-wrap"]
    assert sorted(re.findall(r"max-height:\s*(\d+)mm", css)) == ["14", "40"]
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


# ---------------------------------------------------------------------------
# Banda di leggibilità (D10): larghezza dal corpo del testo, log e report
# ---------------------------------------------------------------------------

_MM = 96.0 / 25.4


def _v11_figure() -> tuple[str, frs.RenderedFigure]:
    """Fixture v11 post-processata con le metriche come le misura Chromium
    (14 px, sei testi): il percorso di produzione senza Chromium."""
    svg = mp._strip_mermaid_max_width(
        (_FIXTURES / "mermaid11_flowchart.svg").read_text(encoding="utf-8")
    )
    return svg, frs.RenderedFigure(svg, SvgMetrics(14.0, 14.0, 6, "measured"))


def _one_mermaid_content(asset_id: str = "A") -> dict[str, Any]:
    return {
        "introduction": f"Vedi [FIG:{asset_id}].",
        "sections": [],
        "summary": "",
        "visual_assets": [_asset(asset_id, "mermaid", "flowchart LR\n A --> B", "Schema")],
    }


def _one_slide_lesson(content: dict[str, Any], asset_id: str = "A") -> CourseLesson:
    slides_raw = {
        "slides": [
            {
                "slide_id": "s1",
                "type": "concept",
                "title": "Titolo",
                "body": "",
                "bullets": [],
                "references_assets": [asset_id],
            }
        ]
    }
    return _lesson(content, slides_raw)


def test_production_map_with_measured_metrics_pins_the_fit() -> None:
    """Golden del percorso misurato: il flowchart v11 (507,8×158, 14 px) va a
    140,76 mm in dispensa (tetto 11 pt del box 168×242) e a 179,15 mm nelle
    slide (tetto 14 pt del box di pagina 255×86,6, D12); l'SVG dentro il
    wrapper è byte-identico e nessun fallback del font viene loggato."""
    svg, fig = _v11_figure()
    report: list[FigureFitEntry] = []
    with structlog.testing.capture_logs() as logs:
        html = _render(_one_mermaid_content(), visual_svg_map={"A": fig}, fit_report=report)
    body = _figures(html)[0]["body"]
    assert body == f'<div class="mermaid-svg" style="width:140.76mm">{svg}</div>'
    fits = [e for e in logs if e["event"] == "figure_fit"]
    assert len(fits) == 1
    assert (fits[0]["width_mm"], fits[0]["text_pt"], fits[0]["in_band"]) == (140.76, 11.0, True)
    assert (fits[0]["font_source"], fits[0]["text_count"], fits[0]["variant"]) == (
        "measured",
        6,
        "lesson",
    )
    assert not [e for e in logs if e["event"] in ("figure_font_fallback", "figure_fit_out_of_band")]
    assert report == [
        FigureFitEntry(
            asset_id="A",
            fmt="mermaid",
            variant="lesson",
            width_mm=140.76,
            scale=1.0476,
            text_pt=11.0,
            band=(8.0, 11.0),
            in_band=True,
            font_source="measured",
            text_count=6,
        )
    ]
    slides = slides_pdf.render_slides_html(
        course=_course("it"),
        lesson=_one_slide_lesson(_one_mermaid_content()),
        organization=None,
        slide_template=None,
        enable_split=False,
        visual_svg_map={"A": fig},
        fit_report=(slide_report := []),
    )
    body = _figures(slides)[0]["body"]
    assert body == (
        f'<img class="mermaid-svg" src="{svg_to_data_uri(svg)}" alt="" style="width:179.15mm" />'
    )
    assert [(e.variant, e.width_mm, e.text_pt, e.in_band) for e in slide_report] == [
        ("slide", 179.15, 14.0, True)
    ]


def test_out_of_band_figure_is_logged_and_reported() -> None:
    """Banda irraggiungibile (gantt 1280 uu a 10 px): larghezza del box,
    `figure_fit_out_of_band` e voce `in_band=False` nel report (D13);
    metriche `root_rule` su Mermaid → `figure_font_fallback`; SVG senza
    viewBox → nessun `style` e `figure_fit_skipped`; stringa nella mappa →
    costante di formato, segnalata."""
    gantt = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="100%" viewBox="0 0 1280 148">'
        "<text>a</text></svg>"
    )
    content = {
        "introduction": "[FIG:G] [FIG:R] [FIG:N] [FIG:S]",
        "sections": [],
        "summary": "",
        "visual_assets": [
            _asset("G", "mermaid", "gantt", "Gantt"),
            _asset("R", "mermaid", "flowchart LR\n A", "Radice"),
            _asset("N", "mermaid", "flowchart LR\n A", "Senza viewBox"),
            _asset("S", "mermaid", "flowchart LR\n A", "Stringa"),
        ],
    }
    svg, _fig = _v11_figure()
    no_viewbox = '<svg xmlns="http://www.w3.org/2000/svg"><text>a</text></svg>'
    report: list[FigureFitEntry] = []
    with structlog.testing.capture_logs() as logs:
        html = _render(
            content,
            visual_svg_map={
                "G": frs.RenderedFigure(gantt, SvgMetrics(10.0, 10.0, 17, "measured")),
                "R": frs.RenderedFigure.from_svg(svg),
                "N": no_viewbox,
                "S": SVG_A,
            },
            fit_report=report,
        )
    bodies = {f["id"]: f["body"] for f in _figures(html)}
    assert bodies["G"] == f'<div class="mermaid-svg" style="width:168mm">{gantt}</div>'
    assert bodies["R"] == f'<div class="mermaid-svg" style="width:140.76mm">{svg}</div>'
    assert bodies["N"] == f'<div class="mermaid-svg">{no_viewbox}</div>'
    assert bodies["S"] == f'<div class="mermaid-svg" style="width:2.77mm">{SVG_A}</div>'
    by_event: dict[str, list[str]] = {}
    for e in logs:
        if e["event"].startswith("figure_fit") or e["event"] == "figure_font_fallback":
            by_event.setdefault(e["event"], []).append(e["asset_id"])
    assert by_event["figure_fit"] == ["G", "R", "S"]
    assert by_event["figure_fit_out_of_band"] == ["G"]
    assert by_event["figure_font_fallback"] == ["R", "S"]
    assert by_event["figure_fit_skipped"] == ["N"]
    out = next(e for e in logs if e["event"] == "figure_fit_out_of_band")
    assert (out["text_pt"], out["width_mm"], out["font_source"]) == (3.72, 168.0, "measured")
    fallback = {
        e["asset_id"]: e["font_source"] for e in logs if e["event"] == "figure_font_fallback"
    }
    assert fallback == {"R": "root_rule", "S": "constant"}
    assert [(e.asset_id, e.in_band, e.font_source) for e in report] == [
        ("G", False, "measured"),
        ("R", True, "root_rule"),
        ("S", True, "constant"),
    ]
    assert report[0].text_count == 17 and report[0].band == (8.0, 11.0)


def test_figure_fit_report_summary_lists_the_out_of_band_figures() -> None:
    """Il summary elenca le figure fuori banda e, a parte, quelle calcolate
    sulla costante di formato (il loro `in_band` è un'ipotesi); per la
    geometria (D14) i grafi con difetti o incroci oltre soglia e quelli
    senza misura degli incroci (Vega-Lite e `function` non sono grafi)."""
    entry = FigureFitEntry(
        "G", "mermaid", "lesson", 168.0, 0.4961, 3.72, (8.0, 11.0), False, "measured", 17
    )
    ok = FigureFitEntry(
        "A", "dot", "lesson", 29.28, 1.0, 10.0, (8.0, 11.0), True, "parsed", 4, crossings=0
    )
    assumed = FigureFitEntry(
        "V", "vegalite", "slide", 80.0, 1.2, 9.9, (10.0, 14.0), False, "constant", 2
    )
    guessed = FigureFitEntry(
        "F", "function", "lesson", 90.0, 1.0, 9.0, (8.0, 11.0), True, "constant", 2
    )
    flipped = FigureFitEntry(
        "C",
        "mermaid",
        "lesson",
        56.35,
        0.8179,
        8.59,
        (8.0, 11.0),
        True,
        "measured",
        26,
        crossings=0,
        direction_flipped=True,
    )
    dense_defect = "graph_too_dense: incroci fra archi 6 > 4 — riordina i nodi"
    dense = FigureFitEntry(
        "D",
        "dot",
        "lesson",
        120.0,
        0.8,
        8.0,
        (8.0, 11.0),
        True,
        "parsed",
        9,
        crossings=6,
        defects=(dense_defect,),
    )
    with structlog.testing.capture_logs() as logs:
        pdf._log_figure_fit_report(lesson_code="M1.L1", fit_report=[entry, ok])
        pdf._log_figure_fit_report(
            lesson_code="M1.L2", fit_report=[ok, assumed, guessed, dense, flipped]
        )
    assert logs == [
        {
            "event": "figure_fit_report",
            "log_level": "info",
            "lesson_code": "M1.L1",
            "total": 2,
            "in_band": 1,
            "out_of_band": [("G", "mermaid", 3.72, "measured")],
            "font_fallback": [],
            "geometry_defects": [],
            "measure_skipped": [("G", "mermaid")],
            "direction_flipped": [],
        },
        {
            "event": "figure_fit_report",
            "log_level": "info",
            "lesson_code": "M1.L2",
            "total": 5,
            "in_band": 4,
            "out_of_band": [("V", "vegalite", 9.9, "constant")],
            "font_fallback": [("V", "vegalite"), ("F", "function")],
            "geometry_defects": [("D", "dot", 6, [dense_defect])],
            "measure_skipped": [],
            "direction_flipped": [("C", 8.59)],
        },
    ]


@pytest.mark.parametrize("fmt", ["dot", "vegalite", "function"])
def test_constant_font_source_is_logged_for_every_format(fmt: str) -> None:
    """Lettura irrisolta (un `em` senza antenati, un `rem`) su un formato
    non Mermaid: il fit usa la costante di formato e lo dice con
    `figure_font_fallback`, non solo con l'info `figure_fit`; una lettura
    esatta non produce l'avviso."""
    em_without_parent = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="100%" viewBox="0 0 600 200">'
        '<text x="5" y="40" font-size="0.6em">relativo</text>'
        '<text x="5" y="80" font-size="14">voce</text></svg>'
    )
    rem = em_without_parent.replace('font-size="0.6em"', 'style="font-size:0.5rem"')
    exact = em_without_parent.replace('font-size="0.6em"', 'font-size="12"')
    content = {
        "introduction": "[FIG:E] [FIG:R] [FIG:X]",
        "sections": [],
        "summary": "",
        "visual_assets": [
            _asset("E", fmt, "x", "Relativo"),
            _asset("R", fmt, "x", "Rem"),
            _asset("X", fmt, "x", "Esatto"),
        ],
    }
    report: list[FigureFitEntry] = []
    with structlog.testing.capture_logs() as logs:
        _render(
            content,
            visual_svg_map={"E": em_without_parent, "R": rem, "X": exact},
            fit_report=report,
        )
    assert [(e.asset_id, e.font_source) for e in report] == [
        ("E", "constant"),
        ("R", "constant"),
        ("X", "parsed"),
    ]
    fallback = [
        (e["asset_id"], e["format"], e["font_source"])
        for e in logs
        if e["event"] == "figure_font_fallback"
    ]
    assert fallback == [("E", fmt, "constant"), ("R", fmt, "constant")]
    assert all(e["log_level"] == "warning" for e in logs if e["event"] == "figure_font_fallback")


def test_figure_box_comes_from_the_template_geometry() -> None:
    """Il box del fit viene da `_compute_template_margins_cm`: contenuto di
    170 mm su A4 con margine 20 e `max_figure_height_cm·10` (242 mm sotto la
    soglia misurata di 248,7); Letter e A3 seguono la larghezza del foglio."""
    default = pdf._compute_template_margins_cm(pdf._default_template_dict(language="it"))
    assert (default["figure_box_w_mm"], default["figure_box_h_mm"]) == (170.0, 242.0)
    assert default["figure_box_h_mm"] == default["max_figure_height_cm"] * 10
    letter = pdf._compute_template_margins_cm({"page_size": "Letter", "margin_mm": 15})
    assert letter["figure_box_w_mm"] == pytest.approx(215.9 - 30)
    a3 = pdf._compute_template_margins_cm({"page_size": "A3", "margin_mm": 25})
    assert a3["figure_box_w_mm"] == pytest.approx(297 - 50)
    # Senza box (chiamanti senza geometria) nessuna larghezza e nessun log del fit.
    with structlog.testing.capture_logs() as logs:
        block = pdf._render_visual_asset_block(
            _asset("A", "mermaid", "x", "Cap"), visual_svg_map={"A": SVG_A}, variant="lesson"
        )
    assert f'<div class="mermaid-svg">{SVG_A}</div>' in block
    assert not [e for e in logs if e["event"].startswith("figure_fit")]


def _walk(box: Any) -> Iterator[Any]:
    yield box
    children = getattr(box, "all_children", None)
    for child in children() if children else getattr(box, "children", []):
        yield from _walk(child)


def _box_class(box: Any) -> str:
    try:
        return (box.element.get("class") or "") if box.element is not None else ""
    except Exception:  # box anonimi senza elemento
        return ""


def _rendered_boxes(
    weasyprint: Any, html: str, tag: str, css_class: str
) -> list[tuple[float, float]]:
    """`(larghezza, altezza)` in mm dei box `<tag class="css_class">` del
    documento reso da WeasyPrint (camminata su `page._page_box`)."""
    out: list[tuple[float, float]] = []
    for page in weasyprint.HTML(string=html).render().pages:
        for box in _walk(page._page_box):
            if box.element_tag == tag and css_class in _box_class(box).split():
                out.append((box.width / _MM, box.height / _MM))
    return out


def test_weasyprint_applies_the_fitted_width() -> None:
    """WeasyPrint rende il wrapper Mermaid alla larghezza del fit (140,76 mm,
    altezza proporzionale) e l'`<img>` DOT alla sua larghezza intrinseca;
    nelle slide l'`<img>` Vega-Lite reale ha la larghezza del fit e non
    supera mai il box della pagina (86,6 mm con titolo su una riga, D12;
    `max-height: var(--figure-h)` come cintura)."""
    weasyprint = _weasyprint()
    _svg, fig = _v11_figure()
    html = _render(_one_mermaid_content(), visual_svg_map={"A": fig})
    ((w, h),) = _rendered_boxes(weasyprint, html, "div", "mermaid-svg")
    assert w == pytest.approx(140.76, abs=0.01)
    assert h == pytest.approx(140.76 * 158 / 507.828125, abs=0.05)
    if frs.REGISTRY["dot"].available():
        dot = frs.REGISTRY["dot"].render_svg('digraph { rankdir=LR; a -> b [label="arco"] }')
        assert dot
        box = svg_intrinsic_box(dot)
        assert box is not None and box.width_px is not None
        content = {
            "introduction": "Vedi [FIG:D].",
            "sections": [],
            "summary": "",
            "visual_assets": [_asset("D", "dot", "digraph { a }", "Grafo")],
        }
        dot_html = _render(content, visual_svg_map={"D": dot})
        expected = fit_figure_width_mm(
            vb_w=box.vb_w,
            vb_h=box.vb_h,
            base_font_px=10 * box.px_per_unit,
            box_w_mm=170,
            box_h_mm=242,
            intrinsic_w_px=box.width_px,
        )
        assert expected is not None and expected.scale == 1.0  # scala naturale
        ((w, h),) = _rendered_boxes(weasyprint, dot_html, "img", "figure-svg")
        assert w == pytest.approx(expected.width_mm, abs=0.02)
    if frs.REGISTRY["vegalite"].available():
        vl = frs.REGISTRY["vegalite"].render_svg(
            json.dumps(
                {
                    "data": {"values": [{"k": "a", "v": 3}, {"k": "b", "v": 5}]},
                    "mark": "bar",
                    "encoding": {
                        "x": {"field": "k", "type": "nominal"},
                        "y": {"field": "v", "type": "quantitative"},
                    },
                }
            )
        )
        assert vl
        box = svg_intrinsic_box(vl)
        assert box is not None
        content = {
            "introduction": "[FIG:V]",
            "sections": [],
            "summary": "",
            "visual_assets": [_asset("V", "vegalite", "{}", "Barre")],
        }
        report: list[FigureFitEntry] = []
        slides = slides_pdf.render_slides_html(
            course=_course("it"),
            lesson=_one_slide_lesson(content, "V"),
            organization=None,
            slide_template=None,
            enable_split=False,
            visual_svg_map={"V": vl},
            fit_report=report,
        )
        ((w, h),) = _rendered_boxes(weasyprint, slides, "img", "figure-svg")
        assert w == pytest.approx(report[0].width_mm, abs=0.02)
        budget = slide_geometry.page_figure_budget(title="Titolo", body="", bullets=[], n_blocks=1)
        box, _squeezed = slide_geometry.image_box(budget, caption_text="Figura. Barre")
        assert box.h_mm == 86.6
        assert h <= box.h_mm + 0.02
        assert f'style="{box.style}"' in slides
        assert report[0].in_band is False  # Vega-Lite resta fuori banda nelle slide (D13)
