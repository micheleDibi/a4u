from __future__ import annotations

import uuid

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.core.i18n_scripts import is_meaningful_translation
from app.core.logging import get_logger
from app.models.language import Language
from app.models.translation import Translation
from app.schemas.i18n import LanguageCreate, LanguageUpdate
from app.services.openai_translate_service import (
    OpenAINotConfiguredError,
    OpenAITranslateError,
    translate_batch,
)

log = get_logger("app.i18n")

DEFAULT_LANG_CODE = "it"


async def list_languages(
    db: AsyncSession, *, only_active: bool = False
) -> list[Language]:
    q = select(Language).order_by(Language.code.asc())
    if only_active:
        q = q.where(Language.is_active.is_(True))
    res = await db.execute(q)
    return list(res.scalars().all())


async def get_language(db: AsyncSession, code: str) -> Language:
    lang = await db.get(Language, code)
    if lang is None:
        raise NotFoundError("Lingua non trovata.", code="language_not_found")
    return lang


async def get_language_or_none(db: AsyncSession, code: str) -> Language | None:
    return await db.get(Language, code)


async def get_translations(db: AsyncSession, code: str) -> dict[str, str]:
    rows = await db.execute(
        select(Translation.key, Translation.value).where(Translation.language_code == code)
    )
    return {k: v for k, v in rows.all()}


async def create_language(
    db: AsyncSession, *, payload: LanguageCreate, actor_id: uuid.UUID
) -> Language:
    existing = await get_language_or_none(db, payload.code)
    if existing is not None:
        raise ConflictError("Codice lingua già esistente.", code="language_exists")

    if payload.copy_translations_from:
        source = await get_language_or_none(db, payload.copy_translations_from)
        if source is None:
            raise NotFoundError(
                "Lingua sorgente per la copia non trovata.", code="source_language_not_found"
            )

    lang = Language(
        code=payload.code,
        name_native=payload.name_native,
        flag_country_code=payload.flag_country_code,
        rtl=payload.rtl,
        is_active=payload.is_active,
        is_default=False,
    )
    db.add(lang)
    await db.flush()

    if payload.copy_translations_from:
        src = payload.copy_translations_from
        rows = await db.execute(
            select(Translation.key, Translation.value).where(Translation.language_code == src)
        )
        for key, value in rows.all():
            db.add(Translation(language_code=lang.code, key=key, value=value))
        await db.flush()

    await write_audit(
        db,
        action="i18n.language.create",
        actor_user_id=actor_id,
        target_type="language",
        target_id=lang.code,
        metadata={
            "name_native": lang.name_native,
            "copy_from": payload.copy_translations_from,
        },
    )
    return lang


async def update_language(
    db: AsyncSession, *, language: Language, payload: LanguageUpdate, actor_id: uuid.UUID
) -> Language:
    if payload.name_native is not None:
        language.name_native = payload.name_native
    if payload.flag_country_code is not None:
        language.flag_country_code = payload.flag_country_code or None
    if payload.rtl is not None:
        language.rtl = payload.rtl
    if payload.is_active is not None:
        language.is_active = payload.is_active
    if payload.is_default is True:
        # Spunta default solo su questa lingua, rimuove dagli altri.
        await db.execute(
            update(Language)
            .where(Language.code != language.code)
            .values(is_default=False)
        )
        language.is_default = True
    elif payload.is_default is False:
        # Non si può rimuovere il default senza assegnarlo ad un'altra lingua.
        if language.is_default:
            raise ConflictError(
                "Imposta prima un'altra lingua come default.", code="default_required"
            )

    await db.flush()
    await write_audit(
        db,
        action="i18n.language.update",
        actor_user_id=actor_id,
        target_type="language",
        target_id=language.code,
        metadata=payload.model_dump(exclude_none=True),
    )
    return language


async def delete_language(
    db: AsyncSession, *, language: Language, actor_id: uuid.UUID
) -> None:
    if language.is_default:
        raise ConflictError(
            "Impossibile eliminare la lingua default.", code="cannot_delete_default"
        )
    code = language.code
    await db.delete(language)
    await db.flush()
    await write_audit(
        db,
        action="i18n.language.delete",
        actor_user_id=actor_id,
        target_type="language",
        target_id=code,
    )


async def clear_translations(
    db: AsyncSession,
    *,
    language: Language,
    actor_id: uuid.UUID,
) -> int:
    """Elimina TUTTE le traduzioni di una lingua. Vietato sulla lingua default.

    Pensato come reset preliminare alla traduzione automatica: l'utente
    cancella i valori esistenti e poi clicca "Completa con AI".
    """
    if language.is_default:
        raise ConflictError(
            "Impossibile svuotare la lingua di riferimento.",
            code="cannot_clear_default",
        )
    result = await db.execute(
        delete(Translation).where(Translation.language_code == language.code)
    )
    deleted = int(result.rowcount or 0)
    await db.flush()
    await write_audit(
        db,
        action="i18n.translations.clear",
        actor_user_id=actor_id,
        target_type="language",
        target_id=language.code,
        metadata={"deleted": deleted},
    )
    return deleted


async def upsert_translations(
    db: AsyncSession,
    *,
    language: Language,
    translations: dict[str, str],
    actor_id: uuid.UUID,
    replace: bool = False,
) -> int:
    """Aggiorna in bulk le traduzioni di una lingua.

    - `replace=False` (PATCH): aggiorna le chiavi presenti, lascia inalterate le altre.
    - `replace=True` (PUT):    sostituisce **tutte** le traduzioni con quelle fornite
                               (cancella le chiavi mancanti). Da usare con cautela.
    """
    for k, v in translations.items():
        if not isinstance(k, str) or not k:
            raise ValidationAppError("Chiave traduzione non valida.", code="invalid_key")
        if not isinstance(v, str):
            raise ValidationAppError(
                f"Valore non stringa per chiave '{k}'.", code="invalid_value"
            )

    existing_rows = (
        await db.execute(
            select(Translation).where(Translation.language_code == language.code)
        )
    ).scalars().all()
    existing_map: dict[str, Translation] = {r.key: r for r in existing_rows}

    if replace:
        keys_to_keep = set(translations.keys())
        for key, row in existing_map.items():
            if key not in keys_to_keep:
                await db.delete(row)

    upserted = 0
    for key, value in translations.items():
        row = existing_map.get(key)
        if row is None:
            db.add(Translation(language_code=language.code, key=key, value=value))
            upserted += 1
        elif row.value != value:
            row.value = value
            upserted += 1
    await db.flush()

    await write_audit(
        db,
        action="i18n.translations.bulk_update",
        actor_user_id=actor_id,
        target_type="language",
        target_id=language.code,
        metadata={"upserted": upserted, "replace": replace},
    )
    return upserted


async def upsert_single_translation(
    db: AsyncSession,
    *,
    language: Language,
    key: str,
    value: str,
    actor_id: uuid.UUID,
) -> None:
    if not key:
        raise ValidationAppError("Chiave non valida.", code="invalid_key")
    row = (
        await db.execute(
            select(Translation).where(
                Translation.language_code == language.code, Translation.key == key
            )
        )
    ).scalar_one_or_none()
    if row is None:
        db.add(Translation(language_code=language.code, key=key, value=value))
    else:
        row.value = value
    await db.flush()
    await write_audit(
        db,
        action="i18n.translation.update",
        actor_user_id=actor_id,
        target_type="translation",
        target_id=f"{language.code}:{key}",
        metadata={"key": key},
    )


async def delete_single_translation(
    db: AsyncSession, *, language: Language, key: str, actor_id: uuid.UUID
) -> None:
    await db.execute(
        delete(Translation).where(
            Translation.language_code == language.code, Translation.key == key
        )
    )
    await db.flush()
    await write_audit(
        db,
        action="i18n.translation.delete",
        actor_user_id=actor_id,
        target_type="translation",
        target_id=f"{language.code}:{key}",
    )


def _missing_or_fallback_keys(
    *,
    reference: dict[str, str],
    target: dict[str, str],
    target_code: str | None = None,
) -> dict[str, str]:
    """Identifica le chiavi non tradotte rispetto alla lingua di riferimento.

    Una chiave è considerata "non tradotta" se:
      - È effettivamente assente nel target.
      - Il valore target è vuoto/whitespace.
      - Il valore target NON è una traduzione plausibile (per `target_code`
        con script non-Latino significa che il valore non contiene caratteri
        dello script atteso → es. "Lingue" salvato come traduzione cinese).

    Per script Latini (target_code in {it, en, fr, es, ...} o `target_code=None`)
    valori uguali al reference vengono accettati come traduzioni valide
    (brand, prestiti linguistici, termini tecnici).

    Restituisce un dict {key: reference_value} con le chiavi da ri-tradurre.
    """
    missing: dict[str, str] = {}
    for key, ref_value in reference.items():
        if not isinstance(ref_value, str) or not ref_value.strip():
            continue
        target_value = target.get(key)
        if target_value is None or not target_value.strip():
            missing[key] = ref_value
            continue
        if target_code and not is_meaningful_translation(
            ref_value, target_value, target_code
        ):
            missing[key] = ref_value
    return missing


async def count_untranslated_per_language(
    db: AsyncSession, *, default_code: str = DEFAULT_LANG_CODE
) -> dict[str, int]:
    """Conta, per ogni lingua, il numero di chiavi non ancora tradotte.

    Una chiave è non-tradotta se manca nel target o se il suo valore coincide
    con quello della lingua di riferimento (it). Esegue una sola query e
    raggruppa in memoria per evitare round-trip ripetuti.
    """
    rows = (
        await db.execute(
            select(
                Translation.language_code, Translation.key, Translation.value
            )
        )
    ).all()
    by_lang: dict[str, dict[str, str]] = {}
    for code, key, value in rows:
        by_lang.setdefault(code, {})[key] = value

    reference = by_lang.get(default_code, {})
    if not reference:
        return {code: 0 for code in by_lang.keys()}

    counts: dict[str, int] = {}
    for code, translations in by_lang.items():
        if code == default_code:
            counts[code] = 0
            continue
        missing = _missing_or_fallback_keys(
            reference=reference, target=translations, target_code=code
        )
        counts[code] = len(missing)
    counts.setdefault(default_code, 0)
    return counts


async def count_untranslated_for_language(
    db: AsyncSession, *, code: str, default_code: str = DEFAULT_LANG_CODE
) -> int:
    """Variante mono-lingua di `count_untranslated_per_language`."""
    if code == default_code:
        return 0
    reference = await get_translations(db, default_code)
    if not reference:
        return 0
    target = await get_translations(db, code)
    return len(
        _missing_or_fallback_keys(
            reference=reference, target=target, target_code=code
        )
    )


def _chunk(
    items: dict[str, str], size: int
) -> list[dict[str, str]]:
    if size <= 0:
        return [items] if items else []
    keys = list(items.keys())
    return [
        {k: items[k] for k in keys[i : i + size]}
        for i in range(0, len(keys), size)
    ]


async def auto_translate_missing(
    db: AsyncSession,
    *,
    language: Language,
    actor_id: uuid.UUID,
    default_code: str = DEFAULT_LANG_CODE,
    default_name: str = "Italian",
) -> dict[str, object]:
    """Traduce automaticamente via OpenAI tutte le chiavi non tradotte di `language`.

    Ritorna un dict con:
      - `requested`: numero di chiavi candidate alla traduzione.
      - `translated`: numero di chiavi effettivamente upserted.
      - `skipped`: numero di chiavi non restituite o restituite vuote da OpenAI.
      - `errors`: lista di messaggi di errore per batch falliti (non interrompe
        l'esecuzione: traduce ciò che può, riporta gli errori).
    """
    if language.code == default_code:
        return {"requested": 0, "translated": 0, "skipped": 0, "errors": []}

    reference = await get_translations(db, default_code)
    target = await get_translations(db, language.code)

    # Pulizia preliminare: per lingue con script non-Latino, rimuove dal DB
    # le righe che contengono echi del source italiano (residuo di run
    # precedenti dove il filtro echo non esisteva). Le chiavi cancellate
    # vengono poi rilevate come `missing` nello step successivo.
    cleaned_echoes = 0
    echo_keys = {
        k
        for k, v in target.items()
        if reference.get(k)
        and not is_meaningful_translation(reference[k], v, language.code)
    }
    if echo_keys:
        await db.execute(
            delete(Translation).where(
                Translation.language_code == language.code,
                Translation.key.in_(echo_keys),
            )
        )
        await db.flush()
        cleaned_echoes = len(echo_keys)
        log.info(
            "auto_translate_cleaned_echoes",
            language=language.code,
            count=cleaned_echoes,
        )
        target = await get_translations(db, language.code)

    missing = _missing_or_fallback_keys(
        reference=reference, target=target, target_code=language.code
    )

    requested = len(missing)
    if not missing:
        log.info(
            "auto_translate_nothing_to_do",
            language=language.code,
        )
        return {
            "requested": 0,
            "translated": 0,
            "skipped": 0,
            "errors": [],
        }

    settings = get_settings()
    batch_size = max(1, settings.openai_translate_batch_size)
    target_name = language.name_native or language.code

    translated_total: dict[str, str] = {}
    errors: list[str] = []

    async def _run_pass(items: dict[str, str], size: int) -> None:
        """Esegue una passata di traduzione e aggiorna `translated_total`."""
        for batch in _chunk(items, size):
            try:
                translated = await translate_batch(
                    items=batch,
                    source_lang_code=default_code,
                    source_lang_name=default_name,
                    target_lang_code=language.code,
                    target_lang_name=target_name,
                )
            except OpenAINotConfiguredError as exc:
                # Inutile riprovare se la chiave non c'è.
                raise ValidationAppError(
                    exc.message, code="openai_not_configured"
                ) from exc
            except OpenAITranslateError as exc:
                log.error(
                    "auto_translate_batch_failed",
                    language=language.code,
                    batch_size=len(batch),
                    error=str(exc),
                )
                errors.append(str(exc))
                continue
            for k, v in translated.items():
                if k in batch and isinstance(v, str) and v.strip():
                    translated_total[k] = v

    # Pass principale a batch_size pieno.
    await _run_pass(missing, batch_size)

    # Retry pass per le chiavi che OpenAI ha silenziosamente omesso dalla
    # risposta JSON (succede su batch grandi, anche con response_format=json_object).
    # Ogni retry dimezza la batch size per ridurre il drop rate.
    max_retries = 3
    current_size = batch_size
    for retry in range(max_retries):
        still_missing = {
            k: v for k, v in missing.items() if k not in translated_total
        }
        if not still_missing:
            break
        current_size = max(1, current_size // 2)
        log.info(
            "auto_translate_retry",
            language=language.code,
            retry=retry + 1,
            still_missing=len(still_missing),
            new_batch_size=current_size,
        )
        await _run_pass(still_missing, current_size)

    upserted = 0
    if translated_total:
        upserted = await upsert_translations(
            db,
            language=language,
            translations=translated_total,
            actor_id=actor_id,
            replace=False,
        )

    skipped = requested - len(translated_total)
    await write_audit(
        db,
        action="i18n.translations.auto_translate",
        actor_user_id=actor_id,
        target_type="language",
        target_id=language.code,
        metadata={
            "requested": requested,
            "translated": len(translated_total),
            "upserted": upserted,
            "skipped": skipped,
            "cleaned_echoes": cleaned_echoes,
            "errors": errors[:5],
        },
    )
    log.info(
        "auto_translate_done",
        language=language.code,
        requested=requested,
        translated=len(translated_total),
        upserted=upserted,
        skipped=skipped,
        errors=len(errors),
    )
    return {
        "requested": requested,
        "translated": len(translated_total),
        "skipped": skipped,
        "errors": errors,
    }
