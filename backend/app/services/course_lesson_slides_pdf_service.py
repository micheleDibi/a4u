"""Service di export PDF delle SLIDE (Fase 4 §7).

Pipeline:
  slides_raw + content_raw + pdf_template
        ↓ registro dei renderer (Mermaid via Playwright, Vega-Lite, DOT,
          function → SVG, tutti come `<img data:svg>` nelle slide)
        ↓ MathJax via Playwright → SVG inline (math)
        ↓ Jinja2 (template `lesson_slides_pdf.html.j2`)
        ↓ WeasyPrint → PDF bytes
        ↓ filesystem (`generated_pdfs/{org}/{course}/{lesson}_slides.pdf`)

Lo stato è scoped a livello LEZIONE (`course_lesson.slides_pdf_status`)
e distinto dal `pdf_status` della lezione testo.

Riusa massivamente gli helper di `course_lesson_pdf_service` per evitare
duplicazione: caricamento template, pre-render delle figure, blocco figura
(partial unico con l'etichetta «Figura.» senza numero, A2), generazione
PDF bytes, audit pattern. Cambia la funzione di rendering HTML che usa il
template dedicato per layout slide (A4 landscape, una slide per pagina).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markdown_it.token import Token
from markupsafe import Markup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.models.organization import Organization
from app.models.slide_template import SlideTemplate
from app.models.user import User
from app.schemas.course_lesson_content import SOURCE_FIGURE_FORMAT
from app.services import course_lesson_pdf_service as base_pdf
from app.services import course_lesson_slides_service, figure_render_service, remote_storage
from app.services.figure_render_service import RenderedFigure, VisualSvgMap
from app.services.figure_scale import FigureBoxMm, FigureFitEntry
from app.services.figure_theme import figure_labels
from app.services.slide_geometry import (
    PageFigureBudget,
    Prose,
    ProseMath,
    ProsePiece,
    SlideGeometry,
    image_box,
    page_figure_budget,
)
from app.services.source_figure_service import (
    ResolvedSourceFigure,
    SourceFigureMap,
    lesson_source_assets,
    resolve_source_figures,
)
from app.services.svg_normalize import svg_to_data_uri

log = get_logger("app.course_lesson_slides_pdf.service")


# Stati `slides_status` da cui è ammesso esportare. Solo `ready`/`approved`.
EXPORTABLE_SLIDES_STATUSES: tuple[str, ...] = ("ready", "approved")
# Stati `slides_pdf_status` da cui è ammesso (ri-)avviare un export.
VALID_SLIDES_PDF_REQUEST_STATUSES: tuple[str, ...] = ("empty", "ready", "failed")


# ---------------------------------------------------------------------------
# Filesystem (path dedicato `_slides.pdf`)
# ---------------------------------------------------------------------------


def slides_pdf_relative_path(
    *,
    organization_id: uuid.UUID,
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
) -> str:
    """Path relativo al `generated_pdfs_dir`. Stabile per (org, corso,
    lezione). Distinto dal PDF della lezione testo via suffisso `_slides`."""
    return f"{organization_id}/{course_id}/{lesson_id}_slides.pdf"


def slides_pdf_filename_for_download(course_title: str, lesson: CourseLesson) -> str:
    """Nome file user-friendly (versione slide della
    `pdf_filename_for_download`)."""
    import re

    safe_lesson = re.sub(r"[^\w\-. ]+", "_", lesson.title)[:80].strip("_ ")
    safe_course = re.sub(r"[^\w\-. ]+", "_", course_title)[:60].strip("_ ")
    return f"{safe_course} — {lesson.lesson_code} {safe_lesson} (slide).pdf"


# ---------------------------------------------------------------------------
# Jinja env (template dedicato slide)
# ---------------------------------------------------------------------------

# Autoescape come la dispensa: `select_autoescape(["html", "xml"])` non
# riconosceva l'estensione reale `.j2` e i campi d'autore uscivano crudi
# (anche nei frame video, dove Chromium esegue la pagina). I `tpl.*` nel CSS
# sono `|safe` o `|css_string` (vedi il template).
_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    autoescape=select_autoescape(enabled_extensions=("html", "xml", "j2")),
    trim_blocks=True,
    lstrip_blocks=True,
)
_jinja_env.filters["css_string"] = base_pdf.css_string


# ---------------------------------------------------------------------------
# Asset rendering per slide
# ---------------------------------------------------------------------------


def _slide_type_label(language: str, slide_type: str) -> str:
    """Traduce il tipo slide in label leggibile per il PDF.

    Niente i18n complessa lato BE: dictionary minimale IT/EN. Per gli
    altri locale usiamo il valore raw."""
    is_en = (language or "it").lower().startswith("en")
    if is_en:
        labels_en = {
            "title": "Title",
            "agenda": "Agenda",
            "prerequisites": "Prerequisites",
            "concept": "Concept",
            "definition": "Definition",
            "diagram": "Diagram",
            "formula": "Formula",
            "table": "Table",
            "example": "Example",
            "case_study": "Case study",
            "exercise": "Exercise",
            "discussion": "Discussion",
            "summary": "Summary",
            "takeaways": "Takeaways",
            "references": "References",
            "bibliography": "Bibliography",
        }
        return labels_en.get(slide_type, slide_type)
    labels_it = {
        "title": "Titolo",
        "agenda": "Agenda",
        "prerequisites": "Prerequisiti",
        "concept": "Concetto",
        "definition": "Definizione",
        "diagram": "Diagramma",
        "formula": "Formula",
        "table": "Tabella",
        "example": "Esempio",
        "case_study": "Caso studio",
        "exercise": "Esercizio",
        "discussion": "Discussione",
        "summary": "Sintesi",
        "takeaways": "Punti chiave",
        "references": "Riferimenti",
        "bibliography": "Bibliografia",
    }
    return labels_it.get(slide_type, slide_type)


# Nome storico di `svg_normalize.svg_to_data_uri` (unica implementazione,
# condivisa da PDF dispensa, PDF slide e frame video): re-export, non copia.
_svg_to_data_uri = svg_to_data_uri


def _page_source_figures(
    page_assets: list[tuple[str, dict[str, Any]]], source_figures: SourceFigureMap | None
) -> tuple[SourceFigureMap | None, list[str]]:
    """Figure di fonte rese sulla pagina e righe «Fonte» della sua fascia.

    Stesse condizioni del blocco figura (senza immagine o senza riga:
    segnaposto e nessuna riga), una riga per figura. La fascia ha 4 righe:
    con 1-2 figure ogni riga può andare su due righe, con 3-4 su una (testo
    accorciato dal resolver, `band_text`/`band_text_short`); dalla quinta
    figura in poi la pagina mostra il segnaposto, mai un'immagine senza la
    sua riga. Restituisce la mappa da usare per i blocchi della pagina.
    """
    if not source_figures:
        return source_figures, []
    shown: list[tuple[str, ResolvedSourceFigure]] = []
    for kind, payload in page_assets:
        if kind not in ("visual", "new_visual") or payload.get("format") != SOURCE_FIGURE_FORMAT:
            continue
        asset_id = str(payload.get("asset_id") or "")
        resolved = source_figures.get(asset_id)
        if resolved is None or not resolved.renderable or not resolved.data_url:
            continue
        shown.append((asset_id, resolved))
    figures: list[Any] = []
    for _asset_id, resolved in shown:
        key = resolved.figure_id or resolved.attribution_text
        if key not in figures:
            figures.append(key)
    geometry = SlideGeometry()
    kept = figures[: geometry.attribution_max_figures]
    per_figure = min(
        geometry.attribution_lines_per_figure, geometry.attribution_max_lines // max(1, len(kept))
    )
    dropped = {
        asset_id
        for asset_id, resolved in shown
        if (resolved.figure_id or resolved.attribution_text) not in kept
    }
    lines: list[str] = []
    for asset_id, resolved in shown:
        if asset_id in dropped:
            continue
        text = resolved.band_text if per_figure >= 2 else resolved.band_text_short
        line = " ".join((text or resolved.attribution_text).split())
        if line and line not in lines:
            lines.append(line)
    if dropped:
        log.warning("slide_source_figures_over_band", dropped=sorted(dropped))
        return {k: v for k, v in source_figures.items() if k not in dropped}, lines
    return source_figures, lines


def _build_slide_asset_html(
    asset: dict[str, Any],
    *,
    kind: str,
    visual_svg_map: VisualSvgMap | None = None,
    math_svg_map: dict | None = None,
    language: str = "it",
    labels: Mapping[str, str] | None = None,
    lesson_code: str | None = None,
    figure_budget: PageFigureBudget | None = None,
    fit_report: list[FigureFitEntry] | None = None,
    cite: Callable[[str], str] = base_pdf._identity,
    source_figures: SourceFigureMap | None = None,
) -> str:
    """Costruisce il blocco HTML per un asset referenziato da una slide.

    `source_figures`: figure di fonte risolte dal server (immagine; la riga
    «Fonte» la mette `render_slides_html` nella fascia della pagina).

    `cite` è il rimando testuale degli asset (`base_pdf.AssetRefs.cite`,
    numeri della dispensa) applicato alle didascalie in una riga, come
    nella dispensa: un `[FIG:x]` nella didascalia della tabella diventa
    «Figura 1» anche qui.

    Gli asset visivi (Fase 3 e `new_assets` di Fase 4) passano dal blocco
    figura del PDF dispensa con `variant="slide"` e `number=None`: stessa
    etichetta «Figura.» senza numerazione progressiva (A2), stesso partial.
    Nelle slide TUTTI gli SVG (Mermaid compreso) sono incapsulati in
    `<img>` con data URI base64 anziché inseriti inline: nel contesto
    slide PDF è l'unico modo affidabile per far rispettare a WeasyPrint
    `max-height`. Un SVG inline con attributi `width="X" height="Y"`
    espliciti emessi da Mermaid (10.9.x come 11.x) ignora il vincolo CSS, e il
    diagramma sborda dal body venendo tagliato. Un `<img>` invece è
    un replaced element con aspect ratio intrinseca: max-width e
    max-height combinati gli applicano scaling proporzionale.

    `visual_svg_map` è `{asset_id → svg | RenderedFigure}` di
    `_prerender_mermaid_for_slides` (tutti i formati); `labels` la mappa
    di `figure_labels(language)`; `figure_budget` il budget del blocco
    sulla pagina resa (D12, `slide_geometry.page_figure_budget`): il blocco
    figura ne ricava il box dell'immagine (`--figure-w`/`--figure-h` sul
    `<figure>`) e dentro quel box applica la banda di leggibilità 10-14 pt
    (D10, `variant="slide"`); `fit_report` è il collettore delle voci del
    fit. Gli altri asset (table/equation/example) usano gli helper del PDF
    lezione testo con `number=None`: etichetta non numerata («Tabella.»,
    «Equazione.», «Esempio.», D5) e, per i teoremi, la sola parola del kind
    come prima; non hanno il problema dello scaling SVG e non ricevono box.
    """
    figure_i18n = labels if labels is not None else figure_labels(language)
    if kind == "visual" or kind == "new_visual":
        return base_pdf._render_visual_asset_block(
            asset,
            visual_svg_map=visual_svg_map,
            math_svg_map=math_svg_map,
            number=None,
            labels=figure_i18n,
            variant="slide",
            language=language,
            lesson_code=lesson_code,
            figure_budget=figure_budget,
            fit_report=fit_report,
            cite=cite,
            source_figures=source_figures,
        )
    if kind == "table":
        return base_pdf._render_table_block(
            asset,
            math_svg_map=math_svg_map,
            number=None,
            labels=figure_i18n,
            language=language,
            cite=cite,
        )
    if kind == "equation":
        return base_pdf._render_equation_block(
            asset,
            math_svg_map=math_svg_map,
            language=language,
            number=None,
            labels=figure_i18n,
            cite=cite,
        )
    if kind == "example":
        return base_pdf._render_example_block(
            asset,
            math_svg_map=math_svg_map,
            number=None,
            labels=figure_i18n,
            language=language,
            cite=cite,
        )
    return ""


def _asset_ref_key(asset_id: object) -> str:
    """Chiave di confronto di un id d'asset: senza spazi ai bordi e in
    minuscolo (`assetRefKey` nel frontend, `_slide_visual_refs` nel CRUD).
    `None` dà la stringa vuota."""
    return str(asset_id or "").strip().lower()


def _resolve_asset_for_slide(
    asset_id: str,
    content_raw: dict[str, Any] | None,
    new_assets: list[dict[str, Any]],
    *,
    new_tables: list[dict[str, Any]] | None = None,
    new_equations: list[dict[str, Any]] | None = None,
    new_examples: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any]] | None:
    """Cerca asset_id nelle Dispense (content_raw) + nei nuovi asset di
    Fase 4 (visivi, tabelle, equazioni, esempi). Ritorna (kind, payload)
    o None.

    Mirror della logica frontend (`lib/slides.resolveAsset`).

    Confronto per `_asset_ref_key` (maiuscole e spazi ai bordi non
    contano): il riferimento della slide e l'id dichiarato dell'asset sono
    generati dall'AI con case non sempre coerente (es. asset `TAB_x`
    referenziato come `tab_x`), e il CRUD e l'editor accettano un
    riferimento con spazi ai bordi (`assetRefKey`).
    """
    aid = _asset_ref_key(asset_id)
    if content_raw:
        for a in content_raw.get("visual_assets") or []:
            if isinstance(a, dict) and _asset_ref_key(a.get("asset_id")) == aid:
                return "visual", a
        for t in content_raw.get("tables") or []:
            if isinstance(t, dict) and _asset_ref_key(t.get("table_id")) == aid:
                return "table", t
        for e in content_raw.get("equations") or []:
            if isinstance(e, dict) and _asset_ref_key(e.get("equation_id")) == aid:
                return "equation", e
        for ex in content_raw.get("examples") or []:
            if isinstance(ex, dict) and _asset_ref_key(ex.get("example_id")) == aid:
                return "example", ex
    for na in new_assets or []:
        if isinstance(na, dict) and _asset_ref_key(na.get("asset_id")) == aid:
            return "new_visual", na
    for t in new_tables or []:
        if isinstance(t, dict) and _asset_ref_key(t.get("table_id")) == aid:
            return "table", t
    for e in new_equations or []:
        if isinstance(e, dict) and _asset_ref_key(e.get("equation_id")) == aid:
            return "equation", e
    for ex in new_examples or []:
        if isinstance(ex, dict) and _asset_ref_key(ex.get("example_id")) == aid:
            return "example", ex
    return None


# ---------------------------------------------------------------------------
# Pre-render delle figure (riusa helper base con dati merged)
# ---------------------------------------------------------------------------


async def _prerender_mermaid_for_slides(
    content_raw: dict[str, Any] | None,
    new_assets: list[dict[str, Any]],
    *,
    language: str = "it",
) -> dict[str, RenderedFigure]:
    """Pre-renderizza tutti gli asset visivi renderizzabili (Fase 3 +
    `new_assets` di Fase 4) attraverso il registro dei renderer: per
    Mermaid una singola sessione Playwright, per Vega-Lite/DOT/function i
    renderer offline. Ritorna {asset_id: RenderedFigure} (SVG e metriche
    del testo, D10). Il nome storico è mantenuto: il PDF slide e il video
    (`lesson_slides_video_render_service`) lo chiamano.
    """
    # Costruisce un dict-like content con tutti gli asset visivi:
    # base_pdf._prerender_visual_assets_for_lesson legge `visual_assets`.
    merged_visual_assets: list[dict[str, Any]] = []
    seen: set[str] = set()
    if content_raw:
        for a in content_raw.get("visual_assets") or []:
            if isinstance(a, dict):
                merged_visual_assets.append(a)
                seen.add(_asset_ref_key(a.get("asset_id")))
    for na in new_assets or []:
        # La mappa è indicizzata per `asset_id`: un nuovo asset con l'id di
        # una figura delle Dispense ne sovrascriverebbe l'SVG, e siccome
        # `_resolve_asset_for_slide` risolve prima le Dispense la slide
        # mostrerebbe la didascalia di una figura e il disegno dell'altra.
        # L'id di Fase 4 è già irraggiungibile: qui si salta il render.
        if isinstance(na, dict) and _asset_ref_key(na.get("asset_id")) not in seen:
            merged_visual_assets.append(na)
    figures = await base_pdf._prerender_visual_assets_for_lesson(
        {"visual_assets": merged_visual_assets}, language=language
    )
    # Direzione delle catene (D15): stessa regola della dispensa, sul box
    # della slide di riferimento. La variante ha la stessa chiave di cache
    # di quella resa per la dispensa (il sorgente variante è lo stesso),
    # quindi in una pipeline che esporta entrambe la resa in più si paga
    # una volta sola.
    return await figure_render_service.render_chain_variants(
        merged_visual_assets,
        figures,
        box_mm=reference_slide_figure_box_mm(),
        variant="slide",
        language=language,
    )


def reference_slide_figure_box_mm() -> FigureBoxMm:
    """Box `(larghezza, altezza)` in mm della figura nella slide di
    RIFERIMENTO: un solo blocco, titolo di una riga, nessuna prosa e
    nessun bullet, didascalia di una riga (255 × 86,6 mm, lo stesso caso
    che l'oracolo D12 documenta).

    Serve solo a decidere se VALE LA PENA rendere la variante verticale di
    una catena (D15): il box vero della pagina lo calcola `image_box` sul
    testo reale di ogni slide, e su quello la misura del fit sceglie fra le
    varianti disponibili. Una slide più densa ha un box più basso: lì una
    catena orizzontale sta ancora peggio, ma anche la verticale, e la
    misura lo constata."""
    budget = page_figure_budget(title="T", body=None, bullets=(), n_blocks=1)
    box, _squeezed = image_box(budget, caption_text="Figura.")
    return box.w_mm, box.h_mm


_prerender_visual_assets_for_slides = _prerender_mermaid_for_slides


def _prose_text(value: object) -> str:
    """Testo di un campo di prosa della slide (`None` → stringa vuota)."""
    return "" if value is None else str(value)


class _SlideProse(NamedTuple):
    """Titolo, prosa e bullet di una slide come testo sorgente: lo stesso
    per il collector del math, per il budget della figura e per
    `render_markdown_inline` (che ne fa il `Markup` del template)."""

    title: str
    body: str
    bullets: list[str]

    def cited(self, cite: Callable[[str], str]) -> _SlideProse:
        """Gli stessi campi con i tag `[KIND:id]` riscritti nel rimando
        testuale della dispensa: è il testo che finisce sulla slide, quindi
        anche quello che il budget misura e che il collector raccoglie."""
        return _SlideProse(cite(self.title), cite(self.body), [cite(b) for b in self.bullets])


def _slide_prose(slide: Mapping[str, Any]) -> _SlideProse:
    return _SlideProse(
        title=_prose_text(slide.get("title")),
        body=_prose_text(slide.get("body")).strip(),
        bullets=[_prose_text(b) for b in slide.get("bullets") or []],
    )


def _slide_prose_texts(slides_raw: Mapping[str, Any] | None) -> list[str]:
    """I campi inline che `render_slides_html` passa a
    `render_markdown_inline`, nello stesso ordine e con lo stesso
    pre-trattamento: titolo, prosa e bullet di ogni slide."""
    texts: list[str] = []
    for slide in (slides_raw or {}).get("slides") or []:
        if isinstance(slide, dict):
            prose = _slide_prose(slide)
            texts.extend([prose.title, prose.body, *prose.bullets])
    return texts


def _math_content_for_slides(
    content_raw: dict[str, Any] | None,
    slides_raw: dict[str, Any] | None,
    *,
    language: str = "it",
) -> dict[str, Any]:
    """Contenuto «fuso» su cui il collector del PDF raccoglie il math delle
    slide: equazioni, tabelle, esempi e figure delle Dispense
    (`content_raw`) più i nuovi asset di Fase 4 (`slides_raw.new_*`), e
    titolo, prosa e bullet delle slide come `inline_texts` (WP4). Le
    figure servono per le didascalie (D9). Helper puro, senza I/O.

    `asset_refs` porta al collector i numeri della DISPENSA
    (`base_pdf.lesson_asset_refs` su `content_raw`): senza, il collector
    li ricalcolerebbe sul contenuto fuso — che non ha corpo e comprende i
    nuovi asset di Fase 4 — e una formula a cavallo di un rimando avrebbe
    chiavi diverse da quelle del renderer."""
    cr = content_raw or {}
    sr = slides_raw or {}
    return {
        "equations": list(cr.get("equations") or []) + list(sr.get("new_equations") or []),
        "tables": list(cr.get("tables") or []) + list(sr.get("new_tables") or []),
        "examples": list(cr.get("examples") or []) + list(sr.get("new_examples") or []),
        "visual_assets": list(cr.get("visual_assets") or []) + list(sr.get("new_assets") or []),
        "inline_texts": _slide_prose_texts(sr),
        "asset_refs": base_pdf.lesson_asset_refs(cr, language=language),
    }


# Delimitatore di chiusura per il markup di apertura dei token math.
_MATH_CLOSERS: dict[str, str] = {"$": "$", "$$": "$$", "\\(": "\\)", "\\[": "\\]"}


def _math_source(tok: Token) -> str:
    return f"{tok.markup}{tok.content}{_MATH_CLOSERS.get(tok.markup, tok.markup)}"


def _prose_for_budget(text: str, math_svg_map: Mapping[Any, Any] | None) -> Prose:
    """Il campo come lo misura `slide_geometry`: la stringa se non ha
    formule (stima identica a prima), altrimenti i pezzi nell'ordine del
    parse inline (stessa grammatica del renderer): testo e `ProseMath` con
    l'SVG della mappa (`None` se manca: il renderer ricade sul MathML) e la
    forma (`span.math-block` → blocco). Il lookup è un `get` semplice: i
    miss sono contati solo dal renderer."""
    if not text:
        return text
    children = [
        child
        for tok in base_pdf._md_inline_renderer.parseInline(text, {})
        for child in tok.children or []
    ]
    if not any(child.type in base_pdf._MATH_TOKEN_DISPLAY for child in children):
        return text
    pieces: list[ProsePiece] = []
    for child in children:
        if child.type not in base_pdf._MATH_TOKEN_DISPLAY:
            pieces.append(child.content)
            continue
        key = base_pdf._token_math_key(child)
        if key is None:  # formula vuota: il renderer non emette nulla
            continue
        svg = math_svg_map.get(key) if math_svg_map else None
        _tag, cls = base_pdf._math_markup(child)
        pieces.append(
            ProseMath(
                source=_math_source(child),
                svg=svg if isinstance(svg, str) and svg else None,
                block=cls == "math-block",
            )
        )
    return tuple(pieces)


async def _prerender_math_for_slides(
    content_raw: dict[str, Any] | None,
    slides_raw: dict[str, Any] | None,
    *,
    language: str = "it",
) -> base_pdf.MathSvgMap:
    """Pre-render LaTeX → SVG per le slide sul contenuto di
    `_math_content_for_slides`. Riusa il collector + batch MathJax del PDF
    lezione (`base_pdf`); la lingua del corso decide il testo dei rimandi
    riscritti, che può finire dentro una formula."""
    return await base_pdf._prerender_math_for_lesson(
        _math_content_for_slides(content_raw, slides_raw, language=language)
    )


# ---------------------------------------------------------------------------
# Slide template helpers (resolve + format)
# ---------------------------------------------------------------------------


async def _get_default_slide_template(
    db: AsyncSession, *, organization_id: uuid.UUID
) -> SlideTemplate | None:
    """Default `is_default=True`, fallback al primo, `None` se nessuno."""
    res = await db.execute(
        select(SlideTemplate)
        .where(SlideTemplate.organization_id == organization_id)
        .order_by(SlideTemplate.is_default.desc(), SlideTemplate.created_at.asc())
        .limit(1)
    )
    return res.scalar_one_or_none()


async def _get_slide_template_or_404(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    template_id: uuid.UUID,
) -> SlideTemplate:
    res = await db.execute(
        select(SlideTemplate).where(
            SlideTemplate.id == template_id,
            SlideTemplate.organization_id == organization_id,
        )
    )
    tpl = res.scalar_one_or_none()
    if tpl is None:
        raise NotFoundError(
            f"Slide template {template_id} non trovato.",
            code="slide_template_not_found",
        )
    return tpl


def _format_slide_template_for_render(
    tpl: SlideTemplate, *, public_base_url: str | None
) -> dict[str, Any]:
    """Adatta SlideTemplate al dict `tpl` consumato dal Jinja delle slide.

    Usa gli stessi nomi di campo del PdfTemplate dove possibile, così il
    template HTML (che legge `tpl.font_family`, `tpl.text_color`,
    `tpl.primary_color`, `tpl.secondary_color`) funziona senza modifiche.
    """
    return {
        "font_family": tpl.font_family,
        "text_color": tpl.text_color,
        "primary_color": tpl.primary_color,
        "secondary_color": tpl.secondary_color,
        "slide_size": tpl.slide_size,
        "margin_mm": tpl.margin_mm,
        "background_opacity_pct": tpl.background_opacity_pct,
        "background_image_url": base_pdf._resolve_template_asset_url(
            tpl.background_image_path, public_base_url=public_base_url
        ),
        "logo_left_url": base_pdf._resolve_template_asset_url(
            tpl.logo_left_path, public_base_url=public_base_url
        ),
        "logo_right_url": base_pdf._resolve_template_asset_url(
            tpl.logo_right_path, public_base_url=public_base_url
        ),
    }


def _default_slide_template_dict() -> dict[str, Any]:
    return {
        "font_family": "Inter",
        "text_color": "#1F1F1F",
        "primary_color": "#1976D2",
        "secondary_color": "#9C27B0",
        "slide_size": "16:9",
        "margin_mm": 20,
        "background_opacity_pct": 0,
        "background_image_url": None,
        "logo_left_url": None,
        "logo_right_url": None,
    }


# ---------------------------------------------------------------------------
# render_slides_html
# ---------------------------------------------------------------------------


class _PageProse(NamedTuple):
    """Prosa e bullet di una pagina resa: sorgente (budget) e Markup."""

    body: str
    body_html: Markup
    bullets: list[str]
    bullets_html: list[Markup]


_EMPTY_PAGE_PROSE = _PageProse("", Markup(""), [], [])


def render_slides_html(
    *,
    course: Course,
    lesson: CourseLesson,
    organization: Organization | None,
    slide_template: SlideTemplate | None,
    public_base_url: str | None = None,
    mermaid_svg_map: VisualSvgMap | None = None,
    math_svg_map: dict | None = None,
    enable_split: bool = True,
    teacher_name: str | None = None,
    visual_svg_map: VisualSvgMap | None = None,
    fit_report: list[FigureFitEntry] | None = None,
    source_figures: SourceFigureMap | None = None,
) -> str:
    """Pure-function: HTML completo delle slide pronto per WeasyPrint.

    Figure di fonte (`source_figures`, dal resolver del server): immagine
    nel blocco figura e riga «Fonte» nella fascia `.slide-attribution` della
    PAGINA che la mostra (U2: in basso a sinistra, fuori dall'avatar), mai
    nella didascalia; una figura di fonte senza riga resta segnaposto.

    `visual_svg_map` è `{asset_id → svg | RenderedFigure}` per tutti i
    formati renderizzabili; `mermaid_svg_map` è il nome storico dello stesso
    argomento (le due mappe sono fuse, `visual_svg_map` prevale). Le figure
    portano l'etichetta «Figura.» senza numero (A2) e la larghezza dalla
    banda di leggibilità 10-14 pt (D10) entro il box della pagina;
    `fit_report`, se dato, raccoglie una `FigureFitEntry` per figura.

    Ogni pagina riceve il box figura calcolato da `slide_geometry` sul
    contenuto della pagina EFFETTIVA (dopo la decisione di split, D12):
    `page_figure_budget` sottrae ai 120 mm del body tag, titolo, prosa,
    bullet, margini e safety, e divide il resto in parti uguali fra i
    blocchi asset della pagina; il blocco figura lo converte nel box
    dell'immagine tenendo conto della didascalia reale. Nel video, senza
    split, bullet e figura si dividono così i 120 mm senza tagli, salvo le
    pagine impossibili (testo da solo oltre il body), che ricevono il
    pavimento e un `slide_figure_box_exhausted` nel log; una pagina con più
    blocchi (solo legacy) è segnalata con `slide_figure_box_shared`.

    Titolo, prosa e bullet passano PRIMA da `AssetRefs.cite` (i tag
    `[KIND:id]` lasciati dal modello nella prosa diventano il rimando
    testuale «Figura 1», con i numeri della DISPENSA e mai un blocco
    figura: la figura sulla slide arriva da `references_assets`) e poi da
    `render_markdown_inline` (testo escapato e formule SVG, niente markdown
    ricco: WP4); il budget li misura sul sorgente citato con gli SVG delle
    formule (`_prose_for_budget`). Le didascalie in una riga degli asset
    ricevono lo stesso trattamento. Un tag che nessun numero risolve — id
    inesistente, o asset dichiarato solo in Fase 4, che la dispensa non
    numera — resta com'è, senza rimando inventato, e la slide produce un
    `slide_asset_ref_unresolved`. Un asset citato più volte dalla stessa
    slide è reso una volta, con un `slide_duplicate_asset_ref` per ogni
    ripetizione.

    `enable_split` (default True): per il PDF cartaceo, una slide con
    bullet+asset viene splittata su 2 pagine consecutive (pattern visivo
    necessario per A4 landscape, spazio verticale limitato). Per la
    pipeline video (`lesson_slides_video_render_service`) chiamare con
    `enable_split=False`: 1 slide JSON → 1 pagina renderizzata, niente
    duplicazioni che complicherebbero il mapping audio↔frame.
    """
    slides_raw = lesson.slides_raw or {}
    if not slides_raw:
        raise ConflictError(
            f"Lezione {lesson.lesson_code} senza slides_raw — impossibile esportare.",
            code="lesson_slides_missing",
        )
    # Contenuto d'archivio che non è un oggetto: lezione senza asset (stessa
    # coercizione di `_prepare_lesson_body`, un solo punto per superficie).
    content_raw = lesson.content_raw if isinstance(lesson.content_raw, dict) else {}
    new_assets = slides_raw.get("new_assets") or []
    new_tables = slides_raw.get("new_tables") or []
    new_equations = slides_raw.get("new_equations") or []
    new_examples = slides_raw.get("new_examples") or []

    language = (course.language_code or "it").lower()

    if slide_template is not None:
        tpl_dict = _format_slide_template_for_render(
            slide_template, public_base_url=public_base_url
        )
    else:
        tpl_dict = _default_slide_template_dict()

    svg_map: dict[str, str | RenderedFigure] = {
        **(mermaid_svg_map or {}),
        **(visual_svg_map or {}),
    }
    figure_i18n = figure_labels(language)
    # Numeri e rimando testuale della DISPENSA (stessa funzione, stesso
    # `content_raw`): «Figura 1» è la stessa figura nelle due superfici.
    refs = base_pdf.lesson_asset_refs(content_raw, language=language)
    # Espansione: se una slide ha sia bullet sia asset, viene divisa in
    # due pagine consecutive con lo stesso titolo:
    #   - pagina N: tag "Lezione X" + titolo + bullet (niente asset)
    #   - pagina N+1: tag "Lezione X" + titolo (stesso) + asset (niente
    #     bullet)
    # Vantaggio: gli asset hanno sempre l'intero body a disposizione
    # per il rendering — niente competizione verticale. La numerazione
    # `slide_number` viene ricalcolata sulla sequenza di pagine effettive.
    # Due passi per slide: (1) risoluzione degli asset e decisione di split,
    # (2) per ogni pagina resa il budget della figura sul suo contenuto
    # reale (D12) e solo allora il rendering dei blocchi.
    rendered_slides: list[dict[str, Any]] = []

    def _inline(text: str) -> Markup:
        return Markup(base_pdf.render_markdown_inline(text, math_svg_map))

    for s in slides_raw.get("slides") or []:
        if not isinstance(s, dict):
            continue
        slide_type = s.get("type", "concept")
        type_label = _slide_type_label(language, slide_type)
        # Titolo, prosa e bullet: prima il rimando testuale, poi il testo
        # escapato più le formule (WP4), come i campi inline della
        # dispensa; il sorgente citato resta per il budget.
        prose = _slide_prose(s).cited(refs.cite)
        unresolved = refs.unresolved(prose.title, prose.body, *prose.bullets)
        if unresolved:
            log.warning(
                "slide_asset_ref_unresolved",
                lesson_code=lesson.lesson_code,
                slide_id=s.get("slide_id"),
                tags=unresolved,
            )
        title_html = _inline(prose.title)
        body_html = _inline(prose.body)
        bullets_html = [_inline(b) for b in prose.bullets]

        # Un asset citato più volte (anche con maiuscole o spazi ai bordi
        # diversi: il lookup usa `_asset_ref_key` e ritorna lo stesso
        # payload) è reso una volta sola; i ripetuti sono loggati (C10).
        resolved_assets: list[tuple[str, dict[str, Any]]] = []
        rendered_payloads: set[int] = set()
        for aid in s.get("references_assets") or []:
            resolved = _resolve_asset_for_slide(
                aid,
                content_raw,
                new_assets,
                new_tables=new_tables,
                new_equations=new_equations,
                new_examples=new_examples,
            )
            if resolved is None:
                continue
            if id(resolved[1]) in rendered_payloads:
                log.warning(
                    "slide_duplicate_asset_ref",
                    lesson_code=lesson.lesson_code,
                    slide_id=s.get("slide_id"),
                    asset_id=aid,
                )
                continue
            rendered_payloads.add(id(resolved[1]))
            resolved_assets.append(resolved)

        base_entry = {
            "slide_id": s.get("slide_id"),
            "type": slide_type,
            "type_label": type_label,
            "title": title_html,
        }
        full_page = _PageProse(prose.body, body_html, prose.bullets, bullets_html)

        # Split: una slide LEGACY con bullet E asset → 2 pagine, asset
        # isolato. Dalla regola Fase 4 "un asset visivo/tabella per
        # slide dedicata" in poi, una slide o ha bullet (contenuto,
        # niente asset) o è dedicata a UN asset (titolo + body breve +
        # asset, niente bullet): in nessuno dei due casi si splitta, e
        # PDF e video rendono identici (1 slide JSON → 1 pagina). Lo
        # split resta solo come fallback per i corsi generati prima di
        # quella regola. Una slide dedicata a un asset NON va mai
        # separata dal suo titolo. Solo i bullet fanno splittare: la prosa
        # resta con la figura (`_resolve_asset_for_slide` ritorna solo kind
        # che `_build_slide_asset_html` rende sempre, quindi «asset risolti»
        # equivale ad «asset resi»).
        # Disabilitato del tutto con `enable_split=False` (pipeline video).
        pages: list[tuple[_PageProse, list[tuple[str, dict[str, Any]]]]]
        if enable_split and resolved_assets and prose.bullets:
            # Niente prosa nella pagina asset-only.
            pages = [(full_page, []), (_EMPTY_PAGE_PROSE, resolved_assets)]
        else:
            pages = [(full_page, resolved_assets)]

        for page_prose, page_assets in pages:
            assets_html: list[str] = []
            page_sources, attributions = _page_source_figures(page_assets, source_figures)
            if page_assets:
                # Il budget misura il sorgente: con le formule, i pezzi con
                # gli SVG (larghezza e altezza di riga, `slide_geometry`).
                budget = page_figure_budget(
                    title=_prose_for_budget(prose.title, math_svg_map),
                    body=_prose_for_budget(page_prose.body, math_svg_map),
                    bullets=[_prose_for_budget(b, math_svg_map) for b in page_prose.bullets],
                    n_blocks=len(page_assets),
                )
                if budget.clamped:
                    log.warning(
                        "slide_figure_box_exhausted",
                        lesson_code=lesson.lesson_code,
                        slide_id=s.get("slide_id"),
                        available_mm=budget.available_mm,
                        deficit_mm=round(budget.block_h_mm - budget.available_mm, 1),
                        n_blocks=budget.n_blocks,
                    )
                if len(page_assets) > 1:
                    log.info(
                        "slide_figure_box_shared",
                        lesson_code=lesson.lesson_code,
                        slide_id=s.get("slide_id"),
                        n_blocks=budget.n_blocks,
                        block_h_mm=budget.block_h_mm,
                    )
                for kind, payload in page_assets:
                    html = _build_slide_asset_html(
                        payload,
                        kind=kind,
                        visual_svg_map=svg_map,
                        math_svg_map=math_svg_map,
                        language=language,
                        labels=figure_i18n,
                        lesson_code=lesson.lesson_code,
                        figure_budget=budget,
                        fit_report=fit_report,
                        cite=refs.cite,
                        source_figures=page_sources,
                    )
                    if html:
                        assets_html.append(html)
            rendered_slides.append(
                {
                    **base_entry,
                    "body": page_prose.body_html,
                    "bullets": page_prose.bullets_html,
                    "assets_html": assets_html,
                    "attributions": attributions,
                }
            )

    # Riassegna slide_number / total in base alla sequenza espansa.
    total_slides = len(rendered_slides)
    for i, rs in enumerate(rendered_slides, start=1):
        rs["slide_number"] = i

    # Etichetta lezione "Modulo X - lezione Y" (localizzata), come le dispense.
    base_labels = base_pdf._labels_for(language)
    lesson_label = base_pdf._format_lesson_code_label(lesson.lesson_code, base_labels)

    template = _jinja_env.get_template("lesson_slides_pdf.html.j2")
    html = template.render(
        language=language,
        course={
            "title": course.title,
            "language": language,
            "cfu": course.cfu,
            "teacher": teacher_name,
        },
        lesson={"title": lesson.title, "lesson_code": lesson.lesson_code},
        lesson_label=lesson_label,
        cfu_label=base_labels["cfu"],
        tpl=tpl_dict,
        slides=rendered_slides,
        total_slides=total_slides,
    )
    return html


# ---------------------------------------------------------------------------
# Materializzazione (chiamato dal worker)
# ---------------------------------------------------------------------------


async def materialize_lesson_slides_pdf(
    db: AsyncSession,
    *,
    course: Course,
    lesson: CourseLesson,
    public_base_url: str | None = None,
) -> str:
    """Genera e salva su disco il PDF delle slide. Aggiorna i campi DB.
    Restituisce il path relativo persistito."""
    organization = await base_pdf._get_organization(db, course.organization_id)
    teacher = await db.get(User, course.assignee_user_id)
    teacher_name = teacher.full_name if teacher else None

    # Risolvi il template SLIDE (unificato con quello dell'avatar):
    # se la lezione ne ha uno scelto esplicitamente, usalo; altrimenti
    # cadi sul default `slide_templates` dell'org.
    slide_template: SlideTemplate | None = None
    if lesson.slides_pdf_template_id is not None:
        slide_template = await _get_slide_template_or_404(
            db,
            organization_id=course.organization_id,
            template_id=lesson.slides_pdf_template_id,
        )
    else:
        slide_template = await _get_default_slide_template(
            db, organization_id=course.organization_id
        )

    slides_raw = lesson.slides_raw or {}
    new_assets = slides_raw.get("new_assets") or []
    language = (course.language_code or "it").lower()
    visual_svg_map = await _prerender_mermaid_for_slides(
        lesson.content_raw, new_assets, language=language
    )
    # Pre-render LaTeX → SVG (MathJax): WeasyPrint non rende il MathML.
    math_svg_map = await _prerender_math_for_slides(
        lesson.content_raw, slides_raw, language=language
    )
    # Figure di fonte (della dispensa, citate dalle slide): immagine e riga
    # «Fonte» decise dal server.
    source_figures = await resolve_source_figures(
        db,
        course_id=course.id,
        assets=lesson_source_assets(lesson),
        language=language,
    )

    fit_report: list[FigureFitEntry] = []
    html = await asyncio.to_thread(
        render_slides_html,
        course=course,
        lesson=lesson,
        organization=organization,
        slide_template=slide_template,
        public_base_url=public_base_url,
        visual_svg_map=visual_svg_map,
        math_svg_map=math_svg_map,
        teacher_name=teacher_name,
        fit_report=fit_report,
        source_figures=source_figures,
    )
    # Un evento per lezione se qualche formula è ricaduta sul MathML.
    base_pdf._log_math_fallbacks(lesson_code=lesson.lesson_code, svg_map=math_svg_map)
    # Un evento per lezione con l'esito del fit delle figure (gate D13).
    base_pdf._log_figure_fit_report(lesson_code=lesson.lesson_code, fit_report=fit_report)

    pdf_bytes = await base_pdf.generate_pdf_bytes(html=html)

    rel = slides_pdf_relative_path(
        organization_id=course.organization_id,
        course_id=course.id,
        lesson_id=lesson.id,
    )
    await asyncio.to_thread(
        remote_storage.get_storage().upload_bytes,
        remote_storage.pdf_key(rel),
        pdf_bytes,
    )

    lesson.slides_pdf_path = rel
    lesson.slides_pdf_template_id = slide_template.id if slide_template else None
    lesson.slides_pdf_generated_at = datetime.now(UTC)
    return rel


# ---------------------------------------------------------------------------
# Public API: enqueue + cancel
# ---------------------------------------------------------------------------


async def request_lesson_slides_pdf(
    db: AsyncSession,
    *,
    course: Course,
    lesson: CourseLesson,
    actor_id: uuid.UUID,
    pdf_template_id: uuid.UUID | None = None,
) -> Course:
    """Sposta `slides_pdf_status → pending`. Vincoli:
    - `lesson.slides_status` ∈ ready/approved
    - `lesson.slides_pdf_status` ∈ empty/ready/failed
    """
    if lesson.slides_status not in EXPORTABLE_SLIDES_STATUSES:
        raise ConflictError(
            f"Lezione {lesson.lesson_code}: slide non `ready`/`approved` "
            f"(attuale: {lesson.slides_status}).",
            code="invalid_lesson_slides_status_for_pdf",
        )
    if lesson.slides_pdf_status not in VALID_SLIDES_PDF_REQUEST_STATUSES:
        raise ConflictError(
            f"Export PDF slide già in corso per {lesson.lesson_code}: {lesson.slides_pdf_status}",
            code="slides_pdf_already_in_progress",
        )

    if pdf_template_id is not None:
        await _get_slide_template_or_404(
            db,
            organization_id=course.organization_id,
            template_id=pdf_template_id,
        )
        lesson.slides_pdf_template_id = pdf_template_id

    lesson.slides_pdf_status = "pending"
    lesson.slides_pdf_error = None
    lesson.slides_pdf_progress = 0
    lesson.slides_pdf_progress_phase = None

    await write_audit(
        db,
        action="course.lesson.slides_pdf.requested",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course_lesson",
        target_id=str(lesson.id),
        metadata={
            "course_id": str(course.id),
            "lesson_code": lesson.lesson_code,
            "pdf_template_id": (str(pdf_template_id) if pdf_template_id else None),
        },
    )
    await db.commit()
    return await course_lesson_slides_service._refresh_full(db, course)


async def request_all_lessons_slides_pdf(
    db: AsyncSession,
    *,
    course: Course,
    actor_id: uuid.UUID,
    pdf_template_id: uuid.UUID | None = None,
    only_missing: bool = False,
) -> Course:
    """Marca tutte le lezioni esportabili come slides_pdf_status='pending'.

    Se `only_missing=True`, esclude le lezioni con PDF slide già
    `ready`: filtra a `slides_pdf_status ∈ (empty, failed)`. Utile per
    "Genera PDF slide mancanti".
    """
    pdf_status_filter: tuple[str, ...] = (
        ("empty", "failed") if only_missing else VALID_SLIDES_PDF_REQUEST_STATUSES
    )
    eligible: list[CourseLesson] = []
    for module in course.modules:
        for lesson in module.lessons:
            if (
                lesson.slides_status in EXPORTABLE_SLIDES_STATUSES
                and lesson.slides_pdf_status in pdf_status_filter
                and not lesson.is_assessment
            ):
                eligible.append(lesson)
    if not eligible:
        raise ConflictError(
            "Nessuna lezione esportabile (servono slide ready/approved).",
            code="no_eligible_lessons_for_slides_pdf",
        )

    if pdf_template_id is not None:
        await _get_slide_template_or_404(
            db,
            organization_id=course.organization_id,
            template_id=pdf_template_id,
        )

    for lesson in eligible:
        if pdf_template_id is not None:
            lesson.slides_pdf_template_id = pdf_template_id
        lesson.slides_pdf_status = "pending"
        lesson.slides_pdf_error = None
        lesson.slides_pdf_progress = 0
        lesson.slides_pdf_progress_phase = None

    await write_audit(
        db,
        action="course.lesson.slides_pdf.requested_all",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course",
        target_id=str(course.id),
        metadata={
            "lessons_count": len(eligible),
            "pdf_template_id": (str(pdf_template_id) if pdf_template_id else None),
        },
    )
    await db.commit()
    return await course_lesson_slides_service._refresh_full(db, course)


async def cancel_all_slides_pdf_exports(
    db: AsyncSession,
    *,
    course: Course,
    actor_id: uuid.UUID,
) -> Course:
    """Annulla export slide PDF in flight."""
    affected: list[CourseLesson] = []
    for module in course.modules:
        for lesson in module.lessons:
            if lesson.slides_pdf_status in ("pending", "processing"):
                lesson.slides_pdf_status = "failed"
                lesson.slides_pdf_error = "Export annullato"
                lesson.slides_pdf_progress = 0
                lesson.slides_pdf_progress_phase = None
                affected.append(lesson)

    if affected:
        await write_audit(
            db,
            action="course.lesson.slides_pdf.cancelled",
            actor_user_id=actor_id,
            organization_id=course.organization_id,
            target_type="course",
            target_id=str(course.id),
            metadata={
                "cancelled_lesson_codes": [x.lesson_code for x in affected],
            },
        )
    await db.commit()
    return await course_lesson_slides_service._refresh_full(db, course)
