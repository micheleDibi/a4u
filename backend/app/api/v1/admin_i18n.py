from __future__ import annotations

from fastapi import APIRouter

from app.core.deps import DbSession, PlatformAdmin
from app.schemas.i18n import (
    AutoTranslateResponse,
    LanguageCreate,
    LanguageOut,
    LanguageUpdate,
    TranslationsBulkUpdate,
    TranslationsResponse,
)
from app.services import i18n_service

router = APIRouter(prefix="/admin/i18n", tags=["admin-i18n"])


def _to_language_out(lang, untranslated: int = 0) -> LanguageOut:
    out = LanguageOut.model_validate(lang)
    out.untranslated_count = untranslated
    return out


@router.get("/languages", response_model=list[LanguageOut])
async def list_languages(db: DbSession, _: PlatformAdmin) -> list[LanguageOut]:
    items = await i18n_service.list_languages(db)
    counts = await i18n_service.count_untranslated_per_language(db)
    return [_to_language_out(it, counts.get(it.code, 0)) for it in items]


@router.post("/languages", response_model=LanguageOut, status_code=201)
async def create_language(
    payload: LanguageCreate, db: DbSession, admin: PlatformAdmin
) -> LanguageOut:
    lang = await i18n_service.create_language(db, payload=payload, actor_id=admin.id)
    untranslated = await i18n_service.count_untranslated_for_language(
        db, code=lang.code
    )
    return _to_language_out(lang, untranslated)


@router.get("/languages/{code}", response_model=LanguageOut)
async def get_language(code: str, db: DbSession, _: PlatformAdmin) -> LanguageOut:
    lang = await i18n_service.get_language(db, code)
    untranslated = await i18n_service.count_untranslated_for_language(
        db, code=lang.code
    )
    return _to_language_out(lang, untranslated)


@router.patch("/languages/{code}", response_model=LanguageOut)
async def update_language(
    code: str, payload: LanguageUpdate, db: DbSession, admin: PlatformAdmin
) -> LanguageOut:
    lang = await i18n_service.get_language(db, code)
    lang = await i18n_service.update_language(
        db, language=lang, payload=payload, actor_id=admin.id
    )
    untranslated = await i18n_service.count_untranslated_for_language(
        db, code=lang.code
    )
    return _to_language_out(lang, untranslated)


@router.delete("/languages/{code}", status_code=204)
async def delete_language(code: str, db: DbSession, admin: PlatformAdmin) -> None:
    lang = await i18n_service.get_language(db, code)
    await i18n_service.delete_language(db, language=lang, actor_id=admin.id)


@router.get("/languages/{code}/translations", response_model=TranslationsResponse)
async def get_translations(
    code: str, db: DbSession, _: PlatformAdmin
) -> TranslationsResponse:
    lang = await i18n_service.get_language(db, code)
    translations = await i18n_service.get_translations(db, code)
    untranslated = await i18n_service.count_untranslated_for_language(
        db, code=lang.code
    )
    return TranslationsResponse(
        language=_to_language_out(lang, untranslated),
        translations=translations,
    )


@router.put("/languages/{code}/translations")
async def replace_translations(
    code: str, payload: TranslationsBulkUpdate, db: DbSession, admin: PlatformAdmin
) -> dict[str, int]:
    lang = await i18n_service.get_language(db, code)
    upserted = await i18n_service.upsert_translations(
        db,
        language=lang,
        translations=payload.translations,
        actor_id=admin.id,
        replace=True,
    )
    return {"upserted": upserted}


@router.patch("/languages/{code}/translations")
async def patch_translations(
    code: str, payload: TranslationsBulkUpdate, db: DbSession, admin: PlatformAdmin
) -> dict[str, int]:
    lang = await i18n_service.get_language(db, code)
    upserted = await i18n_service.upsert_translations(
        db,
        language=lang,
        translations=payload.translations,
        actor_id=admin.id,
        replace=False,
    )
    return {"upserted": upserted}


@router.delete("/languages/{code}/translations")
async def clear_translations(
    code: str, db: DbSession, admin: PlatformAdmin
) -> dict[str, int]:
    """Svuota tutte le traduzioni di una lingua (preludio a "Completa con AI")."""
    lang = await i18n_service.get_language(db, code)
    deleted = await i18n_service.clear_translations(
        db, language=lang, actor_id=admin.id
    )
    return {"deleted": deleted}


@router.post(
    "/languages/{code}/auto-translate", response_model=AutoTranslateResponse
)
async def auto_translate_language(
    code: str, db: DbSession, admin: PlatformAdmin
) -> AutoTranslateResponse:
    """Completa con AI tutte le chiavi non tradotte della lingua `code`.

    Usa la lingua di riferimento (default: it) come sorgente. Le chiavi mancanti
    o quelle che ancora replicano il valore italiano vengono tradotte via OpenAI
    e upserted in batch. Errori di singoli batch non interrompono l'operazione.
    """
    lang = await i18n_service.get_language(db, code)
    result = await i18n_service.auto_translate_missing(
        db, language=lang, actor_id=admin.id
    )
    return AutoTranslateResponse(
        code=lang.code,
        requested=int(result["requested"]),
        translated=int(result["translated"]),
        skipped=int(result["skipped"]),
        errors=list(result["errors"]),  # type: ignore[arg-type]
    )
