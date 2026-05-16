from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPKMixin


AVATAR_TTS_LATENTS_STATUSES: tuple[str, ...] = (
    "pending",
    "processing",
    "ready",
    "failed",
)


class Avatar(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "avatars"
    __table_args__ = (
        CheckConstraint(
            "tts_latents_status IN ('pending','processing','ready','failed')",
            name="ck_avatar_tts_latents_status",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    image_path: Mapped[str] = mapped_column(String(500), nullable=False)
    # Nullable da migration 0026 (forza re-upload per attivare il flusso
    # latents). Quando NULL, l'utente non ha ancora caricato la voce.
    audio_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    audio_lang: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # Riepilogo aggregato per UI rapida: pending|processing|ready|partial|failed.
    clips_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )

    # === TTS latents cache (Fase 6 §9 rifinitura) =====================
    # Workflow: user uploada audio → `tts_latents_status='pending'`. Il
    # worker `avatar_tts_latents_worker` lo pickup, estrae i latents via
    # XTTS, li serializza in `tts_latents_path` (file .pt), set status
    # `ready`. La generazione video usa questi latents pre-computati
    # invece di estrarli ad ogni job (~5-15s saving sul CPU).
    tts_latents_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    tts_latents_path: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    tts_latents_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    tts_latents_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    clips: Mapped[list["AvatarClip"]] = relationship(
        "AvatarClip",
        back_populates="avatar",
        cascade="all, delete-orphan",
        order_by="AvatarClip.position",
        lazy="selectin",
    )
