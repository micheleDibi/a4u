"""avatar v2: per-utente + clip + prompt config

Drop della vecchia tabella `avatars` org-scoped (semantica obsoleta) e
ricreazione del nuovo schema:
  - `avatars` (1 per utente, image+audio)
  - `avatar_clip_prompts` (config admin globale)
  - `avatar_clips` (1..N per avatar, status pipeline MiniMax)

Inoltre rimuove il permesso `avatar:manage` che non ha più senso (l'avatar
non è una risorsa aziendale ma personale dell'utente).

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-26
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) Rimozione del permesso avatar:manage (e overrides relative).
    op.execute(
        """
        DELETE FROM organization_role_permissions
        WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'avatar:manage');
        """
    )
    op.execute(
        """
        DELETE FROM membership_permission_overrides
        WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'avatar:manage');
        """
    )
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'avatar:manage');
        """
    )
    op.execute("DELETE FROM permissions WHERE code = 'avatar:manage';")

    # 2) Drop vecchia tabella avatars (era org-scoped, dato di test).
    op.drop_index("ix_avatars_organization_id", table_name="avatars")
    op.drop_table("avatars")

    # 3) Nuova `avatars`: 1 per utente.
    op.create_table(
        "avatars",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("image_path", sa.String(length=500), nullable=False),
        sa.Column("audio_path", sa.String(length=500), nullable=False),
        sa.Column("audio_text", sa.Text(), nullable=True),
        sa.Column("audio_lang", sa.String(length=10), nullable=True),
        sa.Column(
            "clips_status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", name="uq_avatars_user_id"),
    )

    # 4) Tabella prompts (config admin).
    op.create_table(
        "avatar_clip_prompts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("label_it", sa.String(length=120), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("position", name="uq_avatar_clip_prompts_position"),
    )

    # 5) Tabella clip generati.
    op.create_table(
        "avatar_clips",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("avatar_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("prompt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("minimax_task_id", sa.String(length=80), nullable=True),
        sa.Column("minimax_file_id", sa.String(length=80), nullable=True),
        sa.Column("video_path", sa.String(length=500), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["avatar_id"], ["avatars.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["prompt_id"], ["avatar_clip_prompts.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_avatar_clips_avatar_id", "avatar_clips", ["avatar_id"]
    )
    op.create_index(
        "ix_avatar_clips_avatar_position",
        "avatar_clips",
        ["avatar_id", "position"],
    )
    op.create_index(
        "ix_avatar_clips_minimax_task_id",
        "avatar_clips",
        ["minimax_task_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_avatar_clips_minimax_task_id", table_name="avatar_clips")
    op.drop_index("ix_avatar_clips_avatar_position", table_name="avatar_clips")
    op.drop_index("ix_avatar_clips_avatar_id", table_name="avatar_clips")
    op.drop_table("avatar_clips")
    op.drop_table("avatar_clip_prompts")
    op.drop_table("avatars")
    op.create_table(
        "avatars",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("image_path", sa.String(length=500), nullable=True),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_avatars_organization_id", "avatars", ["organization_id"])
