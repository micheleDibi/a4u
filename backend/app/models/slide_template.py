from __future__ import annotations

import uuid

from sqlalchemy import CHAR, Boolean, CheckConstraint, ForeignKey, SmallInteger, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class SlideTemplate(UUIDPKMixin, TimestampMixin, Base):
    """Template unificato per le SLIDE.

    Lo stesso template controlla:
      - il rendering visuale dell'avatar (16:9 / 4:3)
      - il PDF di esportazione delle slide (Fase 4 §7), con i campi
        `margin_mm` e `background_opacity_pct` per il render WeasyPrint.
    """

    __tablename__ = "slide_templates"
    __table_args__ = (
        CheckConstraint(
            "margin_mm BETWEEN 0 AND 60", name="ck_slide_templates_margin_mm"
        ),
        CheckConstraint(
            "background_opacity_pct BETWEEN 0 AND 100",
            name="ck_slide_templates_background_opacity_pct",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    background_image_path: Mapped[str | None] = mapped_column(String(500))
    logo_left_path: Mapped[str | None] = mapped_column(String(500))
    logo_right_path: Mapped[str | None] = mapped_column(String(500))
    text_color: Mapped[str] = mapped_column(CHAR(7), nullable=False, default="#1F1F1F")
    primary_color: Mapped[str] = mapped_column(CHAR(7), nullable=False, default="#1976D2")
    secondary_color: Mapped[str] = mapped_column(CHAR(7), nullable=False, default="#9C27B0")
    font_family: Mapped[str] = mapped_column(String(120), nullable=False, default="Roboto")
    slide_size: Mapped[str] = mapped_column(String(8), nullable=False, default="16:9")
    margin_mm: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=20, server_default="20"
    )
    background_opacity_pct: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=15, server_default="15"
    )
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
