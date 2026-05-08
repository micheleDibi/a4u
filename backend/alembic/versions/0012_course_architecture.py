"""course architecture (Fase 1): course_module + course_lesson + meta

Tabelle nuove per memorizzare l'architettura didattica del corso prodotta
dalla Fase 1 (§4 di prompt_generazione_corsi.md):

  course_module
    - una riga per ogni modulo (M1, M2, ...)
    - position 1-based, module_code = "M1"
    - title + description

  course_lesson
    - una riga per ogni lezione (M1.L1, M1.L2, ...)
    - position 1-based, lesson_code = "M1.L1"
    - title + summary
    - is_introductory (true SOLO per M1.L1)
    - recommended_bibliography (JSONB) — solo per la lezione introduttiva

ALTER `course`:
  - course_overview, pedagogical_rationale (TEXT) → testi di alto livello dal prompt
  - architecture_raw (JSONB)                       → output OpenAI completo per audit
  - architecture_attempts (SMALLINT)               → counter tentativi
  - architecture_tokens (JSONB)                    → token usati
  - architecture_error (TEXT)                      → ultimo errore
  - architecture_generated_at (TIMESTAMPTZ)        → timestamp generazione

Niente nuovi permessi: `course:generate` (già seedato in 0010) è sufficiente.

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-03
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- ALTER course ---------------------------------------------------------
    op.add_column("course", sa.Column("course_overview", sa.Text(), nullable=True))
    op.add_column(
        "course", sa.Column("pedagogical_rationale", sa.Text(), nullable=True)
    )
    op.add_column(
        "course",
        sa.Column(
            "architecture_raw",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "course",
        sa.Column(
            "architecture_attempts",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "course",
        sa.Column(
            "architecture_tokens",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "course", sa.Column("architecture_error", sa.Text(), nullable=True)
    )
    op.add_column(
        "course",
        sa.Column(
            "architecture_generated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "course",
        sa.Column("architecture_regeneration_hint", sa.Text(), nullable=True),
    )

    # --- course_module --------------------------------------------------------
    op.create_table(
        "course_module",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "course_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("course.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("module_code", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("position >= 1", name="ck_course_module_position_min"),
        sa.UniqueConstraint(
            "course_id", "position", name="uq_course_module_position"
        ),
        sa.UniqueConstraint(
            "course_id", "module_code", name="uq_course_module_code"
        ),
    )
    op.create_index(
        "ix_course_module_course_id", "course_module", ["course_id"]
    )

    # --- course_lesson --------------------------------------------------------
    op.create_table(
        "course_lesson",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "module_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("course_module.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "course_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("course.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("lesson_code", sa.String(length=30), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "is_introductory",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "recommended_bibliography",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "position >= 1", name="ck_course_lesson_position_min"
        ),
        sa.UniqueConstraint(
            "module_id", "position", name="uq_course_lesson_position"
        ),
        sa.UniqueConstraint(
            "course_id", "lesson_code", name="uq_course_lesson_code"
        ),
    )
    op.create_index(
        "ix_course_lesson_module_id", "course_lesson", ["module_id"]
    )
    op.create_index(
        "ix_course_lesson_course_id", "course_lesson", ["course_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_course_lesson_course_id", table_name="course_lesson")
    op.drop_index("ix_course_lesson_module_id", table_name="course_lesson")
    op.drop_table("course_lesson")
    op.drop_index("ix_course_module_course_id", table_name="course_module")
    op.drop_table("course_module")

    op.drop_column("course", "architecture_regeneration_hint")
    op.drop_column("course", "architecture_generated_at")
    op.drop_column("course", "architecture_error")
    op.drop_column("course", "architecture_tokens")
    op.drop_column("course", "architecture_attempts")
    op.drop_column("course", "architecture_raw")
    op.drop_column("course", "pedagogical_rationale")
    op.drop_column("course", "course_overview")
