from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status

from app.core.deps import CurrentUser, DbSession, PlatformAdmin
from app.schemas.course_taxonomy import (
    TaxonomyBulkAutoTranslateResponse,
    TaxonomyTermCreate,
    TaxonomyTermMove,
    TaxonomyTermOut,
    TaxonomyTermUpdate,
    TaxonomyType,
    TermAutoTranslateResponse,
)
from app.services import course_taxonomy_service

router = APIRouter(prefix="/admin/course-taxonomy", tags=["admin-course-taxonomy"])


@router.get("/{taxonomy_type}", response_model=list[TaxonomyTermOut])
async def list_terms(
    taxonomy_type: TaxonomyType,
    db: DbSession,
    _: PlatformAdmin,
) -> list[TaxonomyTermOut]:
    rows = await course_taxonomy_service.list_terms(db, taxonomy_type)
    return [TaxonomyTermOut.model_validate(r) for r in rows]


@router.post(
    "/{taxonomy_type}",
    response_model=TaxonomyTermOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_term(
    taxonomy_type: TaxonomyType,
    payload: TaxonomyTermCreate,
    db: DbSession,
    current: CurrentUser,
    _: PlatformAdmin,
) -> TaxonomyTermOut:
    term = await course_taxonomy_service.create_term(
        db, taxonomy_type=taxonomy_type, payload=payload, actor_id=current.id
    )
    return TaxonomyTermOut.model_validate(term)


@router.get("/{taxonomy_type}/{term_id}", response_model=TaxonomyTermOut)
async def get_term(
    taxonomy_type: TaxonomyType,
    term_id: uuid.UUID,
    db: DbSession,
    _: PlatformAdmin,
) -> TaxonomyTermOut:
    term = await course_taxonomy_service.get_term(db, term_id, taxonomy_type)
    return TaxonomyTermOut.model_validate(term)


@router.patch("/{taxonomy_type}/{term_id}", response_model=TaxonomyTermOut)
async def update_term(
    taxonomy_type: TaxonomyType,
    term_id: uuid.UUID,
    payload: TaxonomyTermUpdate,
    db: DbSession,
    current: CurrentUser,
    _: PlatformAdmin,
) -> TaxonomyTermOut:
    term = await course_taxonomy_service.get_term(db, term_id, taxonomy_type)
    term = await course_taxonomy_service.update_term(
        db, term=term, payload=payload, actor_id=current.id
    )
    return TaxonomyTermOut.model_validate(term)


@router.delete(
    "/{taxonomy_type}/{term_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_term(
    taxonomy_type: TaxonomyType,
    term_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _: PlatformAdmin,
) -> Response:
    term = await course_taxonomy_service.get_term(db, term_id, taxonomy_type)
    await course_taxonomy_service.delete_term(
        db, term=term, actor_id=current.id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{taxonomy_type}/{term_id}/move", response_model=TaxonomyTermOut)
async def move_term(
    taxonomy_type: TaxonomyType,
    term_id: uuid.UUID,
    payload: TaxonomyTermMove,
    db: DbSession,
    current: CurrentUser,
    _: PlatformAdmin,
) -> TaxonomyTermOut:
    term = await course_taxonomy_service.get_term(db, term_id, taxonomy_type)
    term = await course_taxonomy_service.move_term(
        db, term=term, direction=payload.direction, actor_id=current.id
    )
    return TaxonomyTermOut.model_validate(term)


@router.post(
    "/{taxonomy_type}/{term_id}/auto-translate",
    response_model=TermAutoTranslateResponse,
)
async def auto_translate_term(
    taxonomy_type: TaxonomyType,
    term_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _: PlatformAdmin,
) -> TermAutoTranslateResponse:
    term = await course_taxonomy_service.get_term(db, term_id, taxonomy_type)
    result = await course_taxonomy_service.auto_translate_term(
        db, term=term, actor_id=current.id
    )
    return TermAutoTranslateResponse.model_validate(result)


@router.post(
    "/{taxonomy_type}/auto-translate-all",
    response_model=TaxonomyBulkAutoTranslateResponse,
)
async def auto_translate_all_terms(
    taxonomy_type: TaxonomyType,
    db: DbSession,
    current: CurrentUser,
    _: PlatformAdmin,
) -> TaxonomyBulkAutoTranslateResponse:
    result = await course_taxonomy_service.bulk_auto_translate_taxonomy(
        db, taxonomy_type=taxonomy_type, actor_id=current.id
    )
    return TaxonomyBulkAutoTranslateResponse.model_validate(result)
