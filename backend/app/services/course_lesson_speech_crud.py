"""CRUD manuale dei campi Fase 5 (discorso) di una lezione.

L'AI produce il discorso via worker (`course_lesson_speech_worker`),
ma il docente può raffinare manualmente il `speech_raw` finché la
lezione è in stato `ready` o `approved`.

Edit non degrada lo stato (resta `approved` se era `approved`): è una
scelta esplicita del docente. La validazione di consistenza qui è
allentata rispetto a `materialize_lesson_speech` (che valida l'output
fresh dell'AI): hard fail solo per duplicati/orfani che renderebbero
il discorso inutilizzabile, e per violazioni TTS-safety (sempre).

Auto-ricalcolo durata: per ogni segmento modificato senza
`estimated_duration_seconds` esplicito, ricalcoliamo dal word count
con la regola 130 wpm IT / 150 wpm EN.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.errors import ConflictError
from app.core.logging import get_logger
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.schemas.course_lesson_speech import LessonSpeechUpdateInput
from app.services.course_lesson_speech_service import validate_tts_safety
from app.services.openai_lesson_speech_service import words_per_minute

log = get_logger("app.course_lesson_speech_crud")


# Stati della lezione da cui è ammesso l'edit manuale del speech_raw.
EDITABLE_LESSON_STATUSES = ("ready", "approved")


def _ensure_editable(lesson: CourseLesson) -> None:
    """Solleva ConflictError se la lezione non è in `ready`/`approved`."""
    if lesson.speech_status not in EDITABLE_LESSON_STATUSES:
        raise ConflictError(
            f"Discorso lezione non editabile: stato attuale "
            f"`{lesson.speech_status}` (richiesto `ready` o `approved`).",
            code="lesson_speech_not_editable",
        )


def _dump_models(items: list[Any] | None) -> list[dict[str, Any]] | None:
    if items is None:
        return None
    return [
        i.model_dump() if hasattr(i, "model_dump") else i for i in items
    ]


def _word_count(text: str) -> int:
    """Conteggio parole semplice (split su whitespace). Usato per
    l'auto-ricalcolo di `estimated_duration_seconds`."""
    if not text:
        return 0
    return len([w for w in text.split() if w.strip()])


def _validate_consistency(
    *,
    payload: LessonSpeechUpdateInput,
    current_raw: dict[str, Any],
    slides_raw: dict[str, Any] | None,
    target_duration_seconds: int,
) -> None:
    """Validazione di consistenza per l'edit manuale (regole §8.5).

    Hard fail per: slide_id orfani, segment_id duplicati, slide
    scoperte, durata totale fuori range, TTS-safety violazione,
    inconsistenze in slide_to_segments_map.
    """
    segments = (
        _dump_models(payload.speech_segments)
        if payload.speech_segments is not None
        else current_raw.get("speech_segments", [])
    ) or []
    map_entries = (
        _dump_models(payload.slide_to_segments_map)
        if payload.slide_to_segments_map is not None
        else current_raw.get("slide_to_segments_map", [])
    ) or []

    if not segments:
        raise ConflictError(
            "Il discorso deve contenere almeno un segmento.",
            code="lesson_speech_no_segments",
        )

    # Set di slide_id validi (da slides_raw di Fase 4)
    valid_slide_ids: set[str] = set()
    if slides_raw:
        for s in slides_raw.get("slides") or []:
            if isinstance(s, dict) and s.get("slide_id"):
                valid_slide_ids.add(str(s["slide_id"]))
    if not valid_slide_ids:
        raise ConflictError(
            "Le slide della lezione non sono disponibili: impossibile "
            "validare il discorso.",
            code="lesson_speech_no_slides_input",
        )

    # 1. Validazione segmenti: slide_id, segment_id, TTS-safety
    seen_segment_ids: set[str] = set()
    referenced_slide_ids: set[str] = set()
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        sid = seg.get("segment_id")
        slide_id = seg.get("slide_id")
        text = seg.get("text", "")
        duration = seg.get("estimated_duration_seconds")

        if not sid:
            raise ConflictError(
                "Ogni segmento deve avere un `segment_id` non vuoto.",
                code="lesson_speech_segment_id_required",
            )
        if sid in seen_segment_ids:
            raise ConflictError(
                f"segment_id `{sid}` duplicato.",
                code="lesson_speech_duplicate_segment_id",
            )
        seen_segment_ids.add(sid)

        if not slide_id or slide_id not in valid_slide_ids:
            raise ConflictError(
                f"Segmento `{sid}`: slide_id `{slide_id}` non esiste "
                f"nelle slide di Fase 4.",
                code="lesson_speech_unknown_slide_ref",
            )
        referenced_slide_ids.add(slide_id)

        if not text or not str(text).strip():
            raise ConflictError(
                f"Segmento `{sid}`: il testo è vuoto.",
                code="lesson_speech_empty_text",
            )

        # TTS-safety
        violations = validate_tts_safety(str(text))
        if violations:
            raise ConflictError(
                f"Segmento `{sid}` non è TTS-safe: "
                f"{'; '.join(violations[:5])}.",
                code="lesson_speech_tts_unsafe",
            )

        if duration is None or not isinstance(duration, int) or duration < 1:
            raise ConflictError(
                f"Segmento `{sid}`: estimated_duration_seconds deve "
                f"essere un intero ≥ 1.",
                code="lesson_speech_invalid_duration",
            )

    # 2. Ogni slide di Fase 4 ha almeno un segmento
    uncovered = valid_slide_ids - referenced_slide_ids
    if uncovered:
        raise ConflictError(
            f"Slide senza segmento di parlato: {sorted(uncovered)}.",
            code="lesson_speech_uncovered_slides",
        )

    # 3. Range durata totale ±5%
    sum_durations = sum(
        int(s.get("estimated_duration_seconds") or 0) for s in segments
    )
    low = round(target_duration_seconds * 0.95)
    high = round(target_duration_seconds * 1.05)
    if not (low <= sum_durations <= high):
        raise ConflictError(
            f"Durata totale {sum_durations}s fuori range "
            f"[{low}s, {high}s] (target {target_duration_seconds}s ±5%).",
            code="lesson_speech_duration_out_of_range",
        )

    # 4. slide_to_segments_map coerente con speech_segments
    seg_by_id: dict[str, dict[str, Any]] = {
        s["segment_id"]: s for s in segments if isinstance(s, dict) and s.get("segment_id")
    }
    listed_segment_ids: set[str] = set()
    for entry in map_entries:
        if not isinstance(entry, dict):
            continue
        slide_id = entry.get("slide_id")
        segment_ids = entry.get("segment_ids") or []
        slide_total = entry.get("slide_total_duration_seconds")

        if slide_id not in valid_slide_ids:
            raise ConflictError(
                f"slide_to_segments_map: slide_id `{slide_id}` non esiste.",
                code="lesson_speech_map_unknown_slide",
            )
        sum_slide_dur = 0
        for sid in segment_ids:
            if sid not in seg_by_id:
                raise ConflictError(
                    f"slide_to_segments_map: segment_id `{sid}` non "
                    f"presente in speech_segments.",
                    code="lesson_speech_map_unknown_segment",
                )
            seg = seg_by_id[sid]
            if seg.get("slide_id") != slide_id:
                raise ConflictError(
                    f"slide_to_segments_map: segment `{sid}` mappato a "
                    f"slide `{slide_id}` ma `speech_segments` lo ancora "
                    f"a `{seg.get('slide_id')}`.",
                    code="lesson_speech_map_inconsistent_slide_id",
                )
            sum_slide_dur += int(seg.get("estimated_duration_seconds") or 0)
            listed_segment_ids.add(sid)
        if (
            slide_total is None
            or not isinstance(slide_total, int)
            or slide_total != sum_slide_dur
        ):
            raise ConflictError(
                f"slide_to_segments_map: slide_total_duration_seconds "
                f"({slide_total}s) di slide `{slide_id}` non corrisponde "
                f"alla somma dei segmenti ({sum_slide_dur}s).",
                code="lesson_speech_map_duration_mismatch",
            )
    orphan_segments = seen_segment_ids - listed_segment_ids
    if orphan_segments:
        raise ConflictError(
            f"slide_to_segments_map non lista i segmenti "
            f"{sorted(orphan_segments)}.",
            code="lesson_speech_map_orphan_segments",
        )


async def update_lesson_speech(
    db: AsyncSession,
    *,
    course: Course,
    lesson: CourseLesson,
    payload: LessonSpeechUpdateInput,
    actor_id: uuid.UUID,
) -> Course:
    """Patch parziale del `speech_raw` della lezione.

    Edit non degrada lo stato (`approved` resta `approved`). Setta
    `speech_modified_at` per la stale-detection downstream (PDF discorso).
    """
    _ensure_editable(lesson)
    current_raw: dict[str, Any] = dict(lesson.speech_raw or {})

    target_seconds = course.lesson_duration_minutes * 60

    _validate_consistency(
        payload=payload,
        current_raw=current_raw,
        slides_raw=lesson.slides_raw,
        target_duration_seconds=target_seconds,
    )

    changed: dict[str, int | str] = {}

    if payload.speech_segments is not None:
        segments_dump = [s.model_dump() for s in payload.speech_segments]
        current_raw["speech_segments"] = segments_dump
        # Aggiorna i totali derivati dal payload (la validazione ha già
        # garantito che durations e word counts siano coerenti).
        sum_durations = sum(
            int(s.get("estimated_duration_seconds") or 0) for s in segments_dump
        )
        wpm = words_per_minute(course.language_code)
        # Se il caller ha lasciato il word count obsoleto in current_raw,
        # ricalcoliamo da text per mantenere coerenza.
        sum_words = sum(_word_count(s.get("text", "")) for s in segments_dump)
        current_raw["estimated_total_duration_seconds"] = sum_durations
        current_raw["estimated_total_word_count"] = sum_words or round(
            sum_durations * wpm / 60
        )
        changed["speech_segments"] = len(segments_dump)
    if payload.slide_to_segments_map is not None:
        map_dump = [m.model_dump() for m in payload.slide_to_segments_map]
        current_raw["slide_to_segments_map"] = map_dump
        changed["slide_to_segments_map"] = len(map_dump)

    if not changed:
        return course

    # Mantieni i campi di intestazione invariati / coerenti.
    current_raw.setdefault("lesson_id", lesson.lesson_code)
    current_raw.setdefault("language", course.language_code)
    current_raw.setdefault("target_duration_seconds", target_seconds)

    lesson.speech_raw = current_raw
    # Stale-detection: marca il discorso come modificato manualmente. Il
    # FE confronta con `speech_pdf_generated_at` (Step 7) per dedurre
    # se il PDF discorso downstream è stale. I worker AI di Fase 5 NON
    # toccano questo campo.
    lesson.speech_modified_at = datetime.now(UTC)

    await write_audit(
        db,
        action="course.lesson.speech.updated",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course_lesson",
        target_id=str(lesson.id),
        metadata={
            "course_id": str(course.id),
            "lesson_code": lesson.lesson_code,
            "fields": changed,
        },
    )
    await db.commit()

    from app.services import course_lesson_speech_service

    return await course_lesson_speech_service._refresh_full(db, course)
