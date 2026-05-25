from __future__ import annotations

import urllib.parse
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Query, Response, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.config import get_settings
from app.core.deps import CurrentUser, DbSession
from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.core.permissions import P, require, require_membership, resolve_permissions
from app.models.course import Course
from app.models.membership import Membership
from app.models.organization import Organization
from app.schemas.common import Page, PageMeta
from app.schemas.course import (
    CourseAssigneeUpdateInput,
    CourseCreateInput,
    CourseDocumentDetailOut,
    CourseDocumentOut,
    CourseListItemOut,
    CourseListLessonsProgress,
    CourseOut,
    CourseStatus,
    CourseUpdateInput,
    UserCompact,
)
from app.schemas.course_architecture import (
    CourseArchitectureGenerateInput,
    LessonCreateInput,
    LessonUpdateInput,
    ModuleCreateInput,
    ModuleUpdateInput,
    ReorderInput,
)
from app.schemas.course_glossary import GlossaryRegenerateInput
from app.schemas.course_lesson_content import (
    LessonAssessmentUpdateInput,
    LessonContentGenerateInput,
    LessonContentUpdateInput,
)
from app.schemas.course_lesson_slides import (
    LessonSlidesGenerateInput,
    LessonSlidesUpdateInput,
)
from app.schemas.course_lesson_speech import (
    LessonSpeechGenerateInput,
    LessonSpeechUpdateInput,
)
from app.schemas.course_lesson_structure import (
    LessonStructureUpdateInput,
    LessonsStructureGenerateInput,
)
from app.schemas.course_lesson_avatar_video import (
    LessonAvatarVideoBatchOut,
    LessonAvatarVideoGenerateInput,
    LessonAvatarVideoStatusOut,
)
from app.schemas.course_lesson_video import (
    LessonVideoBatchOut,
    LessonVideoGenerateInput,
    LessonVideoStatusOut,
)
from app.schemas.course_duplication import (
    CourseDuplicationJobCompact,
    CourseDuplicationJobOut,
)
from app.services import (
    course_architecture_crud,
    course_architecture_service,
    course_duplication_service,
    course_glossary_service,
    course_lesson_avatar_video_service,
    course_lesson_content_crud,
    course_lesson_content_service,
    course_lesson_pdf_service,
    course_lesson_slides_crud,
    course_lesson_slides_pdf_service,
    course_lesson_slides_service,
    course_lesson_speech_crud,
    course_lesson_speech_pdf_service,
    course_lesson_speech_service,
    course_lesson_structure_crud,
    course_lesson_structure_service,
    course_lesson_video_service,
    course_module_pdf_service,
    course_service,
    file_service,
)
from app.services.openai_client import OpenAINotConfiguredError
from app.services.openai_image_to_mermaid_service import (
    OpenAIImageToMermaidError,
    convert_image_to_mermaid,
)

router = APIRouter(prefix="/orgs/{org_id}/courses", tags=["courses"])


async def _ensure_org(db, org_id: uuid.UUID) -> None:
    org = await db.get(Organization, org_id)
    if org is None or org.deleted_at is not None:
        raise NotFoundError(
            "Organizzazione non trovata.", code="organization_not_found"
        )


@router.get("", response_model=Page[CourseListItemOut])
async def list_courses(
    org_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_VIEW),
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 20,
    q: Annotated[str | None, Query(max_length=200)] = None,
    course_status: Annotated[CourseStatus | None, Query(alias="status")] = None,
    assignee_user_id: Annotated[uuid.UUID | None, Query()] = None,
    language_code: Annotated[str | None, Query(max_length=10)] = None,
    created_after: Annotated[datetime | None, Query()] = None,
    created_before: Annotated[datetime | None, Query()] = None,
    updated_after: Annotated[datetime | None, Query()] = None,
    updated_before: Annotated[datetime | None, Query()] = None,
    sort_by: Annotated[
        Literal["created_at", "updated_at"], Query()
    ] = "updated_at",
    sort_dir: Annotated[Literal["asc", "desc"], Query()] = "desc",
) -> Page[CourseListItemOut]:
    await _ensure_org(db, org_id)
    granted = await resolve_permissions(db, user=current, organization_id=org_id)
    items, total, agg_map, duplication_jobs_map = await course_service.list_courses(
        db,
        organization_id=org_id,
        current_user=current,
        granted_permissions=granted,
        page=page,
        page_size=page_size,
        q=q,
        status=course_status,
        assignee_user_id=assignee_user_id,
        language_code=language_code,
        created_after=created_after,
        created_before=created_before,
        updated_after=updated_after,
        updated_before=updated_before,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )

    def _build_item(c: Any) -> CourseListItemOut:
        agg = agg_map.get(c.id)
        progress = CourseListLessonsProgress(
            total=int(agg.total) if agg else 0,
            content_ready=int(agg.content_ready) if agg else 0,
            slides_ready=int(agg.slides_ready) if agg else 0,
            videos_ready=int(agg.videos_ready) if agg else 0,
            avatar_videos_ready=int(agg.avatar_videos_ready) if agg else 0,
        )
        dup_job = duplication_jobs_map.get(c.id)
        dup_job_out = (
            CourseDuplicationJobCompact.model_validate(dup_job)
            if dup_job is not None
            else None
        )
        return CourseListItemOut(
            id=c.id,
            title=c.title,
            status=c.status,
            language_code=c.language_code,
            assignee=UserCompact.model_validate(c.assignee),
            modules_count=c.modules_count,
            cfu=c.cfu,
            updated_at=c.updated_at,
            created_at=c.created_at,
            lessons_progress=progress,
            duplication_job=dup_job_out,
        )

    return Page[CourseListItemOut](
        items=[_build_item(c) for c in items],
        meta=PageMeta(page=page, page_size=page_size, total=total),
    )


@router.post("", response_model=CourseOut, status_code=status.HTTP_201_CREATED)
async def create_course(
    org_id: uuid.UUID,
    payload: CourseCreateInput,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_CREATE),
) -> CourseOut:
    await _ensure_org(db, org_id)
    course = await course_service.create_course(
        db,
        organization_id=org_id,
        payload=payload,
        current_user=current,
    )
    return CourseOut.model_validate(course)


@router.get("/{course_id}", response_model=CourseOut)
async def get_course(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_VIEW),
) -> CourseOut:
    await _ensure_org(db, org_id)
    granted = await resolve_permissions(db, user=current, organization_id=org_id)
    course = await course_service.get_course(
        db,
        organization_id=org_id,
        course_id=course_id,
        current_user=current,
        granted_permissions=granted,
    )
    return CourseOut.model_validate(course)


def _ensure_can_edit_basic(course, current, granted: set[str]) -> None:
    """Permette di modificare le info di base + inquadramento didattico a:
    - chi ha P.COURSE_EDIT sull'organizzazione, oppure
    - l'assegnatario del corso (anche un Member, che di default non ha
      COURSE_EDIT) — caso d'uso: admin crea una bozza con 'Salva come bozza'
      e la passa a un altro utente perché la completi.
    Platform admin passa via `resolve_permissions` (riceve tutti i permessi).
    """
    if P.COURSE_EDIT in granted:
        return
    if course.assignee_user_id == current.id:
        return
    raise PermissionDeniedError(
        f"Permessi mancanti: {P.COURSE_EDIT}",
        code="permission_denied",
        meta={"missing": [P.COURSE_EDIT]},
    )


@router.patch("/{course_id}", response_model=CourseOut)
async def update_course(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    payload: CourseUpdateInput,
    db: DbSession,
    current: CurrentUser,
    _=require_membership(),
) -> CourseOut:
    await _ensure_org(db, org_id)
    granted = await resolve_permissions(db, user=current, organization_id=org_id)
    course = await course_service.get_course(
        db,
        organization_id=org_id,
        course_id=course_id,
        current_user=current,
        granted_permissions=granted,
    )
    _ensure_can_edit_basic(course, current, granted)
    course = await course_service.update_course(
        db, course=course, payload=payload, actor_id=current.id
    )
    return CourseOut.model_validate(course)


@router.post(
    "/{course_id}/setup/confirm-didactic",
    response_model=CourseOut,
    status_code=status.HTTP_200_OK,
)
async def confirm_didactic_setup(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require_membership(),
) -> CourseOut:
    """Conferma il setup didattico (Tab 1 + Tab 2). Idempotente."""
    await _ensure_org(db, org_id)
    granted = await resolve_permissions(db, user=current, organization_id=org_id)
    course = await course_service.get_course(
        db,
        organization_id=org_id,
        course_id=course_id,
        current_user=current,
        granted_permissions=granted,
    )
    _ensure_can_edit_basic(course, current, granted)
    course = await course_service.confirm_didactic_setup(
        db, course=course, actor_id=current.id
    )
    return CourseOut.model_validate(course)


@router.post(
    "/{course_id}/setup/unlock",
    response_model=CourseOut,
    status_code=status.HTTP_200_OK,
)
async def unlock_didactic_setup(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_EDIT),
) -> CourseOut:
    """Sblocca il setup didattico — solo creator/org_admin (o platform_admin)."""
    await _ensure_org(db, org_id)
    granted = await resolve_permissions(db, user=current, organization_id=org_id)
    course = await course_service.get_course(
        db,
        organization_id=org_id,
        course_id=course_id,
        current_user=current,
        granted_permissions=granted,
    )
    actor_membership = (
        await db.execute(
            select(Membership).where(
                Membership.user_id == current.id,
                Membership.organization_id == org_id,
            )
        )
    ).scalar_one_or_none()
    course = await course_service.unlock_didactic_setup(
        db,
        course=course,
        actor_user=current,
        actor_membership=actor_membership,
        actor_id=current.id,
    )
    return CourseOut.model_validate(course)


@router.patch("/{course_id}/assignee", response_model=CourseOut)
async def update_assignee(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    payload: CourseAssigneeUpdateInput,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_ASSIGN),
) -> CourseOut:
    await _ensure_org(db, org_id)
    granted = await resolve_permissions(db, user=current, organization_id=org_id)
    course = await course_service.get_course(
        db,
        organization_id=org_id,
        course_id=course_id,
        current_user=current,
        granted_permissions=granted,
    )
    course = await course_service.update_assignee(
        db,
        course=course,
        new_assignee_user_id=payload.assignee_user_id,
        actor_id=current.id,
    )
    return CourseOut.model_validate(course)


@router.delete("/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_course(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_DELETE),
) -> Response:
    await _ensure_org(db, org_id)
    granted = await resolve_permissions(db, user=current, organization_id=org_id)
    course = await course_service.get_course(
        db,
        organization_id=org_id,
        course_id=course_id,
        current_user=current,
        granted_permissions=granted,
    )
    await course_service.delete_course(db, course=course, actor_id=current.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Duplicazione corso in altra lingua (background job via worker)
# ---------------------------------------------------------------------------


@router.post(
    "/{course_id}/duplicate",
    response_model=CourseDuplicationJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def duplicate_course(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    target_language_code: Annotated[str, Query(min_length=2, max_length=10)],
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_DUPLICATE),
) -> CourseDuplicationJobOut:
    """Crea un job di duplicazione `pending`. Il worker
    `course_duplication_worker` lo prende in carico, clona la shell del
    corso target e traduce via OpenAI tutti i contenuti.

    Video MP4 e Video con Avatar NON vengono copiati: nel target
    partono da `empty`.
    """
    await _ensure_org(db, org_id)
    granted = await resolve_permissions(db, user=current, organization_id=org_id)
    source = await course_service.get_course(
        db,
        organization_id=org_id,
        course_id=course_id,
        current_user=current,
        granted_permissions=granted,
    )
    job = await course_duplication_service.request_course_duplication(
        db,
        source_course=source,
        target_language_code=target_language_code,
        actor_id=current.id,
    )
    return CourseDuplicationJobOut.model_validate(job)


@router.get(
    "/{course_id}/duplications",
    response_model=list[CourseDuplicationJobOut],
)
async def list_course_duplications(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_VIEW),
) -> list[CourseDuplicationJobOut]:
    """Lista tutti i job (qualsiasi stato) in cui il corso è source o
    target. Usato dalla UI per poll del progresso del job attivo.
    """
    await _ensure_org(db, org_id)
    granted = await resolve_permissions(db, user=current, organization_id=org_id)
    # Verifica visibilità del corso (404 silenzioso se non visto).
    await course_service.get_course(
        db,
        organization_id=org_id,
        course_id=course_id,
        current_user=current,
        granted_permissions=granted,
    )
    jobs = await course_duplication_service.list_duplications_for_course(
        db, course_id=course_id
    )
    return [CourseDuplicationJobOut.model_validate(j) for j in jobs]


@router.post(
    "/duplication-jobs/{job_id}/cancel",
    response_model=CourseDuplicationJobOut,
)
async def cancel_course_duplication(
    org_id: uuid.UUID,
    job_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_DUPLICATE),
) -> CourseDuplicationJobOut:
    """Annulla un job di duplicazione `pending` o `processing` →
    `failed`. Idempotente. Il corso target eventualmente già creato
    resta in DB (l'utente decide se eliminarlo a mano).
    """
    await _ensure_org(db, org_id)
    job = await course_duplication_service.get_job_or_404(db, job_id=job_id)
    # Verifica che il source course appartenga all'organizzazione.
    source = await db.get(Course, job.source_course_id)
    if source is None or source.organization_id != org_id:
        raise NotFoundError(
            "Job di duplicazione non trovato.",
            code="duplication_job_not_found",
        )
    job = await course_duplication_service.cancel_duplication(
        db, job=job, actor_id=current.id
    )
    return CourseDuplicationJobOut.model_validate(job)


@router.get(
    "/{course_id}/documents", response_model=list[CourseDocumentOut]
)
async def list_documents(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_VIEW),
) -> list[CourseDocumentOut]:
    await _ensure_org(db, org_id)
    granted = await resolve_permissions(db, user=current, organization_id=org_id)
    # Verifica visibilità del corso (404 silenzioso se non visto).
    await course_service.get_course(
        db,
        organization_id=org_id,
        course_id=course_id,
        current_user=current,
        granted_permissions=granted,
    )
    docs = await course_service.list_documents(db, course_id=course_id)
    return [CourseDocumentOut.model_validate(d) for d in docs]


@router.post(
    "/{course_id}/documents",
    response_model=CourseDocumentOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    file: Annotated[UploadFile, File(...)],
    _=require(P.COURSE_EDIT),
) -> CourseDocumentOut:
    await _ensure_org(db, org_id)
    granted = await resolve_permissions(db, user=current, organization_id=org_id)
    course = await course_service.get_course(
        db,
        organization_id=org_id,
        course_id=course_id,
        current_user=current,
        granted_permissions=granted,
    )
    doc = await course_service.add_document(
        db, course=course, upload=file, actor_id=current.id
    )
    return CourseDocumentOut.model_validate(doc)


@router.get(
    "/{course_id}/documents/{doc_id}",
    response_model=CourseDocumentDetailOut,
)
async def get_document(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    doc_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_VIEW),
    include_summary: Annotated[bool, Query()] = False,
) -> CourseDocumentDetailOut:
    await _ensure_org(db, org_id)
    granted = await resolve_permissions(db, user=current, organization_id=org_id)
    # Verifica visibilità del corso (404 silenzioso se non visto).
    await course_service.get_course(
        db,
        organization_id=org_id,
        course_id=course_id,
        current_user=current,
        granted_permissions=granted,
    )
    doc = await course_service.get_document(
        db, course_id=course_id, doc_id=doc_id
    )
    out = CourseDocumentDetailOut.model_validate(doc)
    if not include_summary:
        out.summary = None
    return out


@router.post(
    "/{course_id}/documents/{doc_id}/reprocess",
    response_model=CourseDocumentOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reprocess_document(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    doc_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_EDIT),
) -> CourseDocumentOut:
    await _ensure_org(db, org_id)
    granted = await resolve_permissions(db, user=current, organization_id=org_id)
    course = await course_service.get_course(
        db,
        organization_id=org_id,
        course_id=course_id,
        current_user=current,
        granted_permissions=granted,
    )
    doc = await course_service.get_document(
        db, course_id=course_id, doc_id=doc_id
    )
    doc = await course_service.reprocess_document(
        db, course=course, doc=doc, actor_id=current.id
    )
    return CourseDocumentOut.model_validate(doc)


@router.delete(
    "/{course_id}/documents/{doc_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_document(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    doc_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_EDIT),
) -> Response:
    await _ensure_org(db, org_id)
    granted = await resolve_permissions(db, user=current, organization_id=org_id)
    course = await course_service.get_course(
        db,
        organization_id=org_id,
        course_id=course_id,
        current_user=current,
        granted_permissions=granted,
    )
    doc = await course_service.get_document(
        db, course_id=course_id, doc_id=doc_id
    )
    await course_service.delete_document(
        db, course=course, doc=doc, actor_id=current.id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Architettura corso (Fase 1 della pipeline AI)
# ---------------------------------------------------------------------------


@router.post(
    "/{course_id}/architecture/generate",
    response_model=CourseOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_architecture(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    payload: CourseArchitectureGenerateInput,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Avvia la generazione dell'architettura del corso (Fase 1).

    Richiede almeno 1 documento con summary `ready`. Imposta lo status
    a `architecture_pending`; il worker prende in carico al prossimo
    tick. Risponde 202 immediatamente con lo stato aggiornato.
    """
    await _ensure_org(db, org_id)
    granted = await resolve_permissions(db, user=current, organization_id=org_id)
    course = await course_service.get_course(
        db,
        organization_id=org_id,
        course_id=course_id,
        current_user=current,
        granted_permissions=granted,
    )
    course = await course_architecture_service.request_generation(
        db,
        course=course,
        actor_id=current.id,
        regeneration_hint=payload.regeneration_hint,
    )
    return CourseOut.model_validate(course)


@router.post(
    "/{course_id}/architecture/approve",
    response_model=CourseOut,
)
async def approve_architecture(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Approva l'architettura corso (`architecture_ready` → `architecture_approved`).

    Da qui in avanti l'architettura è stabile e si può procedere alle
    fasi successive (lessons structure, content, slides, speech).
    """
    await _ensure_org(db, org_id)
    granted = await resolve_permissions(db, user=current, organization_id=org_id)
    course = await course_service.get_course(
        db,
        organization_id=org_id,
        course_id=course_id,
        current_user=current,
        granted_permissions=granted,
    )
    course = await course_architecture_service.approve_architecture(
        db, course=course, actor_id=current.id
    )
    return CourseOut.model_validate(course)


# ---------------------------------------------------------------------------
# CRUD manuale moduli e lezioni
# ---------------------------------------------------------------------------


async def _load_course_for_edit(
    db, *, org_id: uuid.UUID, course_id: uuid.UUID, current: object
) -> object:
    granted = await resolve_permissions(db, user=current, organization_id=org_id)
    return await course_service.get_course(
        db,
        organization_id=org_id,
        course_id=course_id,
        current_user=current,
        granted_permissions=granted,
    )


@router.post(
    "/{course_id}/modules",
    response_model=CourseOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_module(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    payload: ModuleCreateInput,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_EDIT),
) -> CourseOut:
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    course = await course_architecture_crud.create_module(
        db, course=course, actor_id=current.id, payload=payload
    )
    return CourseOut.model_validate(course)


@router.patch(
    "/{course_id}/modules/{module_id}",
    response_model=CourseOut,
)
async def update_module(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    module_id: uuid.UUID,
    payload: ModuleUpdateInput,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_EDIT),
) -> CourseOut:
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    course = await course_architecture_crud.update_module(
        db,
        course=course,
        actor_id=current.id,
        module_id=module_id,
        payload=payload,
    )
    return CourseOut.model_validate(course)


@router.delete(
    "/{course_id}/modules/{module_id}",
    response_model=CourseOut,
)
async def delete_module(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    module_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_EDIT),
) -> CourseOut:
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    course = await course_architecture_crud.delete_module(
        db, course=course, actor_id=current.id, module_id=module_id
    )
    return CourseOut.model_validate(course)


@router.post(
    "/{course_id}/modules/{module_id}/generate-lessons",
    response_model=CourseOut,
)
async def generate_module_lessons(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    module_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Genera le lezioni di un singolo modulo via AI.

    Sostituisce le lezioni esistenti del modulo con quelle generate.
    Richiede `course:generate` e status `architecture_ready`/`approved`.
    """
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    course = await course_architecture_crud.regenerate_module_lessons(
        db, course=course, actor_id=current.id, module_id=module_id
    )
    return CourseOut.model_validate(course)


@router.post(
    "/{course_id}/modules/reorder",
    response_model=CourseOut,
)
async def reorder_modules(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    payload: ReorderInput,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_EDIT),
) -> CourseOut:
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    course = await course_architecture_crud.reorder_modules(
        db, course=course, actor_id=current.id, new_order=payload.ids
    )
    return CourseOut.model_validate(course)


@router.post(
    "/{course_id}/modules/{module_id}/lessons",
    response_model=CourseOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_lesson(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    module_id: uuid.UUID,
    payload: LessonCreateInput,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_EDIT),
) -> CourseOut:
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    course = await course_architecture_crud.create_lesson(
        db,
        course=course,
        actor_id=current.id,
        module_id=module_id,
        payload=payload,
    )
    return CourseOut.model_validate(course)


@router.patch(
    "/{course_id}/lessons/{lesson_id}",
    response_model=CourseOut,
)
async def update_lesson(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    payload: LessonUpdateInput,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_EDIT),
) -> CourseOut:
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    course = await course_architecture_crud.update_lesson(
        db,
        course=course,
        actor_id=current.id,
        lesson_id=lesson_id,
        payload=payload,
    )
    return CourseOut.model_validate(course)


@router.delete(
    "/{course_id}/lessons/{lesson_id}",
    response_model=CourseOut,
)
async def delete_lesson(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_EDIT),
) -> CourseOut:
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    course = await course_architecture_crud.delete_lesson(
        db, course=course, actor_id=current.id, lesson_id=lesson_id
    )
    return CourseOut.model_validate(course)


@router.post(
    "/{course_id}/modules/{module_id}/lessons/reorder",
    response_model=CourseOut,
)
async def reorder_lessons(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    module_id: uuid.UUID,
    payload: ReorderInput,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_EDIT),
) -> CourseOut:
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    course = await course_architecture_crud.reorder_lessons(
        db,
        course=course,
        actor_id=current.id,
        module_id=module_id,
        new_order=payload.ids,
    )
    return CourseOut.model_validate(course)


# ---------------------------------------------------------------------------
# Fase 2 — Struttura delle lezioni (§5)
# ---------------------------------------------------------------------------


@router.post(
    "/{course_id}/modules/{module_id}/lessons-structure/generate",
    response_model=CourseOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_module_lessons_structure(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    module_id: uuid.UUID,
    payload: LessonsStructureGenerateInput,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Avvia la generazione AI della struttura formativa per un modulo
    (Fase 2 — §5). Imposta `module.lessons_structure_status='pending'`;
    il worker dispatcha il modulo in parallelo agli altri pending.
    Risponde 202 immediatamente con lo stato aggiornato del corso.
    """
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    module = await course_lesson_structure_service.get_module_or_404(
        db, course=course, module_id=module_id
    )
    course = await course_lesson_structure_service.request_module_generation(
        db,
        course=course,
        module=module,
        actor_id=current.id,
        regeneration_hint=payload.regeneration_hint,
    )
    return CourseOut.model_validate(course)


@router.post(
    "/{course_id}/lessons-structure/generate-all",
    response_model=CourseOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_all_lessons_structure(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    payload: LessonsStructureGenerateInput,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Avvia la generazione AI della struttura formativa per TUTTI i
    moduli del corso. Il worker dispatcha i moduli in parallelo (cap
    di concorrenza configurabile).
    """
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    course = await course_lesson_structure_service.request_all_modules_generation(
        db,
        course=course,
        actor_id=current.id,
        regeneration_hint=payload.regeneration_hint,
    )
    return CourseOut.model_validate(course)


@router.post(
    "/{course_id}/modules/{module_id}/lessons-structure/approve",
    response_model=CourseOut,
)
async def approve_module_lessons_structure(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    module_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Approva la struttura di un singolo modulo (`ready` → `approved`)."""
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    module = await course_lesson_structure_service.get_module_or_404(
        db, course=course, module_id=module_id
    )
    course = await course_lesson_structure_service.approve_module_structure(
        db, course=course, module=module, actor_id=current.id
    )
    return CourseOut.model_validate(course)


@router.post(
    "/{course_id}/lessons-structure/approve-all",
    response_model=CourseOut,
)
async def approve_all_lessons_structure(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Approva la struttura di TUTTI i moduli del corso. Richiede che
    tutti i moduli siano in stato `ready` (o già `approved`)."""
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    course = await course_lesson_structure_service.approve_all_modules_structure(
        db, course=course, actor_id=current.id
    )
    return CourseOut.model_validate(course)


@router.patch(
    "/{course_id}/lessons/{lesson_id}/structure",
    response_model=CourseOut,
)
async def update_lesson_structure(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    payload: LessonStructureUpdateInput,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_EDIT),
) -> CourseOut:
    """Patch manuale dei 4 campi JSONB della struttura lezione
    (Fase 2). Richiede che il modulo padre sia in `ready` o `approved`.
    """
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    lesson = await course_lesson_structure_service.get_lesson_or_404(
        db, course=course, lesson_id=lesson_id
    )
    course = await course_lesson_structure_crud.update_lesson_structure(
        db,
        course=course,
        lesson=lesson,
        payload=payload,
        actor_id=current.id,
    )
    return CourseOut.model_validate(course)


# ---------------------------------------------------------------------------
# Fase 3 — Contenuti delle lezioni (§6) + Glossario (§10.1)
# ---------------------------------------------------------------------------


@router.post(
    "/{course_id}/lessons/{lesson_id}/content/generate",
    response_model=CourseOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_lesson_content(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    payload: LessonContentGenerateInput,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Avvia la generazione AI del contenuto di una singola lezione
    (Fase 3 — §6). Imposta `lesson.content_status='pending'`; il worker
    dispatcha la lezione in parallelo alle altre pending (cap default 3).
    Risponde 202 immediatamente con lo stato aggiornato del corso.
    """
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    lesson = await course_lesson_content_service.get_lesson_or_404(
        db, course=course, lesson_id=lesson_id
    )
    course = await course_lesson_content_service.request_lesson_generation(
        db,
        course=course,
        lesson=lesson,
        actor_id=current.id,
        regeneration_hint=payload.regeneration_hint,
    )
    return CourseOut.model_validate(course)


@router.post(
    "/{course_id}/lessons-content/generate-all",
    response_model=CourseOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_all_lessons_content(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    payload: LessonContentGenerateInput,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Avvia la generazione AI dei contenuti per TUTTE le lezioni del
    corso. Il worker le elabora in parallelo (cap configurabile, default
    3). Al primo task il glossario viene auto-generato sync se assente.
    """
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    course = await course_lesson_content_service.request_all_lessons_generation(
        db,
        course=course,
        actor_id=current.id,
        regeneration_hint=payload.regeneration_hint,
    )
    return CourseOut.model_validate(course)


@router.post(
    "/{course_id}/lessons-content/generate-missing",
    response_model=CourseOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_missing_lessons_content(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Avvia la generazione AI dei contenuti SOLO per le lezioni con
    `content_status='empty'`. Utile per "riempire i buchi" senza
    rigenerare le lezioni già pronte/approvate.

    409 `no_missing_lessons` se tutte le lezioni hanno già un contenuto.
    """
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    course = await course_lesson_content_service.request_missing_lessons_generation(
        db,
        course=course,
        actor_id=current.id,
    )
    return CourseOut.model_validate(course)


@router.post(
    "/{course_id}/lessons-content/cancel-all",
    response_model=CourseOut,
)
async def cancel_all_lessons_content(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Annulla la generazione in corso: marca tutte le lezioni
    `pending|processing` come `failed`. Il worker scarta il risultato
    delle lezioni con OpenAI in flight."""
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    course = await course_lesson_content_service.cancel_all_lessons_generation(
        db, course=course, actor_id=current.id
    )
    return CourseOut.model_validate(course)


@router.post(
    "/{course_id}/lessons/{lesson_id}/content/approve",
    response_model=CourseOut,
)
async def approve_lesson_content(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Approva il contenuto di una singola lezione (`ready` → `approved`)."""
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    lesson = await course_lesson_content_service.get_lesson_or_404(
        db, course=course, lesson_id=lesson_id
    )
    course = await course_lesson_content_service.approve_lesson_content(
        db, course=course, lesson=lesson, actor_id=current.id
    )
    return CourseOut.model_validate(course)


@router.post(
    "/{course_id}/lessons-content/approve-all",
    response_model=CourseOut,
)
async def approve_all_lessons_content(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Approva il contenuto di TUTTE le lezioni del corso. Richiede che
    tutte le lezioni siano in stato `ready` (o già `approved`)."""
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    course = await course_lesson_content_service.approve_all_lessons_content(
        db, course=course, actor_id=current.id
    )
    return CourseOut.model_validate(course)


@router.patch(
    "/{course_id}/lessons/{lesson_id}/content",
    response_model=CourseOut,
)
async def update_lesson_content(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    payload: LessonContentUpdateInput,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_EDIT),
) -> CourseOut:
    """Patch manuale del `content_raw` della lezione (Fase 3). Richiede
    che la lezione sia in `ready` o `approved`. Edit non degrada lo stato.
    """
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    lesson = await course_lesson_content_service.get_lesson_or_404(
        db, course=course, lesson_id=lesson_id
    )
    course = await course_lesson_content_crud.update_lesson_content(
        db,
        course=course,
        lesson=lesson,
        payload=payload,
        actor_id=current.id,
    )
    return CourseOut.model_validate(course)


@router.patch(
    "/{course_id}/lessons/{lesson_id}/assessment",
    response_model=CourseOut,
)
async def update_lesson_assessment(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    payload: LessonAssessmentUpdateInput,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_EDIT),
) -> CourseOut:
    """Patch manuale della verifica delle competenze (`content_raw` di una
    lezione `is_assessment`). Richiede lezione in `ready`/`approved`."""
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    lesson = await course_lesson_content_service.get_lesson_or_404(
        db, course=course, lesson_id=lesson_id
    )
    if not lesson.is_assessment:
        raise ConflictError(
            f"La lezione {lesson.lesson_code} non è una verifica.",
            code="lesson_not_assessment",
        )
    course = await course_lesson_content_crud.update_lesson_assessment(
        db,
        course=course,
        lesson=lesson,
        payload=payload,
        actor_id=current.id,
    )
    return CourseOut.model_validate(course)


# ---------------------------------------------------------------------------
# Fase 4 — Slide della lezione (§7)
# ---------------------------------------------------------------------------


@router.post(
    "/{course_id}/lessons/{lesson_id}/slides/generate",
    response_model=CourseOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_lesson_slides(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    payload: LessonSlidesGenerateInput,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Avvia la generazione AI delle slide di una singola lezione
    (Fase 4 — §7). Imposta `lesson.slides_status='pending'`; il worker
    dispatcha la lezione in parallelo (cap default 3). Pre-condizione:
    `lesson.content_status ∈ (ready, approved)`.
    """
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    lesson = await course_lesson_slides_service.get_lesson_or_404(
        db, course=course, lesson_id=lesson_id
    )
    course = await course_lesson_slides_service.request_lesson_slides_generation(
        db,
        course=course,
        lesson=lesson,
        actor_id=current.id,
        regeneration_hint=payload.regeneration_hint,
    )
    return CourseOut.model_validate(course)


@router.post(
    "/{course_id}/lessons-slides/generate-all",
    response_model=CourseOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_all_lessons_slides(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    payload: LessonSlidesGenerateInput,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Avvia la generazione AI delle slide per TUTTE le lezioni con
    `content_status ∈ (ready, approved)`. Le altre lezioni sono ignorate.
    Il worker le elabora in parallelo (cap default 3).
    """
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    course = await course_lesson_slides_service.request_all_lessons_slides_generation(
        db,
        course=course,
        actor_id=current.id,
        regeneration_hint=payload.regeneration_hint,
    )
    return CourseOut.model_validate(course)


@router.post(
    "/{course_id}/lessons-slides/generate-missing",
    response_model=CourseOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_missing_lessons_slides(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Avvia la generazione AI delle slide SOLO per le lezioni con
    `slides_status='empty'` AND `content_status ∈ (ready, approved)`.
    """
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    course = await course_lesson_slides_service.request_missing_lessons_slides_generation(
        db,
        course=course,
        actor_id=current.id,
    )
    return CourseOut.model_validate(course)


@router.post(
    "/{course_id}/lessons-slides/cancel-all",
    response_model=CourseOut,
)
async def cancel_all_lessons_slides(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Annulla la generazione slide in corso: marca tutte le lezioni
    `pending|processing` come `failed`. Il worker scarta il risultato
    delle lezioni con OpenAI in flight."""
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    course = await course_lesson_slides_service.cancel_all_lessons_slides_generation(
        db, course=course, actor_id=current.id
    )
    return CourseOut.model_validate(course)


@router.post(
    "/{course_id}/lessons/{lesson_id}/slides/approve",
    response_model=CourseOut,
)
async def approve_lesson_slides(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Approva le slide di una singola lezione (`ready` → `approved`)."""
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    lesson = await course_lesson_slides_service.get_lesson_or_404(
        db, course=course, lesson_id=lesson_id
    )
    course = await course_lesson_slides_service.approve_lesson_slides(
        db, course=course, lesson=lesson, actor_id=current.id
    )
    return CourseOut.model_validate(course)


@router.post(
    "/{course_id}/lessons-slides/approve-all",
    response_model=CourseOut,
)
async def approve_all_lessons_slides(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Approva le slide di TUTTE le lezioni `ready` del corso."""
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    course = await course_lesson_slides_service.approve_all_lessons_slides(
        db, course=course, actor_id=current.id
    )
    return CourseOut.model_validate(course)


@router.patch(
    "/{course_id}/lessons/{lesson_id}/slides",
    response_model=CourseOut,
)
async def update_lesson_slides(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    payload: LessonSlidesUpdateInput,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_EDIT),
) -> CourseOut:
    """Patch manuale del `slides_raw` della lezione (Fase 4). Richiede
    che le slide della lezione siano in `ready` o `approved`. Edit non
    degrada lo stato.
    """
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    lesson = await course_lesson_slides_service.get_lesson_or_404(
        db, course=course, lesson_id=lesson_id
    )
    course = await course_lesson_slides_crud.update_lesson_slides(
        db,
        course=course,
        lesson=lesson,
        payload=payload,
        actor_id=current.id,
    )
    return CourseOut.model_validate(course)


# ---------------------------------------------------------------------------
# Fase 5 — Discorso temporizzato (§8)
# ---------------------------------------------------------------------------


@router.post(
    "/{course_id}/lessons/{lesson_id}/speech/generate",
    response_model=CourseOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_lesson_speech(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    payload: LessonSpeechGenerateInput,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Avvia la generazione AI del discorso temporizzato di una singola
    lezione (Fase 5 — §8). Imposta `lesson.speech_status='pending'`; il
    worker dispatcha la lezione in parallelo (cap default 3).
    Pre-condizione: `lesson.slides_status ∈ (ready, approved)`.
    """
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    lesson = await course_lesson_speech_service.get_lesson_or_404(
        db, course=course, lesson_id=lesson_id
    )
    course = await course_lesson_speech_service.request_lesson_speech_generation(
        db,
        course=course,
        lesson=lesson,
        actor_id=current.id,
        regeneration_hint=payload.regeneration_hint,
    )
    return CourseOut.model_validate(course)


@router.post(
    "/{course_id}/lessons-speech/generate-all",
    response_model=CourseOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_all_lessons_speech(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    payload: LessonSpeechGenerateInput,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Avvia la generazione AI del discorso per TUTTE le lezioni con
    `slides_status ∈ (ready, approved)`. Le altre lezioni sono ignorate.
    Il worker le elabora in parallelo (cap default 3).
    """
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    course = await course_lesson_speech_service.request_all_lessons_speech_generation(
        db,
        course=course,
        actor_id=current.id,
        regeneration_hint=payload.regeneration_hint,
    )
    return CourseOut.model_validate(course)


@router.post(
    "/{course_id}/lessons-speech/generate-missing",
    response_model=CourseOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_missing_lessons_speech(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Avvia la generazione AI del discorso SOLO per le lezioni con
    `speech_status='empty'` AND `slides_status ∈ (ready, approved)`.
    """
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    course = await course_lesson_speech_service.request_missing_lessons_speech_generation(
        db,
        course=course,
        actor_id=current.id,
    )
    return CourseOut.model_validate(course)


@router.post(
    "/{course_id}/lessons-speech/cancel-all",
    response_model=CourseOut,
)
async def cancel_all_lessons_speech(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Annulla la generazione discorso in corso: marca tutte le lezioni
    `pending|processing` come `failed`. Il worker scarta il risultato
    delle lezioni con OpenAI in flight."""
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    course = await course_lesson_speech_service.cancel_all_speech_generation(
        db, course=course, actor_id=current.id
    )
    return CourseOut.model_validate(course)


@router.post(
    "/{course_id}/lessons/{lesson_id}/speech/approve",
    response_model=CourseOut,
)
async def approve_lesson_speech(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Approva il discorso di una singola lezione (`ready` → `approved`)."""
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    lesson = await course_lesson_speech_service.get_lesson_or_404(
        db, course=course, lesson_id=lesson_id
    )
    course = await course_lesson_speech_service.approve_lesson_speech(
        db, course=course, lesson=lesson, actor_id=current.id
    )
    return CourseOut.model_validate(course)


@router.post(
    "/{course_id}/lessons-speech/approve-all",
    response_model=CourseOut,
)
async def approve_all_lessons_speech(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Approva il discorso di TUTTE le lezioni `ready` del corso."""
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    course = await course_lesson_speech_service.approve_all_lessons_speech(
        db, course=course, actor_id=current.id
    )
    return CourseOut.model_validate(course)


@router.patch(
    "/{course_id}/lessons/{lesson_id}/speech",
    response_model=CourseOut,
)
async def update_lesson_speech(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    payload: LessonSpeechUpdateInput,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_EDIT),
) -> CourseOut:
    """Patch manuale del `speech_raw` della lezione (Fase 5). Richiede
    che il discorso della lezione sia in `ready` o `approved`. Edit non
    degrada lo stato.
    """
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    lesson = await course_lesson_speech_service.get_lesson_or_404(
        db, course=course, lesson_id=lesson_id
    )
    course = await course_lesson_speech_crud.update_lesson_speech(
        db,
        course=course,
        lesson=lesson,
        payload=payload,
        actor_id=current.id,
    )
    return CourseOut.model_validate(course)


@router.post(
    "/{course_id}/glossary/regenerate",
    response_model=CourseOut,
)
async def regenerate_course_glossary(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    payload: GlossaryRegenerateInput,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Rigenera sync il glossario del corso (§10.1). Single-shot, ~10-20s.
    Richiede che l'architettura sia approvata."""
    _ = payload  # body è vuoto in MVP, ma teniamo il parametro per estensioni future
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    course = await course_glossary_service.regenerate_glossary(
        db, course=course, actor_id=current.id
    )
    return CourseOut.model_validate(course)


# ---------------------------------------------------------------------------
# §7 — Export PDF lezione
# ---------------------------------------------------------------------------


@router.post(
    "/{course_id}/lessons/{lesson_id}/pdf/export",
    response_model=CourseOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def export_lesson_pdf(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    pdf_template_id: Annotated[uuid.UUID | None, Query()] = None,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Avvia l'export PDF di una singola lezione. Imposta
    `lesson.pdf_status='pending'`; il worker la prende in carico
    (cap 2 in parallelo). Risponde 202 con lo stato aggiornato.
    Vincoli: la lezione deve avere `content_status` ∈ ready/approved.

    Query param opzionale `pdf_template_id`: scelta esplicita del
    template grafico (validato sull'org). Se omesso, il worker usa il
    template `is_default` dell'org (o il primo, se non c'è default).
    """
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    lesson = await course_lesson_content_service.get_lesson_or_404(
        db, course=course, lesson_id=lesson_id
    )
    course = await course_lesson_pdf_service.request_lesson_pdf(
        db,
        course=course,
        lesson=lesson,
        actor_id=current.id,
        pdf_template_id=pdf_template_id,
    )
    return CourseOut.model_validate(course)


@router.post(
    "/{course_id}/lessons-pdf/export-all",
    response_model=CourseOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def export_all_lessons_pdf(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    pdf_template_id: Annotated[uuid.UUID | None, Query()] = None,
    only_missing: Annotated[bool, Query()] = False,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Avvia l'export PDF per TUTTE le lezioni esportabili del corso
    (`content_status` ∈ ready/approved e `pdf_status` non in flight).

    Query params opzionali:
    - `pdf_template_id`: applica lo stesso template a tutte le lezioni
      esportabili (override). Se omesso, ogni lezione usa il proprio
      `pdf_template_id` salvato (o il default dell'org).
    - `only_missing`: se `true`, esclude le lezioni con PDF già pronto
      (`pdf_status='ready'`) e rigenera solo i mancanti. Utile per il
      pulsante "Genera PDF mancanti".
    """
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    course = await course_lesson_pdf_service.request_all_lessons_pdf(
        db,
        course=course,
        actor_id=current.id,
        pdf_template_id=pdf_template_id,
        only_missing=only_missing,
    )
    return CourseOut.model_validate(course)


@router.post(
    "/{course_id}/lessons-pdf/cancel-all",
    response_model=CourseOut,
)
async def cancel_all_lessons_pdf(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Annulla tutti gli export PDF in flight (pending/processing)."""
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    course = await course_lesson_pdf_service.cancel_all_pdf_exports(
        db, course=course, actor_id=current.id
    )
    return CourseOut.model_validate(course)


@router.get(
    "/{course_id}/lessons/{lesson_id}/pdf/download",
)
async def download_lesson_pdf(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_VIEW),
) -> FileResponse:
    """Scarica il PDF generato. 404 se non disponibile (`pdf_status` non
    `ready`)."""
    await _ensure_org(db, org_id)
    course = await course_lesson_content_service.load_course_full(
        db, course_id=course_id
    )
    if course is None or course.organization_id != org_id:
        raise NotFoundError("Corso non trovato.", code="course_not_found")
    lesson = await course_lesson_content_service.get_lesson_or_404(
        db, course=course, lesson_id=lesson_id
    )
    if lesson.pdf_status != "ready" or not lesson.pdf_path:
        raise NotFoundError(
            "PDF non disponibile per questa lezione.", code="pdf_not_ready"
        )
    abs_path = course_lesson_pdf_service.pdf_absolute_path(lesson.pdf_path)
    if not abs_path.is_file():
        raise NotFoundError(
            "File PDF mancante sul filesystem.", code="pdf_file_missing"
        )
    filename = course_lesson_pdf_service.pdf_filename_for_download(
        course.title, lesson
    )
    return FileResponse(
        path=str(abs_path),
        media_type="application/pdf",
        filename=filename,
    )


# ---------------------------------------------------------------------------
# Fase 4 §7 — Export PDF SLIDE
# ---------------------------------------------------------------------------


@router.post(
    "/{course_id}/lessons/{lesson_id}/slides-pdf/export",
    response_model=CourseOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def export_lesson_slides_pdf(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    pdf_template_id: Annotated[uuid.UUID | None, Query()] = None,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Avvia l'export PDF delle slide di una singola lezione (Fase 4).
    Imposta `lesson.slides_pdf_status='pending'`. Vincoli: la lezione
    deve avere `slides_status` ∈ ready/approved.
    """
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    lesson = await course_lesson_slides_service.get_lesson_or_404(
        db, course=course, lesson_id=lesson_id
    )
    course = await course_lesson_slides_pdf_service.request_lesson_slides_pdf(
        db,
        course=course,
        lesson=lesson,
        actor_id=current.id,
        pdf_template_id=pdf_template_id,
    )
    return CourseOut.model_validate(course)


@router.post(
    "/{course_id}/lessons-slides-pdf/export-all",
    response_model=CourseOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def export_all_lessons_slides_pdf(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    pdf_template_id: Annotated[uuid.UUID | None, Query()] = None,
    only_missing: Annotated[bool, Query()] = False,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Avvia l'export PDF slide per TUTTE le lezioni esportabili.

    Se `only_missing=true`, esclude le lezioni con PDF slide già pronto.
    """
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    course = await course_lesson_slides_pdf_service.request_all_lessons_slides_pdf(
        db,
        course=course,
        actor_id=current.id,
        pdf_template_id=pdf_template_id,
        only_missing=only_missing,
    )
    return CourseOut.model_validate(course)


@router.post(
    "/{course_id}/lessons-slides-pdf/cancel-all",
    response_model=CourseOut,
)
async def cancel_all_lessons_slides_pdf(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Annulla tutti gli export PDF slide in flight."""
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    course = await course_lesson_slides_pdf_service.cancel_all_slides_pdf_exports(
        db, course=course, actor_id=current.id
    )
    return CourseOut.model_validate(course)


@router.get(
    "/{course_id}/lessons/{lesson_id}/slides-pdf/download",
)
async def download_lesson_slides_pdf(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_VIEW),
) -> FileResponse:
    """Scarica il PDF slide. 404 se non disponibile."""
    await _ensure_org(db, org_id)
    course = await course_lesson_slides_service.load_course_full(
        db, course_id=course_id
    )
    if course is None or course.organization_id != org_id:
        raise NotFoundError("Corso non trovato.", code="course_not_found")
    lesson = await course_lesson_slides_service.get_lesson_or_404(
        db, course=course, lesson_id=lesson_id
    )
    if lesson.slides_pdf_status != "ready" or not lesson.slides_pdf_path:
        raise NotFoundError(
            "PDF slide non disponibile per questa lezione.",
            code="slides_pdf_not_ready",
        )
    abs_path = course_lesson_pdf_service.pdf_absolute_path(
        lesson.slides_pdf_path
    )
    if not abs_path.is_file():
        raise NotFoundError(
            "File PDF slide mancante sul filesystem.",
            code="slides_pdf_file_missing",
        )
    filename = course_lesson_slides_pdf_service.slides_pdf_filename_for_download(
        course.title, lesson
    )
    return FileResponse(
        path=str(abs_path),
        media_type="application/pdf",
        filename=filename,
    )


# ---------------------------------------------------------------------------
# §8 — Export PDF discorso
# ---------------------------------------------------------------------------


@router.post(
    "/{course_id}/lessons/{lesson_id}/speech-pdf/export",
    response_model=CourseOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def export_lesson_speech_pdf(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    pdf_template_id: Annotated[uuid.UUID | None, Query()] = None,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Avvia l'export PDF del discorso di una singola lezione (Fase 5).
    Imposta `lesson.speech_pdf_status='pending'`. Vincoli: la lezione
    deve avere `speech_status` ∈ ready/approved.
    """
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    lesson = await course_lesson_speech_service.get_lesson_or_404(
        db, course=course, lesson_id=lesson_id
    )
    course = await course_lesson_speech_pdf_service.request_lesson_speech_pdf(
        db,
        course=course,
        lesson=lesson,
        actor_id=current.id,
        pdf_template_id=pdf_template_id,
    )
    return CourseOut.model_validate(course)


@router.post(
    "/{course_id}/lessons-speech-pdf/export-all",
    response_model=CourseOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def export_all_lessons_speech_pdf(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    pdf_template_id: Annotated[uuid.UUID | None, Query()] = None,
    only_missing: Annotated[bool, Query()] = False,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Avvia l'export PDF discorso per TUTTE le lezioni esportabili.

    Se `only_missing=true`, esclude le lezioni con PDF discorso già pronto.
    """
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    course = await course_lesson_speech_pdf_service.request_all_lessons_speech_pdf(
        db,
        course=course,
        actor_id=current.id,
        pdf_template_id=pdf_template_id,
        only_missing=only_missing,
    )
    return CourseOut.model_validate(course)


@router.post(
    "/{course_id}/lessons-speech-pdf/cancel-all",
    response_model=CourseOut,
)
async def cancel_all_lessons_speech_pdf(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Annulla tutti gli export PDF discorso in flight."""
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    course = await course_lesson_speech_pdf_service.cancel_all_speech_pdf_exports(
        db, course=course, actor_id=current.id
    )
    return CourseOut.model_validate(course)


@router.get(
    "/{course_id}/lessons/{lesson_id}/speech-pdf/download",
)
async def download_lesson_speech_pdf(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_VIEW),
) -> FileResponse:
    """Scarica il PDF discorso. 404 se non disponibile."""
    await _ensure_org(db, org_id)
    course = await course_lesson_speech_service.load_course_full(
        db, course_id=course_id
    )
    if course is None or course.organization_id != org_id:
        raise NotFoundError("Corso non trovato.", code="course_not_found")
    lesson = await course_lesson_speech_service.get_lesson_or_404(
        db, course=course, lesson_id=lesson_id
    )
    if lesson.speech_pdf_status != "ready" or not lesson.speech_pdf_path:
        raise NotFoundError(
            "PDF discorso non disponibile per questa lezione.",
            code="speech_pdf_not_ready",
        )
    abs_path = course_lesson_pdf_service.pdf_absolute_path(
        lesson.speech_pdf_path
    )
    if not abs_path.is_file():
        raise NotFoundError(
            "File PDF discorso mancante sul filesystem.",
            code="speech_pdf_file_missing",
        )
    filename = course_lesson_speech_pdf_service.speech_pdf_filename_for_download(
        course.title, lesson
    )
    return FileResponse(
        path=str(abs_path),
        media_type="application/pdf",
        filename=filename,
    )


# ---------------------------------------------------------------------------
# Lesson assets (upload immagine + Vision API image→Mermaid)
# ---------------------------------------------------------------------------
# L'editor lezione permette di caricare un'immagine come asset visivo. Due
# scelte successive all'upload (UX lato frontend):
#   1) "Mantieni come immagine" → l'asset rimane `format=image` con
#      `content` = path pubblico relativo (es. `lesson_assets/{cid}/{uuid}.png`).
#   2) "Digitalizza in Mermaid" → backend chiama OpenAI Vision; se il modello
#      produce codice Mermaid valido, l'editor sostituisce localmente
#      `format=mermaid` + `content=<codice>`. L'immagine fisica viene poi
#      pulita al successivo salvataggio dal cleanup orfani in
#      `update_lesson_content_raw`.


class _LessonAssetConvertInput(BaseModel):
    """Body del POST /lesson-assets/convert-to-mermaid."""

    path: str = Field(min_length=1, max_length=500)


class _LessonAssetUploadOut(BaseModel):
    path: str
    url: str


class _LessonAssetConvertOut(BaseModel):
    mermaid_code: str
    usage: dict[str, Any]


def _validate_lesson_asset_path(path: str, *, course_id: uuid.UUID) -> str:
    """Verifica che `path` sia un asset di QUESTO corso (no cross-tenant
    leak). Accetta sia il path relativo (`lesson_assets/{cid}/{uuid}.ext`)
    sia il path pubblico (`/uploads/lesson_assets/...`) — normalizza al
    relativo."""
    rel = path.removeprefix("/uploads/").lstrip("/")
    expected_prefix = f"lesson_assets/{course_id}/"
    if not rel.startswith(expected_prefix):
        raise NotFoundError(
            "Asset non trovato in questo corso.",
            code="lesson_asset_not_in_course",
        )
    # Niente segmenti `..` o doppi slash.
    parts = rel.split("/")
    if any(p in {"", ".", ".."} for p in parts):
        raise NotFoundError(
            "Path asset non valido.", code="invalid_lesson_asset_path"
        )
    return rel


@router.post(
    "/{course_id}/lesson-assets/upload",
    response_model=_LessonAssetUploadOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_lesson_asset(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    file: Annotated[UploadFile, File(...)],
    _=require(P.COURSE_EDIT),
) -> _LessonAssetUploadOut:
    """Carica un'immagine come asset visivo per il corso.

    Validazione MIME (jpg/png/webp) + size (5 MB max) + ri-encoding via
    Pillow (strip EXIF). Salvataggio in `lesson_assets/{course_id}/{uuid}.{ext}`.
    Ritorna `{ path, url }` da inserire nell'asset visivo come
    `content = path` + `format = "image"`.
    """
    await _ensure_org(db, org_id)
    granted = await resolve_permissions(
        db, user=current, organization_id=org_id
    )
    # Verifica visibilità + esistenza corso (404 silenzioso se non visto).
    await course_service.get_course(
        db,
        organization_id=org_id,
        course_id=course_id,
        current_user=current,
        granted_permissions=granted,
    )
    public_path = await file_service.save_upload_image(
        file,
        subdir=f"lesson_assets/{course_id}",
        max_dimension=2400,
    )
    rel = public_path.removeprefix("/uploads/")
    return _LessonAssetUploadOut(path=rel, url=public_path)


@router.post(
    "/{course_id}/lesson-assets/convert-to-mermaid",
    response_model=_LessonAssetConvertOut,
)
async def convert_lesson_asset_to_mermaid(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    payload: _LessonAssetConvertInput,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_EDIT),
) -> _LessonAssetConvertOut:
    """Trasforma un'immagine caricata in codice Mermaid via OpenAI Vision.

    Pre-condition: il path deve essere un asset di questo corso (cross-tenant
    isolation). Errori: `409 image_to_mermaid_failed` se il modello produce
    output non valido / UNRECOGNIZED, oppure se la API key OpenAI non è
    configurata.
    """
    await _ensure_org(db, org_id)
    granted = await resolve_permissions(
        db, user=current, organization_id=org_id
    )
    course = await course_service.get_course(
        db,
        organization_id=org_id,
        course_id=course_id,
        current_user=current,
        granted_permissions=granted,
    )
    rel = _validate_lesson_asset_path(payload.path, course_id=course_id)
    settings = get_settings()
    target = settings.upload_root / rel
    if not target.is_file():
        raise NotFoundError(
            "File asset mancante sul filesystem.",
            code="lesson_asset_file_missing",
        )
    # Inferenza MIME dall'estensione (i file passati dal nostro save_upload_image
    # sono già normalizzati a .png/.jpg/.webp).
    ext = target.suffix.lower()
    mime_by_ext = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }
    mime_type = mime_by_ext.get(ext)
    if mime_type is None:
        raise ConflictError(
            f"Estensione immagine non supportata: {ext}",
            code="lesson_asset_unsupported_ext",
        )
    image_bytes = target.read_bytes()
    try:
        mermaid_code, usage = await convert_image_to_mermaid(
            image_bytes=image_bytes,
            mime_type=mime_type,
            language_code=course.language_code,
        )
    except OpenAINotConfiguredError as exc:
        raise ConflictError(
            "OpenAI non è configurato sul server.",
            code="openai_not_configured",
        ) from exc
    except OpenAIImageToMermaidError as exc:
        raise ConflictError(
            exc.message,
            code="image_to_mermaid_failed",
        ) from exc
    return _LessonAssetConvertOut(mermaid_code=mermaid_code, usage=usage)


# ---------------------------------------------------------------------------
# Bundle PDF per modulo (Contenuti / Slide / Discorso)
# ---------------------------------------------------------------------------
# Per ciascuna delle 3 pipeline PDF esistono 2 endpoint a livello di modulo:
#   - download-merged → singolo PDF concatenato (pypdf.PdfWriter.append)
#   - download-zip    → uno .zip con un PDF per ogni lezione
# Visibili in UI nei tab Contenuti / Slide / Discorso accanto al titolo del
# modulo, solo quando TUTTE le lezioni del modulo hanno il PDF in stato
# `ready`. Lato BE la pre-condizione viene ri-controllata in
# `_ensure_all_pdfs_ready` (vedi `course_module_pdf_service`).


def _content_disposition_attachment(filename: str) -> str:
    """Costruisce il valore di `Content-Disposition: attachment; ...` con
    fallback RFC 5987 per filename non-ASCII.

    Starlette serializza gli header HTTP in latin-1, quindi caratteri tipo
    em-dash (—), lettere accentate, ecc. esplodono con `UnicodeEncodeError`
    se messi direttamente nel `filename=`. La forma standard
    `filename*=UTF-8''<percent-encoded>` è supportata da tutti i browser
    moderni e ha precedenza sulla `filename=` ASCII di fallback.
    """
    # Fallback ASCII (browser molto vecchi): caratteri non-ASCII → "?".
    ascii_fallback = (
        filename.encode("ascii", errors="replace").decode("ascii").replace('"', "")
    )
    # Forma RFC 5987 (browser moderni): UTF-8 percent-encoded.
    encoded = urllib.parse.quote(filename, safe="")
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded}'


async def _load_course_module_or_404(
    db: DbSession,
    *,
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    module_id: uuid.UUID,
) -> tuple[Any, Any]:
    """Carica corso (con moduli + lezioni) e ritorna `(course, module)`.
    Usa `course_lesson_pdf_service.load_course_full` (eager-load) e
    cerca il modulo nel grafo. 404 se non trovato."""
    course = await course_lesson_pdf_service.load_course_full(
        db, course_id=course_id
    )
    if course is None or course.organization_id != org_id:
        raise NotFoundError("Corso non trovato.", code="course_not_found")
    module = next(
        (m for m in course.modules if m.id == module_id), None
    )
    if module is None:
        raise NotFoundError(
            "Modulo non trovato in questo corso.", code="module_not_found"
        )
    return course, module


def _module_pdf_response(
    *, content: bytes, filename: str, media_type: str
) -> Response:
    """Helper di risposta per i bundle modulo (merged PDF / ZIP)."""
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": _content_disposition_attachment(filename),
        },
    )


# --- Contenuti (Fase 3) -----------------------------------------------------


@router.get(
    "/{course_id}/modules/{module_id}/lessons-pdf/download-merged",
)
async def download_module_pdf_merged(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    module_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_VIEW),
) -> Response:
    """PDF unico concatenato di TUTTI i PDF lezione del modulo."""
    await _ensure_org(db, org_id)
    course, module = await _load_course_module_or_404(
        db, org_id=org_id, course_id=course_id, module_id=module_id
    )
    content = course_module_pdf_service.merge_module_pdfs(
        kind="content", course=course, module=module
    )
    filename = course_module_pdf_service.module_merged_filename(
        "content", course, module
    )
    return _module_pdf_response(
        content=content, filename=filename, media_type="application/pdf"
    )


@router.get(
    "/{course_id}/modules/{module_id}/lessons-pdf/download-zip",
)
async def download_module_pdf_zip(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    module_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_VIEW),
) -> Response:
    """ZIP con un PDF per ogni lezione del modulo."""
    await _ensure_org(db, org_id)
    course, module = await _load_course_module_or_404(
        db, org_id=org_id, course_id=course_id, module_id=module_id
    )
    content = course_module_pdf_service.zip_module_pdfs(
        kind="content", course=course, module=module
    )
    filename = course_module_pdf_service.module_zip_filename(
        "content", course, module
    )
    return _module_pdf_response(
        content=content, filename=filename, media_type="application/zip"
    )


# --- Slide (Fase 4) ---------------------------------------------------------


@router.get(
    "/{course_id}/modules/{module_id}/lessons-slides-pdf/download-merged",
)
async def download_module_slides_pdf_merged(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    module_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_VIEW),
) -> Response:
    await _ensure_org(db, org_id)
    course, module = await _load_course_module_or_404(
        db, org_id=org_id, course_id=course_id, module_id=module_id
    )
    content = course_module_pdf_service.merge_module_pdfs(
        kind="slides", course=course, module=module
    )
    filename = course_module_pdf_service.module_merged_filename(
        "slides", course, module
    )
    return _module_pdf_response(
        content=content, filename=filename, media_type="application/pdf"
    )


@router.get(
    "/{course_id}/modules/{module_id}/lessons-slides-pdf/download-zip",
)
async def download_module_slides_pdf_zip(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    module_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_VIEW),
) -> Response:
    await _ensure_org(db, org_id)
    course, module = await _load_course_module_or_404(
        db, org_id=org_id, course_id=course_id, module_id=module_id
    )
    content = course_module_pdf_service.zip_module_pdfs(
        kind="slides", course=course, module=module
    )
    filename = course_module_pdf_service.module_zip_filename(
        "slides", course, module
    )
    return _module_pdf_response(
        content=content, filename=filename, media_type="application/zip"
    )


# --- Discorso (Fase 5) ------------------------------------------------------


@router.get(
    "/{course_id}/modules/{module_id}/lessons-speech-pdf/download-merged",
)
async def download_module_speech_pdf_merged(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    module_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_VIEW),
) -> Response:
    await _ensure_org(db, org_id)
    course, module = await _load_course_module_or_404(
        db, org_id=org_id, course_id=course_id, module_id=module_id
    )
    content = course_module_pdf_service.merge_module_pdfs(
        kind="speech", course=course, module=module
    )
    filename = course_module_pdf_service.module_merged_filename(
        "speech", course, module
    )
    return _module_pdf_response(
        content=content, filename=filename, media_type="application/pdf"
    )


@router.get(
    "/{course_id}/modules/{module_id}/lessons-speech-pdf/download-zip",
)
async def download_module_speech_pdf_zip(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    module_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_VIEW),
) -> Response:
    await _ensure_org(db, org_id)
    course, module = await _load_course_module_or_404(
        db, org_id=org_id, course_id=course_id, module_id=module_id
    )
    content = course_module_pdf_service.zip_module_pdfs(
        kind="speech", course=course, module=module
    )
    filename = course_module_pdf_service.module_zip_filename(
        "speech", course, module
    )
    return _module_pdf_response(
        content=content, filename=filename, media_type="application/zip"
    )


# ---------------------------------------------------------------------------
# Fase 6 — Generazione video MP4 (§9)
# ---------------------------------------------------------------------------


async def _video_assignee_context(db, course: object) -> dict[str, Any]:
    """Helper: risolve il campione vocale dell'assegnatario per costruire
    i DTO video."""
    voice_sample_path = (
        await course_lesson_video_service.resolve_voice_sample_path(
            db, assignee_user_id=course.assignee_user_id
        )
    )
    return {
        "voice_sample_path": voice_sample_path,
        "voice_sample_available": voice_sample_path is not None,
    }


@router.post(
    "/{course_id}/lessons/{lesson_id}/video/generate",
    response_model=LessonVideoStatusOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_lesson_video(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    payload: LessonVideoGenerateInput,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> LessonVideoStatusOut:
    """Trigger generazione video MP4 (TTS XTTS-v2 + slide PNG + ffmpeg).

    Pre-condizioni: `speech_status='approved'` AND `slides_status='approved'`
    AND `Avatar.audio_path` dell'assegnatario presente. 409 con codice
    specifico (`speech_not_approved`/`slides_not_approved`/`voice_sample_missing`)
    se manca un pre-requisito.
    """
    _ = payload  # input vuoto (riservato per future opzioni preset)
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    lesson = await course_lesson_video_service.get_lesson_or_404(
        course=course, lesson_id=lesson_id
    )
    ctx = await _video_assignee_context(db, course)
    lesson = await course_lesson_video_service.request_lesson_video(
        db,
        course=course,
        lesson=lesson,
        actor_id=current.id,
        voice_sample_path=ctx["voice_sample_path"],
    )
    return course_lesson_video_service.build_status_out(
        lesson,
        voice_sample_available=ctx["voice_sample_available"],
    )


@router.post(
    "/{course_id}/lessons-video/generate-batch",
    response_model=LessonVideoBatchOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_all_lessons_video(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> LessonVideoBatchOut:
    """Trigger batch: marca come `pending` tutte le lezioni eleggibili
    (speech+slides approved AND video non già in flight). Il worker le
    elabora una alla volta (cap default 1, configurabile)."""
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    ctx = await _video_assignee_context(db, course)
    await course_lesson_video_service.request_all_lessons_video(
        db,
        course=course,
        actor_id=current.id,
        voice_sample_path=ctx["voice_sample_path"],
    )
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    ctx = await _video_assignee_context(db, course)
    return course_lesson_video_service.build_batch_out(
        course,
        voice_sample_available=ctx["voice_sample_available"],
    )


@router.post(
    "/{course_id}/lessons/{lesson_id}/video/cancel",
    response_model=LessonVideoStatusOut,
)
async def cancel_lesson_video(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> LessonVideoStatusOut:
    """Annulla la generazione di una singola lezione (`pending`/`processing`
    → `cancelled`). Idempotente."""
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    lesson = await course_lesson_video_service.get_lesson_or_404(
        course=course, lesson_id=lesson_id
    )
    lesson = await course_lesson_video_service.cancel_lesson_video(
        db, course=course, lesson=lesson, actor_id=current.id
    )
    ctx = await _video_assignee_context(db, course)
    return course_lesson_video_service.build_status_out(
        lesson,
        voice_sample_available=ctx["voice_sample_available"],
    )


@router.post(
    "/{course_id}/lessons-video/cancel-batch",
    response_model=LessonVideoBatchOut,
)
async def cancel_all_lessons_video(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> LessonVideoBatchOut:
    """Annulla la generazione di tutte le lezioni in flight."""
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    await course_lesson_video_service.cancel_all_lesson_videos(
        db, course=course, actor_id=current.id
    )
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    ctx = await _video_assignee_context(db, course)
    return course_lesson_video_service.build_batch_out(
        course,
        voice_sample_available=ctx["voice_sample_available"],
    )


@router.get(
    "/{course_id}/lessons/{lesson_id}/video/status",
    response_model=LessonVideoStatusOut,
)
async def get_lesson_video_status(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_VIEW),
) -> LessonVideoStatusOut:
    """Polling-friendly status di una singola lezione (usato dal FE per
    aggiornare progress bar + ETA mentre il worker lavora)."""
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    lesson = await course_lesson_video_service.get_lesson_or_404(
        course=course, lesson_id=lesson_id
    )
    ctx = await _video_assignee_context(db, course)
    return course_lesson_video_service.build_status_out(
        lesson,
        voice_sample_available=ctx["voice_sample_available"],
    )


@router.get(
    "/{course_id}/lessons-video/status",
    response_model=LessonVideoBatchOut,
)
async def get_course_videos_status(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_VIEW),
) -> LessonVideoBatchOut:
    """Aggregato pagina-corso: contatori + items per costruire la tab
    Video del CourseEditorPage."""
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    ctx = await _video_assignee_context(db, course)
    return course_lesson_video_service.build_batch_out(
        course,
        voice_sample_available=ctx["voice_sample_available"],
    )


# ---------------------------------------------------------------------------
# Fase 6b — Video con Avatar (lip-sync MuseTalk) (§9b)
# ---------------------------------------------------------------------------


async def _avatar_video_context(db, course: object) -> dict[str, Any]:
    """Helper: risolve l'avatar dell'assegnatario per costruire i DTO
    del «Video con Avatar»."""
    avatar = None
    if course.assignee_user_id is not None:
        avatar = await course_lesson_avatar_video_service.resolve_assignee_avatar(
            db, assignee_user_id=course.assignee_user_id
        )
    return {
        "avatar": avatar,
        "avatar_clips_ready": course_lesson_avatar_video_service.avatar_is_ready(
            avatar
        ),
    }


@router.post(
    "/{course_id}/lessons/{lesson_id}/avatar-video/generate",
    response_model=LessonAvatarVideoStatusOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_lesson_avatar_video(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    payload: LessonAvatarVideoGenerateInput,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> LessonAvatarVideoStatusOut:
    """Trigger generazione del «Video con Avatar»: il lip-sync MuseTalk
    dell'avatar sovrapposto al video MP4 già generato della lezione.

    Pre-condizioni: `video_status='ready'` AND l'avatar dell'assegnatario
    ha ≥ 1 clip pronta. 409 con codice specifico
    (`lesson_video_not_ready`/`avatar_clips_not_ready`) se manca un
    pre-requisito.
    """
    _ = payload  # input vuoto (riservato per future opzioni)
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    lesson = await course_lesson_avatar_video_service.get_lesson_or_404(
        course=course, lesson_id=lesson_id
    )
    ctx = await _avatar_video_context(db, course)
    lesson = await course_lesson_avatar_video_service.request_lesson_avatar_video(
        db,
        course=course,
        lesson=lesson,
        actor_id=current.id,
        avatar=ctx["avatar"],
    )
    return course_lesson_avatar_video_service.build_status_out(
        lesson,
        avatar_clips_ready=ctx["avatar_clips_ready"],
    )


@router.post(
    "/{course_id}/lessons-avatar-video/generate-batch",
    response_model=LessonAvatarVideoBatchOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_all_lessons_avatar_video(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> LessonAvatarVideoBatchOut:
    """Trigger batch: marca come `pending` tutte le lezioni eleggibili
    (video della lezione `ready` AND avatar con clip pronte AND non già
    in flight). Il worker le elabora una alla volta."""
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    ctx = await _avatar_video_context(db, course)
    await course_lesson_avatar_video_service.request_all_lessons_avatar_video(
        db,
        course=course,
        actor_id=current.id,
        avatar=ctx["avatar"],
    )
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    ctx = await _avatar_video_context(db, course)
    return course_lesson_avatar_video_service.build_batch_out(
        course,
        avatar_clips_ready=ctx["avatar_clips_ready"],
    )


@router.post(
    "/{course_id}/lessons/{lesson_id}/avatar-video/cancel",
    response_model=LessonAvatarVideoStatusOut,
)
async def cancel_lesson_avatar_video(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> LessonAvatarVideoStatusOut:
    """Annulla la generazione di una singola lezione (`pending`/`processing`
    → `cancelled`). Idempotente."""
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    lesson = await course_lesson_avatar_video_service.get_lesson_or_404(
        course=course, lesson_id=lesson_id
    )
    lesson = await course_lesson_avatar_video_service.cancel_lesson_avatar_video(
        db, course=course, lesson=lesson, actor_id=current.id
    )
    ctx = await _avatar_video_context(db, course)
    return course_lesson_avatar_video_service.build_status_out(
        lesson,
        avatar_clips_ready=ctx["avatar_clips_ready"],
    )


@router.post(
    "/{course_id}/lessons-avatar-video/cancel-batch",
    response_model=LessonAvatarVideoBatchOut,
)
async def cancel_all_lessons_avatar_video(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_GENERATE),
) -> LessonAvatarVideoBatchOut:
    """Annulla la generazione di tutte le lezioni in flight."""
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    await course_lesson_avatar_video_service.cancel_all_lesson_avatar_videos(
        db, course=course, actor_id=current.id
    )
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    ctx = await _avatar_video_context(db, course)
    return course_lesson_avatar_video_service.build_batch_out(
        course,
        avatar_clips_ready=ctx["avatar_clips_ready"],
    )


@router.get(
    "/{course_id}/lessons/{lesson_id}/avatar-video/status",
    response_model=LessonAvatarVideoStatusOut,
)
async def get_lesson_avatar_video_status(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_VIEW),
) -> LessonAvatarVideoStatusOut:
    """Polling-friendly status di una singola lezione."""
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    lesson = await course_lesson_avatar_video_service.get_lesson_or_404(
        course=course, lesson_id=lesson_id
    )
    ctx = await _avatar_video_context(db, course)
    return course_lesson_avatar_video_service.build_status_out(
        lesson,
        avatar_clips_ready=ctx["avatar_clips_ready"],
    )


@router.get(
    "/{course_id}/lessons-avatar-video/status",
    response_model=LessonAvatarVideoBatchOut,
)
async def get_course_avatar_videos_status(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_VIEW),
) -> LessonAvatarVideoBatchOut:
    """Aggregato pagina-corso: contatori + items per la scheda
    «Video con Avatar» del CourseEditorPage."""
    await _ensure_org(db, org_id)
    course = await _load_course_for_edit(
        db, org_id=org_id, course_id=course_id, current=current
    )
    ctx = await _avatar_video_context(db, course)
    return course_lesson_avatar_video_service.build_batch_out(
        course,
        avatar_clips_ready=ctx["avatar_clips_ready"],
    )
