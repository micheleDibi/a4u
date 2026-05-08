"""Background worker per il pre-processing dei documenti corso (Appendice A).

Loop singolo `asyncio.Task` lanciato a startup in `app.main.lifespan`. Lo
stato è in DB (`course_document.summary_status`), quindi se il backend si
riavvia il worker riprende dal punto interrotto. Niente Celery, nessun
broker esterno.

Ciclo per ogni documento:
  pending → processing → ready (oppure failed con messaggio)

Estrazione testo locale (pdfplumber/python-docx/...) → chiamata OpenAI con
schema Appendice A → JSONB salvato in `course_document.summary`.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import async_session_factory
from app.models.course_document import CourseDocument
from app.services import document_extraction_service, openai_summarize_service
from app.services.openai_client import OpenAINotConfiguredError

log = get_logger("app.course_document.worker")


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _resolve_disk_path(public_path: str) -> Path:
    """Mappa `/uploads/courses/<id>/<file>` → disk path assoluto."""
    settings = get_settings()
    if public_path.startswith("/uploads/"):
        rel = public_path[len("/uploads/"):]
    else:
        rel = public_path.lstrip("/")
    return (settings.upload_root / rel).resolve()


async def _process_one(db: AsyncSession, doc: CourseDocument) -> None:
    """Elabora un singolo documento: estrai testo → riassumi → salva."""
    # Marca processing + bump tentativi (commit immediato per visibilità UI).
    doc.summary_status = "processing"
    doc.summary_attempts = (doc.summary_attempts or 0) + 1
    doc.summary_error = None
    await db.commit()
    await db.refresh(doc)

    disk_path = _resolve_disk_path(doc.file_path)

    # 1) Estrazione testo
    try:
        text, original_chars = await document_extraction_service.extract_text(
            disk_path, doc.mime_type
        )
    except document_extraction_service.DocumentExtractionError as exc:
        doc.summary_status = "failed"
        doc.summary_error = str(exc)
        await write_audit(
            db,
            action="course.document.summary.failed",
            actor_user_id=None,
            target_type="course_document",
            target_id=str(doc.id),
            metadata={
                "phase": "extraction",
                "filename": doc.filename_original,
                "error": str(exc)[:500],
                "attempts": doc.summary_attempts,
            },
        )
        await db.commit()
        log.warning(
            "course_document_extraction_failed",
            doc_id=str(doc.id),
            error=str(exc),
        )
        return

    doc.text_extracted_at = _now()
    doc.text_chars_extracted = len(text)
    if original_chars > len(text):
        log.info(
            "course_document_text_truncated_for_summary",
            doc_id=str(doc.id),
            original=original_chars,
            kept=len(text),
        )

    # 2) Riassunto OpenAI
    try:
        summary, usage = await openai_summarize_service.summarize_document(
            text=text,
            source_filename=doc.filename_original,
        )
    except OpenAINotConfiguredError as exc:
        doc.summary_status = "failed"
        doc.summary_error = (
            "OpenAI non configurato: l'amministratore deve impostare "
            "OPENAI_API_KEY nel file .env del backend."
        )
        await write_audit(
            db,
            action="course.document.summary.failed",
            actor_user_id=None,
            target_type="course_document",
            target_id=str(doc.id),
            metadata={
                "phase": "summarize",
                "filename": doc.filename_original,
                "error": "openai_not_configured",
                "attempts": doc.summary_attempts,
            },
        )
        await db.commit()
        log.warning("course_document_openai_not_configured", doc_id=str(doc.id))
        return
    except openai_summarize_service.OpenAISummarizeError as exc:
        doc.summary_status = "failed"
        doc.summary_error = str(exc)[:500]
        await write_audit(
            db,
            action="course.document.summary.failed",
            actor_user_id=None,
            target_type="course_document",
            target_id=str(doc.id),
            metadata={
                "phase": "summarize",
                "filename": doc.filename_original,
                "error": str(exc)[:500],
                "attempts": doc.summary_attempts,
            },
        )
        await db.commit()
        log.warning(
            "course_document_summarize_failed",
            doc_id=str(doc.id),
            error=str(exc),
        )
        return

    # 3) Persistenza
    doc.summary = summary.model_dump()
    doc.summary_tokens = usage
    doc.summary_status = "ready"
    doc.summary_generated_at = _now()
    doc.summary_error = None
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
        },
    )
    await db.commit()
    log.info(
        "course_document_summary_ready",
        doc_id=str(doc.id),
        tokens=usage.get("total"),
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
        except asyncio.TimeoutError:
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
        except asyncio.TimeoutError:
            _worker_task.cancel()
            with_suppressed_cancel = asyncio.gather(
                _worker_task, return_exceptions=True
            )
            await with_suppressed_cancel
    _worker_task = None
    _stop_event = None
