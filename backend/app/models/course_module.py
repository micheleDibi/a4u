from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.course import Course
    from app.models.course_lesson import CourseLesson


# Stati del payload Fase 2 (struttura lezioni) per ciascun modulo.
# Sequenza: empty → pending → processing → ready → approved.
# `failed` è transitorio (l'utente fa "Riprova").
LESSONS_STRUCTURE_STATUSES: tuple[str, ...] = (
    "empty",
    "pending",
    "processing",
    "ready",
    "approved",
    "failed",
)


class CourseModule(UUIDPKMixin, TimestampMixin, Base):
    """Modulo del corso (Fase 1, §4 di prompt_generazione_corsi.md).

    Identificato da `module_code` ("M1", "M2", ...) e `position` 1-based.

    Fase 2 (§5 — struttura delle lezioni) lavora a livello di modulo:
    `lessons_structure_status` traccia lo stato della generazione AI
    della struttura formativa (obiettivi, temi, prerequisiti, scaletta)
    di TUTTE le lezioni del modulo. Lo stato del corso si deriva dagli
    stati dei moduli (vedi `course_lesson_structure_service`).
    """

    __tablename__ = "course_module"
    __table_args__ = (
        CheckConstraint("position >= 1", name="ck_course_module_position_min"),
        UniqueConstraint(
            "course_id", "position", name="uq_course_module_position"
        ),
        UniqueConstraint(
            "course_id", "module_code", name="uq_course_module_code"
        ),
        Index("ix_course_module_course_id", "course_id"),
        CheckConstraint(
            "lessons_structure_status IN "
            "('empty','pending','processing','ready','approved','failed')",
            name="ck_course_module_lessons_structure_status",
        ),
        CheckConstraint(
            "lessons_structure_progress >= 0 AND lessons_structure_progress <= 100",
            name="ck_course_module_lessons_structure_progress",
        ),
    )

    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("course.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    module_code: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )

    # Fase 2 — struttura delle lezioni (per modulo)
    lessons_structure_status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="empty", server_default="empty"
    )
    lessons_structure_raw: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    lessons_structure_tokens: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    lessons_structure_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    lessons_structure_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    lessons_structure_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lessons_structure_approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Stale-detection: timestamp dell'ultima modifica MANUALE al modulo
    # (titolo/descrizione del modulo o titolo/sintesi/bibliografia delle
    # sue lezioni di architettura). Set ESCLUSIVAMENTE dai CRUD endpoint
    # in `course_architecture_crud.py`; i worker AI NON lo toccano.
    # Frontend lo confronta con `lessons_structure_generated_at` per
    # dedurre quando la struttura lezioni del modulo è da rigenerare.
    architecture_modified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lessons_structure_regeneration_hint: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    lessons_structure_progress: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    lessons_structure_progress_phase: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )

    course: Mapped["Course"] = relationship("Course", back_populates="modules")
    lessons: Mapped[list["CourseLesson"]] = relationship(
        "CourseLesson",
        back_populates="module",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="CourseLesson.position",
    )
