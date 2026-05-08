from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.core.deps import CurrentUser, DbSession
from app.core.permissions import P, require
from app.schemas.organization_course_settings import (
    OrganizationCourseSettingsOut,
    OrganizationCourseSettingsUpdate,
)
from app.services import organization_course_settings_service as service

router = APIRouter(
    prefix="/orgs/{org_id}/course-settings", tags=["organizations"]
)


@router.get("", response_model=OrganizationCourseSettingsOut)
async def get_settings(
    org_id: uuid.UUID,
    db: DbSession,
    _=require(P.COURSE_CONFIG_MANAGE),
) -> OrganizationCourseSettingsOut:
    settings = await service.get_or_create_settings(db, organization_id=org_id)
    return OrganizationCourseSettingsOut.model_validate(settings)


@router.put("", response_model=OrganizationCourseSettingsOut)
async def update_settings(
    org_id: uuid.UUID,
    payload: OrganizationCourseSettingsUpdate,
    db: DbSession,
    current: CurrentUser,
    _=require(P.COURSE_CONFIG_MANAGE),
) -> OrganizationCourseSettingsOut:
    settings = await service.get_or_create_settings(db, organization_id=org_id)
    settings = await service.update_settings(
        db, settings=settings, payload=payload, actor_id=current.id
    )
    return OrganizationCourseSettingsOut.model_validate(settings)
