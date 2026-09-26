"""CRUD manuale dei campi Fase 3 (contenuti) di una lezione.

L'AI produce il contenuto via worker (`course_lesson_content_worker`),
ma il docente può raffinare manualmente il `content_raw` finché la
lezione è in stato `ready` o `approved`.

Edit non degrada lo stato (resta `approved` se era `approved`): è una
scelta esplicita del docente. Le validazioni hard di §6.4 sono
allentate per l'edit manuale (warning soft via log) per permettere
correzioni granulari.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import Any, NoReturn

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.errors import ConflictError, ValidationAppError
from app.core.logging import get_logger
from app.models.course import Course
from app.models.course_document import CourseDocument
from app.models.course_document_figure import CourseDocumentFigure
from app.models.course_lesson import CourseLesson
from app.schemas.course_lesson_content import (
    SOURCE_FIGURE_FORMAT,
    LessonAssessmentUpdateInput,
    LessonContentUpdateInput,
)
from app.services import remote_storage
from app.services.document_figures_service import figure_lesson_uses
from app.services.figure_render_service import validate_visual_assets_or_raise
from app.services.source_figure_catalog import license_policy_for, reuse_cap, unsuitable_reason
from app.services.source_figure_policy import figure_visibility
from app.services.source_figure_service import source_figure_uuid

log = get_logger("app.course_lesson_content_crud")


# Stati della lezione da cui è ammesso l'edit manuale del content_raw.
EDITABLE_LESSON_STATUSES = ("ready", "approved")


def _ensure_editable(lesson: CourseLesson) -> None:
    """Solleva ConflictError se la lezione non è in `ready`/`approved`."""
    if lesson.content_status not in EDITABLE_LESSON_STATUSES:
        raise ConflictError(
            f"Contenuto lezione non editabile: stato attuale "
            f"`{lesson.content_status}` (richiesto `ready` o `approved`).",
            code="lesson_content_not_editable",
        )


def _dump_models(items: list[Any] | None) -> list[dict[str, Any]] | None:
    if items is None:
        return None
    return [i.model_dump() if hasattr(i, "model_dump") else i for i in items]


def _validate_consistency(
    *,
    payload: LessonContentUpdateInput,
    current_raw: dict[str, Any],
) -> None:
    """Validazione minima di consistenza per l'edit manuale.

    Hard fail solo per duplicati di ID (rotture strutturali). Le
    coperture e i ref orfani sono solo warning via log (l'utente sa
    cosa sta facendo).
    """
    sections = (
        _dump_models(payload.sections)
        if payload.sections is not None
        else current_raw.get("sections", [])
    )
    section_ids = [s.get("section_id") for s in sections if isinstance(s, dict)]
    if any(not sid or not str(sid).strip() for sid in section_ids):
        raise ConflictError(
            "Ogni sezione deve avere un `section_id` non vuoto.",
            code="lesson_content_section_id_required",
        )
    if len(set(section_ids)) != len(section_ids):
        raise ConflictError(
            "I `section_id` devono essere univoci.",
            code="lesson_content_duplicate_section_id",
        )

    visual_assets = (
        _dump_models(payload.visual_assets)
        if payload.visual_assets is not None
        else current_raw.get("visual_assets", [])
    )
    visual_ids = [a.get("asset_id") for a in visual_assets if isinstance(a, dict)]
    if len(set(visual_ids)) != len(visual_ids):
        raise ConflictError(
            "Gli `asset_id` dei visual_assets devono essere univoci.",
            code="lesson_content_duplicate_visual_asset_id",
        )

    tables = (
        _dump_models(payload.tables)
        if payload.tables is not None
        else current_raw.get("tables", [])
    )
    table_ids = [t.get("table_id") for t in tables if isinstance(t, dict)]
    if len(set(table_ids)) != len(table_ids):
        raise ConflictError(
            "I `table_id` devono essere univoci.",
            code="lesson_content_duplicate_table_id",
        )

    equations = (
        _dump_models(payload.equations)
        if payload.equations is not None
        else current_raw.get("equations", [])
    )
    eq_ids = [e.get("equation_id") for e in equations if isinstance(e, dict)]
    if len(set(eq_ids)) != len(eq_ids):
        raise ConflictError(
            "Gli `equation_id` devono essere univoci.",
            code="lesson_content_duplicate_equation_id",
        )

    examples = (
        _dump_models(payload.examples)
        if payload.examples is not None
        else current_raw.get("examples", [])
    )
    ex_ids = [ex.get("example_id") for ex in examples if isinstance(ex, dict)]
    if len(set(ex_ids)) != len(ex_ids):
        raise ConflictError(
            "Gli `example_id` devono essere univoci.",
            code="lesson_content_duplicate_example_id",
        )


def _extract_image_asset_paths(
    visual_assets: list[Any] | None,
) -> set[str]:
    """Ritorna i `content` (path relativi) degli asset con `format=image`."""
    paths: set[str] = set()
    for a in visual_assets or []:
        if not isinstance(a, dict):
            continue
        if a.get("format") == "image":
            content = a.get("content")
            if isinstance(content, str) and content.strip():
                paths.add(content.strip())
    return paths


def _cleanup_removed_image_assets(
    *,
    old_visual_assets: list[Any] | None,
    new_visual_assets: list[Any] | None,
    course_id: uuid.UUID,
) -> None:
    """Elimina dal filesystem i file degli asset immagine rimossi.

    Best-effort: log warning se il file non esiste o se l'unlink fallisce,
    senza propagare. Safety check: il path deve essere sotto
    `lesson_assets/{course_id}/`.
    """
    old_paths = _extract_image_asset_paths(old_visual_assets)
    new_paths = _extract_image_asset_paths(new_visual_assets)
    to_remove = old_paths - new_paths
    if not to_remove:
        return
    expected_prefix = f"lesson_assets/{course_id}/"
    storage = remote_storage.get_storage()
    for rel in to_remove:
        normalized = rel.removeprefix("/uploads/").lstrip("/")
        if not normalized.startswith(expected_prefix):
            # Path fuori dal corso → non tocco (potrebbe essere un asset
            # di un altro corso, un path malformato, o un valore legacy).
            log.warning(
                "lesson_asset_cleanup_skipped_outside_course",
                path=rel,
                course_id=str(course_id),
            )
            continue
        if any(p in {"", ".", ".."} for p in normalized.split("/")):
            log.warning("lesson_asset_cleanup_skipped_invalid", path=rel)
            continue
        try:
            storage.delete(remote_storage.uploads_key(normalized))
            log.info("lesson_asset_cleanup_deleted", path=rel)
        except remote_storage.StorageError as exc:
            log.warning(
                "lesson_asset_cleanup_unlink_failed",
                path=rel,
                error=str(exc),
            )


async def _guard_source_figures(
    db: AsyncSession,
    *,
    course: Course,
    lesson_id: uuid.UUID,
    assets: list[dict[str, Any]],
    previous: list[Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Figure di fonte nel PATCH della dispensa; restituisce gli asset con il
    riferimento in forma canonica (UUID minuscolo con trattini), la stessa
    che confrontano il resolver e il frontend, e le figure NUOVE inserite
    oltre il tetto di riuso (per l'audit).

    - Un asset non cambia famiglia: una figura di fonte resta tale e un
      asset generato non diventa di fonte (`source_figure_format_locked`).
    - Una figura di fonte NUOVA (UUID che la dispensa non conteneva) deve
      essere del corso (`source_figure_not_in_course`) e proponibile adesso:
      modo select del predicato con la politica di licenza effettiva e gli
      stessi filtri del catalogo (`source_figure_not_available`).
    - Una figura già collocata passa sempre, anche rinominata o spostata e
      anche se nel frattempo la politica del documento è cambiata (U1, non
      retroattivo).
    - Mai due volte la stessa figura nella stessa lezione: un doppione NUOVO
      dà `source_figure_duplicate_in_lesson`; i doppioni già salvati passano.
    - Il tetto di riuso (`FIGURE_SOURCE_MAX_LESSONS_PER_FIGURE`) non blocca il
      docente: una figura nuova già in K altre lezioni passa e finisce
      nell'elenco restituito, che il chiamante registra nell'audit.

    Gli errori portano `meta.errors[{loc: ["visual_assets", i, "content"]}]`
    come quelli dei renderer, così il frontend li mostra sulla card.
    """
    before = {str(a.get("asset_id")): a for a in previous or [] if isinstance(a, dict)}
    placed = {
        fid
        for a in previous or []
        if isinstance(a, dict) and (fid := source_figure_uuid(a)) is not None
    }
    before_counts = Counter(
        fid
        for a in previous or []
        if isinstance(a, dict) and (fid := source_figure_uuid(a)) is not None
    )
    uses: dict[uuid.UUID, list[str]] | None = None
    over_cap: list[dict[str, Any]] = []
    policy: str | None = None
    out: list[dict[str, Any]] = []

    def invalid(message: str, code: str, index: int, asset_id: str, **extra: Any) -> NoReturn:
        error = {
            "loc": ["visual_assets", index, "content"],
            "asset_id": asset_id,
            "format": SOURCE_FIGURE_FORMAT,
            "msg": message,
            "type": code,
        }
        raise ValidationAppError(
            message,
            code=code,
            meta={"asset_id": asset_id, "index": index, **extra, "errors": [error]},
        )

    # Doppioni: una figura che compare più volte di quante ne avesse già la
    # lezione. Il 422 va sul primo asset che la porta di NUOVO (non su quello
    # già salvato, anche se lo precede nel payload), così il frontend mostra
    # l'errore sulla card giusta.
    occurrences: dict[uuid.UUID, list[tuple[int, str]]] = {}
    for index, asset in enumerate(assets):
        if asset.get("format") != SOURCE_FIGURE_FORMAT:
            continue
        fid = source_figure_uuid(asset)
        if fid is not None:
            occurrences.setdefault(fid, []).append((index, str(asset.get("asset_id") or "")))
    blame: dict[uuid.UUID, int] = {}
    for fid, found in occurrences.items():
        if len(found) > max(1, before_counts[fid]):
            fresh = [i for i, aid in found if source_figure_uuid(before.get(aid)) != fid]
            blame[fid] = fresh[0] if fresh else found[-1][0]

    for index, asset in enumerate(assets):
        asset_id = str(asset.get("asset_id") or "")
        old = before.get(asset_id)
        is_source = asset.get("format") == SOURCE_FIGURE_FORMAT
        out.append(asset)
        if old is not None and (old.get("format") == SOURCE_FIGURE_FORMAT) != is_source:
            invalid(
                "Una figura di fonte non cambia formato e un asset generato non "
                "diventa una figura di fonte.",
                "source_figure_format_locked",
                index,
                asset_id,
            )
        if not is_source:
            continue
        figure_id = source_figure_uuid(asset)
        if figure_id is not None:
            out[-1] = {**asset, "content": str(figure_id)}
            if blame.get(figure_id) == index:
                invalid(
                    "La stessa figura di fonte compare già in questa lezione.",
                    "source_figure_duplicate_in_lesson",
                    index,
                    asset_id,
                )
        if figure_id is not None and figure_id in placed:
            continue
        figure = None
        if figure_id is not None:
            figure = (
                await db.execute(
                    select(CourseDocumentFigure).where(
                        CourseDocumentFigure.id == figure_id,
                        CourseDocumentFigure.course_id == course.id,
                    )
                )
            ).scalar_one_or_none()
        if figure is None:
            invalid(
                "La figura di fonte non appartiene a questo corso.",
                "source_figure_not_in_course",
                index,
                asset_id,
            )
        doc = await db.get(CourseDocument, figure.document_id) if figure.document_id else None
        if policy is None:
            policy = await license_policy_for(db, course)
        visibility = figure_visibility(
            figure, doc, course_id=course.id, license_policy=policy, mode="select"
        )
        reason = visibility.reason if not visibility.renderable else unsuitable_reason(figure)
        if reason is not None:
            invalid(
                "La figura di fonte non si può inserire (politica del documento, "
                "licenza, esclusione, qualità o file).",
                "source_figure_not_available",
                index,
                asset_id,
                reason=reason,
            )
        if uses is None:
            uses = await figure_lesson_uses(db, course.id, except_lesson_id=lesson_id)
        used_in = uses.get(figure.id, [])
        if len(used_in) >= reuse_cap():
            over_cap.append(
                {"figure_id": str(figure.id), "used_in": sorted(used_in), "cap": reuse_cap()}
            )
    return out, over_cap


def _prune_figure_review(
    review: Any, assets: list[Any], previous: list[Any]
) -> dict[str, Any] | None:
    """Il CRUD può solo POTARE il verdetto del revisore (PROMPT 19), mai
    aggiungerne: via le figure di fonte tolte o con il riferimento
    cambiato e le coppie verso asset che non ci sono più."""
    if not isinstance(review, dict) or not isinstance(review.get("figures"), dict):
        return None

    def contents(items: list[Any]) -> dict[str, str]:
        return {
            str(a.get("asset_id")): str(a.get("content") or "").strip()
            for a in items
            if isinstance(a, dict)
        }

    current = contents(assets)
    before = contents(previous)
    figures: dict[str, Any] = {}
    for asset_id, verdict in review["figures"].items():
        if asset_id not in current or not isinstance(verdict, dict):
            continue
        if before.get(asset_id) != current[asset_id]:
            continue
        pairs = [
            p
            for p in verdict.get("pairs") or []
            if isinstance(p, dict) and p.get("other") in current
        ]
        figures[asset_id] = {**verdict, "pairs": pairs}
    if not figures:
        return None
    return {**review, "figures": figures}


async def update_lesson_content(
    db: AsyncSession,
    *,
    course: Course,
    lesson: CourseLesson,
    payload: LessonContentUpdateInput,
    actor_id: uuid.UUID,
) -> Course:
    """Patch parziale del `content_raw` della lezione.

    Edit non degrada lo stato (`approved` resta `approved`).
    """
    _ensure_editable(lesson)
    current_raw: dict[str, Any] = dict(lesson.content_raw or {})

    _validate_consistency(payload=payload, current_raw=current_raw)

    # Gate delle figure (A15): SOLO gli asset del payload con (format,
    # content) diversi da quelli già salvati vengono validati offline dal
    # registro dei renderer; un edit del testo non rivalida i diagrammi
    # legacy già in DB. Errori → 422 con `meta.errors` per asset.
    over_cap: list[dict[str, Any]] = []
    if payload.visual_assets is not None:
        await validate_visual_assets_or_raise(
            [a.model_dump() for a in payload.visual_assets],
            previous=current_raw.get("visual_assets"),
            loc_root="visual_assets",
            code="lesson_content_invalid_visual_asset",
        )
        guarded_assets, over_cap = await _guard_source_figures(
            db,
            course=course,
            lesson_id=lesson.id,
            assets=[a.model_dump() for a in payload.visual_assets],
            previous=current_raw.get("visual_assets"),
        )

    # Snapshot dei visual_assets attuali PRIMA dell'update — serve per il
    # cleanup file (asset rimossi con format=image → unlink dopo commit).
    old_visual_assets: list[Any] = list(current_raw.get("visual_assets") or [])

    changed: dict[str, int | str] = {}

    if payload.introduction is not None:
        current_raw["introduction"] = payload.introduction
        changed["introduction"] = len(payload.introduction)
    if payload.sections is not None:
        sections_dump = [s.model_dump() for s in payload.sections]
        current_raw["sections"] = sections_dump
        changed["sections"] = len(sections_dump)
    if payload.summary is not None:
        current_raw["summary"] = payload.summary
        changed["summary"] = len(payload.summary)
    if payload.key_takeaways is not None:
        current_raw["key_takeaways"] = list(payload.key_takeaways)
        changed["key_takeaways"] = len(payload.key_takeaways)
    if payload.visual_assets is not None:
        current_raw["visual_assets"] = guarded_assets
        changed["visual_assets"] = len(payload.visual_assets)
    if payload.tables is not None:
        current_raw["tables"] = [t.model_dump() for t in payload.tables]
        changed["tables"] = len(payload.tables)
    if payload.equations is not None:
        current_raw["equations"] = [e.model_dump() for e in payload.equations]
        changed["equations"] = len(payload.equations)
    if payload.examples is not None:
        current_raw["examples"] = [ex.model_dump() for ex in payload.examples]
        changed["examples"] = len(payload.examples)
    if payload.references is not None:
        current_raw["references"] = [r.model_dump() for r in payload.references]
        changed["references"] = len(payload.references)
    if payload.coverage_check is not None:
        current_raw["coverage_check"] = payload.coverage_check.model_dump()
        changed["coverage_check"] = "updated"

    if not changed:
        return course

    # Mantieni gli ID e i flag invariabili (lesson_id, lesson_title,
    # is_introductory, estimated_word_count). Se l'AI li ha già scritti,
    # restano. Se mancano (caso anomalo), seedaali dalla lezione.
    current_raw.setdefault("lesson_id", lesson.lesson_code)
    current_raw.setdefault("lesson_title", lesson.title)
    current_raw.setdefault("is_introductory", bool(lesson.is_introductory))
    current_raw.setdefault("estimated_word_count", 0)

    lesson.content_raw = current_raw
    if "visual_assets" in changed and lesson.content_figure_review is not None:
        lesson.content_figure_review = _prune_figure_review(
            lesson.content_figure_review,
            current_raw.get("visual_assets") or [],
            old_visual_assets,
        )
    # Stale-detection: marca il content come modificato manualmente. Il
    # FE confronta con `pdf_generated_at` per dedurre se il PDF
    # downstream è stale. I worker AI di Fase 3 NON toccano questo campo.
    lesson.content_modified_at = datetime.now(UTC)

    await write_audit(
        db,
        action="course.lesson.content.updated",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course_lesson",
        target_id=str(lesson.id),
        metadata={
            "course_id": str(course.id),
            "lesson_code": lesson.lesson_code,
            "fields": changed,
        },
    )
    # Il tetto di riuso non blocca il docente: l'inserimento oltre il tetto
    # resta e si registra nell'audit (l'avviso nell'editor arriva con il
    # badge «già usata in», WP9).
    for item in over_cap:
        await write_audit(
            db,
            action="course.lesson.content.source_figure_over_cap",
            actor_user_id=actor_id,
            organization_id=course.organization_id,
            target_type="course_lesson",
            target_id=str(lesson.id),
            metadata={"course_id": str(course.id), "lesson_code": lesson.lesson_code, **item},
        )
    await db.commit()

    # Cleanup file orfani dopo il commit: se l'update ha sostituito o
    # rimosso asset di tipo `image`, eliminiamo i file dal filesystem.
    # Eseguito dopo il commit per evitare di perdere file in caso di
    # rollback. Best-effort, log warning se qualcosa fallisce.
    if "visual_assets" in changed:
        _cleanup_removed_image_assets(
            old_visual_assets=old_visual_assets,
            new_visual_assets=current_raw.get("visual_assets"),
            course_id=course.id,
        )

    from app.services import course_lesson_content_service

    return await course_lesson_content_service._refresh_full(db, course)


async def update_lesson_assessment(
    db: AsyncSession,
    *,
    course: Course,
    lesson: CourseLesson,
    payload: LessonAssessmentUpdateInput,
    actor_id: uuid.UUID,
) -> Course:
    """Patch parziale della verifica delle competenze — `content_raw` di
    una lezione `is_assessment`. Edit non degrada lo stato."""
    _ensure_editable(lesson)
    current_raw: dict[str, Any] = dict(lesson.content_raw or {})

    mc = (
        [q.model_dump() for q in payload.multiple_choice_questions]
        if payload.multiple_choice_questions is not None
        else list(current_raw.get("multiple_choice_questions") or [])
    )
    open_q = (
        [q.model_dump() for q in payload.open_questions]
        if payload.open_questions is not None
        else list(current_raw.get("open_questions") or [])
    )

    # Validazioni hard: question_id univoci, opzione corretta valida.
    question_ids = [q.get("question_id") for q in (mc + open_q) if isinstance(q, dict)]
    if any(not qid or not str(qid).strip() for qid in question_ids):
        raise ConflictError(
            "Ogni domanda deve avere un `question_id` non vuoto.",
            code="lesson_assessment_question_id_required",
        )
    if len(set(question_ids)) != len(question_ids):
        raise ConflictError(
            "I `question_id` devono essere univoci.",
            code="lesson_assessment_duplicate_question_id",
        )
    for q in mc:
        if not isinstance(q, dict):
            continue
        opt_ids = [o.get("option_id") for o in (q.get("options") or []) if isinstance(o, dict)]
        if len(opt_ids) < 2:
            raise ConflictError(
                f"La domanda {q.get('question_id')} deve avere almeno 2 opzioni.",
                code="lesson_assessment_too_few_options",
            )
        if len(set(opt_ids)) != len(opt_ids):
            raise ConflictError(
                f"Domanda {q.get('question_id')}: `option_id` duplicati.",
                code="lesson_assessment_duplicate_option_id",
            )
        if q.get("correct_option_id") not in opt_ids:
            raise ConflictError(
                f"Domanda {q.get('question_id')}: l'opzione corretta non "
                f"corrisponde ad alcuna opzione.",
                code="lesson_assessment_invalid_correct_option",
            )

    changed: dict[str, int] = {}
    if payload.multiple_choice_questions is not None:
        current_raw["multiple_choice_questions"] = mc
        changed["multiple_choice_questions"] = len(mc)
    if payload.open_questions is not None:
        current_raw["open_questions"] = open_q
        changed["open_questions"] = len(open_q)

    if not changed:
        return course

    current_raw.setdefault("lesson_id", lesson.lesson_code)
    current_raw.setdefault("lesson_title", lesson.title)
    current_raw["is_assessment"] = True

    lesson.content_raw = current_raw
    lesson.content_modified_at = datetime.now(UTC)

    await write_audit(
        db,
        action="course.lesson.content.updated",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course_lesson",
        target_id=str(lesson.id),
        metadata={
            "course_id": str(course.id),
            "lesson_code": lesson.lesson_code,
            "is_assessment": True,
            "fields": changed,
        },
    )
    await db.commit()

    from app.services import course_lesson_content_service

    return await course_lesson_content_service._refresh_full(db, course)


FIGURE_NEED_LINK_STATES = ("dismissed", "linked")


async def update_figure_need_link(
    db: AsyncSession,
    *,
    course: Course,
    lesson: CourseLesson,
    need_id: str,
    state: str | None,
    asset_id: str | None,
    actor_id: uuid.UUID,
) -> Course:
    """Collegamento manuale del docente a un fabbisogno di figura (doc 18
    §23.7): «Non serve» (`dismissed`), una figura della lezione collegata
    (`linked`, `asset_id` fra i `visual_assets`), oppure nessuno (`None`,
    si torna allo stato calcolato). Non tocca `content_raw` e quindi non
    segna la lezione come modificata."""
    from sqlalchemy.orm.attributes import flag_modified

    needs = lesson.figure_needs.get("needs") if isinstance(lesson.figure_needs, dict) else None
    known = {str(n.get("need_id")) for n in needs or [] if isinstance(n, dict)}
    if lesson.figure_needs_status != "ready" or need_id not in known:
        raise ValidationAppError(
            "Fabbisogno di figura sconosciuto per questa lezione.",
            code="figure_need_unknown",
            meta={"errors": [{"loc": ["path", "need_id"], "msg": "sconosciuto"}]},
        )
    if state is not None and state not in FIGURE_NEED_LINK_STATES:
        raise ValidationAppError(
            "Stato del collegamento non valido.",
            code="figure_need_state_invalid",
            meta={"errors": [{"loc": ["body", "state"], "msg": "non valido"}]},
        )
    entry: dict[str, Any] | None = None
    if state == "dismissed":
        entry = {"state": "dismissed"}
    elif state == "linked":
        assets = (lesson.content_raw or {}).get("visual_assets") or []
        ids = {str(a.get("asset_id")) for a in assets if isinstance(a, dict)}
        if not asset_id or asset_id not in ids:
            raise ValidationAppError(
                "La figura da collegare non è nella lezione.",
                code="figure_need_asset_unknown",
                meta={"errors": [{"loc": ["body", "asset_id"], "msg": "non nella lezione"}]},
            )
        entry = {"state": "linked", "asset_id": asset_id}
    links = dict(lesson.figure_need_links or {})
    if entry is None:
        links.pop(need_id, None)
    else:
        links[need_id] = entry
    lesson.figure_need_links = links or None
    flag_modified(lesson, "figure_need_links")
    await write_audit(
        db,
        action="course.lesson.figure_need.updated",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course_lesson",
        target_id=str(lesson.id),
        metadata={
            "course_id": str(course.id),
            "lesson_code": lesson.lesson_code,
            "need_id": need_id,
            "state": state,
            **({"asset_id": asset_id} if state == "linked" else {}),
        },
    )
    await db.commit()
    from app.services import course_lesson_content_service

    return await course_lesson_content_service._refresh_full(db, course)
