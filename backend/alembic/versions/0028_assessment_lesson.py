"""course_lesson.is_assessment (lezione di verifica delle competenze)

Aggiunge il flag `is_assessment` a `course_lesson`. Quando
`course.assessment_lesson_enabled` è attivo, l'ultima lezione di ogni
modulo viene materializzata come verifica delle competenze (elenco di
domande a scelta multipla + aperte) invece che come lezione didattica.

Default `false`: zero impatto sulle lezioni esistenti.

Revision ID: 0028
Revises: 0027
Create Date: 2026-05-20
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "course_lesson",
        sa.Column(
            "is_assessment",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("course_lesson", "is_assessment")
