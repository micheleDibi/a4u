"""Buchi di figure di fonte e letteratura aperta (WP5).

Prima della Fase 3 ogni lezione ordinaria passa da una verifica (job del
worker `course_lesson_figures_gap_worker`; la Fase 3 aspetta con un filtro
SQL e un tetto, come per le estrazioni):

1. figure di fonte PERTINENTI già nel catalogo del corso per la lezione
   (stesse regole del catalogo del PROMPT 3: `relevant` di
   `lesson_figure_selection`, modo select, filtri di qualità). Se sono
   almeno `FIGURE_SOURCE_MIN_PER_LESSON` → `done`, nessuna chiamata;
2. documenti citabili con estrazione mai fatta o in corso → `skipped`
   (`documents_not_extracted`): le loro figure non sono note, la
   letteratura non si interroga (con l'estrazione spenta non lo saranno
   mai: contano come senza figure);
3. altrimenti la letteratura aperta integra fino al budget (b): termini di
   ricerca in inglese (PROMPT 20), poi Wikimedia Commons (licenze libere,
   rendering PNG di Commons) e, con `OPENALEX_API_KEY` e l'estrazione
   accesa, i PDF open access di OpenAlex con licenza CC o pubblico dominio
   (ritagli estratti con lo stesso processo figlio dei documenti, sotto
   `HEAVY_JOB_LOCK`). Ogni candidata passa dai limiti di dimensione
   (`image_limits`), dal controllo dei duplicati (phash) e dalla verifica
   Vision di pertinenza (PROMPT 20); le tenute diventano righe del catalogo
   del corso SENZA documento (`source_kind` wikimedia/openalex,
   attribuzione e licenza della fonte congelate, `external_id` unico per
   corso). Nessun `CourseDocument`: riassunti, selezione dei documenti,
   testo e riferimenti delle lezioni non cambiano.

Tetti: candidate valutate per lezione, figure esterne per corso, tempo
totale. Il costo delle chiamate AI va in `course_lesson.figures_gap_usage`
(dashboard admin, fase `figures_gap`). Errori recuperabili (rete, 429,
5xx) → `GapRetryError`: il worker rimette la lezione in coda fino al tetto.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.course import Course
from app.models.course_document import CourseDocument
from app.models.course_document_figure import CourseDocumentFigure
from app.models.course_lesson import CourseLesson
from app.services import image_limits, openalex_client, source_figure_catalog, wikimedia_client
from app.services import openai_figure_relevance_service as relevance
from app.services.document_figures import cropper
from app.services.document_figures import storage as figure_storage
from app.services.document_figures.phash import hamming, phash
from app.services.document_figures_service import EXTRACTABLE_MIMES
from app.services.figure_attribution import figure_number_from_label
from app.services.lesson_document_selection import build_query_profile, terms
from app.services.openai_client import OpenAINotConfiguredError
from app.services.safe_http import SafeFetchError

log = get_logger("app.literature_figures")

GAP_ACTIVE = ("pending", "processing")
EXTERNAL_KINDS = ("wikimedia", "openalex")
DUPLICATE_DISTANCE = 4
EXTRACTION_VERSION = 1
# Ritagli di un PDF OpenAlex valutati dalla Vision (i più vicini alla
# lezione per didascalia) e lavori OpenAlex per ricerca.
OPENALEX_FIGURES_PER_WORK = 3
OPENALEX_WORKS_PER_QUERY = 3
WIKIMEDIA_FILES_PER_QUERY = 10


class GapRetryError(Exception):
    """Errore recuperabile (rete, 429, 5xx): la lezione torna in coda. Porta
    l'usage già pagato e l'esito parziale, da salvare comunque."""

    def __init__(
        self,
        message: str,
        usage: dict[str, Any] | None = None,
        stats: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.usage = usage
        self.stats = dict(stats or {})


def _now() -> datetime:
    return datetime.now(UTC)


def merge_usage(previous: dict[str, Any] | None, usage: dict[str, Any] | None) -> dict[str, Any]:
    """Somma cumulativa dell'usage (stessa forma di `vision_usage`)."""
    from app.services.course_document_figures_worker import merge_usage as merge

    return merge(previous, usage) if usage else dict(previous or {})


def lesson_context(course: Course, lesson: CourseLesson) -> relevance.LessonContext:
    topics: list[str] = []
    for topic in lesson.mandatory_topics or []:
        if isinstance(topic, dict):
            label = topic.get("topic") or topic.get("title")
            if isinstance(label, str) and label.strip():
                topics.append(label.strip())
    objectives = [str(o).strip() for o in lesson.learning_objectives or [] if str(o).strip()]
    return relevance.LessonContext(
        title=lesson.title or "",
        topics=tuple(topics[:8]),
        objectives=tuple(objectives[:6]),
        language_code=(course.language_code or "it").lower(),
    )


async def documents_not_extracted(db: AsyncSession, course_id: uuid.UUID) -> int:
    """Documenti citabili estraibili con figure ancora ignote (estrazione
    mai richiesta o in corso). Con l'estrazione spenta non lo saranno mai:
    0."""
    if not get_settings().figure_extraction_enabled:
        return 0
    count = await db.scalar(
        select(func.count(CourseDocument.id)).where(
            CourseDocument.course_id == course_id,
            CourseDocument.citation_policy == "citable",
            CourseDocument.mime_type.in_(tuple(EXTRACTABLE_MIMES)),
            (CourseDocument.figures_status.is_(None))
            | (CourseDocument.figures_status.in_(GAP_ACTIVE)),
        )
    )
    return int(count or 0)


async def pertinent_figures(db: AsyncSession, course: Course, lesson: CourseLesson) -> int:
    """Figure di fonte pertinenti alla lezione già nel catalogo del corso."""
    result = await source_figure_catalog.build_catalog(db, course, lesson)
    if result is None:
        return 0
    return int(result.catalog.stats.get("relevant") or 0)


async def external_figures(db: AsyncSession, course_id: uuid.UUID) -> int:
    count = await db.scalar(
        select(func.count(CourseDocumentFigure.id)).where(
            CourseDocumentFigure.course_id == course_id,
            CourseDocumentFigure.source_kind.in_(EXTERNAL_KINDS),
        )
    )
    return int(count or 0)


@dataclass
class _Run:
    # Solo valori semplici: dopo un rollback gli oggetti ORM scadono e
    # rileggerli in AsyncSession solleverebbe MissingGreenlet.
    course_id: uuid.UUID
    profile: dict[str, float]
    context: relevance.LessonContext
    target: int
    max_candidates: int
    deadline: float
    hashes: list[str]
    known_ids: set[str]
    usage: dict[str, Any] | None = None
    kept: int = 0
    evaluated: int = 0
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def done(self) -> bool:
        return (
            self.kept >= self.target
            or self.evaluated >= self.max_candidates
            or time.monotonic() > self.deadline
        )

    def count(self, key: str) -> None:
        self.stats[key] = int(self.stats.get(key) or 0) + 1


async def _course_hashes(db: AsyncSession, course_id: uuid.UUID) -> list[str]:
    rows = await db.execute(
        select(CourseDocumentFigure.phash).where(
            CourseDocumentFigure.course_id == course_id,
            CourseDocumentFigure.phash.is_not(None),
            CourseDocumentFigure.status == "ready",
        )
    )
    return [h for h in rows.scalars().all() if h]


async def _course_external_ids(db: AsyncSession, course_id: uuid.UUID) -> set[str]:
    rows = await db.execute(
        select(CourseDocumentFigure.external_id).where(
            CourseDocumentFigure.course_id == course_id,
            CourseDocumentFigure.external_id.is_not(None),
        )
    )
    return {i for i in rows.scalars().all() if i}


def _safe_locator(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in value.lower())
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return cleaned[:60] or "x"


async def _consider(
    db: AsyncSession,
    run: _Run,
    *,
    image_bytes: bytes,
    source_kind: str,
    locator: str,
    external_id: str,
    source_title: str | None,
    source_text: str | None,
    license: str,
    license_url: str | None,
    attribution: dict[str, Any],
    source_url: str | None,
    caption: str | None,
    page: int | None = None,
    source_label: str | None = None,
) -> bool:
    """Valuta una candidata e, se pertinente, la salva nel catalogo."""
    settings = get_settings()
    try:
        safe = await asyncio.to_thread(
            image_limits.load_image,
            image_bytes,
            max_pixels=int(settings.figure_literature_max_image_pixels),
        )
    except image_limits.ImageLimitError as exc:
        run.count(f"rejected_{exc.code}")
        return False
    digest = await asyncio.to_thread(phash, safe.image)
    if any(hamming(digest, other) <= DUPLICATE_DISTANCE for other in run.hashes):
        run.count("duplicates")
        return False
    run.evaluated += 1
    try:
        verdict, usage = await relevance.assess_candidate(
            safe.data, run.context, source_title=source_title, source_text=source_text
        )
    except relevance.OpenAIFigureRelevanceError as exc:
        run.usage = merge_usage(run.usage, exc.usage)
        if exc.status is None or exc.status == 429 or exc.status >= 500:
            raise GapRetryError(str(exc)) from exc
        run.count("vision_errors")
        return False
    run.usage = merge_usage(run.usage, usage)
    if not (
        verdict.relevant
        and verdict.is_useful_for_teaching
        and verdict.quality_score >= int(settings.figure_min_quality_score)
        and verdict.kind not in source_figure_catalog.EXCLUDED_KINDS
    ):
        run.count("not_relevant")
        return False
    sha12 = hashlib.sha256(safe.data).hexdigest()[:12]
    ext = "jpg" if safe.mime == "image/jpeg" else "png"
    course_id = run.course_id
    storage_path = figure_storage.figure_path(course_id, None, f"{locator}-{sha12}.{ext}")
    preview_path = figure_storage.figure_path(course_id, None, f"{locator}-{sha12}-preview.jpg")
    preview = await asyncio.to_thread(cropper.preview, safe.image)
    await asyncio.to_thread(figure_storage.upload, storage_path, safe.data)
    await asyncio.to_thread(figure_storage.upload, preview_path, preview)
    now = _now()
    db.add(
        CourseDocumentFigure(
            course_id=course_id,
            document_id=None,
            source_kind=source_kind,
            locator=locator,
            extraction_version=EXTRACTION_VERSION,
            engine=source_kind,
            page=page if page and page >= 1 else None,
            source_label=(source_label or None) and str(source_label)[:60],
            source_caption=caption,
            storage_path=storage_path,
            preview_path=preview_path,
            mime_type=safe.mime,
            width=safe.width,
            height=safe.height,
            byte_size=len(safe.data),
            phash=digest,
            kind=verdict.kind,
            description=verdict.description,
            keywords={"course": verdict.keywords_course, "en": verdict.keywords_en},
            quality_score=verdict.quality_score,
            legibility=verdict.legibility,
            is_useful_for_teaching=verdict.is_useful_for_teaching,
            described_at=now,
            describe_model=str(settings.openai_figure_relevance_model)[:80],
            status="ready",
            license=license,
            license_source=source_kind,
            license_url=(license_url or None) and license_url[:500],
            attribution=attribution,
            external_id=external_id[:200],
            source_url=(source_url or None) and source_url[:1000],
            retrieved_at=now,
        )
    )
    try:
        await db.commit()
    except IntegrityError:
        # Stessa figura esterna salvata nel frattempo: niente doppioni.
        await db.rollback()
        await asyncio.to_thread(figure_storage.delete, storage_path)
        await asyncio.to_thread(figure_storage.delete, preview_path)
        run.count("duplicates")
        return False
    run.hashes.append(digest)
    run.known_ids.add(external_id)
    run.kept += 1
    run.stats.setdefault("kept_by_source", {})
    run.stats["kept_by_source"][source_kind] = run.stats["kept_by_source"].get(source_kind, 0) + 1
    return True


async def _from_wikimedia(db: AsyncSession, run: _Run, queries: list[str]) -> None:
    for query in queries:
        if run.done:
            return
        try:
            files = await wikimedia_client.search_files(
                query, limit=WIKIMEDIA_FILES_PER_QUERY, language=run.context.language_code
            )
        except SafeFetchError as exc:
            if exc.recoverable:
                raise GapRetryError(str(exc)) from exc
            run.count("wikimedia_errors")
            continue
        for item in files:
            if run.done:
                return
            if item.external_id in run.known_ids:
                continue
            try:
                got = await wikimedia_client.download_image(item)
            except SafeFetchError as exc:
                if exc.code == "rate_limited":
                    raise GapRetryError(str(exc)) from exc
                run.count("download_errors")
                continue
            run.known_ids.add(item.external_id)
            await _consider(
                db,
                run,
                image_bytes=got.content,
                source_kind="wikimedia",
                locator=_safe_locator(f"wm-{item.page_id}"),
                external_id=item.external_id,
                source_title=item.object_name or item.title,
                source_text=item.description,
                license=item.license,
                license_url=item.license_url,
                attribution=item.attribution(),
                source_url=item.description_url,
                caption=item.description,
            )


async def extract_pdf_figures(pdf: bytes, *, max_pages: int) -> list[dict[str, Any]]:
    """Ritagli di un PDF di terzi con lo stesso processo figlio dei documenti
    (motore di `FIGURE_EXTRACTION_ENGINE`, sotto `HEAVY_JOB_LOCK`). Ogni voce
    è l'evento del figlio con `data` (i byte del ritaglio)."""
    from app.services import course_document_figures_worker as figures_worker
    from app.services.document_figures.runner import ChildSession
    from app.services.heavy_job_lock import HEAVY_JOB_LOCK

    config = figures_worker._config()
    workdir = Path(tempfile.mkdtemp(prefix="a4u-literature-"))
    out: list[dict[str, Any]] = []
    try:
        source = workdir / "source.pdf"
        await asyncio.to_thread(source.write_bytes, pdf)
        async with HEAVY_JOB_LOCK:
            if figures_worker._memory_low():
                raise GapRetryError("memoria disponibile sotto la soglia")
            await figures_worker._ensure_engine(config, workdir)
            session = ChildSession(config, workdir)
            try:
                pages = await session.start(source_name=source.name, mime="application/pdf")
                last = min(pages, max_pages)
                block = max(1, int(get_settings().figure_extraction_block_pages))
                first = 1
                while first <= last:
                    end = min(first + block - 1, last)
                    result = await session.run_block(first, end)
                    for event in result.figures:
                        if event.get("reject_reason") or not event.get("file"):
                            continue
                        path = workdir / Path(str(event["file"])).name
                        out.append({**event, "data": await asyncio.to_thread(path.read_bytes)})
                    first = end + 1
            finally:
                await session.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return out


def _caption_score(profile: dict[str, float], caption: str | None) -> float:
    return sum(profile.get(term, 0.0) for term in terms(caption))


async def _from_openalex(db: AsyncSession, run: _Run, queries: list[str]) -> None:
    settings = get_settings()
    profile = run.profile
    for query in queries:
        if run.done:
            return
        try:
            works = await openalex_client.search_open_works(
                query, per_page=OPENALEX_WORKS_PER_QUERY
            )
        except openalex_client.OpenAlexError as exc:
            if exc.status is None or exc.status == 429 or exc.status >= 500:
                raise GapRetryError(str(exc)) from exc
            run.count("openalex_errors")
            continue
        for work in works:
            if run.done:
                return
            license = openalex_client.oa_location_license(work)
            if license is None or not work.oa_pdf_url:
                continue
            try:
                pdf = await openalex_client.download_pdf(
                    work.oa_pdf_url,
                    max_bytes=int(settings.figure_literature_max_pdf_mb) * 1024 * 1024,
                )
                pages = await asyncio.to_thread(
                    image_limits.pdf_page_count,
                    pdf,
                    max_pages=int(settings.figure_literature_max_pdf_pages),
                )
            except openalex_client.OpenAlexError:
                run.count("download_errors")
                continue
            except image_limits.ImageLimitError as exc:
                run.count(f"rejected_{exc.code}")
                continue
            figures = await extract_pdf_figures(pdf, max_pages=pages)
            ranked = sorted(
                (
                    (score, event)
                    for event in figures
                    if (score := _caption_score(profile, event.get("caption"))) > 0
                ),
                key=lambda item: -item[0],
            )[:OPENALEX_FIGURES_PER_WORK]
            work_key = work.id.rsplit("/", 1)[-1].lower()
            landing = openalex_client.oa_landing_url(work)
            doi_url = f"https://doi.org/{work.doi}" if work.doi else None
            for _score, event in ranked:
                if run.done:
                    return
                locator = str(event.get("locator") or "")
                external_id = f"{work.id}#{locator}"
                if external_id in run.known_ids:
                    continue
                run.known_ids.add(external_id)
                figure_number = figure_number_from_label(event.get("source_label"))
                attribution: dict[str, Any] = {
                    "authors": work.authors[:20],
                    "title": work.title,
                    "container": work.journal,
                    "year": work.publication_year,
                    "figure_number": figure_number,
                    "page": event.get("page"),
                    "license": license,
                    "url": doi_url or landing,
                }
                await _consider(
                    db,
                    run,
                    image_bytes=event["data"],
                    source_kind="openalex",
                    locator=_safe_locator(f"oa-{work_key}-{locator}"),
                    external_id=external_id,
                    source_title=work.title,
                    source_text=event.get("caption"),
                    license=license,
                    license_url=None,
                    attribution={k: v for k, v in attribution.items() if v},
                    source_url=landing or doi_url,
                    caption=event.get("caption"),
                    page=event.get("page"),
                    source_label=event.get("source_label"),
                )


@dataclass(frozen=True)
class GapOutcome:
    status: str
    stats: dict[str, Any]
    usage: dict[str, Any] | None


async def check_lesson(db: AsyncSession, lesson: CourseLesson) -> GapOutcome:
    """Verifica i buchi della lezione e integra dalla letteratura aperta.

    Solleva `GapRetryError` sugli errori recuperabili, con l'usage già
    pagato e l'esito parziale."""
    settings = get_settings()
    course = await db.get(Course, lesson.course_id)
    if course is None:
        return GapOutcome("skipped", {"reason": "course_missing"}, None)
    if lesson.is_assessment:
        return GapOutcome("skipped", {"reason": "assessment"}, None)
    budget = source_figure_catalog.budget_for(lesson)
    if not settings.figure_source_enabled or budget <= 0:
        return GapOutcome("skipped", {"reason": "no_budget"}, None)
    pertinent = await pertinent_figures(db, course, lesson)
    stats: dict[str, Any] = {"pertinent_before": pertinent, "budget": budget}
    if pertinent >= int(settings.figure_source_min_per_lesson):
        return GapOutcome("done", {**stats, "reason": "enough"}, None)
    unknown = await documents_not_extracted(db, course.id)
    if unknown:
        log.info(
            "figures_gap_documents_not_extracted",
            lesson_id=str(lesson.id),
            documents=unknown,
        )
        return GapOutcome(
            "skipped", {**stats, "reason": "documents_not_extracted", "documents": unknown}, None
        )
    room = int(settings.figure_literature_max_per_course) - await external_figures(db, course.id)
    if room <= 0:
        return GapOutcome("skipped", {**stats, "reason": "course_cap"}, None)
    run = _Run(
        course_id=course.id,
        profile=build_query_profile(lesson),
        context=lesson_context(course, lesson),
        target=min(budget - pertinent, room),
        max_candidates=max(0, int(settings.figure_literature_max_candidates_per_lesson)),
        deadline=time.monotonic() + float(settings.figure_literature_timeout_seconds),
        hashes=await _course_hashes(db, course.id),
        known_ids=await _course_external_ids(db, course.id),
        stats=dict(stats),
    )
    try:
        try:
            queries, usage = await relevance.search_terms(run.context)
        except relevance.OpenAIFigureRelevanceError as exc:
            run.usage = merge_usage(run.usage, exc.usage)
            raise GapRetryError(str(exc)) from exc
        run.usage = merge_usage(run.usage, usage)
        run.stats["queries"] = queries
        if queries:
            await _from_wikimedia(db, run, queries)
            if (
                not run.done
                and (settings.openalex_api_key or "").strip()
                and settings.figure_extraction_enabled
            ):
                await _from_openalex(db, run, queries)
    except OpenAINotConfiguredError:
        return GapOutcome("skipped", {**run.stats, "reason": "openai_not_configured"}, run.usage)
    except GapRetryError as exc:
        # L'usage già pagato si conserva anche se la lezione torna in coda.
        raise GapRetryError(str(exc), run.usage, run.stats) from exc
    run.stats.update(
        kept=run.kept,
        evaluated=run.evaluated,
        timed_out=time.monotonic() > run.deadline,
    )
    return GapOutcome("done", run.stats, run.usage)
