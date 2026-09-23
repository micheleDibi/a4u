"""Buchi di figure di fonte e letteratura aperta (I2, WP5).

Worker e servizio veri su DB; Wikimedia, OpenAlex e PROMPT 20 finti.

- lezione con abbastanza figure pertinenti → `done` senza alcuna chiamata;
- corso SENZA documenti → la letteratura integra: righe del catalogo
  senza documento (`source_kind=wikimedia`, attribuzione e licenza della
  fonte, `external_id`), subito proponibili per la lezione; nessun
  `CourseDocument` creato, `content_raw` della lezione non toccato (I2);
  costo in `figures_gap_usage` e nella dashboard admin (`figures_gap`);
- documenti con estrazione mai fatta (estrazione accesa) → `skipped`, niente
  rete; con l'estrazione spenta contano come senza figure;
- candidata non pertinente o duplicata (phash) → scartata;
- 429 → di nuovo `pending` con il costo già pagato conservato; oltre il
  tetto → `failed`;
- Fase 3: la lezione aspetta la verifica in coda (entro il tetto), riparte
  a verifica finita o scaduta; le verifiche non vanno alle lezioni di
  verifica.
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from PIL import Image, ImageDraw
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.course import Course
from app.models.course_document import CourseDocument
from app.models.course_document_figure import CourseDocumentFigure
from app.models.course_lesson import CourseLesson
from app.services import (
    admin_metrics_service,
    remote_storage,
    source_figure_catalog,
    wikimedia_client,
)
from app.services import course_lesson_content_worker as content_worker
from app.services import course_lesson_figures_gap_worker as gap_worker
from app.services import literature_figures_service as gaps
from app.services import openai_figure_relevance_service as relevance
from app.services.safe_http import FetchResult, SafeFetchError
from app.services.source_figure_service import resolve_source_figures
from app.services.wikimedia_client import CommonsFile
from tests.course_builders import build_course, build_course_document
from tests.source_figure_builders import build_document_figure

USAGE = {
    "model": "gpt-4.1-mini",
    "prompt": 100,
    "completion": 20,
    "total": 120,
    "cost_usd": 0.001,
}


class _Storage:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def upload_bytes(self, key: str, data: bytes) -> None:
        self.files[key] = data

    def download_bytes(self, key: str) -> bytes:
        if key not in self.files:
            raise remote_storage.StorageFileNotFound(key)
        return self.files[key]

    def delete(self, key: str) -> None:
        self.files.pop(key, None)


def _image(seed: int) -> bytes:
    """Schemi diversi fra loro (phash distinti)."""
    image = Image.new("RGB", (640, 400), "white")
    draw = ImageDraw.Draw(image)
    for i in range(seed + 2):
        x = 40 + (i * 97 * (seed + 1)) % 520
        draw.rectangle(
            [x, 60 + (i * 53) % 200, x + 80, 140 + (i * 53) % 200], outline="black", width=5
        )
        draw.line(
            [20, 20 + i * 30 * (seed + 1) % 360, 620, 380 - i * 41 % 360], fill="navy", width=4
        )
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _file(page_id: int, title: str) -> CommonsFile:
    return CommonsFile(
        page_id=page_id,
        title=f"File:{title}.svg",
        description_url=f"https://commons.wikimedia.org/wiki/File:{page_id}.svg",
        image_url=f"https://upload.wikimedia.org/x/{page_id}.png",
        mime="image/png",
        width=2000,
        height=1200,
        license="cc_by_sa",
        license_url="https://creativecommons.org/licenses/by-sa/4.0",
        author="Jane Doe",
        object_name=title,
        description=f"{title} schematic",
    )


def _verdict(relevant: bool = True) -> relevance.FigureRelevance:
    return relevance.FigureRelevance(
        relevant=relevant,
        kind="schematic",
        description="Schema del vibrometro laser Doppler con cella di Bragg e fotorivelatore.",
        keywords_course=["vibrometro laser Doppler", "cella di Bragg", "fotorivelatore"],
        keywords_en=["laser Doppler vibrometer", "Bragg cell"],
        quality_score=4,
        legibility="good",
        is_useful_for_teaching=True,
        reason="ok",
    )


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, _engine: Any) -> dict[str, Any]:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    storage = _Storage()
    monkeypatch.setattr(remote_storage, "get_storage", lambda: storage)
    settings = get_settings().model_copy(
        update={
            "figure_source_enabled": True,
            "figure_literature_enabled": True,
            "figure_extraction_enabled": False,
            "openalex_api_key": None,
        }
    )
    for module in (gaps, gap_worker, content_worker, source_figure_catalog):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    factory = async_sessionmaker(_engine, expire_on_commit=False)
    monkeypatch.setattr(gap_worker, "async_session_factory", factory)
    monkeypatch.setattr(content_worker, "async_session_factory", factory)
    state: dict[str, Any] = {
        "calls": [],
        "files": [_file(101, "Laser Doppler vibrometer"), _file(102, "Unrelated bridge")],
        "irrelevant": {"Unrelated bridge"},
        "images": {101: _image(1), 102: _image(2)},
        "search_error": None,
        "settings": settings,
        "storage": storage,
    }

    async def search_terms(context: relevance.LessonContext) -> tuple[list[str], dict[str, Any]]:
        state["calls"].append(("terms", context.title))
        return ["laser doppler vibrometer"], dict(USAGE)

    async def search_files(query: str, *, limit: int, language: str, **kw: Any) -> Any:
        state["calls"].append(("wikimedia", query))
        if state["search_error"] is not None:
            raise state["search_error"]
        return list(state["files"])

    async def download_image(item: CommonsFile, **kw: Any) -> FetchResult:
        state["calls"].append(("download", item.page_id))
        return FetchResult(
            content=state["images"][item.page_id],
            kind="png",
            final_url=item.image_url,
            status=200,
            content_type="image/png",
        )

    async def assess(image: bytes, context: Any, *, source_title: Any, source_text: Any) -> Any:
        state["calls"].append(("assess", source_title))
        return _verdict(source_title not in state["irrelevant"]), dict(USAGE)

    monkeypatch.setattr(relevance, "search_terms", search_terms)
    monkeypatch.setattr(relevance, "assess_candidate", assess)
    monkeypatch.setattr(wikimedia_client, "search_files", search_files)
    monkeypatch.setattr(wikimedia_client, "download_image", download_image)
    return state


async def _lesson(db: AsyncSession, **course_kw: Any) -> tuple[uuid.UUID, uuid.UUID]:
    course_id, _org, _user = await build_course(
        db, modules=1, lessons_per_module=1, content_status="pending", **course_kw
    )
    lesson = (
        (await db.execute(select(CourseLesson).where(CourseLesson.course_id == course_id)))
        .scalars()
        .one()
    )
    lesson.title = "Vibrometria laser Doppler"
    lesson.mandatory_topics = [{"topic_id": "T1", "title": "Vibrometro laser Doppler"}]
    lesson.section_outline = [
        {"section_id": "S1", "title": "Il vibrometro laser Doppler", "purpose": "Schema"}
    ]
    lesson.learning_objectives = ["Descrivere il vibrometro laser Doppler"]
    lesson.summary = "Vibrometro laser Doppler e cella di Bragg."
    lesson.content_raw = {"introduction": "Testo della lezione.", "sections": []}
    lesson.figures_gap_status = "pending"
    lesson.figures_gap_requested_at = datetime.now(UTC)
    await db.commit()
    return course_id, lesson.id


async def _fresh(db: AsyncSession, lesson_id: uuid.UUID) -> CourseLesson:
    lesson = await db.get(CourseLesson, lesson_id, populate_existing=True)
    assert lesson is not None
    return lesson


async def test_enough_pertinent_figures_means_no_calls(
    seeded_db: AsyncSession, env: dict[str, Any]
) -> None:
    course_id, lesson_id = await _lesson(seeded_db)
    doc = build_course_document(course_id, filename="dispensa.pdf")
    doc.figures_status = "ready"
    seeded_db.add(doc)
    await seeded_db.flush()
    seeded_db.add(
        build_document_figure(
            course_id,
            doc.id,
            license="cc_by",
            description="Schema del vibrometro laser Doppler con la cella di Bragg",
            keywords={"course": ["vibrometro laser Doppler", "cella di Bragg"], "en": []},
        )
    )
    await seeded_db.commit()
    await gap_worker._tick()
    lesson = await _fresh(seeded_db, lesson_id)
    assert lesson.figures_gap_status == "done"
    assert lesson.figures_gap_stats["reason"] == "enough"
    assert env["calls"] == [] and lesson.figures_gap_usage is None


async def test_course_without_documents_gets_open_literature_figures(
    seeded_db: AsyncSession, env: dict[str, Any]
) -> None:
    course_id, lesson_id = await _lesson(seeded_db)
    before = (await _fresh(seeded_db, lesson_id)).content_raw
    await gap_worker._tick()
    lesson = await _fresh(seeded_db, lesson_id)
    assert lesson.figures_gap_status == "done", lesson.figures_gap_stats
    rows = list(
        (
            await seeded_db.execute(
                select(CourseDocumentFigure).where(CourseDocumentFigure.course_id == course_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    row = rows[0]
    assert (row.source_kind, row.document_id, row.status) == ("wikimedia", None, "ready")
    assert row.external_id == "commons:101" and row.license == "cc_by_sa"
    assert row.license_source == "wikimedia" and row.source_url.endswith("101.svg")
    assert row.attribution["container"] == "Wikimedia Commons"
    assert remote_storage.uploads_key(str(row.storage_path)) in env["storage"].files
    # I2: nessun documento nuovo, testo della lezione intatto.
    docs = await seeded_db.scalar(
        select(func.count(CourseDocument.id)).where(CourseDocument.course_id == course_id)
    )
    assert docs == 0 and lesson.content_raw == before
    # Costo: termini + 2 valutazioni (una non pertinente), dashboard admin.
    assert lesson.figures_gap_usage["calls"] == 3
    assert lesson.figures_gap_usage["cost_usd"] == pytest.approx(0.003)
    assert lesson.figures_gap_stats["kept"] == 1
    assert lesson.figures_gap_stats["not_relevant"] == 1
    now = datetime.now(UTC)
    cost = await admin_metrics_service._cost(
        seeded_db, cutoff_7d=now - timedelta(days=7), cutoff_30d=now - timedelta(days=30)
    )
    phases = {p.phase: p.cost_usd for p in cost.by_phase}
    assert phases["figures_gap"] >= 0.003
    # Subito proponibile per la lezione e resa con la sua riga «Fonte».
    course = await seeded_db.get(Course, course_id)
    assert course is not None
    catalog = await source_figure_catalog.build_catalog(seeded_db, course, lesson)
    assert catalog is not None
    assert [c.figure_id for c in catalog.catalog.candidates] == [row.id]
    resolved = await resolve_source_figures(
        seeded_db,
        course_id=course_id,
        assets=[{"asset_id": "SRC-x", "format": "source_figure", "content": str(row.id)}],
        language="it",
    )
    assert resolved["SRC-x"].renderable
    assert resolved["SRC-x"].attribution_text == (
        "Fonte: Jane Doe, «Laser Doppler vibrometer», Wikimedia Commons (CC BY-SA)"
    )


async def test_documents_not_extracted_skip_the_literature(
    seeded_db: AsyncSession, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    course_id, lesson_id = await _lesson(seeded_db)
    seeded_db.add(build_course_document(course_id, filename="mai_estratto.pdf"))
    await seeded_db.commit()
    extraction_on = env["settings"].model_copy(update={"figure_extraction_enabled": True})
    monkeypatch.setattr(gaps, "get_settings", lambda: extraction_on)
    await gap_worker._tick()
    lesson = await _fresh(seeded_db, lesson_id)
    assert lesson.figures_gap_status == "skipped"
    assert lesson.figures_gap_stats["reason"] == "documents_not_extracted"
    assert env["calls"] == []
    # Con l'estrazione spenta i documenti non avranno mai figure: si cerca.
    monkeypatch.setattr(gaps, "get_settings", lambda: env["settings"])
    await seeded_db.execute(
        update(CourseLesson)
        .where(CourseLesson.id == lesson_id)
        .values(figures_gap_status="pending")
    )
    await seeded_db.commit()
    await gap_worker._tick()
    assert (await _fresh(seeded_db, lesson_id)).figures_gap_status == "done"
    assert ("wikimedia", "laser doppler vibrometer") in env["calls"]


async def test_duplicate_candidate_is_not_evaluated(
    seeded_db: AsyncSession, env: dict[str, Any]
) -> None:
    _course_id, lesson_id = await _lesson(seeded_db)
    env["images"][102] = env["images"][101]
    env["irrelevant"] = set()
    await gap_worker._tick()
    lesson = await _fresh(seeded_db, lesson_id)
    assert lesson.figures_gap_stats["duplicates"] == 1
    assert [c for c in env["calls"] if c[0] == "assess"] == [("assess", "Laser Doppler vibrometer")]


async def test_rate_limit_retries_then_fails_keeping_the_cost(
    seeded_db: AsyncSession, env: dict[str, Any]
) -> None:
    _course_id, lesson_id = await _lesson(seeded_db)
    env["search_error"] = SafeFetchError("rate_limited", "429", status=429)
    await gap_worker._tick()
    lesson = await _fresh(seeded_db, lesson_id)
    assert (lesson.figures_gap_status, lesson.figures_gap_attempts) == ("pending", 1)
    assert lesson.figures_gap_usage["calls"] == 1  # termini di ricerca pagati
    # Backoff: il tick successivo non la riprende subito.
    await gap_worker._tick()
    assert (await _fresh(seeded_db, lesson_id)).figures_gap_attempts == 1
    limit = int(env["settings"].figure_literature_auto_retry_max)
    for _ in range(limit):
        await seeded_db.execute(
            update(CourseLesson)
            .where(CourseLesson.id == lesson_id)
            .values(figures_gap_checked_at=datetime.now(UTC) - timedelta(hours=1))
        )
        await seeded_db.commit()
        await gap_worker._tick()
    lesson = await _fresh(seeded_db, lesson_id)
    assert lesson.figures_gap_status == "failed"
    assert lesson.figures_gap_usage["calls"] == limit + 1


async def test_phase_3_waits_for_the_gap_check(
    seeded_db: AsyncSession, env: dict[str, Any]
) -> None:
    course_id, lesson_id = await _lesson(seeded_db)
    await seeded_db.execute(
        update(CourseLesson)
        .where(CourseLesson.id == lesson_id)
        .values(figures_gap_status=None, figures_gap_requested_at=None)
    )
    assessment = CourseLesson(
        course_id=course_id,
        module_id=(await _fresh(seeded_db, lesson_id)).module_id,
        lesson_code="M1.V",
        title="Verifica",
        position=9,
        is_assessment=True,
        content_status="pending",
    )
    seeded_db.add(assessment)
    await seeded_db.commit()

    async def ready_ids() -> set[uuid.UUID]:
        async with gap_worker.async_session_factory() as db:
            await content_worker._request_figure_gaps(db)
            rows = await db.execute(content_worker._pending_lessons_query())
            return {r[0] for r in rows.all()}

    first = await ready_ids()
    lesson = await _fresh(seeded_db, lesson_id)
    assert lesson.figures_gap_status == "pending" and lesson_id not in first
    assert assessment.id in first
    assert (await _fresh(seeded_db, assessment.id)).figures_gap_status is None
    # Verifica finita → la lezione parte.
    await gap_worker._tick()
    assert lesson_id in await ready_ids()
    # Verifica scaduta (oltre il tetto) → parte comunque.
    await seeded_db.execute(
        update(CourseLesson)
        .where(CourseLesson.id == lesson_id)
        .values(
            figures_gap_status="processing",
            figures_gap_requested_at=datetime.now(UTC) - timedelta(hours=2),
        )
    )
    await seeded_db.commit()
    assert lesson_id in await ready_ids()
