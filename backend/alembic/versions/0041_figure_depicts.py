"""figure di fonte: che cosa raffigura la figura (depicts, PROMPT 18 e 20)

Revision ID: 0041
Revises: 0040
Create Date: 2026-09-25

Piano delle figure (doc 18 §23.2): `course_document_figure.depicts`, oggetti
e varianti raffigurati in inglese canonico più il primo piano, scritto dalla
Vision descrittiva (PROMPT 18) e da quella della letteratura (PROMPT 20).
NULL per le figure descritte prima: le completa
`scripts/redescribe_figure_depicts.py`, che scrive solo questa colonna.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("course_document_figure", sa.Column("depicts", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    for column in ("depicts",):
        op.drop_column("course_document_figure", column)
