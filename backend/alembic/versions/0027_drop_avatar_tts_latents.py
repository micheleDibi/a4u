"""drop avatar tts_latents cache (TTS migrato su RunPod GPU)

Con la migrazione del TTS su RunPod Serverless (GPU), l'estrazione dei
conditioning latents avviene al volo (~1s su GPU): il sottosistema di
pre-training dei latents perde scopo e viene rimosso.

Questa migration elimina le 4 colonne `avatars.tts_latents_*` (+ il check
constraint) introdotte in 0026.

NB: `course.video_language_code` (introdotta sempre in 0026) **resta** —
e' la lingua TTS per-corso, ancora necessaria con RunPod.

Revision ID: 0027
Revises: 0026
Create Date: 2026-05-20
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_avatar_tts_latents_status", "avatars", type_="check"
    )
    op.drop_column("avatars", "tts_latents_error")
    op.drop_column("avatars", "tts_latents_generated_at")
    op.drop_column("avatars", "tts_latents_path")
    op.drop_column("avatars", "tts_latents_status")


def downgrade() -> None:
    op.add_column(
        "avatars",
        sa.Column(
            "tts_latents_status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "avatars",
        sa.Column("tts_latents_path", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "avatars",
        sa.Column(
            "tts_latents_generated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "avatars",
        sa.Column("tts_latents_error", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "ck_avatar_tts_latents_status",
        "avatars",
        "tts_latents_status IN ('pending','processing','ready','failed')",
    )
