"""lesson slides PDF export (Fase 4 §7)

Aggiunge i campi necessari per l'export PDF delle slide. Mirror del
pattern `pdf_*` esistente per la lezione (testo) ma scoped alle slide,
con path file separato (`{...}_slides.pdf`).

Su `course_lesson` (8 colonne slides_pdf_*):
  - slides_pdf_status (VARCHAR 40, default 'empty')
        empty | pending | processing | ready | failed
  - slides_pdf_progress (SMALLINT, default 0, 0-100)
  - slides_pdf_progress_phase (VARCHAR 50 nullable)
  - slides_pdf_path (VARCHAR 500 nullable) — path relativo
  - slides_pdf_template_id (UUID nullable, FK pdf_templates.id ON DELETE SET NULL)
  - slides_pdf_attempts (SMALLINT, default 0)
  - slides_pdf_error (TEXT nullable)
  - slides_pdf_generated_at (TIMESTAMPTZ nullable)

Revision ID: 0020
Revises: 0019
Create Date: 2026-05-09
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "course_lesson",
        sa.Column(
            "slides_pdf_status",
            sa.String(length=40),
            nullable=False,
            server_default="empty",
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "slides_pdf_progress",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "slides_pdf_progress_phase",
            sa.String(length=50),
            nullable=True,
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column("slides_pdf_path", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "slides_pdf_template_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pdf_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "slides_pdf_attempts",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column("slides_pdf_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "slides_pdf_generated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_course_lesson_slides_pdf_status",
        "course_lesson",
        "slides_pdf_status IN "
        "('empty','pending','processing','ready','failed')",
    )
    op.create_check_constraint(
        "ck_course_lesson_slides_pdf_progress",
        "course_lesson",
        "slides_pdf_progress >= 0 AND slides_pdf_progress <= 100",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_course_lesson_slides_pdf_progress", "course_lesson", type_="check"
    )
    op.drop_constraint(
        "ck_course_lesson_slides_pdf_status", "course_lesson", type_="check"
    )
    op.drop_column("course_lesson", "slides_pdf_generated_at")
    op.drop_column("course_lesson", "slides_pdf_error")
    op.drop_column("course_lesson", "slides_pdf_attempts")
    op.drop_column("course_lesson", "slides_pdf_template_id")
    op.drop_column("course_lesson", "slides_pdf_path")
    op.drop_column("course_lesson", "slides_pdf_progress_phase")
    op.drop_column("course_lesson", "slides_pdf_progress")
    op.drop_column("course_lesson", "slides_pdf_status")
