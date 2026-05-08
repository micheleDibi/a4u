"""Servizio orchestrazione per la Fase 3 — Contenuti delle lezioni (§6).

Responsabilità:
- costruire il **user prompt** della §6.3 a partire dai parametri del
  corso, della lezione (Fase 2 approvata), del glossario, dei documenti
  riassunti e dell'eventuale `regeneration_hint` (§9.3);
- avviare la generazione (transizione `lesson.content_status` → `pending`);
- **materializzare** l'output validato (10 validazioni §6.4) sui campi
  Fase 3 di `course_lesson` (`content_raw`, `content_tokens`, ...);
- approvare il contenuto per lezione o per corso intero (deriva
  `course.status` da `lesson.content_*`).

Il worker `course_lesson_content_worker` consuma le righe lezioni
`pending` IN PARALLELO (semaforo con cap configurabile, default 3).
"""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.models.course_module import CourseModule
from app.schemas.course_lesson_content import LessonContentOutput
from app.services.course_architecture_service import (
    _build_documents_context,
    _term_label,
)
from app.services.course_glossary_service import format_glossary_for_prompt

log = get_logger("app.course_lesson_content")


def _now() -> datetime:
    return datetime.now(tz=UTC)


# Stati a livello lezione da cui è ammesso (ri)generare il contenuto.
VALID_LESSON_GENERATE_FROM_STATUSES = {
    "empty",
    "pending",
    "ready",
    "approved",
    "failed",
}

# Stati a livello corso da cui è ammesso triggerare la Fase 3.
VALID_COURSE_GENERATE_FROM_STATUSES = {
    "lessons_structure_approved",
    "content_pending",
    "content_ready",
    "content_approved",
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
    """Ricarica il corso con tutti gli eager-loads usati da CourseOut."""
    res = await db.execute(
        select(Course).where(Course.id == course.id).options(*_eager_full_options())
    )
    return res.scalar_one()


# ---------------------------------------------------------------------------
# Prompt building (§6.3 + §9.3 quando regenerate)
# ---------------------------------------------------------------------------


def _format_previous_lessons_summary(
    course: Course, target_lesson: CourseLesson
) -> str:
    """Riassunto compatto delle lezioni precedenti alla target (per richiami)."""
    if not course.modules:
        return "(Nessuna lezione precedente.)"
    lines: list[str] = []
    for m in course.modules:
        for lesson in m.lessons:
            if lesson.id == target_lesson.id:
                # Ferma alla target esclusa.
                return "\n".join(lines) if lines else "(Nessuna lezione precedente.)"
            summary = (lesson.summary or "").strip() or "(senza sintesi)"
            lines.append(f"- {lesson.lesson_code} {lesson.title}: {summary}")
    return "\n".join(lines) if lines else "(Nessuna lezione precedente.)"


def _format_next_lesson_summary(
    course: Course, target_lesson: CourseLesson
) -> str:
    """Riassunto della lezione successiva (per agganci)."""
    found_target = False
    for m in course.modules:
        for lesson in m.lessons:
            if found_target:
                summary = (lesson.summary or "").strip() or "(senza sintesi)"
                return f"{lesson.lesson_code} {lesson.title}: {summary}"
            if lesson.id == target_lesson.id:
                found_target = True
    return "(Nessuna lezione successiva.)"


def _format_recommended_bibliography(lesson: CourseLesson) -> str:
    """Formatta `recommended_bibliography` (lista di dict) per il prompt."""
    if not lesson.is_introductory or not lesson.recommended_bibliography:
        return "(non applicabile)"
    items = lesson.recommended_bibliography or []
    if not items:
        return "(non applicabile)"
    lines: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        authors = item.get("authors", "")
        title = item.get("title", "")
        publisher = item.get("publisher", "")
        year = item.get("year", "")
        note = item.get("note", "")
        source = item.get("source", "")
        lines.append(
            f"- {authors}, *{title}*, {publisher} ({year}). "
            f"[{source}] {note}".strip()
        )
    return "\n".join(lines) if lines else "(non applicabile)"


def _format_learning_objectives(lesson: CourseLesson) -> str:
    objs = lesson.learning_objectives or []
    if not objs:
        return "(nessuno)"
    return "\n".join(f"- {o}" for o in objs)


def _format_mandatory_topics(lesson: CourseLesson) -> str:
    topics = lesson.mandatory_topics or []
    if not topics:
        return "(nessuno)"
    lines: list[str] = []
    for t in topics:
        if not isinstance(t, dict):
            continue
        tid = t.get("topic_id", "?")
        topic = t.get("topic", "")
        rationale = t.get("rationale", "")
        lines.append(f"- [{tid}] {topic} — {rationale}")
    return "\n".join(lines) if lines else "(nessuno)"


def _format_prerequisites(lesson: CourseLesson) -> str:
    prereqs = lesson.prerequisites or []
    if not prereqs:
        return "(nessuno)"
    return "\n".join(f"- {p}" for p in prereqs)


def _format_section_outline(lesson: CourseLesson) -> str:
    outline = lesson.section_outline or []
    if not outline:
        return "(nessuna scaletta)"
    lines: list[str] = []
    for s in outline:
        if not isinstance(s, dict):
            continue
        sid = s.get("section_id", "?")
        title = s.get("title", "")
        purpose = s.get("purpose", "")
        covers = s.get("covers_topic_ids") or []
        lines.append(
            f"- [{sid}] {title} — {purpose}  (copre: {', '.join(covers)})"
        )
    return "\n".join(lines) if lines else "(nessuna scaletta)"


def _find_module_for_lesson(
    course: Course, lesson: CourseLesson
) -> CourseModule | None:
    for m in course.modules:
        if m.id == lesson.module_id:
            return m
    return None


def _format_current_lesson_phase3(lesson: CourseLesson) -> str:
    """Serializza il `content_raw` attuale per il prompt di rigenerazione."""
    raw = lesson.content_raw
    if not raw:
        return "(Nessuna versione precedente.)"
    parts: list[str] = []
    intro = (raw.get("introduction") or "").strip()
    if intro:
        parts.append(f"### Introduzione\n{intro}")
    sections = raw.get("sections") or []
    for s in sections:
        if not isinstance(s, dict):
            continue
        sid = s.get("section_id", "?")
        title = s.get("title", "")
        content = (s.get("content") or "").strip()
        parts.append(f"### Sezione [{sid}] {title}\n{content}")
    summary = (raw.get("summary") or "").strip()
    if summary:
        parts.append(f"### Sintesi\n{summary}")
    takeaways = raw.get("key_takeaways") or []
    if takeaways:
        parts.append(
            "### Key takeaways\n" + "\n".join(f"- {kt}" for kt in takeaways)
        )
    assets = raw.get("visual_assets") or []
    if assets:
        parts.append(
            "### Asset visivi (asset_id)\n"
            + "\n".join(
                f"- {a.get('asset_id', '?')} ({a.get('asset_type', '?')})"
                for a in assets
                if isinstance(a, dict)
            )
        )
    return "\n\n".join(parts) if parts else "(Nessuna versione precedente.)"


def build_user_prompt(course: Course, lesson: CourseLesson) -> str:
    """Costruisce il messaggio utente conforme al template §6.3.

    Pre-condizione: `course` e `lesson` sono stati caricati con
    eager-load di taxonomies, documents, modules, lessons.
    """
    settings = get_settings()
    lang = course.language_code

    documents_context = _build_documents_context(
        list(course.documents),
        settings.course_lesson_content_documents_context_max_chars,
    )

    glossary_text = format_glossary_for_prompt(course)

    current_module = _find_module_for_lesson(course, lesson)
    current_module_id = current_module.module_code if current_module else "?"
    current_module_title = current_module.title if current_module else "?"
    current_module_description = (
        (current_module.description if current_module else "") or "(non specificata)"
    )

    blocks = [
        "## Contesto del corso",
        "",
        f"- Titolo: {course.title}",
        f"- Obiettivi del corso: {course.objectives or '(non specificati)'}",
        f"- Categoria: {_term_label(course.categoria, lang)}",
        f"- Stile di insegnamento: {_term_label(course.stile_insegnamento, lang)}",
        f"- Profondità del contenuto: {_term_label(course.profondita_contenuto, lang)}",
        f"- Lingua: {lang}",
        f"- Ruolo del docente: {_term_label(course.ruolo_docente, lang)}",
        f"- Dimensione del pubblico: {_term_label(course.dimensione_pubblico, lang)} studenti",
        f"- Livello di conoscenza del pubblico: {_term_label(course.livello_conoscenza, lang)}",
        f"- Destinatari: {_term_label(course.destinatari, lang)}",
        f"- Livello EQF: {_term_label(course.livello_eqf, lang)}",
        "",
        "## Posizionamento della lezione",
        "",
        f"Modulo: {current_module_id} - {current_module_title}",
        f"Descrizione modulo: {current_module_description}",
        "",
        "Lezioni precedenti (per richiami):",
        _format_previous_lessons_summary(course, lesson),
        "",
        "Lezione successiva (per agganci):",
        _format_next_lesson_summary(course, lesson),
        "",
        "## Lezione da generare",
        "",
        f"ID: {lesson.lesson_code}",
        f"Titolo: {lesson.title}",
        f"È introduttiva: {str(lesson.is_introductory).lower()}",
        "",
        "Bibliografia consigliata (solo se introduttiva):",
        _format_recommended_bibliography(lesson),
        "",
        "Obiettivi formativi:",
        _format_learning_objectives(lesson),
        "",
        "Temi obbligatori (con ID):",
        _format_mandatory_topics(lesson),
        "",
        "Prerequisiti:",
        _format_prerequisites(lesson),
        "",
        "Section outline (segui questa scaletta in ordine):",
        _format_section_outline(lesson),
        "",
        "## Documenti di riferimento (estratti rilevanti)",
        "",
        documents_context,
        "",
        "## Glossario del corso",
        "",
        glossary_text,
        "",
        "## Compito",
        "",
        "Genera il testo completo della lezione secondo lo schema JSON.",
        "Verifica internamente che ogni obiettivo, ogni tema obbligatorio",
        "e ogni asset siano correttamente trattati e referenziati.",
    ]

    if lesson.content_regeneration_hint or lesson.content_raw:
        blocks.extend(
            [
                "",
                "## Versione attuale della lezione (DA RIVEDERE)",
                "",
                _format_current_lesson_phase3(lesson),
            ]
        )
        if lesson.content_regeneration_hint:
            blocks.extend(
                [
                    "",
                    "## Indicazioni del docente per la rigenerazione",
                    "",
                    lesson.content_regeneration_hint,
                ]
            )

    return "\n".join(blocks)


def is_regeneration_for_lesson(lesson: CourseLesson) -> bool:
    """True se è una rigenerazione (§9.3): esiste già un content_raw o un hint."""
    return bool(lesson.content_raw or lesson.content_regeneration_hint)


# ---------------------------------------------------------------------------
# State transitions: request generation
# ---------------------------------------------------------------------------


async def request_lesson_generation(
    db: AsyncSession,
    *,
    course: Course,
    lesson: CourseLesson,
    actor_id: uuid.UUID,
    regeneration_hint: str | None,
) -> Course:
    """Sposta lo status della lezione a `pending` e annota l'eventuale hint.
    Il worker prenderà la riga al prossimo tick e la elabora in parallelo."""
    if course.status not in VALID_COURSE_GENERATE_FROM_STATUSES:
        raise ConflictError(
            f"Stato corso non valido per Fase 3: {course.status}",
            code="invalid_course_status",
        )
    if lesson.content_status not in VALID_LESSON_GENERATE_FROM_STATUSES:
        raise ConflictError(
            f"Lezione {lesson.lesson_code} non in stato valido: "
            f"{lesson.content_status}",
            code="invalid_lesson_content_status",
        )

    lesson.content_status = "pending"
    lesson.content_error = None
    lesson.content_progress = 0
    lesson.content_progress_phase = None
    lesson.content_regeneration_hint = (
        regeneration_hint.strip() if regeneration_hint else None
    )

    _recompute_course_content_status(course)

    await write_audit(
        db,
        action="course.lesson.content.generate.requested",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course_lesson",
        target_id=str(lesson.id),
        metadata={
            "course_id": str(course.id),
            "lesson_code": lesson.lesson_code,
            "is_regeneration": is_regeneration_for_lesson(lesson),
            "hint": (
                lesson.content_regeneration_hint[:200]
                if lesson.content_regeneration_hint
                else None
            ),
        },
    )
    await db.commit()
    return await _refresh_full(db, course)


async def request_all_lessons_generation(
    db: AsyncSession,
    *,
    course: Course,
    actor_id: uuid.UUID,
    regeneration_hint: str | None,
) -> Course:
    """Marca TUTTE le lezioni del corso come `pending`. Il worker le
    elabora in parallelo (cap configurabile, default 3)."""
    if course.status not in VALID_COURSE_GENERATE_FROM_STATUSES:
        raise ConflictError(
            f"Stato corso non valido per Fase 3: {course.status}",
            code="invalid_course_status",
        )

    all_lessons: list[CourseLesson] = [
        lesson for m in course.modules for lesson in m.lessons
    ]
    if not all_lessons:
        raise ConflictError(
            "Il corso non ha lezioni — completa prima Fase 1 e Fase 2.",
            code="no_lessons_to_generate",
        )

    hint_clean = regeneration_hint.strip() if regeneration_hint else None
    for lesson in all_lessons:
        lesson.content_status = "pending"
        lesson.content_error = None
        lesson.content_progress = 0
        lesson.content_progress_phase = None
        lesson.content_regeneration_hint = hint_clean

    course.status = "content_pending"

    await write_audit(
        db,
        action="course.content.generate.requested",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course",
        target_id=str(course.id),
        metadata={
            "lessons_count": len(all_lessons),
            "hint": hint_clean[:200] if hint_clean else None,
        },
    )
    await db.commit()
    return await _refresh_full(db, course)


async def cancel_all_lessons_generation(
    db: AsyncSession,
    *,
    course: Course,
    actor_id: uuid.UUID,
) -> Course:
    """Annulla la generazione in corso: marca tutte le lezioni
    `pending|processing` come `failed` con messaggio "annullato".

    Le `pending` si bloccano subito (il worker non le prenderà più). Le
    `processing` continuano l'I/O OpenAI ma il worker, dopo la
    risposta, vede lo status cambiato e scarta il risultato (vedi
    `_process_one` in `course_lesson_content_worker.py`).
    """
    all_lessons: list[CourseLesson] = [
        lesson for m in course.modules for lesson in m.lessons
    ]
    cancelled = 0
    for lesson in all_lessons:
        if lesson.content_status in ("pending", "processing"):
            lesson.content_status = "failed"
            lesson.content_error = "Generazione annullata dall'utente."
            lesson.content_progress = 0
            lesson.content_progress_phase = None
            cancelled += 1

    if cancelled == 0:
        # Nessuna lezione da annullare: no-op silenzioso.
        return await _refresh_full(db, course)

    _recompute_course_content_status(course)

    await write_audit(
        db,
        action="course.content.generate.cancelled",
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
# Materialization: validazioni §6.4 + scrittura content_raw
# ---------------------------------------------------------------------------


_ASSET_REF_RE = re.compile(r"\[(FIG|TAB|EQ|EX):([^\]]+)\]")


def _collect_asset_refs(text: str) -> set[tuple[str, str]]:
    """Estrae i tag `[FIG:..]` / `[TAB:..]` / `[EQ:..]` / `[EX:..]` dal testo."""
    return {
        (kind.upper(), aid.strip())
        for kind, aid in _ASSET_REF_RE.findall(text or "")
    }


def _normalize_objective(s: str) -> str:
    """Normalizza un obiettivo per matching cross-field (case+spazi)."""
    return " ".join((s or "").lower().split())


async def materialize_lesson_content(
    db: AsyncSession,
    *,
    course: Course,
    lesson: CourseLesson,
    output: LessonContentOutput,
    raw: dict[str, Any],
    usage: dict[str, Any],
) -> None:
    """Valida (§6.4) e scrive `content_raw` + meta sulla lezione.

    NOTA: il caller (worker o sync endpoint) deve avere già caricato
    `course.modules` e `lesson.module` con eager-load.
    """
    # 1. Match lesson_id ↔ lesson_code
    if output.lesson_id != lesson.lesson_code:
        raise ConflictError(
            f"L'AI ha prodotto lesson_id `{output.lesson_id}`, "
            f"atteso `{lesson.lesson_code}`.",
            code="lesson_content_id_mismatch",
        )

    # 2. section_id univoci
    section_ids = [s.section_id for s in output.sections]
    if len(set(section_ids)) != len(section_ids):
        raise ConflictError(
            f"section_id duplicati nella lezione {lesson.lesson_code}.",
            code="lesson_content_duplicate_section_id",
        )

    # 3. asset_id univoci per ogni tipo
    visual_ids = [a.asset_id for a in output.visual_assets]
    if len(set(visual_ids)) != len(visual_ids):
        raise ConflictError(
            f"asset_id duplicati nei visual_assets della lezione "
            f"{lesson.lesson_code}.",
            code="lesson_content_duplicate_visual_asset_id",
        )
    table_ids = [t.table_id for t in output.tables]
    if len(set(table_ids)) != len(table_ids):
        raise ConflictError(
            f"table_id duplicati nella lezione {lesson.lesson_code}.",
            code="lesson_content_duplicate_table_id",
        )
    eq_ids = [e.equation_id for e in output.equations]
    if len(set(eq_ids)) != len(eq_ids):
        raise ConflictError(
            f"equation_id duplicati nella lezione {lesson.lesson_code}.",
            code="lesson_content_duplicate_equation_id",
        )
    ex_ids = [ex.example_id for ex in output.examples]
    if len(set(ex_ids)) != len(ex_ids):
        raise ConflictError(
            f"example_id duplicati nella lezione {lesson.lesson_code}.",
            code="lesson_content_duplicate_example_id",
        )

    # 4. Cross-field references: ogni objective/topic_id riferito in
    #    sections deve esistere nei dati Fase 2.
    fase2_objectives_norm = {
        _normalize_objective(o) for o in (lesson.learning_objectives or [])
    }
    fase2_topic_ids = {
        t.get("topic_id")
        for t in (lesson.mandatory_topics or [])
        if isinstance(t, dict) and t.get("topic_id")
    }

    for s in output.sections:
        for obj in s.objectives_addressed:
            if _normalize_objective(obj) not in fase2_objectives_norm:
                raise ConflictError(
                    f"Sezione {s.section_id}: obiettivo `{obj[:80]}...` "
                    f"non corrisponde ad alcun obiettivo di Fase 2.",
                    code="lesson_content_unknown_objective",
                )
        for tid in s.topics_addressed:
            if tid not in fase2_topic_ids:
                raise ConflictError(
                    f"Sezione {s.section_id}: topic_id `{tid}` non esiste "
                    f"nei mandatory_topics di Fase 2.",
                    code="lesson_content_unknown_topic_id",
                )

    # 5. Coverage completa: l'unione su sections deve coprire TUTTI gli
    #    obiettivi e topics di Fase 2.
    covered_objectives_norm: set[str] = set()
    covered_topic_ids: set[str] = set()
    for s in output.sections:
        for obj in s.objectives_addressed:
            covered_objectives_norm.add(_normalize_objective(obj))
        for tid in s.topics_addressed:
            covered_topic_ids.add(tid)

    uncovered_objs = fase2_objectives_norm - covered_objectives_norm
    if uncovered_objs:
        raise ConflictError(
            f"Lezione {lesson.lesson_code}: obiettivi non coperti da alcuna "
            f"sezione ({len(uncovered_objs)}).",
            code="lesson_content_objectives_uncovered",
        )
    uncovered_topics = fase2_topic_ids - covered_topic_ids
    if uncovered_topics:
        raise ConflictError(
            f"Lezione {lesson.lesson_code}: topic non coperti da alcuna "
            f"sezione: {sorted(uncovered_topics)}.",
            code="lesson_content_topics_uncovered",
        )

    # 6. coverage_check coerente con il calcolo effettivo dalle sections.
    declared_objs = {
        _normalize_objective(o.objective)
        for o in output.coverage_check.objectives_covered
    }
    if declared_objs != fase2_objectives_norm:
        raise ConflictError(
            f"coverage_check.objectives_covered non corrisponde agli "
            f"obiettivi della lezione {lesson.lesson_code}.",
            code="lesson_content_coverage_check_objectives_mismatch",
        )
    declared_topics = {
        t.topic_id for t in output.coverage_check.topics_covered
    }
    if declared_topics != fase2_topic_ids:
        raise ConflictError(
            f"coverage_check.topics_covered non corrisponde ai topic "
            f"della lezione {lesson.lesson_code}.",
            code="lesson_content_coverage_check_topics_mismatch",
        )

    # 7. Asset referenziati nel testo: warning soft (non blocca).
    text_corpus = (
        (output.introduction or "")
        + "\n"
        + "\n".join(s.content for s in output.sections)
        + "\n"
        + (output.summary or "")
    )
    refs = _collect_asset_refs(text_corpus)
    fig_refs = {aid for kind, aid in refs if kind == "FIG"}
    tab_refs = {aid for kind, aid in refs if kind == "TAB"}
    eq_refs = {aid for kind, aid in refs if kind == "EQ"}
    ex_refs = {aid for kind, aid in refs if kind == "EX"}

    unused_visuals = set(visual_ids) - fig_refs
    unused_tables = set(table_ids) - tab_refs
    unused_eqs = set(eq_ids) - eq_refs
    unused_examples = set(ex_ids) - ex_refs

    if unused_visuals or unused_tables or unused_eqs or unused_examples:
        log.warning(
            "lesson_content_unused_assets",
            lesson_code=lesson.lesson_code,
            unused_visuals=sorted(unused_visuals),
            unused_tables=sorted(unused_tables),
            unused_equations=sorted(unused_eqs),
            unused_examples=sorted(unused_examples),
        )

    unknown_fig = fig_refs - set(visual_ids)
    unknown_tab = tab_refs - set(table_ids)
    unknown_eq = eq_refs - set(eq_ids)
    unknown_ex = ex_refs - set(ex_ids)
    if unknown_fig or unknown_tab or unknown_eq or unknown_ex:
        log.warning(
            "lesson_content_dangling_asset_refs",
            lesson_code=lesson.lesson_code,
            unknown_fig=sorted(unknown_fig),
            unknown_tab=sorted(unknown_tab),
            unknown_eq=sorted(unknown_eq),
            unknown_ex=sorted(unknown_ex),
        )

    # 8. Apply — scrive content_raw + meta
    lesson.content_raw = raw
    lesson.content_tokens = usage
    lesson.content_status = "ready"
    lesson.content_generated_at = _now()
    lesson.content_error = None
    lesson.content_progress = 100
    lesson.content_progress_phase = None

    # 9. Side-effect course-level
    _recompute_course_content_status(course)


# ---------------------------------------------------------------------------
# Approve
# ---------------------------------------------------------------------------


async def approve_lesson_content(
    db: AsyncSession,
    *,
    course: Course,
    lesson: CourseLesson,
    actor_id: uuid.UUID,
) -> Course:
    """Sposta lo status della lezione da `ready` a `approved`."""
    if lesson.content_status != "ready":
        raise ConflictError(
            f"Lezione {lesson.lesson_code} non è in stato `ready` "
            f"(attuale: {lesson.content_status}).",
            code="lesson_content_not_ready",
        )

    lesson.content_status = "approved"
    lesson.content_approved_at = _now()
    _recompute_course_content_status(course)

    await write_audit(
        db,
        action="course.lesson.content.approved",
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


async def approve_all_lessons_content(
    db: AsyncSession,
    *,
    course: Course,
    actor_id: uuid.UUID,
) -> Course:
    """Approva tutte le lezioni `ready` del corso. Richiede che TUTTE
    siano `ready` o già `approved`."""
    all_lessons: list[CourseLesson] = [
        lesson for m in course.modules for lesson in m.lessons
    ]
    not_ready = [
        l for l in all_lessons
        if l.content_status not in ("ready", "approved")
    ]
    if not_ready:
        raise ConflictError(
            f"Non tutte le lezioni sono pronte. In attesa: "
            f"{', '.join(l.lesson_code for l in not_ready)}.",
            code="not_all_lessons_ready",
        )

    now = _now()
    approved_count = 0
    for lesson in all_lessons:
        if lesson.content_status == "ready":
            lesson.content_status = "approved"
            lesson.content_approved_at = now
            approved_count += 1

    _recompute_course_content_status(course)

    await write_audit(
        db,
        action="course.content.approved",
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


# ---------------------------------------------------------------------------
# Course-level status derivation
# ---------------------------------------------------------------------------


def _recompute_course_content_status(course: Course) -> None:
    """Aggiorna `course.status` in base agli stati delle lezioni.

    Regole:
    - almeno 1 lezione in `pending|processing|failed` → `content_pending`
    - TUTTE in `approved` → `content_approved`
    - TUTTE in `ready|approved` (con almeno 1 ready) → `content_ready`
    - se nessuna lezione è in stato Fase 3 (tutte `empty`) → invariato.

    NON sovrascrive lo status se è in fase precedente alla Fase 3 quando
    nessuna lezione è in lavorazione.
    """
    statuses = [
        lesson.content_status
        for m in course.modules
        for lesson in m.lessons
    ]
    if not statuses:
        return

    if any(s in ("pending", "processing", "failed") for s in statuses):
        course.status = "content_pending"
        return

    if all(s == "approved" for s in statuses):
        course.status = "content_approved"
        return

    if all(s in ("ready", "approved") for s in statuses) and any(
        s == "ready" for s in statuses
    ):
        course.status = "content_ready"
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
