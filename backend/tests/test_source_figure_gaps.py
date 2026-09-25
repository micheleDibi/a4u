"""Buchi di figure di fonte e letteratura aperta (I2, WP5).

Worker e servizio veri su DB; Wikimedia, OpenAlex e PROMPT 20 finti.

- lezione con abbastanza figure pertinenti → `done` senza alcuna chiamata;
- corso SENZA documenti → la letteratura integra: righe del catalogo
  senza documento (`source_kind=wikimedia`, attribuzione e licenza della
  fonte, `external_id`), subito proponibili per la lezione; nessun
  `CourseDocument` creato, `content_raw` della lezione non toccato (I2);
  costo in `figures_gap_usage` e nella dashboard admin (`figures_gap`);
- documento con estrazione mai richiesta → la letteratura parte lo stesso;
  estrazione in corso da poco → `skipped` (`documents_extracting`), oltre il
  tetto di attesa non blocca più;
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


def _file(page_id: int, title: str, **original: Any) -> CommonsFile:
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
        **original,
    )


def _verdict(relevant: bool = True, text_language: str = "it") -> relevance.FigureRelevance:
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
        text_language=text_language,
    )


@pytest.fixture(autouse=True)
async def _isolate_gap_queue(seeded_db: AsyncSession) -> None:
    """Il DB dei test è condiviso fra i file: le lezioni in coda lasciate da
    altri test non devono entrare nella coda dei buchi di questi."""
    await seeded_db.execute(
        update(CourseLesson)
        .where(
            (CourseLesson.figures_gap_status.is_(None))
            | (CourseLesson.figures_gap_status.in_(("pending", "processing")))
        )
        .values(figures_gap_status="done")
    )
    await seeded_db.commit()


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
        "languages": {},
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
        verdict = _verdict(
            source_title not in state["irrelevant"],
            text_language=state["languages"].get(source_title, "it"),
        )
        return verdict, dict(USAGE)

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


@pytest.mark.parametrize("cap", [1, 2])
async def test_a_figure_at_the_reuse_cap_does_not_fill_the_gap(
    seeded_db: AsyncSession, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, cap: int
) -> None:
    """La figura pertinente è già collocata in un'altra lezione. Con K=1 non
    conta e si cerca in letteratura (erano le 7 figure di Commons riusate in
    quasi tutte le lezioni); con K=2 ha ancora un posto e copre il buco."""
    patched = get_settings().model_copy(update={"figure_source_max_lessons_per_figure": cap})
    monkeypatch.setattr(source_figure_catalog, "get_settings", lambda: patched)
    course_id, lesson_id = await _lesson(seeded_db)
    doc = build_course_document(course_id, filename="dispensa.pdf")
    doc.figures_status = "ready"
    seeded_db.add(doc)
    await seeded_db.flush()
    figure = build_document_figure(
        course_id,
        doc.id,
        license="cc_by",
        description="Schema del vibrometro laser Doppler con la cella di Bragg",
        keywords={"course": ["vibrometro laser Doppler", "cella di Bragg"], "en": []},
    )
    seeded_db.add(figure)
    lesson = await _fresh(seeded_db, lesson_id)
    seeded_db.add(
        CourseLesson(
            module_id=lesson.module_id,
            course_id=course_id,
            position=2,
            lesson_code="M1.L2",
            title="Applicazioni",
            summary="Applicazioni.",
            learning_objectives=[],
            mandatory_topics=[],
            prerequisites=[],
            section_outline=[],
            content_status="ready",
            figures_gap_status="done",
            content_raw={
                "visual_assets": [
                    {"asset_id": "SRC-a", "format": "source_figure", "content": str(figure.id)}
                ]
            },
        )
    )
    await seeded_db.commit()
    await gap_worker._tick()
    lesson = await _fresh(seeded_db, lesson_id)
    assert lesson.figures_gap_status == "done"
    if cap > 1:
        assert lesson.figures_gap_stats == {"pertinent_before": 1, "budget": 4, "reason": "enough"}
        assert env["calls"] == []
        return
    assert lesson.figures_gap_stats.get("reason") != "enough", lesson.figures_gap_stats
    assert lesson.figures_gap_stats["pertinent_before"] == 0
    assert ("wikimedia", "laser doppler vibrometer") in env["calls"]


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


async def test_a_never_extracted_document_does_not_block_the_literature(
    seeded_db: AsyncSession, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Estrazione mai richiesta (figures_status NULL) con l'estrazione accesa:
    le sue figure non sono in arrivo, la letteratura parte (WP1.1)."""
    course_id, lesson_id = await _lesson(seeded_db)
    seeded_db.add(build_course_document(course_id, filename="mai_estratto.pdf"))
    await seeded_db.commit()
    extraction_on = env["settings"].model_copy(update={"figure_extraction_enabled": True})
    monkeypatch.setattr(gaps, "get_settings", lambda: extraction_on)
    await gap_worker._tick()
    assert (await _fresh(seeded_db, lesson_id)).figures_gap_status == "done"
    assert ("wikimedia", "laser doppler vibrometer") in env["calls"]


async def test_an_extraction_in_progress_is_awaited_within_the_cap(
    seeded_db: AsyncSession, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Estrazione in corso richiesta da poco: la verifica aspetta (skipped,
    niente rete). Oltre `FIGURE_WAIT_MAX_MINUTES` non blocca più."""
    course_id, lesson_id = await _lesson(seeded_db)
    doc = build_course_document(course_id, filename="in_corso.pdf")
    doc.figures_status = "processing"
    doc.figures_requested_at = datetime.now(UTC)
    seeded_db.add(doc)
    await seeded_db.commit()
    extraction_on = env["settings"].model_copy(update={"figure_extraction_enabled": True})
    monkeypatch.setattr(gaps, "get_settings", lambda: extraction_on)
    lesson = await _fresh(seeded_db, lesson_id)
    outcome = await gaps.check_lesson(seeded_db, lesson)
    assert outcome.status == "skipped"
    assert outcome.stats["reason"] == "documents_extracting"
    assert env["calls"] == []
    # Con l'estrazione spenta nessun documento è «in estrazione».
    extraction_off = env["settings"].model_copy(update={"figure_extraction_enabled": False})
    monkeypatch.setattr(gaps, "get_settings", lambda: extraction_off)
    assert await gaps.documents_extracting(seeded_db, course_id) == 0
    monkeypatch.setattr(gaps, "get_settings", lambda: extraction_on)
    # Un `processing` senza data di richiesta (righe vecchie) non blocca.
    stale = build_course_document(course_id, filename="senza_data.pdf")
    stale.figures_status = "processing"
    stale.figures_requested_at = None
    seeded_db.add(stale)
    await seeded_db.commit()
    assert await gaps.documents_extracting(seeded_db, course_id) == 1
    doc.figures_requested_at = datetime.now(UTC) - timedelta(
        minutes=int(extraction_on.figure_wait_max_minutes) + 1
    )
    await seeded_db.commit()
    assert await gaps.documents_extracting(seeded_db, course_id) == 0


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


@pytest.fixture(scope="module")
def fixture_pdf(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    from tests.fixtures.source_figures.build import PDF_NAME, build_all

    directory = tmp_path_factory.mktemp("gap_fixtures")
    build_all(directory)
    return (directory / PDF_NAME).read_bytes()


async def test_openalex_figures_come_from_the_open_access_pdf(
    seeded_db: AsyncSession,
    env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    fixture_pdf: bytes,
) -> None:
    """Con la chiave OpenAlex: PDF open access del lavoro, ritagli estratti
    dal processo figlio vero (motore euristico), Vision sulle didascalie
    più vicine alla lezione, riga con attribuzione del lavoro e licenza
    della location, `external_id` = lavoro#ritaglio."""
    from app.services import course_document_figures_worker as figures_worker
    from app.services import openalex_client
    from app.services.openalex_client import _to_work

    settings = env["settings"].model_copy(
        update={
            "openalex_api_key": "k-test",
            "figure_extraction_enabled": True,
            "figure_extraction_engine": "heuristic",
            "figure_extraction_block_pages": 2,
        }
    )
    monkeypatch.setattr(gaps, "get_settings", lambda: settings)
    monkeypatch.setattr(figures_worker, "get_settings", lambda: settings)
    monkeypatch.setattr(figures_worker, "mem_available_mb", lambda: None)
    figures_worker._reset_probe_for_tests()
    env["files"] = []  # Wikimedia non trova nulla: tocca a OpenAlex.
    work = _to_work(
        {
            "id": "https://openalex.org/W42",
            "doi": "https://doi.org/10.1000/ldv",
            "title": "Vibrometria laser Doppler",
            "authorships": [{"author": {"display_name": "Mario Rossi"}}],
            "publication_year": 2021,
            "primary_location": {"source": {"display_name": "Misure"}},
            "open_access": {"is_oa": True},
            "best_oa_location": {
                "pdf_url": "https://example.org/ldv.pdf",
                "landing_page_url": "https://example.org/ldv",
                "license": "cc-by",
            },
        }
    )

    async def search_open_works(query: str, *, per_page: int) -> list[Any]:
        env["calls"].append(("openalex", query))
        return [work]

    async def download_pdf(url: str, *, max_bytes: int) -> bytes:
        env["calls"].append(("pdf", url))
        return fixture_pdf

    async def assess(image: bytes, context: Any, *, source_title: Any, source_text: Any) -> Any:
        env["calls"].append(("assess", source_text))
        return _verdict("vibrometro laser" in str(source_text).lower()), dict(USAGE)

    monkeypatch.setattr(openalex_client, "search_open_works", search_open_works)
    monkeypatch.setattr(openalex_client, "download_pdf", download_pdf)
    monkeypatch.setattr(relevance, "assess_candidate", assess)
    course_id, lesson_id = await _lesson(seeded_db)
    await gap_worker._tick()
    lesson = await _fresh(seeded_db, lesson_id)
    assert lesson.figures_gap_status == "done", lesson.figures_gap_stats
    assert ("pdf", "https://example.org/ldv.pdf") in env["calls"]
    rows = list(
        (
            await seeded_db.execute(
                select(CourseDocumentFigure).where(CourseDocumentFigure.course_id == course_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1, [r.source_caption for r in rows]
    row = rows[0]
    assert (row.source_kind, row.document_id, row.license) == ("openalex", None, "cc_by")
    assert row.external_id.startswith("https://openalex.org/W42#")
    assert row.page == 1 and "vibrometro laser Doppler" in str(row.source_caption)
    assert row.attribution["authors"] == ["Mario Rossi"]
    assert row.attribution["figure_number"] == "2.1"
    assert row.source_url == "https://example.org/ldv"
    # Ingressi della risoluzione del ritaglio (prima si perdevano, D9).
    assert row.crop_version == 2 and row.crop_mode in ("raster_native", "mixed", "vector")
    assert row.dpi and row.bbox and isinstance(row.is_vector, bool)
    assert row.natural_width_mm and row.natural_width_mm > 0
    resolved = await resolve_source_figures(
        seeded_db,
        course_id=course_id,
        assets=[{"asset_id": "SRC-y", "format": "source_figure", "content": str(row.id)}],
        language="it",
    )
    assert resolved["SRC-y"].attribution_text == (
        "Fonte: Mario Rossi, «Vibrometria laser Doppler», Misure, 2021, fig. 2.1, p. 1 (CC BY)"
    )


async def test_blocked_publishers_fall_back_to_the_openalex_copy(
    seeded_db: AsyncSession,
    env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    fixture_pdf: bytes,
) -> None:
    """MDPI risponde 403 ai download automatici: si prende la copia ospitata
    da OpenAlex; lo stesso editore non si riprova nel giro; a credito finito
    (429) la copia di OpenAlex non si chiede più."""
    from app.services import course_document_figures_worker as figures_worker
    from app.services import openalex_client
    from app.services.openalex_client import OpenAlexError, _to_work

    settings = env["settings"].model_copy(
        update={
            "openalex_api_key": "k-test",
            "figure_extraction_enabled": True,
            "figure_extraction_engine": "heuristic",
            "figure_extraction_block_pages": 2,
        }
    )
    monkeypatch.setattr(gaps, "get_settings", lambda: settings)
    monkeypatch.setattr(figures_worker, "get_settings", lambda: settings)
    monkeypatch.setattr(figures_worker, "mem_available_mb", lambda: None)
    figures_worker._reset_probe_for_tests()
    env["files"] = []

    def mdpi_work(n: int) -> Any:
        return _to_work(
            {
                "id": f"https://openalex.org/W{n}",
                "title": "Vibrometria laser Doppler",
                "authorships": [{"author": {"display_name": "Mario Rossi"}}],
                "publication_year": 2021,
                "primary_location": {"source": {"display_name": "Sensors"}},
                "open_access": {"is_oa": True},
                "best_oa_location": {
                    "pdf_url": f"https://www.mdpi.com/{n}/pdf",
                    "landing_page_url": f"https://www.mdpi.com/{n}",
                    "license": "cc-by",
                },
                "has_content": {"pdf": True},
            }
        )

    works = [mdpi_work(1), mdpi_work(2), mdpi_work(3)]
    content_answers: list[Any] = [fixture_pdf]

    async def search_open_works(query: str, *, per_page: int) -> list[Any]:
        return works

    async def download_pdf(url: str, *, max_bytes: int) -> bytes:
        env["calls"].append(("publisher", url))
        raise OpenAlexError(status=403, message="HTTP 403")

    async def download_content_pdf(work: Any, *, max_bytes: int) -> bytes:
        env["calls"].append(("content", work.id))
        answer = content_answers.pop(0) if content_answers else None
        if answer is None:
            raise OpenAlexError(status=429, message="credito finito")
        return answer

    async def assess(image: bytes, context: Any, *, source_title: Any, source_text: Any) -> Any:
        return _verdict(True), dict(USAGE)

    monkeypatch.setattr(openalex_client, "search_open_works", search_open_works)
    monkeypatch.setattr(openalex_client, "download_pdf", download_pdf)
    monkeypatch.setattr(openalex_client, "download_content_pdf", download_content_pdf)
    monkeypatch.setattr(relevance, "assess_candidate", assess)
    course_id, lesson_id = await _lesson(seeded_db)
    await gap_worker._tick()
    lesson = await _fresh(seeded_db, lesson_id)
    stats = lesson.figures_gap_stats
    assert stats["downloads_openalex"] == 1, stats
    assert stats["kept"] >= 1, stats
    # L'editore che ha risposto 403 si prova una volta sola nel giro.
    assert [c for c in env["calls"] if c[0] == "publisher"] == [
        ("publisher", "https://www.mdpi.com/1/pdf")
    ]
    # Credito finito al secondo lavoro (429): il terzo non chiede la copia.
    assert [c[1] for c in env["calls"] if c[0] == "content"] == [
        "https://openalex.org/W1",
        "https://openalex.org/W2",
    ]
    assert stats["download_errors"] == 2, stats
    rows = list(
        (
            await seeded_db.execute(
                select(CourseDocumentFigure).where(CourseDocumentFigure.course_id == course_id)
            )
        )
        .scalars()
        .all()
    )
    assert rows and all(r.source_kind == "openalex" and r.license == "cc_by" for r in rows)


async def test_gap_is_checked_only_before_phase_3(
    seeded_db: AsyncSession, env: dict[str, Any]
) -> None:
    """Una verifica rimasta in coda dopo la partenza della Fase 3 si chiude
    senza costi (il contenuto è già scritto)."""
    _course_id, lesson_id = await _lesson(seeded_db)
    await seeded_db.execute(
        update(CourseLesson).where(CourseLesson.id == lesson_id).values(content_status="ready")
    )
    await seeded_db.commit()
    await gap_worker._tick()
    lesson = await _fresh(seeded_db, lesson_id)
    assert lesson.figures_gap_status == "skipped"
    assert lesson.figures_gap_stats == {"reason": "phase3_started"}
    assert env["calls"] == []


async def test_user_regeneration_repeats_a_skipped_check(
    seeded_db: AsyncSession, env: dict[str, Any]
) -> None:
    from app.services.course_lesson_content_service import _reset_figure_gap

    _course_id, lesson_id = await _lesson(seeded_db)
    lesson = await _fresh(seeded_db, lesson_id)
    lesson.figures_gap_status = "skipped"
    lesson.figures_gap_attempts = 2
    _reset_figure_gap(lesson)
    assert (lesson.figures_gap_status, lesson.figures_gap_attempts) == (None, 0)
    lesson.figures_gap_status = "done"
    _reset_figure_gap(lesson)
    assert lesson.figures_gap_status == "done"  # la letteratura è già stata consultata


async def test_unexpected_errors_keep_the_cost_and_retry(
    seeded_db: AsyncSession, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _course_id, lesson_id = await _lesson(seeded_db)

    async def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("figlio caduto")

    monkeypatch.setattr(relevance, "assess_candidate", broken)
    await gap_worker._tick()
    lesson = await _fresh(seeded_db, lesson_id)
    assert (lesson.figures_gap_status, lesson.figures_gap_attempts) == ("pending", 1)
    assert lesson.figures_gap_usage["calls"] == 1  # termini di ricerca pagati
    assert "figlio caduto" in lesson.figures_gap_stats["error"]


async def test_interrupted_checks_count_as_attempts(
    seeded_db: AsyncSession, env: dict[str, Any]
) -> None:
    _course_id, lesson_id = await _lesson(seeded_db)
    limit = int(env["settings"].figure_literature_auto_retry_max)
    await seeded_db.execute(
        update(CourseLesson)
        .where(CourseLesson.id == lesson_id)
        .values(figures_gap_status="processing", figures_gap_attempts=limit - 1)
    )
    await seeded_db.commit()
    await gap_worker.reset_interrupted()
    lesson = await _fresh(seeded_db, lesson_id)
    assert (lesson.figures_gap_status, lesson.figures_gap_attempts) == ("pending", limit)
    await seeded_db.execute(
        update(CourseLesson)
        .where(CourseLesson.id == lesson_id)
        .values(figures_gap_status="processing")
    )
    await seeded_db.commit()
    await gap_worker.reset_interrupted()
    assert (await _fresh(seeded_db, lesson_id)).figures_gap_status == "failed"


async def test_non_open_licenses_never_reach_the_vision(
    seeded_db: AsyncSession, env: dict[str, Any]
) -> None:
    course_id, _lesson_id = await _lesson(seeded_db)
    run = gaps._Run(
        course_id=course_id,
        profile={},
        context=relevance.LessonContext("L", (), (), "it"),
        target=1,
        max_candidates=5,
        deadline=10**12,
        hashes=[],
        known_ids=set(),
    )
    kept = await gaps._consider(
        seeded_db,
        run,
        image_bytes=_image(1),
        source_kind="openalex",
        locator="oa-w1-p0001",
        external_id="W1#p0001",
        source_title="T",
        source_text=None,
        license="cc_by_nc",
        license_url=None,
        attribution={"title": "T"},
        source_url=None,
        caption=None,
    )
    assert kept is False and run.stats == {"license_not_open": 1}
    no_attr = await gaps._consider(
        seeded_db,
        run,
        image_bytes=_image(1),
        source_kind="openalex",
        locator="oa-w1-p0002",
        external_id="W1#p0002",
        source_title=None,
        source_text=None,
        license="cc_by",
        license_url=None,
        attribution={"year": 2020},
        source_url=None,
        caption=None,
    )
    assert no_attr is False and run.stats["attribution_missing"] == 1
    assert [c for c in env["calls"] if c[0] == "assess"] == []


async def test_external_file_names_are_not_derivable(
    seeded_db: AsyncSession, env: dict[str, Any]
) -> None:
    course_id, _lesson_id = await _lesson(seeded_db)
    await gap_worker._tick()
    (row,) = (
        (
            await seeded_db.execute(
                select(CourseDocumentFigure).where(CourseDocumentFigure.course_id == course_id)
            )
        )
        .scalars()
        .all()
    )
    name = str(row.storage_path).rsplit("/", 1)[-1]
    # wm-<pageid>-<casuale 8>-<sha12>.png
    parts = name.removesuffix(".png").split("-")
    assert parts[:2] == ["wm", "101"] and len(parts[2]) == 8 and len(parts[3]) == 12


async def test_caps_and_quality_filters(
    seeded_db: AsyncSession, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tetto per corso, candidate massime e filtri sul verdetto."""
    course_id, lesson_id = await _lesson(seeded_db)
    # Tetto per corso già raggiunto: nessuna chiamata.
    capped = env["settings"].model_copy(update={"figure_literature_max_per_course": 0})
    monkeypatch.setattr(gaps, "get_settings", lambda: capped)
    await gap_worker._tick()
    lesson = await _fresh(seeded_db, lesson_id)
    assert lesson.figures_gap_stats["reason"] == "course_cap" and env["calls"] == []
    # Nessuna candidata ammessa: nessuna chiamata, nemmeno i termini.
    none = env["settings"].model_copy(update={"figure_literature_max_candidates_per_lesson": 0})
    monkeypatch.setattr(gaps, "get_settings", lambda: none)
    await seeded_db.execute(
        update(CourseLesson)
        .where(CourseLesson.id == lesson_id)
        .values(figures_gap_status="pending")
    )
    await seeded_db.commit()
    await gap_worker._tick()
    lesson = await _fresh(seeded_db, lesson_id)
    assert lesson.figures_gap_stats["reason"] == "no_room" and env["calls"] == []
    # Verdetti pertinenti ma non utili, di qualità bassa o loghi: scartati.
    monkeypatch.setattr(gaps, "get_settings", lambda: env["settings"])
    verdicts = iter(
        [
            _verdict().model_copy(update={"is_useful_for_teaching": False}),
            _verdict().model_copy(update={"quality_score": 1}),
        ]
    )

    async def assess(image: bytes, context: Any, **kw: Any) -> Any:
        return next(verdicts), dict(USAGE)

    monkeypatch.setattr(relevance, "assess_candidate", assess)
    await seeded_db.execute(
        update(CourseLesson)
        .where(CourseLesson.id == lesson_id)
        .values(figures_gap_status="pending")
    )
    await seeded_db.commit()
    await gap_worker._tick()
    lesson = await _fresh(seeded_db, lesson_id)
    assert lesson.figures_gap_stats["not_relevant"] == 2 and lesson.figures_gap_stats["kept"] == 0
    count = await seeded_db.scalar(
        select(func.count(CourseDocumentFigure.id)).where(
            CourseDocumentFigure.course_id == course_id
        )
    )
    assert count == 0


async def test_vision_rate_limit_retries_with_the_paid_usage(
    seeded_db: AsyncSession, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _course_id, lesson_id = await _lesson(seeded_db)

    async def limited(image: bytes, context: Any, **kw: Any) -> Any:
        raise relevance.OpenAIFigureRelevanceError(
            status=429, message="rate limit", usage=dict(USAGE)
        )

    monkeypatch.setattr(relevance, "assess_candidate", limited)
    await gap_worker._tick()
    lesson = await _fresh(seeded_db, lesson_id)
    assert (lesson.figures_gap_status, lesson.figures_gap_attempts) == ("pending", 1)
    # Termini + la valutazione rifiutata (pagata anche se 429 in coda).
    assert lesson.figures_gap_usage["calls"] == 2


async def test_phase_3_tick_requests_the_gap_check(
    seeded_db: AsyncSession, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`content_worker._tick` stesso marca la verifica e non avvia la lezione."""
    _course_id, lesson_id = await _lesson(seeded_db)
    await seeded_db.execute(
        update(CourseLesson)
        .where(CourseLesson.id == lesson_id)
        .values(figures_gap_status=None, figures_gap_requested_at=None)
    )
    await seeded_db.commit()
    dispatched: list[uuid.UUID] = []

    async def record(lid: uuid.UUID) -> None:
        dispatched.append(lid)

    monkeypatch.setattr(content_worker, "_bound_process", record)
    monkeypatch.setattr(content_worker, "_inflight", set())
    await content_worker._tick()
    import asyncio

    await asyncio.sleep(0)
    lesson = await _fresh(seeded_db, lesson_id)
    assert lesson.figures_gap_status == "pending" and lesson_id not in dispatched
    # Con la letteratura spenta: niente attesa, niente richiesta.
    off = env["settings"].model_copy(update={"figure_literature_enabled": False})
    monkeypatch.setattr(content_worker, "get_settings", lambda: off)
    await content_worker._tick()
    await asyncio.sleep(0)
    assert lesson_id in dispatched


async def test_course_with_a_pdf_without_figures_gets_open_literature(
    seeded_db: AsyncSession, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """I2: estrazione accesa e fatta, nessuna figura nel PDF → letteratura;
    nessun documento nuovo."""
    course_id, lesson_id = await _lesson(seeded_db)
    doc = build_course_document(course_id, filename="solo_testo.pdf")
    doc.figures_status = "ready"
    doc.figures_count = 0
    seeded_db.add(doc)
    await seeded_db.commit()
    extraction_on = env["settings"].model_copy(update={"figure_extraction_enabled": True})
    monkeypatch.setattr(gaps, "get_settings", lambda: extraction_on)
    await gap_worker._tick()
    lesson = await _fresh(seeded_db, lesson_id)
    assert lesson.figures_gap_status == "done" and lesson.figures_gap_stats["kept"] == 1
    docs = await seeded_db.scalar(
        select(func.count(CourseDocument.id)).where(CourseDocument.course_id == course_id)
    )
    assert docs == 1


async def test_errors_outside_the_check_go_back_to_pending(
    seeded_db: AsyncSession, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fase D: anche un errore inatteso fuori dalla verifica (per esempio
    un errore DB transitorio al commit) rimette la lezione in coda con un
    tentativo in più; `failed` solo oltre il tetto."""
    _course_id, lesson_id = await _lesson(seeded_db)

    async def broken(db: AsyncSession, lesson: CourseLesson) -> None:
        raise RuntimeError("connessione persa")

    monkeypatch.setattr(gap_worker, "process_lesson", broken)
    await gap_worker._tick()
    lesson = await _fresh(seeded_db, lesson_id)
    assert (lesson.figures_gap_status, lesson.figures_gap_attempts) == ("pending", 1)
    assert "connessione persa" in lesson.figures_gap_stats["error"]


# --- qualità delle figure della letteratura -----------------------------------


def _png(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _transparent_schematic() -> bytes:
    """Rendering di Commons: tratti neri su fondo trasparente, modo LA."""
    rgba = Image.new("RGBA", (640, 400), (0, 0, 0, 0))
    draw = ImageDraw.Draw(rgba)
    draw.rectangle([60, 80, 300, 200], outline=(0, 0, 0, 255), width=6)
    draw.line([300, 140, 580, 320], fill=(0, 0, 0, 255), width=6)
    return _png(rgba.convert("LA"))


async def _external_rows(db: AsyncSession, course_id: uuid.UUID) -> list[CourseDocumentFigure]:
    rows = await db.execute(
        select(CourseDocumentFigure)
        .where(CourseDocumentFigure.course_id == course_id)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars().all())


async def test_transparent_commons_image_is_stored_on_white_and_blank_is_rejected(
    seeded_db: AsyncSession, env: dict[str, Any]
) -> None:
    env["files"] = [
        _file(101, "Laser Doppler vibrometer"),
        _file(103, "Laser Doppler vibrometer blank"),
    ]
    env["images"] = {
        101: _transparent_schematic(),
        103: _png(Image.new("RGB", (640, 400), "black")),
    }
    course_id, lesson_id = await _lesson(seeded_db)
    await gap_worker._tick()
    lesson = await _fresh(seeded_db, lesson_id)
    assert lesson.figures_gap_stats["rejected_blank"] == 1, lesson.figures_gap_stats
    # L'immagine vuota non arriva nemmeno alla Vision.
    assessed = [c[1] for c in env["calls"] if c[0] == "assess"]
    assert assessed == ["Laser Doppler vibrometer"]
    (row,) = await _external_rows(seeded_db, course_id)
    stored = Image.open(
        io.BytesIO(env["storage"].files[remote_storage.uploads_key(str(row.storage_path))])
    )
    pixels = list(stored.convert("L").getdata())
    assert sum(pixels) / len(pixels) > 230  # prima: tutta nera
    assert min(pixels) < 40


async def test_figures_with_text_in_another_language_are_rejected(
    seeded_db: AsyncSession, env: dict[str, Any]
) -> None:
    """Corso in italiano: testo in italiano, in inglese o senza parole sì;
    in arabo no (la variante `-ar` dello stesso schema di Commons)."""
    env["files"] = [
        _file(101, "Laser Doppler vibrometer"),
        _file(104, "Laser Doppler vibrometer ar"),
        _file(105, "Laser Doppler vibrometer en"),
    ]
    env["images"] = {101: _image(1), 104: _image(4), 105: _image(5)}
    env["languages"] = {"Laser Doppler vibrometer ar": "ar", "Laser Doppler vibrometer en": "en"}
    course_id, lesson_id = await _lesson(seeded_db)
    await gap_worker._tick()
    lesson = await _fresh(seeded_db, lesson_id)
    assert lesson.figures_gap_stats["rejected_language"] == 1, lesson.figures_gap_stats
    kept = {row.external_id for row in await _external_rows(seeded_db, course_id)}
    assert kept == {"commons:101", "commons:105"}


@pytest.mark.parametrize(
    ("text_language", "course", "allowed"),
    [
        ("it", "it", True),
        ("en", "it", True),
        ("EN-us", "it", True),
        ("none", "it", True),
        ("ar", "it", False),
        ("fa", "it", False),
        ("zh", "it", False),
        ("", "it", False),
        ("de", "de", True),
        ("it", "de", False),
    ],
)
def test_text_language_allowed(text_language: str, course: str, allowed: bool) -> None:
    assert relevance.text_language_allowed(text_language, course) is allowed


# --- risoluzione effettiva nella letteratura (doc 18 §22) ----------------------------


def _tiny_png(width: int, height: int) -> bytes:
    image = Image.new("RGB", (width, height), "white")
    ImageDraw.Draw(image).rectangle([5, 5, width - 5, height - 5], outline="black", width=3)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


async def test_commons_raster_below_the_minimum_is_skipped_before_download(
    seeded_db: AsyncSession, env: dict[str, Any]
) -> None:
    """Originale di 250 px: a 90 mm convenzionali sono 70 ppi (unusable).
    Niente download e niente Vision; l'altra candidata passa."""
    env["files"] = [
        _file(101, "Laser Doppler vibrometer"),
        _file(103, "Tiny LDV", original_mime="image/png", original_width=250, original_height=150),
    ]
    _course_id, lesson_id = await _lesson(seeded_db)
    await gap_worker._tick()
    lesson = await _fresh(seeded_db, lesson_id)
    assert ("download", 103) not in env["calls"]
    assert ("assess", "Tiny LDV") not in env["calls"]
    assert lesson.figures_gap_stats["rejected_resolution"] == 1


async def test_downloaded_image_below_the_minimum_skips_the_vision(
    seeded_db: AsyncSession, env: dict[str, Any]
) -> None:
    """Dimensioni originali ignote: si scarica, ma i pixel veri (200 px)
    bastano a scartarla prima della Vision a pagamento."""
    env["files"] = [_file(104, "Small LDV")]
    env["images"][104] = _tiny_png(200, 120)
    _course_id, lesson_id = await _lesson(seeded_db)
    await gap_worker._tick()
    lesson = await _fresh(seeded_db, lesson_id)
    assert ("download", 104) in env["calls"]
    assert ("assess", "Small LDV") not in env["calls"]
    assert lesson.figures_gap_stats["rejected_resolution"] == 1


async def test_commons_svg_is_saved_as_vector_with_the_crop_inputs(
    seeded_db: AsyncSession, env: dict[str, Any]
) -> None:
    env["files"] = [
        _file(
            101,
            "Laser Doppler vibrometer",
            original_mime="image/svg+xml",
            original_width=512,
            original_height=300,
        )
    ]
    course_id, _lesson_id = await _lesson(seeded_db)
    await gap_worker._tick()
    rows = (
        (
            await seeded_db.execute(
                select(CourseDocumentFigure).where(CourseDocumentFigure.course_id == course_id)
            )
        )
        .scalars()
        .all()
    )
    (row,) = rows
    assert row.is_vector is True and row.crop_mode == "external" and row.crop_version == 2
    assert row.mime_type == "image/png"  # schema: mai JPEG
    assert row.dpi is None and row.natural_width_mm is None  # misura convenzionale


async def test_openalex_unusable_crops_do_not_take_a_slot(
    seeded_db: AsyncSession, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rilievo della verifica WP2: nella scelta dei ritagli di un PDF
    OpenAlex quelli sotto il minimo si scartano prima di leggerne i byte e
    prima della Vision; il ritaglio buono prende lo slot."""
    from app.services import openalex_client
    from app.services.openalex_client import _to_work

    settings = env["settings"].model_copy(
        update={"openalex_api_key": "k-test", "figure_extraction_enabled": True}
    )
    monkeypatch.setattr(gaps, "get_settings", lambda: settings)
    env["files"] = []
    work = _to_work(
        {
            "id": "https://openalex.org/W7",
            "title": "Vibrometria laser Doppler",
            "authorships": [{"author": {"display_name": "Mario Rossi"}}],
            "publication_year": 2021,
            "open_access": {"is_oa": True},
            "best_oa_location": {"pdf_url": "https://example.org/w7.pdf", "license": "cc-by"},
        }
    )
    bbox = {"l": 72.0, "t": 100.0, "r": 216.0, "b": 196.0, "page_w": 595.0, "page_h": 842.0}
    events = [
        {  # 120 px su 50,8 mm con un nativo a 60 ppi: unusable
            "locator": "p0001-f01",
            "caption": "Figura 1. Vibrometro laser Doppler",
            "width": 120,
            "height": 90,
            "dpi": 60,
            "native_ppi": 60.0,
            "natural_width_mm": 50.8,
            "is_vector": False,
            "bbox": bbox,
            "page": 1,
        },
        {
            "locator": "p0002-f01",
            "caption": "Figura 2. Vibrometro laser Doppler a scansione",
            "width": 640,
            "height": 400,
            "dpi": 300,
            "native_ppi": 300.0,
            "natural_width_mm": 54.2,
            "is_vector": False,
            "bbox": bbox,
            "page": 2,
        },
    ]
    chosen: list[str] = []

    async def fake_extract(pdf: bytes, *, max_pages: int, wait_seconds: float, select: Any) -> Any:
        picked = select(events)
        chosen.extend(e["locator"] for e in picked)
        return [{**e, "data": _image(3)} for e in picked]

    async def search_open_works(query: str, *, per_page: int) -> list[Any]:
        return [work]

    async def download_pdf(url: str, *, max_bytes: int) -> bytes:
        return b"%PDF-1.4"

    monkeypatch.setattr(gaps, "extract_pdf_figures", fake_extract)
    monkeypatch.setattr(openalex_client, "search_open_works", search_open_works)
    monkeypatch.setattr(openalex_client, "download_pdf", download_pdf)
    _course_id, lesson_id = await _lesson(seeded_db)
    await gap_worker._tick()
    lesson = await _fresh(seeded_db, lesson_id)
    assert chosen == ["p0002-f01"]
    assert lesson.figures_gap_stats["rejected_resolution"] == 1


async def test_with_the_rule_off_nothing_is_rejected_for_resolution(
    seeded_db: AsyncSession, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = env["settings"].model_copy(update={"figure_resolution_rules_enabled": False})
    monkeypatch.setattr(gaps, "get_settings", lambda: settings)
    env["files"] = [
        _file(103, "Tiny LDV", original_mime="image/png", original_width=250, original_height=150)
    ]
    env["images"][103] = _tiny_png(250, 150)
    _course_id, lesson_id = await _lesson(seeded_db)
    await gap_worker._tick()
    lesson = await _fresh(seeded_db, lesson_id)
    assert ("download", 103) in env["calls"] and ("assess", "Tiny LDV") in env["calls"]
    assert "rejected_resolution" not in (lesson.figures_gap_stats or {})


@pytest.mark.parametrize("native_crop", [True, False])
async def test_third_party_images_follow_the_native_crop_switch(
    seeded_db: AsyncSession, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, native_crop: bool
) -> None:
    from app.services import image_limits

    settings = env["settings"].model_copy(
        update={"figure_extraction_native_crop_enabled": native_crop}
    )
    monkeypatch.setattr(gaps, "get_settings", lambda: settings)
    seen: list[bool] = []
    original = image_limits.load_image

    def recording(data: bytes, *, max_pixels: int, crop_v2: bool = False) -> Any:
        seen.append(crop_v2)
        return original(data, max_pixels=max_pixels, crop_v2=crop_v2)

    monkeypatch.setattr(image_limits, "load_image", recording)
    await _lesson(seeded_db)
    await gap_worker._tick()
    assert seen and all(flag is native_crop for flag in seen)
