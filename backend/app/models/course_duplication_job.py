"""Model `CourseDuplicationJob` — orchestrazione del job background di
duplicazione di un corso in un'altra lingua.

Pipeline gestita da `course_duplication_worker._process_one`:
- crea il corso target (clone shell, video/avatar/pdf resettati)
- traduce architecture / content / slides / speech / glossary /
  document summaries via OpenAI (`translate_batch`)
- al termine allinea `target.status = source.status` (escluse Fase
  6/6b che restano `empty`) tramite `advance_course_status`.

L'unique parziale a livello DB (`uq_course_duplication_active`)
impedisce job concorrenti per la stessa coppia (source, lingua
target) quando uno è ancora `pending` o `processing`.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.course import Course
    from app.models.language import Language
    from app.models.user import User


# Valori validi di status. CHECK constraint in DB li tiene allineati.
DUPLICATION_JOB_STATUSES: tuple[str, ...] = (
    "pending",
    "processing",
    "ready",
    "failed",
)


class CourseDuplicationJob(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "course_duplication_job"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','processing','ready','failed')",
            name="ck_course_duplication_job_status",
        ),
        CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name="ck_course_duplication_job_progress",
        ),
        Index("ix_course_duplication_job_source", "source_course_id"),
        Index("ix_course_duplication_job_target", "target_course_id"),
        Index("ix_course_duplication_job_status", "status"),
    )

    source_course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("course.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_course_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("course.id", ondelete="SET NULL"),
        nullable=True,
    )
    target_language_code: Mapped[str] = mapped_column(
        String(10),
        ForeignKey("languages.code", ondelete="RESTRICT"),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="pending", server_default="pending"
    )
    progress: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    progress_phase: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    tokens: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships — esplicitamente legate a `source_course_id` /
    # `target_course_id` perché la stessa tabella `course` viene
    # referenziata due volte (SQLAlchemy ha bisogno del foreign_keys=).
    source_course: Mapped["Course"] = relationship(
        "Course", foreign_keys=[source_course_id]
    )
    target_course: Mapped["Course | None"] = relationship(
        "Course", foreign_keys=[target_course_id]
    )
    target_language: Mapped["Language"] = relationship(
        "Language", foreign_keys=[target_language_code]
    )
    requested_by: Mapped["User | None"] = relationship(
        "User", foreign_keys=[requested_by_user_id]
    )
