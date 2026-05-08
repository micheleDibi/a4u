from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.models.organization_course_settings import OrganizationCourseSettings
from app.schemas.organization_course_settings import (
    OrganizationCourseSettingsUpdate,
)


async def get_or_create_settings(
    db: AsyncSession, *, organization_id: uuid.UUID
) -> OrganizationCourseSettings:
    """Recupera i parametri corso dell'org; se mancano (org pre-esistente o
    edge case), li crea on-the-fly con i default."""
    res = await db.execute(
        select(OrganizationCourseSettings).where(
            OrganizationCourseSettings.organization_id == organization_id
        )
    )
    settings = res.scalar_one_or_none()
    if settings is not None:
        return settings
    settings = OrganizationCourseSettings(organization_id=organization_id)
    db.add(settings)
    await db.flush()
    await db.refresh(settings)
    return settings


async def update_settings(
    db: AsyncSession,
    *,
    settings: OrganizationCourseSettings,
    payload: OrganizationCourseSettingsUpdate,
    actor_id: uuid.UUID,
) -> OrganizationCourseSettings:
    """Applica un update completo (PUT) e scrive l'audit con il diff."""
    new_values = payload.model_dump()
    changes: dict[str, dict[str, object]] = {}
    for key, new_value in new_values.items():
        old_value = getattr(settings, key)
        if old_value != new_value:
            changes[key] = {"old": old_value, "new": new_value}
            setattr(settings, key, new_value)
    if changes:
        await db.flush()
        await db.refresh(settings)
        await write_audit(
            db,
            action="organization.course_settings.update",
            actor_user_id=actor_id,
            organization_id=settings.organization_id,
            target_type="organization_course_settings",
            target_id=str(settings.id),
            metadata={"changes": changes},
        )
    return settings
