"""course architecture progress tracking

Aggiunge due colonne a `course` per esporre lo stato di avanzamento durante
la generazione architettura (Fase 1):

  - architecture_progress (SMALLINT, 0-100)  → percentuale corrente
  - architecture_progress_phase (VARCHAR)    → chiave i18n della fase

Il worker aggiorna queste colonne ai checkpoint del flusso (preparazione,
chiamata OpenAI, materializzazione) e durante la chiamata OpenAI un task
di sfondo incrementa gradualmente la percentuale per dare feedback in UI.

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-03
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "course",
        sa.Column(
            "architecture_progress",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "course",
        sa.Column(
            "architecture_progress_phase",
            sa.String(length=50),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("course", "architecture_progress_phase")
    op.drop_column("course", "architecture_progress")
