"""lesson PDF export pipeline (§7 — export PDF)

Aggiunge i campi necessari per la pipeline di export PDF delle lezioni
generate. Il PDF viene renderizzato lato server con Playwright (Chromium
headless) usando il template grafico (`pdf_templates`) configurato per
l'organizzazione del corso. Stato per lezione su `course_lesson.pdf_status`:
    empty → pending → processing → ready → failed (transitorio, retry manuale)

Su `course_lesson` (8 colonne pdf_*):
  - pdf_status (VARCHAR 40, default 'empty')
        empty | pending | processing | ready | failed
  - pdf_progress (SMALLINT, default 0, 0-100)
  - pdf_progress_phase (VARCHAR 50 nullable)
  - pdf_path (VARCHAR 500 nullable) — path relativo al GENERATED_PDFS_DIR
  - pdf_template_id (UUID nullable, FK pdf_templates SET NULL)
        template usato all'ultima generazione (snapshot)
  - pdf_attempts (SMALLINT, default 0)
  - pdf_error (TEXT nullable)
  - pdf_generated_at (TIMESTAMPTZ nullable)

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-03
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "course_lesson",
        sa.Column(
            "pdf_status",
            sa.String(length=40),
            nullable=False,
            server_default="empty",
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "pdf_progress",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column("pdf_progress_phase", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "course_lesson",
        sa.Column("pdf_path", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "pdf_template_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pdf_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "pdf_attempts",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column("pdf_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "pdf_generated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_course_lesson_pdf_status",
        "course_lesson",
        "pdf_status IN ('empty','pending','processing','ready','failed')",
    )
    op.create_check_constraint(
        "ck_course_lesson_pdf_progress",
        "course_lesson",
        "pdf_progress >= 0 AND pdf_progress <= 100",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_course_lesson_pdf_progress", "course_lesson", type_="check"
    )
    op.drop_constraint(
        "ck_course_lesson_pdf_status", "course_lesson", type_="check"
    )
    op.drop_column("course_lesson", "pdf_generated_at")
    op.drop_column("course_lesson", "pdf_error")
    op.drop_column("course_lesson", "pdf_attempts")
    op.drop_column("course_lesson", "pdf_template_id")
    op.drop_column("course_lesson", "pdf_path")
    op.drop_column("course_lesson", "pdf_progress_phase")
    op.drop_column("course_lesson", "pdf_progress")
    op.drop_column("course_lesson", "pdf_status")
