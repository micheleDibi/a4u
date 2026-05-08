from __future__ import annotations

from sqlalchemy import Boolean, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class AvatarClipPrompt(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "avatar_clip_prompts"

    position: Mapped[int] = mapped_column(SmallInteger, nullable=False, unique=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    label_it: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
