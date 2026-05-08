"""i18n: tabelle languages e translations

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-26
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "languages",
        sa.Column("code", sa.String(length=10), primary_key=True),
        sa.Column("name_native", sa.String(length=120), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("rtl", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("flag_country_code", sa.String(length=2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "translations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("language_code", sa.String(length=10), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["language_code"], ["languages.code"], ondelete="CASCADE"),
        sa.UniqueConstraint("language_code", "key", name="uq_translations_language_key"),
    )
    op.create_index("ix_translations_language_code", "translations", ["language_code"])
    op.create_index("ix_translations_key", "translations", ["key"])


def downgrade() -> None:
    op.drop_index("ix_translations_key", table_name="translations")
    op.drop_index("ix_translations_language_code", table_name="translations")
    op.drop_table("translations")
    op.drop_table("languages")
