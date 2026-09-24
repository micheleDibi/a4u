"""Servizio delle API delle figure di fonte del corso (WP4).

- `list_course_figures`: figure pronte del corso (o di un suo documento)
  con la riga «Fonte» del
  server, la resa (modo render, non retroattivo) e la proponibilità (modo
  select con la politica di licenza effettiva, più qualità e utilità come
  il catalogo del PROMPT 3);
- `figure_image`: byte del ritaglio (o dell'anteprima) di una figura del
  corso che si può rendere; 404 in ogni altro caso, senza dire perché;
- `update_figure`: esclusione del docente (non retroattiva, U1);
- `document_figure_usage`: dove sono collocate le figure di un documento.

Ogni lettura è filtrata per `course_id` (G7): l'id di una figura di un
altro corso è un 404, come un id inventato.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.models.course import Course
from app.models.course_document import CourseDocument
from app.models.course_document_figure import CourseDocumentFigure
from app.models.course_lesson import CourseLesson
from app.schemas.document_figures import (
    DocumentFigureOut,
    DocumentFigureUsageOut,
    FigureUsageLesson,
)
from app.services.document_figures import storage as figure_storage
from app.services.figure_attribution import figure_attribution_line
from app.services.source_caption import clean_caption
from app.services.source_figure_catalog import license_policy_for, unsuitable_reason
from app.services.source_figure_policy import figure_visibility
from app.services.source_figure_service import source_figure_uuid

log = get_logger("app.source_figure_api_service")

_NOT_FOUND = "Figura non trovata."


async def _documents(db: AsyncSession, course_id: uuid.UUID) -> dict[uuid.UUID, CourseDocument]:
    rows = await db.execute(select(CourseDocument).where(CourseDocument.course_id == course_id))
    return {d.id: d for d in rows.scalars().all()}


def _selectable_reason(fig: CourseDocumentFigure, select_reason: str | None) -> str | None:
    return select_reason if select_reason is not None else unsuitable_reason(fig)


_CAPTION_MAX_CHARS = 600


def suggested_caption(fig: CourseDocumentFigure) -> str:
    """Didascalia da proporre inserendo la figura dal selettore."""
    caption = clean_caption(fig.source_caption)[0] if fig.source_caption else ""
    text = " ".join((caption or fig.description or "").split())
    if len(text) <= _CAPTION_MAX_CHARS:
        return text
    cut = text[: _CAPTION_MAX_CHARS - 1]
    return (cut[: cut.rfind(" ")] if " " in cut else cut).rstrip(" ,;:") + "\u2026"


def figure_out(
    fig: CourseDocumentFigure,
    doc: CourseDocument | None,
    *,
    course: Course,
    license_policy: str,
) -> DocumentFigureOut:
    render = figure_visibility(
        fig, doc, course_id=course.id, license_policy=license_policy, mode="render"
    )
    selectable = figure_visibility(
        fig, doc, course_id=course.id, license_policy=license_policy, mode="select"
    )
    reason = _selectable_reason(fig, selectable.reason)
    return DocumentFigureOut(
        id=fig.id,
        document_id=fig.document_id,
        document_filename=doc.filename_original if doc is not None else None,
        source_kind=fig.source_kind,
        page=fig.page,
        locator=fig.locator,
        kind=fig.kind,
        description=fig.description,
        keywords=fig.keywords,
        source_caption=fig.source_caption,
        source_label=fig.source_label,
        suggested_caption=suggested_caption(fig),
        width=fig.width,
        height=fig.height,
        mime_type=fig.mime_type,
        quality_score=fig.quality_score,
        is_useful_for_teaching=fig.is_useful_for_teaching,
        excluded_by_user=fig.excluded_by_user,
        status=fig.status,
        license=fig.license,
        detached=fig.detached_at is not None,
        attribution=figure_attribution_line(fig, doc, language=course.language_code) or "",
        renderable=render.renderable,
        selectable=reason is None,
        reason=reason,
    )


async def list_course_figures(
    db: AsyncSession, *, course: Course, document_id: uuid.UUID | None = None
) -> list[DocumentFigureOut]:
    """Figure pronte del corso; con `document_id` solo quelle di quel
    documento (riassunto strutturato)."""
    query = select(CourseDocumentFigure).where(
        CourseDocumentFigure.course_id == course.id,
        CourseDocumentFigure.status == "ready",
    )
    if document_id is not None:
        query = query.where(CourseDocumentFigure.document_id == document_id)
    rows = await db.execute(
        query.order_by(
            CourseDocumentFigure.document_id.asc().nulls_last(),
            CourseDocumentFigure.page.asc().nulls_last(),
            CourseDocumentFigure.locator.asc(),
        )
    )
    docs = await _documents(db, course.id)
    policy = await license_policy_for(db, course)
    out = []
    for fig in rows.scalars().all():
        doc = docs.get(fig.document_id) if fig.document_id else None
        out.append(figure_out(fig, doc, course=course, license_policy=policy))
    return out


async def get_course_figure(
    db: AsyncSession, *, course_id: uuid.UUID, figure_id: uuid.UUID
) -> CourseDocumentFigure:
    fig = (
        await db.execute(
            select(CourseDocumentFigure).where(
                CourseDocumentFigure.id == figure_id,
                CourseDocumentFigure.course_id == course_id,
            )
        )
    ).scalar_one_or_none()
    if fig is None:
        raise NotFoundError(_NOT_FOUND, code="document_figure_not_found")
    return fig


async def figure_image(
    db: AsyncSession, *, course: Course, figure_id: uuid.UUID, preview: bool
) -> tuple[bytes, str]:
    """(byte, MIME) del ritaglio; 404 se la figura non si può rendere."""
    fig = await get_course_figure(db, course_id=course.id, figure_id=figure_id)
    doc = await db.get(CourseDocument, fig.document_id) if fig.document_id else None
    if doc is not None and doc.course_id != course.id:
        raise NotFoundError(_NOT_FOUND, code="document_figure_not_found")
    visibility = figure_visibility(
        fig, doc, course_id=course.id, license_policy="cite_all", mode="render"
    )
    if not visibility.renderable:
        raise NotFoundError(_NOT_FOUND, code="document_figure_not_found")
    path = fig.preview_path if preview and fig.preview_path else fig.storage_path
    if not figure_storage.belongs_to_course(path, course.id):
        raise NotFoundError(_NOT_FOUND, code="document_figure_not_found")
    try:
        data = await asyncio.to_thread(figure_storage.read, str(path))
    except Exception as exc:
        log.warning("document_figure_image_unreadable", figure_id=str(fig.id), error=str(exc))
        raise NotFoundError(_NOT_FOUND, code="document_figure_not_found") from exc
    mime = "image/jpeg" if preview and fig.preview_path else str(fig.mime_type or "image/png")
    return data, mime


async def update_figure(
    db: AsyncSession,
    *,
    course: Course,
    figure_id: uuid.UUID,
    excluded_by_user: bool,
    actor_id: uuid.UUID,
) -> DocumentFigureOut:
    fig = await get_course_figure(db, course_id=course.id, figure_id=figure_id)
    if fig.excluded_by_user != excluded_by_user:
        fig.excluded_by_user = excluded_by_user
        await write_audit(
            db,
            action="course.document_figure.update",
            actor_user_id=actor_id,
            organization_id=course.organization_id,
            target_type="course_document_figure",
            target_id=str(fig.id),
            metadata={"course_id": str(course.id), "excluded_by_user": excluded_by_user},
        )
        await db.commit()
        await db.refresh(fig)
    doc = await db.get(CourseDocument, fig.document_id) if fig.document_id else None
    return figure_out(fig, doc, course=course, license_policy=await license_policy_for(db, course))


def _lesson_figure_assets(content_raw: Any) -> list[tuple[str, uuid.UUID]]:
    if not isinstance(content_raw, dict):
        return []
    out = []
    for asset in content_raw.get("visual_assets") or []:
        fid = source_figure_uuid(asset)
        if fid is not None:
            out.append((str(asset.get("asset_id") or ""), fid))
    return out


async def document_figure_usage(
    db: AsyncSession, *, course_id: uuid.UUID, document_id: uuid.UUID
) -> DocumentFigureUsageOut:
    figure_ids = set(
        (
            await db.execute(
                select(CourseDocumentFigure.id).where(
                    CourseDocumentFigure.course_id == course_id,
                    CourseDocumentFigure.document_id == document_id,
                )
            )
        )
        .scalars()
        .all()
    )
    lessons = (
        await db.execute(
            select(CourseLesson)
            .where(CourseLesson.course_id == course_id)
            .order_by(CourseLesson.lesson_code)
        )
    ).scalars()
    used: set[uuid.UUID] = set()
    usage: list[FigureUsageLesson] = []
    for lesson in lessons:
        hits = [
            (aid, fid)
            for aid, fid in _lesson_figure_assets(lesson.content_raw)
            if fid in figure_ids
        ]
        if not hits:
            continue
        used |= {fid for _aid, fid in hits}
        usage.append(
            FigureUsageLesson(
                lesson_id=lesson.id,
                lesson_code=lesson.lesson_code,
                title=lesson.title,
                asset_ids=[aid for aid, _fid in hits],
            )
        )
    return DocumentFigureUsageOut(
        document_id=document_id,
        figures_total=len(figure_ids),
        figures_used=len(used),
        lessons=usage,
    )
