"""Autoescape dei tre PDF, math nella prosa di slide e discorso, duplicati
di slide, regola max-1-visivo nel CRUD, note del discorso sanificate (WP4,
D19).

- i tre `Environment` Jinja (dispensa, slide, discorso) escapano i
  template `lesson_*.html.j2` (prima slide e discorso no:
  `select_autoescape(["html", "xml"])` non riconosce `.j2`);
- i campi d'autore delle slide e del discorso escono escapati; titolo,
  prosa e bullet delle slide, testo, note e titolo di slide del discorso
  passano da `render_markdown_inline` (testo escapato + math, niente
  markdown ricco) e i loro `$..$` arrivano al PDF come SVG, raccolti dal
  collector con la stessa grammatica;
- i `tpl.*` in contesto CSS restano CSS: colori e numeri `|safe`, font e
  URL dentro stringhe CSS con l'escape dedicato `css_string`; gli URL
  arrivano al fetcher di WeasyPrint identici (anche i loghi, in attributo:
  lì l'escape HTML è la codifica giusta e il parser la toglie);
- il piè di pagina del discorso (stringa CSS di `@bottom-center`)
  sopravvive a virgolette, backslash e `<` nei titoli;
- `references_assets` duplicati (anche per maiuscole) danno un solo blocco
  e un `slide_duplicate_asset_ref`;
- il CRUD delle slide rifiuta con 409 una seconda figura/tabella sulle
  sole slide a cui il PATCH aggiunge un visivo; una slide storica con due
  figure resta editabile nel resto e si può ridurre (3 -> 2 -> 1), non
  cambiare figura a parità di numero;
- il prompt e lo schema del discorso dichiarano per `delivery_notes` le
  regole TTS del testo (un'abbreviazione nelle note è un hard-fail);
- `delivery_notes` seguono la politica TTS di `text`;
- C12 (segment_id ripetuto nella mappa) è un limite dichiarato: la
  timeline lo rende due volte;
- contenuti d'autore nei motori (giro 1 di verifica): l'URL di un asset
  `image` resta dentro `src`; i frame video non eseguono il JavaScript del
  markdown di esempi, equazioni e tabelle (contesto con JavaScript spento);
  la guardia di rete chiude i WebSocket; WeasyPrint legge solo data URL e
  l'host dei media; riferimenti con spazi ai bordi risolti come nel CRUD;
  il percorso AI conta i visivi distinti come il CRUD.
"""

from __future__ import annotations

import asyncio
import base64
import io
import re
import threading
from collections.abc import Callable
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest
import structlog.testing
from markupsafe import Markup

from app.core.errors import ConflictError
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.models.course_module import CourseModule
from app.models.pdf_template import PdfTemplate
from app.models.slide_template import SlideTemplate
from app.schemas.course_lesson_slides import LessonSlideItem, LessonSlidesUpdateInput
from app.schemas.course_lesson_speech import LessonSpeechOutput
from app.services import course_lesson_pdf_service as pdf
from app.services import course_lesson_slides_crud as slides_crud
from app.services import course_lesson_slides_pdf_service as slides_pdf
from app.services import course_lesson_speech_pdf_service as speech_pdf
from app.services import course_lesson_speech_service as speech_svc
from app.services import openai_lesson_speech_service as speech_ai
from tests.test_lesson_pdf_figures import SVG_A, _pdf_text_and_warnings, _weasyprint

# SVG con le dimensioni di un MathJax reale (`$G = 2^{10}$`): ex e
# `vertical-align`, come li emette `_prerender_math_to_svg_batch`.
MJ_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="7.714ex" height="2.072ex" role="img" '
    'focusable="false" viewBox="0 -833.9 3409.7 915.9" aria-hidden="true" '
    'style="vertical-align: -0.186ex;"><rect x="0" y="-833.9" width="3409.7" height="915.9"/>'
    "</svg>"
)
_URL_BG = "https://media.example/bg.png?a=1&b=2"
_URL_LOGO_L = "https://media.example/logo-l.png?x=1&y=2"
_URL_LOGO_R = "https://media.example/logo-r.png?q=a&amp=1"
_PNG_1PX = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00"
    b"\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7"
    b"5\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82"
)


class _AnyMathMap(dict):
    """Mappa MathJax «completa»: ogni chiave cercata è registrata (`seen`)
    e resa con `MJ_SVG`."""

    def __init__(self) -> None:
        super().__init__()
        self.seen: list[tuple[str, str]] = []

    def __bool__(self) -> bool:
        return True

    def get(self, key: Any, default: Any = None) -> str:
        self.seen.append(key)
        return MJ_SVG


def _course(title: str = "Corso di prova") -> Course:
    return Course(title=title, language_code="it", cfu=6)


def _slide(slide_id: str, **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "slide_id": slide_id,
        "slide_number": 1,
        "type": "concept",
        "title": "Titolo",
        "body": "",
        "bullets": [],
        "references_assets": [],
    }
    return {**base, **fields}


def _visual(asset_id: str, caption: str = "Schema") -> dict[str, Any]:
    return {"asset_id": asset_id, "format": "mermaid", "content": "flowchart LR\n A-->B",
            "caption": caption}  # fmt: skip


def _render_slides(
    slides: list[dict[str, Any]],
    *,
    content_raw: dict[str, Any] | None = None,
    math_svg_map: dict | None = None,
    slide_template: SlideTemplate | None = None,
    visual_svg_map: dict | None = None,
    enable_split: bool = False,
) -> tuple[str, list[dict[str, Any]]]:
    lesson = CourseLesson(
        lesson_code="M1.L1",
        title="Lezione di prova",
        content_raw=content_raw or {},
        slides_raw={"slides": slides},
    )
    with structlog.testing.capture_logs() as logs:
        html = slides_pdf.render_slides_html(
            course=_course(),
            lesson=lesson,
            organization=None,
            slide_template=slide_template,
            math_svg_map=math_svg_map,
            visual_svg_map=visual_svg_map,
            enable_split=enable_split,
        )
    return html, logs


def _speech_lesson(
    *,
    title: str = "Lezione di prova",
    text: str = "Testo del segmento.",
    notes: str = "",
    slide_title: str = "Titolo della slide",
    segment_ids: list[str] | None = None,
) -> CourseLesson:
    return CourseLesson(
        lesson_code="M1.L1",
        title=title,
        slides_raw={"slides": [_slide("s1", title=slide_title)]},
        speech_raw={
            "speech_segments": [
                {
                    "segment_id": "g1",
                    "slide_id": "s1",
                    "text": text,
                    "estimated_duration_seconds": 30,
                    "delivery_notes": notes,
                }
            ],
            "slide_to_segments_map": [
                {
                    "slide_id": "s1",
                    "segment_ids": segment_ids or ["g1"],
                    "slide_total_duration_seconds": 30,
                }
            ],
            "estimated_total_duration_seconds": 30,
            "estimated_total_word_count": 3,
        },
    )


def _render_speech(
    lesson: CourseLesson,
    *,
    course: Course | None = None,
    math_svg_map: dict | None = None,
    pdf_template: PdfTemplate | None = None,
) -> str:
    return speech_pdf.render_speech_html(
        course=course or _course(),
        lesson=lesson,
        organization=None,
        pdf_template=pdf_template,
        math_svg_map=math_svg_map,
    )


# ---------------------------------------------------------------------------
# 1. Autoescape dei tre env
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env_owner", "template"),
    [
        (pdf, "lesson_pdf.html.j2"),
        (slides_pdf, "lesson_slides_pdf.html.j2"),
        (speech_pdf, "lesson_speech_pdf.html.j2"),
    ],
)
def test_the_three_pdf_envs_autoescape_their_templates(env_owner: Any, template: str) -> None:
    env = env_owner._jinja_env
    assert env.autoescape(template) is True
    assert env.autoescape("partials/figure.html.j2") is True
    # Il filtro dell'escape CSS è registrato su ogni env.
    assert env.filters["css_string"] is pdf.css_string


def test_slide_author_fields_are_escaped() -> None:
    html, _logs = _render_slides(
        [
            _slide(
                "s1",
                title="A & B <x>",
                body="Prosa <script>alert(1)</script>",
                bullets=["**non grassetto** <b>b</b>"],
            )
        ]
    )
    body = html.split("</style>", 1)[1]
    assert '<h1 class="slide-title">A &amp; B &lt;x&gt;</h1>' in body
    assert "<script>" not in body and "Prosa &lt;script&gt;alert(1)&lt;/script&gt;" in body
    # Nessun markdown ricco: l'enfasi resta letterale, come nel preset zero.
    assert "<li>**non grassetto** &lt;b&gt;b&lt;/b&gt;</li>" in body
    assert "<b>" not in body and "<strong>" not in body


def test_speech_author_fields_are_escaped() -> None:
    lesson = _speech_lesson(
        title="Lezione <i>1</i>",
        text="Testo <script>x</script> & altro",
        notes="Nota <b>forte</b>",
        slide_title="Slide <u>uno</u>",
    )
    html = _render_speech(lesson, course=_course("Corso <em>A</em>"))
    body = html.split("</style>", 1)[1]
    for raw in ("<script>", "<i>1</i>", "<b>forte</b>", "<u>uno</u>", "<em>A</em>"):
        assert raw not in body, raw
    assert "Testo &lt;script&gt;x&lt;/script&gt; &amp; altro" in body
    assert "Nota &lt;b&gt;forte&lt;/b&gt;" in body
    assert "Slide &lt;u&gt;uno&lt;/u&gt;" in body
    assert "<title>Corso &lt;em&gt;A&lt;/em&gt;" in html


# ---------------------------------------------------------------------------
# 2. Math nella prosa delle slide e del discorso
# ---------------------------------------------------------------------------


def test_slide_prose_math_is_collected_and_rendered_as_svg() -> None:
    slides = [
        _slide(
            "s1",
            title="Legge $E = mc^2$",
            body="Con \\(x_0\\) iniziale.",
            bullets=["Il guadagno $G = 2^{10}$", "Costo $50 e $70"],
        )
    ]
    content = slides_pdf._math_content_for_slides({}, {"slides": slides})
    collected = set(pdf._collect_math_from_content(content))
    assert collected == {("E = mc^2", "inline"), ("x_0", "inline"), ("G = 2^{10}", "inline")}
    rec = _AnyMathMap()
    html, _logs = _render_slides(slides, math_svg_map=rec)
    assert set(rec.seen) == collected
    body = html.split("</style>", 1)[1]
    assert f'<li>Il guadagno <span class="math-inline">{MJ_SVG}</span></li>' in body
    assert f'<h1 class="slide-title">Legge <span class="math-inline">{MJ_SVG}</span></h1>' in body
    assert "<li>Costo $50 e $70</li>" in body  # importi: prosa
    assert "$" not in body.replace("$50 e $70", "")


def test_speech_prose_math_is_collected_and_reaches_the_pdf_as_svg() -> None:
    lesson = _speech_lesson(
        text="La relazione $a^2 + b^2 = c^2$ vale sempre.",
        notes="Scrivere $\\frac{a}{b}$ alla lavagna.",
        slide_title="Legge \\(E = mc^2\\)",
    )
    content = speech_pdf._math_content_for_speech(lesson)
    collected = set(pdf._collect_math_from_content(content))
    assert collected == {
        ("a^2 + b^2 = c^2", "inline"),
        ("\\frac{a}{b}", "inline"),
        ("E = mc^2", "inline"),
    }
    rec = _AnyMathMap()
    html = _render_speech(lesson, math_svg_map=rec)
    assert set(rec.seen) == collected
    assert html.count('<span class="math-inline">') == 3
    _data, text, warnings = _pdf_text_and_warnings(_weasyprint(), html)
    assert "Scrivere" in text and "alla lavagna" in text and "vale sempre" in text, text
    for residue in ("\\frac", "$", "mc^2", "\\(", "a^2"):
        assert residue not in text, (residue, text)
    assert warnings == []


async def test_speech_pdf_prerenders_math_and_logs_fallbacks(monkeypatch: Any) -> None:
    """`materialize_lesson_speech_pdf` pre-rende le formule con MathJax
    (una sola batch), le passa al render e chiude con
    `_log_math_fallbacks`: prima il discorso non aveva MathJax."""
    import inspect

    batches: list[list[tuple[str, str]]] = []

    async def fake_batch(items: list[tuple[str, str]]) -> list[str | None]:
        batches.append(list(items))
        return [MJ_SVG for _ in items]

    monkeypatch.setattr(pdf, "_prerender_math_to_svg_batch", fake_batch)
    lesson = _speech_lesson(notes="Nota $y^2$", text="Testo $x$.")
    svg_map = await speech_pdf._prerender_math_for_speech(lesson)
    assert batches == [[("x", "inline"), ("y^2", "inline")]]
    assert isinstance(svg_map, pdf.MathSvgMap) and svg_map.requested == 2
    src = inspect.getsource(speech_pdf.materialize_lesson_speech_pdf)
    assert src.index("_prerender_math_for_speech(") < src.index("render_speech_html")
    assert src.index("render_speech_html") < src.index("_log_math_fallbacks(")


# ---------------------------------------------------------------------------
# 3. tpl.* in contesto CSS e URL
# ---------------------------------------------------------------------------


def _slide_template() -> SlideTemplate:
    return SlideTemplate(
        name="T",
        font_family='Open "Sans" \\ Pro',
        text_color="#1F1F1F",
        primary_color="#1976D2",
        secondary_color="#9C27B0",
        slide_size="16:9",
        margin_mm=20,
        background_opacity_pct=40,
        background_image_path=_URL_BG,
        logo_left_path=_URL_LOGO_L,
        logo_right_path=_URL_LOGO_R,
    )


def _pdf_template() -> PdfTemplate:
    return PdfTemplate(
        name="T",
        font_family='Open "Sans" \\ Pro',
        text_color="#1F1F1F",
        primary_color="#1976D2",
        secondary_color="#9C27B0",
        page_size="A4",
        header_height_mm=20,
        footer_height_mm=15,
        margin_mm=20,
        background_opacity_pct=40,
        background_image_path=_URL_BG,
        logo_left_path=_URL_LOGO_L,
        logo_right_path=_URL_LOGO_R,
    )


def _three_surfaces() -> dict[str, str]:
    content_raw = {"introduction": "Intro.", "sections": [], "summary": ""}
    lesson = CourseLesson(lesson_code="M1.L1", title="Lezione", content_raw=content_raw)
    dispensa = pdf.render_lesson_html(
        course=_course(), lesson=lesson, organization=None, pdf_template=_pdf_template()
    )
    slides, _logs = _render_slides([_slide("s1")], slide_template=_slide_template())
    speech = _render_speech(_speech_lesson(), pdf_template=_pdf_template())
    return {"dispensa": dispensa, "slide": slides, "discorso": speech}


def _recording_fetcher(seen: list[str]) -> Callable[[str], Any]:
    from weasyprint.urls import URLFetcherResponse

    def fetch(url: str, *args: Any, **kwargs: Any) -> Any:
        seen.append(url)
        return URLFetcherResponse(url, body=_PNG_1PX, headers={"Content-Type": "image/png"})

    return fetch


def _walk(box: Any) -> Any:
    yield box
    children = getattr(box, "all_children", None)
    for child in children() if children else getattr(box, "children", []):
        yield from _walk(child)


@pytest.mark.parametrize("surface", ["dispensa", "slide", "discorso"])
def test_template_css_and_urls_reach_the_engine_intact(surface: str) -> None:
    """Nell'HTML: `url("…&b=2")` letterale, font con l'escape CSS, colori
    concatenati intatti, loghi in attributo con l'escape HTML (`&amp;`).
    Nel motore: WeasyPrint chiede ESATTAMENTE i tre URL originali e il
    `body` riceve la famiglia con virgolette e backslash."""
    weasyprint = _weasyprint()
    html = _three_surfaces()[surface]
    style = html.split("</style>", 1)[0]
    assert f'url("{_URL_BG}")' in style
    assert "&amp;" not in style and "&#34;" not in style and "&#39;" not in style
    assert 'font-family: "Open \\"Sans\\" \\\\ Pro"' in style
    assert "#9C27B0" in style and "#1976D2" in style
    if surface == "slide":
        assert "opacity: 0.4;" in style
    body = html.split("</style>", 1)[1]
    assert f'src="{_URL_LOGO_L.replace("&", "&amp;")}"' in body
    assert f'src="{_URL_LOGO_R.replace("&", "&amp;")}"' in body
    seen: list[str] = []
    document = weasyprint.HTML(string=html, url_fetcher=_recording_fetcher(seen)).render()
    assert set(seen) == {_URL_BG, _URL_LOGO_L, _URL_LOGO_R}, seen
    families = {
        box.style["font_family"]
        for box in _walk(document.pages[0]._page_box)
        if getattr(box, "element_tag", None) == "body"
    }
    assert families and all(f[0] == 'Open "Sans" \\ Pro' for f in families), families


def test_css_string_escapes_quotes_backslashes_newlines_and_style_closers() -> None:
    import tinycss2

    raw = 'Corso "Alfa" \\ Beta\r\nL\'uno </style><script>'
    out = pdf.css_string(raw)
    assert isinstance(out, Markup)
    assert str(out) == ('Corso \\"Alfa\\" \\\\ Beta  L\\\'uno \\3c /style\\3e \\3c script\\3e ')
    assert "<" not in out and "\n" not in out and "\r" not in out
    tokens = tinycss2.parse_component_value_list(f'"{out}"')
    (token,) = [t for t in tokens if t.type != "whitespace"]
    assert token.type == "string"
    assert token.value == 'Corso "Alfa" \\ Beta  L\'uno </style><script>'
    assert pdf.css_string(None) == Markup("") and pdf.css_string(12) == Markup("12")


def test_speech_footer_survives_quotes_and_backslashes_in_titles() -> None:
    """Il piè di pagina del discorso è una stringa CSS (`@bottom-center`):
    prima un `"` nel titolo lo cancellava (regola CSS invalida), con
    l'autoescape resterebbe `&#34;` letterale. Nel testo del PDF c'è il
    titolo esatto, su ogni pagina."""
    weasyprint = _weasyprint()
    course = _course('Corso "Alfa" \\ Beta')
    html = _render_speech(_speech_lesson(title="Lezione <1> & c."), course=course)
    assert 'content: "Corso \\"Alfa\\" \\\\ Beta · Lezione \\3c 1\\3e  & c. · "' in html
    data, _text, warnings = _pdf_text_and_warnings(weasyprint, html)
    pypdf = pytest.importorskip("pypdf")
    pages = pypdf.PdfReader(io.BytesIO(data)).pages
    assert len(pages) == 2
    for number, page in enumerate(pages, start=1):
        footer = 'Corso "Alfa" \\ Beta · Lezione <1> & c. · ' + f"{number}/2"
        assert footer in (page.extract_text() or ""), (number, page.extract_text())
    assert warnings == [], warnings


# ---------------------------------------------------------------------------
# 4. Duplicati di slide e box della figura
# ---------------------------------------------------------------------------


def test_duplicate_references_render_one_block_and_log() -> None:
    content_raw = {
        "visual_assets": [_visual("fig_1")],
        "equations": [{"equation_id": "eq_1", "latex": "x = 1", "label": "Uno"}],
    }
    html, logs = _render_slides(
        [
            _slide("s1", references_assets=["fig_1", "FIG_1", "Fig_1", "missing"]),
            _slide("s2", references_assets=["eq_1", "eq_1"]),
        ],
        content_raw=content_raw,
        visual_svg_map={"fig_1": SVG_A},
    )
    first, second = html.split('<div class="slide">')[1:]
    assert first.count("<figure") == 1 and 'data-asset-id="fig_1"' in first
    assert second.count('<figure class="equation"') == 1
    dup = [e for e in logs if e["event"] == "slide_duplicate_asset_ref"]
    assert [(e["lesson_code"], e["slide_id"], e["asset_id"]) for e in dup] == [
        ("M1.L1", "s1", "FIG_1"),
        ("M1.L1", "s1", "Fig_1"),
        ("M1.L1", "s2", "eq_1"),
    ]
    assert all(e["log_level"] == "warning" for e in dup)


def test_figure_box_style_survives_the_autoescape() -> None:
    html, _logs = _render_slides(
        [_slide("s1", references_assets=["fig_1"])],
        content_raw={"visual_assets": [_visual("fig_1")]},
        visual_svg_map={"fig_1": SVG_A},
    )
    assert re.search(
        r'<figure class="visual[^"]*" data-asset-id="fig_1"[^>]* aria-label="[^"]*" '
        r'style="--figure-w: 255\.0mm; --figure-h: [\d.]+mm">',
        html,
    ), html


# ---------------------------------------------------------------------------
# 5. Regola max-1-visivo nel CRUD (solo slide toccate)
# ---------------------------------------------------------------------------

_CONTENT_RAW = {
    "sections": [],
    "visual_assets": [_visual("fig_1"), _visual("fig_2"), _visual("fig_3")],
    "tables": [{"table_id": "tab_1", "caption": "T", "markdown": "| a |\n|---|\n| 1 |"}],
    "equations": [{"equation_id": "eq_1", "latex": "x"}],
    "examples": [{"example_id": "ex_1", "title": "E", "content": "c"}],
}
_HISTORICAL = {
    "slides": [
        _slide("s1", slide_number=1, references_assets=["fig_1", "fig_2"]),
        _slide("s2", slide_number=2, references_assets=["fig_3"]),
    ]
}


def _payload(*slides: dict[str, Any]) -> LessonSlidesUpdateInput:
    return LessonSlidesUpdateInput(slides=[LessonSlideItem(**s) for s in slides])


def test_crud_rejects_a_second_visual_on_an_edited_slide() -> None:
    s1, s2 = _HISTORICAL["slides"]
    for refs in (["fig_3", "fig_2"], ["fig_3", "tab_1"], ["fig_3", "new_1"]):
        payload = LessonSlidesUpdateInput(
            slides=[LessonSlideItem(**s1), LessonSlideItem(**{**s2, "references_assets": refs})],
            new_assets=[{"asset_id": "new_1", "format": "mermaid", "content": "flowchart LR"}],
        )
        with pytest.raises(ConflictError) as exc:
            slides_crud._validate_consistency(
                payload=payload, current_raw=_HISTORICAL, content_raw=_CONTENT_RAW
            )
        assert exc.value.code == "lesson_slides_multiple_visual_assets", refs
        assert exc.value.status_code == 409
        assert "s2" in exc.value.message
    # Una slide nuova con due figure è una slide toccata.
    new = _slide("s3", slide_number=3, references_assets=["fig_1", "FIG_3"])
    with pytest.raises(ConflictError):
        slides_crud._validate_consistency(
            payload=_payload(s1, s2, new), current_raw=_HISTORICAL, content_raw=_CONTENT_RAW
        )
    # La slide storica con due figure: sostituirne una (stesso numero di
    # visivi) o aggiungerne una terza introduce un visivo non salvato.
    for refs in (["fig_1", "fig_3"], ["fig_1", "fig_2", "tab_1"]):
        with pytest.raises(ConflictError) as exc:
            slides_crud._validate_consistency(
                payload=_payload({**s1, "references_assets": refs}, s2),
                current_raw=_HISTORICAL,
                content_raw=_CONTENT_RAW,
            )
        assert exc.value.code == "lesson_slides_multiple_visual_assets", refs
        assert "s1" in exc.value.message


def test_crud_keeps_historical_slides_with_two_visuals_editable() -> None:
    s1, s2 = _HISTORICAL["slides"]
    ok_payloads = [
        # Nessuna slide toccata nei riferimenti: il titolo di s1 cambia.
        _payload({**s1, "title": "Nuovo titolo"}, s2),
        # s1 riceve un'equazione e un esempio (fuori dal limite).
        _payload({**s1, "references_assets": ["fig_1", "fig_2", "eq_1", "ex_1"]}, s2),
        # s1 riordina le sue due figure storiche.
        _payload({**s1, "references_assets": ["fig_2", "FIG_1"]}, s2),
        # s2 ripete la stessa figura con un'altra grafia: una sola.
        _payload(s1, {**s2, "references_assets": ["fig_3", "FIG_3"]}),
    ]
    for payload in ok_payloads:
        slides_crud._validate_consistency(
            payload=payload, current_raw=_HISTORICAL, content_raw=_CONTENT_RAW
        )
    # Solo i nuovi asset: le slide non cambiano.
    slides_crud._validate_consistency(
        payload=LessonSlidesUpdateInput(new_equations=[]),
        current_raw=_HISTORICAL,
        content_raw=_CONTENT_RAW,
    )


def test_crud_lets_historical_slides_shrink_toward_the_rule() -> None:
    """Una slide storica a tre visivi si riduce un passo alla volta: ogni
    sottoinsieme di quanto salvato passa (3 -> 2 -> 1), mentre una
    sostituzione a parità di numero resta un visivo nuovo (409)."""
    three = {
        "slides": [_slide("s1", slide_number=1, references_assets=["fig_1", "fig_2", "tab_1"])]
    }
    (s1,) = three["slides"]
    for refs in (["fig_1", "fig_2"], ["FIG_2", " tab_1 "], ["fig_1"], []):
        slides_crud._validate_consistency(
            payload=_payload({**s1, "references_assets": refs}),
            current_raw=three,
            content_raw=_CONTENT_RAW,
        )
    # Il passo successivo parte dalla versione ridotta e salvata.
    two = {"slides": [{**s1, "references_assets": ["fig_1", "fig_2"]}]}
    slides_crud._validate_consistency(
        payload=_payload({**s1, "references_assets": ["fig_2"]}),
        current_raw=two,
        content_raw=_CONTENT_RAW,
    )
    for refs in (["fig_1", "fig_3"], ["fig_1", "tab_1", "fig_3"]):
        with pytest.raises(ConflictError) as exc:
            slides_crud._validate_consistency(
                payload=_payload({**s1, "references_assets": refs}),
                current_raw=two if len(refs) == 2 else three,
                content_raw=_CONTENT_RAW,
            )
        assert exc.value.code == "lesson_slides_multiple_visual_assets", refs


def test_crud_counts_a_retyped_asset_as_a_new_visual() -> None:
    """Un'equazione salvata che il PATCH ridichiara come visivo con lo stesso
    id aggiunge un visivo alla slide che la cita: 409 anche con le slide
    invariate e anche senza `slides` nel payload (finding V3-1 di WP4)."""
    saved = {
        "slides": [_slide("s1", slide_number=1, references_assets=["fig_3", "x_1"])],
        "new_equations": [{"equation_id": "x_1", "latex": "x"}],
    }
    (s1,) = saved["slides"]
    retyped = [{"asset_id": "x_1", "format": "mermaid", "content": "flowchart LR"}]
    for payload in (
        LessonSlidesUpdateInput(
            slides=[LessonSlideItem(**s1)], new_equations=[], new_assets=retyped
        ),
        LessonSlidesUpdateInput(new_equations=[], new_assets=retyped),
    ):
        with pytest.raises(ConflictError) as exc:
            slides_crud._validate_consistency(
                payload=payload, current_raw=saved, content_raw=_CONTENT_RAW
            )
        assert exc.value.code == "lesson_slides_multiple_visual_assets"
        assert "s1" in exc.value.message
    # Il percorso inverso toglie un visivo: passa.
    saved_visual = {
        "slides": [_slide("s1", slide_number=1, references_assets=["fig_3", "x_1"])],
        "new_assets": retyped,
    }
    slides_crud._validate_consistency(
        payload=LessonSlidesUpdateInput(
            new_assets=[], new_equations=[{"equation_id": "x_1", "latex": "x"}]
        ),
        current_raw=saved_visual,
        content_raw=_CONTENT_RAW,
    )


async def test_crud_patch_statuses_on_a_real_lesson(seeded_db: Any) -> None:
    """Percorso completo `update_lesson_slides` su una lezione salvata:
    il PATCH che aggiunge una seconda figura è 409, quello che non tocca
    la slide storica con due figure passa e salva."""
    from app.services import course_lesson_content_service as content_svc
    from tests.course_builders import build_course, find_lesson

    course_id, _org, user = await build_course(
        seeded_db, modules=1, lessons_per_module=1, slides_status="ready"
    )
    course = await content_svc.load_course_full(seeded_db, course_id=course_id)
    assert course is not None
    lesson = find_lesson(course, "M1.L1")
    lesson.content_raw = _CONTENT_RAW
    lesson.slides_raw = _HISTORICAL
    await seeded_db.commit()
    s1, s2 = _HISTORICAL["slides"]
    with pytest.raises(ConflictError) as exc:
        await slides_crud.update_lesson_slides(
            seeded_db,
            course=course,
            lesson=lesson,
            payload=_payload(s1, {**s2, "references_assets": ["fig_3", "fig_1"]}),
            actor_id=user.id,
        )
    assert exc.value.status_code == 409
    await slides_crud.update_lesson_slides(
        seeded_db,
        course=course,
        lesson=lesson,
        payload=_payload(s1, {**s2, "title": "Titolo corretto"}),
        actor_id=user.id,
    )
    assert lesson.slides_raw["slides"][1]["title"] == "Titolo corretto"
    assert lesson.slides_raw["slides"][0]["references_assets"] == ["fig_1", "fig_2"]


# ---------------------------------------------------------------------------
# 6. Discorso: note sanificate, C12 come limite dichiarato
# ---------------------------------------------------------------------------


def _speech_course(lesson: CourseLesson) -> Course:
    module = CourseModule(module_code="M1", title="M", position=1)
    module.lessons = [lesson]
    course = Course(title="Corso", language_code="it", cfu=6, lesson_duration_minutes=1)
    course.modules = [module]
    return course


def _speech_output(notes: str) -> LessonSpeechOutput:
    return LessonSpeechOutput(
        lesson_id="M1.L1",
        language="it",
        target_duration_seconds=60,
        estimated_total_duration_seconds=60,
        estimated_total_word_count=150,
        speech_segments=[
            {
                "segment_id": "g1",
                "slide_id": "s1",
                "text": "Testo del parlato senza formule.",
                "estimated_duration_seconds": 60,
                "delivery_notes": notes,
            }
        ],
        slide_to_segments_map=[
            {"slide_id": "s1", "segment_ids": ["g1"], "slide_total_duration_seconds": 60}
        ],
    )


async def test_delivery_notes_follow_the_tts_policy_of_the_text() -> None:
    lesson = CourseLesson(lesson_code="M1.L1", title="L", slides_raw={"slides": [_slide("s1")]})
    course = _speech_course(lesson)
    output = _speech_output("Pausa su $\\frac{a}{b}$ e *enfasi* `codice` #1")
    with structlog.testing.capture_logs() as logs:
        await speech_svc.materialize_lesson_speech(
            None,  # type: ignore[arg-type]  # nessun accesso al DB
            course=course,
            lesson=lesson,
            output=output,
            raw=output.model_dump(),
            usage={},
        )
    notes = lesson.speech_raw["speech_segments"][0]["delivery_notes"]
    assert notes == "Pausa su {a}{b} e enfasi codice 1"
    assert speech_svc.validate_tts_safety(notes) == []
    assert [e["event"] for e in logs].count("lesson_speech_text_sanitized") == 1
    # Un'abbreviazione nelle note fa fallire come nel testo.
    lesson2 = CourseLesson(lesson_code="M1.L1", title="L", slides_raw={"slides": [_slide("s1")]})
    output2 = _speech_output("Rallentare, es. sulle definizioni.")
    with pytest.raises(ConflictError) as exc:
        await speech_svc.materialize_lesson_speech(
            None,  # type: ignore[arg-type]
            course=_speech_course(lesson2),
            lesson=lesson2,
            output=output2,
            raw=output2.model_dump(),
            usage={},
        )
    assert exc.value.code == "lesson_speech_tts_unsafe"
    assert "delivery_notes" in exc.value.message


def test_speech_prompt_and_schema_give_delivery_notes_the_text_rules() -> None:
    """Un'abbreviazione in `delivery_notes` fa fallire il discorso e, nel
    worker, costa una rigenerazione completa (errore recuperabile): il
    modello deve trovare la regola nel prompt e nello schema delle note, non
    solo in quello del testo."""
    segment = speech_ai.LESSON_SPEECH_JSON_SCHEMA["schema"]["properties"]["speech_segments"]
    notes = segment["items"]["properties"]["delivery_notes"]["description"]
    for rule in ("abbreviazioni", "caratteri speciali", "markdown", "formule LaTeX"):
        assert rule in notes, rule
    prompt = speech_ai._system_prompt("it", minuti_per_lezione=10)
    rules = prompt.split("REGOLE — VINCOLI DI VALIDAZIONE", 1)[1]
    assert "`delivery_notes` rispetta le stesse REGOLE — TTS-FRIENDLY di `text`" in rules
    for abbreviation in ('"es."', '"etc."', '"ca."'):
        assert abbreviation in rules, abbreviation
    # Le abbreviazioni citate sono proprio quelle che il validatore rifiuta.
    assert speech_svc.validate_tts_safety("es. etc. ca.") == [
        "abbreviazione proibita `ca.`",
        "abbreviazione proibita `es.`",
        "abbreviazione proibita `etc.`",
    ]


def test_repeated_segment_ids_in_the_map_are_a_declared_limit() -> None:
    """C12, limite dichiarato (docs/courses/11-lesson-speech.md): un
    `segment_id` ripetuto nella stessa voce della mappa passa la
    validazione se il totale dichiarato è già raddoppiato, e il PDF lo
    rende due volte con la timeline che avanza due volte. Non si corregge
    in `format_timeline`: il totale per slide stampato nell'intestazione
    non tornerebbe più con i segmenti mostrati."""
    seg = {"segment_id": "g1", "text": "Uno", "estimated_duration_seconds": 30}
    timeline = speech_pdf.format_timeline(
        [{"slide_id": "s1", "segment_ids": ["g1", "g1"], "slide_total_duration_seconds": 60}],
        {"g1": seg},
    )
    (entry,) = timeline
    assert [s["segment_id"] for s in entry["segments"]] == ["g1", "g1"]
    assert [(s["start_mmss"], s["end_mmss"]) for s in entry["segments"]] == [
        ("00:00", "00:30"),
        ("00:30", "01:00"),
    ]
    assert entry["slide_total_label"] == "01:00"
    html = _render_speech(_speech_lesson(segment_ids=["g1", "g1"]))
    assert html.count('<p class="segment-text">Testo del segmento.</p>') == 2


# ---------------------------------------------------------------------------
# 7. Frontend: editor e viste di slide e discorso come il PDF
# ---------------------------------------------------------------------------

_FE = Path(__file__).resolve().parents[2] / "frontend" / "src"


def test_slide_editor_and_view_compare_asset_refs_like_the_pdf() -> None:
    """Editor e vista confrontano i riferimenti con `assetRefKey`
    (`trim().toLowerCase()`, come il CRUD) e mostrano/salvano ogni asset una
    volta (`uniqueAssetRefs`); la vista rende titolo, prosa e bullet con
    `InlineMath`, come `render_markdown_inline` nel PDF."""
    lib = (_FE / "lib" / "slides.ts").read_text(encoding="utf-8")
    assert 'return (assetId || "").trim().toLowerCase();' in lib
    # `resolveAsset` usa la stessa chiave del PDF (`_asset_ref_key`).
    resolve = lib.split("export function resolveAsset(", 1)[1].split("\n}\n", 1)[0]
    assert "const target = assetRefKey(assetId);" in resolve
    assert ".toLowerCase()" not in resolve
    assert resolve.count("assetRefKey(") == 9
    assert "export function uniqueAssetRefs(refs: readonly string[]): string[]" in lib
    components = _FE / "pages" / "org" / "courses" / "components"
    editor = (components / "LessonSlidesEditDialog.tsx").read_text(encoding="utf-8")
    toggle = editor.split("const toggleAssetRef = ", 1)[1].split("};", 1)[0]
    assert ".includes(" not in toggle
    assert "current.some((a) => assetRefKey(a) === key)" in toggle
    assert "references_assets: uniqueAssetRefs(next)" in toggle
    assert "slide.references_assets.includes(opt.id)" not in editor
    assert "(a) => assetRefKey(a) === assetRefKey(opt.id)" in editor
    view = (components / "LessonSlidesView.tsx").read_text(encoding="utf-8")
    assert "uniqueAssetRefs(slide.references_assets).map((aid) => (" in view
    assert "key={assetRefKey(aid)}" in view
    # WP8: il campo passa prima dal rimando testuale degli asset e poi da
    # `InlineMath`, come `cite` + `render_markdown_inline` nel PDF.
    for field in ("slide.title", "slide.body", "b"):
        assert f"<InlineMath text={{refs.cite({field})}} />" in view, field


def test_speech_view_renders_math_like_the_speech_pdf() -> None:
    """La vista del discorso rende titolo di slide, testo e note con
    `InlineMath`, come `render_markdown_inline` nel PDF del discorso e come
    la vista delle slide; nessuno dei tre resta testo semplice."""
    components = _FE / "pages" / "org" / "courses" / "components"
    view = (components / "LessonSpeechView.tsx").read_text(encoding="utf-8")
    assert 'import { InlineMath } from "@/components/shared/InlineMath";' in view
    for field in ("slideMeta.title", "segment.text", "segment.delivery_notes"):
        # Ogni interpolazione del campo è quella di `InlineMath`, col
        # rimando testuale degli asset applicato prima (WP8).
        assert view.count(f"<InlineMath text={{refs.cite({field})}} />") == 1, field
        assert view.count(f"{{refs.cite({field})}}") == 1, field


# ---------------------------------------------------------------------------
# 8. Contenuti d'autore nei motori: frame video senza JavaScript, WebSocket
#    chiusi dalla guardia, fetcher di WeasyPrint con allowlist, una sola
#    chiave per i riferimenti
# ---------------------------------------------------------------------------

# Cancella tutte le slide: se parte, il frame video non ha più nulla da
# fotografare. Niente `<`, `>`, `&` né `"`: nel markdown il corpo di uno
# `<script>` è testo escapato, e nel browser resterebbe codice non valido.
_KILL_SLIDES = "document.querySelectorAll('.slide').forEach(function (el) { el.remove(); })"
_INJECTED_IMAGE_URL = f'https://x.invalid/a.png" onerror="{_KILL_SLIDES}'
_AUTHOR_JS_CONTENT = {
    "visual_assets": [
        {"asset_id": "img_1", "format": "image", "content": _INJECTED_IMAGE_URL, "caption": "c"}
    ],
    # HTML d'autore nel markdown (ammesso come nella dispensa).
    "examples": [
        {"example_id": "ex_1", "title": "E",
         "content": f'Testo <img src="data:," onerror="{_KILL_SLIDES}">'}
    ],
    "equations": [
        {"equation_id": "eq_1", "latex": "x",
         "explanation": "Spiega <script>document.addEventListener('DOMContentLoaded', "
                        f"function () {{ {_KILL_SLIDES}; }})</script>"}
    ],
    "tables": [
        {"table_id": "tab_1", "caption": "T",
         "markdown": f'| a |\n|---|\n| <img src="data:," onerror="{_KILL_SLIDES}"> |'}
    ],
}  # fmt: skip


def _author_js_html(refs: tuple[str, ...] = ("img_1", "ex_1", "eq_1", "tab_1")) -> str:
    slides = [
        _slide(f"s{i}", slide_number=i, references_assets=[ref])
        for i, ref in enumerate(refs, start=1)
    ]
    html, _logs = _render_slides(slides, content_raw=_AUTHOR_JS_CONTENT)
    return html


class _ImgAttrs(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.images: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "img":
            self.images.append(dict(attrs))


def test_uploaded_image_url_stays_inside_src() -> None:
    """Un asset `image` con un URL assoluto torna dal resolver così com'è: il
    `"` d'autore resta dentro `src` e non apre un `onerror`."""
    parser = _ImgAttrs()
    parser.feed(_author_js_html(("img_1",)))
    uploaded = [a for a in parser.images if a.get("class") == "uploaded-image"]
    assert uploaded == [{"class": "uploaded-image", "src": _INJECTED_IMAGE_URL, "alt": ""}]


async def _launch_chromium(pw: Any) -> Any:
    try:
        return await pw.chromium.launch(args=["--no-sandbox"])
    except Exception as exc:  # launch di Chromium: verifica locale, non gate CI
        pytest.skip(f"Chromium non disponibile: {exc!r}"[:300])


async def test_video_frames_do_not_run_author_javascript(tmp_path: Path) -> None:
    """Il markdown di esempi, equazioni e tabelle porta HTML d'autore fino ad
    `asset_html|safe`: con JavaScript acceso i suoi `<script>` e `onerror`
    cancellano le slide (controllo), nei frame video no."""
    from playwright.async_api import async_playwright

    from app.services import lesson_slides_video_render_service as video
    from app.services.mermaid_prerender import block_external_requests

    html = _author_js_html()
    async with async_playwright() as pw:
        browser = await _launch_chromium(pw)
        try:
            for refs, left in ((("ex_1", "eq_1", "tab_1"), 0), (("img_1",), 1)):
                page = await browser.new_page()
                await block_external_requests(page)
                await page.set_content(_author_js_html(refs), wait_until="networkidle")
                assert await page.evaluate("document.querySelectorAll('.slide').length") == left
                await page.close()
        finally:
            await browser.close()
    frames = await video._screenshot_slides_async(html, tmp_path / "frames")
    assert [f.name for f in frames] == [f"slide_00{i}.png" for i in range(1, 5)]


async def test_network_guard_closes_websockets_without_reaching_the_server() -> None:
    """`page.route` non vede gli upgrade WebSocket: la guardia li instrada a
    parte e li chiude. Controllo: senza guardia l'upgrade arriva al server."""
    from playwright.async_api import async_playwright

    from app.services.mermaid_prerender import block_external_requests

    hits: list[bytes] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        hits.append((await reader.readline()).strip())
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    url = f"ws://127.0.0.1:{port}/ws"
    html = f"<html><body><script>window.__ws = new WebSocket('{url}');</script></body></html>"
    try:
        async with async_playwright() as pw:
            browser = await _launch_chromium(pw)
            try:
                page = await browser.new_page()
                await page.set_content(html)
                for _ in range(100):
                    if hits:
                        break
                    await asyncio.sleep(0.05)
                assert hits == [b"GET /ws HTTP/1.1"]
                hits.clear()

                page = await browser.new_page()
                with structlog.testing.capture_logs() as logs:
                    await block_external_requests(page)
                    await page.set_content(html)
                    await page.wait_for_function("window.__ws.readyState === 3", timeout=5000)
                    await asyncio.sleep(0.5)
            finally:
                await browser.close()
    finally:
        server.close()
        await server.wait_closed()
    assert hits == []
    blocked = [e["url"] for e in logs if e["event"] == "prerender_websocket_blocked"]
    assert blocked == [url]


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("data:image/png;base64,AAAA", True),
        ("DATA:image/svg+xml;utf8,<svg/>", True),
        ("https://media.example/m/logo.png", True),
        ("HTTPS://MEDIA.EXAMPLE/m/x.png", True),
        ("https://media.example/other/x.png", False),
        ("https://media.example.evil/m/x.png", False),
        ("http://127.0.0.1:8000/uploads/x.png", False),
        ("http://169.254.169.254/latest/meta-data/", False),
        ("file:///etc/hosts", False),
        ("https://cdn.jsdelivr.net/npm/x.js", False),
        ("about:blank", False),
    ],
)
def test_pdf_fetcher_allows_data_urls_and_the_media_host(url: str, allowed: bool) -> None:
    assert pdf.allows_pdf_resource_url(url, allowed_prefixes=("https://media.example/m/",)) is (
        allowed
    )
    assert pdf.allows_pdf_resource_url(url) is url.lower().startswith("data:")
    assert not pdf.allows_pdf_resource_url("http://x/", allowed_prefixes=("", "  "))


class _RecordingHandler(BaseHTTPRequestHandler):
    hits: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        type(self).hits.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(_PNG_1PX)))
        self.end_headers()
        self.wfile.write(_PNG_1PX)

    def log_message(self, format: str, *args: Any) -> None:
        return


def _image_count(data: bytes) -> int:
    """Immagini distinte del PDF (WeasyPrint le nomina dall'URL e condivide
    le risorse fra le pagine)."""
    pypdf = pytest.importorskip("pypdf")
    names: set[str] = set()
    for page in pypdf.PdfReader(io.BytesIO(data)).pages:
        xobjects = page["/Resources"].get("/XObject") or {}
        names.update(n for n, ref in xobjects.items() if ref.get_object()["/Subtype"] == "/Image")
    return len(names)


def test_weasyprint_fetches_only_data_urls_and_the_media_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Il fetcher dei tre PDF: un `<img>` d'autore nel markdown della dispensa
    verso un host qualunque o `file:` non parte (log
    `pdf_resource_blocked`); le data URL e l'host dei media, con lo storage
    remoto, sì."""
    _weasyprint()
    from app.core.config import get_settings

    server = ThreadingHTTPServer(("127.0.0.1", 0), _RecordingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    _RecordingHandler.hits = []
    settings = get_settings()
    data_png = "data:image/png;base64," + base64.b64encode(_PNG_1PX).decode()
    try:
        monkeypatch.setattr(settings, "storage_backend", "local")
        monkeypatch.setattr(settings, "ovh_public_base_url", f"{base}/media")
        lesson = CourseLesson(
            lesson_code="M1.L1",
            title="Lezione",
            content_raw={
                "sections": [
                    {"section_id": "S1", "title": "Sezione",
                     "content": f'Testo <img src="{base}/media/autore.png"> '
                                f'<img src="file:///etc/hosts"> <img src="{data_png}">'}
                ]
            },
        )  # fmt: skip
        html = pdf.render_lesson_html(
            course=_course(), lesson=lesson, organization=None, pdf_template=None
        )
        with structlog.testing.capture_logs() as logs:
            data = pdf._render_with_weasyprint_sync(html)
        assert _RecordingHandler.hits == []
        blocked = sorted(e["url"] for e in logs if e["event"] == "pdf_resource_blocked")
        assert blocked == sorted([f"{base}/media/autore.png", "file:///etc/hosts"])
        assert _image_count(data) == 1

        monkeypatch.setattr(settings, "storage_backend", "ovh_sftp")
        media_html = (
            f'<p><img src="{base}/media/logo.png"> <img src="{base}/altro.png"> '
            f'<img src="{data_png}"></p>'
        )
        with structlog.testing.capture_logs() as logs:
            data = pdf._render_with_weasyprint_sync(media_html)
        assert _RecordingHandler.hits == ["/media/logo.png"]
        blocked = [e["url"] for e in logs if e["event"] == "pdf_resource_blocked"]
        assert blocked == [f"{base}/altro.png"]
        assert _image_count(data) == 2
    finally:
        server.shutdown()
        server.server_close()


def test_slide_refs_match_with_spaces_like_the_crud_and_the_editor() -> None:
    """`_asset_ref_key` (minuscolo, senza spazi ai bordi) è la chiave del
    CRUD (`_slide_visual_refs`) e dell'editor (`assetRefKey`): un
    riferimento che il PATCH accetta è anche reso nel PDF."""
    content_raw = {"sections": [], "visual_assets": [_visual("fig_1")]}
    refs = [" fig_1 ", "FIG_1"]
    slides_crud._validate_consistency(
        payload=_payload(_slide("s1", references_assets=refs)),
        current_raw={"slides": []},
        content_raw=content_raw,
    )
    resolved = slides_pdf._resolve_asset_for_slide(" FIG_1\t", content_raw, [])
    assert resolved is not None and resolved[0] == "visual"
    html, logs = _render_slides(
        [_slide("s1", references_assets=refs)],
        content_raw=content_raw,
        visual_svg_map={"fig_1": SVG_A},
    )
    (slide,) = html.split('<div class="slide">')[1:]
    assert slide.count("<figure") == 1 and 'data-asset-id="fig_1"' in slide
    dup = [
        (e["slide_id"], e["asset_id"]) for e in logs if e["event"] == "slide_duplicate_asset_ref"
    ]
    assert dup == [("s1", "FIG_1")]


async def test_ai_slides_count_distinct_visuals_like_the_crud() -> None:
    """Percorso AI (`materialize_lesson_slides`, punto 6b): la stessa figura
    citata con due grafie è un asset solo, come nel CRUD e nel PDF; due
    visivi diversi restano 409."""
    from app.schemas.course_lesson_slides import LessonSlidesOutput
    from app.services import course_lesson_slides_service as slides_svc

    def output(refs: list[str]) -> LessonSlidesOutput:
        return LessonSlidesOutput(
            lesson_id="M1.L1",
            total_slides=2,
            slides=[
                LessonSlideItem(**_slide("s1", slide_number=1, references_assets=refs)),
                LessonSlideItem(**_slide("s2", slide_number=2)),
            ],
        )

    for refs in (["fig_1", "FIG_1"], [" fig_1", "eq_1", "EX_1 "]):
        lesson = CourseLesson(lesson_code="M1.L1", title="L", content_raw=_CONTENT_RAW)
        out = output(refs)
        await slides_svc.materialize_lesson_slides(
            None,  # type: ignore[arg-type]  # nessun accesso al DB
            course=_speech_course(lesson),
            lesson=lesson,
            output=out,
            raw=out.model_dump(),
            usage={},
        )
        assert lesson.slides_status == "ready", refs
    for refs in (["fig_1", "tab_1"], ["fig_1", "FIG_2"]):
        lesson = CourseLesson(lesson_code="M1.L1", title="L", content_raw=_CONTENT_RAW)
        out = output(refs)
        with pytest.raises(ConflictError) as exc:
            await slides_svc.materialize_lesson_slides(
                None,  # type: ignore[arg-type]
                course=_speech_course(lesson),
                lesson=lesson,
                output=out,
                raw=out.model_dump(),
                usage={},
            )
        assert exc.value.code == "lesson_slides_multiple_visual_assets", refs
