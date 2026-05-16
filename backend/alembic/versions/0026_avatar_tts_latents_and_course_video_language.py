"""avatar tts latents cache + course video_language (Fase 6 §9 rifinitura)

Tre cambi correlati:

1. **Avatar TTS latents cache** (`avatars.tts_latents_*`): nuove 4 colonne
   per persistere su disco i `gpt_cond_latent + speaker_embedding`
   estratti da XTTS-v2 una sola volta per voce (al momento dell'upload
   audio dal worker `avatar_tts_latents_worker`). Saltano ~5-15s di
   estrazione inline al primo job video di ciascun assegnatario.

2. **Force re-upload audio**: `avatars.audio_path` diventa NULLABLE e
   viene azzerato per tutti gli avatar esistenti. Scelta consapevole
   dell'utente per partire puliti col nuovo flusso latents (gli audio
   pre-esistenti non hanno mai avuto i latents estratti; piuttosto che
   schedulare un mass-extract in background, forziamo un re-upload con
   validazione durata >=6s).

3. **Lingua TTS per-corso** (`course.video_language_code`): override
   opzionale della `course.language_code` per la voce nei video. Quando
   NULL, il worker video usa `course.language_code`. FK a `languages`
   con ON DELETE SET NULL.

Revision ID: 0026
Revises: 0025
Create Date: 2026-05-16
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # === 1. Avatar TTS latents fields ====================================
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

    # === 2. audio_path nullable + reset (force re-upload) =============
    op.alter_column(
        "avatars",
        "audio_path",
        existing_type=sa.String(length=500),
        nullable=True,
    )
    # Reset distruttivo: gli avatar esistenti perdono l'audio_path su DB.
    # Il file fisico in /uploads/avatars/* resta — solo il riferimento DB
    # viene rimosso. L'utente deve ri-caricare l'audio dalla UI; il worker
    # latents poi processerà.
    op.execute("UPDATE avatars SET audio_path = NULL, audio_lang = NULL")

    # === 3. Course.video_language_code ================================
    op.add_column(
        "course",
        sa.Column("video_language_code", sa.String(length=10), nullable=True),
    )
    op.create_foreign_key(
        "fk_course_video_language",
        "course",
        "languages",
        ["video_language_code"],
        ["code"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    # === 3 (reverse) ==================================================
    op.drop_constraint(
        "fk_course_video_language", "course", type_="foreignkey"
    )
    op.drop_column("course", "video_language_code")

    # === 2 (reverse) — NB: i path eliminati non si possono ripristinare
    # (non li abbiamo salvati). Il downgrade re-rende NOT NULL e setta
    # placeholder vuoto per evitare violazione di constraint.
    op.execute(
        "UPDATE avatars SET audio_path = '' WHERE audio_path IS NULL"
    )
    op.alter_column(
        "avatars",
        "audio_path",
        existing_type=sa.String(length=500),
        nullable=False,
    )

    # === 1 (reverse) ==================================================
    op.drop_constraint(
        "ck_avatar_tts_latents_status", "avatars", type_="check"
    )
    op.drop_column("avatars", "tts_latents_error")
    op.drop_column("avatars", "tts_latents_generated_at")
    op.drop_column("avatars", "tts_latents_path")
    op.drop_column("avatars", "tts_latents_status")
