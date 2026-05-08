"""course_document: campi meta per il pre-processing AI (Appendice A)

Aggiunge alla tabella `course_document` quattro colonne informative usate
dal worker che genera il riassunto strutturato:

  - text_extracted_at      — timestamp dell'estrazione testuale
  - text_chars_extracted   — caratteri estratti (post-troncamento)
  - summary_tokens         — JSONB { prompt, completion, total, model }
  - summary_attempts       — counter incrementato a ogni tentativo

Niente nuovi indici (campi solo informativi). Niente cambio sul JSONB
`summary` esistente: la validazione resta lato Pydantic prima del write.

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-03
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "course_document",
        sa.Column("text_extracted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "course_document",
        sa.Column("text_chars_extracted", sa.Integer(), nullable=True),
    )
    op.add_column(
        "course_document",
        sa.Column(
            "summary_tokens",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "course_document",
        sa.Column(
            "summary_attempts",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("course_document", "summary_attempts")
    op.drop_column("course_document", "summary_tokens")
    op.drop_column("course_document", "text_chars_extracted")
    op.drop_column("course_document", "text_extracted_at")
