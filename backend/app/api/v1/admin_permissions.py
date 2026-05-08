from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.deps import DbSession, PlatformAdmin
from app.core.permissions import ALL_PERMISSION_CODES, ROLE_NAME_IT
from app.schemas.membership import RolePermissionDefaultUpdate
from app.services import permission_service

router = APIRouter(prefix="/admin/permissions", tags=["admin-permissions"])


@router.get("/permissions")
async def list_permission_catalog(_: PlatformAdmin) -> dict[str, object]:
    return {
        "permissions": list(ALL_PERMISSION_CODES),
        "roles": [{"code": k, "name_it": v} for k, v in ROLE_NAME_IT.items()],
    }


@router.get("/role-defaults")
async def get_role_defaults(
    db: DbSession,
    _: PlatformAdmin,
    role_code: str = Query(..., min_length=1, max_length=40),
) -> dict[str, object]:
    perms = await permission_service.get_role_default_permissions(db, role_code=role_code)
    return {"role_code": role_code, "permissions": perms}


@router.put("/role-defaults")
async def update_role_defaults(
    payload: RolePermissionDefaultUpdate,
    db: DbSession,
    admin: PlatformAdmin,
) -> dict[str, str]:
    await permission_service.update_role_default_permissions(
        db, payload=payload, actor_id=admin.id
    )
    return {"status": "ok"}
