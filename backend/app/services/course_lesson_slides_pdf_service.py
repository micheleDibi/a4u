"""Service di export PDF delle SLIDE (Fase 4 §7).

Pipeline:
  slides_raw + content_raw + pdf_template
        ↓ Playwright (pre-render mermaid → SVG inline)
        ↓ latex2mathml → MathML inline (math)
        ↓ Jinja2 (template `lesson_slides_pdf.html.j2`)
        ↓ WeasyPrint → PDF bytes
        ↓ filesystem (`generated_pdfs/{org}/{course}/{lesson}_slides.pdf`)

Lo stato è scoped a livello LEZIONE (`course_lesson.slides_pdf_status`)
e distinto dal `pdf_status` della lezione testo.

Riusa massivamente gli helper di `course_lesson_pdf_service` per evitare
duplicazione: caricamento template, pre-render mermaid, generazione PDF
bytes, audit pattern. Cambia la funzione di rendering HTML che usa il
template dedicato per layout slide (A4 landscape, una slide per pagina).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.errors import ConflictError
from app.core.logging import get_logger
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.models.organization import Organization
from app.models.pdf_template import PdfTemplate
from app.services import course_lesson_pdf_service as base_pdf
from app.services import course_lesson_slides_service

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


def slides_pdf_filename_for_download(
    course_title: str, lesson: CourseLesson
) -> str:
    """Nome file user-friendly (versione slide della
    `pdf_filename_for_download`)."""
    import re

    safe_lesson = re.sub(r"[^\w\-. ]+", "_", lesson.title)[:80].strip("_ ")
    safe_course = re.sub(r"[^\w\-. ]+", "_", course_title)[:60].strip("_ ")
    return f"{safe_course} — {lesson.lesson_code} {safe_lesson} (slide).pdf"


# ---------------------------------------------------------------------------
# Jinja env (template dedicato slide)
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


# ---------------------------------------------------------------------------
# Asset rendering per slide
# ---------------------------------------------------------------------------


def _labels_for(language: str) -> dict[str, str]:
    if (language or "it").lower().startswith("en"):
        return {"speaker_hint": "Speaker notes"}
    return {"speaker_hint": "Note"}


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


def _build_slide_asset_html(
    asset: dict[str, Any],
    *,
    kind: str,
    mermaid_svg_map: dict[str, str],
) -> str:
    """Costruisce il blocco HTML per un asset referenziato da una slide.

    Riusa le helper di `course_lesson_pdf_service` per i diversi tipi.
    """
    if kind == "visual" or kind == "new_visual":
        # Asset visuale (mermaid o image_*).
        return base_pdf._render_visual_asset_block(asset, mermaid_svg_map)
    if kind == "table":
        return base_pdf._render_table_block(asset)
    if kind == "equation":
        return base_pdf._render_equation_block(asset)
    if kind == "example":
        return base_pdf._render_example_block(asset)
    return ""


def _resolve_asset_for_slide(
    asset_id: str,
    content_raw: dict[str, Any] | None,
    new_assets: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]] | None:
    """Cerca asset_id in content_raw + new_assets. Ritorna (kind, payload)
    o None.

    Mirror della logica frontend (`lib/slides.resolveAsset`).
    """
    if content_raw:
        for a in content_raw.get("visual_assets") or []:
            if isinstance(a, dict) and a.get("asset_id") == asset_id:
                return "visual", a
        for t in content_raw.get("tables") or []:
            if isinstance(t, dict) and t.get("table_id") == asset_id:
                return "table", t
        for e in content_raw.get("equations") or []:
            if isinstance(e, dict) and e.get("equation_id") == asset_id:
                return "equation", e
        for ex in content_raw.get("examples") or []:
            if isinstance(ex, dict) and ex.get("example_id") == asset_id:
                return "example", ex
    for na in new_assets or []:
        if isinstance(na, dict) and na.get("asset_id") == asset_id:
            return "new_visual", na
    return None


# ---------------------------------------------------------------------------
# Mermaid pre-render (riusa helper base con dati merged)
# ---------------------------------------------------------------------------


async def _prerender_mermaid_for_slides(
    content_raw: dict[str, Any] | None,
    new_assets: list[dict[str, Any]],
) -> dict[str, str]:
    """Pre-renderizza tutti i diagrammi mermaid (Fase 3 + new_assets) in
    una singola sessione Playwright. Ritorna {asset_id: svg_str}.
    """
    # Costruisce un dict-like content con tutti gli asset visivi mermaid:
    # base_pdf._prerender_mermaid_for_lesson legge `visual_assets`.
    merged_visual_assets: list[dict[str, Any]] = []
    if content_raw:
        for a in content_raw.get("visual_assets") or []:
            if isinstance(a, dict):
                merged_visual_assets.append(a)
    for na in new_assets or []:
        if isinstance(na, dict):
            merged_visual_assets.append(na)
    return await base_pdf._prerender_mermaid_for_lesson(
        {"visual_assets": merged_visual_assets}
    )


# ---------------------------------------------------------------------------
# render_slides_html
# ---------------------------------------------------------------------------


def render_slides_html(
    *,
    course: Course,
    lesson: CourseLesson,
    organization: Organization | None,
    pdf_template: PdfTemplate | None,
    public_base_url: str | None = None,
    mermaid_svg_map: dict[str, str] | None = None,
) -> str:
    """Pure-function: HTML completo delle slide pronto per WeasyPrint.

    Reuses base PDF helpers per template formatting + asset rendering.
    """
    slides_raw = lesson.slides_raw or {}
    if not slides_raw:
        raise ConflictError(
            f"Lezione {lesson.lesson_code} senza slides_raw — "
            f"impossibile esportare.",
            code="lesson_slides_missing",
        )
    content_raw = lesson.content_raw or {}
    new_assets = slides_raw.get("new_assets") or []

    language = (course.language_code or "it").lower()
    labels = _labels_for(language)

    if pdf_template is not None:
        tpl_dict = base_pdf._format_pdf_template_for_render(
            pdf_template, public_base_url=public_base_url
        )
    else:
        tpl_dict = base_pdf._default_template_dict(language=language)

    mmap = mermaid_svg_map or {}
    rendered_slides: list[dict[str, Any]] = []
    for s in slides_raw.get("slides") or []:
        if not isinstance(s, dict):
            continue
        slide_type = s.get("type", "concept")
        # Asset HTML embeds.
        assets_html: list[str] = []
        for aid in s.get("references_assets") or []:
            resolved = _resolve_asset_for_slide(
                aid, content_raw, new_assets
            )
            if resolved is None:
                # Asset non trovato: skip silenzioso (la validazione lo
                # avrebbe già impedito al materialize).
                continue
            kind, payload = resolved
            html = _build_slide_asset_html(
                payload, kind=kind, mermaid_svg_map=mmap
            )
            if html:
                assets_html.append(html)
        rendered_slides.append(
            {
                "slide_number": s.get("slide_number"),
                "slide_id": s.get("slide_id"),
                "type": slide_type,
                "type_label": _slide_type_label(language, slide_type),
                "title": s.get("title", ""),
                "bullets": s.get("bullets") or [],
                "speaker_hint": s.get("speaker_hint", ""),
                "assets_html": assets_html,
            }
        )

    template = _jinja_env.get_template("lesson_slides_pdf.html.j2")
    html = template.render(
        language=language,
        labels=labels,
        course={"title": course.title, "language": language},
        lesson={"title": lesson.title, "lesson_code": lesson.lesson_code},
        organization_name=(organization.name if organization else None),
        tpl=tpl_dict,
        slides=rendered_slides,
        total_slides=len(rendered_slides),
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

    # Risolvi template: se la lezione ha `slides_pdf_template_id`, usalo;
    # altrimenti default org. Non ricicla il `pdf_template_id` del PDF
    # lezione testo (sono concettualmente distinti).
    pdf_template: PdfTemplate | None = None
    if lesson.slides_pdf_template_id is not None:
        pdf_template = await base_pdf._get_pdf_template_or_404(
            db,
            organization_id=course.organization_id,
            template_id=lesson.slides_pdf_template_id,
        )
    else:
        pdf_template = await base_pdf._get_default_pdf_template(
            db, organization_id=course.organization_id
        )

    slides_raw = lesson.slides_raw or {}
    new_assets = slides_raw.get("new_assets") or []
    mermaid_svg_map = await _prerender_mermaid_for_slides(
        lesson.content_raw, new_assets
    )

    html = render_slides_html(
        course=course,
        lesson=lesson,
        organization=organization,
        pdf_template=pdf_template,
        public_base_url=public_base_url,
        mermaid_svg_map=mermaid_svg_map,
    )

    pdf_bytes = await base_pdf.generate_pdf_bytes(html=html)

    rel = slides_pdf_relative_path(
        organization_id=course.organization_id,
        course_id=course.id,
        lesson_id=lesson.id,
    )
    abs_path = base_pdf.pdf_absolute_path(rel)
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(pdf_bytes)

    lesson.slides_pdf_path = rel
    lesson.slides_pdf_template_id = pdf_template.id if pdf_template else None
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
            f"Export PDF slide già in corso per {lesson.lesson_code}: "
            f"{lesson.slides_pdf_status}",
            code="slides_pdf_already_in_progress",
        )

    if pdf_template_id is not None:
        await base_pdf._get_pdf_template_or_404(
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
            "pdf_template_id": (
                str(pdf_template_id) if pdf_template_id else None
            ),
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
) -> Course:
    """Marca tutte le lezioni esportabili come slides_pdf_status='pending'."""
    eligible: list[CourseLesson] = []
    for module in course.modules:
        for lesson in module.lessons:
            if (
                lesson.slides_status in EXPORTABLE_SLIDES_STATUSES
                and lesson.slides_pdf_status in VALID_SLIDES_PDF_REQUEST_STATUSES
            ):
                eligible.append(lesson)
    if not eligible:
        raise ConflictError(
            "Nessuna lezione esportabile (servono slide ready/approved).",
            code="no_eligible_lessons_for_slides_pdf",
        )

    if pdf_template_id is not None:
        await base_pdf._get_pdf_template_or_404(
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
            "pdf_template_id": (
                str(pdf_template_id) if pdf_template_id else None
            ),
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
                "cancelled_lesson_codes": [l.lesson_code for l in affected],
            },
        )
    await db.commit()
    return await course_lesson_slides_service._refresh_full(db, course)
