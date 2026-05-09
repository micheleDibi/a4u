"""lesson speech (Fase 5 §8)

Aggiunge i campi necessari per la pipeline AI di Fase 5 — Discorso
temporizzato. La generazione AI è per lezione: ogni lezione ha il proprio
stato `speech_*` indipendente; lo stato del corso è derivato.

Su `course_lesson` (11 colonne speech_*):
  - speech_status (VARCHAR 40, default 'empty')
        empty | pending | processing | ready | approved | failed
  - speech_raw (JSONB nullable) — output AI completo §8.4
        {lesson_id, language, target_duration_seconds,
         estimated_total_duration_seconds, estimated_total_word_count,
         speech_segments:[...], slide_to_segments_map:[...]}
  - speech_tokens (JSONB nullable) — usage OpenAI
  - speech_attempts (SMALLINT, default 0)
  - speech_error (TEXT nullable)
  - speech_generated_at (TIMESTAMPTZ nullable)
  - speech_approved_at (TIMESTAMPTZ nullable)
  - speech_modified_at (TIMESTAMPTZ nullable) — stale-detection (set da
    CRUD manuale, NON dal worker AI)
  - speech_regeneration_hint (TEXT nullable)
  - speech_progress (SMALLINT, default 0, 0-100)
  - speech_progress_phase (VARCHAR 50 nullable)

Su `course.status` (CHECK constraint): aggiunge `speech_approved`
(simmetrico a slides_approved/content_approved). speech_pending /
speech_ready erano già presenti.

Revision ID: 0023
Revises: 0022
Create Date: 2026-05-09
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # course_lesson — payload Fase 5 (11 colonne)
    op.add_column(
        "course_lesson",
        sa.Column(
            "speech_status",
            sa.String(length=40),
            nullable=False,
            server_default="empty",
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "speech_raw",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "speech_tokens",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "speech_attempts",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column("speech_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "speech_generated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "speech_approved_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "speech_modified_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column("speech_regeneration_hint", sa.Text(), nullable=True),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "speech_progress",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "speech_progress_phase",
            sa.String(length=50),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_course_lesson_speech_status",
        "course_lesson",
        "speech_status IN "
        "('empty','pending','processing','ready','approved','failed')",
    )
    op.create_check_constraint(
        "ck_course_lesson_speech_progress",
        "course_lesson",
        "speech_progress >= 0 AND speech_progress <= 100",
    )

    # course.status — estendi CHECK constraint per aggiungere
    # `speech_approved` (simmetrico a slides_approved/content_approved).
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
        "'speech_pending','speech_ready','speech_approved',"
        "'published','archived')",
    )


def downgrade() -> None:
    # Ripristina il CHECK constraint senza `speech_approved`.
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

    # course_lesson — drop check + 11 colonne (ordine inverso)
    op.drop_constraint(
        "ck_course_lesson_speech_progress", "course_lesson", type_="check"
    )
    op.drop_constraint(
        "ck_course_lesson_speech_status", "course_lesson", type_="check"
    )
    op.drop_column("course_lesson", "speech_progress_phase")
    op.drop_column("course_lesson", "speech_progress")
    op.drop_column("course_lesson", "speech_regeneration_hint")
    op.drop_column("course_lesson", "speech_modified_at")
    op.drop_column("course_lesson", "speech_approved_at")
    op.drop_column("course_lesson", "speech_generated_at")
    op.drop_column("course_lesson", "speech_error")
    op.drop_column("course_lesson", "speech_attempts")
    op.drop_column("course_lesson", "speech_tokens")
    op.drop_column("course_lesson", "speech_raw")
    op.drop_column("course_lesson", "speech_status")
