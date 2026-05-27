"""CRUD per il dominio Course (foundation).

Pattern:
- list filtrato per organization_id + ulteriore filtro per assegnatario se
  l'utente non ha `course:view_all` (membro vede solo i propri corsi).
- create con snapshot dei parametri di `OrganizationCourseSettings`
  (immutabili dopo creazione: cambi successivi ai parametri org NON si
  propagano).
- audit per ogni mutazione.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

from app.core.audit import write_audit
from app.core.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationAppError,
)
from app.core.permissions import P, R
from app.models.course import Course
from app.models.course_document import CourseDocument
from app.models.course_duplication_job import CourseDuplicationJob
from app.models.course_lesson import CourseLesson
from app.models.course_taxonomy import CourseTaxonomyTerm
from app.models.language import Language
from app.models.membership import Membership
from app.models.role import OrganizationRole
from app.models.user import User
from app.schemas.course import (
    CourseCreateInput,
    CourseUpdateInput,
    TaxonomyAssignments,
)
from app.services import file_service
from app.services.organization_course_settings_service import (
    get_or_create_settings,
)

# Mappa nome campo Pydantic → (taxonomy_type, model attribute) per
# validazione coerenza term_type.
TAXONOMY_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("categoria", "category", "categoria_term_id"),
    ("stile_insegnamento", "teaching_style", "stile_insegnamento_term_id"),
    ("profondita_contenuto", "content_depth", "profondita_contenuto_term_id"),
    ("ruolo_docente", "teacher_role", "ruolo_docente_term_id"),
    ("dimensione_pubblico", "audience_size", "dimensione_pubblico_term_id"),
    ("livello_conoscenza", "knowledge_level", "livello_conoscenza_term_id"),
    ("destinatari", "target_audience", "destinatari_term_id"),
    ("livello_eqf", "eqf_level", "livello_eqf_term_id"),
)


def _eager_options() -> list[Any]:
    """Opzioni di joinedload/selectinload per caricare il Course completo."""
    from app.models.course_module import CourseModule  # local import: avoid cycle

    return [
        selectinload(Course.assignee),
        selectinload(Course.created_by),
        selectinload(Course.documents),
        selectinload(Course.modules).selectinload(CourseModule.lessons),
        selectinload(Course.categoria),
        selectinload(Course.stile_insegnamento),
        selectinload(Course.profondita_contenuto),
        selectinload(Course.ruolo_docente),
        selectinload(Course.dimensione_pubblico),
        selectinload(Course.livello_conoscenza),
        selectinload(Course.destinatari),
        selectinload(Course.livello_eqf),
    ]


async def _validate_taxonomy_term(
    db: AsyncSession,
    *,
    term_id: uuid.UUID | None,
    expected_type: str,
    field_name: str,
) -> None:
    if term_id is None:
        return
    term = await db.get(CourseTaxonomyTerm, term_id)
    if term is None:
        raise ValidationAppError(
            f"Termine tassonomia non trovato per il campo '{field_name}'.",
            code="taxonomy_term_not_found",
        )
    if term.taxonomy_type != expected_type:
        raise ValidationAppError(
            f"Il campo '{field_name}' richiede un termine di tipo "
            f"'{expected_type}', ricevuto '{term.taxonomy_type}'.",
            code="taxonomy_type_mismatch",
        )


async def _validate_all_taxonomies(
    db: AsyncSession, *, taxonomies: TaxonomyAssignments
) -> None:
    for field_name, expected_type, _ in TAXONOMY_FIELDS:
        term_id = getattr(taxonomies, field_name)
        await _validate_taxonomy_term(
            db, term_id=term_id, expected_type=expected_type, field_name=field_name
        )


async def _validate_assignee(
    db: AsyncSession, *, organization_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    """Verifica che `user_id` sia membro attivo dell'organizzazione."""
    res = await db.execute(
        select(User, Membership)
        .join(Membership, Membership.user_id == User.id)
        .where(
            Membership.organization_id == organization_id,
            Membership.user_id == user_id,
        )
    )
    row = res.first()
    if row is None:
        raise ValidationAppError(
            "L'assegnatario deve essere un membro dell'organizzazione.",
            code="assignee_not_a_member",
        )
    user = row[0]
    if not user.is_active:
        raise ValidationAppError(
            "L'assegnatario non è un utente attivo.",
            code="assignee_inactive",
        )


async def _validate_language(
    db: AsyncSession, *, language_code: str
) -> None:
    lang = await db.get(Language, language_code)
    if lang is None or not lang.is_active:
        raise ValidationAppError(
            f"Lingua '{language_code}' non disponibile.",
            code="language_not_available",
        )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


async def list_courses(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    current_user: User,
    granted_permissions: set[str],
    page: int,
    page_size: int,
    q: str | None = None,
    status: str | None = None,
    assignee_user_id: uuid.UUID | None = None,
    language_code: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    updated_after: datetime | None = None,
    updated_before: datetime | None = None,
    sort_by: str = "updated_at",
    sort_dir: str = "desc",
) -> tuple[
    list[Course],
    int,
    dict[uuid.UUID, Any],
    dict[uuid.UUID, "CourseDuplicationJob"],
]:
    """List dei corsi paginata + filtrata + ordinata, con aggregazione
    delle lezioni per indicatori di completezza pipeline.

    Ritorna `(items, total, agg_map, duplication_jobs_map)`:
    - `agg_map[course_id]` → Row con `total/content_ready/slides_ready/
      videos_ready/avatar_videos_ready` (escluse le lezioni
      `is_assessment=True` dal denominatore). Se un corso non ha
      lezioni il suo id non è in `agg_map`: assenza = tutti i
      contatori a 0.
    - `duplication_jobs_map[target_course_id]` → `CourseDuplicationJob`
      attivo (status ∈ pending|processing) di cui il corso è target.
      Usato dalla UI per il badge "Duplicazione in corso XX%".
    """
    base_q = select(Course).where(Course.organization_id == organization_id)
    count_q = select(func.count(Course.id)).where(
        Course.organization_id == organization_id
    )

    # Filtro membro: chi NON ha course:view_all vede solo i corsi assegnati
    # a sé. Platform admin (resolve_permissions ritorna ALL) bypassa.
    if (
        not current_user.is_platform_admin
        and P.COURSE_VIEW_ALL not in granted_permissions
    ):
        base_q = base_q.where(Course.assignee_user_id == current_user.id)
        count_q = count_q.where(Course.assignee_user_id == current_user.id)

    if q:
        like = f"%{q.strip()}%"
        base_q = base_q.where(
            or_(Course.title.ilike(like), Course.objectives.ilike(like))
        )
        count_q = count_q.where(
            or_(Course.title.ilike(like), Course.objectives.ilike(like))
        )
    if status:
        base_q = base_q.where(Course.status == status)
        count_q = count_q.where(Course.status == status)
    if assignee_user_id:
        base_q = base_q.where(Course.assignee_user_id == assignee_user_id)
        count_q = count_q.where(Course.assignee_user_id == assignee_user_id)
    if language_code:
        base_q = base_q.where(Course.language_code == language_code)
        count_q = count_q.where(Course.language_code == language_code)
    if created_after is not None:
        base_q = base_q.where(Course.created_at >= created_after)
        count_q = count_q.where(Course.created_at >= created_after)
    if created_before is not None:
        base_q = base_q.where(Course.created_at <= created_before)
        count_q = count_q.where(Course.created_at <= created_before)
    if updated_after is not None:
        base_q = base_q.where(Course.updated_at >= updated_after)
        count_q = count_q.where(Course.updated_at >= updated_after)
    if updated_before is not None:
        base_q = base_q.where(Course.updated_at <= updated_before)
        count_q = count_q.where(Course.updated_at <= updated_before)

    # Ordinamento: solo created_at | updated_at, asc | desc. Tie-break su id
    # per stabilità della paginazione.
    sort_col = Course.created_at if sort_by == "created_at" else Course.updated_at
    sort_expr = sort_col.asc() if sort_dir == "asc" else sort_col.desc()

    base_q = (
        base_q.options(selectinload(Course.assignee))
        .order_by(sort_expr, Course.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = list((await db.execute(base_q)).scalars().all())
    total = int((await db.execute(count_q)).scalar_one())

    # Aggregazione lezioni per i corsi della pagina corrente. Una query
    # sola con `COUNT(*) FILTER (WHERE …)` (pattern Postgres standard).
    # Esclude `is_assessment=true` dal denominatore — vedi
    # `CourseListLessonsProgress` per i criteri esatti.
    agg_map: dict[uuid.UUID, Any] = {}
    if items:
        course_ids = [c.id for c in items]
        not_assessment = CourseLesson.is_assessment.is_(False)
        agg_q = (
            select(
                CourseLesson.course_id,
                func.count().filter(not_assessment).label("total"),
                func.count()
                .filter(
                    not_assessment,
                    CourseLesson.content_status.in_(["ready", "approved"]),
                )
                .label("content_ready"),
                func.count()
                .filter(
                    not_assessment,
                    CourseLesson.slides_status.in_(["ready", "approved"]),
                )
                .label("slides_ready"),
                func.count()
                .filter(
                    not_assessment,
                    CourseLesson.video_status == "ready",
                )
                .label("videos_ready"),
                func.count()
                .filter(
                    not_assessment,
                    CourseLesson.avatar_video_status == "ready",
                )
                .label("avatar_videos_ready"),
            )
            .where(CourseLesson.course_id.in_(course_ids))
            .group_by(CourseLesson.course_id)
        )
        agg_map = {row.course_id: row for row in (await db.execute(agg_q)).all()}

    # Job di duplicazione attivi (pending|processing) di cui i corsi
    # della pagina sono target. Usato dal FE per il badge.
    duplication_jobs_map: dict[uuid.UUID, CourseDuplicationJob] = {}
    if items:
        course_ids = [c.id for c in items]
        dup_q = select(CourseDuplicationJob).where(
            CourseDuplicationJob.target_course_id.in_(course_ids),
            CourseDuplicationJob.status.in_(("pending", "processing")),
        )
        for job in (await db.execute(dup_q)).scalars().all():
            if job.target_course_id is not None:
                duplication_jobs_map[job.target_course_id] = job

    return items, total, agg_map, duplication_jobs_map


async def get_course(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    course_id: uuid.UUID,
    current_user: User,
    granted_permissions: set[str],
) -> Course:
    q = (
        select(Course)
        .where(Course.id == course_id, Course.organization_id == organization_id)
        .options(*_eager_options())
    )
    course = (await db.execute(q)).scalar_one_or_none()
    if course is None:
        raise NotFoundError("Corso non trovato.", code="course_not_found")
    # Filtro membro: visibilità al di fuori dei propri corsi richiede
    # course:view_all (separato da course:edit dopo lo split del 2026-05-11).
    if (
        not current_user.is_platform_admin
        and P.COURSE_VIEW_ALL not in granted_permissions
        and course.assignee_user_id != current_user.id
    ):
        raise NotFoundError("Corso non trovato.", code="course_not_found")
    return course


async def _refresh_full(db: AsyncSession, course_id: uuid.UUID) -> Course:
    """Ricarica il Course con tutti gli eager-load applicati."""
    q = select(Course).where(Course.id == course_id).options(*_eager_options())
    fresh = (await db.execute(q)).scalar_one_or_none()
    if fresh is None:
        raise NotFoundError("Corso non trovato.", code="course_not_found")
    return fresh


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


async def create_course(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    payload: CourseCreateInput,
    current_user: User,
) -> Course:
    assignee_id = payload.assignee_user_id or current_user.id
    await _validate_assignee(
        db, organization_id=organization_id, user_id=assignee_id
    )
    await _validate_language(db, language_code=payload.language_code)
    await _validate_all_taxonomies(db, taxonomies=payload.taxonomies)

    # Snapshot dei parametri organizzazione.
    settings = await get_or_create_settings(db, organization_id=organization_id)
    modules_count = payload.cfu * settings.modules_per_cfu

    # Pulizia argomenti chiave: trim + dedup mantenendo l'ordine.
    seen: set[str] = set()
    cleaned_keywords: list[str] = []
    for k in payload.argomenti_chiave:
        kk = (k or "").strip()
        if not kk or kk in seen:
            continue
        seen.add(kk)
        cleaned_keywords.append(kk)

    cleaned_corso_di_laurea: str | None = None
    if isinstance(payload.corso_di_laurea, str):
        stripped = payload.corso_di_laurea.strip()
        cleaned_corso_di_laurea = stripped or None

    course = Course(
        organization_id=organization_id,
        title=payload.title.strip(),
        objectives=(payload.objectives or "").strip(),
        language_code=payload.language_code,
        argomenti_chiave=cleaned_keywords,
        corso_di_laurea=cleaned_corso_di_laurea,
        cfu=payload.cfu,
        modules_count=modules_count,
        lessons_per_module=settings.lessons_per_module,
        lesson_duration_minutes=settings.lesson_duration_minutes,
        assessment_lesson_enabled=settings.assessment_lesson_enabled,
        multiple_choice_questions_count=settings.multiple_choice_questions_count,
        open_questions_count=settings.open_questions_count,
        assignee_user_id=assignee_id,
        created_by_user_id=current_user.id,
        status="draft",
        categoria_term_id=payload.taxonomies.categoria,
        stile_insegnamento_term_id=payload.taxonomies.stile_insegnamento,
        profondita_contenuto_term_id=payload.taxonomies.profondita_contenuto,
        ruolo_docente_term_id=payload.taxonomies.ruolo_docente,
        dimensione_pubblico_term_id=payload.taxonomies.dimensione_pubblico,
        livello_conoscenza_term_id=payload.taxonomies.livello_conoscenza,
        destinatari_term_id=payload.taxonomies.destinatari,
        livello_eqf_term_id=payload.taxonomies.livello_eqf,
    )
    db.add(course)
    await db.flush()
    await write_audit(
        db,
        action="course.create",
        actor_user_id=current_user.id,
        organization_id=organization_id,
        target_type="course",
        target_id=str(course.id),
        metadata={
            "title": course.title,
            "cfu": course.cfu,
            "modules_count": course.modules_count,
            "language_code": course.language_code,
            "assignee_user_id": str(course.assignee_user_id),
        },
    )
    return await _refresh_full(db, course.id)


async def update_course(
    db: AsyncSession,
    *,
    course: Course,
    payload: CourseUpdateInput,
    actor_id: uuid.UUID,
) -> Course:
    # Gating: se il setup didattico è confermato, blocca le modifiche ai
    # parametri di Tab 1 + Tab 2. Per tornare editabile, l'utente deve
    # passare per `unlock_didactic_setup` (creator/org_admin only).
    # Lo `status` è gestito dal pipeline AI e non rientra nel lock.
    # Per `assignee_user_id` esiste l'endpoint dedicato `update_assignee`.
    if course.didactic_setup_confirmed_at is not None:
        # `corso_di_laurea` e' considerato campo di setup (mostrato in
        # tab "Inquadramento didattico" insieme alle tassonomie): viene
        # bloccato dopo conferma.
        new_corso_di_laurea_raw = (
            payload.corso_di_laurea.strip()
            if isinstance(payload.corso_di_laurea, str)
            else None
        )
        new_corso_di_laurea = (
            new_corso_di_laurea_raw or None
        )
        locked_change = (
            (payload.title is not None and payload.title.strip() != course.title)
            or (
                payload.objectives is not None
                and payload.objectives.strip() != course.objectives
            )
            or (
                payload.language_code is not None
                and payload.language_code != course.language_code
            )
            or (payload.cfu is not None and payload.cfu != course.cfu)
            or payload.argomenti_chiave is not None
            or payload.taxonomies is not None
            or (
                payload.corso_di_laurea is not None
                and new_corso_di_laurea != course.corso_di_laurea
            )
        )
        if locked_change:
            raise ConflictError(
                "Setup didattico confermato: i parametri non sono più "
                "modificabili. Sblocca il setup prima di modificare.",
                code="setup_locked",
            )

    diff: dict[str, Any] = {}

    if payload.title is not None:
        new_title = payload.title.strip()
        if new_title != course.title:
            diff["title"] = {"old": course.title, "new": new_title}
            course.title = new_title

    if payload.objectives is not None:
        new_obj = payload.objectives.strip()
        if new_obj != course.objectives:
            diff["objectives"] = True
            course.objectives = new_obj

    if payload.language_code is not None and payload.language_code != course.language_code:
        await _validate_language(db, language_code=payload.language_code)
        diff["language_code"] = {
            "old": course.language_code,
            "new": payload.language_code,
        }
        course.language_code = payload.language_code

    if (
        payload.video_language_code is not None
        and payload.video_language_code != course.video_language_code
    ):
        # Fase 6 §9: validazione contro XTTS_SUPPORTED_LANGUAGES.
        # Sentinel "" = reset a NULL (uso default language_code).
        from app.services.tts_languages import (
            XTTS_SUPPORTED_LANGUAGES,
            normalize_language_code,
        )

        new_video_lang: str | None
        if payload.video_language_code == "":
            new_video_lang = None
        else:
            normalized = normalize_language_code(payload.video_language_code)
            if normalized not in XTTS_SUPPORTED_LANGUAGES:
                raise ValidationAppError(
                    f"Lingua TTS non supportata: '{payload.video_language_code}'. "
                    f"Supportate: {sorted(XTTS_SUPPORTED_LANGUAGES)}",
                    code="unsupported_video_language",
                )
            # Verifica anche l'esistenza nella table `languages` per il FK.
            await _validate_language(db, language_code=normalized)
            new_video_lang = normalized
        diff["video_language_code"] = {
            "old": course.video_language_code,
            "new": new_video_lang,
        }
        course.video_language_code = new_video_lang

    if payload.cfu is not None and payload.cfu != course.cfu:
        # Ricalcolo modules_count usando lo stesso modules_per_cfu snapshot
        # implicito: deriviamo dal rapporto attuale.
        modules_per_cfu = max(1, course.modules_count // max(1, course.cfu))
        new_modules_count = payload.cfu * modules_per_cfu
        diff["cfu"] = {"old": course.cfu, "new": payload.cfu}
        diff["modules_count"] = {
            "old": course.modules_count,
            "new": new_modules_count,
        }
        course.cfu = payload.cfu
        course.modules_count = new_modules_count

    if payload.argomenti_chiave is not None:
        seen: set[str] = set()
        cleaned: list[str] = []
        for k in payload.argomenti_chiave:
            kk = (k or "").strip()
            if not kk or kk in seen:
                continue
            seen.add(kk)
            cleaned.append(kk)
        if cleaned != list(course.argomenti_chiave or []):
            diff["argomenti_chiave"] = True
            course.argomenti_chiave = cleaned
            flag_modified(course, "argomenti_chiave")

    if payload.corso_di_laurea is not None:
        # Trim + empty-as-null: "" o "   " viene normalizzato a None.
        stripped = payload.corso_di_laurea.strip()
        new_val = stripped if stripped else None
        if new_val != course.corso_di_laurea:
            diff["corso_di_laurea"] = {
                "old": course.corso_di_laurea,
                "new": new_val,
            }
            course.corso_di_laurea = new_val

    if payload.taxonomies is not None:
        await _validate_all_taxonomies(db, taxonomies=payload.taxonomies)
        for field_name, _expected_type, attr in TAXONOMY_FIELDS:
            new_val = getattr(payload.taxonomies, field_name)
            if getattr(course, attr) != new_val:
                diff.setdefault("taxonomies", {})[field_name] = {
                    "old": str(getattr(course, attr)) if getattr(course, attr) else None,
                    "new": str(new_val) if new_val else None,
                }
                setattr(course, attr, new_val)

    if payload.status is not None and payload.status != course.status:
        diff["status"] = {"old": course.status, "new": payload.status}
        course.status = payload.status

    if not diff:
        return await _refresh_full(db, course.id)

    await db.flush()
    await write_audit(
        db,
        action="course.update",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course",
        target_id=str(course.id),
        metadata={"diff": diff},
    )
    return await _refresh_full(db, course.id)


async def update_assignee(
    db: AsyncSession,
    *,
    course: Course,
    new_assignee_user_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> Course:
    if course.assignee_user_id == new_assignee_user_id:
        return await _refresh_full(db, course.id)
    if course.didactic_setup_confirmed_at is not None:
        # Setup confermato → assignee fa parte di Tab 1, anch'esso lockato.
        raise ConflictError(
            "Setup didattico confermato: l'assegnatario non è modificabile. "
            "Sblocca il setup prima di cambiarlo.",
            code="setup_locked",
        )
    await _validate_assignee(
        db,
        organization_id=course.organization_id,
        user_id=new_assignee_user_id,
    )
    old = course.assignee_user_id
    course.assignee_user_id = new_assignee_user_id
    await db.flush()
    await write_audit(
        db,
        action="course.assignee.update",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course",
        target_id=str(course.id),
        metadata={
            "old_assignee_user_id": str(old),
            "new_assignee_user_id": str(new_assignee_user_id),
        },
    )
    return await _refresh_full(db, course.id)


async def delete_course(
    db: AsyncSession, *, course: Course, actor_id: uuid.UUID
) -> None:
    course_id = course.id
    organization_id = course.organization_id
    documents_count = len(course.documents)
    document_paths = [d.file_path for d in course.documents]
    await db.delete(course)
    await db.flush()
    # Cancella i file su disco DOPO il flush DB, così se DELETE fallisce
    # (vincoli FK) non lasciamo orfani sul filesystem.
    for path in document_paths:
        await file_service.delete_upload(path)
    await write_audit(
        db,
        action="course.delete",
        actor_user_id=actor_id,
        organization_id=organization_id,
        target_type="course",
        target_id=str(course_id),
        metadata={"documents_deleted": documents_count},
    )


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


async def list_documents(
    db: AsyncSession, *, course_id: uuid.UUID
) -> list[CourseDocument]:
    res = await db.execute(
        select(CourseDocument)
        .where(CourseDocument.course_id == course_id)
        .order_by(CourseDocument.created_at.asc())
    )
    return list(res.scalars().all())


async def get_document(
    db: AsyncSession, *, course_id: uuid.UUID, doc_id: uuid.UUID
) -> CourseDocument:
    doc = await db.get(CourseDocument, doc_id)
    if doc is None or doc.course_id != course_id:
        raise NotFoundError(
            "Documento non trovato.", code="course_document_not_found"
        )
    return doc


async def add_document(
    db: AsyncSession,
    *,
    course: Course,
    upload: UploadFile,
    actor_id: uuid.UUID,
) -> CourseDocument:
    # Salva il file su disco; ritorna path relativo, filename stored, dimensione.
    public_path, filename_stored, size_bytes = (
        await file_service.save_upload_document(
            upload,
            subdir=f"courses/{course.id}",
        )
    )
    doc = CourseDocument(
        course_id=course.id,
        filename_original=(upload.filename or "documento")[:300],
        filename_stored=filename_stored,
        file_path=public_path,
        mime_type=(upload.content_type or "application/octet-stream"),
        size_bytes=size_bytes,
        uploaded_by_user_id=actor_id,
        summary_status="pending",
    )
    db.add(doc)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        # Pulisci il file su disco se l'inserimento DB fallisce.
        await file_service.delete_upload(public_path)
        raise ConflictError(
            "Conflitto nel salvataggio del documento.",
            code="course_document_conflict",
        ) from exc
    await write_audit(
        db,
        action="course.document.add",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course_document",
        target_id=str(doc.id),
        metadata={
            "course_id": str(course.id),
            "filename_original": doc.filename_original,
            "size_bytes": doc.size_bytes,
            "mime_type": doc.mime_type,
        },
    )
    return doc


async def reprocess_document(
    db: AsyncSession,
    *,
    course: Course,
    doc: CourseDocument,
    actor_id: uuid.UUID,
) -> CourseDocument:
    """Resetta lo stato del riassunto a `pending`. Il worker lo riprenderà
    al prossimo tick. Disponibile sempre (anche su `ready` o `failed`)."""
    doc.summary_status = "pending"
    doc.summary_error = None
    await write_audit(
        db,
        action="course.document.reprocess",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course_document",
        target_id=str(doc.id),
        metadata={
            "course_id": str(course.id),
            "filename_original": doc.filename_original,
            "previous_attempts": doc.summary_attempts,
        },
    )
    await db.commit()
    await db.refresh(doc)
    return doc


async def delete_document(
    db: AsyncSession,
    *,
    course: Course,
    doc: CourseDocument,
    actor_id: uuid.UUID,
) -> None:
    file_path = doc.file_path
    doc_id = doc.id
    await db.delete(doc)
    await db.flush()
    await file_service.delete_upload(file_path)
    await write_audit(
        db,
        action="course.document.delete",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course_document",
        target_id=str(doc_id),
        metadata={"course_id": str(course.id)},
    )


# ---------------------------------------------------------------------------
# Wizard setup confirm/unlock (Tab 1 + Tab 2 lock)
# ---------------------------------------------------------------------------


async def confirm_didactic_setup(
    db: AsyncSession,
    *,
    course: Course,
    actor_id: uuid.UUID,
) -> Course:
    """Marca il setup didattico (Tab 1 + Tab 2) come confermato.

    Idempotente: se già confermato, ritorna il corso senza errore. Da
    questo momento i campi parametri (title, objectives, argomenti_chiave,
    language, cfu, taxonomies) non sono più modificabili tramite
    `update_course` finché l'utente non chiama `unlock_didactic_setup`.
    """
    if course.didactic_setup_confirmed_at is not None:
        # Idempotenza: nessun side-effect, niente audit.
        return await _refresh_full(db, course.id)
    course.didactic_setup_confirmed_at = datetime.now(UTC)
    await db.flush()
    await write_audit(
        db,
        action="course.didactic_setup.confirmed",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course",
        target_id=str(course.id),
    )
    return await _refresh_full(db, course.id)


async def unlock_didactic_setup(
    db: AsyncSession,
    *,
    course: Course,
    actor_user: User,
    actor_membership: Membership | None,
    actor_id: uuid.UUID,
) -> Course:
    """Sblocca il setup didattico — solo creator/org_admin (o platform_admin).

    Azzera `didactic_setup_confirmed_at`, riportando il corso allo stato
    pre-conferma. Idempotente sul corso non confermato.
    """
    # Permission check: platform_admin bypass; altrimenti serve creator
    # o org_admin nell'org del corso.
    if not actor_user.is_platform_admin:
        if actor_membership is None:
            raise PermissionDeniedError(
                "Membership richiesta per sbloccare il setup.",
                code="not_a_member",
            )
        actor_role = await db.get(OrganizationRole, actor_membership.role_id)
        if actor_role is None or actor_role.code not in (R.CREATOR, R.ORG_ADMIN):
            raise PermissionDeniedError(
                "Solo il creator o l'org_admin può sbloccare il setup.",
                code="setup_unlock_role_required",
            )

    if course.didactic_setup_confirmed_at is None:
        # Idempotenza: nessun side-effect.
        return await _refresh_full(db, course.id)
    course.didactic_setup_confirmed_at = None
    await db.flush()
    await write_audit(
        db,
        action="course.didactic_setup.unlocked",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course",
        target_id=str(course.id),
    )
    return await _refresh_full(db, course.id)
