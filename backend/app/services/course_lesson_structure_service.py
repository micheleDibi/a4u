"""Servizio orchestrazione per la Fase 2 — Struttura delle lezioni (§5).

Responsabilità:
- costruire il **user prompt** della §5.2 a partire dai parametri del
  corso, dell'architettura approvata, dei documenti riassunti e
  dell'eventuale `regeneration_hint` per modulo (§9.2);
- avviare la generazione (transizione `module.lessons_structure_status`
  → `pending`);
- **materializzare** l'output validato sui 4 campi JSONB di
  `course_lesson` per le lezioni del modulo;
- approvare la struttura per modulo o per corso intero (deriva
  `course.status` da `lessons_structure_*`).

Il worker `course_lesson_structure_worker` consuma le righe dei moduli
`pending` IN PARALLELO (semaforo con cap configurabile).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.course_phase_order import advance_course_status
from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.models.course import Course
from app.models.course_document import CourseDocument
from app.models.course_lesson import CourseLesson
from app.models.course_module import CourseModule
from app.models.course_taxonomy import CourseTaxonomyTerm
from app.schemas.course_lesson_structure import LessonStructureModuleOutput
from app.services.course_architecture_service import (
    _build_documents_context,
    _term_label,
)

log = get_logger("app.course_lesson_structure")


def _now() -> datetime:
    return datetime.now(tz=UTC)


# Stati a livello modulo da cui è ammesso generare/rigenerare la struttura.
VALID_MODULE_GENERATE_FROM_STATUSES = {
    "empty",
    "pending",  # idempotent retrigger
    "ready",
    "approved",
    "failed",
}

# Stati a livello corso da cui è ammesso triggerare la Fase 2.
# Richiede che l'architettura sia almeno `approved` (oppure che il
# corso sia già in fase Fase 2).
VALID_COURSE_GENERATE_FROM_STATUSES = {
    "architecture_approved",
    "lessons_structure_pending",
    "lessons_structure_ready",
    "lessons_structure_approved",
}


# Prefix obiettivi formativi per lingua (§5.1 — soft validation in service).
# Il check è case-insensitive e accetta varianti minimali.
_OBJECTIVE_PREFIXES: dict[str, tuple[str, ...]] = {
    "it": (
        "lo studente sarà in grado di",
        "lo studente sara' in grado di",
        "lo studente sara in grado di",
    ),
    "en": (
        "the student will be able to",
        "the student should be able to",
    ),
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
    """Ricarica il corso con tutti gli eager-loads usati da CourseOut.

    Da chiamare dopo `db.commit()` per evitare lazy-load durante la
    serializzazione Pydantic (errore `MissingGreenlet`).
    """
    from sqlalchemy import select

    res = await db.execute(
        select(Course).where(Course.id == course.id).options(*_eager_full_options())
    )
    return res.scalar_one()


# ---------------------------------------------------------------------------
# Prompt building (§5.2 + §9.2 quando regenerate)
# ---------------------------------------------------------------------------


def _format_modules_map_compact(course: Course) -> str:
    """Mappa compatta dei moduli del corso, formato §5.2:
    "M1 - Titolo modulo: L1 Titolo (introduttiva), L2 Titolo, ...".
    """
    if not course.modules:
        return "(Nessun modulo)"
    lines: list[str] = []
    for m in course.modules:
        lesson_parts: list[str] = []
        for lesson in m.lessons:
            if lesson.is_assessment:
                continue  # la verifica non ha struttura didattica
            intro = " (introduttiva)" if lesson.is_introductory else ""
            lesson_parts.append(f"{lesson.lesson_code} {lesson.title}{intro}")
        lessons_str = ", ".join(lesson_parts) if lesson_parts else "(nessuna lezione)"
        lines.append(f"{m.module_code} - {m.title}: {lessons_str}")
    return "\n".join(lines)


def _format_current_module_lessons_detailed(module: CourseModule) -> str:
    """Lista dettagliata delle lezioni del modulo target con flag intro."""
    if not module.lessons:
        return "(Nessuna lezione)"
    parts: list[str] = []
    for lesson in module.lessons:
        if lesson.is_assessment:
            continue  # la verifica non ha struttura didattica
        intro = " [INTRODUTTIVA]" if lesson.is_introductory else ""
        parts.append(f"- {lesson.lesson_code}{intro}: {lesson.title}")
        if lesson.summary:
            parts.append(f"  Sintesi: {lesson.summary}")
    return "\n".join(parts)


def _format_current_module_phase2(module: CourseModule) -> str:
    """Serializza la struttura attuale del modulo (Fase 2) per il prompt
    di rigenerazione (§9.2). Usa i 4 campi JSONB delle lezioni."""
    parts: list[str] = []
    for lesson in module.lessons:
        if lesson.is_assessment:
            continue  # la verifica non ha struttura didattica
        parts.append(f"### Lezione {lesson.lesson_code} — {lesson.title}")
        if lesson.is_introductory:
            parts.append("(Lezione introduttiva)")
        objs = lesson.learning_objectives or []
        if objs:
            parts.append("Obiettivi formativi:")
            for o in objs:
                parts.append(f"  - {o}")
        topics = lesson.mandatory_topics or []
        if topics:
            parts.append("Temi obbligatori:")
            for t in topics:
                tid = t.get("topic_id", "?") if isinstance(t, dict) else "?"
                ttitle = t.get("topic", "") if isinstance(t, dict) else ""
                trat = t.get("rationale", "") if isinstance(t, dict) else ""
                parts.append(f"  - [{tid}] {ttitle} — {trat}")
        prereqs = lesson.prerequisites or []
        if prereqs:
            parts.append("Prerequisiti:")
            for p in prereqs:
                parts.append(f"  - {p}")
        outline = lesson.section_outline or []
        if outline:
            parts.append("Scaletta:")
            for s in outline:
                if not isinstance(s, dict):
                    continue
                sid = s.get("section_id", "?")
                stitle = s.get("title", "")
                spurp = s.get("purpose", "")
                covers = s.get("covers_topic_ids") or []
                parts.append(f"  - [{sid}] {stitle} — {spurp}  (copre: {', '.join(covers)})")
        parts.append("")
    return "\n".join(parts) if parts else "(Nessuna struttura precedente.)"


def build_user_prompt(course: Course, module: CourseModule) -> str:
    """Costruisce il messaggio utente conforme al template §5.2.

    Pre-condizione: `course` e `module` sono stati caricati con
    eager-load di taxonomies, documents, modules, lessons.
    """
    settings = get_settings()
    lang = course.language_code

    documents_context = _build_documents_context(
        list(course.documents),
        settings.course_lesson_structure_documents_context_max_chars,
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
        f"- Destinatari: {_term_label(course.destinatari, lang)}",
        f"- Livello di conoscenza del pubblico: {_term_label(course.livello_conoscenza, lang)}",
        f"- Livello EQF: {_term_label(course.livello_eqf, lang)}",
        f"- Ruolo del docente: {_term_label(course.ruolo_docente, lang)}",
        "",
        "## Architettura completa del corso (approvata)",
        "",
        course.course_overview or "(Overview non disponibile.)",
        "",
        f"Razionale pedagogico: {course.pedagogical_rationale or '(non disponibile)'}",
        "",
        "Mappa dei moduli e delle lezioni:",
        _format_modules_map_compact(course),
        "",
        "## Modulo da strutturare ORA",
        "",
        f"ID: {module.module_code}",
        f"Titolo: {module.title}",
        f"Descrizione: {module.description or '(non specificata)'}",
        "",
        "Lezioni del modulo (con flag introduttiva):",
        _format_current_module_lessons_detailed(module),
        "",
        "## Documenti di riferimento (estratti rilevanti)",
        "",
        documents_context,
        "",
        "## Compito",
        "",
        f"Per OGNI lezione del modulo `{module.module_code}` produci:",
        "- 3-6 obiettivi formativi",
        "- 3-7 temi obbligatori, ognuno con topic_id e rationale",
        "- 0-5 prerequisiti",
        "- una section outline di 3-7 sezioni",
        "",
        "Per la lezione introduttiva (se presente nel modulo) applica la",
        "struttura speciale descritta nelle istruzioni di sistema.",
        "",
        "Restituisci il risultato nel formato JSON richiesto.",
    ]

    if module.lessons_structure_regeneration_hint or module.lessons_structure_raw:
        blocks.extend(
            [
                "",
                "## Versione attuale del modulo (DA RIVEDERE)",
                "",
                _format_current_module_phase2(module),
            ]
        )
        if module.lessons_structure_regeneration_hint:
            blocks.extend(
                [
                    "",
                    "## Indicazioni del docente per la rigenerazione",
                    "",
                    module.lessons_structure_regeneration_hint,
                ]
            )

    return "\n".join(blocks)


def is_regeneration(module: CourseModule) -> bool:
    """Determina se la chiamata corrente è una rigenerazione (§9.2)."""
    return bool(
        module.lessons_structure_raw
        or module.lessons_structure_regeneration_hint
        or any(
            (lesson.learning_objectives or lesson.mandatory_topics)
            for lesson in module.lessons
        )
    )


# ---------------------------------------------------------------------------
# State transitions: request generation
# ---------------------------------------------------------------------------


async def request_module_generation(
    db: AsyncSession,
    *,
    course: Course,
    module: CourseModule,
    actor_id: uuid.UUID,
    regeneration_hint: str | None,
) -> Course:
    """Sposta lo status del modulo a `pending` e annota l'eventuale hint.
    Il worker prenderà la riga al prossimo tick e la elabora in parallelo
    con gli altri moduli pending."""
    if course.status not in VALID_COURSE_GENERATE_FROM_STATUSES:
        raise ConflictError(
            f"Stato corso non valido per Fase 2: {course.status}",
            code="invalid_course_status",
        )
    if module.lessons_structure_status not in VALID_MODULE_GENERATE_FROM_STATUSES:
        raise ConflictError(
            f"Modulo {module.module_code} non in stato valido: "
            f"{module.lessons_structure_status}",
            code="invalid_module_lessons_structure_status",
        )

    module.lessons_structure_status = "pending"
    module.lessons_structure_error = None
    module.lessons_structure_progress = 0
    module.lessons_structure_progress_phase = None
    module.lessons_structure_regeneration_hint = (
        regeneration_hint.strip() if regeneration_hint else None
    )

    # Side-effect course-level: se il corso è in `architecture_approved`
    # (o stato terminale Fase 2), riportalo a `lessons_structure_pending`
    # per riflettere la lavorazione in corso.
    _recompute_course_lessons_structure_status(course)

    await write_audit(
        db,
        action="course.module.lessons_structure.generate.requested",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course_module",
        target_id=str(module.id),
        metadata={
            "course_id": str(course.id),
            "module_code": module.module_code,
            "is_regeneration": bool(
                module.lessons_structure_raw
                or module.lessons_structure_regeneration_hint
            ),
            "hint": (
                module.lessons_structure_regeneration_hint[:200]
                if module.lessons_structure_regeneration_hint
                else None
            ),
        },
    )
    await db.commit()
    return await _refresh_full(db, course)


async def request_all_modules_generation(
    db: AsyncSession,
    *,
    course: Course,
    actor_id: uuid.UUID,
    regeneration_hint: str | None,
) -> Course:
    """Marca TUTTI i moduli del corso come `pending`. Il worker li
    elabora in parallelo (cap configurabile)."""
    if course.status not in VALID_COURSE_GENERATE_FROM_STATUSES:
        raise ConflictError(
            f"Stato corso non valido per Fase 2: {course.status}",
            code="invalid_course_status",
        )
    if not course.modules:
        raise ConflictError(
            "Il corso non ha moduli — completa prima la Fase 1 (Architettura).",
            code="no_modules_to_generate",
        )

    hint_clean = regeneration_hint.strip() if regeneration_hint else None
    for m in course.modules:
        m.lessons_structure_status = "pending"
        m.lessons_structure_error = None
        m.lessons_structure_progress = 0
        m.lessons_structure_progress_phase = None
        m.lessons_structure_regeneration_hint = hint_clean

    # Regressione esplicita: l'utente sta richiedendo (ri)generazione.
    # NON usiamo advance_course_status qui — vogliamo riallineare la
    # fase del corso a Fase 2 anche se era più avanti.
    course.status = "lessons_structure_pending"

    await write_audit(
        db,
        action="course.lessons_structure.generate.requested",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course",
        target_id=str(course.id),
        metadata={
            "modules_count": len(course.modules),
            "hint": hint_clean[:200] if hint_clean else None,
        },
    )
    await db.commit()
    return await _refresh_full(db, course)


# ---------------------------------------------------------------------------
# Materialization: scrittura sui 4 campi JSONB di course_lesson
# ---------------------------------------------------------------------------


def _validate_objective_prefix(
    objectives: list[str], language_code: str
) -> list[str]:
    """Ritorna la lista di obiettivi che NON iniziano con il prefix per
    la lingua data. Lista vuota = tutto OK. Soft validation: se la lingua
    non è in mappa, ritorna sempre vuoto (non blocca)."""
    prefixes = _OBJECTIVE_PREFIXES.get(language_code.lower())
    if not prefixes:
        return []
    bad: list[str] = []
    for obj in objectives:
        ostrip = obj.strip().lower()
        if not any(ostrip.startswith(p) for p in prefixes):
            bad.append(obj)
    return bad


async def materialize_module_structure(
    db: AsyncSession,
    *,
    course: Course,
    module: CourseModule,
    output: LessonStructureModuleOutput,
    raw: dict[str, Any],
    usage: dict[str, Any],
) -> None:
    """Valida (§5.4) e scrive i 4 campi JSONB sulle lezioni del modulo.

    NOTA: il caller (worker o sync endpoint) deve avere già caricato
    `module.lessons` e `course.modules` con eager-load.
    """
    # La lezione-verifica (is_assessment) non ha struttura didattica:
    # esclusa dalla Fase 2 (non passata all'AI, non conteggiata qui).
    didactic_lessons = [l for l in module.lessons if not l.is_assessment]

    # 1. Lessons count match
    if len(output.lessons) != len(didactic_lessons):
        raise ConflictError(
            f"Numero lezioni in output ({len(output.lessons)}) ≠ "
            f"atteso ({len(didactic_lessons)}) per modulo {module.module_code}.",
            code="lessons_structure_lessons_count_mismatch",
        )

    # 2. lesson_id → lesson_code matching: l'AI usa gli ID di Fase 1
    #    (es. "M1.L1"). Costruisci una mappa per il merge.
    lessons_by_code = {l.lesson_code: l for l in didactic_lessons}

    # 3. Validazione per-lezione + apply
    for ai_lesson in output.lessons:
        target = lessons_by_code.get(ai_lesson.lesson_id)
        if target is None:
            raise ConflictError(
                f"L'AI ha prodotto lesson_id `{ai_lesson.lesson_id}` "
                f"non presente nel modulo {module.module_code}.",
                code="lessons_structure_lesson_id_not_found",
            )

        # 3a. Prefisso obiettivi (soft validation, blocca solo se IT/EN
        #     e violazione completa)
        bad = _validate_objective_prefix(
            ai_lesson.learning_objectives, course.language_code
        )
        if bad and len(bad) == len(ai_lesson.learning_objectives):
            raise ConflictError(
                f"Tutti gli obiettivi della lezione {ai_lesson.lesson_id} "
                f"non iniziano con il prefisso atteso per lingua "
                f"{course.language_code}.",
                code="lessons_structure_objectives_prefix_invalid",
            )

        # 3b. topic_id univoci
        topic_ids = [t.topic_id for t in ai_lesson.mandatory_topics]
        if len(set(topic_ids)) != len(topic_ids):
            raise ConflictError(
                f"topic_id duplicati nella lezione {ai_lesson.lesson_id}.",
                code="lessons_structure_duplicate_topic_id",
            )

        # 3c. section_id univoci
        section_ids = [s.section_id for s in ai_lesson.section_outline]
        if len(set(section_ids)) != len(section_ids):
            raise ConflictError(
                f"section_id duplicati nella lezione {ai_lesson.lesson_id}.",
                code="lessons_structure_duplicate_section_id",
            )

        # 3d. covers_topic_ids referenziano topic_id esistenti
        topic_id_set = set(topic_ids)
        for s in ai_lesson.section_outline:
            for cid in s.covers_topic_ids:
                if cid not in topic_id_set:
                    raise ConflictError(
                        f"section {s.section_id} referenzia topic_id "
                        f"`{cid}` inesistente in lezione "
                        f"{ai_lesson.lesson_id}.",
                        code="lessons_structure_invalid_covers_reference",
                    )

        # 3e. Coverage: l'unione dei covers deve coprire tutti i topic
        covered = {cid for s in ai_lesson.section_outline for cid in s.covers_topic_ids}
        uncovered = topic_id_set - covered
        if uncovered:
            raise ConflictError(
                f"Lezione {ai_lesson.lesson_id}: topic non coperti da "
                f"alcuna sezione: {sorted(uncovered)}.",
                code="lessons_structure_topics_uncovered",
            )

        # 4. Apply — scrive i 4 JSONB
        target.learning_objectives = list(ai_lesson.learning_objectives)
        target.mandatory_topics = [t.model_dump() for t in ai_lesson.mandatory_topics]
        target.prerequisites = list(ai_lesson.prerequisites)
        target.section_outline = [s.model_dump() for s in ai_lesson.section_outline]

    # 5. Module-level update
    module.lessons_structure_raw = raw
    module.lessons_structure_tokens = usage
    module.lessons_structure_status = "ready"
    module.lessons_structure_generated_at = _now()
    module.lessons_structure_error = None
    module.lessons_structure_progress = 100
    module.lessons_structure_progress_phase = None

    # 6. Side-effect course-level
    _recompute_course_lessons_structure_status(course)


# ---------------------------------------------------------------------------
# Approve
# ---------------------------------------------------------------------------


async def approve_module_structure(
    db: AsyncSession,
    *,
    course: Course,
    module: CourseModule,
    actor_id: uuid.UUID,
) -> Course:
    """Sposta lo status del modulo da `ready` a `approved`."""
    if module.lessons_structure_status != "ready":
        raise ConflictError(
            f"Modulo {module.module_code} non è in stato `ready` "
            f"(attuale: {module.lessons_structure_status}).",
            code="module_lessons_structure_not_ready",
        )

    module.lessons_structure_status = "approved"
    module.lessons_structure_approved_at = _now()
    _recompute_course_lessons_structure_status(course)

    await write_audit(
        db,
        action="course.module.lessons_structure.approved",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course_module",
        target_id=str(module.id),
        metadata={
            "course_id": str(course.id),
            "module_code": module.module_code,
        },
    )
    await db.commit()
    return await _refresh_full(db, course)


async def approve_all_modules_structure(
    db: AsyncSession,
    *,
    course: Course,
    actor_id: uuid.UUID,
) -> Course:
    """Approva tutti i moduli `ready` del corso. Richiede che TUTTI i
    moduli siano `ready` o già `approved`."""
    not_ready = [
        m for m in course.modules
        if m.lessons_structure_status not in ("ready", "approved")
    ]
    if not_ready:
        raise ConflictError(
            f"Non tutti i moduli sono pronti per l'approvazione. "
            f"Moduli in attesa: {', '.join(m.module_code for m in not_ready)}.",
            code="not_all_modules_ready",
        )

    now = _now()
    approved_count = 0
    for m in course.modules:
        if m.lessons_structure_status == "ready":
            m.lessons_structure_status = "approved"
            m.lessons_structure_approved_at = now
            approved_count += 1

    _recompute_course_lessons_structure_status(course)

    await write_audit(
        db,
        action="course.lessons_structure.approved",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course",
        target_id=str(course.id),
        metadata={
            "modules_count": len(course.modules),
            "newly_approved": approved_count,
        },
    )
    await db.commit()
    return await _refresh_full(db, course)


# ---------------------------------------------------------------------------
# Course-level status derivation
# ---------------------------------------------------------------------------


def _recompute_course_lessons_structure_status(course: Course) -> None:
    """Aggiorna `course.status` in base agli stati dei moduli.

    Regole:
    - almeno 1 modulo in `pending|processing|failed` → `lessons_structure_pending`
    - TUTTI i moduli in `approved` → `lessons_structure_approved`
    - TUTTI i moduli in `ready|approved` (con almeno 1 ready) → `lessons_structure_ready`
    - se nessun modulo è in stato Fase 2 (tutti `empty`) → status invariato.

    NON sovrascrive lo status se è in fase precedente alla Fase 2 quando
    nessun modulo è in lavorazione (caso anomalo, lasciato all'utente).
    """
    statuses = [m.lessons_structure_status for m in course.modules]
    if not statuses:
        return

    if any(s in ("pending", "processing", "failed") for s in statuses):
        advance_course_status(course, "lessons_structure_pending")
        return

    if all(s == "approved" for s in statuses):
        advance_course_status(course, "lessons_structure_approved")
        return

    if all(s in ("ready", "approved") for s in statuses) and any(
        s == "ready" for s in statuses
    ):
        advance_course_status(course, "lessons_structure_ready")
        return

    # Tutti `empty` o mix con `empty` → non triggerato, lascia status corrente.


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


async def load_course_full(
    db: AsyncSession, *, course_id: uuid.UUID
) -> Course | None:
    from sqlalchemy import select

    res = await db.execute(
        select(Course)
        .where(Course.id == course_id)
        .options(*_eager_full_options())
    )
    return res.scalar_one_or_none()


async def get_module_or_404(
    db: AsyncSession, *, course: Course, module_id: uuid.UUID
) -> CourseModule:
    for m in course.modules:
        if m.id == module_id:
            return m
    raise NotFoundError(
        f"Modulo {module_id} non trovato nel corso.",
        code="module_not_found",
    )


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
