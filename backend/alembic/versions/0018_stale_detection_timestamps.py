"""stale-detection timestamps (3 colonne)

Aggiunge 3 timestamp che il backend setta SOLO durante CRUD manuale di
moduli/lezioni/contenuti. I worker AI (architecture, lessons_structure,
lesson_content) NON toccano queste colonne — usano solo i `*_generated_at`
esistenti.

Il frontend confronta `*_modified_at` con `*_generated_at` per dedurre se
qualcosa a monte è cambiato dopo l'ultima generazione AI a valle, e mostra
un alert di "stale" suggerendo di rigenerare.

Colonne aggiunte:
- course_module.architecture_modified_at (TIMESTAMPTZ NULL): set quando
  l'utente modifica il modulo o le lezioni dell'architettura.
- course_lesson.lesson_structure_modified_at (TIMESTAMPTZ NULL): set quando
  l'utente modifica i 4 campi JSONB della struttura lezione (Fase 2).
- course_lesson.content_modified_at (TIMESTAMPTZ NULL): set quando l'utente
  modifica content_raw (Fase 3).

Revision ID: 0018
Revises: 0017
Create Date: 2026-05-09
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "course_module",
        sa.Column(
            "architecture_modified_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "lesson_structure_modified_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "content_modified_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("course_lesson", "content_modified_at")
    op.drop_column("course_lesson", "lesson_structure_modified_at")
    op.drop_column("course_module", "architecture_modified_at")
