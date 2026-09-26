"""Figure di fonte nella generazione della Fase 3, dal catalogo al DB (WP3).

Il worker di Fase 3 gira davvero; OpenAI (PROMPT 3 e PROMPT 19) e la
validazione degli asset sono sostituiti. Oracoli:

- G2: figure di documenti `excluded`, escluse dal docente o di altri
  corsi non entrano mai nel catalogo (canarini nelle descrizioni) né
  nell'output; quelle delle fonti riservate (`content_only`, materiale del
  docente) sì, ma il nome del documento non entra mai nel prompt; il
  ricontrollo TOCTOU toglie una figura il cui documento viene escluso
  durante la generazione, con audit;
- I1: senza catalogo messaggio user e riferimenti offerti sono quelli di
  prima, e `content_raw` ha le stesse chiavi;
- la figura scelta diventa un asset `source_figure` con l'UUID, il budget
  (b) vale, il verdetto del revisore finisce in `content_figure_review`, il
  costo in `content_tokens.assets` (phase `redundancy`) e le statistiche in
  `content_tokens.source_figures`;
- attesa delle estrazioni come filtro SQL del `_tick`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
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
from app.services.figure_attribution import figure_attribution_line
from app.services.lesson_figure_selection import figure_ref
from tests.course_builders import build_course, build_course_document, find_lesson
from tests.source_figure_builders import build_document_figure

CANARY_RESERVED = "CANARINORISERVATO"
CANARY_EXCLUDED = "CANARINOESCLUSO"
CANARY_USER = "CANARINOESCLUSODOCENTE"
# Identità del documento riservato: mai nel prompt.
CANARY_RESERVED_NAME = ("CANARINONOMEFILE", "CANARINOTITOLO", "CANARINOAUTORE")


async def _setup(db: AsyncSession, *, with_reserved: bool = False) -> dict[str, Any]:
    """Corso con una figura citabile pertinente, una esclusa dal docente e
    una di un documento escluso; con `with_reserved` anche una figura della
    fonte riservata, anch'essa proponibile."""
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
        "excluded": fig(docs["excluded"], CANARY_EXCLUDED),
        "by_user": fig(docs["citable"], CANARY_USER, excluded_by_user=True),
    }
    if with_reserved:
        reserved = docs["content_only"]
        reserved.filename_original = f"{CANARY_RESERVED_NAME[0]}.pdf"
        reserved.bibliography = {
            "title": CANARY_RESERVED_NAME[1],
            "authors": [CANARY_RESERVED_NAME[2]],
        }
        reserved.bibliography_source = "user"
        figures["reserved"] = fig(reserved, CANARY_RESERVED)
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
        # Il codice della lezione dal messaggio user (riga «ID: M1.L2»).
        found = re.search(r"(?m)^ID: (\S+)", str(kwargs.get("user_prompt") or ""))
        return _output(found.group(1) if found else "M1.L1", refs), usage

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


async def test_reserved_figure_is_offered_without_the_document_name(
    seeded_db: AsyncSession, fakes: dict[str, Any]
) -> None:
    """Fonte riservata = materiale del docente: la figura entra nel catalogo
    e nella dispensa, ma nome del file, titolo e autori del documento non
    entrano mai nel prompt, nel contenuto né nella riga «Fonte»."""
    setup = await _setup(seeded_db, with_reserved=True)
    reserved = setup["figures"]["reserved"]
    await worker._process_one(setup["lesson_id"])
    call = fakes["calls"][0]
    prompt = call["user_prompt"]
    assert figure_ref(reserved.id) in list(call["source_figure_refs"])
    assert CANARY_RESERVED in prompt  # controprova: la figura è nel catalogo
    for canary in CANARY_RESERVED_NAME:
        assert canary not in prompt
    lesson = await _lesson(seeded_db, setup["lesson_id"])
    assert lesson.content_status == "ready", lesson.content_error
    placed = [a for a in lesson.content_raw["visual_assets"] if a["format"] == "source_figure"]
    assert str(reserved.id) in {a["content"] for a in placed}
    dumped = json.dumps(lesson.content_raw, ensure_ascii=False)
    for canary in CANARY_RESERVED_NAME:
        assert canary not in dumped
    doc = setup["docs"]["content_only"]
    assert figure_attribution_line(reserved, doc, language="it") == "Fonte: materiale del docente"


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

    async def make_excluded() -> None:
        factory = async_sessionmaker(_engine, expire_on_commit=False)
        async with factory() as other:
            await other.execute(
                update(CourseDocument)
                .where(CourseDocument.id == citable.id)
                .values(citation_policy="excluded")
            )
            await other.commit()

    fakes["on_generate"] = make_excluded
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
    assert audits[0].payload["reason"] == "not_selectable_at_materialize"


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


# --- correzioni della verifica WP3 -------------------------------------------------


async def test_org_license_policy_is_reread_at_the_recheck(
    seeded_db: AsyncSession, fakes: dict[str, Any], _engine: Any
) -> None:
    """L'organizzazione passa a open_only durante la generazione: il
    ricontrollo rilegge la politica e toglie la figura a licenza ignota."""
    from app.models.organization_course_settings import OrganizationCourseSettings

    setup = await _setup(seeded_db)
    good = setup["figures"]["good"]
    good.license = "unknown"
    await seeded_db.commit()
    course = await seeded_db.get(Course, setup["course_id"])
    assert course is not None
    org_id = course.organization_id

    async def switch_to_open_only() -> None:
        factory = async_sessionmaker(_engine, expire_on_commit=False)
        async with factory() as other:
            row = (
                await other.execute(
                    select(OrganizationCourseSettings).where(
                        OrganizationCourseSettings.organization_id == org_id
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                other.add(
                    OrganizationCourseSettings(
                        organization_id=org_id, figure_source_license_policy="open_only"
                    )
                )
            else:
                row.figure_source_license_policy = "open_only"
            await other.commit()

    fakes["on_generate"] = switch_to_open_only
    await worker._process_one(setup["lesson_id"])
    lesson = await _lesson(seeded_db, setup["lesson_id"])
    assert lesson.content_status == "ready", lesson.content_error
    assert not [a for a in lesson.content_raw["visual_assets"] if a["format"] == "source_figure"]
    # Il verdetto del revisore non parla di una figura che non c'è più.
    assert lesson.content_figure_review is None


async def test_cancel_during_the_redundancy_review_is_not_overwritten(
    seeded_db: AsyncSession, fakes: dict[str, Any], _engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup = await _setup(seeded_db)

    async def cancel_then_answer(body: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        factory = async_sessionmaker(_engine, expire_on_commit=False)
        async with factory() as other:
            await other.execute(
                update(CourseLesson)
                .where(CourseLesson.id == setup["lesson_id"])
                .values(content_status="failed", content_error="Generazione annullata dall'utente.")
            )
            await other.commit()
        answer = {"coherence": "coerente", "reason": "ok", "pairs": []}
        return {
            "choices": [{"message": {"content": json.dumps(answer)}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    monkeypatch.setattr(redundancy, "post_chat_with_retry", cancel_then_answer)
    await worker._process_one(setup["lesson_id"])
    lesson = await _lesson(seeded_db, setup["lesson_id"])
    assert lesson.content_status == "failed"
    assert lesson.content_error == "Generazione annullata dall'utente."


async def test_reviewer_error_means_no_notice_and_the_lesson_is_ready(
    seeded_db: AsyncSession, fakes: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    setup = await _setup(seeded_db)

    async def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("DB non raggiungibile")

    monkeypatch.setattr(source_figure_catalog, "figure_infos", broken)
    await worker._process_one(setup["lesson_id"])
    lesson = await _lesson(seeded_db, setup["lesson_id"])
    assert lesson.content_status == "ready", lesson.content_error
    assert lesson.content_figure_review is None
    assert [a for a in lesson.content_raw["visual_assets"] if a["format"] == "source_figure"]


async def test_failed_recheck_drops_every_source_figure(
    seeded_db: AsyncSession, fakes: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    setup = await _setup(seeded_db)

    async def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("DB non raggiungibile")

    monkeypatch.setattr(source_figure_catalog, "not_selectable", broken)
    await worker._process_one(setup["lesson_id"])
    lesson = await _lesson(seeded_db, setup["lesson_id"])
    assert lesson.content_status == "ready", lesson.content_error
    assert not [a for a in lesson.content_raw["visual_assets"] if a["format"] == "source_figure"]


async def test_reserved_document_name_in_a_caption_is_flagged(
    seeded_db: AsyncSession, fakes: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guardia leak anche su didascalie e testi alternativi (audit, mai
    modifica del contenuto)."""
    from app.schemas.course_lesson_content import LessonContentVisualAsset

    setup = await _setup(seeded_db)
    reserved = setup["docs"]["content_only"]
    title = "Manuale riservato delle misure vibrometriche"
    reserved.summary = {"source_title": title}
    await seeded_db.commit()

    async def generate(**kwargs: Any) -> tuple[LessonContentOutput, dict[str, Any]]:
        out = _output("M1.L1", [])
        out.visual_assets = [
            LessonContentVisualAsset(
                asset_id="gen-1",
                format="dot",
                content="digraph{a->b}",
                caption=f"Schema dal {title}",
                alt_text="schema",
            )
        ]
        return out, {"model": "gpt-5.5", "total": 1, "cost_usd": 0.0}

    monkeypatch.setattr(openai_svc, "generate_lesson_content", generate)
    await worker._process_one(setup["lesson_id"])
    lesson = await _lesson(seeded_db, setup["lesson_id"])
    assert lesson.content_status == "ready", lesson.content_error
    # Il contenuto non cambia: solo segnalazione.
    assert lesson.content_raw["visual_assets"][0]["caption"] == f"Schema dal {title}"
    audits = (
        (
            await seeded_db.execute(
                select(AuditLog).where(
                    AuditLog.action == "course.lesson.content.reserved_leak",
                    AuditLog.target_id == str(setup["lesson_id"]),
                )
            )
        )
        .scalars()
        .all()
    )
    assert audits, "nome del documento riservato nella didascalia non segnalato"


# --- oracoli aggiunti dalla verifica dei test di WP3 --------------------------------


def _extra_figure(setup: dict[str, Any], **kw: Any) -> Any:
    return build_document_figure(
        setup["course_id"],
        setup["docs"]["citable"].id,
        license=kw.pop("license", "cc_by"),
        description=kw.pop(
            "description", "Schema del vibrometro laser Doppler con la cella di Bragg"
        ),
        keywords=kw.pop(
            "keywords", {"course": ["vibrometro laser Doppler", "cella di Bragg"], "en": []}
        ),
        **kw,
    )


async def _catalog_ids(seeded_db: AsyncSession, setup: dict[str, Any]) -> set[uuid.UUID]:
    course = await content_svc.load_course_full(seeded_db, course_id=setup["course_id"])
    assert course is not None
    lesson = find_lesson(course, "M1.L1")
    result = await source_figure_catalog.build_catalog(seeded_db, course, lesson)
    return set(result.catalog.refs.values()) if result is not None else set()


async def test_i3_content_raw_is_identical_with_and_without_the_reviewer(
    seeded_db: AsyncSession, fakes: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """I3 nel worker: il revisore segnala soltanto, `content_raw` è lo stesso
    con il revisore acceso e spento (stessa lezione rigenerata)."""
    setup = await _setup(seeded_db)
    await worker._process_one(setup["lesson_id"])
    with_review = await _lesson(seeded_db, setup["lesson_id"])
    assert with_review.content_figure_review is not None
    raw_on = json.loads(json.dumps(with_review.content_raw))
    patched = get_settings().model_copy(update={"figure_redundancy_enabled": False})
    monkeypatch.setattr(avs, "get_settings", lambda: patched)
    await seeded_db.execute(
        update(CourseLesson)
        .where(CourseLesson.id == setup["lesson_id"])
        .values(content_status="pending", content_raw=None)
    )
    await seeded_db.commit()
    await worker._process_one(setup["lesson_id"])
    without = await _lesson(seeded_db, setup["lesson_id"])
    assert without.content_status == "ready"
    assert without.content_figure_review is None
    assert without.content_raw == raw_on


async def test_catalog_applies_the_organization_license_policy(
    seeded_db: AsyncSession,
) -> None:
    from app.models.organization_course_settings import OrganizationCourseSettings

    setup = await _setup(seeded_db)
    unknown = _extra_figure(setup, license="unknown")
    seeded_db.add(unknown)
    await seeded_db.commit()
    good = setup["figures"]["good"]
    assert {good.id, unknown.id} <= await _catalog_ids(seeded_db, setup)
    course = await seeded_db.get(Course, setup["course_id"])
    assert course is not None
    seeded_db.add(
        OrganizationCourseSettings(
            organization_id=course.organization_id, figure_source_license_policy="open_only"
        )
    )
    await seeded_db.commit()
    ids = await _catalog_ids(seeded_db, setup)
    assert good.id in ids and unknown.id not in ids


async def test_catalog_drops_low_quality_useless_and_decorative_figures(
    seeded_db: AsyncSession,
) -> None:
    setup = await _setup(seeded_db)
    low = _extra_figure(setup, quality_score=1)
    useless = _extra_figure(setup, is_useful_for_teaching=False)
    logo = _extra_figure(setup, kind="logo_or_decoration")
    seeded_db.add_all([low, useless, logo])
    await seeded_db.commit()
    ids = await _catalog_ids(seeded_db, setup)
    assert setup["figures"]["good"].id in ids
    assert not ({low.id, useless.id, logo.id} & ids)


async def test_worker_respects_the_source_figure_budget(
    seeded_db: AsyncSession, fakes: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    setup = await _setup(seeded_db)
    seeded_db.add_all([_extra_figure(setup), _extra_figure(setup)])
    await seeded_db.commit()
    patched = get_settings().model_copy(update={"figure_source_max_per_lesson": 1})
    monkeypatch.setattr(source_figure_catalog, "get_settings", lambda: patched)
    await worker._process_one(setup["lesson_id"])
    lesson = await _lesson(seeded_db, setup["lesson_id"])
    sources = [a for a in lesson.content_raw["visual_assets"] if a["format"] == "source_figure"]
    assert len(sources) == 1
    assert len(fakes["calls"][0]["source_figure_refs"]) == 3  # il catalogo ne offriva 3


async def test_tick_dispatches_only_the_lessons_of_the_pending_query(
    seeded_db: AsyncSession, monkeypatch: pytest.MonkeyPatch, _engine: Any
) -> None:
    """`_tick` usa il filtro SQL dell'attesa delle estrazioni: una lezione il
    cui corso ha un'estrazione recente in corso non parte."""
    setup = await _setup(seeded_db)
    # Solo l'attesa delle estrazioni: quella dei buchi di figure (WP5) ha il
    # suo test in test_source_figure_gaps.
    patched = get_settings().model_copy(
        update={
            "figure_extraction_enabled": True,
            "figure_source_enabled": True,
            "figure_literature_enabled": False,
        }
    )
    monkeypatch.setattr(worker, "get_settings", lambda: patched)
    monkeypatch.setattr(
        worker, "async_session_factory", async_sessionmaker(_engine, expire_on_commit=False)
    )
    dispatched: list[uuid.UUID] = []

    async def record(lesson_id: uuid.UUID) -> None:
        dispatched.append(lesson_id)

    monkeypatch.setattr(worker, "_bound_process", record)
    monkeypatch.setattr(worker, "_inflight", set())
    doc = setup["docs"]["citable"]
    doc.figures_status = "processing"
    doc.figures_requested_at = datetime.now(UTC)
    await seeded_db.commit()
    await worker._tick()
    await asyncio.sleep(0)
    assert setup["lesson_id"] not in dispatched
    doc.figures_requested_at = datetime.now(UTC) - timedelta(
        minutes=patched.figure_wait_max_minutes + 1
    )
    await seeded_db.commit()
    await worker._tick()
    await asyncio.sleep(0)
    assert setup["lesson_id"] in dispatched


# --- riuso limitato: una figura di fonte in al più K lezioni ------------------------


def _with_cap(monkeypatch: pytest.MonkeyPatch, cap: int) -> None:
    patched = get_settings().model_copy(update={"figure_source_max_lessons_per_figure": cap})
    monkeypatch.setattr(source_figure_catalog, "get_settings", lambda: patched)


async def _second_lesson(
    db: AsyncSession,
    setup: dict[str, Any],
    visual_assets: list[dict[str, Any]],
    *,
    code: str = "M1.L2",
    position: int = 2,
) -> CourseLesson:
    first = await _lesson(db, setup["lesson_id"])
    other = CourseLesson(
        module_id=first.module_id,
        course_id=first.course_id,
        position=position,
        lesson_code=code,
        title="Vibrometro laser Doppler: applicazioni",
        summary="Applicazioni del vibrometro laser Doppler.",
        learning_objectives=[],
        mandatory_topics=[],
        prerequisites=[],
        section_outline=[],
        content_status="ready",
        content_raw={"introduction": "Testo.", "sections": [], "visual_assets": visual_assets},
    )
    db.add(other)
    await db.commit()
    return other


def _placed(fig_id: uuid.UUID) -> dict[str, Any]:
    return {"asset_id": "SRC-altra", "format": "source_figure", "content": str(fig_id)}


@pytest.mark.parametrize("cap", [1, 2])
async def test_a_source_figure_is_offered_up_to_the_reuse_cap(
    seeded_db: AsyncSession, fakes: dict[str, Any], monkeypatch: pytest.MonkeyPatch, cap: int
) -> None:
    """Con K=1 (comportamento precedente) la figura collocata in un'altra
    lezione non entra nel catalogo; con K=2 sì, finché le altre lezioni che
    la usano sono meno di K."""
    _with_cap(monkeypatch, cap)
    setup = await _setup(seeded_db)
    good = setup["figures"]["good"]
    await _second_lesson(seeded_db, setup, [_placed(good.id)])
    assert (good.id in await _catalog_ids(seeded_db, setup)) is (cap > 1)
    await _second_lesson(seeded_db, setup, [_placed(good.id)], code="M1.L3", position=3)
    assert good.id not in await _catalog_ids(seeded_db, setup)


async def test_a_lesson_keeps_its_own_figure_even_beyond_the_cap(
    seeded_db: AsyncSession, fakes: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Una figura già nella lezione stessa non consuma un posto: rigenerando
    la lezione resta proponibile anche se altre 5 lezioni la usano (U1,
    lo schema LDV del corso del docente sta in 5 lezioni)."""
    _with_cap(monkeypatch, 2)
    setup = await _setup(seeded_db)
    good = setup["figures"]["good"]
    for i in range(5):
        await _second_lesson(
            seeded_db, setup, [_placed(good.id)], code=f"M1.L{i + 2}", position=i + 2
        )
    assert good.id not in await _catalog_ids(seeded_db, setup)
    lesson = await _lesson(seeded_db, setup["lesson_id"])
    lesson.content_raw = {
        "introduction": "Testo.",
        "sections": [],
        "visual_assets": [_placed(good.id)],
    }
    await seeded_db.commit()
    assert good.id in await _catalog_ids(seeded_db, setup)
    course = await content_svc.load_course_full(seeded_db, course_id=setup["course_id"])
    assert course is not None
    assert not await source_figure_catalog.over_reuse_cap(
        seeded_db, course, [good.id], lesson_id=setup["lesson_id"]
    )


async def _dropped_audits(db: AsyncSession, lesson_id: uuid.UUID) -> list[AuditLog]:
    rows = await db.execute(
        select(AuditLog).where(
            AuditLog.action == "course.lesson.content.source_figures_dropped",
            AuditLog.target_id == str(lesson_id),
        )
    )
    return list(rows.scalars().all())


@pytest.mark.parametrize("cap", [1, 2])
async def test_regenerating_keeps_the_lessons_own_figure_beyond_the_cap_end_to_end(
    seeded_db: AsyncSession, fakes: dict[str, Any], monkeypatch: pytest.MonkeyPatch, cap: int
) -> None:
    """Rigenerazione vera nel worker (catalogo, fusione, ricontrollo,
    materializzazione): la figura già nella lezione resta anche se altre 5
    lezioni la usano, con K=1 come con K=2, e nessun audit di scarto."""
    _with_cap(monkeypatch, cap)
    setup = await _setup(seeded_db)
    good = setup["figures"]["good"]
    for i in range(5):
        await _second_lesson(
            seeded_db, setup, [_placed(good.id)], code=f"M1.L{i + 2}", position=i + 2
        )
    lesson = await _lesson(seeded_db, setup["lesson_id"])
    lesson.content_raw = {
        "introduction": "Testo.",
        "sections": [],
        "visual_assets": [_placed(good.id)],
    }
    await seeded_db.commit()
    await worker._process_one(setup["lesson_id"])
    lesson = await _lesson(seeded_db, setup["lesson_id"])
    assert lesson.content_status == "ready", lesson.content_error
    assert list(fakes["calls"][0]["source_figure_refs"]) == [figure_ref(good.id)]
    kept = [
        a["content"] for a in lesson.content_raw["visual_assets"] if a["format"] == "source_figure"
    ]
    assert kept == [str(good.id)]
    assert not await _dropped_audits(seeded_db, setup["lesson_id"])


async def test_recheck_splits_the_audit_by_reason_with_policy_first(
    seeded_db: AsyncSession, fakes: dict[str, Any], _engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Due figure scelte: A diventa di un documento escluso E arriva al
    tetto, B arriva solo al tetto (K=1). Due audit: la politica prevale sul
    tetto per A, il tetto (con K) vale per B."""
    _with_cap(monkeypatch, 1)
    setup = await _setup(seeded_db)
    first = setup["figures"]["good"]
    other_doc = build_course_document(setup["course_id"], filename="altro.pdf", policy="citable")
    seeded_db.add(other_doc)
    await seeded_db.flush()
    second = build_document_figure(
        setup["course_id"],
        other_doc.id,
        license="cc_by",
        description="Schema del vibrometro laser Doppler con la cella di Bragg, variante",
        keywords={"course": ["vibrometro laser Doppler", "cella di Bragg"], "en": []},
    )
    seeded_db.add(second)
    await seeded_db.commit()
    other = await _second_lesson(seeded_db, setup, [])

    async def both_saturated_and_first_excluded() -> None:
        factory = async_sessionmaker(_engine, expire_on_commit=False)
        async with factory() as session:
            await session.execute(
                update(CourseDocument)
                .where(CourseDocument.id == setup["docs"]["citable"].id)
                .values(citation_policy="excluded")
            )
            placed = [
                {"asset_id": "SRC-a", "format": "source_figure", "content": str(first.id)},
                {"asset_id": "SRC-b", "format": "source_figure", "content": str(second.id)},
            ]
            await session.execute(
                update(CourseLesson)
                .where(CourseLesson.id == other.id)
                .values(content_raw={"introduction": "T.", "sections": [], "visual_assets": placed})
            )
            await session.commit()

    fakes["on_generate"] = both_saturated_and_first_excluded
    await worker._process_one(setup["lesson_id"])
    refs = set(fakes["calls"][0]["source_figure_refs"])
    assert refs == {figure_ref(first.id), figure_ref(second.id)}
    lesson = await _lesson(seeded_db, setup["lesson_id"])
    assert lesson.content_status == "ready", lesson.content_error
    assert not [a for a in lesson.content_raw["visual_assets"] if a["format"] == "source_figure"]
    by_reason = {
        a.payload["reason"]: a.payload for a in await _dropped_audits(seeded_db, setup["lesson_id"])
    }
    assert set(by_reason) == {"not_selectable_at_materialize", "reuse_cap"}
    assert by_reason["not_selectable_at_materialize"]["dropped"] == [figure_ref(first.id)]
    assert by_reason["reuse_cap"]["dropped"] == [figure_ref(second.id)]
    assert by_reason["reuse_cap"]["cap"] == 1
    assert "cap" not in by_reason["not_selectable_at_materialize"]


@pytest.mark.parametrize("cap", [1, 2])
async def test_sequential_lessons_respect_the_reuse_cap(
    seeded_db: AsyncSession,
    fakes: dict[str, Any],
    _engine: Any,
    monkeypatch: pytest.MonkeyPatch,
    cap: int,
) -> None:
    """L'altra lezione colloca la figura (commit) mentre questa è in
    generazione. Con K=1 al ricontrollo questa la toglie, con audit
    `reuse_cap`; con K=2 la tengono entrambe. Due lezioni generate insieme:
    `test_parallel_lessons_respect_the_reuse_cap_under_the_course_lock`."""
    _with_cap(monkeypatch, cap)
    setup = await _setup(seeded_db)
    good = setup["figures"]["good"]
    other = await _second_lesson(seeded_db, setup, [])

    async def other_lesson_takes_it() -> None:
        factory = async_sessionmaker(_engine, expire_on_commit=False)
        async with factory() as session:
            await session.execute(
                update(CourseLesson)
                .where(CourseLesson.id == other.id)
                .values(
                    content_raw={
                        "introduction": "Testo.",
                        "sections": [],
                        "visual_assets": [_placed(good.id)],
                    }
                )
            )
            await session.commit()

    fakes["on_generate"] = other_lesson_takes_it
    await worker._process_one(setup["lesson_id"])
    lesson = await _lesson(seeded_db, setup["lesson_id"])
    assert lesson.content_status == "ready", lesson.content_error
    assert list(fakes["calls"][0]["source_figure_refs"]) == [figure_ref(good.id)]
    kept = [a for a in lesson.content_raw["visual_assets"] if a["format"] == "source_figure"]
    if cap > 1:
        assert [a["content"] for a in kept] == [str(good.id)]
        return
    assert not kept
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
    assert audits[0].payload["reason"] == "reuse_cap"
    assert audits[0].payload["cap"] == 1


async def test_parallel_lessons_respect_the_reuse_cap_under_the_course_lock(
    seeded_db: AsyncSession, fakes: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Due lezioni generate INSIEME scelgono la stessa figura con K=1: entrambe
    passano il primo ricontrollo (nessuna delle due ha ancora salvato), ma
    alla materializzazione il lock di corso le serializza e la seconda la
    toglie con audit `reuse_cap` (WP6)."""
    _with_cap(monkeypatch, 1)
    setup = await _setup(seeded_db)
    good = setup["figures"]["good"]
    first = await _lesson(seeded_db, setup["lesson_id"])
    other = await _second_lesson(seeded_db, setup, [])
    for field in ("title", "mandatory_topics", "section_outline", "learning_objectives", "summary"):
        setattr(other, field, getattr(first, field))
    other.content_status = "pending"
    await seeded_db.commit()
    both_generated = asyncio.Event()

    async def wait_for_both() -> None:
        if len(fakes["calls"]) >= 2:
            both_generated.set()
        await asyncio.wait_for(both_generated.wait(), timeout=30)

    fakes["on_generate"] = wait_for_both
    # Barriera dentro la materializzazione: senza lock entrano tutte e due
    # (e tengono la figura), col lock la seconda aspetta la prima e la
    # barriera scade dopo 1 s.
    inside = asyncio.Event()
    entered: list[uuid.UUID] = []
    original = content_svc.materialize_lesson_content

    async def barrier(db: AsyncSession, **kwargs: Any) -> Any:
        entered.append(kwargs["lesson"].id)
        if len(entered) >= 2:
            inside.set()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(inside.wait(), timeout=1.0)
        return await original(db, **kwargs)

    monkeypatch.setattr(content_svc, "materialize_lesson_content", barrier)
    await asyncio.gather(worker._process_one(first.id), worker._process_one(other.id))
    lessons = [await _lesson(seeded_db, lid) for lid in (first.id, other.id)]
    assert all(lesson.content_status == "ready" for lesson in lessons), [
        lesson.content_error for lesson in lessons
    ]
    assert all(figure_ref(good.id) in list(call["source_figure_refs"]) for call in fakes["calls"])
    holders = [
        lesson
        for lesson in lessons
        if any(a["content"] == str(good.id) for a in lesson.content_raw["visual_assets"])
    ]
    assert len(holders) == 1
    loser = next(lesson for lesson in lessons if lesson not in holders)
    audits = await _dropped_audits(seeded_db, loser.id)
    assert [a.payload["reason"] for a in audits] == ["reuse_cap"]


# --- piano delle figure nella Fase 3 (WP6, WP8) --------------------------------------


def _plan_settings(monkeypatch: pytest.MonkeyPatch, **updates: Any) -> None:
    from app.services import figure_plan_service as plan
    from app.services import source_figure_assignment_service as svc

    patched = get_settings().model_copy(
        update={"figure_source_enabled": True, "figure_plan_enabled": True, **updates}
    )
    for module in (plan, svc, source_figure_catalog):
        monkeypatch.setattr(module, "get_settings", lambda: patched)


async def _with_needs(db: AsyncSession, setup: dict[str, Any]) -> dict[str, Any]:
    """Fabbisogno must in S1 coperto dalla figura buona (depicts scanning)."""
    from app.services import figure_plan_service as plan
    from app.services import openai_figure_needs_service as needs_svc

    good = setup["figures"]["good"]
    good.depicts = {
        "v": 1,
        "items": [{"object_en": "laser Doppler vibrometer", "variant_en": "scanning"}],
        "focus": "optical layout",
    }
    course = await content_svc.load_course_full(db, course_id=setup["course_id"])
    assert course is not None
    lesson = find_lesson(course, "M1.L1")
    need = {
        "need_id": needs_svc.need_id("S1", "Schema a scansione"),
        "section_id": "S1",
        "subject": "Schema del vibrometro a scansione",
        "representation": "schematic",
        "focus": "",
        "priority": "must",
        "object_en": "laser Doppler vibrometer",
        "object_terms": ["LDV"],
        "variant_en": "scanning",
        "variant_terms": ["scanning"],
        "is_base": False,
        "sequence_group": "",
        "sequence_index": 0,
        "terms_course": ["vibrometro"],
        "terms_en": ["Bragg cell"],
    }
    item = plan.needs_input(course, lesson)
    assert item is not None
    plan.store_needs(
        lesson,
        result=needs_svc.NeedsResult([need], {}),
        fp=plan.fingerprint(item, plan.max_needs(lesson)),
        model="test",
        usage=None,
    )
    await db.commit()
    return need


async def test_the_plan_catalog_drives_the_prompt_and_the_snapshot_is_settled(
    seeded_db: AsyncSession, fakes: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _plan_settings(monkeypatch)
    setup = await _setup(seeded_db)
    good = setup["figures"]["good"]
    need = await _with_needs(seeded_db, setup)
    await worker._process_one(setup["lesson_id"])
    lesson = await _lesson(seeded_db, setup["lesson_id"])
    assert lesson.content_status == "ready", lesson.content_error
    prompt = fakes["calls"][0]["user_prompt"]
    assert "## Figure di fonte per sezione (catalogo del piano)" in prompt
    assert "### Sezione S1" in prompt and "N1 (obbligatoria)" in prompt
    data = lesson.figure_assignment
    assert data["state"] == "settled"
    assert data["bound"] == {need["need_id"]: str(good.id)}
    assert data["placement"]["needs"][need["need_id"]]["status"] == "placed"
    assert lesson.content_tokens["source_figures"]["plan"]["placed"] == 1


async def test_plan_out_of_the_prompt_keeps_the_lexical_catalog(
    seeded_db: AsyncSession, fakes: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _plan_settings(monkeypatch, figure_plan_in_prompt_enabled=False)
    setup = await _setup(seeded_db)
    await _with_needs(seeded_db, setup)
    await worker._process_one(setup["lesson_id"])
    lesson = await _lesson(seeded_db, setup["lesson_id"])
    assert lesson.content_status == "ready", lesson.content_error
    prompt = fakes["calls"][0]["user_prompt"]
    assert "catalogo del piano" not in prompt
    assert "## Figure di fonte disponibili (catalogo)" in prompt
    assert lesson.figure_assignment is None


async def test_a_failed_materialization_leaves_no_settled_snapshot(
    seeded_db: AsyncSession, fakes: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.errors import ConflictError

    _plan_settings(monkeypatch)
    setup = await _setup(seeded_db)
    await _with_needs(seeded_db, setup)

    async def broken(db: AsyncSession, **kwargs: Any) -> Any:
        raise ConflictError("obiettivi non coperti", code="x")

    monkeypatch.setattr(content_svc, "materialize_lesson_content", broken)
    await worker._process_one(setup["lesson_id"])
    lesson = await _lesson(seeded_db, setup["lesson_id"])
    assert lesson.content_status in ("pending", "failed")
    assert (lesson.figure_assignment or {}).get("state") != "settled"


async def test_a_cancel_during_the_lock_wait_is_not_overwritten(
    seeded_db: AsyncSession,
    fakes: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    _engine: Any,
) -> None:
    from app.services import source_figure_assignment_service as svc

    setup = await _setup(seeded_db)
    original = svc.course_lock

    async def cancel_then_lock(db: AsyncSession, course_id: uuid.UUID, **kw: Any) -> bool:
        factory = async_sessionmaker(_engine, expire_on_commit=False)
        async with factory() as other:
            await other.execute(
                update(CourseLesson)
                .where(CourseLesson.id == setup["lesson_id"])
                .values(content_status="failed", content_error="annullata")
            )
            await other.commit()
        return await original(db, course_id, **kw)

    monkeypatch.setattr(svc, "course_lock", cancel_then_lock)
    await worker._process_one(setup["lesson_id"])
    lesson = await _lesson(seeded_db, setup["lesson_id"])
    assert lesson.content_status == "failed" and lesson.content_raw is None


# --- risoluzione effettiva nel catalogo (doc 18 §22) --------------------------------

_TINY = {  # 120 px d'informazione su 50,8 mm: 60 ppi → unusable
    "width": 300,
    "height": 200,
    "dpi": 150,
    "native_ppi": 60.0,
    "is_vector": False,
    "bbox": {"l": 72.0, "t": 100.0, "r": 216.0, "b": 196.0, "page_w": 595.0, "page_h": 842.0},
}
_LOW = {  # 480 px su 101,6 mm: 120 ppi → low
    "width": 600,
    "height": 400,
    "dpi": 150,
    "native_ppi": 120.0,
    "is_vector": False,
    "bbox": {"l": 72.0, "t": 100.0, "r": 360.0, "b": 292.0, "page_w": 595.0, "page_h": 842.0},
}


def _with_rules(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    patched = get_settings().model_copy(update={"figure_resolution_rules_enabled": enabled})
    monkeypatch.setattr(source_figure_catalog, "get_settings", lambda: patched)


async def _catalog_order(seeded_db: AsyncSession, setup: dict[str, Any]) -> list[uuid.UUID]:
    course = await content_svc.load_course_full(seeded_db, course_id=setup["course_id"])
    assert course is not None
    result = await source_figure_catalog.build_catalog(
        seeded_db, course, find_lesson(course, "M1.L1")
    )
    return [c.figure_id for c in result.catalog.candidates] if result is not None else []


@pytest.mark.parametrize("enabled", [True, False])
async def test_catalog_excludes_unusable_and_puts_low_last(
    seeded_db: AsyncSession, fakes: dict[str, Any], monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    _with_rules(monkeypatch, enabled)
    setup = await _setup(seeded_db)
    # Stesso punteggio lessicale: senza la regola decide il locator (la low
    # viene prima), con la regola la low va in coda.
    low = _extra_figure(setup, locator="p0001-a-low", **_LOW)
    sharp = _extra_figure(setup, locator="p0001-z-sharp")
    tiny = _extra_figure(setup, locator="p0001-m-tiny", **_TINY)
    seeded_db.add_all([low, sharp, tiny])
    await seeded_db.commit()
    order = await _catalog_order(seeded_db, setup)
    if not enabled:
        assert {low.id, sharp.id, tiny.id} <= set(order)
        assert order.index(low.id) < order.index(sharp.id)
        return
    assert tiny.id not in order
    assert order.index(sharp.id) < order.index(low.id)
    assert source_figure_catalog.unsuitable_reason(tiny) == "resolution_unusable"
    assert source_figure_catalog.unsuitable_reason(low) is None


async def test_regenerating_does_not_offer_an_unusable_figure_it_already_has(
    seeded_db: AsyncSession, fakes: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sotto il minimo la figura non si ripropone neanche alla lezione che
    la usa; la collocazione esistente resta finché la lezione non si
    rigenera (U1)."""
    _with_rules(monkeypatch, True)
    setup = await _setup(seeded_db)
    tiny = _extra_figure(setup, **_TINY)
    seeded_db.add(tiny)
    await seeded_db.commit()
    lesson = await _lesson(seeded_db, setup["lesson_id"])
    lesson.content_raw = {"introduction": "T.", "sections": [], "visual_assets": [_placed(tiny.id)]}
    await seeded_db.commit()
    assert tiny.id not in await _catalog_order(seeded_db, setup)
