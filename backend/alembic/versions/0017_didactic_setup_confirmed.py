"""course.didactic_setup_confirmed_at — wizard lock setup

Aggiunge un singolo timestamp `didactic_setup_confirmed_at` sulla tabella
`course`. Quando NULL, le tab "Informazioni di base" e "Inquadramento
didattico" del CourseEditor sono editabili. Quando valorizzato, sono
read-only: i parametri del corso sono "confermati" e non si modificano
più senza un esplicito sblocco (creator/org_admin only).

Il sblocco azzera la colonna a NULL.

Revision ID: 0017
Revises: 0016
Create Date: 2026-05-09
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "course",
        sa.Column(
            "didactic_setup_confirmed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("course", "didactic_setup_confirmed_at")
