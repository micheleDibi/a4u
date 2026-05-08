from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import EmailStr

from app.core.deps import DbSession, PlatformAdmin
from app.schemas.common import Page, PageMeta
from app.schemas.organization import OrganizationBase, OrganizationOut
from app.services import org_service
from app.services.file_service import save_upload_image

router = APIRouter(prefix="/admin/organizations", tags=["admin-organizations"])


@router.get("", response_model=Page[OrganizationOut])
async def list_organizations(
    db: DbSession,
    _: PlatformAdmin,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 25,
    q: str | None = Query(default=None, max_length=120),
) -> Page[OrganizationOut]:
    items, total = await org_service.list_organizations(db, page=page, page_size=page_size, q=q)
    return Page[OrganizationOut](
        items=[OrganizationOut.model_validate(it) for it in items],
        meta=PageMeta(page=page, page_size=page_size, total=total),
    )


@router.get("/{org_id}", response_model=OrganizationOut)
async def get_organization(org_id: uuid.UUID, db: DbSession, _: PlatformAdmin) -> OrganizationOut:
    org = await org_service.get_organization(db, org_id)
    return OrganizationOut.model_validate(org)


def _form_organization(
    name: Annotated[str, Form(...)],
    email: Annotated[EmailStr, Form(...)],
    phone: Annotated[str | None, Form()] = None,
    website: Annotated[str | None, Form()] = None,
    vat_number: Annotated[str | None, Form()] = None,
    fiscal_code: Annotated[str | None, Form()] = None,
    country: Annotated[str | None, Form()] = None,
    address: Annotated[str | None, Form()] = None,
    city: Annotated[str | None, Form()] = None,
    province: Annotated[str | None, Form()] = None,
    postal_code: Annotated[str | None, Form()] = None,
) -> OrganizationBase:
    return OrganizationBase(
        name=name,
        email=email,
        phone=phone,
        website=website,
        vat_number=vat_number,
        fiscal_code=fiscal_code,
        country=country,
        address=address,
        city=city,
        province=province,
        postal_code=postal_code,
    )


@router.post("", response_model=OrganizationOut, status_code=201)
async def create_organization(
    db: DbSession,
    admin: PlatformAdmin,
    payload: Annotated[OrganizationBase, Depends(_form_organization)],
    logo: Annotated[UploadFile | None, File()] = None,
) -> OrganizationOut:
    logo_path = await save_upload_image(logo, subdir="organizations") if logo else None
    org = await org_service.create_organization(
        db, payload=payload, logo_path=logo_path, actor_id=admin.id
    )
    return OrganizationOut.model_validate(org)


@router.put("/{org_id}", response_model=OrganizationOut)
async def update_organization(
    org_id: uuid.UUID,
    db: DbSession,
    admin: PlatformAdmin,
    payload: Annotated[OrganizationBase, Depends(_form_organization)],
    logo: Annotated[UploadFile | None, File()] = None,
    remove_logo: Annotated[bool, Form()] = False,
) -> OrganizationOut:
    org = await org_service.get_organization(db, org_id)
    new_logo: str | None = None
    if logo:
        new_logo = await save_upload_image(logo, subdir="organizations")
    elif remove_logo:
        new_logo = None
    else:
        new_logo = org.logo_path  # mantieni
    org = await org_service.update_organization(
        db, org=org, payload=payload, actor_id=admin.id, new_logo_path=new_logo
    )
    return OrganizationOut.model_validate(org)


@router.delete("/{org_id}", status_code=204)
async def delete_organization(
    org_id: uuid.UUID, db: DbSession, admin: PlatformAdmin
) -> None:
    org = await org_service.get_organization(db, org_id)
    await org_service.soft_delete_organization(db, org=org, actor_id=admin.id)
