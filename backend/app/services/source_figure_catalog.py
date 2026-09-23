"""Catalogo delle figure di fonte per il PROMPT 3 e ricontrollo prima di salvare.

- `build_catalog`: figure del corso pronte, utili e di qualità sufficiente,
  ammesse dal predicato unico in modo `select` (politica del documento,
  esclusione del docente, politica di licenza effettiva con l'override
  dell'organizzazione), poi selezione lessicale per la lezione
  (`lesson_figure_selection`). Nessun catalogo per le verifiche, con
  `FIGURE_SOURCE_ENABLED=false` o con budget (b) nullo.
- `not_selectable`: ricontrollo TOCTOU delle figure scelte dal modello,
  sullo stato del DB al momento della materializzazione (un documento
  diventato riservato durante la generazione non entra).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.course import Course
from app.models.course_document import CourseDocument
from app.models.course_document_figure import CourseDocumentFigure
from app.models.course_lesson import CourseLesson
from app.models.organization_course_settings import OrganizationCourseSettings
from app.services.asset_validation_service import SourceFigureInfo
from app.services.lesson_figure_selection import FigureCatalog, select_figure_candidates
from app.services.source_figure_policy import effective_license_policy, figure_visibility

# Tipi che la Vision marca come non didattici anche quando «utili».
EXCLUDED_KINDS = frozenset({"logo_or_decoration"})


def unsuitable_reason(fig: CourseDocumentFigure) -> str | None:
    """Perché una figura ammessa dal predicato non si propone comunque: gli
    stessi filtri del catalogo (non utile alla didattica, qualità sotto
    `FIGURE_MIN_QUALITY_SCORE`, loghi e decorazioni). Vale anche per il
    selettore del frontend e per la guardia del PATCH."""
    if fig.is_useful_for_teaching is not True:
        return "not_useful"
    if (fig.quality_score or 0) < int(get_settings().figure_min_quality_score):
        return "low_quality"
    if fig.kind in EXCLUDED_KINDS:
        return "excluded_kind"
    return None


@dataclass(frozen=True)
class CatalogResult:
    catalog: FigureCatalog
    budget: int
    license_policy: str


async def license_policy_for(db: AsyncSession, course: Course) -> str:
    org_policy = (
        await db.execute(
            select(OrganizationCourseSettings.figure_source_license_policy).where(
                OrganizationCourseSettings.organization_id == course.organization_id
            )
        )
    ).scalar_one_or_none()
    return effective_license_policy(org_policy, get_settings().figure_source_license_policy)


def budget_for(lesson: CourseLesson) -> int:
    settings = get_settings()
    if lesson.is_assessment:
        return 0
    if lesson.is_introductory:
        return max(0, int(settings.figure_source_max_per_intro_lesson))
    return max(0, int(settings.figure_source_max_per_lesson))


async def _documents(db: AsyncSession, course_id: uuid.UUID) -> dict[uuid.UUID, CourseDocument]:
    rows = await db.execute(
        select(CourseDocument)
        .where(CourseDocument.course_id == course_id)
        .execution_options(populate_existing=True)
    )
    return {d.id: d for d in rows.scalars().all()}


async def selectable_figures(
    db: AsyncSession, course: Course, *, license_policy: str
) -> list[CourseDocumentFigure]:
    settings = get_settings()
    rows = await db.execute(
        select(CourseDocumentFigure).where(
            CourseDocumentFigure.course_id == course.id,
            CourseDocumentFigure.status == "ready",
            CourseDocumentFigure.excluded_by_user.is_(False),
            CourseDocumentFigure.reject_reason.is_(None),
            CourseDocumentFigure.is_useful_for_teaching.is_(True),
            CourseDocumentFigure.quality_score >= int(settings.figure_min_quality_score),
        )
    )
    docs = await _documents(db, course.id)
    out = []
    for fig in rows.scalars().all():
        if fig.kind in EXCLUDED_KINDS:
            continue
        doc = docs.get(fig.document_id) if fig.document_id else None
        visibility = figure_visibility(
            fig, doc, course_id=course.id, license_policy=license_policy, mode="select"
        )
        if visibility.renderable:
            out.append(fig)
    return out


async def build_catalog(
    db: AsyncSession, course: Course, lesson: CourseLesson
) -> CatalogResult | None:
    settings = get_settings()
    budget = budget_for(lesson)
    if not settings.figure_source_enabled or budget <= 0:
        return None
    policy = await license_policy_for(db, course)
    figures = await selectable_figures(db, course, license_policy=policy)
    catalog = select_figure_candidates(
        figures,
        lesson,
        max_items=int(settings.figure_source_catalog_max_items),
        max_chars=int(settings.figure_source_catalog_max_chars),
    )
    if not catalog.candidates:
        return None
    return CatalogResult(catalog=catalog, budget=budget, license_policy=policy)


async def not_selectable(
    db: AsyncSession, course: Course, figure_ids: Iterable[uuid.UUID], *, license_policy: str
) -> set[uuid.UUID]:
    """Figure scelte che nel frattempo non sono più ammesse."""
    wanted = set(figure_ids)
    if not wanted:
        return set()
    rows = await db.execute(
        select(CourseDocumentFigure)
        .where(CourseDocumentFigure.id.in_(wanted))
        .execution_options(populate_existing=True)
    )
    found = {f.id: f for f in rows.scalars().all()}
    docs = await _documents(db, course.id)
    blocked = set()
    for fid in wanted:
        fig = found.get(fid)
        doc = docs.get(fig.document_id) if fig is not None and fig.document_id else None
        visibility = figure_visibility(
            fig, doc, course_id=course.id, license_policy=license_policy, mode="select"
        )
        if not visibility.renderable:
            blocked.add(fid)
    return blocked


async def figure_infos(
    db: AsyncSession, course_id: uuid.UUID, by_asset: dict[str, uuid.UUID]
) -> dict[str, SourceFigureInfo]:
    """Descrizione e didascalia originale delle figure fuse (revisore),
    solo del corso (difesa in profondità: arrivano dal catalogo)."""
    if not by_asset:
        return {}
    rows = await db.execute(
        select(CourseDocumentFigure).where(
            CourseDocumentFigure.id.in_(set(by_asset.values())),
            CourseDocumentFigure.course_id == course_id,
        )
    )
    found = {f.id: f for f in rows.scalars().all()}
    out = {}
    for asset_id, fid in by_asset.items():
        fig = found.get(fid)
        if fig is not None:
            out[asset_id] = SourceFigureInfo(
                description=fig.description or "", original_caption=fig.source_caption or ""
            )
    return out
