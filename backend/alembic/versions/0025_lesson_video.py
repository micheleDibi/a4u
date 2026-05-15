"""lesson video generation (Fase 6 §9)

Aggiunge i campi necessari per la generazione del video MP4 della
lezione (TTS XTTS-v2 + slide PNG + ffmpeg). Pre-condizione runtime:
`speech_status='approved'` AND `slides_status='approved'`.

Su `course_lesson` (9 colonne video_*):
  - video_status (VARCHAR 40, default 'empty')
        empty | pending | processing | ready | failed | cancelled
  - video_progress (SMALLINT, default 0, 0-100)
  - video_progress_phase (VARCHAR 50 nullable)
        preparing | tts | rendering_slides | encoding
  - video_path (VARCHAR 500 nullable) — path relativo (`lesson_videos/...`)
  - video_attempts (SMALLINT, default 0)
  - video_error (TEXT nullable)
  - video_generated_at (TIMESTAMPTZ nullable)
  - video_tokens (JSONB nullable) — metadata: audio_duration_s,
        video_duration_s, encode_duration_ms, tts_duration_ms, device,
        model_xtts, num_segments, num_slides, file_size_bytes.

Index `ix_course_lesson_course_video_status` su `(course_id, video_status)`
per query batch (`/courses/{id}/video/generate-batch`).

Revision ID: 0025
Revises: 0024
Create Date: 2026-05-15
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "course_lesson",
        sa.Column(
            "video_status",
            sa.String(length=40),
            nullable=False,
            server_default="empty",
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "video_progress",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "video_progress_phase",
            sa.String(length=50),
            nullable=True,
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column("video_path", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "video_attempts",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column("video_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "video_generated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "video_tokens",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_course_lesson_video_status",
        "course_lesson",
        "video_status IN "
        "('empty','pending','processing','ready','failed','cancelled')",
    )
    op.create_check_constraint(
        "ck_course_lesson_video_progress",
        "course_lesson",
        "video_progress >= 0 AND video_progress <= 100",
    )
    op.create_index(
        "ix_course_lesson_course_video_status",
        "course_lesson",
        ["course_id", "video_status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_course_lesson_course_video_status", table_name="course_lesson"
    )
    op.drop_constraint(
        "ck_course_lesson_video_progress", "course_lesson", type_="check"
    )
    op.drop_constraint(
        "ck_course_lesson_video_status", "course_lesson", type_="check"
    )
    op.drop_column("course_lesson", "video_tokens")
    op.drop_column("course_lesson", "video_generated_at")
    op.drop_column("course_lesson", "video_error")
    op.drop_column("course_lesson", "video_attempts")
    op.drop_column("course_lesson", "video_path")
    op.drop_column("course_lesson", "video_progress_phase")
    op.drop_column("course_lesson", "video_progress")
    op.drop_column("course_lesson", "video_status")
