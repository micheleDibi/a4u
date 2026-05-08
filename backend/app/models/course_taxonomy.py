from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPKMixin

TAXONOMY_TYPES: tuple[str, ...] = (
    "category",
    "teaching_style",
    "content_depth",
    "teacher_role",
    "audience_size",
    "knowledge_level",
    "target_audience",
    "eqf_level",
)


class CourseTaxonomyTerm(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "course_taxonomy_term"
    __table_args__ = (
        UniqueConstraint(
            "taxonomy_type", "slug", name="uq_course_taxonomy_term_type_slug"
        ),
        Index(
            "ix_course_taxonomy_term_type_parent_sort",
            "taxonomy_type",
            "parent_id",
            "sort_order",
        ),
        CheckConstraint(
            "taxonomy_type IN ('category','teaching_style','content_depth',"
            "'teacher_role','audience_size','knowledge_level',"
            "'target_audience','eqf_level')",
            name="taxonomy_type_valid",
        ),
    )

    taxonomy_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("course_taxonomy_term.id", ondelete="CASCADE"),
        nullable=True,
    )
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    sort_order: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    labels: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    descriptions: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    parent: Mapped["CourseTaxonomyTerm | None"] = relationship(
        "CourseTaxonomyTerm",
        remote_side="CourseTaxonomyTerm.id",
        back_populates="children",
    )
    children: Mapped[list["CourseTaxonomyTerm"]] = relationship(
        "CourseTaxonomyTerm",
        back_populates="parent",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
