"""avatar voice scripts (testo da leggere) e drop audio_text

L'`audio_text` libero scritto dall'utente non serviva più: ora l'admin
configura un *script* da leggere durante la registrazione (uno per
lingua), così otteniamo un campione audio standardizzato adatto al voice
cloning. Quel campo viene rimosso da `avatars` e nasce la tabella
`avatar_voice_scripts`.

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-26
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("avatars", "audio_text")
    op.create_table(
        "avatar_voice_scripts",
        sa.Column("language_code", sa.String(length=10), primary_key=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["language_code"], ["languages.code"], ondelete="CASCADE"
        ),
    )


def downgrade() -> None:
    op.drop_table("avatar_voice_scripts")
    op.add_column(
        "avatars",
        sa.Column("audio_text", sa.Text(), nullable=True),
    )
