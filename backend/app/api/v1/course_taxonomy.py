"""Lettura read-only delle tassonomie corso da parte di qualsiasi utente
autenticato — necessaria per popolare i Select del form di creazione corso.

Le tassonomie sono platform-wide (non per-org), quindi un utente loggato
in qualsiasi organizzazione può leggere i term attivi.

La gestione (CRUD) resta esclusiva del platform admin via `/admin/course-taxonomy`
(vedi `admin_course_taxonomy.py`).
"""
from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession
from app.models.course_taxonomy import CourseTaxonomyTerm
from app.schemas.course_taxonomy import TaxonomyTermOut, TaxonomyType

router = APIRouter(prefix="/course-taxonomy", tags=["course-taxonomy"])


@router.get("/{taxonomy_type}", response_model=list[TaxonomyTermOut])
async def list_active_terms(
    taxonomy_type: TaxonomyType,
    db: DbSession,
    _: CurrentUser,
    only_active: bool = Query(default=True),
) -> list[TaxonomyTermOut]:
    q = select(CourseTaxonomyTerm).where(
        CourseTaxonomyTerm.taxonomy_type == taxonomy_type
    )
    if only_active:
        q = q.where(CourseTaxonomyTerm.is_active.is_(True))
    q = q.order_by(
        CourseTaxonomyTerm.parent_id.asc().nulls_first(),
        CourseTaxonomyTerm.sort_order.asc(),
    )
    rows = (await db.execute(q)).scalars().all()
    return [TaxonomyTermOut.model_validate(r) for r in rows]
