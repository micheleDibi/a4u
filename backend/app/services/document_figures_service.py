"""Transizioni di stato e ciclo di vita delle figure di fonte dei documenti.

- Richiesta di estrazione (bottone «Estrai figure», script di
  amministrazione): idempotente; `figures_status` NULL = mai richiesta,
  nessun backfill automatico. Documenti non estraibili → `skipped` con il
  motivo (politica, formato, estrazione spenta).
- Cambio di politica verso `citable`: il documento torna in coda solo se era
  stato saltato proprio per la politica.
- Cancellazione del documento (decisione U1, non retroattiva): le figure già
  usate da una lezione vengono staccate (riga e file conservati,
  attribuzione congelata identica), le altre cancellate con i loro file.
- Figure staccate non più usate: pulizia esplicita (`gc_detached`, dallo
  script `extract_document_figures.py --gc-detached`).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.course import Course
from app.models.course_document import CourseDocument
from app.models.course_document_figure import CourseDocumentFigure
from app.models.course_lesson import CourseLesson
from app.services.document_figures import storage as figure_storage
from app.services.figure_attribution import freeze_attribution

log = get_logger("app.document_figures_service")

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
EXTRACTABLE_MIMES = frozenset({PDF_MIME, DOCX_MIME, PPTX_MIME})
ACTIVE_STATUSES = ("pending", "processing")
POLICY_SKIP_CODES = ("policy_excluded", "policy_content_only")
SOURCE_FIGURE_FORMAT = "source_figure"


def _now() -> datetime:
    return datetime.now(UTC)


def skip_code(doc: CourseDocument) -> str | None:
    """Motivo per cui il documento non si estrae (None = estraibile)."""
    if doc.citation_policy == "excluded":
        return "policy_excluded"
    if doc.citation_policy == "content_only":
        return "policy_content_only"
    if doc.mime_type not in EXTRACTABLE_MIMES:
        return "unsupported_format"
    if not get_settings().figure_extraction_enabled:
        return "extraction_disabled"
    return None


def _queue(doc: CourseDocument) -> None:
    doc.figures_status = "pending"
    doc.figures_error_code = None
    doc.figures_error = None
    doc.figures_attempts = 0
    doc.figures_next_attempt_at = None
    doc.figures_requested_at = _now()


def _mark_request(doc: CourseDocument) -> bool:
    """Applica la richiesta; True se lo stato è cambiato."""
    if doc.figures_status in ACTIVE_STATUSES or doc.figures_status == "ready":
        return False
    code = skip_code(doc)
    if code is not None:
        changed = (doc.figures_status, doc.figures_error_code) != ("skipped", code)
        doc.figures_status = "skipped"
        doc.figures_error_code = code
        doc.figures_error = None
        return changed
    _queue(doc)
    return True


async def request_extraction(
    db: AsyncSession, *, course: Course, doc: CourseDocument, actor_id: uuid.UUID | None
) -> CourseDocument:
    """Mette in coda l'estrazione delle figure del documento (idempotente:
    in coda, in corso o pronta → nessun cambio)."""
    previous = doc.figures_status
    if _mark_request(doc):
        await write_audit(
            db,
            action="course.document.figures.extract",
            actor_user_id=actor_id,
            organization_id=course.organization_id,
            target_type="course_document",
            target_id=str(doc.id),
            metadata={
                "course_id": str(course.id),
                "previous": previous,
                "status": doc.figures_status,
                "code": doc.figures_error_code,
            },
        )
    await db.commit()
    await db.refresh(doc)
    return doc


async def request_course_extraction(
    db: AsyncSession, *, course: Course, actor_id: uuid.UUID | None
) -> list[CourseDocument]:
    docs = list(
        (
            await db.execute(
                select(CourseDocument)
                .where(CourseDocument.course_id == course.id)
                .order_by(CourseDocument.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    changed = [d for d in docs if _mark_request(d)]
    if changed:
        await write_audit(
            db,
            action="course.documents.figures.extract",
            actor_user_id=actor_id,
            organization_id=course.organization_id,
            target_type="course",
            target_id=str(course.id),
            metadata={"documents": [str(d.id) for d in changed]},
        )
    await db.commit()
    for d in docs:
        await db.refresh(d)
    return docs


def requeue_after_policy_change(doc: CourseDocument, *, old_policy: str) -> bool:
    """Politica tornata `citable` su un documento saltato per la politica:
    torna in coda. Nessun altro cambio (le figure già estratte restano;
    il catalogo applica la politica corrente)."""
    if (
        doc.citation_policy == "citable"
        and old_policy != "citable"
        and doc.figures_status == "skipped"
        and doc.figures_error_code in POLICY_SKIP_CODES
        and skip_code(doc) is None
    ):
        _queue(doc)
        return True
    return False


def source_figure_ids(content_raw: Any) -> set[uuid.UUID]:
    """Id delle figure di fonte citate negli asset di una lezione."""
    ids: set[uuid.UUID] = set()
    if not isinstance(content_raw, dict):
        return ids
    for asset in content_raw.get("visual_assets") or []:
        if not isinstance(asset, dict) or asset.get("format") != SOURCE_FIGURE_FORMAT:
            continue
        try:
            ids.add(uuid.UUID(str(asset.get("content") or "").strip()))
        except ValueError:
            continue
    return ids


async def used_figure_ids(db: AsyncSession, course_id: uuid.UUID) -> set[uuid.UUID]:
    rows = await db.execute(
        select(CourseLesson.content_raw).where(CourseLesson.course_id == course_id)
    )
    used: set[uuid.UUID] = set()
    for content_raw in rows.scalars().all():
        used |= source_figure_ids(content_raw)
    return used


async def prepare_document_deletion(db: AsyncSession, doc: CourseDocument) -> tuple[list[str], int]:
    """Da chiamare prima di cancellare il documento: stacca le figure usate
    (attribuzione congelata) e cancella le altre righe. Ritorna i percorsi
    dei file da togliere dallo storage DOPO il commit e il numero di figure
    staccate."""
    rows = list(
        (
            await db.execute(
                select(CourseDocumentFigure).where(CourseDocumentFigure.document_id == doc.id)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return [], 0
    used = await used_figure_ids(db, doc.course_id)
    to_delete: list[CourseDocumentFigure] = []
    detached = 0
    for row in rows:
        frozen = freeze_attribution(row, doc) if row.id in used else None
        if frozen is not None:
            row.detached_at = _now()
            row.attribution = frozen
            detached += 1
        else:
            to_delete.append(row)
    paths = [p for row in to_delete for p in (row.storage_path, row.preview_path) if p]
    if to_delete:
        await db.execute(
            delete(CourseDocumentFigure).where(
                CourseDocumentFigure.id.in_([r.id for r in to_delete])
            )
        )
    await db.flush()
    return paths, detached


async def delete_files(paths: Iterable[str]) -> None:
    """Rimozione best-effort dei ritagli dallo storage (dopo il commit)."""
    for path in paths:
        try:
            await asyncio.to_thread(figure_storage.delete, path)
        except Exception as exc:  # pragma: no cover - best effort
            log.warning("document_figures_file_delete_failed", path=path, error=str(exc))


async def delete_course_files(course_id: uuid.UUID) -> None:
    try:
        await asyncio.to_thread(figure_storage.delete_course, course_id)
    except Exception as exc:  # pragma: no cover - best effort
        log.warning(
            "document_figures_course_files_failed", course_id=str(course_id), error=str(exc)
        )


async def gc_detached(
    db: AsyncSession, *, course_id: uuid.UUID | None = None, apply: bool
) -> list[CourseDocumentFigure]:
    """Figure staccate che nessuna lezione usa più: con `apply` le cancella
    (righe e file). Idempotente."""
    query = select(CourseDocumentFigure).where(CourseDocumentFigure.detached_at.is_not(None))
    if course_id is not None:
        query = query.where(CourseDocumentFigure.course_id == course_id)
    rows = list((await db.execute(query)).scalars().all())
    used_by_course: dict[uuid.UUID, set[uuid.UUID]] = {}
    orphans = []
    for row in rows:
        if row.course_id not in used_by_course:
            used_by_course[row.course_id] = await used_figure_ids(db, row.course_id)
        if row.id not in used_by_course[row.course_id]:
            orphans.append(row)
    if apply and orphans:
        paths = [p for row in orphans for p in (row.storage_path, row.preview_path) if p]
        await db.execute(
            delete(CourseDocumentFigure).where(CourseDocumentFigure.id.in_([r.id for r in orphans]))
        )
        await db.commit()
        await delete_files(paths)
    return orphans
