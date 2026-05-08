from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class AvatarClip(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "avatar_clips"

    avatar_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("avatars.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    prompt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("avatar_clip_prompts.id", ondelete="SET NULL"),
        nullable=True,
    )
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    minimax_task_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    minimax_file_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    video_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    avatar: Mapped["Avatar"] = relationship("Avatar", back_populates="clips")

    __table_args__ = (
        Index("ix_avatar_clips_avatar_position", "avatar_id", "position"),
    )
