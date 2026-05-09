"""pdf_templates: kind discriminator (lesson | slides)

Aggiunge il campo `kind` per distinguere i template PDF della lezione
testo (`kind='lesson'`) da quelli per le slide (`kind='slides'`).
Le righe esistenti sono backfilled a `'lesson'` (default), assumendo
che fino ad ora i template siano stati creati per il PDF lezione.

Revision ID: 0021
Revises: 0020
Create Date: 2026-05-09
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pdf_templates",
        sa.Column(
            "kind",
            sa.String(length=20),
            nullable=False,
            server_default="lesson",
        ),
    )
    op.create_check_constraint(
        "ck_pdf_templates_kind",
        "pdf_templates",
        "kind IN ('lesson', 'slides')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_pdf_templates_kind", "pdf_templates", type_="check")
    op.drop_column("pdf_templates", "kind")
