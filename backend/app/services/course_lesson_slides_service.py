"""Servizio orchestrazione per la Fase 4 — Slide della lezione (§7).

Step 2 — questa versione esporta SOLO i helper richiesti dal worker:
- `build_user_prompt` (§7.2 + §9.4 in regenerazione)
- `is_regeneration_for_lesson`
- `materialize_lesson_slides` (validazione §7.4 + persist)
- `_recompute_course_slides_status` (deriva course.status dai
  lesson.slides_status[])
- `load_course_full`, `get_lesson_or_404`

Le funzioni di orchestrazione lato API (`request_lesson_slides_generation`,
`approve_lesson_slides`, ecc.) vivono in Step 3.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.models.course_module import CourseModule
from app.schemas.course_lesson_slides import LessonSlidesOutput

log = get_logger("app.course_lesson_slides")


def _now() -> datetime:
    return datetime.now(tz=UTC)


# Stati lezione da cui è ammesso (ri)generare le slide.
# Le slide possono essere generate solo dopo che il content è almeno
# `ready` (anche se non approvato — lo stale-detection segnalerà se il
# docente edita il content dopo aver generato slide).
VALID_LESSON_SLIDES_GENERATE_FROM_STATUSES = {
    "empty",
    "pending",
    "ready",
    "approved",
    "failed",
}

# Stati a livello corso da cui è ammesso triggerare la Fase 4.
VALID_COURSE_SLIDES_GENERATE_FROM_STATUSES = {
    "content_ready",
    "content_approved",
    "slides_pending",
    "slides_ready",
    "slides_approved",
}


# ---------------------------------------------------------------------------
# Eager loading
# ---------------------------------------------------------------------------


def _eager_full_options() -> list:
    return [
        selectinload(Course.documents),
        selectinload(Course.modules).selectinload(CourseModule.lessons),
        selectinload(Course.categoria),
        selectinload(Course.stile_insegnamento),
        selectinload(Course.profondita_contenuto),
        selectinload(Course.ruolo_docente),
        selectinload(Course.dimensione_pubblico),
        selectinload(Course.livello_conoscenza),
        selectinload(Course.destinatari),
        selectinload(Course.livello_eqf),
        selectinload(Course.assignee),
        selectinload(Course.created_by),
    ]


async def _refresh_full(db: AsyncSession, course: Course) -> Course:
    """Ricarica il corso con eager-load per CourseOut."""
    res = await db.execute(
        select(Course).where(Course.id == course.id).options(*_eager_full_options())
    )
    return res.scalar_one()


# ---------------------------------------------------------------------------
# Prompt building (§7.2 + §9.4 in regenerazione)
# ---------------------------------------------------------------------------


def _format_recommended_bibliography(lesson: CourseLesson) -> str:
    """Bibliografia consigliata della lezione introduttiva (rilevante
    per slide tipo `bibliography`)."""
    if not lesson.is_introductory or not lesson.recommended_bibliography:
        return "(non applicabile)"
    items = lesson.recommended_bibliography or []
    lines: list[str] = []
    for b in items:
        if not isinstance(b, dict):
            continue
        authors = b.get("authors", "")
        title = b.get("title", "")
        publisher = b.get("publisher", "")
        year = b.get("year", "")
        note = b.get("note", "")
        confidence = b.get("confidence", "")
        line = f"- {authors}, *{title}*, {publisher}, {year}."
        if note:
            line += f" — {note}"
        if confidence == "to_verify":
            line += " [DA VERIFICARE]"
        lines.append(line)
    return "\n".join(lines) if lines else "(nessuna voce)"


def _format_current_slides_phase4(lesson: CourseLesson) -> str:
    """Serializza il `slides_raw` corrente per il prompt di rigenerazione."""
    raw = lesson.slides_raw
    if not raw:
        return "(Nessuna versione precedente.)"
    # Compatto ma leggibile: usiamo JSON pretty con cap dimensionale ragionevole.
    try:
        return json.dumps(raw, ensure_ascii=False, indent=2)
    except Exception:
        return "(Versione precedente non serializzabile.)"


def build_user_prompt(course: Course, lesson: CourseLesson) -> str:
    """Costruisce il messaggio utente §7.2 (slide della lezione).

    Pre-condizione: `course` e `lesson` sono stati caricati con eager-load.
    `lesson.content_raw` deve essere popolato (lezione `content_status ∈
    (ready, approved)`).
    """
    minuti = course.lesson_duration_minutes
    lang = course.language_code
    eqf_level = (
        lesson.module is not None
        and getattr(lesson.module, "course", None) is not None
    )  # noqa
    # Per evitare lazy-load: leggiamo `livello_eqf` da course
    eqf_label = (
        getattr(course.livello_eqf, "name", "") if course.livello_eqf else ""
    )

    content_raw_json = (
        json.dumps(lesson.content_raw, ensure_ascii=False, indent=2)
        if lesson.content_raw
        else "(content_raw assente — questa è una situazione anomala)"
    )

    blocks = [
        "## Lezione da slidificare",
        "",
        f"ID: {lesson.lesson_code}",
        f"Titolo: {lesson.title}",
        f"È introduttiva: {str(lesson.is_introductory).lower()}",
        f"Durata della lezione: {minuti} minuti",
        f"Lingua: {lang}",
        f"Livello EQF: {eqf_label}",
        "",
        "## Testo completo della lezione (output di Fase 3)",
        "",
        content_raw_json,
        "",
        "## Bibliografia consigliata (se introduttiva)",
        "",
        _format_recommended_bibliography(lesson),
        "",
        "## Compito",
        "",
        "Genera la sequenza di slide secondo lo schema JSON. Riusa gli",
        "asset di Fase 3 dove possibile. Aggiungi `new_assets` solo se",
        "strettamente necessario.",
    ]

    if lesson.slides_regeneration_hint or lesson.slides_raw:
        blocks.extend(
            [
                "",
                "## Versione attuale delle slide (DA RIVEDERE)",
                "",
                _format_current_slides_phase4(lesson),
            ]
        )
        if lesson.slides_regeneration_hint:
            blocks.extend(
                [
                    "",
                    "## Indicazioni del docente per la rigenerazione",
                    "",
                    lesson.slides_regeneration_hint,
                ]
            )

    return "\n".join(blocks)


def is_regeneration_for_lesson(lesson: CourseLesson) -> bool:
    """True se è una rigenerazione (§9.4): esiste già un slides_raw o un hint."""
    return bool(lesson.slides_raw) or bool(lesson.slides_regeneration_hint)


# ---------------------------------------------------------------------------
# Validazione + materializzazione (§7.4)
# ---------------------------------------------------------------------------


def _expected_slide_range(minutes: int) -> tuple[int, int]:
    """Range atteso (min, max) per durata lezione, con tolleranza ±20%
    rispetto alle linee guida §7.1 punto 3.

    - 30 min → 12-15 → 10-18
    - 45 min → 18-23 → 14-28
    - 60 min → 22-30 → 18-36
    - 90 min → 32-42 → 26-50
    Per durate intermedie/insolite, interpolazione lineare con cap.
    """
    # Tabella di riferimento (durata → (low, high) base).
    # Calcoliamo low/high con interpolazione lineare e tolleranza ±20%.
    if minutes <= 0:
        return (1, 1)
    base_low = max(1, round(minutes * 0.36))   # ~12 slide / 30 min
    base_high = max(2, round(minutes * 0.50))  # ~15 slide / 30 min
    # Tolleranza ±20%
    low = max(1, round(base_low * 0.80))
    high = max(low + 1, round(base_high * 1.20))
    return (low, high)


async def materialize_lesson_slides(
    db: AsyncSession,
    *,
    course: Course,
    lesson: CourseLesson,
    output: LessonSlidesOutput,
    raw: dict[str, Any],
    usage: dict[str, Any],
) -> None:
    """Valida (§7.4) e scrive `slides_raw` + meta sulla lezione.

    NOTA: il caller (worker o sync endpoint) deve avere già caricato
    `course.modules` e `lesson.module` con eager-load. `lesson.content_raw`
    deve essere disponibile (Fase 3 deve essere ready/approved).
    """
    # 1. Match lesson_id ↔ lesson_code
    if output.lesson_id != lesson.lesson_code:
        raise ConflictError(
            f"L'AI ha prodotto lesson_id `{output.lesson_id}`, "
            f"atteso `{lesson.lesson_code}`.",
            code="lesson_slides_id_mismatch",
        )

    # 2. total_slides == len(slides)
    if output.total_slides != len(output.slides):
        raise ConflictError(
            f"total_slides={output.total_slides} non corrisponde a "
            f"len(slides)={len(output.slides)}.",
            code="lesson_slides_total_mismatch",
        )

    # 3. slide_number univoci e sequenziali 1..N
    nums = [s.slide_number for s in output.slides]
    if sorted(nums) != list(range(1, len(nums) + 1)):
        raise ConflictError(
            f"slide_number non sequenziali 1..N (trovati: {sorted(nums)}).",
            code="lesson_slides_nonsequential",
        )

    # 4. slide_id univoci
    slide_ids = [s.slide_id for s in output.slides]
    if len(set(slide_ids)) != len(slide_ids):
        raise ConflictError(
            "slide_id duplicati nella lezione.",
            code="lesson_slides_duplicate_slide_id",
        )

    # 5. total_slides nel range atteso per minuti_per_lezione
    low, high = _expected_slide_range(course.lesson_duration_minutes)
    if not (low <= output.total_slides <= high):
        log.warning(
            "lesson_slides_out_of_range",
            lesson_code=lesson.lesson_code,
            total_slides=output.total_slides,
            expected_low=low,
            expected_high=high,
            minutes=course.lesson_duration_minutes,
        )
        # Soft warning, non bloccante (con tolleranza già applicata, hard
        # fail solo se molto fuori range).
        if output.total_slides < max(1, low // 2) or output.total_slides > high * 2:
            raise ConflictError(
                f"total_slides={output.total_slides} fuori range atteso "
                f"({low}-{high}) per {course.lesson_duration_minutes} min.",
                code="lesson_slides_count_out_of_range",
            )

    # 6. references_assets risolvibili (in content_raw OR in new_assets)
    valid_asset_ids: set[str] = set()
    content_raw = lesson.content_raw or {}
    for key in ("visual_assets", "tables", "equations", "examples"):
        for a in content_raw.get(key, []) or []:
            if isinstance(a, dict):
                # Asset_id può essere asset_id, table_id, equation_id, example_id.
                for id_key in ("asset_id", "table_id", "equation_id", "example_id"):
                    if id_key in a and a[id_key]:
                        valid_asset_ids.add(str(a[id_key]))
    for na in output.new_assets:
        valid_asset_ids.add(na.asset_id)

    # asset_id in new_assets devono essere univoci
    new_asset_ids = [na.asset_id for na in output.new_assets]
    if len(set(new_asset_ids)) != len(new_asset_ids):
        raise ConflictError(
            "asset_id duplicati in new_assets.",
            code="lesson_slides_duplicate_new_asset_id",
        )

    for s in output.slides:
        for aid in s.references_assets:
            if aid not in valid_asset_ids:
                raise ConflictError(
                    f"Slide {s.slide_id}: references_assets contiene "
                    f"`{aid}` non presente in Fase 3 né in new_assets.",
                    code="lesson_slides_unknown_asset_ref",
                )

    # 7. source_section_id (se non vuoto) deve referenziare una sezione
    valid_section_ids = {
        s.get("section_id")
        for s in (content_raw.get("sections") or [])
        if isinstance(s, dict) and s.get("section_id")
    }
    for s in output.slides:
        if s.source_section_id and s.source_section_id not in valid_section_ids:
            raise ConflictError(
                f"Slide {s.slide_id}: source_section_id `{s.source_section_id}` "
                f"non esiste nelle sezioni di Fase 3.",
                code="lesson_slides_unknown_source_section",
            )

    # 8. Ogni section deve essere referenziata da almeno una slide (soft)
    referenced_sections = {
        s.source_section_id for s in output.slides if s.source_section_id
    }
    unreferenced = valid_section_ids - referenced_sections
    if unreferenced:
        log.warning(
            "lesson_slides_unreferenced_sections",
            lesson_code=lesson.lesson_code,
            unreferenced_sections=sorted(unreferenced),
        )

    # 9. Apply — scrive slides_raw + meta
    lesson.slides_raw = raw
    lesson.slides_tokens = usage
    lesson.slides_status = "ready"
    lesson.slides_generated_at = _now()
    lesson.slides_error = None
    lesson.slides_progress = 100
    lesson.slides_progress_phase = None

    # 10. Side-effect course-level
    _recompute_course_slides_status(course)


# ---------------------------------------------------------------------------
# Course-level status derivation
# ---------------------------------------------------------------------------


def _recompute_course_slides_status(course: Course) -> None:
    """Aggiorna `course.status` in base agli stati slide delle lezioni.

    Regole (mirror del content):
    - almeno 1 lezione in `pending|processing|failed` → `slides_pending`
    - TUTTE in `approved` → `slides_approved`
    - TUTTE in `ready|approved` (con almeno 1 ready) → `slides_ready`
    - se nessuna lezione è in stato Fase 4 (tutte `empty`) → invariato.
    """
    statuses = [
        lesson.slides_status
        for m in course.modules
        for lesson in m.lessons
    ]
    if not statuses:
        return

    if any(s in ("pending", "processing", "failed") for s in statuses):
        course.status = "slides_pending"
        return

    if all(s == "approved" for s in statuses):
        course.status = "slides_approved"
        return

    if all(s in ("ready", "approved") for s in statuses) and any(
        s == "ready" for s in statuses
    ):
        course.status = "slides_ready"
        return


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


async def load_course_full(
    db: AsyncSession, *, course_id: uuid.UUID
) -> Course | None:
    res = await db.execute(
        select(Course)
        .where(Course.id == course_id)
        .options(*_eager_full_options())
    )
    return res.scalar_one_or_none()


async def get_lesson_or_404(
    db: AsyncSession, *, course: Course, lesson_id: uuid.UUID
) -> CourseLesson:
    for m in course.modules:
        for lesson in m.lessons:
            if lesson.id == lesson_id:
                return lesson
    raise NotFoundError(
        f"Lezione {lesson_id} non trovata nel corso.",
        code="lesson_not_found",
    )
