"""piano delle figure: collegamenti manuali del docente ai fabbisogni

Revision ID: 0043
Revises: 0042
Create Date: 2026-09-26

`course_lesson.figure_need_links` (JSONB): per fabbisogno «Non serve» o la
figura della lezione che il docente gli collega (doc 18 §23.7). Lo scrive
solo il CRUD; lo stato dei fabbisogni si calcola alla lettura.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "course_lesson", sa.Column("figure_need_links", postgresql.JSONB(), nullable=True)
    )


def downgrade() -> None:
    for column in ("figure_need_links",):
        op.drop_column("course_lesson", column)
