"""Ordine totale delle fasi di `Course.status`.

Lo stato del corso avanza per fasi sequenziali (architecture → lessons
structure → content → slides → speech → video → avatar_video →
published/archived). Ogni service AI ricalcola lo stato del corso in
base allo stato delle lezioni della propria fase, ma DEVE essere
monotono: una volta che il corso è avanzato (es. slides approvate), il
service del Content non deve riportarlo indietro a `content_approved`.

`advance_course_status` setta il nuovo stato solo se non significa
regressione (rank del nuovo ≥ rank dell'attuale). Usato dai 6
`_recompute_course_*_status` per evitare il bug "approvo slide → poi
approvo un contenuto → corso torna a content_approved → non posso più
generare discorso".
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.errors import ConflictError

if TYPE_CHECKING:
    from app.models.course import Course
    from app.models.course_lesson import CourseLesson
    from app.models.course_module import CourseModule


# Rank totale degli stati di Course.status. L'ordine riflette le fasi
# della pipeline: dentro la stessa fase, pending < ready < approved.
# Video/avatar_video non hanno `approved` (vedi schemas/course.py).
COURSE_STATUS_RANK: dict[str, int] = {
    "draft": 0,
    "architecture_pending": 1,
    "architecture_ready": 2,
    "architecture_approved": 3,
    "lessons_structure_pending": 4,
    "lessons_structure_ready": 5,
    "lessons_structure_approved": 6,
    "content_pending": 7,
    "content_ready": 8,
    "content_approved": 9,
    "slides_pending": 10,
    "slides_ready": 11,
    "slides_approved": 12,
    "speech_pending": 13,
    "speech_ready": 14,
    "speech_approved": 15,
    "video_pending": 16,
    "video_ready": 17,
    "avatar_video_pending": 18,
    "avatar_video_ready": 19,
    "published": 20,
    "archived": 21,
}


def advance_course_status(course: Course, new_status: str) -> None:
    """Aggiorna `course.status` a `new_status` SOLO se non è una
    regressione di fase (rank monotono ≥).

    Esempi:
    - corso `slides_approved`, service Content computa `content_approved`
      → no-op (slides_approved è dopo content_approved).
    - corso `content_ready`, service Content computa `content_approved`
      → avanza a content_approved.
    - corso `slides_pending`, service Slides computa `slides_approved`
      → avanza.
    """
    current_rank = COURSE_STATUS_RANK.get(course.status, 0)
    new_rank = COURSE_STATUS_RANK.get(new_status, 0)
    if new_rank >= current_rank:
        course.status = new_status


# ---------------------------------------------------------------------------
# Gate per-unità (Fasi 2-5).
#
# Con il gating per-unità `Course.status` è un INDICATORE di avanzamento
# (milestone monotona "fase massima raggiunta"), non un lock: le
# precondizioni di generazione leggono i dati per-lezione/per-modulo.
# Gli helper qui sotto sono le uniche guardie trasversali rimaste.
# ---------------------------------------------------------------------------


def ensure_course_not_terminal(course: Course) -> None:
    """409 se il corso è in uno stato terminale (published/archived).

    Preserva l'esclusione che prima era implicita negli allow-set su
    `course.status` delle Fasi 2-5. Le Fasi 6/6b non la applicano
    (status quo: i media si possono completare anche a corso pubblicato).
    """
    if course.status in ("published", "archived"):
        raise ConflictError(
            f"Il corso è in stato terminale ({course.status}): "
            f"la generazione non è più disponibile.",
            code="course_terminal_status",
        )


def module_of_lesson(
    course: Course, lesson: CourseLesson
) -> CourseModule | None:
    """Trova il modulo della lezione tra i moduli eager-loaded del corso
    (niente lazy-load: in sessione async esploderebbe)."""
    for module in course.modules:
        if module.id == lesson.module_id:
            return module
    return None


def lesson_structure_is_ready(
    course: Course, lesson: CourseLesson
) -> bool:
    """True se la lezione può entrare in Fase 3 (dispense): il SUO modulo
    ha la struttura approvata e la lezione ha i dati di struttura Fase 2.

    Il check di esistenza (`section_outline`) copre le lezioni create a
    mano via CRUD architettura o ricreate da `regenerate_module_lessons`,
    che nascono senza struttura anche in moduli `approved`. Le lezioni di
    verifica (is_assessment) non hanno una sezione-outline propria e sono
    esenti dal check di esistenza.
    """
    module = module_of_lesson(course, lesson)
    if module is None or module.lessons_structure_status != "approved":
        return False
    return lesson.is_assessment or bool(lesson.section_outline)


def normalize_course_status_from_data(course: Course) -> str:
    """Milestone massima derivabile dagli stati per-modulo/per-lezione.

    Replica le regole dei 6 `_recompute_course_*_status` (assessment
    INCLUSE per content, ESCLUSE per slides/speech/video/avatar) presi
    in ordine di rank decrescente: la prima milestone soddisfatta vince.
    Usata dalla riattivazione da published/archived e come riferimento
    di equivalenza logica per la migrazione di normalizzazione 0034.
    Ritorna solo milestone stabili (mai `*_pending`, mai published/
    archived): gli stati in-flight si ri-derivano alla prossima azione.
    """
    modules = list(course.modules)
    lessons = [lesson for m in modules for lesson in m.lessons]
    relevant = [lesson for lesson in lessons if not lesson.is_assessment]

    def _all(items: list, attr: str, allowed: tuple[str, ...]) -> bool:
        return bool(items) and all(
            getattr(item, attr) in allowed for item in items
        )

    if _all(relevant, "avatar_video_status", ("ready", "cancelled", "empty")) and any(
        lesson.avatar_video_status == "ready" for lesson in relevant
    ):
        return "avatar_video_ready"
    if _all(relevant, "video_status", ("ready", "cancelled", "empty")) and any(
        lesson.video_status == "ready" for lesson in relevant
    ):
        return "video_ready"
    if _all(relevant, "speech_status", ("approved",)):
        return "speech_approved"
    if _all(relevant, "speech_status", ("ready", "approved")):
        return "speech_ready"
    if _all(relevant, "slides_status", ("approved",)):
        return "slides_approved"
    if _all(relevant, "slides_status", ("ready", "approved")):
        return "slides_ready"
    if _all(lessons, "content_status", ("approved",)):
        return "content_approved"
    if _all(lessons, "content_status", ("ready", "approved")):
        return "content_ready"
    if _all(modules, "lessons_structure_status", ("approved",)):
        return "lessons_structure_approved"
    if any(
        m.lessons_structure_status in ("ready", "approved") for m in modules
    ):
        return "lessons_structure_ready"
    if modules:
        # I moduli esistono → la Fase 1 è stata materializzata; l'unico
        # marcatore dell'approvazione P1 è Course.status stesso, quindi
        # qui si assume il caso più utile (sblocca la Fase 2).
        return "architecture_approved"
    return "draft"


def ensure_lesson_structure_ready(
    course: Course, lesson: CourseLesson
) -> None:
    """409 se la lezione non soddisfa il prerequisito Fase 2 → Fase 3
    (vedi `lesson_structure_is_ready`). Errori distinti per i due casi."""
    module = module_of_lesson(course, lesson)
    if module is None or module.lessons_structure_status != "approved":
        module_code = module.module_code if module else "?"
        raise ConflictError(
            f"La struttura del modulo {module_code} deve essere approvata "
            f"prima di generare le dispense delle sue lezioni.",
            code="lessons_structure_not_approved",
            meta={"module_code": module_code},
        )
    if not lesson.is_assessment and not (lesson.section_outline or []):
        raise ConflictError(
            f"La lezione {lesson.lesson_code} non ha una struttura "
            f"(obiettivi/sezioni): generala o compilala prima di "
            f"generare la dispensa.",
            code="lesson_structure_missing",
        )
