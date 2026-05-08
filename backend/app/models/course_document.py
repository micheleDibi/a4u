from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.course import Course


class CourseDocument(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "course_document"
    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="ck_course_document_size_bytes_min"),
        CheckConstraint(
            "summary_status IN ('pending','processing','ready','failed')",
            name="ck_course_document_summary_status_valid",
        ),
    )

    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("course.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename_original: Mapped[str] = mapped_column(String(300), nullable=False)
    filename_stored: Mapped[str] = mapped_column(
        String(300), nullable=False, unique=True
    )
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Output dell'Appendice A (riassunto strutturato).
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    summary_status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="pending", server_default="pending"
    )
    summary_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Metadati del worker di pre-processing (Appendice A).
    text_extracted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    text_chars_extracted: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary_tokens: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    summary_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )

    course: Mapped["Course"] = relationship("Course", back_populates="documents")
