"""pdf_templates.background_opacity_pct

Aggiunge il campo `background_opacity_pct` (0-100) ai template PDF, in modo
che l'utente possa configurare l'opacità dell'immagine di sfondo invece di
restare al valore hardcoded 15%.

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-26
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pdf_templates",
        sa.Column(
            "background_opacity_pct",
            sa.SmallInteger(),
            nullable=False,
            server_default="15",
        ),
    )


def downgrade() -> None:
    op.drop_column("pdf_templates", "background_opacity_pct")
