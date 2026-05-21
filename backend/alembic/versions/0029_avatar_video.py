"""video con avatar lip-sync (MuseTalk)

Aggiunge i campi per la scheda "Video con Avatar" delle lezioni: un
video di avatar parlante (lip-sync MuseTalk su RunPod) sovrapposto in
basso a destra al video MP4 già generato della lezione (Fase 6).

Su `avatars` (3 colonne `musetalk_*`) — parametri MuseTalk per-avatar,
passati a `synth_random_lipsync`. Default = valori del comando testato
manualmente:
  - musetalk_extra_margin       (SMALLINT, default 15)
  - musetalk_left_cheek_width   (SMALLINT, default 110)
  - musetalk_right_cheek_width  (SMALLINT, default 110)

Su `course_lesson` (8 colonne `avatar_video_*`) — gemelle delle
`video_*` di `0025_lesson_video.py`:
  - avatar_video_status (VARCHAR 40, default 'empty')
        empty | pending | processing | ready | failed | cancelled
  - avatar_video_progress (SMALLINT, default 0, 0-100)
  - avatar_video_progress_phase (VARCHAR 50 nullable)
        preparing | lipsync | overlay
  - avatar_video_path (VARCHAR 500 nullable) — path relativo
        (`lesson_avatar_videos/...`)
  - avatar_video_attempts (SMALLINT, default 0)
  - avatar_video_error (TEXT nullable)
  - avatar_video_generated_at (TIMESTAMPTZ nullable)
  - avatar_video_tokens (JSONB nullable) — metadata della run.

Index `ix_course_lesson_course_avatar_video_status` su
`(course_id, avatar_video_status)` per le query batch.

Default invariati: zero impatto sulle righe esistenti.

Revision ID: 0029
Revises: 0028
Create Date: 2026-05-21
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- avatars: parametri MuseTalk per-avatar -----------------------
    op.add_column(
        "avatars",
        sa.Column(
            "musetalk_extra_margin",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("15"),
        ),
    )
    op.add_column(
        "avatars",
        sa.Column(
            "musetalk_left_cheek_width",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("110"),
        ),
    )
    op.add_column(
        "avatars",
        sa.Column(
            "musetalk_right_cheek_width",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("110"),
        ),
    )

    # --- course_lesson: pipeline video con avatar ---------------------
    op.add_column(
        "course_lesson",
        sa.Column(
            "avatar_video_status",
            sa.String(length=40),
            nullable=False,
            server_default="empty",
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "avatar_video_progress",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "avatar_video_progress_phase",
            sa.String(length=50),
            nullable=True,
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column("avatar_video_path", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "avatar_video_attempts",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column("avatar_video_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "avatar_video_generated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "avatar_video_tokens",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_course_lesson_avatar_video_status",
        "course_lesson",
        "avatar_video_status IN "
        "('empty','pending','processing','ready','failed','cancelled')",
    )
    op.create_check_constraint(
        "ck_course_lesson_avatar_video_progress",
        "course_lesson",
        "avatar_video_progress >= 0 AND avatar_video_progress <= 100",
    )
    op.create_index(
        "ix_course_lesson_course_avatar_video_status",
        "course_lesson",
        ["course_id", "avatar_video_status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_course_lesson_course_avatar_video_status",
        table_name="course_lesson",
    )
    op.drop_constraint(
        "ck_course_lesson_avatar_video_progress",
        "course_lesson",
        type_="check",
    )
    op.drop_constraint(
        "ck_course_lesson_avatar_video_status",
        "course_lesson",
        type_="check",
    )
    op.drop_column("course_lesson", "avatar_video_tokens")
    op.drop_column("course_lesson", "avatar_video_generated_at")
    op.drop_column("course_lesson", "avatar_video_error")
    op.drop_column("course_lesson", "avatar_video_attempts")
    op.drop_column("course_lesson", "avatar_video_path")
    op.drop_column("course_lesson", "avatar_video_progress_phase")
    op.drop_column("course_lesson", "avatar_video_progress")
    op.drop_column("course_lesson", "avatar_video_status")
    op.drop_column("avatars", "musetalk_right_cheek_width")
    op.drop_column("avatars", "musetalk_left_cheek_width")
    op.drop_column("avatars", "musetalk_extra_margin")
