from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.errors import NotFoundError
from app.models.organization import Organization
from app.models.organization_course_settings import OrganizationCourseSettings
from app.schemas.organization import OrganizationBase


async def list_organizations(
    db: AsyncSession, *, page: int, page_size: int, q: str | None = None
) -> tuple[list[Organization], int]:
    base = select(Organization).where(Organization.deleted_at.is_(None))
    if q:
        like = f"%{q.strip()}%"
        base = base.where(Organization.name.ilike(like))
    count_q = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_q)).scalar_one()
    items_q = base.order_by(Organization.name.asc()).offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(items_q)).scalars().all()
    return list(items), int(total)


async def get_organization(db: AsyncSession, org_id: uuid.UUID) -> Organization:
    org = await db.get(Organization, org_id)
    if org is None or org.deleted_at is not None:
        raise NotFoundError("Organizzazione non trovata.", code="organization_not_found")
    return org


async def create_organization(
    db: AsyncSession,
    *,
    payload: OrganizationBase,
    logo_path: str | None,
    actor_id: uuid.UUID,
) -> Organization:
    org = Organization(
        **payload.model_dump(),
        logo_path=logo_path,
        created_by_user_id=actor_id,
    )
    db.add(org)
    await db.flush()
    db.add(OrganizationCourseSettings(organization_id=org.id))
    await db.flush()
    await write_audit(
        db,
        action="organization.create",
        actor_user_id=actor_id,
        organization_id=org.id,
        target_type="organization",
        target_id=str(org.id),
        metadata={"name": org.name, "email": org.email},
    )
    return org


async def update_organization(
    db: AsyncSession,
    *,
    org: Organization,
    payload: OrganizationBase,
    actor_id: uuid.UUID,
    new_logo_path: str | None = None,
) -> Organization:
    for k, v in payload.model_dump().items():
        setattr(org, k, v)
    if new_logo_path is not None:
        org.logo_path = new_logo_path
    await db.flush()
    # Refresh per garantire che server_default/onupdate (es. updated_at)
    # siano popolati prima della serializzazione Pydantic in async context.
    await db.refresh(org)
    await write_audit(
        db,
        action="organization.update",
        actor_user_id=actor_id,
        organization_id=org.id,
        target_type="organization",
        target_id=str(org.id),
    )
    return org


async def soft_delete_organization(
    db: AsyncSession, *, org: Organization, actor_id: uuid.UUID
) -> None:
    from datetime import UTC, datetime

    org.deleted_at = datetime.now(tz=UTC)
    await db.flush()
    await write_audit(
        db,
        action="organization.delete",
        actor_user_id=actor_id,
        organization_id=org.id,
        target_type="organization",
        target_id=str(org.id),
    )
