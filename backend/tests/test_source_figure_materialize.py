"""Figure di fonte nella generazione della Fase 3, dal catalogo al DB (WP3).

Il worker di Fase 3 gira davvero; OpenAI (PROMPT 3 e PROMPT 19) e la
validazione degli asset sono sostituiti. Oracoli:

- G2: figure di documenti `excluded`/`content_only`, escluse dal docente o
  di altri corsi non entrano mai nel catalogo (canarini nelle descrizioni)
  né nell'output; il ricontrollo TOCTOU toglie una figura il cui documento
  diventa riservato durante la generazione, con audit;
- I1: senza catalogo messaggio user e riferimenti offerti sono quelli di
  prima, e `content_raw` ha le stesse chiavi;
- la figura scelta diventa un asset `source_figure` con l'UUID, il budget
  (b) vale, il verdetto del revisore finisce in `content_figure_review`, il
  costo in `content_tokens.assets` (phase `redundancy`) e le statistiche in
  `content_tokens.source_figures`;
- attesa delle estrazioni come filtro SQL del `_tick`.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.models.audit_log import AuditLog
from app.models.course import Course
from app.models.course_document import CourseDocument
from app.models.course_lesson import CourseLesson
from app.schemas.course_lesson_content import LessonContentOutput
from app.services import asset_validation_service as avs
from app.services import course_lesson_content_service as content_svc
from app.services import course_lesson_content_worker as worker
from app.services import openai_figure_redundancy_service as redundancy
from app.services import openai_lesson_content_service as openai_svc
from app.services import source_figure_catalog
from app.services.lesson_figure_selection import figure_ref
from tests.course_builders import build_course, build_course_document, find_lesson
from tests.source_figure_builders import build_document_figure

CANARY_RESERVED = "CANARINORISERVATO"
CANARY_EXCLUDED = "CANARINOESCLUSO"
CANARY_USER = "CANARINOESCLUSODOCENTE"


async def _setup(db: AsyncSession) -> dict[str, Any]:
    course_id, _org, _user = await build_course(
        db, modules=1, lessons_per_module=1, content_status="pending"
    )
    await db.execute(update(Course).where(Course.id == course_id).values(glossary_status="ready"))
    await db.commit()
    course = await content_svc.load_course_full(db, course_id=course_id)
    assert course is not None
    lesson = find_lesson(course, "M1.L1")
    lesson.title = "Vibrometria laser Doppler"
    lesson.mandatory_topics = [{"topic_id": "T1", "title": "Vibrometro laser Doppler"}]
    lesson.section_outline = [
        {
            "section_id": "S1",
            "title": "Il vibrometro laser Doppler",
            "purpose": "Schema dello strumento",
            "covers_topic_ids": ["T1"],
        }
    ]
    lesson.learning_objectives = ["Descrivere il vibrometro laser Doppler"]
    lesson.summary = "Vibrometro laser Doppler e cella di Bragg."
    docs = {
        policy: build_course_document(course_id, filename=f"{policy}.pdf", policy=policy)
        for policy in ("citable", "content_only", "excluded")
    }
    db.add_all(docs.values())
    await db.flush()

    def fig(doc: CourseDocument, canary: str = "", **kw: Any) -> Any:
        return build_document_figure(
            course_id,
            doc.id,
            license="cc_by",
            description=f"Schema del vibrometro laser Doppler con la cella di Bragg {canary}",
            keywords={"course": ["vibrometro laser Doppler", "cella di Bragg"], "en": []},
            **kw,
        )

    figures = {
        "good": fig(docs["citable"]),
        "reserved": fig(docs["content_only"], CANARY_RESERVED),
        "excluded": fig(docs["excluded"], CANARY_EXCLUDED),
        "by_user": fig(docs["citable"], CANARY_USER, excluded_by_user=True),
    }
    db.add_all(figures.values())
    await db.commit()
    return {"course_id": course_id, "lesson_id": lesson.id, "docs": docs, "figures": figures}


def _output(lesson_code: str, refs: list[str]) -> LessonContentOutput:
    tags = "".join(f"\n\n[FIG:{r}]\n\nCommento." for r in refs)
    return LessonContentOutput.model_validate(
        {
            "lesson_id": lesson_code,
            "lesson_title": "Vibrometria laser Doppler",
            "is_introductory": False,
            "estimated_word_count": 800,
            "introduction": "Introduzione.",
            "sections": [
                {
                    "section_id": "S1",
                    "title": "Il vibrometro laser Doppler",
                    "content": "Il principio." + tags,
                    "objectives_addressed": ["O1"],
                    "topics_addressed": ["T1"],
                }
            ],
            "summary": "Sintesi.",
            "key_takeaways": ["uno", "due", "tre"],
            "visual_assets": [],
            "coverage_check": {
                "objectives_covered": [{"objective": "O1", "covered_in_section_ids": ["S1"]}],
                "topics_covered": [{"topic_id": "T1", "covered_in_section_ids": ["S1"]}],
            },
            "source_figures": [
                {"figure": r, "caption": "Schema del vibrometro. Fonte: X", "alt_text": "schema"}
                for r in refs
            ],
        }
    )


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch, _engine: Any) -> dict[str, Any]:
    state: dict[str, Any] = {"calls": [], "choose": True, "on_generate": None}

    async def generate(**kwargs: Any) -> tuple[LessonContentOutput, dict[str, Any]]:
        state["calls"].append(kwargs)
        if state["on_generate"] is not None:
            await state["on_generate"]()
        refs = list(kwargs.get("source_figure_refs") or []) if state["choose"] else []
        usage = {"model": "gpt-5.5", "total": 10, "prompt": 5, "completion": 5, "cost_usd": 0.1}
        return _output("M1.L1", refs), usage

    async def no_validation(
        out: LessonContentOutput, *, language_code: str
    ) -> tuple[LessonContentOutput, list[dict[str, Any]]]:
        return out, []

    async def review_transport(body: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        state.setdefault("review_calls", []).append(body)
        answer = {"coherence": "coerente", "reason": "ok", "pairs": []}
        return {
            "choices": [{"message": {"content": json.dumps(answer)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
        }

    monkeypatch.setattr(
        worker, "async_session_factory", async_sessionmaker(_engine, expire_on_commit=False)
    )
    monkeypatch.setattr(openai_svc, "generate_lesson_content", generate)
    monkeypatch.setattr(avs, "validate_and_fix_content_assets", no_validation)
    monkeypatch.setattr(redundancy, "post_chat_with_retry", review_transport)
    return state


async def _lesson(db: AsyncSession, lesson_id: uuid.UUID) -> CourseLesson:
    row = (
        await db.execute(
            select(CourseLesson)
            .where(CourseLesson.id == lesson_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    return row


async def test_catalog_respects_policies_and_figure_is_fused(
    seeded_db: AsyncSession, fakes: dict[str, Any]
) -> None:
    setup = await _setup(seeded_db)
    good = setup["figures"]["good"]
    await worker._process_one(setup["lesson_id"])

    call = fakes["calls"][0]
    prompt = call["user_prompt"]
    assert list(call["source_figure_refs"]) == [figure_ref(good.id)]
    assert "## Figure di fonte disponibili (catalogo)" in prompt
    for canary in (CANARY_RESERVED, CANARY_EXCLUDED, CANARY_USER):
        assert canary not in prompt
    assert "Figure di fonte: IN AGGIUNTA alle figure da generare" in prompt

    lesson = await _lesson(seeded_db, setup["lesson_id"])
    assert lesson.content_status == "ready", lesson.content_error
    sources = [a for a in lesson.content_raw["visual_assets"] if a["format"] == "source_figure"]
    assert sources == [
        {
            "asset_id": figure_ref(good.id),
            "format": "source_figure",
            "content": str(good.id),
            "caption": "Schema del vibrometro.",
            "alt_text": "schema",
        }
    ]
    assert "source_figures" not in lesson.content_raw
    review = lesson.content_figure_review
    assert review is not None and figure_ref(good.id) in review["figures"]
    tokens = lesson.content_tokens
    assert tokens["source_figures"]["added"] == [figure_ref(good.id)]
    assert tokens["source_figures"]["catalog"] == 1
    assert [a["phase"] for a in tokens["assets"]] == ["redundancy"]


async def test_no_catalog_keeps_prompt_and_content_raw_as_before(
    seeded_db: AsyncSession, fakes: dict[str, Any]
) -> None:
    course_id, _org, _user = await build_course(
        seeded_db, modules=1, lessons_per_module=1, content_status="pending"
    )
    await seeded_db.execute(
        update(Course).where(Course.id == course_id).values(glossary_status="ready")
    )
    await seeded_db.commit()
    course = await content_svc.load_course_full(seeded_db, course_id=course_id)
    assert course is not None
    lesson = find_lesson(course, "M1.L1")
    expected_prompt = content_svc.build_user_prompt(course, lesson)
    await worker._process_one(lesson.id)
    call = fakes["calls"][0]
    assert call["user_prompt"] == expected_prompt
    assert list(call["source_figure_refs"]) == []
    fresh = await _lesson(seeded_db, lesson.id)
    assert set(fresh.content_raw) == set(LessonContentOutput.model_fields) - {"source_figures"}
    assert fresh.content_figure_review is None
    assert "source_figures" not in (fresh.content_tokens or {})


async def test_policy_change_during_generation_drops_the_figure(
    seeded_db: AsyncSession, fakes: dict[str, Any], _engine: Any
) -> None:
    setup = await _setup(seeded_db)
    citable = setup["docs"]["citable"]

    async def make_reserved() -> None:
        factory = async_sessionmaker(_engine, expire_on_commit=False)
        async with factory() as other:
            await other.execute(
                update(CourseDocument)
                .where(CourseDocument.id == citable.id)
                .values(citation_policy="content_only")
            )
            await other.commit()

    fakes["on_generate"] = make_reserved
    await worker._process_one(setup["lesson_id"])
    lesson = await _lesson(seeded_db, setup["lesson_id"])
    assert lesson.content_status == "ready"
    assert not [a for a in lesson.content_raw["visual_assets"] if a["format"] == "source_figure"]
    assert "[FIG:SRC-" not in lesson.content_raw["sections"][0]["content"]
    audits = (
        (
            await seeded_db.execute(
                select(AuditLog).where(
                    AuditLog.action == "course.lesson.content.source_figures_dropped",
                    AuditLog.target_id == str(setup["lesson_id"]),
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1


async def test_intro_lesson_budget_is_one(seeded_db: AsyncSession) -> None:
    setup = await _setup(seeded_db)
    lesson = await _lesson(seeded_db, setup["lesson_id"])
    assert source_figure_catalog.budget_for(lesson) == get_settings().figure_source_max_per_lesson
    lesson.is_introductory = True
    assert source_figure_catalog.budget_for(lesson) == 1
    lesson.is_assessment = True
    assert source_figure_catalog.budget_for(lesson) == 0


async def test_tick_waits_for_recent_extractions(
    seeded_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup = await _setup(seeded_db)
    patched = get_settings().model_copy(
        update={"figure_extraction_enabled": True, "figure_source_enabled": True}
    )
    monkeypatch.setattr(worker, "get_settings", lambda: patched)
    doc = setup["docs"]["citable"]

    async def pending_ids() -> set[uuid.UUID]:
        rows = await seeded_db.execute(worker._pending_lessons_query())
        return {r[0] for r in rows.all()}

    doc.figures_status = "processing"
    doc.figures_requested_at = datetime.now(UTC)
    await seeded_db.commit()
    assert setup["lesson_id"] not in await pending_ids()
    doc.figures_requested_at = datetime.now(UTC) - timedelta(
        minutes=patched.figure_wait_max_minutes + 1
    )
    await seeded_db.commit()
    assert setup["lesson_id"] in await pending_ids()
