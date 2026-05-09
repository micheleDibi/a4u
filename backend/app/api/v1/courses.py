from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, File, Query, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession
from app.core.errors import NotFoundError
from app.core.permissions import P, require, resolve_permissions
from app.models.membership import Membership
from app.models.organization import Organization
from app.schemas.common import Page, PageMeta
from app.schemas.course import (
    CourseAssigneeUpdateInput,
    CourseCreateInput,
    CourseDocumentDetailOut,
    CourseDocumentOut,
    CourseListItemOut,
    CourseOut,
    CourseStatus,
    CourseUpdateInput,
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
    LessonContentGenerateInput,
    LessonContentUpdateInput,
)
from app.schemas.course_lesson_structure import (
    LessonStructureUpdateInput,
    LessonsStructureGenerateInput,
)
from app.services import (
    course_architecture_crud,
    course_architecture_service,
    course_glossary_service,
    course_lesson_content_crud,
    course_lesson_content_service,
    course_lesson_pdf_service,
    course_lesson_structure_crud,
    course_lesson_structure_service,
    course_service,
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
) -> Page[CourseListItemOut]:
    await _ensure_org(db, org_id)
    granted = await resolve_permissions(db, user=current, organization_id=org_id)
    items, total = await course_service.list_courses(
        db,
        organization_id=org_id,
        current_user=current,
        granted_permissions=granted,
        page=page,
        page_size=page_size,
        q=q,
        status=course_status,
    )
    return Page[CourseListItemOut](
        items=[CourseListItemOut.model_validate(c) for c in items],
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


@router.patch("/{course_id}", response_model=CourseOut)
async def update_course(
    org_id: uuid.UUID,
    course_id: uuid.UUID,
    payload: CourseUpdateInput,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_EDIT),
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
    _=require(P.COURSE_EDIT),
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
    _=require(P.COURSE_GENERATE),
) -> CourseOut:
    """Avvia l'export PDF per TUTTE le lezioni esportabili del corso
    (`content_status` ∈ ready/approved e `pdf_status` non in flight).

    Query param opzionale `pdf_template_id`: applica lo stesso template
    a tutte le lezioni esportabili (override). Se omesso, ogni lezione
    usa il proprio `pdf_template_id` salvato (o il default dell'org).
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
