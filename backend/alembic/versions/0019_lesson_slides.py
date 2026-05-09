"""lesson slides (Fase 4 §7)

Aggiunge i campi necessari per la pipeline AI di Fase 4 (Slide della
lezione). La generazione AI è per lezione: ogni lezione ha il proprio
stato `slides_*` indipendente, lo stato del corso è derivato.

Su `course_lesson` (10 colonne slides_*):
  - slides_status (VARCHAR 40, default 'empty')
        empty | pending | processing | ready | approved | failed
  - slides_raw (JSONB nullable) — output AI completo §7.3
        {lesson_id, total_slides, slides:[...], new_assets:[...]}
  - slides_tokens (JSONB nullable) — usage OpenAI
  - slides_attempts (SMALLINT, default 0)
  - slides_error (TEXT nullable)
  - slides_generated_at (TIMESTAMPTZ nullable)
  - slides_approved_at (TIMESTAMPTZ nullable)
  - slides_modified_at (TIMESTAMPTZ nullable) — stale-detection (set da
    CRUD manuale, NON dal worker AI)
  - slides_regeneration_hint (TEXT nullable)
  - slides_progress (SMALLINT, default 0, 0-100)
  - slides_progress_phase (VARCHAR 50 nullable)

Su `course.status` (CHECK constraint): aggiunge `slides_approved`
(`slides_pending`/`slides_ready` erano già presenti come scaffolding).

Revision ID: 0019
Revises: 0018
Create Date: 2026-05-09
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # course_lesson — payload Fase 4 (10 colonne)
    op.add_column(
        "course_lesson",
        sa.Column(
            "slides_status",
            sa.String(length=40),
            nullable=False,
            server_default="empty",
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "slides_raw",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "slides_tokens",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "slides_attempts",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column("slides_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "slides_generated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "slides_approved_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "slides_modified_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column("slides_regeneration_hint", sa.Text(), nullable=True),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "slides_progress",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "slides_progress_phase",
            sa.String(length=50),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_course_lesson_slides_status",
        "course_lesson",
        "slides_status IN "
        "('empty','pending','processing','ready','approved','failed')",
    )
    op.create_check_constraint(
        "ck_course_lesson_slides_progress",
        "course_lesson",
        "slides_progress >= 0 AND slides_progress <= 100",
    )

    # course.status — estendi CHECK constraint per aggiungere
    # `slides_approved` (simmetrico a content_approved). slides_pending
    # e slides_ready erano già lì.
    op.drop_constraint("ck_course_status_valid", "course", type_="check")
    op.create_check_constraint(
        "ck_course_status_valid",
        "course",
        "status IN ("
        "'draft','architecture_pending','architecture_ready',"
        "'architecture_approved','lessons_structure_pending',"
        "'lessons_structure_ready','lessons_structure_approved',"
        "'content_pending','content_ready','content_approved',"
        "'slides_pending','slides_ready','slides_approved',"
        "'speech_pending','speech_ready',"
        "'published','archived')",
    )


def downgrade() -> None:
    # Ripristina il CHECK constraint senza `slides_approved`.
    op.drop_constraint("ck_course_status_valid", "course", type_="check")
    op.create_check_constraint(
        "ck_course_status_valid",
        "course",
        "status IN ("
        "'draft','architecture_pending','architecture_ready',"
        "'architecture_approved','lessons_structure_pending',"
        "'lessons_structure_ready','lessons_structure_approved',"
        "'content_pending','content_ready','content_approved',"
        "'slides_pending','slides_ready',"
        "'speech_pending','speech_ready',"
        "'published','archived')",
    )

    # course_lesson — drop check + 11 colonne (ordine inverso)
    op.drop_constraint(
        "ck_course_lesson_slides_progress", "course_lesson", type_="check"
    )
    op.drop_constraint(
        "ck_course_lesson_slides_status", "course_lesson", type_="check"
    )
    op.drop_column("course_lesson", "slides_progress_phase")
    op.drop_column("course_lesson", "slides_progress")
    op.drop_column("course_lesson", "slides_regeneration_hint")
    op.drop_column("course_lesson", "slides_modified_at")
    op.drop_column("course_lesson", "slides_approved_at")
    op.drop_column("course_lesson", "slides_generated_at")
    op.drop_column("course_lesson", "slides_error")
    op.drop_column("course_lesson", "slides_attempts")
    op.drop_column("course_lesson", "slides_tokens")
    op.drop_column("course_lesson", "slides_raw")
    op.drop_column("course_lesson", "slides_status")
