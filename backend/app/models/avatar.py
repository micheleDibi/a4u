from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, SmallInteger, String
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

    # Parametri MuseTalk per il "Video con Avatar" delle lezioni (lip-sync
    # dell'avatar sovrapposto al video della lezione). Configurabili
    # per-avatar dalla pagina "Mio Avatar"; passati a
    # `synth_random_lipsync` come --extra-margin / --left-cheek-width /
    # --right-cheek-width. I default sono i valori del comando MuseTalk
    # testato manualmente. Vedi `course_lesson_avatar_video_worker`.
    musetalk_extra_margin: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=15, server_default="15"
    )
    musetalk_left_cheek_width: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=110, server_default="110"
    )
    musetalk_right_cheek_width: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=110, server_default="110"
    )

    clips: Mapped[list["AvatarClip"]] = relationship(
        "AvatarClip",
        back_populates="avatar",
        cascade="all, delete-orphan",
        order_by="AvatarClip.position",
        lazy="selectin",
    )
