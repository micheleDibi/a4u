from __future__ import annotations

from typing import ClassVar

import pytest

from app.core.errors import ConflictError
from app.services import (
    course_lesson_content_service as content_svc,
)
from app.services import (
    course_lesson_slides_service as slides_svc,
)
from app.services import (
    course_lesson_speech_service as speech_svc,
)
from tests.course_builders import build_course, find_lesson

pytestmark = pytest.mark.asyncio


async def _load(svc, db, course_id):
    course = await svc.load_course_full(db, course_id=course_id)
    assert course is not None
    return course


# ---------------------------------------------------------------------------
# Caratterizzazione dei gate per-step attuali su course.status.
# Questi test fotografano il comportamento PRIMA del passaggio al gating
# per-unità: i singoli casi vengono aggiornati insieme alle PR che li
# cambiano deliberatamente.
# ---------------------------------------------------------------------------


async def test_content_regeneration_ok_beyond_content_phase(seeded_db):
    """Gating per-unità: la dispensa di una lezione si rigenera anche a
    corso avanzato (prima: 409 invalid_course_status oltre content_*)."""
    course_id, _org, user = await build_course(
        seeded_db,
        status="speech_pending",
        content_status="approved",
        slides_status="approved",
    )
    course = await _load(content_svc, seeded_db, course_id)
    lesson = find_lesson(course, "M1.L1")
    refreshed = await content_svc.request_lesson_generation(
        seeded_db,
        course=course,
        lesson=lesson,
        actor_id=user.id,
        regeneration_hint=None,
    )
    assert find_lesson(refreshed, "M1.L1").content_status == "pending"
    # L'indicatore non regredisce (advance_course_status monotono).
    assert refreshed.status == "speech_pending"


async def test_content_generation_blocked_on_terminal_course(seeded_db):
    for status in ("published", "archived"):
        course_id, _org, user = await build_course(
            seeded_db, status=status, content_status="approved"
        )
        course = await _load(content_svc, seeded_db, course_id)
        lesson = find_lesson(course, "M1.L1")
        with pytest.raises(ConflictError) as exc:
            await content_svc.request_lesson_generation(
                seeded_db,
                course=course,
                lesson=lesson,
                actor_id=user.id,
                regeneration_hint=None,
            )
        assert exc.value.code == "course_terminal_status"


async def test_content_generation_gated_on_own_module_only(seeded_db):
    """Per-modulo: il modulo non approvato blocca SOLO le proprie
    lezioni; quelle degli altri moduli procedono."""
    course_id, _org, user = await build_course(
        seeded_db, status="lessons_structure_ready"
    )
    course = await _load(content_svc, seeded_db, course_id)
    module2 = next(m for m in course.modules if m.module_code == "M2")
    module2.lessons_structure_status = "ready"  # non ancora approvata
    await seeded_db.flush()

    blocked = find_lesson(course, "M2.L1")
    with pytest.raises(ConflictError) as exc:
        await content_svc.request_lesson_generation(
            seeded_db,
            course=course,
            lesson=blocked,
            actor_id=user.id,
            regeneration_hint=None,
        )
    assert exc.value.code == "lessons_structure_not_approved"

    allowed = find_lesson(course, "M1.L1")
    refreshed = await content_svc.request_lesson_generation(
        seeded_db,
        course=course,
        lesson=allowed,
        actor_id=user.id,
        regeneration_hint=None,
    )
    assert find_lesson(refreshed, "M1.L1").content_status == "pending"


async def test_content_generation_blocked_without_lesson_structure(seeded_db):
    """Lezione creata a mano (o ricreata da regenerate_module_lessons)
    in modulo approvato ma senza section_outline → 409."""
    course_id, _org, user = await build_course(
        seeded_db, status="lessons_structure_approved", with_structure=False
    )
    course = await _load(content_svc, seeded_db, course_id)
    lesson = find_lesson(course, "M1.L1")
    with pytest.raises(ConflictError) as exc:
        await content_svc.request_lesson_generation(
            seeded_db,
            course=course,
            lesson=lesson,
            actor_id=user.id,
            regeneration_hint=None,
        )
    assert exc.value.code == "lesson_structure_missing"


async def test_generate_all_content_skips_unready_modules(seeded_db):
    course_id, _org, user = await build_course(
        seeded_db, status="lessons_structure_ready"
    )
    course = await _load(content_svc, seeded_db, course_id)
    module2 = next(m for m in course.modules if m.module_code == "M2")
    module2.lessons_structure_status = "ready"
    await seeded_db.flush()

    refreshed = await content_svc.request_all_lessons_generation(
        seeded_db, course=course, actor_id=user.id, regeneration_hint=None
    )
    assert find_lesson(refreshed, "M1.L1").content_status == "pending"
    assert find_lesson(refreshed, "M1.L2").content_status == "pending"
    assert find_lesson(refreshed, "M2.L1").content_status == "empty"
    # Regressione esplicita dell'indicatore mantenuta.
    assert refreshed.status == "content_pending"


async def test_structure_generation_ok_for_virgin_module_at_advanced_course(
    seeded_db,
):
    """Il modulo aggiunto tardi (lezioni senza dispense) può generare la
    propria struttura anche a corso avanzato (prima: 409 oltre
    lessons_structure_approved). Il modulo con dispense resta bloccato."""
    from app.services import course_lesson_structure_service as structure_svc

    course_id, _org, user = await build_course(
        seeded_db, status="slides_approved",
        content_status="approved", slides_status="approved",
    )
    course = await _load(structure_svc, seeded_db, course_id)
    module2 = next(m for m in course.modules if m.module_code == "M2")
    for lesson in module2.lessons:
        lesson.content_status = "empty"
    module2.lessons_structure_status = "empty"
    await seeded_db.flush()

    refreshed = await structure_svc.request_module_generation(
        seeded_db,
        course=course,
        module=module2,
        actor_id=user.id,
        regeneration_hint=None,
    )
    m2 = next(m for m in refreshed.modules if m.module_code == "M2")
    assert m2.lessons_structure_status == "pending"

    module1 = next(m for m in refreshed.modules if m.module_code == "M1")
    with pytest.raises(ConflictError) as exc:
        await structure_svc.request_module_generation(
            seeded_db,
            course=refreshed,
            module=module1,
            actor_id=user.id,
            regeneration_hint=None,
        )
    assert exc.value.code == "module_has_content"


async def test_regenerate_module_lessons_resets_module_structure_status():
    """Il reset a `empty` è verificato a livello di codice negli step
    successivi (richiede monkeypatch OpenAI): qui si fissa il contratto
    del gate — una lezione senza section_outline non genera dispense
    (vedi test_content_generation_blocked_without_lesson_structure)."""
    from app.core.course_phase_order import lesson_structure_is_ready

    class FakeModule:
        id = 1
        module_code = "M1"
        lessons_structure_status = "approved"

    class FakeCourse:
        modules: ClassVar[list] = [FakeModule()]

    class FakeLesson:
        module_id = 1
        is_assessment = False
        section_outline: ClassVar[list] = []

    assert not lesson_structure_is_ready(FakeCourse(), FakeLesson())


async def test_content_generation_ok_from_structure_approved(seeded_db):
    course_id, _org, user = await build_course(
        seeded_db, status="lessons_structure_approved"
    )
    course = await _load(content_svc, seeded_db, course_id)
    lesson = find_lesson(course, "M1.L1")
    refreshed = await content_svc.request_lesson_generation(
        seeded_db,
        course=course,
        lesson=lesson,
        actor_id=user.id,
        regeneration_hint=None,
    )
    assert find_lesson(refreshed, "M1.L1").content_status == "pending"
    assert refreshed.status == "content_pending"


async def test_content_generation_blocked_while_processing(seeded_db):
    course_id, _org, user = await build_course(
        seeded_db, status="content_pending", content_status="processing"
    )
    course = await _load(content_svc, seeded_db, course_id)
    lesson = find_lesson(course, "M1.L1")
    with pytest.raises(ConflictError) as exc:
        await content_svc.request_lesson_generation(
            seeded_db,
            course=course,
            lesson=lesson,
            actor_id=user.id,
            regeneration_hint=None,
        )
    assert exc.value.code == "invalid_lesson_content_status"


async def test_slides_generation_ok_for_single_approved_lesson(seeded_db):
    """Caso-manifesto del gating per-unità: la SOLA M1.L1 ha la dispensa
    approvata, il resto del corso è vuoto e il corso è `content_pending`
    — le slide di M1.L1 si generano comunque (prima: 409
    invalid_course_status_for_slides)."""
    course_id, _org, user = await build_course(
        seeded_db, status="content_pending", content_status="empty"
    )
    course = await _load(slides_svc, seeded_db, course_id)
    lesson = find_lesson(course, "M1.L1")
    lesson.content_status = "approved"
    await seeded_db.flush()
    refreshed = await slides_svc.request_lesson_slides_generation(
        seeded_db,
        course=course,
        lesson=lesson,
        actor_id=user.id,
        regeneration_hint=None,
    )
    assert find_lesson(refreshed, "M1.L1").slides_status == "pending"
    assert find_lesson(refreshed, "M1.L2").content_status == "empty"


async def test_slides_generation_requires_approved_content(seeded_db):
    """Invariante N-1 approvato: contenuto solo `ready` non basta più
    (prima bastava)."""
    course_id, _org, user = await build_course(
        seeded_db, status="content_ready", content_status="ready"
    )
    course = await _load(slides_svc, seeded_db, course_id)
    lesson = find_lesson(course, "M1.L1")
    with pytest.raises(ConflictError) as exc:
        await slides_svc.request_lesson_slides_generation(
            seeded_db,
            course=course,
            lesson=lesson,
            actor_id=user.id,
            regeneration_hint=None,
        )
    assert exc.value.code == "lesson_content_not_ready_for_slides"


async def test_slides_generation_blocked_on_terminal_course(seeded_db):
    course_id, _org, user = await build_course(
        seeded_db, status="published", content_status="approved"
    )
    course = await _load(slides_svc, seeded_db, course_id)
    lesson = find_lesson(course, "M1.L1")
    with pytest.raises(ConflictError) as exc:
        await slides_svc.request_lesson_slides_generation(
            seeded_db,
            course=course,
            lesson=lesson,
            actor_id=user.id,
            regeneration_hint=None,
        )
    assert exc.value.code == "course_terminal_status"


async def test_generate_all_slides_filters_on_approved_content(seeded_db):
    course_id, _org, user = await build_course(
        seeded_db, status="content_ready", content_status="ready"
    )
    course = await _load(slides_svc, seeded_db, course_id)
    approved = find_lesson(course, "M2.L2")
    approved.content_status = "approved"
    await seeded_db.flush()
    refreshed = await slides_svc.request_all_lessons_slides_generation(
        seeded_db, course=course, actor_id=user.id, regeneration_hint=None
    )
    assert find_lesson(refreshed, "M2.L2").slides_status == "pending"
    assert find_lesson(refreshed, "M1.L1").slides_status == "empty"


async def test_slides_generation_blocked_without_lesson_content(seeded_db):
    """Invariante per-lezione (esiste già oggi): niente slide senza
    dispensa della stessa lezione."""
    course_id, _org, user = await build_course(
        seeded_db, status="content_ready", content_status="ready"
    )
    course = await _load(slides_svc, seeded_db, course_id)
    lesson = find_lesson(course, "M1.L2")
    lesson.content_status = "empty"
    await seeded_db.flush()
    with pytest.raises(ConflictError) as exc:
        await slides_svc.request_lesson_slides_generation(
            seeded_db,
            course=course,
            lesson=lesson,
            actor_id=user.id,
            regeneration_hint=None,
        )
    assert exc.value.code == "lesson_content_not_ready_for_slides"


async def test_speech_generation_ok_regardless_of_course_status(seeded_db):
    """Per-unità: slide della lezione approvate bastano, anche con il
    corso 'indietro' (prima: 409 sotto slides_ready)."""
    course_id, _org, user = await build_course(
        seeded_db,
        status="content_approved",
        content_status="approved",
        slides_status="approved",
    )
    course = await _load(speech_svc, seeded_db, course_id)
    lesson = find_lesson(course, "M1.L1")
    refreshed = await speech_svc.request_lesson_speech_generation(
        seeded_db,
        course=course,
        lesson=lesson,
        actor_id=user.id,
        regeneration_hint=None,
    )
    assert find_lesson(refreshed, "M1.L1").speech_status == "pending"


async def test_speech_generation_requires_approved_slides(seeded_db):
    """Invariante N-1 approvato: slide solo `ready` non bastano più."""
    course_id, _org, user = await build_course(
        seeded_db,
        status="slides_ready",
        content_status="approved",
        slides_status="ready",
    )
    course = await _load(speech_svc, seeded_db, course_id)
    lesson = find_lesson(course, "M1.L1")
    with pytest.raises(ConflictError) as exc:
        await speech_svc.request_lesson_speech_generation(
            seeded_db,
            course=course,
            lesson=lesson,
            actor_id=user.id,
            regeneration_hint=None,
        )
    assert exc.value.code == "lesson_slides_not_ready_for_speech"


async def test_speech_generation_blocked_on_assessment(seeded_db):
    course_id, _org, user = await build_course(
        seeded_db,
        status="slides_approved",
        modules=1,
        lessons_per_module=2,
        with_assessment=True,
        content_status="approved",
        slides_status="approved",
    )
    course = await _load(speech_svc, seeded_db, course_id)
    lesson = find_lesson(course, "M1.L2")
    assert lesson.is_assessment
    with pytest.raises(ConflictError) as exc:
        await speech_svc.request_lesson_speech_generation(
            seeded_db,
            course=course,
            lesson=lesson,
            actor_id=user.id,
            regeneration_hint=None,
        )
    assert exc.value.code == "lesson_is_assessment_not_eligible"


async def test_approve_content_requires_ready(seeded_db):
    course_id, _org, user = await build_course(
        seeded_db, status="content_pending", content_status="processing"
    )
    course = await _load(content_svc, seeded_db, course_id)
    lesson = find_lesson(course, "M1.L1")
    with pytest.raises(ConflictError) as exc:
        await content_svc.approve_lesson_content(
            seeded_db, course=course, lesson=lesson, actor_id=user.id
        )
    assert exc.value.code == "lesson_content_not_ready"


async def test_recompute_content_status_is_monotonic():
    """advance_course_status non deve mai far regredire il corso: unit
    test puro, senza DB (il bug storico 'approvo un contenuto dopo le
    slide → corso torna a content_approved')."""
    from app.core.course_phase_order import advance_course_status

    class FakeCourse:
        status = "slides_approved"

    course = FakeCourse()
    advance_course_status(course, "content_approved")
    assert course.status == "slides_approved"
    advance_course_status(course, "speech_pending")
    assert course.status == "speech_pending"
