"""Service per la gestione della tabella `course_taxonomy_term`.

Una sola tabella con discriminatore `taxonomy_type` per le 8 tassonomie
usate nella creazione corso. Operazioni: list/get/create/update/delete +
swap di sort_order tra siblings + auto-traduzione AI delle label/desc
mancanti.
"""
from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.errors import NotFoundError, ValidationAppError
from app.models.course_taxonomy import CourseTaxonomyTerm
from app.models.language import Language
from app.schemas.course_taxonomy import (
    TaxonomyTermCreate,
    TaxonomyTermUpdate,
    TaxonomyType,
)
from app.services.openai_translate_service import (
    OpenAINotConfiguredError,
    OpenAITranslateError,
    translate_batch,
)

# Tassonomie che ammettono parent (gerarchia a 2 livelli).
HIERARCHICAL_TYPES: frozenset[str] = frozenset(
    {"category", "teacher_role", "eqf_level"}
)

DEFAULT_LANG_CODE = "it"
DEFAULT_LANG_NAME = "Italian"


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


async def list_terms(
    db: AsyncSession, taxonomy_type: TaxonomyType
) -> list[CourseTaxonomyTerm]:
    """Lista i termini di una tassonomia, ordinati: prima i parent (NULL),
    poi i children, raggruppati per parent_id; ciascun gruppo ordinato per
    sort_order asc."""
    res = await db.execute(
        select(CourseTaxonomyTerm)
        .where(CourseTaxonomyTerm.taxonomy_type == taxonomy_type)
        .order_by(
            # NULLS FIRST garantisce i parent in cima.
            CourseTaxonomyTerm.parent_id.asc().nulls_first(),
            CourseTaxonomyTerm.sort_order.asc(),
        )
    )
    return list(res.scalars().all())


async def get_term(
    db: AsyncSession, term_id: uuid.UUID, taxonomy_type: TaxonomyType
) -> CourseTaxonomyTerm:
    term = await db.get(CourseTaxonomyTerm, term_id)
    if term is None or term.taxonomy_type != taxonomy_type:
        raise NotFoundError(
            "Termine tassonomia non trovato.", code="taxonomy_term_not_found"
        )
    return term


async def _get_parent(
    db: AsyncSession, taxonomy_type: TaxonomyType, parent_id: uuid.UUID
) -> CourseTaxonomyTerm:
    parent = await db.get(CourseTaxonomyTerm, parent_id)
    if parent is None or parent.taxonomy_type != taxonomy_type:
        raise ValidationAppError(
            "Parent non trovato nella stessa tassonomia.",
            code="taxonomy_parent_invalid",
        )
    if parent.parent_id is not None:
        # 2 livelli max: vietato un terzo livello.
        raise ValidationAppError(
            "La gerarchia ammette solo 2 livelli.",
            code="taxonomy_max_depth",
        )
    return parent


async def _next_sort_order(
    db: AsyncSession, taxonomy_type: TaxonomyType, parent_id: uuid.UUID | None
) -> int:
    res = await db.execute(
        select(func.coalesce(func.max(CourseTaxonomyTerm.sort_order), -1)).where(
            CourseTaxonomyTerm.taxonomy_type == taxonomy_type,
            CourseTaxonomyTerm.parent_id.is_(parent_id)
            if parent_id is None
            else CourseTaxonomyTerm.parent_id == parent_id,
        )
    )
    return int(res.scalar_one()) + 1


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


async def create_term(
    db: AsyncSession,
    *,
    taxonomy_type: TaxonomyType,
    payload: TaxonomyTermCreate,
    actor_id: uuid.UUID,
) -> CourseTaxonomyTerm:
    if payload.parent_id is not None:
        if taxonomy_type not in HIERARCHICAL_TYPES:
            raise ValidationAppError(
                "Questa tassonomia è flat: parent_id non ammesso.",
                code="taxonomy_not_hierarchical",
            )
        await _get_parent(db, taxonomy_type, payload.parent_id)

    sort_order = (
        payload.sort_order
        if payload.sort_order is not None
        else await _next_sort_order(db, taxonomy_type, payload.parent_id)
    )
    term = CourseTaxonomyTerm(
        taxonomy_type=taxonomy_type,
        parent_id=payload.parent_id,
        slug=payload.slug,
        sort_order=sort_order,
        is_active=payload.is_active,
        labels=payload.labels,
        descriptions=payload.descriptions,
    )
    db.add(term)
    await db.flush()
    await db.refresh(term)
    await write_audit(
        db,
        action="course_taxonomy.create",
        actor_user_id=actor_id,
        target_type="course_taxonomy_term",
        target_id=str(term.id),
        metadata={
            "taxonomy_type": taxonomy_type,
            "slug": term.slug,
            "parent_id": str(term.parent_id) if term.parent_id else None,
        },
    )
    return term


async def update_term(
    db: AsyncSession,
    *,
    term: CourseTaxonomyTerm,
    payload: TaxonomyTermUpdate,
    actor_id: uuid.UUID,
) -> CourseTaxonomyTerm:
    diff: dict[str, object] = {}

    if payload.unset_parent:
        if term.parent_id is not None:
            diff["parent_id"] = {"old": str(term.parent_id), "new": None}
        term.parent_id = None
    elif payload.parent_id is not None:
        if term.taxonomy_type not in HIERARCHICAL_TYPES:
            raise ValidationAppError(
                "Questa tassonomia è flat: parent_id non ammesso.",
                code="taxonomy_not_hierarchical",
            )
        if payload.parent_id == term.id:
            raise ValidationAppError(
                "Un termine non può essere parent di se stesso.",
                code="taxonomy_self_parent",
            )
        # Vietato spostare un parent (con figli) sotto un altro: rispetta
        # la profondità massima a 2 livelli.
        children_count = (
            await db.execute(
                select(func.count(CourseTaxonomyTerm.id)).where(
                    CourseTaxonomyTerm.parent_id == term.id
                )
            )
        ).scalar_one()
        if int(children_count) > 0:
            raise ValidationAppError(
                "Il termine ha figli: non può essere spostato sotto un altro parent.",
                code="taxonomy_has_children",
            )
        await _get_parent(db, term.taxonomy_type, payload.parent_id)
        if term.parent_id != payload.parent_id:
            diff["parent_id"] = {
                "old": str(term.parent_id) if term.parent_id else None,
                "new": str(payload.parent_id),
            }
        term.parent_id = payload.parent_id

    if payload.sort_order is not None and payload.sort_order != term.sort_order:
        diff["sort_order"] = {"old": term.sort_order, "new": payload.sort_order}
        term.sort_order = payload.sort_order

    if payload.is_active is not None and payload.is_active != term.is_active:
        diff["is_active"] = {"old": term.is_active, "new": payload.is_active}
        term.is_active = payload.is_active

    if payload.labels is not None:
        if payload.labels != (term.labels or {}):
            diff["labels_changed_keys"] = sorted(
                set(payload.labels.keys())
                ^ set((term.labels or {}).keys())
                | {
                    k
                    for k, v in payload.labels.items()
                    if (term.labels or {}).get(k) != v
                }
            )
        term.labels = payload.labels
        flag_modified(term, "labels")

    if payload.descriptions is not None:
        existing = term.descriptions or {}
        if payload.descriptions != existing:
            diff["descriptions_changed"] = True
        term.descriptions = payload.descriptions or None
        flag_modified(term, "descriptions")

    await db.flush()
    await db.refresh(term)
    if diff:
        await write_audit(
            db,
            action="course_taxonomy.update",
            actor_user_id=actor_id,
            target_type="course_taxonomy_term",
            target_id=str(term.id),
            metadata={"taxonomy_type": term.taxonomy_type, "diff": diff},
        )
    return term


async def delete_term(
    db: AsyncSession,
    *,
    term: CourseTaxonomyTerm,
    actor_id: uuid.UUID,
) -> None:
    children_count = (
        await db.execute(
            select(func.count(CourseTaxonomyTerm.id)).where(
                CourseTaxonomyTerm.parent_id == term.id
            )
        )
    ).scalar_one()
    term_id = term.id
    taxonomy_type = term.taxonomy_type
    await db.delete(term)
    await db.flush()
    await write_audit(
        db,
        action="course_taxonomy.delete",
        actor_user_id=actor_id,
        target_type="course_taxonomy_term",
        target_id=str(term_id),
        metadata={
            "taxonomy_type": taxonomy_type,
            "cascade_children": int(children_count),
        },
    )


async def move_term(
    db: AsyncSession,
    *,
    term: CourseTaxonomyTerm,
    direction: Literal["up", "down"],
    actor_id: uuid.UUID,
) -> CourseTaxonomyTerm:
    """Scambia il sort_order con il sibling adiacente nello stesso parent."""
    siblings_q = select(CourseTaxonomyTerm).where(
        CourseTaxonomyTerm.taxonomy_type == term.taxonomy_type,
    )
    if term.parent_id is None:
        siblings_q = siblings_q.where(CourseTaxonomyTerm.parent_id.is_(None))
    else:
        siblings_q = siblings_q.where(
            CourseTaxonomyTerm.parent_id == term.parent_id
        )
    siblings = list(
        (
            await db.execute(siblings_q.order_by(CourseTaxonomyTerm.sort_order.asc()))
        ).scalars().all()
    )
    try:
        idx = next(i for i, s in enumerate(siblings) if s.id == term.id)
    except StopIteration as exc:
        raise NotFoundError(
            "Sibling non trovato.", code="taxonomy_sibling_missing"
        ) from exc
    swap_idx = idx - 1 if direction == "up" else idx + 1
    if swap_idx < 0 or swap_idx >= len(siblings):
        # Già primo/ultimo: no-op.
        return term
    other = siblings[swap_idx]
    term.sort_order, other.sort_order = other.sort_order, term.sort_order
    await db.flush()
    await db.refresh(term)
    await write_audit(
        db,
        action="course_taxonomy.move",
        actor_user_id=actor_id,
        target_type="course_taxonomy_term",
        target_id=str(term.id),
        metadata={"direction": direction, "swapped_with": str(other.id)},
    )
    return term


# ---------------------------------------------------------------------------
# AI auto-translate
# ---------------------------------------------------------------------------


async def _active_languages(db: AsyncSession) -> list[Language]:
    res = await db.execute(
        select(Language)
        .where(Language.is_active.is_(True))
        .order_by(Language.code.asc())
    )
    return list(res.scalars().all())


async def auto_translate_term(
    db: AsyncSession,
    *,
    term: CourseTaxonomyTerm,
    actor_id: uuid.UUID,
) -> dict[str, object]:
    """Per ogni lingua attiva mancante in `term.labels` (e descriptions),
    genera la traduzione via OpenAI a partire dall'IT canonico.

    Se l'IT non è presente, ripiega sul primo valore di `labels` non vuoto
    (e idem per `descriptions`). La chiamata è no-op se nessuna lingua
    risulta mancante.
    """
    languages = await _active_languages(db)
    labels = dict(term.labels or {})
    descriptions = dict(term.descriptions or {}) if term.descriptions else {}

    # Source per labels e descriptions: prima tenta IT, poi qualunque
    # valore non vuoto già presente.
    label_source_code, label_source_text = _pick_source(labels)
    desc_source_code, desc_source_text = _pick_source(descriptions)

    translated_labels: dict[str, str] = {}
    translated_descriptions: dict[str, str] = {}
    skipped_labels: list[str] = []
    skipped_descriptions: list[str] = []
    errors: list[str] = []

    if label_source_text:
        for lang in languages:
            if lang.code == label_source_code:
                continue
            if labels.get(lang.code, "").strip():
                continue
            try:
                out = await translate_batch(
                    items={"label": label_source_text},
                    source_lang_code=label_source_code,
                    source_lang_name=DEFAULT_LANG_NAME
                    if label_source_code == DEFAULT_LANG_CODE
                    else label_source_code,
                    target_lang_code=lang.code,
                    target_lang_name=lang.name_native or lang.code,
                )
            except OpenAINotConfiguredError as exc:
                raise ValidationAppError(
                    exc.message, code="openai_not_configured"
                ) from exc
            except OpenAITranslateError as exc:
                errors.append(f"label[{lang.code}]: {exc}")
                continue
            value = (out.get("label") or "").strip()
            if value:
                translated_labels[lang.code] = value
            else:
                skipped_labels.append(lang.code)

    if desc_source_text:
        for lang in languages:
            if lang.code == desc_source_code:
                continue
            if descriptions.get(lang.code, "").strip():
                continue
            try:
                out = await translate_batch(
                    items={"description": desc_source_text},
                    source_lang_code=desc_source_code,
                    source_lang_name=DEFAULT_LANG_NAME
                    if desc_source_code == DEFAULT_LANG_CODE
                    else desc_source_code,
                    target_lang_code=lang.code,
                    target_lang_name=lang.name_native or lang.code,
                )
            except OpenAINotConfiguredError as exc:
                raise ValidationAppError(
                    exc.message, code="openai_not_configured"
                ) from exc
            except OpenAITranslateError as exc:
                errors.append(f"description[{lang.code}]: {exc}")
                continue
            value = (out.get("description") or "").strip()
            if value:
                translated_descriptions[lang.code] = value
            else:
                skipped_descriptions.append(lang.code)

    if translated_labels:
        labels.update(translated_labels)
        term.labels = labels
        flag_modified(term, "labels")
    if translated_descriptions:
        descriptions.update(translated_descriptions)
        term.descriptions = descriptions or None
        flag_modified(term, "descriptions")
    if translated_labels or translated_descriptions:
        await db.flush()
        await db.refresh(term)

    await write_audit(
        db,
        action="course_taxonomy.auto_translate",
        actor_user_id=actor_id,
        target_type="course_taxonomy_term",
        target_id=str(term.id),
        metadata={
            "taxonomy_type": term.taxonomy_type,
            "translated_labels": len(translated_labels),
            "translated_descriptions": len(translated_descriptions),
            "skipped_labels": skipped_labels,
            "skipped_descriptions": skipped_descriptions,
            "errors": errors[:5],
        },
    )

    return {
        "term_id": term.id,
        "translated_label_langs": sorted(translated_labels.keys()),
        "translated_description_langs": sorted(translated_descriptions.keys()),
        "skipped_label_langs": skipped_labels,
        "skipped_description_langs": skipped_descriptions,
        "errors": errors,
    }


def _pick_source(values: dict[str, str]) -> tuple[str, str]:
    """Sceglie la lingua sorgente: IT se presente, altrimenti la prima
    chiave con valore non vuoto. Ritorna ('', '') se non c'è nulla."""
    it_value = (values.get(DEFAULT_LANG_CODE) or "").strip()
    if it_value:
        return DEFAULT_LANG_CODE, it_value
    for code, text in values.items():
        text = (text or "").strip()
        if text:
            return code, text
    return "", ""


async def bulk_auto_translate_taxonomy(
    db: AsyncSession,
    *,
    taxonomy_type: TaxonomyType,
    actor_id: uuid.UUID,
) -> dict[str, object]:
    """Traduce in bulk tutti i termini di una tassonomia in tutte le lingue
    attive. Usa l'IT come sorgente: i termini privi di label IT non vengono
    toccati (l'admin deve compilare almeno l'IT).

    Strategia: una chiamata `translate_batch` per lingua per labels (e una
    per descriptions se presenti), passando come item-key l'UUID del termine.
    Questo riduce le chiamate da N_terms × N_lang a ~N_lang.
    """
    terms = await list_terms(db, taxonomy_type)
    languages = await _active_languages(db)

    label_sources: dict[uuid.UUID, str] = {}
    desc_sources: dict[uuid.UUID, str] = {}
    for term in terms:
        it_label = ((term.labels or {}).get(DEFAULT_LANG_CODE) or "").strip()
        if it_label:
            label_sources[term.id] = it_label
        it_desc = (
            (term.descriptions or {}).get(DEFAULT_LANG_CODE) or ""
        ).strip()
        if it_desc:
            desc_sources[term.id] = it_desc

    term_by_id = {t.id: t for t in terms}

    translated_labels = 0
    translated_descriptions = 0
    languages_processed: list[str] = []
    errors: list[str] = []

    async def _translate_into(
        lang: Language,
        items: dict[str, str],
        kind: Literal["label", "description"],
    ) -> int:
        if not items:
            return 0
        try:
            out = await translate_batch(
                items=items,
                source_lang_code=DEFAULT_LANG_CODE,
                source_lang_name=DEFAULT_LANG_NAME,
                target_lang_code=lang.code,
                target_lang_name=lang.name_native or lang.code,
            )
        except OpenAINotConfiguredError as exc:
            raise ValidationAppError(
                exc.message, code="openai_not_configured"
            ) from exc
        except OpenAITranslateError as exc:
            errors.append(f"{kind}[{lang.code}]: {exc}")
            return 0
        applied = 0
        for tid_str, value in out.items():
            if not value or not value.strip():
                continue
            try:
                tid = uuid.UUID(tid_str)
            except ValueError:
                continue
            term = term_by_id.get(tid)
            if term is None:
                continue
            text = value.strip()
            if kind == "label":
                new_labels = dict(term.labels or {})
                new_labels[lang.code] = text
                term.labels = new_labels
                flag_modified(term, "labels")
            else:
                new_desc = dict(term.descriptions or {})
                new_desc[lang.code] = text
                term.descriptions = new_desc
                flag_modified(term, "descriptions")
            applied += 1
        return applied

    for lang in languages:
        if lang.code == DEFAULT_LANG_CODE:
            continue
        languages_processed.append(lang.code)

        items_labels: dict[str, str] = {}
        for tid, it_text in label_sources.items():
            term = term_by_id[tid]
            existing = ((term.labels or {}).get(lang.code) or "").strip()
            if existing:
                continue
            items_labels[str(tid)] = it_text
        translated_labels += await _translate_into(lang, items_labels, "label")

        items_desc: dict[str, str] = {}
        for tid, it_text in desc_sources.items():
            term = term_by_id[tid]
            existing = (
                (term.descriptions or {}).get(lang.code) or ""
            ).strip()
            if existing:
                continue
            items_desc[str(tid)] = it_text
        translated_descriptions += await _translate_into(
            lang, items_desc, "description"
        )

    if translated_labels or translated_descriptions:
        await db.flush()

    await write_audit(
        db,
        action="course_taxonomy.bulk_auto_translate",
        actor_user_id=actor_id,
        target_type="course_taxonomy",
        target_id=taxonomy_type,
        metadata={
            "terms_total": len(terms),
            "languages_processed": languages_processed,
            "translated_labels": translated_labels,
            "translated_descriptions": translated_descriptions,
            "errors": errors[:10],
        },
    )

    return {
        "taxonomy_type": taxonomy_type,
        "terms_total": len(terms),
        "languages_processed": languages_processed,
        "translated_labels": translated_labels,
        "translated_descriptions": translated_descriptions,
        "errors": errors,
    }
