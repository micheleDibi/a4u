"""Background worker per il pre-processing dei documenti corso (Appendice A).

Loop singolo `asyncio.Task` lanciato a startup in `app.main.lifespan`. Lo
stato è in DB (`course_document.summary_status`), quindi se il backend si
riavvia il worker riprende dal punto interrotto. Niente Celery, nessun
broker esterno.

Ciclo per ogni documento:
  pending → processing → ready (oppure failed con messaggio)

Due percorsi (dispatcher in `_process_one`):
- SINGLE-SHOT (testo ≤ soglia, o kill-switch off): estrazione →
  chiamata unica OpenAI con schema Appendice A → JSONB in
  `course_document.summary`. Identico al comportamento storico.
- CHUNKED (copertura totale, Blocco 1): estrazione integrale a
  segmenti → map per-chunk (persistenza in `course_document_chunk`,
  insert-only-on-success: la ripresa processa gli indici senza riga) →
  merge deterministico (in thread) → eventuale digest → reduce con lo
  STESSO schema/validazione del single-shot. Il fingerprint (bytes del
  file + parametri + modello + PROMPT_VERSION) invalida i chunk se il
  run cambia. Il worker resta sequenziale TRA documenti; la concorrenza
  (semaforo) è sulle chiamate map DENTRO il documento.

NB single-process: il claim non usa SELECT FOR UPDATE — regge perché
uvicorn gira senza `--workers`; l'INSERT dei chunk è comunque
idempotente (ON CONFLICT DO NOTHING) come cintura.
"""
from __future__ import annotations

import asyncio
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import async_session_factory
from app.models.course_document import CourseDocument
from app.models.course_document_chunk import CourseDocumentChunk
from app.services import (
    document_chunking_service,
    document_extraction_service,
    document_summary_merge_service,
    openai_summarize_service,
    remote_storage,
)
from app.services.document_chunking_service import ChunkSpec
from app.services.openai_client import OpenAINotConfiguredError

log = get_logger("app.course_document.worker")


def _now() -> datetime:
    return datetime.now(tz=UTC)


async def _doc_still_exists(db: AsyncSession, doc_id) -> bool:
    """Il delete del documento è permesso anche durante un run lungo:
    prima di ogni scrittura per-chunk si verifica che la riga esista
    ancora (il CASCADE ha già eliminato i chunk)."""
    row = await db.execute(
        select(CourseDocument.id).where(CourseDocument.id == doc_id)
    )
    return row.scalar_one_or_none() is not None


async def _fail(
    db: AsyncSession,
    doc: CourseDocument,
    *,
    phase: str,
    error: str,
    log_key: str,
) -> None:
    doc.summary_status = "failed"
    doc.summary_error = error[:500] if phase != "extraction" else error
    await write_audit(
        db,
        action="course.document.summary.failed",
        actor_user_id=None,
        target_type="course_document",
        target_id=str(doc.id),
        metadata={
            "phase": phase,
            "filename": doc.filename_original,
            "error": str(error)[:500],
            "attempts": doc.summary_attempts,
        },
    )
    await db.commit()
    log.warning(log_key, doc_id=str(doc.id), error=str(error)[:300])


async def _persist_ready(
    db: AsyncSession,
    doc: CourseDocument,
    summary,
    usage: dict,
    *,
    coverage: str | None,
    chunks: int | None = None,
    calls: int | None = None,
) -> None:
    doc.summary = summary.model_dump()
    doc.summary_tokens = usage
    doc.summary_status = "ready"
    doc.summary_generated_at = _now()
    doc.summary_error = None
    doc.summary_coverage = coverage
    # Il single-shot azzera lo stato chunked; il chunked lo lascia ai
    # contatori già scritti (total==done) e cancella le righe chunk.
    await db.execute(
        delete(CourseDocumentChunk).where(
            CourseDocumentChunk.document_id == doc.id
        )
    )
    if chunks is None:
        doc.summary_chunks_total = None
        doc.summary_chunks_done = None
        doc.summary_fingerprint = None
    await write_audit(
        db,
        action="course.document.summary.ready",
        actor_user_id=None,
        target_type="course_document",
        target_id=str(doc.id),
        metadata={
            "filename": doc.filename_original,
            "tokens_total": usage.get("total"),
            "tokens_prompt": usage.get("prompt"),
            "tokens_completion": usage.get("completion"),
            "model": usage.get("model"),
            "attempts": doc.summary_attempts,
            "chars": doc.text_chars_extracted,
            "coverage": coverage,
            "chunks": chunks,
            "calls": calls,
        },
    )
    await db.commit()
    log.info(
        "course_document_summary_ready",
        doc_id=str(doc.id),
        tokens=usage.get("total"),
        coverage=coverage,
        chunks=chunks,
    )


async def _summarize_singleshot(
    db: AsyncSession,
    doc: CourseDocument,
    text: str,
    *,
    coverage: str | None,
) -> None:
    """Percorso storico: una chiamata unica. Usato dal kill-switch
    (coverage=None, byte-identico a prima) e dai documenti sotto soglia
    (coverage='full')."""
    try:
        summary, usage = await openai_summarize_service.summarize_document(
            text=text,
            source_filename=doc.filename_original,
        )
    except OpenAINotConfiguredError:
        await _fail(
            db,
            doc,
            phase="summarize",
            error=(
                "OpenAI non configurato: l'amministratore deve impostare "
                "OPENAI_API_KEY nel file .env del backend."
            ),
            log_key="course_document_openai_not_configured",
        )
        return
    except openai_summarize_service.OpenAISummarizeError as exc:
        await _fail(
            db,
            doc,
            phase="summarize",
            error=str(exc),
            log_key="course_document_summarize_failed",
        )
        return
    await _persist_ready(db, doc, summary, usage, coverage=coverage)


async def _map_one_chunk(
    semaphore: asyncio.Semaphore,
    chunk: ChunkSpec,
    chunk_text: str,
    source_filename: str,
    total: int,
):
    async with semaphore:
        try:
            facts, usage = (
                await openai_summarize_service.extract_chunk_facts(
                    chunk_text=chunk_text,
                    source_filename=source_filename,
                    position_label=f"blocco {chunk.index + 1} di {total}",
                )
            )
        except OpenAINotConfiguredError:
            raise
        except openai_summarize_service.OpenAISummarizeError as exc:
            raise _ChunkMapError(
                f"blocco {chunk.index + 1} di {total}: {exc}"
            ) from exc
        return chunk, facts, usage


def _pages_label(page_start: int | None, page_end: int | None, index: int) -> str:
    if page_start is not None and page_end is not None:
        if page_start == page_end:
            return f"pag. {page_start}"
        return f"pagg. {page_start}-{page_end}"
    return f"blocco {index + 1}"


async def _summarize_chunked(
    db: AsyncSession,
    doc: CourseDocument,
    text: str,
    spans,
    file_bytes: bytes,
    *,
    hard_capped: bool,
) -> None:
    """Percorso a copertura totale: map (per-chunk, ripresa dai
    mancanti) → merge deterministico → digest eventuale → reduce."""
    settings = get_settings()
    fingerprint = document_chunking_service.compute_fingerprint(
        file_bytes,
        target_chars=settings.course_document_chunk_chars,
        overlap_chars=settings.course_document_chunk_overlap_chars,
        singleshot_max_chars=settings.course_document_singleshot_max_chars,
        hard_cap_chars=settings.course_document_max_chars_hard,
        model=settings.openai_summarize_model,
    )
    chunks = document_chunking_service.plan_chunks(
        text,
        spans,
        target_chars=settings.course_document_chunk_chars,
        overlap_chars=settings.course_document_chunk_overlap_chars,
    )
    total = len(chunks)

    if doc.summary_fingerprint != fingerprint:
        # Run nuovo (o parametri/prompt/file cambiati): mai risultati misti.
        await db.execute(
            delete(CourseDocumentChunk).where(
                CourseDocumentChunk.document_id == doc.id
            )
        )
        doc.summary_fingerprint = fingerprint

    existing = {
        row.chunk_index
        for row in (
            await db.execute(
                select(CourseDocumentChunk.chunk_index).where(
                    CourseDocumentChunk.document_id == doc.id
                )
            )
        ).all()
    }
    doc.summary_chunks_total = total
    doc.summary_chunks_done = len(
        [c for c in chunks if c.index in existing]
    )
    await db.commit()

    missing = [c for c in chunks if c.index not in existing]
    log.info(
        "course_document_chunked_run",
        doc_id=str(doc.id),
        chunks=total,
        missing=len(missing),
        hard_capped=hard_capped,
    )

    semaphore = asyncio.Semaphore(
        max(1, int(settings.course_document_chunk_concurrency))
    )
    tasks = [
        asyncio.create_task(
            _map_one_chunk(
                semaphore,
                chunk,
                chunk.slice_text(text),
                doc.filename_original,
                total,
            )
        )
        for chunk in missing
    ]
    try:
        for future in asyncio.as_completed(tasks):
            chunk, facts, usage = await future

            # Il documento può essere stato cancellato durante il run
            # (minuti): abort pulito, il CASCADE ha già tolto i chunk.
            if not await _doc_still_exists(db, doc.id):
                log.info(
                    "course_document_deleted_mid_run", doc_id=str(doc.id)
                )
                return

            await db.execute(
                pg_insert(CourseDocumentChunk)
                .values(
                    document_id=doc.id,
                    chunk_index=chunk.index,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    result=facts.model_dump(),
                    tokens=usage,
                )
                .on_conflict_do_nothing(
                    index_elements=["document_id", "chunk_index"]
                )
            )
            doc.summary_chunks_done = (doc.summary_chunks_done or 0) + 1
            await db.commit()
    except OpenAINotConfiguredError:
        await _fail(
            db,
            doc,
            phase="map",
            error=(
                "OpenAI non configurato: l'amministratore deve impostare "
                "OPENAI_API_KEY nel file .env del backend."
            ),
            log_key="course_document_openai_not_configured",
        )
        return
    except _ChunkMapError as exc:
        await _fail(
            db,
            doc,
            phase="map",
            error=f"Analisi fallita al {exc}",
            log_key="course_document_map_failed",
        )
        return
    finally:
        # Qualunque uscita (errore, delete mid-run, eccezione inattesa):
        # niente task map orfane che continuano a bruciare token.
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # Tutti i chunk pronti: merge deterministico (CPU-bound → thread).
    rows = (
        await db.execute(
            select(CourseDocumentChunk)
            .where(CourseDocumentChunk.document_id == doc.id)
            .order_by(CourseDocumentChunk.chunk_index)
        )
    ).scalars().all()
    results = []
    map_tokens_prompt = map_tokens_completion = map_tokens_total = 0
    for row in rows:
        result = dict(row.result)
        result["_pages_label"] = _pages_label(
            row.page_start, row.page_end, row.chunk_index
        )
        results.append(result)
        tokens = row.tokens or {}
        map_tokens_prompt += int(tokens.get("prompt") or 0)
        map_tokens_completion += int(tokens.get("completion") or 0)
        map_tokens_total += int(tokens.get("total") or 0)

    merged = await asyncio.to_thread(
        document_summary_merge_service.merge_chunk_facts, results
    )
    facts_block = await asyncio.to_thread(
        document_summary_merge_service.render_merged_facts, merged
    )

    abstract_items = [
        f"[{pages}] {abstract}" for _i, pages, abstract in merged.abstracts
    ]
    extra_usages: list[dict] = []
    budget = int(settings.course_document_reduce_input_max_chars)
    group_size = max(2, int(settings.course_document_reduce_group_size))
    try:
        # Digest ricorsivo dei soli mini-abstract finché l'input del
        # reduce sta nel budget (coi default basta 1 livello).
        while (
            len(facts_block) + sum(len(a) + 2 for a in abstract_items)
            > budget
            and len(abstract_items) > 1
        ):
            groups = [
                abstract_items[i:i + group_size]
                for i in range(0, len(abstract_items), group_size)
            ]
            new_items: list[str] = []
            for gi, group in enumerate(groups):
                digest, usage = (
                    await openai_summarize_service.consolidate_abstracts(
                        abstracts_block="\n\n".join(group),
                        source_filename=doc.filename_original,
                        group_label=f"{gi + 1}/{len(groups)}",
                    )
                )
                extra_usages.append(usage)
                new_items.append(f"[sezione {gi + 1}] {digest}")
            abstract_items = new_items

        summary, reduce_usage = (
            await openai_summarize_service.reduce_summary(
                facts_block=facts_block,
                abstracts_block="\n\n".join(abstract_items),
                source_filename=doc.filename_original,
                language_hint=merged.detected_language,
            )
        )
        extra_usages.append(reduce_usage)
    except OpenAINotConfiguredError:
        await _fail(
            db,
            doc,
            phase="reduce",
            error=(
                "OpenAI non configurato: l'amministratore deve impostare "
                "OPENAI_API_KEY nel file .env del backend."
            ),
            log_key="course_document_openai_not_configured",
        )
        return
    except openai_summarize_service.OpenAISummarizeError as exc:
        # I chunk restano persistiti: il reprocess ripaga solo
        # digest+reduce (il passo rischioso si ritenta a costo minimo).
        await _fail(
            db,
            doc,
            phase="reduce",
            error=f"Sintesi finale fallita: {exc}",
            log_key="course_document_reduce_failed",
        )
        return

    # Guard ottimistico: un reprocess/reset arrivato mid-run non deve
    # essere silenziosamente annullato dal commit `ready`; e il
    # documento può essere stato cancellato.
    if not await _doc_still_exists(db, doc.id):
        log.info("course_document_deleted_mid_run", doc_id=str(doc.id))
        return
    await db.refresh(doc)
    if (
        doc.summary_status != "processing"
        or doc.summary_fingerprint != fingerprint
    ):
        log.info(
            "course_document_run_superseded",
            doc_id=str(doc.id),
            status=doc.summary_status,
        )
        return

    usage = {
        "prompt": map_tokens_prompt
        + sum(int(u.get("prompt") or 0) for u in extra_usages),
        "completion": map_tokens_completion
        + sum(int(u.get("completion") or 0) for u in extra_usages),
        "total": map_tokens_total
        + sum(int(u.get("total") or 0) for u in extra_usages),
        "model": settings.openai_summarize_model,
        "calls": len(rows) + len(extra_usages),
    }
    await _persist_ready(
        db,
        doc,
        summary,
        usage,
        coverage="partial" if hard_capped else "full",
        chunks=total,
        calls=usage["calls"],
    )


class _ChunkMapError(Exception):
    """Errore terminale su una chiamata map (retry già esauriti)."""


async def _process_one(db: AsyncSession, doc: CourseDocument) -> None:
    """Elabora un singolo documento: estrai testo → riassumi → salva."""
    settings = get_settings()

    # Guardia anti-loop: una riga rimasta `processing` (crash o eccezione
    # inattesa) viene ri-selezionata a ogni tick; oltre il cap → failed.
    # Il reprocess manuale (che setta `pending`) resta sempre onorato.
    if (
        doc.summary_status == "processing"
        and (doc.summary_attempts or 0)
        >= int(settings.course_document_summary_attempts_max)
    ):
        await _fail(
            db,
            doc,
            phase="requeue",
            error=(
                f"Analisi interrotta dopo {doc.summary_attempts} tentativi: "
                f"riprova con 'Rielabora' o contatta l'amministratore."
            ),
            log_key="course_document_requeue_exhausted",
        )
        return

    # Marca processing + bump tentativi (commit immediato per visibilità UI).
    doc.summary_status = "processing"
    doc.summary_attempts = (doc.summary_attempts or 0) + 1
    doc.summary_error = None
    await db.commit()
    await db.refresh(doc)

    full_coverage = bool(settings.course_document_full_coverage_enabled)

    # 1) Scarica il documento dallo storage in un file temporaneo, poi estrai.
    suffix = Path(doc.file_path).suffix
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)
        await asyncio.to_thread(
            remote_storage.get_storage().download_to,
            remote_storage.uploads_key(doc.file_path),
            tmp_path,
        )
        if full_coverage:
            file_bytes = await asyncio.to_thread(tmp_path.read_bytes)
            (
                text,
                spans,
                original_chars,
                hard_capped,
            ) = await document_extraction_service.extract_segments(
                tmp_path,
                doc.mime_type,
                hard_cap_chars=settings.course_document_max_chars_hard,
            )
        else:
            file_bytes = b""
            spans = []
            hard_capped = False
            text, original_chars = (
                await document_extraction_service.extract_text(
                    tmp_path, doc.mime_type
                )
            )
    except (
        document_extraction_service.DocumentExtractionError,
        remote_storage.StorageFileNotFound,
    ) as exc:
        await _fail(
            db,
            doc,
            phase="extraction",
            error=str(exc),
            log_key="course_document_extraction_failed",
        )
        return
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

    doc.text_extracted_at = _now()
    doc.text_chars_extracted = len(text)
    if original_chars > len(text):
        log.info(
            "course_document_text_truncated_for_summary",
            doc_id=str(doc.id),
            original=original_chars,
            kept=len(text),
        )

    # 2+3) Dispatcher: single-shot (storico) o chunked (copertura totale).
    if not full_coverage:
        await _summarize_singleshot(db, doc, text, coverage=None)
        return
    if len(text) <= int(settings.course_document_singleshot_max_chars):
        await _summarize_singleshot(db, doc, text, coverage="full")
        return
    await _summarize_chunked(
        db, doc, text, spans, file_bytes, hard_capped=hard_capped
    )


async def _tick() -> None:
    """Processa tutti i documenti `pending`/`processing` in un singolo passaggio."""
    async with async_session_factory() as db:
        try:
            res = await db.execute(
                select(CourseDocument).where(
                    CourseDocument.summary_status.in_(["pending", "processing"])
                )
            )
            docs = list(res.scalars().all())
            for doc in docs:
                try:
                    await _process_one(db, doc)
                except Exception as exc:  # pragma: no cover - safety
                    await db.rollback()
                    log.error(
                        "course_document_worker_unexpected",
                        doc_id=str(doc.id),
                        error=str(exc),
                        exc_info=True,
                    )
        except Exception as exc:  # pragma: no cover
            await db.rollback()
            log.warning("course_document_worker_tick_failed", error=str(exc))


_worker_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None


async def _run_loop() -> None:
    settings = get_settings()
    interval = max(2, int(settings.course_document_poll_interval_seconds))
    log.info("course_document_worker_started", interval=interval)
    assert _stop_event is not None
    while not _stop_event.is_set():
        await _tick()
        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=interval)
        except TimeoutError:
            pass
    log.info("course_document_worker_stopped")


def start_worker() -> None:
    global _worker_task, _stop_event
    if _worker_task is not None and not _worker_task.done():
        return
    _stop_event = asyncio.Event()
    _worker_task = asyncio.create_task(_run_loop(), name="course_document_worker")


async def stop_worker() -> None:
    global _worker_task, _stop_event
    if _stop_event is not None:
        _stop_event.set()
    if _worker_task is not None:
        try:
            await asyncio.wait_for(_worker_task, timeout=15)
        except TimeoutError:
            _worker_task.cancel()
            with_suppressed_cancel = asyncio.gather(
                _worker_task, return_exceptions=True
            )
            await with_suppressed_cancel
    _worker_task = None
    _stop_event = None
