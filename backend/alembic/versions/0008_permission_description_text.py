"""permissions.description -> TEXT

La colonna `permissions.description` era `VARCHAR(255)`, troppo stretta per
descrizioni articolate (es. il permesso `course_config:manage` richiede ~380
caratteri per spiegare cosa controlla in modo utile per l'admin che assegna
permessi). La porto a TEXT senza limite di lunghezza.

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-28
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "permissions",
        "description",
        existing_type=sa.String(length=255),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "permissions",
        "description",
        existing_type=sa.Text(),
        type_=sa.String(length=255),
        existing_nullable=True,
    )
