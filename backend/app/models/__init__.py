"""Modelli SQLAlchemy. Importazione centralizzata per Alembic autogenerate."""

from app.models.audit_log import AuditLog
from app.models.avatar import Avatar
from app.models.avatar_clip import AvatarClip
from app.models.avatar_clip_prompt import AvatarClipPrompt
from app.models.avatar_voice_script import AvatarVoiceScript
from app.models.course import Course
from app.models.course_document import CourseDocument
from app.models.course_lesson import CourseLesson
from app.models.course_module import CourseModule
from app.models.course_taxonomy import CourseTaxonomyTerm
from app.models.invitation import Invitation
from app.models.language import Language
from app.models.login_attempt import LoginAttempt
from app.models.membership import Membership, MembershipPermissionOverride
from app.models.organization import Organization
from app.models.organization_course_settings import OrganizationCourseSettings
from app.models.pdf_template import PdfTemplate
from app.models.permission import (
    OrganizationRolePermission,
    Permission,
    RolePermission,
)
from app.models.refresh_token import RefreshToken
from app.models.role import OrganizationRole
from app.models.slide_template import SlideTemplate
from app.models.translation import Translation
from app.models.user import User

__all__ = [
    "AuditLog",
    "Avatar",
    "AvatarClip",
    "AvatarClipPrompt",
    "AvatarVoiceScript",
    "Course",
    "CourseDocument",
    "CourseLesson",
    "CourseModule",
    "CourseTaxonomyTerm",
    "Invitation",
    "Language",
    "LoginAttempt",
    "Membership",
    "MembershipPermissionOverride",
    "Organization",
    "OrganizationCourseSettings",
    "OrganizationRole",
    "OrganizationRolePermission",
    "PdfTemplate",
    "Permission",
    "RefreshToken",
    "RolePermission",
    "SlideTemplate",
    "Translation",
    "User",
]
