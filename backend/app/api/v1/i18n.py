from __future__ import annotations

from fastapi import APIRouter

from app.core.deps import DbSession
from app.schemas.i18n import PublicLanguageOut, PublicTranslationsResponse
from app.services import i18n_service

router = APIRouter(prefix="/i18n", tags=["i18n"])


@router.get("/languages", response_model=list[PublicLanguageOut])
async def public_list_languages(db: DbSession) -> list[PublicLanguageOut]:
    items = await i18n_service.list_languages(db, only_active=True)
    return [PublicLanguageOut.model_validate(it) for it in items]


@router.get("/translations/{code}", response_model=PublicTranslationsResponse)
async def public_translations(code: str, db: DbSession) -> PublicTranslationsResponse:
    # Anche se la lingua non esiste o è disattivata, ritorniamo dict vuoto:
    # i client devono fare fallback alla lingua di default.
    translations = await i18n_service.get_translations(db, code)
    return PublicTranslationsResponse(code=code, translations=translations)
