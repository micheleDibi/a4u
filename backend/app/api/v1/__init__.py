from fastapi import APIRouter

from app.api.v1 import (
    admin_avatar_config,
    admin_course_taxonomy,
    admin_i18n,
    admin_metrics,
    admin_organizations,
    admin_permissions,
    admin_users,
    auth,
    course_taxonomy,
    courses,
    i18n,
    invitations,
    me_avatar,
    memberships,
    org_metrics,
    organization_course_settings,
    pdf_templates,
    slide_templates,
    system,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(system.router)
api_router.include_router(auth.router)
api_router.include_router(i18n.router)
api_router.include_router(admin_organizations.router)
api_router.include_router(admin_users.router)
api_router.include_router(admin_permissions.router)
api_router.include_router(admin_i18n.router)
api_router.include_router(admin_avatar_config.router)
api_router.include_router(admin_course_taxonomy.router)
api_router.include_router(admin_metrics.router)
api_router.include_router(memberships.router)
api_router.include_router(org_metrics.router)
api_router.include_router(invitations.router)
api_router.include_router(slide_templates.router)
api_router.include_router(pdf_templates.router)
api_router.include_router(organization_course_settings.router)
api_router.include_router(course_taxonomy.router)
api_router.include_router(courses.router)
api_router.include_router(me_avatar.router)
