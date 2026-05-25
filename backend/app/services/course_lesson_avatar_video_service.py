"""Public API + helper per la scheda "Video con Avatar" (§9b).

La scheda "Video con Avatar" prende il video MP4 già generato della
lezione (Fase 6) e ci sovrappone in basso a destra un avatar parlante
con lip-sync MuseTalk. L'audio del lip-sync è estratto direttamente dal
video della lezione, quindi l'avatar resta sempre sincronizzato con lo
scorrere delle slide.

Espone:
- `request_lesson_avatar_video` / `request_all_lessons_avatar_video` —
  enqueue generazione (status → `pending`).
- `cancel_lesson_avatar_video` / `cancel_all_lesson_avatar_videos` —
  annulla in flight (`pending`/`processing` → `cancelled`).
- `build_batch_out` — builder DTO aggregato per la pagina corso.
- helper di path (`avatar_video_*`, `avatar_clips_dir`).

Pre-condizioni runtime:
- `video_status='ready'` (il video MP4 della lezione esiste già);
- l'avatar dell'assegnatario del corso ha ≥ 1 clip MiniMax pronta.

Il vero lavoro è nel worker
(`course_lesson_avatar_video_worker._process_one`).
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.course_phase_order import advance_course_status
from app.core.errors import ConflictError
from app.core.logging import get_logger
from app.models.avatar import Avatar
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.schemas.course_lesson_avatar_video import (
    LessonAvatarVideoBatchOut,
    LessonAvatarVideoStatusOut,
)
from app.services.course_lesson_video_service import (
    get_lesson_or_404,
    load_course_full,
    resolve_assignee_avatar,
)

log = get_logger("app.course_lesson_avatar_video.service")

# Re-export: i call site (rotte) importano questi helper da qui.
__all__ = [
    "VALID_AVATAR_VIDEO_REQUEST_STATUSES",
    "load_course_full",
    "get_lesson_or_404",
    "resolve_assignee_avatar",
    "count_ready_clips",
    "avatar_is_ready",
    "avatar_clips_dir",
    "avatar_musetalk_clips_dir",
    "avatar_video_relative_path",
    "avatar_video_absolute_path",
    "avatar_video_public_url",
    "is_lesson_eligible",
    "build_status_out",
    "build_batch_out",
    "request_lesson_avatar_video",
    "request_all_lessons_avatar_video",
    "cancel_lesson_avatar_video",
    "cancel_all_lesson_avatar_videos",
    "save_avatar_video_metadata",
]


# Status `avatar_video_status` da cui è ammesso (ri-)avviare una generazione.
VALID_AVATAR_VIDEO_REQUEST_STATUSES: tuple[str, ...] = (
    "empty",
    "ready",
    "failed",
    "cancelled",
)


# ---------------------------------------------------------------------------
# Course status — auto-transizione Fase 6b (Video con Avatar)
# ---------------------------------------------------------------------------


async def _recompute_course_avatar_video_status(
    db: AsyncSession, course_id: uuid.UUID
) -> None:
    """Aggiorna `course.status` in base agli stati avatar-video delle
    lezioni non-assessment del corso. Da chiamare DOPO aver mutato un
    `lesson.avatar_video_status` e PRIMA del commit.

    Regole (simmetriche a `_recompute_course_video_status` di Fase 6):
    - almeno 1 lezione in `pending|processing|failed` → `avatar_video_pending`
    - almeno 1 `ready` e tutte in `ready|cancelled|empty` → `avatar_video_ready`
    - tutte `cancelled`/`empty` → invariato (lascia lo stato di Fase 6).
    """
    res = await db.execute(
        select(CourseLesson.avatar_video_status)
        .where(CourseLesson.course_id == course_id)
        .where(CourseLesson.is_assessment.is_(False))
    )
    statuses = list(res.scalars().all())
    if not statuses:
        return
    new_status: str | None = None
    if any(s in ("pending", "processing", "failed") for s in statuses):
        new_status = "avatar_video_pending"
    elif any(s == "ready" for s in statuses) and all(
        s in ("ready", "cancelled", "empty") for s in statuses
    ):
        new_status = "avatar_video_ready"
    if new_status is None:
        return
    course = await db.get(Course, course_id)
    if course is not None:
        advance_course_status(course, new_status)


# ---------------------------------------------------------------------------
# Avatar / clip helpers
# ---------------------------------------------------------------------------


def count_ready_clips(avatar: Avatar | None) -> int:
    """Numero di clip MiniMax dell'avatar in stato `ready` con un file
    video associato. La relazione `Avatar.clips` è `lazy='selectin'`,
    quindi è già caricata quando l'avatar viene letto dal DB."""
    if avatar is None:
        return 0
    return sum(
        1
        for c in avatar.clips
        if c.status == "ready" and c.video_path
    )


def avatar_is_ready(avatar: Avatar | None) -> bool:
    """True se l'avatar può essere usato per il lip-sync (≥ 1 clip pronta)."""
    return count_ready_clips(avatar) >= 1


def avatar_clips_dir(user_id: uuid.UUID) -> Path:
    """Path filesystem assoluto della cartella clip dell'avatar utente.

    Le clip MiniMax sono salvate da `avatar_clip_worker` come
    `{upload_root}/avatars/{user_id}/clips/{clip_id}.mp4`. È la
    `--clips-dir` passata a `synth_random_lipsync`: puntarci direttamente
    (anziché copiare i file) mantiene stabile l'hash del set di clip, e
    quindi la cache del preprocessing MuseTalk fra lezioni e rigenerazioni.
    """
    settings = get_settings()
    return (settings.upload_root / "avatars" / str(user_id) / "clips").resolve()


def avatar_musetalk_clips_dir(user_id: uuid.UUID, resolution: int) -> Path:
    """Cartella delle clip dell'avatar ridimensionate per MuseTalk.

    `{upload_root}/avatars/{user_id}/clips_musetalk_{resolution}/`. Le clip
    MiniMax originali (1080×1080) sono troppo grandi per il lip-sync su
    RunPod (il job sfora il tetto di 60 min); il worker ne tiene qui una
    copia ridimensionata e punta lì `--clips-dir`. La risoluzione è nel
    nome della cartella: cambiare il setting crea una cartella nuova,
    senza file stantii.
    """
    settings = get_settings()
    return (
        settings.upload_root
        / "avatars"
        / str(user_id)
        / f"clips_musetalk_{resolution}"
    ).resolve()


# ---------------------------------------------------------------------------
# Filesystem path per il MP4 di output
# ---------------------------------------------------------------------------


def avatar_video_relative_path(
    course_id: uuid.UUID, lesson_id: uuid.UUID
) -> str:
    """Path relativo sotto `upload_root` (pubblico via `/uploads/`).

    Pattern: `lesson_avatar_videos/{course_id}/{lesson_id}.mp4`. Stabile
    per (course, lesson) — la rigenerazione sovrascrive lo stesso file.
    """
    return f"lesson_avatar_videos/{course_id}/{lesson_id}.mp4"


def avatar_video_absolute_path(rel: str) -> Path:
    settings = get_settings()
    return (settings.upload_root / rel).resolve()


def avatar_video_public_url(rel: str | None) -> str | None:
    if not rel:
        return None
    if rel.startswith("/uploads/"):
        return rel
    return f"/uploads/{rel.lstrip('/')}"


# ---------------------------------------------------------------------------
# Status DTO builder
# ---------------------------------------------------------------------------


def _is_stale(lesson: CourseLesson) -> bool:
    """True se il video con avatar è stato generato PRIMA dell'ultima
    (ri)generazione del video MP4 della lezione su cui è sovrapposto."""
    gen = lesson.avatar_video_generated_at
    if gen is None:
        return False
    base = lesson.video_generated_at
    return base is not None and base > gen


def build_status_out(
    lesson: CourseLesson,
    *,
    avatar_clips_ready: bool,
) -> LessonAvatarVideoStatusOut:
    return LessonAvatarVideoStatusOut(
        lesson_id=str(lesson.id),
        lesson_code=lesson.lesson_code,
        status=lesson.avatar_video_status,
        progress=lesson.avatar_video_progress,
        progress_phase=lesson.avatar_video_progress_phase,
        video_url=avatar_video_public_url(lesson.avatar_video_path),
        error=lesson.avatar_video_error,
        attempts=lesson.avatar_video_attempts,
        generated_at=lesson.avatar_video_generated_at,
        tokens=lesson.avatar_video_tokens,
        is_stale=_is_stale(lesson),
        lesson_video_ready=lesson.video_status == "ready"
        and bool(lesson.video_path),
        avatar_clips_ready=avatar_clips_ready,
    )


def is_lesson_eligible(
    lesson: CourseLesson,
    *,
    avatar_clips_ready: bool,
) -> bool:
    """Lezione eleggibile alla generazione del video con avatar: video
    della lezione `ready` AND avatar con clip pronte AND avatar-video non
    già in corso.

    Le lezioni-verifica (`is_assessment`) non sono mai eleggibili."""
    if lesson.is_assessment:
        return False
    if lesson.video_status != "ready" or not lesson.video_path:
        return False
    if not avatar_clips_ready:
        return False
    if lesson.avatar_video_status not in (
        VALID_AVATAR_VIDEO_REQUEST_STATUSES + ("ready",)
    ):
        return False
    return True


def build_batch_out(
    course: Course,
    *,
    avatar_clips_ready: bool,
) -> LessonAvatarVideoBatchOut:
    """Costruisce l'aggregato pagina-corso. Niente DB extra: i dati sono
    nei lesson già eager-loaded."""
    items: list[LessonAvatarVideoStatusOut] = []
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
            # La lezione-verifica non fa parte della pipeline video.
            if lesson.is_assessment:
                continue
            item = build_status_out(
                lesson,
                avatar_clips_ready=avatar_clips_ready,
            )
            items.append(item)
            counts[item.status] = counts.get(item.status, 0) + 1
            if item.status in ("pending", "processing"):
                in_flight += 1
                progress_sum += item.progress
            if is_lesson_eligible(
                lesson,
                avatar_clips_ready=avatar_clips_ready,
            ):
                eligible += 1

    aggregate = int(progress_sum / in_flight) if in_flight else 0
    return LessonAvatarVideoBatchOut(
        items=items,
        total=len(items),
        ready_count=counts.get("ready", 0),
        processing_count=counts.get("processing", 0),
        pending_count=counts.get("pending", 0),
        failed_count=counts.get("failed", 0),
        eligible_count=eligible,
        aggregate_progress=aggregate,
        avatar_clips_ready=avatar_clips_ready,
    )


# ---------------------------------------------------------------------------
# Enqueue / cancel public API
# ---------------------------------------------------------------------------


def _assert_lesson_preconditions(
    lesson: CourseLesson, *, avatar: Avatar | None
) -> None:
    """Solleva `ConflictError` se la lezione non può generare il video
    con avatar. Comune a richiesta singola e validazione."""
    if lesson.is_assessment:
        raise ConflictError(
            f"La lezione {lesson.lesson_code} è una verifica delle "
            f"competenze: non genera video.",
            code="lesson_is_assessment_not_eligible",
        )
    if lesson.video_status != "ready" or not lesson.video_path:
        raise ConflictError(
            f"Lezione {lesson.lesson_code}: il video della lezione deve "
            f"essere già stato generato (scheda «Video»).",
            code="lesson_video_not_ready",
        )
    if not avatar_is_ready(avatar):
        raise ConflictError(
            "L'avatar dell'assegnatario del corso non ha clip pronte: "
            "genera prima le clip dell'avatar.",
            code="avatar_clips_not_ready",
        )


async def request_lesson_avatar_video(
    db: AsyncSession,
    *,
    course: Course,
    lesson: CourseLesson,
    actor_id: uuid.UUID,
    avatar: Avatar | None,
) -> CourseLesson:
    """Marca la lezione come `avatar_video_status='pending'`. Vincoli:
    - video della lezione `ready`
    - avatar dell'assegnatario con ≥ 1 clip pronta
    - avatar_video status ∈ empty/ready/failed/cancelled
    """
    _assert_lesson_preconditions(lesson, avatar=avatar)
    if lesson.avatar_video_status not in VALID_AVATAR_VIDEO_REQUEST_STATUSES:
        raise ConflictError(
            f"Generazione del video con avatar già in corso per "
            f"{lesson.lesson_code}: {lesson.avatar_video_status}",
            code="avatar_video_already_in_progress",
        )

    lesson.avatar_video_status = "pending"
    lesson.avatar_video_error = None
    lesson.avatar_video_progress = 0
    lesson.avatar_video_progress_phase = None

    await write_audit(
        db,
        action="course.lesson.avatar_video.requested",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course_lesson",
        target_id=str(lesson.id),
        metadata={
            "course_id": str(course.id),
            "lesson_code": lesson.lesson_code,
        },
    )
    await _recompute_course_avatar_video_status(db, course.id)
    await db.commit()
    await db.refresh(lesson)
    return lesson


async def request_all_lessons_avatar_video(
    db: AsyncSession,
    *,
    course: Course,
    actor_id: uuid.UUID,
    avatar: Avatar | None,
) -> list[CourseLesson]:
    """Marca tutte le lezioni eleggibili come `avatar_video_status='pending'`.

    Eleggibile = `is_lesson_eligible()`. Lezioni già `pending`/`processing`
    vengono saltate silenziosamente.
    """
    if not avatar_is_ready(avatar):
        raise ConflictError(
            "L'avatar dell'assegnatario del corso non ha clip pronte: "
            "genera prima le clip dell'avatar.",
            code="avatar_clips_not_ready",
        )

    eligible: list[CourseLesson] = []
    for module in course.modules:
        for lesson in module.lessons:
            if is_lesson_eligible(lesson, avatar_clips_ready=True):
                eligible.append(lesson)

    if not eligible:
        raise ConflictError(
            "Nessuna lezione eleggibile (serve il video della lezione "
            "già generato e non già in corso).",
            code="no_eligible_lessons_for_avatar_video",
        )

    for lesson in eligible:
        lesson.avatar_video_status = "pending"
        lesson.avatar_video_error = None
        lesson.avatar_video_progress = 0
        lesson.avatar_video_progress_phase = None

    await write_audit(
        db,
        action="course.lesson.avatar_video.requested_all",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course",
        target_id=str(course.id),
        metadata={
            "lessons_count": len(eligible),
            "lesson_codes": [lesson.lesson_code for lesson in eligible],
        },
    )
    await _recompute_course_avatar_video_status(db, course.id)
    await db.commit()
    for lesson in eligible:
        await db.refresh(lesson)
    return eligible


async def cancel_lesson_avatar_video(
    db: AsyncSession,
    *,
    course: Course,
    lesson: CourseLesson,
    actor_id: uuid.UUID,
) -> CourseLesson:
    """Annulla una singola lezione in flight (`pending`/`processing` →
    `cancelled`). Idempotente: se non è in flight, no-op."""
    if lesson.avatar_video_status not in ("pending", "processing"):
        return lesson
    lesson.avatar_video_status = "cancelled"
    lesson.avatar_video_error = "Generazione annullata"
    lesson.avatar_video_progress = 0
    lesson.avatar_video_progress_phase = None
    await write_audit(
        db,
        action="course.lesson.avatar_video.cancelled",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course_lesson",
        target_id=str(lesson.id),
        metadata={
            "course_id": str(course.id),
            "lesson_code": lesson.lesson_code,
        },
    )
    await _recompute_course_avatar_video_status(db, course.id)
    await db.commit()
    await db.refresh(lesson)
    return lesson


async def cancel_all_lesson_avatar_videos(
    db: AsyncSession,
    *,
    course: Course,
    actor_id: uuid.UUID,
) -> list[CourseLesson]:
    affected: list[CourseLesson] = []
    for module in course.modules:
        for lesson in module.lessons:
            if lesson.avatar_video_status in ("pending", "processing"):
                lesson.avatar_video_status = "cancelled"
                lesson.avatar_video_error = "Generazione annullata"
                lesson.avatar_video_progress = 0
                lesson.avatar_video_progress_phase = None
                affected.append(lesson)
    if affected:
        await write_audit(
            db,
            action="course.lesson.avatar_video.cancelled_all",
            actor_user_id=actor_id,
            organization_id=course.organization_id,
            target_type="course",
            target_id=str(course.id),
            metadata={
                "cancelled_lesson_codes": [
                    lesson.lesson_code for lesson in affected
                ],
            },
        )
        await _recompute_course_avatar_video_status(db, course.id)
    await db.commit()
    for lesson in affected:
        await db.refresh(lesson)
    return affected


# ---------------------------------------------------------------------------
# Persistenza avatar_video_path al termine del worker
# ---------------------------------------------------------------------------


def save_avatar_video_metadata(
    lesson: CourseLesson,
    *,
    video_rel_path: str,
    tokens: dict[str, Any],
) -> None:
    """Aggiorna lesson con i campi finali post-overlay. Non commita."""
    from datetime import UTC, datetime

    lesson.avatar_video_path = video_rel_path
    lesson.avatar_video_status = "ready"
    lesson.avatar_video_progress = 100
    lesson.avatar_video_progress_phase = None
    lesson.avatar_video_error = None
    lesson.avatar_video_generated_at = datetime.now(UTC)
    lesson.avatar_video_tokens = tokens
