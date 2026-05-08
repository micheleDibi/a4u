"""lesson structure (Fase 2 §5): meta moduli + payload lezioni

Aggiunge i campi necessari per la pipeline AI di Fase 2 (Struttura
delle lezioni). La generazione AI è per modulo: ogni modulo ha il
proprio stato indipendente, lo stato del corso è derivato.

Su `course_module` (10 colonne):
  - lessons_structure_status (VARCHAR 40, default 'empty')
        empty | pending | processing | ready | approved | failed
  - lessons_structure_raw (JSONB nullable) — output AI completo
  - lessons_structure_tokens (JSONB nullable) — usage OpenAI
  - lessons_structure_attempts (SMALLINT, default 0)
  - lessons_structure_error (TEXT nullable)
  - lessons_structure_generated_at (TIMESTAMPTZ nullable)
  - lessons_structure_approved_at (TIMESTAMPTZ nullable)
  - lessons_structure_regeneration_hint (TEXT nullable)
  - lessons_structure_progress (SMALLINT, default 0, 0-100)
  - lessons_structure_progress_phase (VARCHAR 50 nullable)

Su `course_lesson` (4 colonne JSONB con default '[]'):
  - learning_objectives        — string[]
  - mandatory_topics           — { topic_id, topic, rationale }[]
  - prerequisites              — string[]
  - section_outline            — { section_id, title, purpose, covers_topic_ids[] }[]

Su `course.status` (CHECK constraint): aggiunge `lessons_structure_approved`
ai valori ammessi (gli altri due `lessons_structure_pending`/`_ready` erano
già presenti come scaffolding nelle migrazioni precedenti).

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-03
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # course_module — meta + stato della Fase 2 per modulo
    op.add_column(
        "course_module",
        sa.Column(
            "lessons_structure_status",
            sa.String(length=40),
            nullable=False,
            server_default="empty",
        ),
    )
    op.add_column(
        "course_module",
        sa.Column(
            "lessons_structure_raw",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "course_module",
        sa.Column(
            "lessons_structure_tokens",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "course_module",
        sa.Column(
            "lessons_structure_attempts",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "course_module",
        sa.Column("lessons_structure_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "course_module",
        sa.Column(
            "lessons_structure_generated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "course_module",
        sa.Column(
            "lessons_structure_approved_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "course_module",
        sa.Column("lessons_structure_regeneration_hint", sa.Text(), nullable=True),
    )
    op.add_column(
        "course_module",
        sa.Column(
            "lessons_structure_progress",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "course_module",
        sa.Column(
            "lessons_structure_progress_phase",
            sa.String(length=50),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_course_module_lessons_structure_status",
        "course_module",
        "lessons_structure_status IN "
        "('empty','pending','processing','ready','approved','failed')",
    )
    op.create_check_constraint(
        "ck_course_module_lessons_structure_progress",
        "course_module",
        "lessons_structure_progress >= 0 AND lessons_structure_progress <= 100",
    )

    # course_lesson — payload Fase 2 per lezione
    op.add_column(
        "course_lesson",
        sa.Column(
            "learning_objectives",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "mandatory_topics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "prerequisites",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "section_outline",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )

    # course.status — estendi CHECK constraint per aggiungere
    # `lessons_structure_approved` ai valori ammessi.
    op.drop_constraint("ck_course_status_valid", "course", type_="check")
    op.create_check_constraint(
        "ck_course_status_valid",
        "course",
        "status IN ("
        "'draft','architecture_pending','architecture_ready',"
        "'architecture_approved','lessons_structure_pending',"
        "'lessons_structure_ready','lessons_structure_approved',"
        "'content_pending','content_ready',"
        "'slides_pending','slides_ready',"
        "'speech_pending','speech_ready',"
        "'published','archived')",
    )


def downgrade() -> None:
    # Ripristina il CHECK constraint senza `lessons_structure_approved`.
    op.drop_constraint("ck_course_status_valid", "course", type_="check")
    op.create_check_constraint(
        "ck_course_status_valid",
        "course",
        "status IN ("
        "'draft','architecture_pending','architecture_ready',"
        "'architecture_approved','lessons_structure_pending',"
        "'lessons_structure_ready','content_pending','content_ready',"
        "'slides_pending','slides_ready',"
        "'speech_pending','speech_ready',"
        "'published','archived')",
    )

    # course_lesson — drop dei 4 campi JSONB
    op.drop_column("course_lesson", "section_outline")
    op.drop_column("course_lesson", "prerequisites")
    op.drop_column("course_lesson", "mandatory_topics")
    op.drop_column("course_lesson", "learning_objectives")

    # course_module — drop check + 10 colonne (ordine inverso)
    op.drop_constraint(
        "ck_course_module_lessons_structure_progress",
        "course_module",
        type_="check",
    )
    op.drop_constraint(
        "ck_course_module_lessons_structure_status",
        "course_module",
        type_="check",
    )
    op.drop_column("course_module", "lessons_structure_progress_phase")
    op.drop_column("course_module", "lessons_structure_progress")
    op.drop_column("course_module", "lessons_structure_regeneration_hint")
    op.drop_column("course_module", "lessons_structure_approved_at")
    op.drop_column("course_module", "lessons_structure_generated_at")
    op.drop_column("course_module", "lessons_structure_error")
    op.drop_column("course_module", "lessons_structure_attempts")
    op.drop_column("course_module", "lessons_structure_tokens")
    op.drop_column("course_module", "lessons_structure_raw")
    op.drop_column("course_module", "lessons_structure_status")
