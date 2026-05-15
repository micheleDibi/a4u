"""Rendering delle slide come PNG 1920×1080 per la pipeline video.

Riusa il template Jinja `lesson_slides_video.html.j2` (variante senza
@page/footer del template PDF) + lo stesso meccanismo di pre-rendering
Mermaid → SVG di `course_lesson_slides_pdf_service`. Per ogni slide
prende uno screenshot dell'elemento `[data-slide-id=...]` via Playwright.

Esempio output:
    output_dir/
        slide_001.png  # 1920×1080 PNG
        slide_002.png
        ...

Output usato da `lesson_video_compose_service`: ffmpeg `-loop 1 -i
slide_NNN.png` per costruire i segmenti video.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.models.organization import Organization
from app.models.slide_template import SlideTemplate
from app.services import course_lesson_slides_pdf_service as slides_pdf
from app.services import course_lesson_pdf_service as base_pdf

log = get_logger("app.lesson_slides_png")


_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


# ---------------------------------------------------------------------------
# HTML render (riusa logica di slides_pdf con template video dedicato)
# ---------------------------------------------------------------------------


def _build_slides_html_for_video(
    *,
    course: Course,
    lesson: CourseLesson,
    organization: Organization | None,
    slide_template: SlideTemplate | None,
    public_base_url: str | None,
    mermaid_svg_map: dict[str, str],
) -> tuple[str, list[str]]:
    """Costruisce l'HTML completo per il rendering Playwright + restituisce
    la lista ORDINATA di `slide_id` (utile al video composer per mappare
    PNG → audio segment).

    A differenza del PDF (`render_slides_html` di `slides_pdf`), qui NON
    splittiamo le slide con bullet+asset su pagine separate: il video
    16:9 1920×1080 ha più spazio verticale del foglio A4 landscape PDF
    (210mm di altezza) e mostra tutto in un singolo frame, semplificando
    il mapping 1:1 con `slide_to_segments_map`.
    """
    slides_raw = lesson.slides_raw or {}
    if not slides_raw:
        raise ValueError(
            f"Lezione {lesson.lesson_code}: slides_raw mancante."
        )
    content_raw = lesson.content_raw or {}
    new_assets = slides_raw.get("new_assets") or []

    language = (course.language_code or "it").lower()

    if slide_template is not None:
        tpl_dict = slides_pdf._format_slide_template_for_render(
            slide_template, public_base_url=public_base_url
        )
    else:
        tpl_dict = slides_pdf._default_slide_template_dict()

    rendered_slides: list[dict[str, Any]] = []
    slide_id_order: list[str] = []
    for s in slides_raw.get("slides") or []:
        if not isinstance(s, dict):
            continue
        slide_type = s.get("type", "concept")
        title = s.get("title", "")
        type_label = slides_pdf._slide_type_label(language, slide_type)
        body_text = (s.get("body") or "").strip()
        bullets = list(s.get("bullets") or [])

        assets_html: list[str] = []
        for aid in s.get("references_assets") or []:
            resolved = slides_pdf._resolve_asset_for_slide(
                aid, content_raw, new_assets
            )
            if resolved is None:
                continue
            kind, payload = resolved
            html = slides_pdf._build_slide_asset_html(
                payload, kind=kind, mermaid_svg_map=mermaid_svg_map
            )
            if html:
                assets_html.append(html)

        rendered_slides.append(
            {
                "slide_id": s.get("slide_id"),
                "type": slide_type,
                "type_label": type_label,
                "title": title,
                "body": body_text,
                "bullets": bullets,
                "assets_html": assets_html,
            }
        )
        slide_id_order.append(str(s.get("slide_id") or ""))

    # Etichetta "Lezione N" (uguale a slides_pdf).
    lesson_word = "Lesson" if language.startswith("en") else "Lezione"
    lesson_num = ""
    if lesson.lesson_code:
        last = lesson.lesson_code.split(".")[-1].strip()
        if last and last[0].upper() == "L":
            lesson_num = last[1:]
        else:
            lesson_num = last
    lesson_label = (
        f"{lesson_word} {lesson_num}".strip() if lesson_num else lesson_word
    )

    template = _jinja_env.get_template("lesson_slides_video.html.j2")
    html = template.render(
        language=language,
        course={"title": course.title, "language": language},
        lesson={"title": lesson.title, "lesson_code": lesson.lesson_code},
        lesson_label=lesson_label,
        organization_name=(organization.name if organization else None),
        tpl=tpl_dict,
        slides=rendered_slides,
    )
    return html, slide_id_order


# ---------------------------------------------------------------------------
# Playwright screenshot — async + sync wrapper (Windows-safe)
# ---------------------------------------------------------------------------


async def _screenshot_slides_async(
    html: str,
    slide_index_count: int,
    output_dir: Path,
) -> list[Path]:
    """Avvia Chromium headless 1920×1080, carica l'HTML, prende uno
    screenshot per ciascun elemento `section.slide`. Ritorna i path delle
    PNG nell'ordine di apparizione nel DOM.
    """
    from playwright.async_api import async_playwright

    output_dir.mkdir(parents=True, exist_ok=True)
    png_paths: list[Path] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--no-sandbox"])
        try:
            ctx = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                device_scale_factor=1,
            )
            page = await ctx.new_page()
            await page.set_content(html, wait_until="networkidle")
            # Wait fonts settled (in caso di font-family esterni).
            try:
                await page.evaluate(
                    "document.fonts && document.fonts.ready"
                )
            except Exception:  # pragma: no cover
                pass

            sections = await page.query_selector_all("section.slide")
            if len(sections) != slide_index_count:
                log.warning(
                    "slides_png_count_mismatch",
                    expected=slide_index_count,
                    found=len(sections),
                )
            for i, section in enumerate(sections):
                png_path = output_dir / f"slide_{i + 1:03d}.png"
                await section.screenshot(
                    path=str(png_path), type="png", omit_background=False
                )
                png_paths.append(png_path)
        finally:
            await browser.close()

    return png_paths


def _screenshot_slides_sync(
    html: str,
    slide_index_count: int,
    output_dir: Path,
) -> list[Path]:
    """Sync wrapper come `_prerender_mermaid_to_svg_batch_sync`: crea un
    nuovo loop (ProactorEventLoop su Windows per Playwright subprocess)."""
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(
            _screenshot_slides_async(html, slide_index_count, output_dir)
        )
    finally:
        try:
            loop.close()
        except Exception:  # pragma: no cover
            pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def render_slides_to_png(
    db: AsyncSession,
    *,
    course: Course,
    lesson: CourseLesson,
    output_dir: Path,
    public_base_url: str | None = None,
) -> tuple[list[Path], list[str]]:
    """Renderizza tutte le slide della lezione come PNG 1920×1080.

    Args:
        db: sessione per recuperare Organization + SlideTemplate.
        course: corso eager con modules+lessons.
        lesson: lezione target. Deve avere `slides_raw` valorizzato.
        output_dir: directory dove vengono scritti i PNG.
        public_base_url: per resolvere asset relativi del template.

    Returns:
        Tupla `(png_paths, slide_id_order)`:
          - `png_paths`: lista ordinata di Path PNG (slide_001.png, ...).
          - `slide_id_order`: lista parallela di `slide_id` (per il video
            composer che mappa ogni PNG ai segment audio).
    """
    organization = await base_pdf._get_organization(
        db, course.organization_id
    )
    # Slide template: usa quello impostato per la lezione (slides_pdf)
    # se presente, altrimenti default org.
    slide_template: SlideTemplate | None = None
    if lesson.slides_pdf_template_id is not None:
        try:
            slide_template = await slides_pdf._get_slide_template_or_404(
                db,
                organization_id=course.organization_id,
                template_id=lesson.slides_pdf_template_id,
            )
        except Exception:  # pragma: no cover
            slide_template = None
    if slide_template is None:
        slide_template = await slides_pdf._get_default_slide_template(
            db, organization_id=course.organization_id
        )

    slides_raw = lesson.slides_raw or {}
    new_assets = slides_raw.get("new_assets") or []
    mermaid_svg_map = await slides_pdf._prerender_mermaid_for_slides(
        lesson.content_raw, new_assets
    )

    html, slide_id_order = _build_slides_html_for_video(
        course=course,
        lesson=lesson,
        organization=organization,
        slide_template=slide_template,
        public_base_url=public_base_url,
        mermaid_svg_map=mermaid_svg_map,
    )

    png_paths = await asyncio.to_thread(
        _screenshot_slides_sync,
        html,
        len(slide_id_order),
        output_dir,
    )
    log.info(
        "slides_png_rendered",
        lesson_id=str(lesson.id),
        lesson_code=lesson.lesson_code,
        count=len(png_paths),
        output_dir=str(output_dir),
    )
    return png_paths, slide_id_order
