from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, TYPE_CHECKING

from sqlalchemy import (
    Boolean,
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
    from app.models.course_module import CourseModule


LESSON_CONTENT_STATUSES: tuple[str, ...] = (
    "empty",
    "pending",
    "processing",
    "ready",
    "approved",
    "failed",
)

LESSON_PDF_STATUSES: tuple[str, ...] = (
    "empty",
    "pending",
    "processing",
    "ready",
    "failed",
)

LESSON_SLIDES_STATUSES: tuple[str, ...] = (
    "empty",
    "pending",
    "processing",
    "ready",
    "approved",
    "failed",
)


class CourseLesson(UUIDPKMixin, TimestampMixin, Base):
    """Lezione del corso (Fase 1, §4 di prompt_generazione_corsi.md).

    Identificata da `lesson_code` ("M1.L1", "M1.L2", ...) e `position`
    1-based all'interno del modulo. La lezione introduttiva
    (`is_introductory=True`) corrisponde sempre a M1.L1 e contiene la
    bibliografia consigliata in `recommended_bibliography`.
    """

    __tablename__ = "course_lesson"
    __table_args__ = (
        CheckConstraint("position >= 1", name="ck_course_lesson_position_min"),
        UniqueConstraint(
            "module_id", "position", name="uq_course_lesson_position"
        ),
        UniqueConstraint(
            "course_id", "lesson_code", name="uq_course_lesson_code"
        ),
        Index("ix_course_lesson_module_id", "module_id"),
        Index("ix_course_lesson_course_id", "course_id"),
        CheckConstraint(
            "content_status IN "
            "('empty','pending','processing','ready','approved','failed')",
            name="ck_course_lesson_content_status",
        ),
        CheckConstraint(
            "content_progress >= 0 AND content_progress <= 100",
            name="ck_course_lesson_content_progress",
        ),
        CheckConstraint(
            "pdf_status IN ('empty','pending','processing','ready','failed')",
            name="ck_course_lesson_pdf_status",
        ),
        CheckConstraint(
            "pdf_progress >= 0 AND pdf_progress <= 100",
            name="ck_course_lesson_pdf_progress",
        ),
        CheckConstraint(
            "slides_status IN "
            "('empty','pending','processing','ready','approved','failed')",
            name="ck_course_lesson_slides_status",
        ),
        CheckConstraint(
            "slides_progress >= 0 AND slides_progress <= 100",
            name="ck_course_lesson_slides_progress",
        ),
    )

    module_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("course_module.id", ondelete="CASCADE"),
        nullable=False,
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("course.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    lesson_code: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    is_introductory: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    recommended_bibliography: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    # Fase 2 — struttura formativa della lezione (§5).
    # Popolata dal worker `course_lesson_structure_worker` quando il
    # modulo padre passa per `processing → ready`. Modificabile a mano
    # via PATCH /lessons/{id}/structure (richiede status modulo
    # `ready` o `approved`).
    learning_objectives: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    mandatory_topics: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    prerequisites: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    section_outline: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    # Fase 3 — Contenuti della lezione (§6).
    # Popolata dal worker `course_lesson_content_worker` quando la
    # lezione passa per `processing → ready`. Modificabile a mano via
    # PATCH /lessons/{id}/content (richiede status `ready` o `approved`).
    content_status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="empty", server_default="empty"
    )
    content_raw: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    content_tokens: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    content_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    content_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    content_approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Stale-detection — modifica manuale dei 4 campi JSONB di Fase 2
    # (learning_objectives, mandatory_topics, prerequisites, section_outline).
    # Set da `course_lesson_structure_crud.update_lesson_structure`; il
    # worker AI di Fase 2 NON tocca questa colonna. Confrontato lato FE
    # con `content_generated_at` per dedurre stale del contenuto.
    lesson_structure_modified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Stale-detection — modifica manuale di `content_raw` (Fase 3). Set
    # da `course_lesson_content_crud.update_lesson_content`; il worker AI
    # di Fase 3 NON tocca questa colonna. Confrontato lato FE con
    # `pdf_generated_at` per dedurre stale del PDF.
    content_modified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    content_regeneration_hint: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    content_progress: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    content_progress_phase: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )

    # §7 — Export PDF della lezione (pipeline async, scoped a livello
    # lezione). Lo stato `ready` significa "PDF disponibile a `pdf_path`".
    pdf_status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="empty", server_default="empty"
    )
    pdf_progress: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    pdf_progress_phase: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    pdf_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    pdf_template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pdf_templates.id", ondelete="SET NULL"),
        nullable=True,
    )
    pdf_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    pdf_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # §7 — Fase 4: Slide della lezione (pipeline async, scoped a lezione).
    # Popolata dal worker `course_lesson_slides_worker` quando la lezione
    # passa per `processing → ready`. Modificabile a mano via PATCH
    # /lessons/{id}/slides (richiede status `ready` o `approved`).
    slides_status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="empty", server_default="empty"
    )
    slides_raw: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    slides_tokens: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    slides_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    slides_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    slides_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    slides_approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Stale-detection — modifica manuale del `slides_raw` (Fase 4). Set
    # da `course_lesson_slides_crud.update_lesson_slides`; il worker AI
    # di Fase 4 NON tocca questa colonna. Confrontato lato FE col PDF
    # slide e con upstream (content/structure/architecture).
    slides_modified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    slides_regeneration_hint: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    slides_progress: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    slides_progress_phase: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )

    module: Mapped["CourseModule"] = relationship(
        "CourseModule", back_populates="lessons"
    )
    course: Mapped["Course"] = relationship("Course", back_populates="lessons")
