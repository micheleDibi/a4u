"""Servizio orchestrazione per la Fase 1 — Architettura del corso.

Responsabilità:
- costruire il **user prompt** della §4.2 a partire dai parametri del
  corso, dei documenti riassunti e dell'eventuale `regeneration_hint`;
- avviare la generazione (transizione status `draft|architecture_ready|
  architecture_approved` → `architecture_pending`);
- **materializzare** l'output validato in `course_module` + `course_lesson`;
- approvare l'architettura (`architecture_ready` → `architecture_approved`).

Il worker `course_architecture_worker` consuma le righe `architecture_pending`.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.errors import ConflictError
from app.core.logging import get_logger
from app.models.course import Course
from app.models.course_document import CourseDocument
from app.models.course_lesson import CourseLesson
from app.models.course_module import CourseModule
from app.models.course_taxonomy import CourseTaxonomyTerm
from app.schemas.course_architecture import ArchitectureOutput

log = get_logger("app.course_architecture")


def _now() -> datetime:
    return datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# Lezione di verifica delle competenze (ultima lezione di ogni modulo)
# ---------------------------------------------------------------------------

# Titolo standard della lezione-verifica, per lingua del corso. Lingue
# non mappate → inglese. Il titolo resta comunque modificabile a mano.
_ASSESSMENT_LESSON_TITLES: dict[str, str] = {
    "it": "Verifica delle competenze",
    "en": "Competence assessment",
    "es": "Evaluación de competencias",
    "fr": "Évaluation des compétences",
    "de": "Kompetenzüberprüfung",
    "pt": "Avaliação de competências",
    "nl": "Competentietoets",
}


def _assessment_lesson_title(language_code: str) -> str:
    code = (language_code or "en").lower().split("-")[0]
    return _ASSESSMENT_LESSON_TITLES.get(
        code, _ASSESSMENT_LESSON_TITLES["en"]
    )


def _assessment_enabled(course: Course) -> bool:
    """True se ogni modulo deve avere una lezione-verifica come ultima.

    Richiede `lessons_per_module >= 2`: un modulo di sola verifica, senza
    lezioni didattiche, non avrebbe senso.
    """
    return (
        bool(course.assessment_lesson_enabled)
        and course.lessons_per_module >= 2
    )


def _architecture_lessons_per_module(course: Course) -> int:
    """Numero di lezioni DIDATTICHE che l'AI deve generare per modulo.

    Quando la verifica è attiva la lezione-verifica viene aggiunta dal
    codice di materializzazione, quindi l'AI ne produce una in meno.
    """
    if _assessment_enabled(course):
        return course.lessons_per_module - 1
    return course.lessons_per_module


# ---------------------------------------------------------------------------
# Prompt building (§4.2)
# ---------------------------------------------------------------------------


def _term_label(term: CourseTaxonomyTerm | None, language_code: str) -> str:
    """Estrae l'etichetta human-readable di un termine tassonomia,
    con fallback IT → EN → slug."""
    if term is None:
        return "(non specificato)"
    labels = term.labels or {}
    return (
        labels.get(language_code)
        or labels.get("it")
        or labels.get("en")
        or term.slug
    )


def _format_document_summary_for_prompt(
    doc: CourseDocument, max_chars: int
) -> str | None:
    """Formatta il riassunto strutturato di un documento per inclusione
    nel prompt. Salta documenti senza summary `ready`."""
    if doc.summary_status != "ready" or not doc.summary:
        return None
    s = doc.summary
    parts: list[str] = [
        f"## Documento: {doc.filename_original}",
        f"Lingua rilevata: {s.get('detected_language', '?')}",
        "",
        "### Abstract",
        str(s.get("abstract") or "").strip(),
    ]
    structure = s.get("structure_outline") or []
    if structure:
        parts.append("")
        parts.append("### Struttura")
        for item in structure[:25]:
            parts.append(f"- {item}")
    concepts = s.get("key_concepts") or []
    if concepts:
        parts.append("")
        parts.append("### Concetti chiave")
        for c in concepts[:25]:
            name = c.get("name", "")
            expl = c.get("explanation", "")
            parts.append(f"- **{name}**: {expl}")
    definitions = s.get("definitions") or []
    if definitions:
        parts.append("")
        parts.append("### Definizioni")
        for d in definitions[:30]:
            parts.append(f"- **{d.get('term','')}**: {d.get('definition','')}")
    tags = s.get("didactic_relevance_tags") or []
    if tags:
        parts.append("")
        parts.append(f"### Tag rilevanza didattica: {', '.join(tags)}")
    text = "\n".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... (troncato)"
    return text


def _build_documents_context(
    docs: list[CourseDocument], total_max_chars: int
) -> str:
    """Concatena i riassunti dei documenti `ready` con un budget totale di
    caratteri. Se la somma dei riassunti supera il budget, riduce
    proporzionalmente per documento."""
    ready_docs = [d for d in docs if d.summary_status == "ready" and d.summary]
    if not ready_docs:
        return "(Nessun documento di riferimento elaborato.)"

    per_doc_budget = max(2000, total_max_chars // max(1, len(ready_docs)))
    chunks: list[str] = []
    for d in ready_docs:
        formatted = _format_document_summary_for_prompt(d, per_doc_budget)
        if formatted:
            chunks.append(formatted)
    text = "\n\n---\n\n".join(chunks)
    if len(text) > total_max_chars:
        text = text[:total_max_chars] + "\n... (troncato)"
    return text


def build_user_prompt(course: Course) -> str:
    """Costruisce il messaggio utente conforme al template §4.2.

    Pre-condizione: `course` è stato caricato con eager-load di tutti
    i taxonomy_terms e i documents.
    """
    settings = get_settings()
    lang = course.language_code
    arch_lessons = _architecture_lessons_per_module(course)

    argomenti = course.argomenti_chiave or []
    argomenti_lista = (
        "\n".join(f"  - {a}" for a in argomenti)
        if argomenti
        else "  (nessuno specificato)"
    )

    documents_context = _build_documents_context(
        list(course.documents),
        settings.course_architecture_documents_context_max_chars,
    )

    blocks = [
        "## Parametri del corso",
        "",
        f"- Titolo: {course.title}",
        f"- Obiettivi del corso: {course.objectives or '(non specificati)'}",
        f"- Categoria disciplinare: {_term_label(course.categoria, lang)}",
        "- Argomenti chiave:",
        argomenti_lista,
        f"- Stile di insegnamento: {_term_label(course.stile_insegnamento, lang)}",
        f"- Profondità del contenuto: {_term_label(course.profondita_contenuto, lang)}",
        f"- Numero di moduli: {course.modules_count}",
        f"- Numero di lezioni didattiche per modulo: {arch_lessons}",
        f"- Lingua: {lang}",
        f"- Ruolo del docente: {_term_label(course.ruolo_docente, lang)}",
        f"- Dimensione del pubblico: {_term_label(course.dimensione_pubblico, lang)}",
        f"- Livello di conoscenza del pubblico: {_term_label(course.livello_conoscenza, lang)}",
        f"- Destinatari: {_term_label(course.destinatari, lang)}",
        f"- Livello EQF: {_term_label(course.livello_eqf, lang)}",
        "",
        "## Documenti di riferimento",
        "",
        documents_context,
        "",
        "## Compito",
        "",
        "Progetta l'architettura del corso producendo:",
        f"- ESATTAMENTE {course.modules_count} moduli",
        f"- per OGNI modulo ESATTAMENTE {arch_lessons} lezioni",
        "- la PRIMA lezione del PRIMO modulo (M1.L1) marcata come introduttiva",
        "  con bibliografia consigliata",
        *(
            [
                "- NOTA: oltre a queste, ogni modulo avrà una lezione finale "
                "di verifica delle competenze generata automaticamente: NON "
                "includerla nell'output (genera solo le lezioni didattiche).",
            ]
            if _assessment_enabled(course)
            else []
        ),
        "",
        "Restituisci il risultato nel formato JSON richiesto.",
    ]

    if course.architecture_regeneration_hint:
        blocks.extend(
            [
                "",
                "## Versione attuale dell'architettura (DA RIVEDERE)",
                "",
                _format_current_architecture_for_prompt(course),
                "",
                "## Indicazioni del docente per la rigenerazione",
                "",
                course.architecture_regeneration_hint,
            ]
        )

    return "\n".join(blocks)


def _format_current_architecture_for_prompt(course: Course) -> str:
    """Serializza la versione attuale dell'architettura per il prompt
    di rigenerazione (vedi §9.2 — pattern simile)."""
    if not course.modules:
        return "(Nessuna architettura precedente disponibile.)"
    parts: list[str] = []
    if course.course_overview:
        parts.append(f"Overview: {course.course_overview}")
    if course.pedagogical_rationale:
        parts.append(f"Razionale: {course.pedagogical_rationale}")
        parts.append("")
    for m in course.modules:
        parts.append(f"### {m.module_code} — {m.title}")
        if m.description:
            parts.append(m.description)
        for lesson in m.lessons:
            # La lezione-verifica è ricreata dal codice ad ogni
            # materializzazione: non rimandarla all'AI in rigenerazione.
            if lesson.is_assessment:
                continue
            intro_marker = " (introduttiva)" if lesson.is_introductory else ""
            parts.append(f"- {lesson.lesson_code}{intro_marker}: {lesson.title}")
            if lesson.summary:
                parts.append(f"  → {lesson.summary}")
        parts.append("")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Eager loading
# ---------------------------------------------------------------------------


def _eager_full_options() -> list:
    """Carica tutto il necessario per costruire il prompt + restituire l'output."""
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
    ]


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------


VALID_GENERATE_FROM_STATUSES = {
    "draft",
    "architecture_pending",  # idempotent retrigger
    "architecture_ready",
    "architecture_approved",
}


async def request_generation(
    db: AsyncSession,
    *,
    course: Course,
    actor_id: uuid.UUID,
    regeneration_hint: str | None,
) -> Course:
    """Sposta lo status a `architecture_pending` e annota l'eventuale hint.
    Il worker prenderà la riga al prossimo tick.

    I documenti di riferimento sono opzionali: se nessuno è in stato
    `ready`, il prompt riceverà comunque un placeholder esplicito e l'AI
    genererà l'architettura solo a partire da titolo, obiettivi, argomenti
    chiave e parametri pedagogici.
    """
    if course.status not in VALID_GENERATE_FROM_STATUSES:
        raise ConflictError(
            f"Stato corso non valido per generare architettura: {course.status}",
            code="invalid_course_status",
        )

    course.status = "architecture_pending"
    course.architecture_error = None
    course.architecture_regeneration_hint = (
        regeneration_hint.strip() if regeneration_hint else None
    )
    await write_audit(
        db,
        action="course.architecture.generate.requested",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course",
        target_id=str(course.id),
        metadata={
            "regeneration_hint": (
                course.architecture_regeneration_hint[:200]
                if course.architecture_regeneration_hint
                else None
            ),
        },
    )
    await db.commit()
    await db.refresh(course)
    return course


async def materialize_architecture(
    db: AsyncSession,
    *,
    course: Course,
    architecture: ArchitectureOutput,
    raw: dict,
    usage: dict,
) -> None:
    """Sostituisce le righe modules/lessons con quelle generate.

    NOTA: caller deve aver già eseguito le validazioni §4.4. Qui
    eseguiamo comunque check finali per evitare scrivere stato corrotto.
    """
    # Validazioni §4.4
    if len(architecture.modules) != course.modules_count:
        raise ConflictError(
            f"Numero moduli generati ({len(architecture.modules)}) ≠ "
            f"atteso ({course.modules_count}).",
            code="architecture_modules_count_mismatch",
        )
    arch_lessons = _architecture_lessons_per_module(course)
    for m_idx, m in enumerate(architecture.modules, start=1):
        if len(m.lessons) != arch_lessons:
            raise ConflictError(
                f"Modulo {m.module_id}: {len(m.lessons)} lezioni ≠ "
                f"atteso ({arch_lessons}).",
                code="architecture_lessons_count_mismatch",
            )
        for l_idx, lesson in enumerate(m.lessons, start=1):
            is_first_first = (m_idx == 1 and l_idx == 1)
            if is_first_first:
                if not lesson.is_introductory:
                    raise ConflictError(
                        "M1.L1 deve avere is_introductory=true.",
                        code="architecture_intro_flag_invalid",
                    )
                if not lesson.recommended_bibliography:
                    raise ConflictError(
                        "M1.L1 deve avere bibliografia non vuota.",
                        code="architecture_intro_bibliography_missing",
                    )
            else:
                if lesson.is_introductory:
                    raise ConflictError(
                        f"Solo M1.L1 può essere introduttiva: violato da "
                        f"{lesson.lesson_id}.",
                        code="architecture_intro_flag_invalid",
                    )
                if lesson.recommended_bibliography:
                    raise ConflictError(
                        f"Solo M1.L1 può avere bibliografia: violato da "
                        f"{lesson.lesson_id}.",
                        code="architecture_bibliography_only_intro",
                    )
            for entry in lesson.recommended_bibliography:
                if (
                    entry.source == "general_knowledge_suggestion"
                    and entry.confidence != "to_verify"
                ):
                    raise ConflictError(
                        "Voci `general_knowledge_suggestion` devono avere "
                        "confidence=`to_verify`.",
                        code="architecture_bibliography_confidence_invalid",
                    )

    # Drop pre-existing materialized rows (cascade su course_lesson via FK).
    if course.modules:
        for m in list(course.modules):
            await db.delete(m)
        await db.flush()

    # Crea le nuove righe.
    for m_idx, m in enumerate(architecture.modules, start=1):
        module = CourseModule(
            course_id=course.id,
            position=m_idx,
            module_code=m.module_id.strip()[:20] or f"M{m_idx}",
            title=m.title.strip()[:300],
            description=m.description.strip(),
        )
        db.add(module)
        await db.flush()
        for l_idx, lesson in enumerate(m.lessons, start=1):
            db.add(
                CourseLesson(
                    module_id=module.id,
                    course_id=course.id,
                    position=l_idx,
                    lesson_code=lesson.lesson_id.strip()[:30]
                    or f"M{m_idx}.L{l_idx}",
                    title=lesson.title.strip()[:300],
                    summary=lesson.summary.strip(),
                    is_introductory=lesson.is_introductory,
                    recommended_bibliography=[
                        b.model_dump() for b in lesson.recommended_bibliography
                    ],
                )
            )
        # Ultima lezione del modulo = verifica delle competenze (aggiunta
        # dal codice, non dall'AI). Vedi `_assessment_enabled`.
        if _assessment_enabled(course):
            assessment_position = len(m.lessons) + 1
            db.add(
                CourseLesson(
                    module_id=module.id,
                    course_id=course.id,
                    position=assessment_position,
                    lesson_code=f"{module.module_code}.L{assessment_position}",
                    title=_assessment_lesson_title(course.language_code),
                    summary="",
                    is_introductory=False,
                    is_assessment=True,
                    recommended_bibliography=[],
                )
            )

    # Aggiorna i metadati di alto livello sul corso.
    course.course_overview = architecture.course_overview.strip()
    course.pedagogical_rationale = architecture.pedagogical_rationale.strip()
    course.architecture_raw = raw
    course.architecture_tokens = usage
    course.architecture_generated_at = _now()
    course.architecture_error = None
    course.status = "architecture_ready"


async def approve_architecture(
    db: AsyncSession, *, course: Course, actor_id: uuid.UUID
) -> Course:
    """Sposta lo status da `architecture_ready` ad `architecture_approved`."""
    if course.status != "architecture_ready":
        raise ConflictError(
            f"Stato non valido per approvare architettura: {course.status}",
            code="invalid_course_status",
        )
    course.status = "architecture_approved"
    await write_audit(
        db,
        action="course.architecture.approved",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course",
        target_id=str(course.id),
        metadata={"modules_count": len(course.modules)},
    )
    await db.commit()
    await db.refresh(course)
    return course


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------


async def get_course_with_architecture(
    db: AsyncSession, *, course_id: uuid.UUID
) -> Course | None:
    res = await db.execute(
        select(Course)
        .where(Course.id == course_id)
        .options(*_eager_full_options())
    )
    return res.scalar_one_or_none()
