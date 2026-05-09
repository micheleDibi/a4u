from __future__ import annotations

import uuid

from sqlalchemy import CHAR, Boolean, CheckConstraint, ForeignKey, SmallInteger, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin


# Discriminatore: distingue i template del PDF lezione testo da quelli
# del PDF delle slide. L'output è sempre un .pdf, ma il layout (page
# size landscape, font-size, ecc.) e la pipeline di rendering cambiano.
PDF_TEMPLATE_KINDS: tuple[str, ...] = ("lesson", "slides")


class PdfTemplate(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "pdf_templates"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('lesson', 'slides')",
            name="ck_pdf_templates_kind",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default="lesson", server_default="lesson"
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    background_image_path: Mapped[str | None] = mapped_column(String(500))
    logo_left_path: Mapped[str | None] = mapped_column(String(500))
    logo_right_path: Mapped[str | None] = mapped_column(String(500))
    text_color: Mapped[str] = mapped_column(CHAR(7), nullable=False, default="#1F1F1F")
    primary_color: Mapped[str] = mapped_column(CHAR(7), nullable=False, default="#1976D2")
    secondary_color: Mapped[str] = mapped_column(CHAR(7), nullable=False, default="#9C27B0")
    font_family: Mapped[str] = mapped_column(String(120), nullable=False, default="Roboto")
    page_size: Mapped[str] = mapped_column(String(8), nullable=False, default="A4")
    header_height_mm: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=20)
    footer_height_mm: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=15)
    margin_mm: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=20)
    background_opacity_pct: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=15, server_default="15"
    )
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
