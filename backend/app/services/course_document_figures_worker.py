"""Worker delle figure di fonte: estrazione dai documenti del corso (J-Q3).

Gemello di `course_document_worker` (riassunti), con stato separato
(`figures_*`): un errore qui non tocca il riassunto e viceversa.

Ciclo di un documento:

1. claim condizionale (`pending`, o `processing` rimasto da un riavvio) con
   `figures_next_attempt_at` scaduto; un documento alla volta;
2. ricontrollo della politica (excluded / content_only → `skipped`), poi
   di nuovo prima di ogni blocco e prima della Vision (il docente può
   cambiarla durante l'estrazione);
3. download nella cartella temporanea (storage in `to_thread`), impronta
   del file; con un'impronta nuova le righe della vecchia estrazione
   escono (`superseded` se già collocate in una lezione, altrimenti
   cancellate);
4. estrazione nel sottoprocesso sotto `HEAVY_JOB_LOCK`, a blocchi di
   pagine con checkpoint (`figures_progress.next_page`); `MemAvailable` si
   controlla prima di avviare un figlio (rinvio senza consumare tentativi,
   finestra dal primo rinvio); la bibliografia deterministica, se manca, la
   legge il figlio (:mod:`.document_figures.metadata`);
5. ritagli caricati nello storage dal padre, righe `extracted`/`rejected`;
6. deduplicazione (ripetute, duplicate nel documento e nel corso);
7. descrizione Vision; se qualche descrizione fallisce il documento torna
   in coda (le figure descritte restano pronte).

Errori: `encrypted`, `corrupt`, `unsupported_format`, `engine_unavailable`,
`source_missing` sono terminali; `timeout`, `oom`, `crashed`,
`storage_error` tornano `pending` con backoff fino a
`FIGURE_EXTRACTION_AUTO_RETRY_MAX` (poi `failed`); un blocco che manda in
crash il figlio due volte viene saltato (copertura `partial`).

Con `FIGURE_EXTRACTION_ENABLED=false` il worker non parte e non prende
lavori (nessun backfill: `figures_status` NULL = mai richiesto).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import async_session_factory
from app.models.course import Course
from app.models.course_document import CourseDocument
from app.models.course_document_figure import CourseDocumentFigure
from app.schemas.document_bibliography import DocumentBibliography
from app.services import crossref_client, document_figures_service, remote_storage
from app.services.document_figures import EXTRACTION_VERSION, metadata
from app.services.document_figures import storage as figure_storage
from app.services.document_figures.child import METADATA_TEXT_CHARS
from app.services.document_figures.filters import HashedFigure, repeated_and_duplicates
from app.services.document_figures.phash import hamming
from app.services.document_figures.runner import (
    ChildConfig,
    ChildSession,
    ExtractionChildError,
    mem_available_mb,
)
from app.services.figure_attribution import attribution_source
from app.services.heavy_job_lock import HEAVY_JOB_LOCK
from app.services.openai_client import OpenAINotConfiguredError
from app.services.openai_figure_describe_service import (
    DescribeInput,
    FigureDescription,
    describe_figure,
)
from app.services.source_figure_policy import document_license_to_figure

log = get_logger("app.course_document_figures_worker")

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
SUPPORTED_MIMES = frozenset({PDF_MIME, DOCX_MIME, PPTX_MIME})

TERMINAL_CODES = frozenset(
    {"encrypted", "corrupt", "unsupported_format", "engine_unavailable", "source_missing"}
)
RECOVERABLE_CODES = frozenset({"timeout", "oom", "crashed", "storage_error", "vision_unavailable"})
# Pausa prima di riprovare un documento rinviato per memoria insufficiente.
DEFER_SECONDS = 120
# Tempo per blocco: caricamento del modello più un minuto a pagina.
_BLOCK_BASE_SECONDS = 180
_BLOCK_SECONDS_PER_PAGE = 60
# Duplicati nel corso: stessa immagine (pHash) di un'altra figura.
_COURSE_DUPLICATE_DISTANCE = 4
# Descrizione riusata da una figura quasi identica già descritta.
_DESCRIBE_REUSE_DISTANCE = 4


class _DeferredError(Exception):
    """Memoria disponibile sotto soglia: si riprova più tardi."""


class _VisionUnavailableError(Exception):
    """Descrizioni fallite (chiave assente, servizio irraggiungibile o errori
    su singole figure): il documento riprova più tardi."""


class _PolicyChangedError(Exception):
    """Il documento non è più citabile: ci si ferma."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class _Outcome:
    figures: int = 0
    rejected: int = 0
    skipped_blocks: int = 0


def _now() -> datetime:
    return datetime.now(UTC)


def _suffix(mime: str) -> str:
    return {PDF_MIME: ".pdf", PPTX_MIME: ".pptx"}.get(mime, ".docx")


def fingerprint(file_sha256: str, engine: str) -> str:
    raw = f"{file_sha256}|v{EXTRACTION_VERSION}|{engine}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --- probe del motore (una volta per processo) -----------------------------

_probe_result: dict[str, Any] | None = None
_probe_error: ExtractionChildError | None = None


async def _ensure_engine(config: ChildConfig, workdir: Path) -> None:
    """Sonda Docling una volta per processo (torch senza AVX → SIGILL qui,
    non a metà di un documento). Resta in memoria fino al riavvio dell'app
    solo un motore davvero inutilizzabile (`engine_unavailable`: SIGILL,
    import, caricamento del modello); timeout, memoria e crash della sonda
    sono transitori e il documento riprova."""
    global _probe_result, _probe_error
    if config.engine != "docling" or _probe_result is not None:
        return
    if _probe_error is not None:
        raise _probe_error
    try:
        _probe_result = await ChildSession(config, workdir).probe()
        log.info(
            "document_figures_probe_ok", **{k: v for k, v in _probe_result.items() if k != "event"}
        )
    except ExtractionChildError as exc:
        if exc.code != "engine_unavailable":
            log.warning(
                "document_figures_probe_transient",
                code=exc.code,
                error=str(exc),
                stderr=exc.stderr_tail[-1500:],
            )
            raise
        _probe_error = ExtractionChildError(
            "engine_unavailable", str(exc), stderr_tail=exc.stderr_tail
        )
        log.error("document_figures_probe_failed", error=str(exc), stderr=exc.stderr_tail[-1500:])
        raise _probe_error from exc


def _reset_probe_for_tests() -> None:
    global _probe_result, _probe_error
    _probe_result = None
    _probe_error = None


# --- transizioni di stato ---------------------------------------------------


async def _finish(
    db: AsyncSession,
    doc: CourseDocument,
    *,
    status: str,
    code: str | None = None,
    error: str | None = None,
    next_attempt_at: datetime | None = None,
) -> None:
    doc.figures_status = status
    doc.figures_error_code = code
    doc.figures_error = (error or None) and error[:2000]
    doc.figures_next_attempt_at = next_attempt_at
    await db.commit()
    log.info(
        "document_figures_status",
        doc_id=str(doc.id),
        status=status,
        code=code,
        attempts=doc.figures_attempts,
    )


async def _recoverable(db: AsyncSession, doc: CourseDocument, code: str, error: str) -> None:
    settings = get_settings()
    if doc.figures_attempts >= int(settings.figure_extraction_auto_retry_max):
        await _finish(db, doc, status="failed", code=code, error=error)
        return
    delay = min(3600, 60 * 2 ** max(0, doc.figures_attempts - 1))
    await _finish(
        db,
        doc,
        status="pending",
        code=code,
        error=error,
        next_attempt_at=_now() + timedelta(seconds=delay),
    )


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _memory_low() -> bool:
    available = mem_available_mb()
    return available is not None and available < int(
        get_settings().figure_extraction_min_available_mb
    )


async def _defer(db: AsyncSession, doc: CourseDocument) -> None:
    """Rinvio per memoria insufficiente: il tentativo non conta. La finestra
    `FIGURE_EXTRACTION_MAX_DEFER_MINUTES` parte dal PRIMO rinvio (non dalla
    richiesta: un documento può aspettare a lungo in coda dietro altri)."""
    settings = get_settings()
    now = _now()
    progress = dict(doc.figures_progress or {})
    since = _parse_time(progress.get("deferred_since")) or now
    if now - since > timedelta(minutes=int(settings.figure_extraction_max_defer_minutes)):
        progress.pop("deferred_since", None)
        doc.figures_progress = progress
        await _finish(
            db,
            doc,
            status="failed",
            code="resources_unavailable",
            error="Memoria disponibile insufficiente per troppo tempo.",
        )
        return
    progress["deferred_since"] = since.isoformat()
    doc.figures_progress = progress
    doc.figures_attempts = max(0, doc.figures_attempts - 1)
    await _finish(
        db,
        doc,
        status="pending",
        code=None,
        next_attempt_at=now + timedelta(seconds=DEFER_SECONDS),
    )


async def _ensure_citable(db: AsyncSession, doc_id: uuid.UUID) -> None:
    """Politica riletta dal DB (prima di ogni blocco e prima della Vision):
    un documento reso excluded/content_only durante l'estrazione non
    produce altri ritagli né altre chiamate a pagamento."""
    policy = (
        await db.execute(select(CourseDocument.citation_policy).where(CourseDocument.id == doc_id))
    ).scalar_one_or_none()
    if policy == "excluded":
        raise _PolicyChangedError("policy_excluded")
    if policy == "content_only":
        raise _PolicyChangedError("policy_content_only")


# --- claim ------------------------------------------------------------------


async def claim_next(db: AsyncSession) -> CourseDocument | None:
    """Prende un documento in coda (UPDATE condizionale, un solo vincitore)."""
    now = _now()
    candidate = (
        await db.execute(
            select(CourseDocument.id)
            .where(
                CourseDocument.figures_status.in_(("pending", "processing")),
                or_(
                    CourseDocument.figures_next_attempt_at.is_(None),
                    CourseDocument.figures_next_attempt_at <= now,
                ),
            )
            .order_by(CourseDocument.figures_requested_at.asc().nulls_last())
            .limit(1)
        )
    ).scalar_one_or_none()
    if candidate is None:
        return None
    claimed = (
        await db.execute(
            update(CourseDocument)
            .where(
                CourseDocument.id == candidate,
                CourseDocument.figures_status.in_(("pending", "processing")),
            )
            .values(
                figures_status="processing",
                figures_attempts=CourseDocument.figures_attempts + 1,
                figures_next_attempt_at=None,
            )
            .returning(CourseDocument.id)
        )
    ).scalar_one_or_none()
    await db.commit()
    if claimed is None:
        return None
    return await db.get(CourseDocument, claimed, populate_existing=True)


# --- elaborazione -------------------------------------------------------------


def _config() -> ChildConfig:
    settings = get_settings()
    block = max(1, int(settings.figure_extraction_block_pages))
    return ChildConfig(
        engine=str(settings.figure_extraction_engine),
        artifacts_path=str(settings.figure_docling_artifacts_path) or None,
        threads=max(1, int(settings.figure_extraction_threads)),
        block_timeout_seconds=float(_BLOCK_BASE_SECONDS + _BLOCK_SECONDS_PER_PAGE * block),
        probe_timeout_seconds=float(settings.figure_extraction_probe_timeout_seconds),
        max_rss_mb=int(settings.figure_extraction_max_rss_mb),
    )


async def _fill_bibliography(doc: CourseDocument, meta: dict[str, Any] | None) -> None:
    """Bibliografia deterministica solo se il documento non ne ha una, dai
    metadati letti dal figlio (il documento non si apre mai nel processo
    principale). Ordine del piano: prima i metadati del file
    (`pdf_metadata`), poi il DOI risolto da Crossref, accettato solo se il
    titolo restituito compare nel documento (il primo DOI di una dispensa è
    spesso quello di un articolo citato)."""
    if doc.bibliography_source is not None or not isinstance(meta, dict):
        return
    raw = meta.get("bibliography")
    if isinstance(raw, dict):
        try:
            found: DocumentBibliography | None = DocumentBibliography.model_validate(raw)
        except ValidationError:
            found = None
        if found is not None and (found.title or found.authors):
            doc.bibliography = found.as_json()
            doc.bibliography_source = "pdf_metadata"
            return
    if doc.mime_type != PDF_MIME:
        return
    text = str(meta.get("text") or "")[:METADATA_TEXT_CHARS]
    doi = metadata.find_doi(text)
    if not doi:
        return
    work = await crossref_client.get_work_by_doi(doi)
    title = metadata.plausible_title(work.title) if work else None
    if work is None or not title:
        return
    info_title = meta.get("info_title")
    if not metadata.crossref_title_matches(
        title, text, info_title if isinstance(info_title, str) else None
    ):
        log.info("document_figures_doi_not_the_document", doc_id=str(doc.id), doi=doi)
        return
    bib = DocumentBibliography(
        title=title,
        authors=[a[:200] for a in work.authors][:50],
        year=work.published_year
        if work.published_year and 1400 <= work.published_year <= 2100
        else None,
        container=(work.container or "")[:300] or None,
        doi=doi[:200],
        url=f"https://doi.org/{doi}"[:1000],
    )
    doc.bibliography = bib.as_json()
    doc.bibliography_source = "crossref"


async def _supersede_previous(db: AsyncSession, doc: CourseDocument) -> list[str]:
    """Impronta nuova (file, motore o `EXTRACTION_VERSION` diversi): le righe
    della vecchia estrazione non si mescolano con le nuove. Quelle già
    collocate in una lezione restano (U1) ma escono dal catalogo
    (`superseded`) e liberano il locator; le altre si cancellano. Ritorna i
    file da togliere dallo storage dopo il commit."""
    rows = list(
        (
            await db.execute(
                select(CourseDocumentFigure).where(CourseDocumentFigure.document_id == doc.id)
            )
        )
        .scalars()
        .all()
    )
    rows = [r for r in rows if r.reject_reason != "superseded"]
    if not rows:
        return []
    used = await document_figures_service.used_figure_ids(db, doc.course_id)
    paths: list[str] = []
    for row in rows:
        if row.id in used:
            row.reject_reason = "superseded"
            row.locator = f"old-{row.id.hex[:12]}-{row.locator}"[:60]
        else:
            paths.extend(p for p in (row.storage_path, row.preview_path) if p)
            await db.delete(row)
    await db.flush()
    log.info(
        "document_figures_superseded",
        doc_id=str(doc.id),
        kept=sum(1 for r in rows if r.id in used),
        deleted=sum(1 for r in rows if r.id not in used),
    )
    return paths


async def _existing_locators(db: AsyncSession, doc_id: uuid.UUID) -> set[str]:
    rows = await db.execute(
        select(CourseDocumentFigure.locator).where(CourseDocumentFigure.document_id == doc_id)
    )
    return set(rows.scalars().all())


async def _store_block(
    db: AsyncSession,
    doc: CourseDocument,
    figures: list[dict[str, Any]],
    workdir: Path,
    known: set[str],
    engine: str,
) -> _Outcome:
    outcome = _Outcome()
    license = document_license_to_figure(doc.license)
    for event in figures:
        locator = str(event["locator"])[:60]
        if locator in known:
            continue
        reason = event.get("reject_reason")
        storage_path = preview_path = None
        if reason is None and event.get("file"):
            data = (workdir / Path(str(event["file"])).name).read_bytes()
            sha12 = hashlib.sha256(data).hexdigest()[:12]
            ext = "jpg" if event.get("mime") == "image/jpeg" else "png"
            storage_path = figure_storage.figure_path(
                doc.course_id, doc.id, f"{locator}-{sha12}.{ext}"
            )
            preview_path = figure_storage.figure_path(
                doc.course_id, doc.id, f"{locator}-{sha12}-preview.jpg"
            )
            preview = (workdir / Path(str(event["preview_file"])).name).read_bytes()
            await asyncio.to_thread(figure_storage.upload, storage_path, data)
            await asyncio.to_thread(figure_storage.upload, preview_path, preview)
        elif reason is None:
            reason = "blank"
        db.add(
            CourseDocumentFigure(
                course_id=doc.course_id,
                document_id=doc.id,
                source_kind="uploaded",
                locator=locator,
                extraction_version=EXTRACTION_VERSION,
                engine=engine,
                page=event.get("page"),
                bbox=event.get("bbox"),
                source_label=(event.get("source_label") or None)
                and str(event["source_label"])[:60],
                source_caption=event.get("caption"),
                context_excerpt=event.get("context_excerpt"),
                storage_path=storage_path,
                preview_path=preview_path,
                mime_type=event.get("mime") if storage_path else None,
                width=event.get("width"),
                height=event.get("height"),
                dpi=event.get("dpi"),
                byte_size=event.get("byte_size") if storage_path else None,
                is_vector=event.get("is_vector"),
                phash=event.get("phash"),
                detector_class=(event.get("detector_class") or None)
                and str(event["detector_class"])[:40],
                detector_confidence=event.get("detector_confidence"),
                status="rejected" if reason else "extracted",
                reject_reason=reason,
                license=license,
                license_source="document",
            )
        )
        known.add(locator)
        if reason:
            outcome.rejected += 1
        else:
            outcome.figures += 1
    await db.commit()
    return outcome


async def _deduplicate(db: AsyncSession, doc: CourseDocument) -> None:
    """Ripetute (loghi su molte pagine) e duplicate nel documento; poi
    duplicate di figure di altri documenti dello stesso corso."""
    rows = list(
        (
            await db.execute(
                select(CourseDocumentFigure).where(
                    CourseDocumentFigure.document_id == doc.id,
                    CourseDocumentFigure.status == "extracted",
                    CourseDocumentFigure.phash.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    by_key = {str(r.id): r for r in rows}
    repeated, duplicates = repeated_and_duplicates(
        [HashedFigure(key=str(r.id), page=r.page, phash=str(r.phash)) for r in rows]
    )
    for key in repeated:
        by_key[key].status = "rejected"
        by_key[key].reject_reason = "repeated"
    for key, original in duplicates.items():
        by_key[key].status = "rejected"
        by_key[key].reject_reason = "duplicate"
        by_key[key].duplicate_of_id = uuid.UUID(original)
    # Solo figure proponibili: una copia di una figura staccata, esclusa dal
    # docente, superata o di un documento non citabile non deve rendere
    # «duplicata» (quindi mai proponibile) la figura di questo documento.
    others = list(
        (
            await db.execute(
                select(CourseDocumentFigure)
                .join(CourseDocument, CourseDocument.id == CourseDocumentFigure.document_id)
                .where(
                    CourseDocumentFigure.course_id == doc.course_id,
                    CourseDocumentFigure.document_id != doc.id,
                    CourseDocument.citation_policy == "citable",
                    CourseDocumentFigure.status.in_(("extracted", "ready")),
                    CourseDocumentFigure.excluded_by_user.is_(False),
                    CourseDocumentFigure.reject_reason.is_(None),
                    CourseDocumentFigure.phash.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        if row.status != "extracted":
            continue
        for other in others:
            if hamming(str(row.phash), str(other.phash)) <= _COURSE_DUPLICATE_DISTANCE:
                row.status = "rejected"
                row.reject_reason = "duplicate"
                row.duplicate_of_id = other.id
                break
    await db.commit()


# --- descrizione Vision (PROMPT 18) -------------------------------------------


def merge_usage(previous: dict[str, Any] | None, usage: dict[str, Any]) -> dict[str, Any]:
    """Somma cumulativa dell'usage delle chiamate AI su una figura."""
    merged = dict(previous or {})
    merged["model"] = usage.get("model")
    merged["calls"] = int(merged.get("calls") or 0) + 1
    for key in ("prompt", "completion", "total", "cached_tokens", "reasoning_tokens"):
        merged[key] = int(merged.get(key) or 0) + int(usage.get(key) or 0)
    cost = usage.get("cost_usd")
    if cost is not None:
        merged["cost_usd"] = round(float(merged.get("cost_usd") or 0.0) + float(cost), 8)
    merged["last"] = usage
    return merged


def _apply_description(row: CourseDocumentFigure, out: FigureDescription, model: str) -> None:
    row.kind = out.kind
    row.description = out.description
    row.keywords = {"course": out.keywords_course, "en": out.keywords_en}
    row.quality_score = out.quality_score
    row.legibility = out.legibility
    row.is_useful_for_teaching = out.is_useful_for_teaching
    row.described_at = _now()
    row.describe_model = model[:80]
    row.status = "ready"


def _copy_description(row: CourseDocumentFigure, source: CourseDocumentFigure) -> None:
    row.kind = source.kind
    row.description = source.description
    row.keywords = source.keywords
    row.quality_score = source.quality_score
    row.legibility = source.legibility
    row.is_useful_for_teaching = source.is_useful_for_teaching
    row.described_at = _now()
    row.describe_model = source.describe_model
    row.describe_source_id = source.id
    row.status = "ready"


def _local_crop(workdir: Path | None, row: CourseDocumentFigure) -> Path | None:
    if workdir is None or not row.storage_path:
        return None
    ext = ".jpg" if row.mime_type == "image/jpeg" else ".png"
    candidate = workdir / f"{row.locator}{ext}"
    return candidate if candidate.is_file() else None


def _spread(items: list[CourseDocumentFigure], n: int) -> list[CourseDocumentFigure]:
    """`n` elementi distribuiti su tutta la lista (in ordine di pagina)."""
    if n <= 0:
        return []
    if len(items) <= n:
        return list(items)
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


def pick_for_description(rows: list[CourseDocumentFigure], cap: int) -> list[CourseDocumentFigure]:
    """Figure da descrivere entro il tetto `FIGURE_DESCRIBE_MAX_PER_DOCUMENT`.

    Prima quelle con una didascalia letta («Figura N», etichetta), poi le
    altre; in ciascun gruppo distribuite su tutto il documento. Prima si
    prendevano le prime in ordine di pagina: su un manuale di 300 pagine
    la 80ª figura era a pagina 138 e il 62% delle figure non entrava mai
    nel catalogo (Fase D). Ritorna le scelte in ordine di pagina."""
    if cap <= 0:
        return []
    if len(rows) <= cap:
        return list(rows)
    captioned = [r for r in rows if r.source_label or r.source_caption]
    others = [r for r in rows if not (r.source_label or r.source_caption)]
    chosen = _spread(captioned, cap)
    chosen += _spread(others, cap - len(chosen))
    order = {row.id: index for index, row in enumerate(rows)}
    return sorted(chosen, key=lambda row: order[row.id])


async def _describe(db: AsyncSession, doc: CourseDocument, workdir: Path | None) -> None:
    """Descrive le figure `extracted` del documento (fino al tetto), riusando
    le descrizioni delle figure quasi identiche del corso."""
    settings = get_settings()
    rows = list(
        (
            await db.execute(
                select(CourseDocumentFigure)
                .where(
                    CourseDocumentFigure.document_id == doc.id,
                    CourseDocumentFigure.status == "extracted",
                )
                .order_by(
                    CourseDocumentFigure.page.asc().nulls_last(), CourseDocumentFigure.locator
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return
    already = int(
        (
            await db.execute(
                select(func.count())
                .select_from(CourseDocumentFigure)
                .where(
                    CourseDocumentFigure.document_id == doc.id,
                    CourseDocumentFigure.described_at.is_not(None),
                )
            )
        ).scalar_one()
    )
    cap = max(0, int(settings.figure_describe_max_per_document) - already)
    chosen = pick_for_description(rows, cap)
    chosen_ids = {row.id for row in chosen}
    for row in rows:
        if row.id not in chosen_ids:
            row.status = "rejected"
            row.reject_reason = "describe_capped"
    rows = chosen
    await db.commit()
    if not rows:
        return
    course = await db.get(Course, doc.course_id)
    language = course.language_code if course is not None else "it"
    src = attribution_source(rows[0], doc)
    title = (src.title or src.fallback_name) if src is not None else None
    # Riuso delle descrizioni fra i corsi della stessa organizzazione (lo
    # stesso PDF caricato in più corsi): niente seconda chiamata Vision. Solo
    # corsi della stessa lingua (descrizione e parole chiave sono scritte
    # nella lingua del corso) e figure di documenti citabili.
    described_pool = list(
        (
            await db.execute(
                select(CourseDocumentFigure)
                .join(Course, Course.id == CourseDocumentFigure.course_id)
                .join(CourseDocument, CourseDocument.id == CourseDocumentFigure.document_id)
                .where(
                    Course.organization_id == (course.organization_id if course else None),
                    Course.language_code == language,
                    CourseDocument.citation_policy == "citable",
                    CourseDocumentFigure.status == "ready",
                    CourseDocumentFigure.described_at.is_not(None),
                    CourseDocumentFigure.describe_source_id.is_(None),
                    CourseDocumentFigure.phash.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    semaphore = asyncio.Semaphore(max(1, int(settings.openai_figure_describe_concurrency)))
    model = settings.openai_figure_describe_model

    async def call(row: CourseDocumentFigure) -> tuple[FigureDescription, dict[str, Any]]:
        local = _local_crop(workdir, row)
        if local is not None:
            image = await asyncio.to_thread(local.read_bytes)
        else:
            image = await asyncio.to_thread(figure_storage.read, str(row.storage_path))
        async with semaphore:
            return await describe_figure(
                DescribeInput(
                    image=image,
                    caption=row.source_caption,
                    context=row.context_excerpt,
                    document_title=title,
                    language_code=language,
                )
            )

    succeeded = failed = 0
    batch_size = max(1, int(settings.openai_figure_describe_concurrency)) * 2
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        pending: list[CourseDocumentFigure] = []
        for row in batch:
            source = (
                next(
                    (
                        other
                        for other in described_pool
                        if other.id != row.id
                        and hamming(str(row.phash), str(other.phash)) <= _DESCRIBE_REUSE_DISTANCE
                    ),
                    None,
                )
                if row.phash
                else None
            )
            if source is not None:
                _copy_description(row, source)
                succeeded += 1
            else:
                pending.append(row)
        results = await asyncio.gather(*(call(r) for r in pending), return_exceptions=True)
        for row, result in zip(pending, results, strict=True):
            if isinstance(result, OpenAINotConfiguredError):
                await db.commit()
                raise _VisionUnavailableError("chiave OpenAI assente")
            if isinstance(result, BaseException):
                failed += 1
                usage = getattr(result, "usage", None)
                if isinstance(usage, dict):
                    row.vision_usage = merge_usage(row.vision_usage, usage)
                    row.vision_usage_at = _now()
                log.warning(
                    "document_figures_describe_failed",
                    figure_id=str(row.id),
                    error=str(result)[:300],
                )
                continue
            out, usage = result
            _apply_description(row, out, model)
            row.vision_usage = merge_usage(row.vision_usage, usage)
            row.vision_usage_at = _now()
            described_pool.append(row)
            succeeded += 1
        progress = dict(doc.figures_progress or {})
        progress.update(
            stage="describing",
            candidates_total=len(rows),
            candidates_done=min(len(rows), start + len(batch)),
        )
        doc.figures_progress = progress
        await db.commit()
    if failed:
        # Anche con qualche descrizione riuscita: le figure rimaste
        # `extracted` si riprovano al prossimo giro (le pronte restano).
        raise _VisionUnavailableError(f"{failed} descrizioni fallite, {succeeded} riuscite")


def _extraction_done(doc: CourseDocument) -> bool:
    """Tutte le pagine (fino al tetto) già estratte: al nuovo giro resta solo
    la descrizione (per esempio dopo descrizioni Vision fallite)."""
    total = doc.figures_pages_total
    if not total:
        return False
    next_page = int((doc.figures_progress or {}).get("next_page") or 1)
    return next_page > min(total, max(1, int(get_settings().figure_extraction_max_pages)))


async def _extract(
    db: AsyncSession, doc: CourseDocument, workdir: Path, source: Path, config: ChildConfig
) -> _Outcome:
    settings = get_settings()
    progress: dict[str, Any] = dict(doc.figures_progress or {})
    next_page = int(progress.get("next_page") or 1)
    crashes: dict[str, int] = dict(progress.get("crashes") or {})
    skipped: list[list[int]] = list(progress.get("skipped_blocks") or [])
    block_pages = max(1, int(settings.figure_extraction_block_pages))
    per_child = max(block_pages, int(settings.figure_extraction_pages_per_child))
    max_pages = max(1, int(settings.figure_extraction_max_pages))
    deadline = time.monotonic() + float(settings.figure_extraction_total_timeout_seconds)
    total = _Outcome(skipped_blocks=len(skipped))
    if _extraction_done(doc):
        return total
    known = await _existing_locators(db, doc.id)
    want_metadata = doc.bibliography_source is None
    session: ChildSession | None = None
    try:
        while True:
            if time.monotonic() > deadline:
                raise ExtractionChildError("timeout", "tempo totale dell'estrazione superato")
            await _ensure_citable(db, doc.id)
            # Pagine già tutte fatte (dopo il riciclo del figlio): nessun
            # figlio nuovo da avviare solo per scoprirlo (Fase D).
            if (
                session is None
                and doc.figures_pages_total
                and next_page > min(doc.figures_pages_total, max_pages)
            ):
                break
            if session is None:
                # Memoria controllata solo prima di avviare un figlio: con il
                # figlio vivo la sua RSS la abbasserebbe a ogni blocco.
                if _memory_low():
                    raise _DeferredError()
                session = ChildSession(config, workdir)
                pages = await session.start(
                    source_name=source.name, mime=doc.mime_type, metadata=want_metadata
                )
                doc.figures_pages_total = pages
                if want_metadata:
                    want_metadata = False
                    try:
                        await _fill_bibliography(doc, session.metadata)
                    except Exception as exc:  # la bibliografia non blocca le figure
                        log.warning(
                            "document_figures_bibliography_failed",
                            doc_id=str(doc.id),
                            error=str(exc),
                        )
            last_page = min(doc.figures_pages_total or 1, max_pages)
            if next_page > last_page:
                break
            end = min(next_page + block_pages - 1, last_page)
            try:
                result = await session.run_block(next_page, end)
            except ExtractionChildError as exc:
                await session.close()
                session = None
                if exc.code not in ("crashed", "oom", "timeout"):
                    raise
                key = f"{next_page}-{end}"
                crashes[key] = crashes.get(key, 0) + 1
                if crashes[key] < 2:
                    progress.update(next_page=next_page, crashes=crashes)
                    doc.figures_progress = dict(progress)
                    await db.commit()
                    raise
                # Secondo crash sullo stesso blocco: lo si salta.
                log.warning("document_figures_block_skipped", doc_id=str(doc.id), pages=key)
                skipped.append([next_page, end])
                total.skipped_blocks += 1
                next_page = end + 1
                progress.update(next_page=next_page, crashes=crashes, skipped_blocks=skipped)
                doc.figures_progress = dict(progress)
                await db.commit()
                continue
            stored = await _store_block(db, doc, result.figures, workdir, known, config.engine)
            total.figures += stored.figures
            total.rejected += stored.rejected
            next_page = end + 1
            progress.update(stage="extracting", next_page=next_page)
            progress.pop("deferred_since", None)
            doc.figures_progress = dict(progress)
            doc.figures_pages_done = end
            await db.commit()
            if session.pages_processed >= per_child:
                await session.close()
                session = None
    finally:
        if session is not None:
            await session.close()
    return total


async def process_document(db: AsyncSession, doc: CourseDocument) -> None:
    settings = get_settings()
    # Id salvato subito: dopo un rollback l'oggetto è scaduto e rileggerne
    # gli attributi in AsyncSession solleva MissingGreenlet.
    doc_id = doc.id
    if doc.figures_attempts > int(settings.figure_extraction_attempts_max):
        await _finish(
            db, doc, status="failed", code="attempts_exhausted", error="Tentativi esauriti."
        )
        return
    if doc.citation_policy == "excluded":
        await _finish(db, doc, status="skipped", code="policy_excluded")
        return
    if doc.citation_policy == "content_only":
        await _finish(db, doc, status="skipped", code="policy_content_only")
        return
    if doc.mime_type not in SUPPORTED_MIMES:
        await _finish(db, doc, status="skipped", code="unsupported_format")
        return
    config = _config()
    started = time.monotonic()
    workdir = Path(tempfile.mkdtemp(prefix="a4u-figures-"))
    try:
        source = workdir / f"source{_suffix(doc.mime_type)}"
        try:
            await asyncio.to_thread(
                remote_storage.get_storage().download_to,
                remote_storage.uploads_key(doc.file_path),
                source,
            )
        except remote_storage.StorageFileNotFound:
            await _finish(db, doc, status="failed", code="source_missing", error=doc.file_path)
            return
        except remote_storage.StorageError as exc:
            await _recoverable(db, doc, "storage_error", str(exc))
            return
        file_sha = await asyncio.to_thread(_sha256, source)
        new_fingerprint = fingerprint(file_sha, config.engine)
        stale_paths: list[str] = []
        if doc.figures_fingerprint != new_fingerprint:
            # File, motore o versione diversi: si riparte da capo, senza
            # mescolare le righe della vecchia estrazione con le nuove.
            stale_paths = await _supersede_previous(db, doc)
            doc.figures_fingerprint = new_fingerprint
            doc.figures_progress = {"stage": "extracting", "next_page": 1}
            doc.figures_pages_done = 0
            doc.figures_pages_total = None
        doc.figures_engine = config.engine
        await db.commit()
        if stale_paths:
            await document_figures_service.delete_files(stale_paths)

        async with HEAVY_JOB_LOCK:
            try:
                if doc.mime_type == PDF_MIME and not _extraction_done(doc):
                    if _memory_low():
                        raise _DeferredError()
                    await _ensure_engine(config, workdir)
                outcome = await _extract(db, doc, workdir, source, config)
            except _DeferredError:
                await _defer(db, doc)
                return
            except _PolicyChangedError as exc:
                await _finish(db, doc, status="skipped", code=exc.code)
                return
            except ExtractionChildError as exc:
                if exc.code in RECOVERABLE_CODES:
                    await _recoverable(db, doc, exc.code, str(exc))
                else:
                    code = exc.code if exc.code in TERMINAL_CODES else "crashed"
                    await _finish(db, doc, status="failed", code=code, error=str(exc))
                return
            except (remote_storage.StorageError, OSError) as exc:
                await db.rollback()
                fresh = await db.get(CourseDocument, doc_id, populate_existing=True)
                if fresh is not None:
                    await _recoverable(db, fresh, "storage_error", str(exc))
                return

        await _deduplicate(db, doc)
        vision_error: str | None = None
        try:
            await _ensure_citable(db, doc_id)
            await _describe(db, doc, workdir)
        except _PolicyChangedError as exc:
            await _finish(db, doc, status="skipped", code=exc.code)
            return
        except _VisionUnavailableError as exc:
            vision_error = str(exc)
        rows = (
            await db.execute(
                select(CourseDocumentFigure.status, CourseDocumentFigure.reject_reason).where(
                    CourseDocumentFigure.document_id == doc_id
                )
            )
        ).all()
        partial = bool(outcome.skipped_blocks) or (doc.figures_pages_total or 0) > int(
            settings.figure_extraction_max_pages
        )
        # Le righe `superseded` di un'estrazione precedente non contano.
        doc.figures_count = sum(1 for st, reason in rows if st == "ready" and reason is None)
        doc.figures_coverage = "partial" if partial else "full"
        doc.figures_stats = {
            "seconds": round(time.monotonic() - started, 1),
            "pages_total": doc.figures_pages_total,
            "pages_done": doc.figures_pages_done,
            "engine": config.engine,
            "extraction_version": EXTRACTION_VERSION,
            "figures": doc.figures_count,
            "rejected": sum(1 for st, _reason in rows if st == "rejected"),
            "skipped_blocks": (doc.figures_progress or {}).get("skipped_blocks") or [],
        }
        if vision_error is not None:
            await _recoverable(db, doc, "vision_unavailable", vision_error)
            return
        progress = dict(doc.figures_progress or {})
        progress["stage"] = "done"
        doc.figures_progress = dict(progress)
        await _finish(
            db,
            doc,
            status="ready",
            code="crashed_repeatedly" if outcome.skipped_blocks else None,
        )
    finally:
        await asyncio.to_thread(shutil.rmtree, workdir, True)


async def _tick() -> None:
    async with async_session_factory() as db:
        try:
            doc = await claim_next(db)
            if doc is None:
                return
            doc_id = doc.id
            try:
                await process_document(db, doc)
            except Exception as exc:  # pragma: no cover - rete di sicurezza
                await db.rollback()
                log.error(
                    "document_figures_unexpected", doc_id=str(doc_id), error=str(exc), exc_info=True
                )
                fresh = await db.get(CourseDocument, doc_id, populate_existing=True)
                if fresh is not None and fresh.figures_status == "processing":
                    await _recoverable(db, fresh, "crashed", str(exc))
        except Exception as exc:  # pragma: no cover
            await db.rollback()
            log.warning("document_figures_tick_failed", error=str(exc))


_worker_task: asyncio.Task[None] | None = None
_stop_event: asyncio.Event | None = None


async def _run_loop() -> None:
    settings = get_settings()
    interval = max(2, int(settings.figure_extraction_poll_interval_seconds))
    log.info("document_figures_worker_started", interval=interval)
    assert _stop_event is not None
    while not _stop_event.is_set():
        await _tick()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(_stop_event.wait(), timeout=interval)
    log.info("document_figures_worker_stopped")


def start_worker() -> None:
    global _worker_task, _stop_event
    if not get_settings().figure_extraction_enabled:
        log.info("document_figures_worker_disabled")
        return
    if _worker_task is not None and not _worker_task.done():
        return
    _stop_event = asyncio.Event()
    _worker_task = asyncio.create_task(_run_loop(), name="course_document_figures_worker")


async def stop_worker() -> None:
    global _worker_task, _stop_event
    if _stop_event is not None:
        _stop_event.set()
    if _worker_task is not None:
        try:
            await asyncio.wait_for(_worker_task, timeout=30)
        except TimeoutError:
            _worker_task.cancel()
            await asyncio.gather(_worker_task, return_exceptions=True)
    _worker_task = None
    _stop_event = None
