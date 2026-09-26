"""Catalogo delle figure di fonte per il PROMPT 3 e ricontrollo prima di salvare.

- `build_catalog`: figure del corso pronte, utili e di qualità sufficiente,
  ammesse dal predicato unico in modo `select` (politica del documento,
  esclusione del docente, politica di licenza effettiva con l'override
  dell'organizzazione), poi selezione lessicale per la lezione
  (`lesson_figure_selection`). Nessun catalogo per le verifiche, con
  `FIGURE_SOURCE_ENABLED=false` o con budget (b) nullo.
- Riuso limitato: una figura di fonte sta in al più
  `FIGURE_SOURCE_MAX_LESSONS_PER_FIGURE` lezioni del corso. Il catalogo di
  una lezione non offre le figure già in quel numero di ALTRE lezioni; le
  figure già collocate nella lezione stessa restano sue anche alla
  rigenerazione (non consumano un posto). Il docente può comunque inserirle
  a mano dall'editor.
- `not_selectable`: ricontrollo TOCTOU della politica delle figure scelte
  dal modello, sullo stato del DB al momento della materializzazione (un
  documento escluso durante la generazione non entra).
- `over_reuse_cap`: ricontrollo del riuso (figure che nel frattempo altre
  lezioni generate in parallelo hanno portato al tetto).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.course import Course
from app.models.course_document import CourseDocument
from app.models.course_document_figure import CourseDocumentFigure
from app.models.course_lesson import CourseLesson
from app.models.organization_course_settings import OrganizationCourseSettings
from app.services import document_figures_service
from app.services.asset_validation_service import SourceFigureInfo
from app.services.lesson_figure_selection import FigureCatalog, select_figure_candidates
from app.services.source_figure_policy import effective_license_policy, figure_visibility
from app.services.source_figure_resolution import ResolutionInputs, selection_class

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
    if resolution_class(fig) == "unusable":
        return "resolution_unusable"
    return None


def resolution_class(fig: Any) -> str | None:
    """Classe di selezione della figura (doc 18 §22); None con la regola
    spenta (FIGURE_RESOLUTION_RULES_ENABLED=false) o senza pixel."""
    if not get_settings().figure_resolution_rules_enabled:
        return None
    return selection_class(ResolutionInputs.from_figure(fig))


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


def reuse_cap() -> int:
    """Numero massimo di lezioni per figura di fonte (almeno 1)."""
    return max(1, int(get_settings().figure_source_max_lessons_per_figure))


async def saturated_figures(
    db: AsyncSession, course_id: uuid.UUID, lesson_id: uuid.UUID
) -> set[uuid.UUID]:
    """Figure già in `reuse_cap()` lezioni diverse da `lesson_id`, tolte
    quelle già collocate nella lezione stessa (rigenerandola le tiene)."""
    cap = reuse_cap()
    uses = await document_figures_service.figure_lesson_uses(
        db, course_id, except_lesson_id=lesson_id
    )
    own = await document_figures_service.lesson_figure_ids(db, lesson_id)
    return {fid for fid, lessons in uses.items() if len(lessons) >= cap} - own


async def offered_saturated(
    db: AsyncSession, course_id: uuid.UUID, lesson_id: uuid.UUID
) -> set[uuid.UUID]:
    """Figure che, contando anche le offerte valide delle lezioni in
    generazione (piano delle figure), sono già al tetto di riuso per altre
    lezioni: il catalogo non le propone (l'altra lezione le sta collocando e
    il ricontrollo sotto il lock le toglierebbe a chi arriva secondo)."""
    from app.services.source_figure_assignment_service import _offered_ids, valid_offer

    rows = (
        (
            await db.execute(
                select(CourseLesson).where(
                    CourseLesson.course_id == course_id,
                    CourseLesson.content_status == "processing",
                    CourseLesson.id != lesson_id,
                    CourseLesson.figure_assignment.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    # Per codice di lezione, come `figure_lesson_uses`: una lezione in
    # generazione che ha la figura sia nell'offerta sia nel contenuto vecchio
    # conta una volta.
    offered: dict[uuid.UUID, set[str]] = {}
    for row in rows:
        offer = valid_offer(row)
        for fid in _offered_ids(offer) if offer else set():
            offered.setdefault(fid, set()).add(row.lesson_code)
    if not offered:
        return set()
    cap = reuse_cap()
    uses = await document_figures_service.figure_lesson_uses(
        db, course_id, except_lesson_id=lesson_id
    )
    own = await document_figures_service.lesson_figure_ids(db, lesson_id)
    return {
        fid for fid, holders in offered.items() if len(holders | set(uses.get(fid, []))) >= cap
    } - own


async def selectable_figures(
    db: AsyncSession,
    course: Course,
    *,
    license_policy: str,
    lesson_id: uuid.UUID | None = None,
) -> list[CourseDocumentFigure]:
    """Figure proponibili del corso; con `lesson_id`, senza quelle già al
    tetto di riuso in altre lezioni."""
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
    taken = (
        await saturated_figures(db, course.id, lesson_id)
        | await offered_saturated(db, course.id, lesson_id)
        if lesson_id is not None
        else set()
    )
    out = []
    for fig in rows.scalars().all():
        if fig.kind in EXCLUDED_KINDS or fig.id in taken:
            continue
        # Sotto il minimo di risoluzione: mai proposta (neanche rigenerando
        # la lezione che già la usa; la collocazione esistente resta, U1).
        if resolution_class(fig) == "unusable":
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
    figures = await selectable_figures(db, course, license_policy=policy, lesson_id=lesson.id)
    catalog = select_figure_candidates(
        figures,
        lesson,
        max_items=int(settings.figure_source_catalog_max_items),
        max_chars=int(settings.figure_source_catalog_max_chars),
        demote=lambda fig: resolution_class(fig) == "low",
    )
    if not catalog.candidates:
        return None
    return CatalogResult(catalog=catalog, budget=budget, license_policy=policy)


async def not_selectable(
    db: AsyncSession,
    course: Course,
    figure_ids: Iterable[uuid.UUID],
    *,
    license_policy: str,
) -> set[uuid.UUID]:
    """Figure scelte che nel frattempo non sono più ammesse (politica del
    documento, esclusione, licenza, file)."""
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


async def over_reuse_cap(
    db: AsyncSession, course: Course, figure_ids: Iterable[uuid.UUID], *, lesson_id: uuid.UUID
) -> set[uuid.UUID]:
    """Figure scelte che intanto altre lezioni hanno portato al tetto di
    riuso (le figure già collocate nella lezione stessa passano).

    Il worker di Fase 3 lo ripete sotto il lock di corso, tenuto fino al
    commit della materializzazione (`source_figure_assignment_service.
    course_lock`, doc 18 §23.4): le lezioni generate in parallelo si
    serializzano. Resta possibile superare K solo se il lock non si prende
    entro 20 s (si procede senza, con un avviso nel log)."""
    wanted = set(figure_ids)
    if not wanted:
        return set()
    return wanted & await saturated_figures(db, course.id, lesson_id)


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
