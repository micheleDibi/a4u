"""Servizio orchestrazione per la Fase 5 — Discorso temporizzato (§8).

Mirror del service di Fase 4 (slides) ma scoped sul `speech_*` della
lezione. Pre-condizione: la lezione deve avere `slides_status ∈ (ready,
approved)` (l'AI ha bisogno sia di `content_raw` sia di `slides_raw`
come input).

Funzioni esposte (mirror slides):
- `build_user_prompt(course, lesson)` — §8.3 + §9.5 in regenerazione
- `is_regeneration_for_lesson(lesson)` — True se esiste già speech_raw o hint
- `materialize_lesson_speech` — validazione §8.5 + persist
- `_recompute_course_speech_status` — deriva course.status dai speech_*
- `request_lesson_speech_generation` — pending + reset PDF
- `request_all_lessons_speech_generation` / `request_missing_lessons_speech_generation`
- `cancel_all_speech_generation` — pending|processing → failed
- `approve_lesson_speech` / `approve_all_lessons_speech`
- `validate_tts_safety(text)` — helper riusato dal CRUD
- `load_course_full`, `get_lesson_or_404`
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import write_audit
from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.models.course_module import CourseModule
from app.schemas.course_lesson_speech import LessonSpeechOutput
from app.services.openai_lesson_speech_service import words_per_minute

log = get_logger("app.course_lesson_speech")


def _now() -> datetime:
    return datetime.now(tz=UTC)


# Stati lezione da cui è ammesso (ri)generare il discorso.
# Il discorso può essere generato solo dopo che le slide sono almeno
# `ready` (anche se non approvate — lo stale-detection segnalerà se il
# docente edita le slide dopo aver generato il discorso).
VALID_LESSON_SPEECH_GENERATE_FROM_STATUSES = {
    "empty",
    "pending",
    "ready",
    "approved",
    "failed",
}

# Stati a livello corso da cui è ammesso triggerare la Fase 5.
VALID_COURSE_SPEECH_GENERATE_FROM_STATUSES = {
    "slides_ready",
    "slides_approved",
    "speech_pending",
    "speech_ready",
    "speech_approved",
}


# ---------------------------------------------------------------------------
# TTS-safety validation (regola §8.5 punto 5)
# ---------------------------------------------------------------------------


# Caratteri speciali proibiti nel testo TTS-safe.
# `*`, `_`, `` ` ``, `#`, `\`, `$` (markdown / TeX / shell).
_TTS_SAFETY_FORBIDDEN_CHARS = ("*", "_", "`", "#", "\\", "$")

# Abbreviazioni proibite (case-insensitive con word boundary).
# Punteggiatura inclusa nel match per minimizzare i falsi positivi
# (es. "es." vs "espresso"). Lista da spec §8.5.
_TTS_SAFETY_FORBIDDEN_ABBREVIATIONS = (
    r"\bes\.",
    r"\betc\.",
    r"\bca\.",
    r"\bp\.es\.",
    r"\bi\.e\.",
    r"\be\.g\.",
)
_TTS_SAFETY_ABBREVIATION_RE = re.compile(
    "|".join(_TTS_SAFETY_FORBIDDEN_ABBREVIATIONS), re.IGNORECASE
)

# Comandi LaTeX comuni che non devono comparire nel parlato (l'AI deve
# descriverli a parole).
_TTS_SAFETY_LATEX_RE = re.compile(
    r"\\(frac|sum|int|cdot|alpha|beta|gamma|delta|epsilon|zeta|eta|"
    r"theta|iota|kappa|lambda|mu|nu|xi|pi|rho|sigma|tau|upsilon|phi|"
    r"chi|psi|omega|sqrt|infty|partial|nabla|times|leq|geq|neq|approx|"
    r"propto|in|notin|forall|exists|emptyset|cap|cup|subset|supset|"
    r"begin|end|left|right|mathrm|mathbf|mathit|text|textbf|textit|"
    r"hline|cline|cr|displaystyle|over|underline|overline|hat|tilde|"
    r"bar|vec|dot|ddot|prime|widehat|widetilde)\b"
)


def validate_tts_safety(text: str) -> list[str]:
    """Ritorna un elenco di violazioni TTS-safety (vuoto = OK).

    Pubblica perché riusata dal CRUD (`update_lesson_speech`) per
    validare l'edit manuale.
    """
    violations: list[str] = []
    # Caratteri proibiti
    found_chars = sorted({c for c in _TTS_SAFETY_FORBIDDEN_CHARS if c in text})
    for c in found_chars:
        violations.append(f"carattere proibito `{c}`")
    # Abbreviazioni
    abbr_matches = sorted({m.group(0) for m in _TTS_SAFETY_ABBREVIATION_RE.finditer(text)})
    for m in abbr_matches:
        violations.append(f"abbreviazione proibita `{m}`")
    # LaTeX
    latex_matches = sorted({m.group(0) for m in _TTS_SAFETY_LATEX_RE.finditer(text)})
    for m in latex_matches:
        violations.append(f"comando LaTeX `{m}`")
    return violations


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
# Prompt building (§8.3 + §9.5 in regenerazione)
# ---------------------------------------------------------------------------


def _format_recommended_bibliography(lesson: CourseLesson) -> str:
    """Bibliografia consigliata della lezione introduttiva (§7.2 — il
    discorso introduttivo legge i titoli per esteso)."""
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
        line = f"- {authors}, {title}, {publisher}, {year}."
        if note:
            line += f" — {note}"
        lines.append(line)
    return "\n".join(lines) if lines else "(nessuna voce)"


def _format_current_speech_phase5(lesson: CourseLesson) -> str:
    """Serializza il `speech_raw` corrente per il prompt di rigenerazione."""
    raw = lesson.speech_raw
    if not raw:
        return "(Nessuna versione precedente.)"
    try:
        return json.dumps(raw, ensure_ascii=False, indent=2)
    except Exception:
        return "(Versione precedente non serializzabile.)"


def build_user_prompt(course: Course, lesson: CourseLesson) -> str:
    """Costruisce il messaggio utente §8.3 (discorso temporizzato).

    Pre-condizione: `course` e `lesson` sono stati caricati con eager-load.
    `lesson.content_raw` e `lesson.slides_raw` devono essere popolati
    (lezione `slides_status ∈ (ready, approved)`).
    """
    minuti = course.lesson_duration_minutes
    lang = course.language_code
    eqf_label = (
        getattr(course.livello_eqf, "name", "") if course.livello_eqf else ""
    )
    ruolo_docente = (
        getattr(course.ruolo_docente, "name", "") if course.ruolo_docente else ""
    )
    stile_insegnamento = (
        getattr(course.stile_insegnamento, "name", "")
        if course.stile_insegnamento
        else ""
    )

    content_raw_json = (
        json.dumps(lesson.content_raw, ensure_ascii=False, indent=2)
        if lesson.content_raw
        else "(content_raw assente — questa è una situazione anomala)"
    )
    slides_raw_json = (
        json.dumps(lesson.slides_raw, ensure_ascii=False, indent=2)
        if lesson.slides_raw
        else "(slides_raw assente — questa è una situazione anomala)"
    )

    blocks = [
        "## Lezione",
        "",
        f"ID: {lesson.lesson_code}",
        f"Titolo: {lesson.title}",
        f"È introduttiva: {str(lesson.is_introductory).lower()}",
        f"Durata target: {minuti} minuti",
        f"Lingua: {lang}",
        f"Livello EQF: {eqf_label}",
        f"Ruolo del docente: {ruolo_docente}",
        f"Stile di insegnamento: {stile_insegnamento}",
        "",
        "## Testo della lezione (Fase 3)",
        "",
        content_raw_json,
        "",
        "## Slide della lezione (Fase 4)",
        "",
        slides_raw_json,
        "",
        "## Bibliografia consigliata (se introduttiva)",
        "",
        _format_recommended_bibliography(lesson),
        "",
        "## Compito",
        "",
        "Genera il discorso temporizzato secondo lo schema JSON.",
        "",
        "Vincoli da rispettare:",
        "- ogni slide ha almeno un segmento di parlato",
        f"- somma delle estimated_duration_seconds = {minuti} * 60",
        "  (tolleranza ±5%)",
        "- testo TTS-friendly come da regole",
    ]

    if lesson.speech_regeneration_hint or lesson.speech_raw:
        blocks.extend(
            [
                "",
                "## Versione attuale del discorso (DA RIVEDERE)",
                "",
                _format_current_speech_phase5(lesson),
            ]
        )
        if lesson.speech_regeneration_hint:
            blocks.extend(
                [
                    "",
                    "## Indicazioni del docente per la rigenerazione",
                    "",
                    lesson.speech_regeneration_hint,
                ]
            )

    return "\n".join(blocks)


def is_regeneration_for_lesson(lesson: CourseLesson) -> bool:
    """True se è una rigenerazione (§9.5): esiste già un speech_raw o un hint."""
    return bool(lesson.speech_raw) or bool(lesson.speech_regeneration_hint)


# ---------------------------------------------------------------------------
# Validazione + materializzazione (§8.5)
# ---------------------------------------------------------------------------


async def materialize_lesson_speech(
    db: AsyncSession,
    *,
    course: Course,
    lesson: CourseLesson,
    output: LessonSpeechOutput,
    raw: dict[str, Any],
    usage: dict[str, Any],
) -> None:
    """Valida (§8.5) e scrive `speech_raw` + meta sulla lezione.

    NOTA: il caller (worker o sync endpoint) deve avere già caricato
    `course.modules` e `lesson.module` con eager-load. `lesson.slides_raw`
    deve essere disponibile (Fase 4 deve essere ready/approved).
    """
    # 1. Match lesson_id ↔ lesson_code
    if output.lesson_id != lesson.lesson_code:
        raise ConflictError(
            f"L'AI ha prodotto lesson_id `{output.lesson_id}`, "
            f"atteso `{lesson.lesson_code}`.",
            code="lesson_speech_id_mismatch",
        )

    # 2. Tutti gli slide_id referenziati esistono in slides_raw.slides
    slides_raw = lesson.slides_raw or {}
    valid_slide_ids: set[str] = {
        s.get("slide_id")
        for s in (slides_raw.get("slides") or [])
        if isinstance(s, dict) and s.get("slide_id")
    }
    if not valid_slide_ids:
        raise ConflictError(
            "Le slide della lezione (slides_raw) sono vuote o malformate.",
            code="lesson_speech_no_slides_input",
        )

    referenced_slide_ids: set[str] = set()
    seen_segment_ids: set[str] = set()
    for seg in output.speech_segments:
        if seg.slide_id not in valid_slide_ids:
            raise ConflictError(
                f"Segmento `{seg.segment_id}`: slide_id `{seg.slide_id}` "
                f"non esiste nelle slide di Fase 4.",
                code="lesson_speech_unknown_slide_ref",
            )
        referenced_slide_ids.add(seg.slide_id)
        # 3. segment_id univoci
        if seg.segment_id in seen_segment_ids:
            raise ConflictError(
                f"segment_id `{seg.segment_id}` duplicato.",
                code="lesson_speech_duplicate_segment_id",
            )
        seen_segment_ids.add(seg.segment_id)

    # 4. Ogni slide di Fase 4 ha almeno un segmento associato
    uncovered = valid_slide_ids - referenced_slide_ids
    if uncovered:
        raise ConflictError(
            f"Slide senza segmento di parlato: {sorted(uncovered)}.",
            code="lesson_speech_uncovered_slides",
        )

    # 5. sum(estimated_duration_seconds) ∈ [target × 0.95, target × 1.05]
    target = course.lesson_duration_minutes * 60
    sum_durations = sum(
        s.estimated_duration_seconds for s in output.speech_segments
    )
    low = round(target * 0.95)
    high = round(target * 1.05)
    if not (low <= sum_durations <= high):
        raise ConflictError(
            f"Durata totale stimata {sum_durations}s fuori range "
            f"[{low}s, {high}s] (target {target}s ±5%).",
            code="lesson_speech_duration_out_of_range",
        )

    # 6. Word count coerente con duration × wpm (±15% soft warning)
    wpm = words_per_minute(course.language_code)
    expected_words = round(sum_durations * wpm / 60)
    if expected_words > 0:
        delta_pct = abs(output.estimated_total_word_count - expected_words) / expected_words
        if delta_pct > 0.15:
            log.warning(
                "lesson_speech_word_count_drift",
                lesson_code=lesson.lesson_code,
                actual=output.estimated_total_word_count,
                expected=expected_words,
                wpm=wpm,
                delta_pct=round(delta_pct * 100, 1),
            )

    # 7. slide_to_segments_map coerente con speech_segments
    seg_by_id = {s.segment_id: s for s in output.speech_segments}
    listed_segment_ids: set[str] = set()
    for entry in output.slide_to_segments_map:
        if entry.slide_id not in valid_slide_ids:
            raise ConflictError(
                f"slide_to_segments_map: slide_id `{entry.slide_id}` "
                f"non esiste nelle slide di Fase 4.",
                code="lesson_speech_map_unknown_slide",
            )
        sum_slide_dur = 0
        for sid in entry.segment_ids:
            if sid not in seg_by_id:
                raise ConflictError(
                    f"slide_to_segments_map: segment_id `{sid}` non "
                    f"presente in speech_segments.",
                    code="lesson_speech_map_unknown_segment",
                )
            seg = seg_by_id[sid]
            if seg.slide_id != entry.slide_id:
                raise ConflictError(
                    f"slide_to_segments_map: segment `{sid}` mappato a "
                    f"slide `{entry.slide_id}` ma `speech_segments` lo "
                    f"ancora a `{seg.slide_id}`.",
                    code="lesson_speech_map_inconsistent_slide_id",
                )
            sum_slide_dur += seg.estimated_duration_seconds
            listed_segment_ids.add(sid)
        if sum_slide_dur != entry.slide_total_duration_seconds:
            raise ConflictError(
                f"slide_to_segments_map: slide_total_duration_seconds "
                f"({entry.slide_total_duration_seconds}s) di slide "
                f"`{entry.slide_id}` non corrisponde alla somma dei "
                f"segmenti ({sum_slide_dur}s).",
                code="lesson_speech_map_duration_mismatch",
            )
    orphan_segments = seen_segment_ids - listed_segment_ids
    if orphan_segments:
        raise ConflictError(
            f"slide_to_segments_map non lista i segmenti "
            f"{sorted(orphan_segments)}.",
            code="lesson_speech_map_orphan_segments",
        )

    # 8. TTS-safety
    for seg in output.speech_segments:
        violations = validate_tts_safety(seg.text)
        if violations:
            raise ConflictError(
                f"Segmento `{seg.segment_id}` non è TTS-safe: "
                f"{'; '.join(violations[:5])}.",
                code="lesson_speech_tts_unsafe",
            )

    # 9. Apply — scrive speech_raw + meta
    lesson.speech_raw = raw
    lesson.speech_tokens = usage
    lesson.speech_status = "ready"
    lesson.speech_generated_at = _now()
    lesson.speech_error = None
    lesson.speech_progress = 100
    lesson.speech_progress_phase = None

    # 10. Side-effect course-level
    _recompute_course_speech_status(course)


# ---------------------------------------------------------------------------
# Course-level status derivation
# ---------------------------------------------------------------------------


def _recompute_course_speech_status(course: Course) -> None:
    """Aggiorna `course.status` in base agli stati speech delle lezioni.

    Regole (mirror del slides):
    - almeno 1 lezione in `pending|processing|failed` → `speech_pending`
    - TUTTE in `approved` → `speech_approved`
    - TUTTE in `ready|approved` (con almeno 1 ready) → `speech_ready`
    - se nessuna lezione è in stato Fase 5 (tutte `empty`) → invariato.
    """
    statuses = [
        lesson.speech_status
        for m in course.modules
        for lesson in m.lessons
    ]
    if not statuses:
        return

    if any(s in ("pending", "processing", "failed") for s in statuses):
        course.status = "speech_pending"
        return

    if all(s == "approved" for s in statuses):
        course.status = "speech_approved"
        return

    if all(s in ("ready", "approved") for s in statuses) and any(
        s == "ready" for s in statuses
    ):
        course.status = "speech_ready"
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


# ---------------------------------------------------------------------------
# PDF reset helper (riusato da request/cancel)
# ---------------------------------------------------------------------------


def _reset_speech_pdf_if_needed(lesson: CourseLesson) -> None:
    """Reset `speech_pdf_*` a stato vuoto quando il discorso viene rigenerato.

    Da chiamare ogni volta che `speech_status` torna in `pending`. Il
    PDF discorso (Step 7) diventa obsoleto: i nuovi `speech_raw`
    avranno segmenti, durate o testo diversi.

    Sicuro da chiamare anche prima di Step 7 — se le colonne speech_pdf_*
    non esistono ancora, hasattr ritorna False.
    """
    if not hasattr(lesson, "speech_pdf_status"):
        return
    if lesson.speech_pdf_status in ("ready", "failed"):
        lesson.speech_pdf_status = "empty"
        lesson.speech_pdf_progress = 0
        lesson.speech_pdf_progress_phase = None
        lesson.speech_pdf_error = None


# ---------------------------------------------------------------------------
# Orchestration: request / cancel / approve
# ---------------------------------------------------------------------------


async def request_lesson_speech_generation(
    db: AsyncSession,
    *,
    course: Course,
    lesson: CourseLesson,
    actor_id: uuid.UUID,
    regeneration_hint: str | None,
) -> Course:
    """Sposta lo status della lezione a `pending` e annota l'eventuale
    hint. Il worker prenderà la riga al prossimo tick e la elabora in
    parallelo. Pre-condizione: `lesson.slides_status ∈ (ready, approved)`.
    """
    if course.status not in VALID_COURSE_SPEECH_GENERATE_FROM_STATUSES:
        raise ConflictError(
            f"Stato corso non valido per Fase 5: {course.status}. "
            f"Servono slide `ready` o `approved` prima di generare il discorso.",
            code="invalid_course_status_for_speech",
        )
    if lesson.slides_status not in ("ready", "approved"):
        raise ConflictError(
            f"Lezione {lesson.lesson_code}: le slide devono essere "
            f"`ready` o `approved` per generare il discorso (attuale: "
            f"{lesson.slides_status}).",
            code="lesson_slides_not_ready_for_speech",
        )
    if lesson.speech_status not in VALID_LESSON_SPEECH_GENERATE_FROM_STATUSES:
        raise ConflictError(
            f"Lezione {lesson.lesson_code}: stato discorso non valido: "
            f"{lesson.speech_status}",
            code="invalid_lesson_speech_status",
        )

    lesson.speech_status = "pending"
    lesson.speech_error = None
    lesson.speech_progress = 0
    lesson.speech_progress_phase = None
    lesson.speech_regeneration_hint = (
        regeneration_hint.strip() if regeneration_hint else None
    )
    # Il PDF discorso diventa obsoleto: i nuovi speech_raw potrebbero
    # avere segmenti, durate o testo diversi. Reset per impedire il
    # download del PDF stale.
    _reset_speech_pdf_if_needed(lesson)

    _recompute_course_speech_status(course)

    await write_audit(
        db,
        action="course.lesson.speech.generate.requested",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course_lesson",
        target_id=str(lesson.id),
        metadata={
            "course_id": str(course.id),
            "lesson_code": lesson.lesson_code,
            "is_regeneration": is_regeneration_for_lesson(lesson),
            "hint": (
                lesson.speech_regeneration_hint[:200]
                if lesson.speech_regeneration_hint
                else None
            ),
        },
    )
    await db.commit()
    return await _refresh_full(db, course)


async def request_all_lessons_speech_generation(
    db: AsyncSession,
    *,
    course: Course,
    actor_id: uuid.UUID,
    regeneration_hint: str | None,
) -> Course:
    """Marca tutte le lezioni con `slides_status ∈ (ready, approved)`
    come `speech_status='pending'`. Il worker le elabora in parallelo
    (cap configurabile, default 3)."""
    if course.status not in VALID_COURSE_SPEECH_GENERATE_FROM_STATUSES:
        raise ConflictError(
            f"Stato corso non valido per Fase 5: {course.status}",
            code="invalid_course_status_for_speech",
        )

    eligible: list[CourseLesson] = [
        lesson
        for m in course.modules
        for lesson in m.lessons
        if lesson.slides_status in ("ready", "approved")
    ]
    if not eligible:
        raise ConflictError(
            "Nessuna lezione con slide pronte. Genera prima la Fase 4.",
            code="no_lessons_with_slides",
        )

    hint_clean = regeneration_hint.strip() if regeneration_hint else None
    for lesson in eligible:
        lesson.speech_status = "pending"
        lesson.speech_error = None
        lesson.speech_progress = 0
        lesson.speech_progress_phase = None
        lesson.speech_regeneration_hint = hint_clean
        _reset_speech_pdf_if_needed(lesson)

    course.status = "speech_pending"

    await write_audit(
        db,
        action="course.speech.generate.requested",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course",
        target_id=str(course.id),
        metadata={
            "lessons_count": len(eligible),
            "hint": hint_clean[:200] if hint_clean else None,
        },
    )
    await db.commit()
    return await _refresh_full(db, course)


async def request_missing_lessons_speech_generation(
    db: AsyncSession,
    *,
    course: Course,
    actor_id: uuid.UUID,
) -> Course:
    """Marca SOLO le lezioni con `speech_status='empty'` AND
    `slides_status ∈ (ready, approved)` come `speech_status='pending'`."""
    if course.status not in VALID_COURSE_SPEECH_GENERATE_FROM_STATUSES:
        raise ConflictError(
            f"Stato corso non valido per Fase 5: {course.status}",
            code="invalid_course_status_for_speech",
        )

    missing: list[CourseLesson] = [
        lesson
        for m in course.modules
        for lesson in m.lessons
        if lesson.speech_status == "empty"
        and lesson.slides_status in ("ready", "approved")
    ]
    if not missing:
        raise ConflictError(
            "Nessuna lezione mancante: tutte hanno già discorso o non hanno "
            "slide pronte.",
            code="no_missing_speech_lessons",
        )

    for lesson in missing:
        lesson.speech_status = "pending"
        lesson.speech_error = None
        lesson.speech_progress = 0
        lesson.speech_progress_phase = None
        lesson.speech_regeneration_hint = None

    course.status = "speech_pending"

    await write_audit(
        db,
        action="course.speech.generate_missing.requested",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course",
        target_id=str(course.id),
        metadata={"lessons_count": len(missing)},
    )
    await db.commit()
    return await _refresh_full(db, course)


async def cancel_all_speech_generation(
    db: AsyncSession,
    *,
    course: Course,
    actor_id: uuid.UUID,
) -> Course:
    """Annulla la generazione discorso in corso: marca le `pending|processing`
    come `failed` con messaggio `annullato`. Le pending si bloccano
    subito, le processing finiscono l'I/O OpenAI ma il worker scarta
    il risultato (vedi `_process_one`).
    """
    all_lessons: list[CourseLesson] = [
        lesson for m in course.modules for lesson in m.lessons
    ]
    cancelled = 0
    for lesson in all_lessons:
        if lesson.speech_status in ("pending", "processing"):
            lesson.speech_status = "failed"
            lesson.speech_error = "Generazione annullata dall'utente."
            lesson.speech_progress = 0
            lesson.speech_progress_phase = None
            cancelled += 1

    if cancelled == 0:
        return await _refresh_full(db, course)

    _recompute_course_speech_status(course)

    await write_audit(
        db,
        action="course.speech.generate.cancelled",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course",
        target_id=str(course.id),
        metadata={
            "lessons_count": len(all_lessons),
            "cancelled": cancelled,
        },
    )
    await db.commit()
    return await _refresh_full(db, course)


# ---------------------------------------------------------------------------
# Approve
# ---------------------------------------------------------------------------


async def approve_lesson_speech(
    db: AsyncSession,
    *,
    course: Course,
    lesson: CourseLesson,
    actor_id: uuid.UUID,
) -> Course:
    """Sposta lo status del discorso della lezione da `ready` a `approved`."""
    if lesson.speech_status != "ready":
        raise ConflictError(
            f"Lezione {lesson.lesson_code}: discorso non in stato `ready` "
            f"(attuale: {lesson.speech_status}).",
            code="lesson_speech_not_ready",
        )

    lesson.speech_status = "approved"
    lesson.speech_approved_at = _now()
    _recompute_course_speech_status(course)

    await write_audit(
        db,
        action="course.lesson.speech.approved",
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
    return await _refresh_full(db, course)


async def approve_all_lessons_speech(
    db: AsyncSession,
    *,
    course: Course,
    actor_id: uuid.UUID,
) -> Course:
    """Approva tutti i discorsi `ready` del corso. Richiede che TUTTE le
    lezioni che hanno discorso siano `ready` o già `approved`."""
    all_lessons: list[CourseLesson] = [
        lesson for m in course.modules for lesson in m.lessons
    ]
    not_ready = [
        l for l in all_lessons
        if l.speech_status not in ("ready", "approved", "empty")
    ]
    if not_ready:
        raise ConflictError(
            f"Non tutte le lezioni hanno il discorso pronto. In attesa: "
            f"{', '.join(l.lesson_code for l in not_ready)}.",
            code="not_all_lessons_speech_ready",
        )

    eligible = [l for l in all_lessons if l.speech_status == "ready"]
    if not eligible:
        raise ConflictError(
            "Nessuna lezione con discorso `ready` da approvare.",
            code="no_speech_to_approve",
        )

    now = _now()
    approved_count = 0
    for lesson in eligible:
        lesson.speech_status = "approved"
        lesson.speech_approved_at = now
        approved_count += 1

    _recompute_course_speech_status(course)

    await write_audit(
        db,
        action="course.speech.approved",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course",
        target_id=str(course.id),
        metadata={
            "lessons_count": len(all_lessons),
            "newly_approved": approved_count,
        },
    )
    await db.commit()
    return await _refresh_full(db, course)
