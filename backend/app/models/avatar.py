from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class Avatar(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "avatars"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    image_path: Mapped[str] = mapped_column(String(500), nullable=False)
    # Nullable da migration 0026. Quando NULL, l'utente non ha ancora
    # caricato il campione vocale.
    audio_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    audio_lang: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # Riepilogo aggregato per UI rapida: pending|processing|ready|partial|failed.
    clips_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )

    clips: Mapped[list["AvatarClip"]] = relationship(
        "AvatarClip",
        back_populates="avatar",
        cascade="all, delete-orphan",
        order_by="AvatarClip.position",
        lazy="selectin",
    )
