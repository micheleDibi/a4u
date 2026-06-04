from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select

from app.core.audit import write_audit
from app.core.deps import DbSession, PlatformAdmin
from app.core.errors import ConflictError
from app.core.security import hash_password
from app.models.role import OrganizationRole
from app.models.user import User
from app.schemas.common import Page, PageMeta
from app.schemas.membership import EnrollUserRequest, MembershipOut
from app.schemas.user import (
    UserAdminSetPassword,
    UserCreateAdmin,
    UserOut,
    UserUpdateAdmin,
)
from app.services import membership_service, user_admin_service

router = APIRouter(prefix="/admin", tags=["admin-users"])


@router.get("/users", response_model=Page[UserOut])
async def list_users(
    db: DbSession,
    _: PlatformAdmin,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 25,
    q: str | None = Query(default=None, max_length=120),
) -> Page[UserOut]:
    base = select(User)
    if q:
        like = f"%{q.strip()}%"
        base = base.where((User.email.ilike(like)) | (User.full_name.ilike(like)))
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    items_q = base.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(items_q)).scalars().all()
    return Page[UserOut](
        items=[UserOut.model_validate(u) for u in items],
        meta=PageMeta(page=page, page_size=page_size, total=int(total)),
    )


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(
    payload: UserCreateAdmin, request: Request, db: DbSession, admin: PlatformAdmin
) -> UserOut:
    email = payload.email.lower().strip()
    existing = (
        await db.execute(select(User).where(func.lower(User.email) == email))
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("Email già in uso.", code="email_in_use")
    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        is_platform_admin=payload.is_platform_admin,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    await write_audit(
        db,
        action="user.create",
        actor_user_id=admin.id,
        target_type="user",
        target_id=str(user.id),
        metadata={"is_platform_admin": payload.is_platform_admin},
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return UserOut.model_validate(user)


@router.put("/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdateAdmin,
    request: Request,
    db: DbSession,
    admin: PlatformAdmin,
) -> UserOut:
    user = await user_admin_service.update_user_admin(
        db,
        user_id=user_id,
        payload=payload,
        actor=admin,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return UserOut.model_validate(user)


@router.post("/users/{user_id}/password", response_model=UserOut)
async def set_user_password(
    user_id: uuid.UUID,
    payload: UserAdminSetPassword,
    request: Request,
    db: DbSession,
    admin: PlatformAdmin,
) -> UserOut:
    user = await user_admin_service.set_user_password(
        db,
        user_id=user_id,
        new_password=payload.password,
        actor=admin,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return UserOut.model_validate(user)


@router.post(
    "/organizations/{org_id}/memberships",
    response_model=MembershipOut,
    status_code=201,
)
async def enroll_user_in_org(
    org_id: uuid.UUID,
    payload: EnrollUserRequest,
    db: DbSession,
    admin: PlatformAdmin,
) -> MembershipOut:
    membership = await membership_service.enroll_user(
        db,
        user_id=payload.user_id,
        organization_id=org_id,
        role_code=payload.role_code,
        actor_id=admin.id,
    )
    user = await db.get(User, membership.user_id)
    role = await db.get(OrganizationRole, membership.role_id)
    return MembershipOut(
        id=membership.id,
        user_id=membership.user_id,
        user_email=user.email,  # type: ignore[union-attr]
        user_full_name=user.full_name,  # type: ignore[union-attr]
        organization_id=membership.organization_id,
        role_id=membership.role_id,
        role_code=role.code,  # type: ignore[union-attr]
        role_name_it=role.name_it,  # type: ignore[union-attr]
        joined_at=membership.joined_at,
    )
