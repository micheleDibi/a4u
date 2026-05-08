from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, SmallInteger
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.organization import Organization


class OrganizationCourseSettings(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "organization_course_settings"
    __table_args__ = (
        CheckConstraint("modules_per_cfu >= 1", name="modules_per_cfu_min"),
        CheckConstraint("lessons_per_module >= 1", name="lessons_per_module_min"),
        CheckConstraint(
            "lesson_duration_minutes >= 1", name="lesson_duration_minutes_min"
        ),
        CheckConstraint(
            "multiple_choice_questions_count >= 0",
            name="multiple_choice_questions_count_min",
        ),
        CheckConstraint(
            "open_questions_count >= 0", name="open_questions_count_min"
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    modules_per_cfu: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default="1"
    )
    lessons_per_module: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=8, server_default="8"
    )
    lesson_duration_minutes: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=15, server_default="15"
    )
    assessment_lesson_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    multiple_choice_questions_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=30, server_default="30"
    )
    open_questions_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=6, server_default="6"
    )

    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="course_settings"
    )
