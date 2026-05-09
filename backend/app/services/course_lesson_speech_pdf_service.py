"""Service di export PDF del DISCORSO temporizzato (Fase 5 §8).

Pipeline:
  speech_raw + slides_raw + pdf_template
        ↓ Jinja2 (template `lesson_speech_pdf.html.j2`)
        ↓ WeasyPrint → PDF bytes
        ↓ filesystem (`generated_pdfs/{org}/{course}/{lesson}_speech.pdf`)

Lo stato è scoped a livello LEZIONE (`course_lesson.speech_pdf_status`)
e distinto dal `pdf_status` (lezione testo) e dal `slides_pdf_status`
(slide). Il discorso è prosa pura — NIENTE Mermaid pre-render, niente
asset rendering: una sezione per ciascuna slide con timeline cumulativa
e testo dei segmenti.

FK al `pdf_templates` (kind=lesson, stesso del PDF lezione testo): A4
portrait, single-column block-flow, perfetto per prosa.

Riusa massivamente gli helper di `course_lesson_pdf_service` per:
- `_get_default_pdf_template` / `_get_pdf_template_or_404`
- `_format_pdf_template_for_render` / `_default_template_dict`
- `_compute_template_margins_cm`
- `generate_pdf_bytes` / `pdf_absolute_path` / `_get_organization`
"""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.errors import ConflictError
from app.core.logging import get_logger
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.models.organization import Organization
from app.models.pdf_template import PdfTemplate
from app.services import course_lesson_pdf_service as base_pdf
from app.services import course_lesson_speech_service

log = get_logger("app.course_lesson_speech_pdf.service")


EXPORTABLE_SPEECH_STATUSES: tuple[str, ...] = ("ready", "approved")
VALID_SPEECH_PDF_REQUEST_STATUSES: tuple[str, ...] = ("empty", "ready", "failed")


# ---------------------------------------------------------------------------
# Filesystem
# ---------------------------------------------------------------------------


def speech_pdf_relative_path(
    *,
    organization_id: uuid.UUID,
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
) -> str:
    """Path relativo al `generated_pdfs_dir`. Suffisso `_speech` per
    distinguerlo dal PDF lezione testo e dal PDF slide."""
    return f"{organization_id}/{course_id}/{lesson_id}_speech.pdf"


def speech_pdf_filename_for_download(
    course_title: str, lesson: CourseLesson
) -> str:
    """Nome file user-friendly."""
    safe_lesson = re.sub(r"[^\w\-. ]+", "_", lesson.title)[:80].strip("_ ")
    safe_course = re.sub(r"[^\w\-. ]+", "_", course_title)[:60].strip("_ ")
    return (
        f"{safe_course} — {lesson.lesson_code} {safe_lesson} (discorso).pdf"
    )


# ---------------------------------------------------------------------------
# Jinja env (template dedicato discorso)
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=False,
    lstrip_blocks=False,
)


# ---------------------------------------------------------------------------
# Timeline helper
# ---------------------------------------------------------------------------


def _format_mmss(seconds: int) -> str:
    """Format `seconds` come `mm:ss`."""
    total = max(0, int(seconds))
    mm = total // 60
    ss = total % 60
    return f"{mm:02d}:{ss:02d}"


def format_timeline(
    slide_to_segments_map: list[dict[str, Any]],
    seg_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Calcola la timeline cumulativa per ciascuna slide.

    Ritorna una lista di entries pronte per il template Jinja, ciascuna
    contenente:
        slide_id, segments[{
            segment_id, text, delivery_notes,
            duration_seconds, duration_label (`Ns`),
            start_mmss, end_mmss
        }],
        slide_total_duration_seconds, slide_total_label
    """
    out: list[dict[str, Any]] = []
    cumulative = 0
    for entry in slide_to_segments_map:
        if not isinstance(entry, dict):
            continue
        slide_id = entry.get("slide_id")
        seg_ids = entry.get("segment_ids") or []
        slide_total = int(entry.get("slide_total_duration_seconds") or 0)
        rendered_segs: list[dict[str, Any]] = []
        for sid in seg_ids:
            seg = seg_by_id.get(sid)
            if seg is None:
                continue
            duration = int(seg.get("estimated_duration_seconds") or 0)
            start = cumulative
            end = cumulative + duration
            cumulative = end
            rendered_segs.append(
                {
                    "segment_id": sid,
                    "text": seg.get("text", ""),
                    "delivery_notes": seg.get("delivery_notes", ""),
                    "duration_seconds": duration,
                    "duration_label": f"{duration}s",
                    "start_mmss": _format_mmss(start),
                    "end_mmss": _format_mmss(end),
                }
            )
        out.append(
            {
                "slide_id": slide_id,
                "segments": rendered_segs,
                "slide_total_duration_seconds": slide_total,
                "slide_total_label": _format_mmss(slide_total),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Render HTML
# ---------------------------------------------------------------------------


def _labels_for(language: str) -> dict[str, str]:
    if (language or "it").lower().startswith("en"):
        return {
            "speech": "Speech",
            "lesson": "Lesson",
            "totalDuration": "Total duration",
            "totalWordCount": "Total word count",
            "deliveryNotes": "Delivery notes",
            "slidePrefix": "Slide",
            "slideMissing": "Slide not found",
            "noSegments": "(No segment)",
        }
    return {
        "speech": "Discorso",
        "lesson": "Lezione",
        "totalDuration": "Durata totale",
        "totalWordCount": "Conteggio parole",
        "deliveryNotes": "Note al docente",
        "slidePrefix": "Slide",
        "slideMissing": "Slide non trovata",
        "noSegments": "(Nessun segmento)",
    }


def render_speech_html(
    *,
    course: Course,
    lesson: CourseLesson,
    organization: Organization | None,
    pdf_template: PdfTemplate | None,
    public_base_url: str | None = None,
) -> str:
    """Pure-function: HTML completo del discorso pronto per WeasyPrint."""
    speech_raw = lesson.speech_raw or {}
    if not speech_raw:
        raise ConflictError(
            f"Lezione {lesson.lesson_code} senza speech_raw — "
            f"impossibile esportare.",
            code="lesson_speech_missing",
        )
    slides_raw = lesson.slides_raw or {}

    language = (course.language_code or "it").lower()
    labels = _labels_for(language)

    if pdf_template is not None:
        tpl_dict = base_pdf._format_pdf_template_for_render(
            pdf_template, public_base_url=public_base_url
        )
    else:
        tpl_dict = base_pdf._default_template_dict(language=language)

    margins_cm = base_pdf._compute_template_margins_cm(tpl_dict)

    # Index slide per titolo + numero
    slide_meta_by_id: dict[str, dict[str, Any]] = {}
    for s in slides_raw.get("slides") or []:
        if isinstance(s, dict) and s.get("slide_id"):
            slide_meta_by_id[str(s["slide_id"])] = {
                "slide_number": s.get("slide_number"),
                "title": s.get("title", ""),
            }

    seg_by_id: dict[str, dict[str, Any]] = {
        s["segment_id"]: s
        for s in (speech_raw.get("speech_segments") or [])
        if isinstance(s, dict) and s.get("segment_id")
    }

    timeline = format_timeline(
        speech_raw.get("slide_to_segments_map") or [], seg_by_id
    )

    # Iniezione metadati slide nel timeline (titoli, numero) per
    # l'header di ciascuna sezione.
    for entry in timeline:
        meta = slide_meta_by_id.get(entry["slide_id"]) or {}
        entry["slide_number"] = meta.get("slide_number")
        entry["slide_title"] = meta.get("title") or ""
        entry["slide_missing"] = entry["slide_id"] not in slide_meta_by_id

    # Etichetta "Lezione N" derivata dal lesson_code (mirror del PDF slide).
    lesson_word = labels["lesson"]
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

    total_duration_seconds = int(
        speech_raw.get("estimated_total_duration_seconds") or 0
    )
    total_word_count = int(
        speech_raw.get("estimated_total_word_count") or 0
    )

    template = _jinja_env.get_template("lesson_speech_pdf.html.j2")
    html = template.render(
        language=language,
        labels=labels,
        course={"title": course.title, "language": language},
        lesson={"title": lesson.title, "lesson_code": lesson.lesson_code},
        lesson_label=lesson_label,
        organization_name=(organization.name if organization else None),
        tpl=tpl_dict,
        margin_top_cm=margins_cm["margin_top_cm"],
        margin_side_cm=margins_cm["margin_side_cm"],
        margin_bottom_cm=margins_cm["margin_bottom_cm"],
        timeline=timeline,
        total_duration_label=_format_mmss(total_duration_seconds),
        total_word_count=total_word_count,
    )
    return html


# ---------------------------------------------------------------------------
# Materializzazione (chiamato dal worker)
# ---------------------------------------------------------------------------


async def materialize_lesson_speech_pdf(
    db: AsyncSession,
    *,
    course: Course,
    lesson: CourseLesson,
    public_base_url: str | None = None,
) -> str:
    """Genera e salva su disco il PDF del discorso. Aggiorna i campi DB.
    Restituisce il path relativo persistito."""
    organization = await base_pdf._get_organization(db, course.organization_id)

    pdf_template: PdfTemplate | None = None
    if lesson.speech_pdf_template_id is not None:
        try:
            pdf_template = await base_pdf._get_pdf_template_or_404(
                db,
                organization_id=course.organization_id,
                template_id=lesson.speech_pdf_template_id,
            )
        except Exception:
            log.warning(
                "lesson_speech_pdf_requested_template_missing",
                lesson_id=str(lesson.id),
                requested_template_id=str(lesson.speech_pdf_template_id),
            )
            pdf_template = await base_pdf._get_default_pdf_template(
                db, organization_id=course.organization_id
            )
    else:
        pdf_template = await base_pdf._get_default_pdf_template(
            db, organization_id=course.organization_id
        )

    html = render_speech_html(
        course=course,
        lesson=lesson,
        organization=organization,
        pdf_template=pdf_template,
        public_base_url=public_base_url,
    )

    pdf_bytes = await base_pdf.generate_pdf_bytes(html=html)

    rel = speech_pdf_relative_path(
        organization_id=course.organization_id,
        course_id=course.id,
        lesson_id=lesson.id,
    )
    abs_path = base_pdf.pdf_absolute_path(rel)
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(pdf_bytes)

    lesson.speech_pdf_path = rel
    lesson.speech_pdf_template_id = (
        pdf_template.id if pdf_template else None
    )
    lesson.speech_pdf_generated_at = datetime.now(UTC)
    return rel


# ---------------------------------------------------------------------------
# Public API: enqueue + cancel
# ---------------------------------------------------------------------------


async def request_lesson_speech_pdf(
    db: AsyncSession,
    *,
    course: Course,
    lesson: CourseLesson,
    actor_id: uuid.UUID,
    pdf_template_id: uuid.UUID | None = None,
) -> Course:
    """Sposta `speech_pdf_status → pending`. Vincoli:
    - `lesson.speech_status` ∈ ready/approved
    - `lesson.speech_pdf_status` ∈ empty/ready/failed
    """
    if lesson.speech_status not in EXPORTABLE_SPEECH_STATUSES:
        raise ConflictError(
            f"Lezione {lesson.lesson_code}: discorso non `ready`/`approved` "
            f"(attuale: {lesson.speech_status}).",
            code="invalid_lesson_speech_status_for_pdf",
        )
    if lesson.speech_pdf_status not in VALID_SPEECH_PDF_REQUEST_STATUSES:
        raise ConflictError(
            f"Export PDF discorso già in corso per {lesson.lesson_code}: "
            f"{lesson.speech_pdf_status}",
            code="speech_pdf_already_in_progress",
        )

    if pdf_template_id is not None:
        await base_pdf._get_pdf_template_or_404(
            db,
            organization_id=course.organization_id,
            template_id=pdf_template_id,
        )
        lesson.speech_pdf_template_id = pdf_template_id

    lesson.speech_pdf_status = "pending"
    lesson.speech_pdf_error = None
    lesson.speech_pdf_progress = 0
    lesson.speech_pdf_progress_phase = None

    await write_audit(
        db,
        action="course.lesson.speech_pdf.requested",
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
    return await course_lesson_speech_service._refresh_full(db, course)


async def request_all_lessons_speech_pdf(
    db: AsyncSession,
    *,
    course: Course,
    actor_id: uuid.UUID,
    pdf_template_id: uuid.UUID | None = None,
) -> Course:
    """Marca tutte le lezioni esportabili come speech_pdf_status='pending'."""
    eligible: list[CourseLesson] = []
    for module in course.modules:
        for lesson in module.lessons:
            if (
                lesson.speech_status in EXPORTABLE_SPEECH_STATUSES
                and lesson.speech_pdf_status in VALID_SPEECH_PDF_REQUEST_STATUSES
            ):
                eligible.append(lesson)
    if not eligible:
        raise ConflictError(
            "Nessuna lezione esportabile (servono discorsi ready/approved).",
            code="no_eligible_lessons_for_speech_pdf",
        )

    if pdf_template_id is not None:
        await base_pdf._get_pdf_template_or_404(
            db,
            organization_id=course.organization_id,
            template_id=pdf_template_id,
        )

    for lesson in eligible:
        if pdf_template_id is not None:
            lesson.speech_pdf_template_id = pdf_template_id
        lesson.speech_pdf_status = "pending"
        lesson.speech_pdf_error = None
        lesson.speech_pdf_progress = 0
        lesson.speech_pdf_progress_phase = None

    await write_audit(
        db,
        action="course.lesson.speech_pdf.requested_all",
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
    return await course_lesson_speech_service._refresh_full(db, course)


async def cancel_all_speech_pdf_exports(
    db: AsyncSession,
    *,
    course: Course,
    actor_id: uuid.UUID,
) -> Course:
    """Annulla export speech PDF in flight."""
    affected: list[CourseLesson] = []
    for module in course.modules:
        for lesson in module.lessons:
            if lesson.speech_pdf_status in ("pending", "processing"):
                lesson.speech_pdf_status = "failed"
                lesson.speech_pdf_error = "Export annullato"
                lesson.speech_pdf_progress = 0
                lesson.speech_pdf_progress_phase = None
                affected.append(lesson)

    if affected:
        await write_audit(
            db,
            action="course.lesson.speech_pdf.cancelled",
            actor_user_id=actor_id,
            organization_id=course.organization_id,
            target_type="course",
            target_id=str(course.id),
            metadata={
                "cancelled_lesson_codes": [l.lesson_code for l in affected],
            },
        )
    await db.commit()
    return await course_lesson_speech_service._refresh_full(db, course)
