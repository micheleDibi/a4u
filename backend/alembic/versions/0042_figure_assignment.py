"""piano delle figure: assegnazione ai fabbisogni e figure trovate per un fabbisogno

Revision ID: 0042
Revises: 0041
Create Date: 2026-09-25

Piano delle figure (doc 18 §23.4):
- `course_lesson.figure_assignment` (JSONB): offerta all'avvio della Fase 3
  e fotografia finale alla materializzazione;
- `course_document_figure.found_for_lesson_id` (FK a `course_lesson`,
  ON DELETE SET NULL) e `found_for_need_id`: la figura della letteratura
  aperta trovata per quel fabbisogno (riserva dell'assegnazione).
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "course_lesson", sa.Column("figure_assignment", postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        "course_document_figure",
        sa.Column("found_for_lesson_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "course_document_figure",
        sa.Column("found_for_need_id", sa.String(length=40), nullable=True),
    )
    op.create_foreign_key(
        "fk_course_document_figure_found_for_lesson_id_course_lesson",
        "course_document_figure",
        "course_lesson",
        ["found_for_lesson_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # ON DELETE SET NULL cerca le righe della lezione cancellata.
    op.create_index(
        "ix_course_document_figure_found_for_lesson_id",
        "course_document_figure",
        ["found_for_lesson_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_course_document_figure_found_for_lesson_id", table_name="course_document_figure"
    )
    op.drop_constraint(
        "fk_course_document_figure_found_for_lesson_id_course_lesson",
        "course_document_figure",
        type_="foreignkey",
    )
    for column in ("found_for_need_id", "found_for_lesson_id"):
        op.drop_column("course_document_figure", column)
    for column in ("figure_assignment",):
        op.drop_column("course_lesson", column)
