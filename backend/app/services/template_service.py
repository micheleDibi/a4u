from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.errors import NotFoundError
from app.models.pdf_template import PdfTemplate
from app.models.slide_template import SlideTemplate
from app.schemas.template import PdfTemplateBase, SlideTemplateBase
from app.services.file_service import delete_upload


async def list_slide_templates(db: AsyncSession, organization_id: uuid.UUID) -> list[SlideTemplate]:
    res = await db.execute(
        select(SlideTemplate)
        .where(SlideTemplate.organization_id == organization_id)
        .order_by(SlideTemplate.name.asc())
    )
    return list(res.scalars().all())


async def list_pdf_templates(db: AsyncSession, organization_id: uuid.UUID) -> list[PdfTemplate]:
    res = await db.execute(
        select(PdfTemplate)
        .where(PdfTemplate.organization_id == organization_id)
        .order_by(PdfTemplate.name.asc())
    )
    return list(res.scalars().all())


async def get_slide_template(
    db: AsyncSession, organization_id: uuid.UUID, template_id: uuid.UUID
) -> SlideTemplate:
    tpl = await db.get(SlideTemplate, template_id)
    if tpl is None or tpl.organization_id != organization_id:
        raise NotFoundError("Template slide non trovato.", code="template_not_found")
    return tpl


async def get_pdf_template(
    db: AsyncSession, organization_id: uuid.UUID, template_id: uuid.UUID
) -> PdfTemplate:
    tpl = await db.get(PdfTemplate, template_id)
    if tpl is None or tpl.organization_id != organization_id:
        raise NotFoundError("Template PDF non trovato.", code="template_not_found")
    return tpl


async def create_slide_template(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    payload: SlideTemplateBase,
    background_image_path: str | None,
    logo_left_path: str | None,
    logo_right_path: str | None,
    actor_id: uuid.UUID,
) -> SlideTemplate:
    tpl = SlideTemplate(
        organization_id=organization_id,
        **payload.model_dump(),
        background_image_path=background_image_path,
        logo_left_path=logo_left_path,
        logo_right_path=logo_right_path,
        created_by_user_id=actor_id,
    )
    db.add(tpl)
    await db.flush()
    await db.refresh(tpl)
    await write_audit(
        db,
        action="template.slide.create",
        actor_user_id=actor_id,
        organization_id=organization_id,
        target_type="slide_template",
        target_id=str(tpl.id),
        metadata={"name": tpl.name},
    )
    return tpl


async def delete_slide_template(
    db: AsyncSession, *, tpl: SlideTemplate, actor_id: uuid.UUID
) -> None:
    await delete_upload(tpl.background_image_path)
    await delete_upload(tpl.logo_left_path)
    await delete_upload(tpl.logo_right_path)
    await db.delete(tpl)
    await db.flush()
    await write_audit(
        db,
        action="template.slide.delete",
        actor_user_id=actor_id,
        organization_id=tpl.organization_id,
        target_type="slide_template",
        target_id=str(tpl.id),
    )


async def create_pdf_template(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    payload: PdfTemplateBase,
    background_image_path: str | None,
    logo_left_path: str | None,
    logo_right_path: str | None,
    actor_id: uuid.UUID,
) -> PdfTemplate:
    tpl = PdfTemplate(
        organization_id=organization_id,
        **payload.model_dump(),
        background_image_path=background_image_path,
        logo_left_path=logo_left_path,
        logo_right_path=logo_right_path,
        created_by_user_id=actor_id,
    )
    db.add(tpl)
    await db.flush()
    await db.refresh(tpl)
    await write_audit(
        db,
        action="template.pdf.create",
        actor_user_id=actor_id,
        organization_id=organization_id,
        target_type="pdf_template",
        target_id=str(tpl.id),
        metadata={"name": tpl.name},
    )
    return tpl


async def set_default_slide_template(
    db: AsyncSession, *, tpl: SlideTemplate, actor_id: uuid.UUID
) -> SlideTemplate:
    # Azzera l'eventuale default precedente nella stessa org, poi marca questo come default.
    await db.execute(
        update(SlideTemplate)
        .where(
            SlideTemplate.organization_id == tpl.organization_id,
            SlideTemplate.id != tpl.id,
            SlideTemplate.is_default.is_(True),
        )
        .values(is_default=False)
    )
    tpl.is_default = True
    await db.flush()
    await db.refresh(tpl)
    await write_audit(
        db,
        action="template.slide.set_default",
        actor_user_id=actor_id,
        organization_id=tpl.organization_id,
        target_type="slide_template",
        target_id=str(tpl.id),
    )
    return tpl


async def set_default_pdf_template(
    db: AsyncSession, *, tpl: PdfTemplate, actor_id: uuid.UUID
) -> PdfTemplate:
    await db.execute(
        update(PdfTemplate)
        .where(
            PdfTemplate.organization_id == tpl.organization_id,
            PdfTemplate.id != tpl.id,
            PdfTemplate.is_default.is_(True),
        )
        .values(is_default=False)
    )
    tpl.is_default = True
    await db.flush()
    await db.refresh(tpl)
    await write_audit(
        db,
        action="template.pdf.set_default",
        actor_user_id=actor_id,
        organization_id=tpl.organization_id,
        target_type="pdf_template",
        target_id=str(tpl.id),
    )
    return tpl


async def delete_pdf_template(
    db: AsyncSession, *, tpl: PdfTemplate, actor_id: uuid.UUID
) -> None:
    await delete_upload(tpl.background_image_path)
    await delete_upload(tpl.logo_left_path)
    await delete_upload(tpl.logo_right_path)
    await db.delete(tpl)
    await db.flush()
    await write_audit(
        db,
        action="template.pdf.delete",
        actor_user_id=actor_id,
        organization_id=tpl.organization_id,
        target_type="pdf_template",
        target_id=str(tpl.id),
    )
