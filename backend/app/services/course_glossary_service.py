"""Servizio orchestrazione per il Glossario del corso (§10.1).

Single-shot: una sola chiamata OpenAI per corso, riusata come
`{{glossario}}` nei prompt successivi (Fasi 2, 3, 5). Non ha worker
dedicato — la generazione avviene sync inline:
- alla chiamata pubblica `regenerate_glossary` (endpoint utente);
- automaticamente dal worker della Fase 3 al primo passaggio se
  `course.glossary_status` è `empty`/`failed` (§10.1).

State machine: `empty → processing → ready (+failed)`.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.errors import ConflictError
from app.core.logging import get_logger
from app.models.course import Course
from app.models.course_module import CourseModule
from app.schemas.course_glossary import GlossaryOutput
from app.services.course_architecture_service import (
    _build_documents_context,
    _term_label,
)
from app.services.openai_glossary_service import (
    OpenAIGlossaryError,
    generate_glossary,
)

log = get_logger("app.course_glossary")


def _now() -> datetime:
    return datetime.now(tz=UTC)


# Stati dai quali è ammesso lanciare la generazione (sync). Esclude
# `processing` per evitare double-fire.
VALID_GENERATE_FROM_STATUSES = {"empty", "ready", "approved", "failed"}


# Fase del corso minima per generare il glossario: serve avere almeno
# l'architettura approvata (titoli moduli/lezioni di Fase 1).
_VALID_COURSE_STATUSES_FOR_GLOSSARY = {
    "architecture_approved",
    "lessons_structure_pending",
    "lessons_structure_ready",
    "lessons_structure_approved",
    "content_pending",
    "content_ready",
    "content_approved",
    "slides_pending",
    "slides_ready",
    "speech_pending",
    "speech_ready",
    "published",
}


# ---------------------------------------------------------------------------
# Eager loading
# ---------------------------------------------------------------------------


def _eager_full_options() -> list:
    """Carica corso + documents + tutti i moduli con lezioni + taxonomies."""
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
    res = await db.execute(
        select(Course).where(Course.id == course.id).options(*_eager_full_options())
    )
    return res.scalar_one()


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------


def _format_modules_lessons_compact(course: Course) -> str:
    """Mappa compatta moduli/lezioni: `M1 - Titolo: L1 Titolo, L2 Titolo`."""
    if not course.modules:
        return "(Nessun modulo)"
    lines: list[str] = []
    for m in course.modules:
        lesson_parts = [
            f"{lesson.lesson_code} {lesson.title}" for lesson in m.lessons
        ]
        lessons_str = ", ".join(lesson_parts) if lesson_parts else "(nessuna lezione)"
        lines.append(f"{m.module_code} - {m.title}: {lessons_str}")
    return "\n".join(lines)


def _build_glossary_user_prompt(course: Course) -> str:
    """Costruisce il messaggio utente per la generazione del glossario.

    Concatena: parametri corso (titolo, obiettivi, tassonomie), mappa
    moduli/lezioni di Fase 1, summary documenti (con budget condiviso).
    Pre-condizione: eager-load di documents/modules/lessons/taxonomies.
    """
    settings = get_settings()
    lang = course.language_code

    documents_context = _build_documents_context(
        list(course.documents),
        settings.course_glossary_documents_context_max_chars,
    )

    blocks = [
        "## Contesto del corso",
        "",
        f"- Titolo: {course.title}",
        f"- Obiettivi del corso: {course.objectives or '(non specificati)'}",
        f"- Lingua: {lang}",
        f"- Categoria: {_term_label(course.categoria, lang)}",
        f"- Profondità del contenuto: {_term_label(course.profondita_contenuto, lang)}",
        f"- Livello EQF: {_term_label(course.livello_eqf, lang)}",
        f"- Destinatari: {_term_label(course.destinatari, lang)}",
        f"- Livello di conoscenza del pubblico: {_term_label(course.livello_conoscenza, lang)}",
        "",
        "## Argomenti chiave dichiarati",
        "",
        "\n".join(f"- {a}" for a in (course.argomenti_chiave or []))
        or "(nessuno)",
        "",
        "## Architettura del corso (Fase 1 approvata)",
        "",
        course.course_overview or "(Overview non disponibile.)",
        "",
        f"Razionale pedagogico: {course.pedagogical_rationale or '(non disponibile)'}",
        "",
        "Mappa dei moduli e delle lezioni:",
        _format_modules_lessons_compact(course),
        "",
        "## Documenti di riferimento (estratti rilevanti)",
        "",
        documents_context,
        "",
        "## Compito",
        "",
        f"Estrai il GLOSSARIO ESSENZIALE del corso (10-30 termini chiave).",
        f"Usa `course_id = \"{course.id}\"` nell'output.",
        "",
        "Restituisci il risultato nel formato JSON richiesto.",
    ]
    return "\n".join(blocks)


# ---------------------------------------------------------------------------
# Glossary serialization for downstream prompts (Fase 2, 3, 5)
# ---------------------------------------------------------------------------


def format_glossary_for_prompt(course: Course) -> str:
    """Serializza `course.glossary_raw` in formato bullet per i prompt.

    Output: `- term (translation): usage_note` per ogni termine. Se il
    glossario non è disponibile (status diverso da `ready`/`approved`),
    ritorna un placeholder testuale e logga un warning.
    """
    raw = course.glossary_raw or {}
    terms = raw.get("terms") or []
    if not terms:
        return "(Glossario non disponibile.)"
    lines: list[str] = []
    for t in terms:
        if not isinstance(t, dict):
            continue
        term = t.get("term") or "?"
        translation = (t.get("translation") or "").strip()
        usage = (t.get("usage_note") or "").strip()
        if translation:
            lines.append(f"- {term} ({translation}): {usage}")
        else:
            lines.append(f"- {term}: {usage}")
    return "\n".join(lines) if lines else "(Glossario vuoto.)"


# ---------------------------------------------------------------------------
# Generation (sync, single-shot)
# ---------------------------------------------------------------------------


async def _do_generate(
    db: AsyncSession, *, course: Course, actor_id: uuid.UUID | None
) -> Course:
    """Esegue la chiamata OpenAI sync, scrive l'output o l'errore.

    Restituisce il corso ricaricato eager. Solleva `ConflictError`/
    `OpenAIGlossaryError` se la chiamata fallisce.
    """
    course.glossary_status = "processing"
    course.glossary_error = None
    await db.commit()

    user_prompt = _build_glossary_user_prompt(course)
    log.info(
        "glossary_generate_start",
        course_id=str(course.id),
        chars=len(user_prompt),
    )

    try:
        glossary, usage = await generate_glossary(
            user_prompt=user_prompt,
            language_code=course.language_code,
        )
    except OpenAIGlossaryError as exc:
        course.glossary_status = "failed"
        course.glossary_error = (str(exc.message) or "Errore OpenAI")[:500]
        if actor_id is not None:
            await write_audit(
                db,
                action="course.glossary.failed",
                actor_user_id=actor_id,
                organization_id=course.organization_id,
                target_type="course",
                target_id=str(course.id),
                metadata={"error": course.glossary_error[:200]},
            )
        await db.commit()
        log.error(
            "glossary_generate_failed",
            course_id=str(course.id),
            error=course.glossary_error,
        )
        raise

    course.glossary_raw = glossary.model_dump()
    course.glossary_tokens = usage
    course.glossary_status = "ready"
    course.glossary_generated_at = _now()
    course.glossary_error = None

    if actor_id is not None:
        await write_audit(
            db,
            action="course.glossary.generated",
            actor_user_id=actor_id,
            organization_id=course.organization_id,
            target_type="course",
            target_id=str(course.id),
            metadata={
                "terms": len(glossary.terms),
                "tokens": usage.get("total"),
            },
        )
    await db.commit()
    log.info(
        "glossary_generate_ok",
        course_id=str(course.id),
        terms=len(glossary.terms),
        tokens=usage.get("total"),
    )
    return await _refresh_full(db, course)


async def regenerate_glossary(
    db: AsyncSession, *, course: Course, actor_id: uuid.UUID
) -> Course:
    """Endpoint pubblico per la rigenerazione manuale del glossario.

    Pre-condizione: il corso ha l'architettura approvata. Lancia la
    generazione sync (~10-20s). Audit `course.glossary.regenerate.requested`.
    """
    if course.status not in _VALID_COURSE_STATUSES_FOR_GLOSSARY:
        raise ConflictError(
            f"Stato corso non valido per generazione glossario: {course.status}",
            code="invalid_course_status",
        )
    if course.glossary_status not in VALID_GENERATE_FROM_STATUSES:
        raise ConflictError(
            f"Glossario già in lavorazione (status: {course.glossary_status}).",
            code="glossary_already_processing",
        )

    await write_audit(
        db,
        action="course.glossary.regenerate.requested",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course",
        target_id=str(course.id),
        metadata={"previous_status": course.glossary_status},
    )
    return await _do_generate(db, course=course, actor_id=actor_id)


async def ensure_glossary_ready(
    db: AsyncSession, *, course: Course, actor_id: uuid.UUID | None = None
) -> Course:
    """Helper chiamato dal worker Fase 3: se il glossario non è pronto,
    lo genera sync inline. Idempotente: se già `ready`/`approved`, no-op.

    Solleva `OpenAIGlossaryError` se la generazione fallisce.
    Restituisce il corso ricaricato eager.
    """
    if course.glossary_status in ("ready", "approved"):
        return course
    if course.glossary_status == "processing":
        # Caso limite: un altro task lo sta già generando. Ricarica e
        # ritorna; il caller può ritentare al prossimo tick. Non blocca.
        return await _refresh_full(db, course)

    log.info(
        "glossary_auto_generate_triggered",
        course_id=str(course.id),
        previous_status=course.glossary_status,
    )
    return await _do_generate(db, course=course, actor_id=actor_id)
