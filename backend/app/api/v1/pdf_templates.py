from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.core.audit import write_audit
from app.core.deps import CurrentUser, DbSession
from app.core.permissions import P, require, require_membership
from app.schemas.template import PdfTemplateBase, PdfTemplateOut
from app.services import template_service
from app.services.file_service import delete_upload, save_upload_image

router = APIRouter(prefix="/orgs/{org_id}/templates/pdf", tags=["templates"])


def _form_pdf_template(
    name: Annotated[str, Form(...)],
    text_color: Annotated[str, Form()] = "#1F1F1F",
    primary_color: Annotated[str, Form()] = "#1976D2",
    secondary_color: Annotated[str, Form()] = "#9C27B0",
    font_family: Annotated[str, Form()] = "Roboto",
    page_size: Annotated[Literal["A4", "Letter"], Form()] = "A4",
    header_height_mm: Annotated[int, Form(ge=0, le=80)] = 20,
    footer_height_mm: Annotated[int, Form(ge=0, le=80)] = 15,
    margin_mm: Annotated[int, Form(ge=0, le=60)] = 20,
    background_opacity_pct: Annotated[int, Form(ge=0, le=100)] = 15,
) -> PdfTemplateBase:
    return PdfTemplateBase(
        name=name,
        text_color=text_color,
        primary_color=primary_color,
        secondary_color=secondary_color,
        font_family=font_family,
        page_size=page_size,
        header_height_mm=header_height_mm,
        footer_height_mm=footer_height_mm,
        margin_mm=margin_mm,
        background_opacity_pct=background_opacity_pct,
    )


@router.get("", response_model=list[PdfTemplateOut])
async def list_templates(
    org_id: uuid.UUID, db: DbSession, _=require_membership()
) -> list[PdfTemplateOut]:
    # Lettura: chiunque sia membro dell'org può listare i template (servono
    # per scegliere il template all'export PDF di lezione/discorso). La
    # gestione (create/update/delete/set-default) resta gated su
    # `template:pdf:manage`.
    items = await template_service.list_pdf_templates(db, org_id)
    return [PdfTemplateOut.model_validate(it) for it in items]


@router.post("", response_model=PdfTemplateOut, status_code=201)
async def create_template(
    org_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    payload: Annotated[PdfTemplateBase, Depends(_form_pdf_template)],
    background: Annotated[UploadFile | None, File()] = None,
    logo_left: Annotated[UploadFile | None, File()] = None,
    logo_right: Annotated[UploadFile | None, File()] = None,
    _=require(P.TEMPLATE_PDF_MANAGE),
) -> PdfTemplateOut:
    bg = await save_upload_image(background, subdir="templates") if background else None
    ll = await save_upload_image(logo_left, subdir="templates") if logo_left else None
    lr = await save_upload_image(logo_right, subdir="templates") if logo_right else None
    tpl = await template_service.create_pdf_template(
        db,
        organization_id=org_id,
        payload=payload,
        background_image_path=bg,
        logo_left_path=ll,
        logo_right_path=lr,
        actor_id=current.id,
    )
    return PdfTemplateOut.model_validate(tpl)


@router.get("/{template_id}", response_model=PdfTemplateOut)
async def get_template(
    org_id: uuid.UUID, template_id: uuid.UUID, db: DbSession, _=require_membership()
) -> PdfTemplateOut:
    tpl = await template_service.get_pdf_template(db, org_id, template_id)
    return PdfTemplateOut.model_validate(tpl)


@router.put("/{template_id}", response_model=PdfTemplateOut)
async def update_template(
    org_id: uuid.UUID,
    template_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    payload: Annotated[PdfTemplateBase, Depends(_form_pdf_template)],
    background: Annotated[UploadFile | None, File()] = None,
    logo_left: Annotated[UploadFile | None, File()] = None,
    logo_right: Annotated[UploadFile | None, File()] = None,
    remove_background: Annotated[bool, Form()] = False,
    remove_logo_left: Annotated[bool, Form()] = False,
    remove_logo_right: Annotated[bool, Form()] = False,
    _=require(P.TEMPLATE_PDF_MANAGE),
) -> PdfTemplateOut:
    tpl = await template_service.get_pdf_template(db, org_id, template_id)

    new_bg = tpl.background_image_path
    if background:
        new_bg = await save_upload_image(background, subdir="templates")
    elif remove_background:
        new_bg = None

    new_ll = tpl.logo_left_path
    if logo_left:
        new_ll = await save_upload_image(logo_left, subdir="templates")
    elif remove_logo_left:
        new_ll = None

    new_lr = tpl.logo_right_path
    if logo_right:
        new_lr = await save_upload_image(logo_right, subdir="templates")
    elif remove_logo_right:
        new_lr = None

    for k, v in payload.model_dump().items():
        setattr(tpl, k, v)

    if new_bg != tpl.background_image_path and tpl.background_image_path:
        await delete_upload(tpl.background_image_path)
    tpl.background_image_path = new_bg
    if new_ll != tpl.logo_left_path and tpl.logo_left_path:
        await delete_upload(tpl.logo_left_path)
    tpl.logo_left_path = new_ll
    if new_lr != tpl.logo_right_path and tpl.logo_right_path:
        await delete_upload(tpl.logo_right_path)
    tpl.logo_right_path = new_lr

    await db.flush()
    await db.refresh(tpl)
    await write_audit(
        db,
        action="template.pdf.update",
        actor_user_id=current.id,
        organization_id=org_id,
        target_type="pdf_template",
        target_id=str(tpl.id),
    )
    return PdfTemplateOut.model_validate(tpl)


@router.post("/{template_id}/default", response_model=PdfTemplateOut)
async def set_default_template(
    org_id: uuid.UUID,
    template_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.TEMPLATE_PDF_MANAGE),
) -> PdfTemplateOut:
    tpl = await template_service.get_pdf_template(db, org_id, template_id)
    tpl = await template_service.set_default_pdf_template(db, tpl=tpl, actor_id=current.id)
    return PdfTemplateOut.model_validate(tpl)


@router.delete("/{template_id}", status_code=204)
async def delete_template(
    org_id: uuid.UUID,
    template_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.TEMPLATE_PDF_MANAGE),
) -> None:
    tpl = await template_service.get_pdf_template(db, org_id, template_id)
    await template_service.delete_pdf_template(db, tpl=tpl, actor_id=current.id)
