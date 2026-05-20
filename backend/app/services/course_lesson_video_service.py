"""Public API + helper per la generazione video lezione (§9).

Espone:
- `request_lesson_video` / `request_all_lessons_video` — enqueue
  generazione (status → `pending`).
- `cancel_lesson_video` / `cancel_all_lesson_videos` — annulla in flight
  (sposta `pending`/`processing` → `cancelled`).
- `load_course_full` — eager-load con modules+lessons (riusa pattern PDF).
- `resolve_voice_sample_path` — risoluzione path filesystem del campione
  vocale dell'assegnatario (course.assignee_user_id → Avatar.audio_path).
- `compute_lesson_video_status` — builder DTO `LessonVideoStatusOut`.

Il vero lavoro di rendering è nel worker
(`course_lesson_video_worker._process_one`).
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.models.avatar import Avatar
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.models.course_module import CourseModule
from app.schemas.course_lesson_video import (
    LessonVideoBatchOut,
    LessonVideoStatusOut,
)

log = get_logger("app.course_lesson_video.service")


# Status `video_status` da cui è ammesso (ri-)avviare una generazione.
VALID_VIDEO_REQUEST_STATUSES: tuple[str, ...] = (
    "empty",
    "ready",
    "failed",
    "cancelled",
)
# Pre-condizione di contenuto upstream.
EXPORTABLE_SPEECH_STATUSES: tuple[str, ...] = ("approved",)
EXPORTABLE_SLIDES_STATUSES: tuple[str, ...] = ("approved",)


# ---------------------------------------------------------------------------
# Eager loaders
# ---------------------------------------------------------------------------


async def load_course_full(
    db: AsyncSession, *, course_id: uuid.UUID
) -> Course | None:
    """Eager load di Course → modules → lessons. Stessa shape usata dal
    worker speech/pdf per evitare N+1."""
    res = await db.execute(
        select(Course)
        .where(Course.id == course_id)
        .options(
            selectinload(Course.modules).selectinload(CourseModule.lessons),
        )
    )
    return res.scalar_one_or_none()


async def get_lesson_or_404(
    *, course: Course, lesson_id: uuid.UUID
) -> CourseLesson:
    for module in course.modules:
        for lesson in module.lessons:
            if lesson.id == lesson_id:
                return lesson
    raise NotFoundError(
        f"Lezione {lesson_id} non trovata in corso {course.id}",
        code="lesson_not_found",
    )


# ---------------------------------------------------------------------------
# Voice sample resolution
# ---------------------------------------------------------------------------


async def resolve_assignee_avatar(
    db: AsyncSession, *, assignee_user_id: uuid.UUID
) -> Avatar | None:
    """Carica l'Avatar dell'assegnatario del corso."""
    res = await db.execute(
        select(Avatar).where(Avatar.user_id == assignee_user_id)
    )
    return res.scalar_one_or_none()


async def resolve_voice_sample_path(
    db: AsyncSession, *, assignee_user_id: uuid.UUID
) -> Path | None:
    """Risolve il path filesystem assoluto del campione vocale dell'utente
    assegnatario al corso. Ritorna `None` se mancante.

    `Avatar.audio_path` è un path pubblico (es. `/uploads/avatars/uuid.wav`)
    sotto `settings.upload_root`. Strippiamo il prefisso `/uploads/` e
    componiamo l'absolute path.
    """
    settings = get_settings()
    avatar = await resolve_assignee_avatar(
        db, assignee_user_id=assignee_user_id
    )
    if avatar is None or not avatar.audio_path:
        return None
    rel = avatar.audio_path
    if rel.startswith("/uploads/"):
        rel = rel.removeprefix("/uploads/")
    target = (settings.upload_root / rel).resolve()
    # Path traversal safety.
    try:
        target.relative_to(settings.upload_root.resolve())
    except ValueError:  # pragma: no cover
        return None
    if not target.is_file():
        return None
    return target


# ---------------------------------------------------------------------------
# Filesystem path per il MP4 di output
# ---------------------------------------------------------------------------


def video_relative_path(course_id: uuid.UUID, lesson_id: uuid.UUID) -> str:
    """Path relativo sotto `upload_root` (e quindi pubblico via `/uploads/`).

    Pattern: `lesson_videos/{course_id}/{lesson_id}.mp4`. Stabile per
    (course, lesson) — la rigenerazione sovrascrive lo stesso file.
    """
    return f"lesson_videos/{course_id}/{lesson_id}.mp4"


def video_absolute_path(rel: str) -> Path:
    settings = get_settings()
    return (settings.upload_root / rel).resolve()


def video_public_url(rel: str | None) -> str | None:
    if not rel:
        return None
    if rel.startswith("/uploads/"):
        return rel
    return f"/uploads/{rel.lstrip('/')}"


# ---------------------------------------------------------------------------
# Status DTO builder
# ---------------------------------------------------------------------------


def _is_stale(lesson: CourseLesson) -> bool:
    """True se il video è stato generato PRIMA dell'ultima modifica al
    discorso o alle slide (timestamps `*_modified_at` e `*_approved_at`).
    Allineato al pattern `isSpeechPdfStale` lato FE.
    """
    gen = lesson.video_generated_at
    if gen is None:
        return False
    upstream = []
    if lesson.speech_modified_at is not None:
        upstream.append(lesson.speech_modified_at)
    if lesson.speech_approved_at is not None:
        upstream.append(lesson.speech_approved_at)
    if lesson.slides_modified_at is not None:
        upstream.append(lesson.slides_modified_at)
    if lesson.slides_approved_at is not None:
        upstream.append(lesson.slides_approved_at)
    return any(u > gen for u in upstream)


def build_status_out(
    lesson: CourseLesson,
    *,
    voice_sample_available: bool,
) -> LessonVideoStatusOut:
    return LessonVideoStatusOut(
        lesson_id=str(lesson.id),
        lesson_code=lesson.lesson_code,
        status=lesson.video_status,
        progress=lesson.video_progress,
        progress_phase=lesson.video_progress_phase,
        video_url=video_public_url(lesson.video_path),
        error=lesson.video_error,
        attempts=lesson.video_attempts,
        generated_at=lesson.video_generated_at,
        tokens=lesson.video_tokens,
        is_stale=_is_stale(lesson),
        speech_approved=lesson.speech_status == "approved",
        slides_approved=lesson.slides_status == "approved",
        voice_sample_available=voice_sample_available,
    )


def is_lesson_eligible(
    lesson: CourseLesson,
    *,
    voice_sample_available: bool,
) -> bool:
    """Lezione eleggibile alla generazione video: speech+slides approved
    AND voice sample presente AND video non già in corso."""
    if lesson.speech_status not in EXPORTABLE_SPEECH_STATUSES:
        return False
    if lesson.slides_status not in EXPORTABLE_SLIDES_STATUSES:
        return False
    if not voice_sample_available:
        return False
    if lesson.video_status not in VALID_VIDEO_REQUEST_STATUSES + ("ready",):
        return False
    return True


def build_batch_out(
    course: Course,
    *,
    voice_sample_available: bool,
) -> LessonVideoBatchOut:
    """Costruisce l'aggregato pagina-corso. Niente DB extra: i dati sono
    nei lesson già eager-loaded."""
    items: list[LessonVideoStatusOut] = []
    counts = {
        "ready": 0,
        "processing": 0,
        "pending": 0,
        "failed": 0,
        "cancelled": 0,
        "empty": 0,
    }
    eligible = 0
    progress_sum = 0
    in_flight = 0
    for module in course.modules:
        for lesson in module.lessons:
            item = build_status_out(
                lesson,
                voice_sample_available=voice_sample_available,
            )
            items.append(item)
            counts[item.status] = counts.get(item.status, 0) + 1
            if item.status in ("pending", "processing"):
                in_flight += 1
                progress_sum += item.progress
            if is_lesson_eligible(
                lesson,
                voice_sample_available=voice_sample_available,
            ):
                eligible += 1

    aggregate = (
        int(progress_sum / in_flight) if in_flight else 0
    )
    return LessonVideoBatchOut(
        items=items,
        total=len(items),
        ready_count=counts.get("ready", 0),
        processing_count=counts.get("processing", 0),
        pending_count=counts.get("pending", 0),
        failed_count=counts.get("failed", 0),
        eligible_count=eligible,
        aggregate_progress=aggregate,
    )


# ---------------------------------------------------------------------------
# Enqueue / cancel public API
# ---------------------------------------------------------------------------


async def request_lesson_video(
    db: AsyncSession,
    *,
    course: Course,
    lesson: CourseLesson,
    actor_id: uuid.UUID,
    voice_sample_path: Path | None,
) -> CourseLesson:
    """Marca la lezione come `video_status='pending'`. Vincoli:
    - speech+slides `approved`
    - video status ∈ empty/ready/failed/cancelled
    - voice sample presente
    """
    if lesson.speech_status not in EXPORTABLE_SPEECH_STATUSES:
        raise ConflictError(
            f"Lezione {lesson.lesson_code}: il discorso deve essere "
            f"`approved` (attuale: {lesson.speech_status}).",
            code="speech_not_approved",
        )
    if lesson.slides_status not in EXPORTABLE_SLIDES_STATUSES:
        raise ConflictError(
            f"Lezione {lesson.lesson_code}: le slide devono essere "
            f"`approved` (attuale: {lesson.slides_status}).",
            code="slides_not_approved",
        )
    if voice_sample_path is None:
        raise ConflictError(
            "L'assegnatario del corso non ha un campione vocale "
            "configurato (Avatar.audio_path mancante).",
            code="voice_sample_missing",
        )
    if lesson.video_status not in VALID_VIDEO_REQUEST_STATUSES:
        raise ConflictError(
            f"Generazione video già in corso per {lesson.lesson_code}: "
            f"{lesson.video_status}",
            code="video_already_in_progress",
        )

    lesson.video_status = "pending"
    lesson.video_error = None
    lesson.video_progress = 0
    lesson.video_progress_phase = None

    await write_audit(
        db,
        action="course.lesson.video.requested",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course_lesson",
        target_id=str(lesson.id),
        metadata={
            "course_id": str(course.id),
            "lesson_code": lesson.lesson_code,
        },
    )
    await db.commit()
    await db.refresh(lesson)
    return lesson


async def request_all_lessons_video(
    db: AsyncSession,
    *,
    course: Course,
    actor_id: uuid.UUID,
    voice_sample_path: Path | None,
) -> list[CourseLesson]:
    """Marca tutte le lezioni eleggibili come `video_status='pending'`.

    Eleggibile = `is_lesson_eligible()`. Lezioni già `pending`/`processing`
    vengono saltate silenziosamente.
    """
    if voice_sample_path is None:
        raise ConflictError(
            "L'assegnatario del corso non ha un campione vocale configurato.",
            code="voice_sample_missing",
        )

    eligible: list[CourseLesson] = []
    for module in course.modules:
        for lesson in module.lessons:
            if is_lesson_eligible(
                lesson,
                voice_sample_available=True,
            ):
                eligible.append(lesson)

    if not eligible:
        raise ConflictError(
            "Nessuna lezione eleggibile (servono discorso+slide approvati "
            "e video non già in corso).",
            code="no_eligible_lessons_for_video",
        )

    for lesson in eligible:
        lesson.video_status = "pending"
        lesson.video_error = None
        lesson.video_progress = 0
        lesson.video_progress_phase = None

    await write_audit(
        db,
        action="course.lesson.video.requested_all",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course",
        target_id=str(course.id),
        metadata={
            "lessons_count": len(eligible),
            "lesson_codes": [l.lesson_code for l in eligible],
        },
    )
    await db.commit()
    for lesson in eligible:
        await db.refresh(lesson)
    return eligible


async def cancel_lesson_video(
    db: AsyncSession,
    *,
    course: Course,
    lesson: CourseLesson,
    actor_id: uuid.UUID,
) -> CourseLesson:
    """Annulla una singola lezione in flight (`pending`/`processing` →
    `cancelled`). Idempotente: se non è in flight, no-op."""
    if lesson.video_status not in ("pending", "processing"):
        return lesson
    lesson.video_status = "cancelled"
    lesson.video_error = "Generazione annullata"
    lesson.video_progress = 0
    lesson.video_progress_phase = None
    await write_audit(
        db,
        action="course.lesson.video.cancelled",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course_lesson",
        target_id=str(lesson.id),
        metadata={
            "course_id": str(course.id),
            "lesson_code": lesson.lesson_code,
        },
    )
    await db.commit()
    await db.refresh(lesson)
    return lesson


async def cancel_all_lesson_videos(
    db: AsyncSession,
    *,
    course: Course,
    actor_id: uuid.UUID,
) -> list[CourseLesson]:
    affected: list[CourseLesson] = []
    for module in course.modules:
        for lesson in module.lessons:
            if lesson.video_status in ("pending", "processing"):
                lesson.video_status = "cancelled"
                lesson.video_error = "Generazione annullata"
                lesson.video_progress = 0
                lesson.video_progress_phase = None
                affected.append(lesson)
    if affected:
        await write_audit(
            db,
            action="course.lesson.video.cancelled_all",
            actor_user_id=actor_id,
            organization_id=course.organization_id,
            target_type="course",
            target_id=str(course.id),
            metadata={
                "cancelled_lesson_codes": [l.lesson_code for l in affected],
            },
        )
    await db.commit()
    for lesson in affected:
        await db.refresh(lesson)
    return affected


# ---------------------------------------------------------------------------
# Persistenza video_path al termine del worker
# ---------------------------------------------------------------------------


def save_video_metadata(
    lesson: CourseLesson,
    *,
    video_rel_path: str,
    tokens: dict[str, Any],
) -> None:
    """Aggiorna lesson con i campi finali post-encoding. Non commita."""
    from datetime import UTC, datetime

    lesson.video_path = video_rel_path
    lesson.video_status = "ready"
    lesson.video_progress = 100
    lesson.video_progress_phase = None
    lesson.video_error = None
    lesson.video_generated_at = datetime.now(UTC)
    lesson.video_tokens = tokens
