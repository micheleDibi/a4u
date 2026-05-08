"""template default flag

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-26
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "slide_templates",
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "pdf_templates",
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    # Indice parziale: al massimo un default per organizzazione per tipo.
    op.create_index(
        "uq_slide_templates_default_per_org",
        "slide_templates",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )
    op.create_index(
        "uq_pdf_templates_default_per_org",
        "pdf_templates",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )


def downgrade() -> None:
    op.drop_index("uq_pdf_templates_default_per_org", table_name="pdf_templates")
    op.drop_index("uq_slide_templates_default_per_org", table_name="slide_templates")
    op.drop_column("pdf_templates", "is_default")
    op.drop_column("slide_templates", "is_default")
