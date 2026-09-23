"""Resolver delle figure di fonte per la resa (J-Q1, WP4).

Un asset `source_figure` di `content_raw.visual_assets` porta in `content`
l'UUID della riga `course_document_figure` (riferimento vivo): l'immagine e
la riga «Fonte» le decide SOLO il server, qui, a ogni resa. Il modello e il
client non scrivono mai la fonte.

- Modo `render` del predicato (`source_figure_policy.figure_visibility`):
  solo le condizioni strutturali (corso, stato, attribuzione, file). Una
  figura già collocata resta anche se la politica del documento è cambiata
  (U1, non retroattivo).
- Filtro per `course_id` nella query e controllo del percorso del file
  (`document_figures.storage.belongs_to_course`): un UUID di un altro corso
  non si risolve mai (G7).
- Senza riga «Fonte» la figura non si rende: il chiamante mostra il
  segnaposto numerato (`courses.figures.missing`), mai l'immagine senza
  attribuzione.

I byte si leggono dallo storage in `to_thread`; la mappa ritornata è per
`asset_id` e passa come UN parametro (`source_figures`) a
`render_lesson_html`, `render_slides_html` e ai blocchi figura.
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.course_document import CourseDocument
from app.models.course_document_figure import CourseDocumentFigure
from app.schemas.course_lesson_content import SOURCE_FIGURE_FORMAT
from app.services.document_figures import storage as figure_storage
from app.services.figure_attribution import (
    attribution_source,
    figure_attribution_line,
    spoken_source,
)
from app.services.source_figure_policy import figure_visibility

log = get_logger("app.source_figure_service")


@dataclass(frozen=True)
class ResolvedSourceFigure:
    """Esito della risoluzione di un asset `source_figure`.

    `renderable` è vero solo con immagine E riga «Fonte»; altrimenti
    `reason` dice perché (`not_found`, `wrong_course`, `not_ready`,
    `attribution_missing`, `file_missing`, `invalid_reference`)."""

    renderable: bool
    reason: str | None = None
    figure_id: uuid.UUID | None = None
    data_url: str = ""
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    # Riga scritta («Fonte: …»): dispensa, PDF slide, frame video, frontend.
    attribution_text: str = ""
    # Dati da pronunciare (PROMPT 6, Fase 5): cognomi, titolo breve, anno.
    spoken: Mapping[str, Any] | None = None


SourceFigureMap = Mapping[str, ResolvedSourceFigure]

_UNRESOLVED = ResolvedSourceFigure(False, "invalid_reference")


def is_source_figure(asset: Any) -> bool:
    return isinstance(asset, dict) and asset.get("format") == SOURCE_FIGURE_FORMAT


def source_figure_uuid(asset: Any) -> uuid.UUID | None:
    """UUID della riga citata dall'asset, o None se il contenuto non lo è."""
    if not is_source_figure(asset):
        return None
    try:
        return uuid.UUID(str(asset.get("content") or "").strip())
    except ValueError:
        return None


def _data_url(mime: str, data: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


async def _read(path: str) -> bytes | None:
    try:
        return await asyncio.to_thread(figure_storage.read, path)
    except Exception as exc:  # storage remoto, file sparito, percorso non valido
        log.warning("source_figure_file_unreadable", path=path, error=str(exc)[:300])
        return None


async def resolve_source_figures(
    db: AsyncSession,
    *,
    course_id: uuid.UUID,
    assets: Iterable[Any],
    language: str | None,
    with_bytes: bool = True,
) -> dict[str, ResolvedSourceFigure]:
    """Risolve gli asset `source_figure` di `assets` (gli altri si
    ignorano). Chiave: `asset_id` così come sta nell'asset.

    `with_bytes=False` salta la lettura dei file (payload API, Fase 4 e 5):
    `renderable` riflette allora solo il predicato e la riga «Fonte»."""
    wanted: dict[str, uuid.UUID | None] = {}
    for asset in assets:
        if not is_source_figure(asset):
            continue
        wanted[str(asset.get("asset_id") or "")] = source_figure_uuid(asset)
    ids = {fid for fid in wanted.values() if fid is not None}
    rows: dict[uuid.UUID, CourseDocumentFigure] = {}
    if ids:
        # `populate_existing`: una riga già nella sessione potrebbe avere un
        # `document_id` vecchio (lo stacco lo azzera via FK, nel DB).
        found = await db.execute(
            select(CourseDocumentFigure)
            .where(
                CourseDocumentFigure.id.in_(ids),
                CourseDocumentFigure.course_id == course_id,
            )
            .execution_options(populate_existing=True)
        )
        rows = {row.id: row for row in found.scalars().all()}
    doc_ids = {row.document_id for row in rows.values() if row.document_id is not None}
    docs: dict[uuid.UUID, CourseDocument] = {}
    if doc_ids:
        found_docs = await db.execute(
            select(CourseDocument)
            .where(CourseDocument.id.in_(doc_ids), CourseDocument.course_id == course_id)
            .execution_options(populate_existing=True)
        )
        docs = {doc.id: doc for doc in found_docs.scalars().all()}

    out: dict[str, ResolvedSourceFigure] = {}
    for asset_id, fid in wanted.items():
        if fid is None:
            out[asset_id] = _UNRESOLVED
            continue
        fig = rows.get(fid)
        doc = docs.get(fig.document_id) if fig is not None and fig.document_id else None
        if fig is not None and fig.document_id is not None and doc is None:
            # Riga che punta a un documento fuori dal corso: mai risolta.
            out[asset_id] = ResolvedSourceFigure(False, "wrong_course", figure_id=fid)
            continue
        visibility = figure_visibility(
            fig, doc, course_id=course_id, license_policy="cite_all", mode="render"
        )
        if not visibility.renderable or fig is None:
            out[asset_id] = ResolvedSourceFigure(False, visibility.reason, figure_id=fid)
            continue
        line = figure_attribution_line(fig, doc, language=language) or ""
        if not line.strip():
            out[asset_id] = ResolvedSourceFigure(False, "attribution_missing", figure_id=fid)
            continue
        path = str(fig.storage_path or "")
        if not figure_storage.belongs_to_course(path, course_id):
            out[asset_id] = ResolvedSourceFigure(False, "wrong_course", figure_id=fid)
            continue
        src = attribution_source(fig, doc)
        spoken = spoken_source(src, language=language) if src is not None else None
        data_url = ""
        if with_bytes:
            data = await _read(path)
            if not data:
                out[asset_id] = ResolvedSourceFigure(False, "file_missing", figure_id=fid)
                continue
            data_url = _data_url(str(fig.mime_type or "image/png"), data)
        out[asset_id] = ResolvedSourceFigure(
            True,
            None,
            figure_id=fid,
            data_url=data_url,
            mime_type=fig.mime_type,
            width=fig.width,
            height=fig.height,
            attribution_text=line,
            spoken=spoken,
        )
    for asset_id, resolved in out.items():
        if not resolved.renderable:
            log.warning(
                "source_figure_unresolved",
                course_id=str(course_id),
                asset_id=asset_id,
                figure_id=str(resolved.figure_id) if resolved.figure_id else None,
                reason=resolved.reason,
            )
    return out


def lesson_source_assets(lesson: Any) -> list[dict[str, Any]]:
    """Asset `source_figure` della dispensa (le slide li citano da lì)."""
    raw = lesson.content_raw if isinstance(getattr(lesson, "content_raw", None), dict) else {}
    return [a for a in raw.get("visual_assets") or [] if is_source_figure(a)]
