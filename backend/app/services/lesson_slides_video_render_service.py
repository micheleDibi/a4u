"""Render Playwright delle slide per il video MP4 — usa IDENTICAMENTE
il template PDF (`lesson_slides_pdf.html.j2`).

Sostituisce il vecchio `lesson_slides_png_service.py` che usava un
template custom (`lesson_slides_video.html.j2`) — eliminato perché non
supportava asset Mermaid / equazioni LaTeX / immagini caricate e
divergeva dal layout PDF approvato dall'utente.

Approccio:
1. Pre-render Mermaid → SVG via `slides_pdf._prerender_mermaid_for_slides`
   (stessa logica del PDF: Playwright headless con mermaid.esm pinned).
2. Genera HTML completo via `slides_pdf.render_slides_html(..., enable_split=False)`
   — ottiene esattamente l'HTML che produrrebbe il PDF (con tutti gli
   asset risolti, MathML inline, ecc.), senza lo split bullet/asset.
3. Apre Playwright con viewport **1920×1080**, carica l'HTML, e per
   ciascun `.slide` (A4 landscape ~1123×794 px @96dpi) prende uno
   screenshot dell'elemento. Il browser scala/centra naturalmente la
   slide nel frame 16:9 → bordi bianchi laterali simmetrici.

Output: lista ordinata di PNG 1:1 con `slides_raw.slides[].slide_id`
(niente split → niente complessità di mapping audio↔frame).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.models.slide_template import SlideTemplate
from app.services import course_lesson_pdf_service as base_pdf
from app.services import course_lesson_slides_pdf_service as slides_pdf

log = get_logger("app.lesson_slides_video_render")


# Mini-CSS injected dentro <head> dell'HTML PDF: rimuove il page-break
# che WeasyPrint userebbe per separare le slide e forza il body a
# scrollare verticalmente con una slide sotto l'altra. Playwright così
# vede tutti gli elementi `.slide` e può screenshottarli uno per uno.
_VIDEO_OVERRIDE_CSS = """
<style>
  html, body { background: #ffffff; }
  @page { margin: 0; }
  .slide {
    page-break-after: auto !important;
    break-after: auto !important;
    margin: 0 0 16px 0;
  }
</style>
"""


async def _screenshot_slides_async(
    html: str, output_dir: Path
) -> list[Path]:
    """Playwright headless: viewport 1920×1080, screenshot per ogni .slide.

    Le slide nel template PDF sono `position: relative; width: 297mm;
    height: 210mm` → a 96dpi sono ~1123×794 px. Dentro viewport 1920×1080
    risultano centrate orizzontalmente con bordi bianchi laterali
    (~398px per lato) e padding verticale automatico (~143px).
    Aspect mantenuto, contenuto identico al PDF.
    """
    from playwright.async_api import async_playwright  # type: ignore

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
            try:
                # Aspetta che i font del template (Inter, ecc.) siano
                # caricati prima dello screenshot.
                await page.evaluate(
                    "document.fonts && document.fonts.ready"
                )
            except Exception:  # pragma: no cover
                pass

            slides = await page.query_selector_all(".slide")
            log.info("video_render_slides_found", count=len(slides))

            # Per ogni slide: setto il body a contenere solo questa
            # slide, così Playwright la centra nel viewport e la
            # screenshotta full-frame 1920×1080 con padding bianco.
            for i, slide_handle in enumerate(slides):
                # Nascondi tutte le altre slide via display:none.
                await page.evaluate(
                    """(idx) => {
                        const all = document.querySelectorAll('.slide');
                        all.forEach((el, j) => {
                            el.style.display = (j === idx) ? '' : 'none';
                        });
                    }""",
                    i,
                )
                # Aspetto un repaint.
                await page.wait_for_timeout(50)
                png_path = output_dir / f"slide_{i + 1:03d}.png"
                # Screenshot del viewport pieno: slide centrata + bordi
                # bianchi naturali.
                await page.screenshot(
                    path=str(png_path),
                    type="png",
                    omit_background=False,
                    full_page=False,
                )
                png_paths.append(png_path)
        finally:
            await browser.close()

    return png_paths


def _screenshot_slides_sync(html: str, output_dir: Path) -> list[Path]:
    """Wrapper sync: loop dedicato (ProactorEventLoop su Win per supporto
    `subprocess_exec` richiesto da Playwright). Eseguito via
    `asyncio.to_thread` dal worker async."""
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(
            _screenshot_slides_async(html, output_dir)
        )
    finally:
        try:
            loop.close()
        except Exception:  # pragma: no cover
            pass


async def render_slides_to_png(
    db: AsyncSession,
    *,
    course: Course,
    lesson: CourseLesson,
    output_dir: Path,
    public_base_url: str | None = None,
) -> tuple[list[Path], list[str]]:
    """Renderizza le slide della lezione come PNG 1920×1080, una per slide.

    Riusa al 100% la pipeline del PDF:
    - `_prerender_mermaid_for_slides` per Mermaid SVG inline
    - `render_slides_html(enable_split=False)` per HTML identico al PDF
    - Asset Mermaid, equazioni LaTeX→MathML, immagini caricate: tutti
      renderizzati nello stesso modo del PDF approvato dall'utente

    Returns:
        Tupla `(png_paths, slide_id_order)` parallela:
        - `png_paths`: PNG 1920×1080 in ordine di apparizione DOM
        - `slide_id_order`: `slide_id` corrispondente (1:1 con
          `slides_raw.slides[]`, niente split)
    """
    organization = await base_pdf._get_organization(
        db, course.organization_id
    )
    # Slide template: stesso default org del PDF slides.
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

    html_pdf = slides_pdf.render_slides_html(
        course=course,
        lesson=lesson,
        organization=organization,
        slide_template=slide_template,
        public_base_url=public_base_url,
        mermaid_svg_map=mermaid_svg_map,
        enable_split=False,  # 1 slide JSON → 1 frame video
    )

    # Inject override CSS dopo `<head>` per neutralizzare i page-break
    # PDF-specifici e garantire sfondo bianco esplicito.
    html_video = html_pdf.replace(
        "</head>", _VIDEO_OVERRIDE_CSS + "</head>", 1
    )

    # Costruisce la lista degli slide_id nell'ordine del JSON
    # (1:1 con i `.slide` del DOM dato enable_split=False).
    slide_id_order: list[str] = []
    for s in slides_raw.get("slides") or []:
        if isinstance(s, dict) and s.get("slide_id"):
            slide_id_order.append(str(s["slide_id"]))

    png_paths = await asyncio.to_thread(
        _screenshot_slides_sync, html_video, output_dir
    )

    if len(png_paths) != len(slide_id_order):
        log.warning(
            "video_render_count_mismatch",
            png_count=len(png_paths),
            slide_id_count=len(slide_id_order),
            lesson_id=str(lesson.id),
        )
        # Tronca al minimo per evitare IndexError downstream.
        n = min(len(png_paths), len(slide_id_order))
        png_paths = png_paths[:n]
        slide_id_order = slide_id_order[:n]

    log.info(
        "video_render_slides_done",
        lesson_id=str(lesson.id),
        lesson_code=lesson.lesson_code,
        count=len(png_paths),
        output_dir=str(output_dir),
    )
    return png_paths, slide_id_order
