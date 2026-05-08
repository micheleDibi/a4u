from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.core.permissions import R
from app.core.security import generate_url_safe_token, hash_password, hash_secret, is_password_strong
from app.models.invitation import Invitation
from app.models.membership import Membership
from app.models.user import User
from app.services.membership_service import get_role_by_code

INVITATION_TTL_DAYS = 7


def _now() -> datetime:
    return datetime.now(tz=UTC)


async def create_invitation(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    email: str,
    role_code: str,
    actor_id: uuid.UUID,
) -> tuple[Invitation, str]:
    if role_code == R.CREATOR:
        raise ValidationAppError(
            "Non si può invitare un creator: usa transfer-creator dopo l'accettazione.",
            code="cannot_invite_creator",
        )
    role = await get_role_by_code(db, role_code)
    email_norm = email.strip().lower()

    raw_token = generate_url_safe_token()
    invitation = Invitation(
        organization_id=organization_id,
        email=email_norm,
        role_id=role.id,
        token_hash=hash_secret(raw_token),
        created_by_user_id=actor_id,
        expires_at=_now() + timedelta(days=INVITATION_TTL_DAYS),
    )
    db.add(invitation)
    await db.flush()
    await write_audit(
        db,
        action="invitation.create",
        actor_user_id=actor_id,
        organization_id=organization_id,
        target_type="invitation",
        target_id=str(invitation.id),
        metadata={"email": email_norm, "role": role.code},
    )
    return invitation, raw_token


async def accept_invitation(
    db: AsyncSession,
    *,
    token: str,
    full_name: str | None,
    password: str | None,
    ip: str | None,
    user_agent: str | None,
) -> tuple[User, Membership]:
    invitation = (
        await db.execute(
            select(Invitation).where(Invitation.token_hash == hash_secret(token))
        )
    ).scalar_one_or_none()
    if invitation is None:
        raise NotFoundError("Invito non trovato.", code="invitation_not_found")
    if invitation.accepted_at is not None:
        raise ConflictError("Invito già accettato.", code="invitation_used")
    if invitation.revoked_at is not None:
        raise ConflictError("Invito revocato.", code="invitation_revoked")
    if invitation.expires_at <= _now():
        raise ConflictError("Invito scaduto.", code="invitation_expired")

    user = (
        await db.execute(select(User).where(User.email == invitation.email))
    ).scalar_one_or_none()

    if user is None:
        if not password or not full_name:
            raise ValidationAppError(
                "Per nuovi utenti servono nome e password.", code="missing_signup_fields"
            )
        if not is_password_strong(password):
            raise ValidationAppError(
                "Password debole.", code="weak_password",
            )
        user = User(
            email=invitation.email,
            password_hash=hash_password(password),
            full_name=full_name.strip(),
            is_active=True,
        )
        db.add(user)
        await db.flush()

    existing = (
        await db.execute(
            select(Membership).where(
                Membership.user_id == user.id,
                Membership.organization_id == invitation.organization_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        invitation.accepted_at = _now()
        await db.flush()
        return user, existing

    membership = Membership(
        user_id=user.id,
        organization_id=invitation.organization_id,
        role_id=invitation.role_id,
        joined_by_user_id=invitation.created_by_user_id,
    )
    db.add(membership)
    invitation.accepted_at = _now()
    await db.flush()

    await write_audit(
        db,
        action="invitation.accept",
        actor_user_id=user.id,
        organization_id=invitation.organization_id,
        target_type="invitation",
        target_id=str(invitation.id),
        ip=ip,
        user_agent=user_agent,
    )
    return user, membership
