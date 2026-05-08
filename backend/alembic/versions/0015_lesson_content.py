"""lesson content (Fase 3 §6) + course glossary (§10.1)

Aggiunge i campi necessari per la pipeline AI di Fase 3 (Contenuti delle
lezioni). La generazione AI è per lezione: ogni lezione ha il proprio
stato indipendente, lo stato del corso è derivato.

Inoltre aggiunge il glossario del corso (§10.1), generato una sola volta
da Fase 1 + documenti, riusato come `{{glossario}}` in tutti i prompt
successivi (Fasi 2,3,5). In questa iterazione lo creiamo come prerequisito
di Fase 3 (auto-trigger al primo task del worker se assente).

Su `course_lesson` (10 colonne content_*):
  - content_status (VARCHAR 40, default 'empty')
        empty | pending | processing | ready | approved | failed
  - content_raw (JSONB nullable) — output AI completo §6.3
  - content_tokens (JSONB nullable) — usage OpenAI
  - content_attempts (SMALLINT, default 0)
  - content_error (TEXT nullable)
  - content_generated_at (TIMESTAMPTZ nullable)
  - content_approved_at (TIMESTAMPTZ nullable)
  - content_regeneration_hint (TEXT nullable)
  - content_progress (SMALLINT, default 0, 0-100)
  - content_progress_phase (VARCHAR 50 nullable)

Su `course` (5 colonne glossary_*):
  - glossary_status (VARCHAR 40, default 'empty')
        empty | pending | processing | ready | approved | failed
  - glossary_raw (JSONB nullable) — {course_id, terms:[{term,translation,usage_note}]}
  - glossary_tokens (JSONB nullable)
  - glossary_generated_at (TIMESTAMPTZ nullable)
  - glossary_error (VARCHAR 500 nullable)

Su `course.status` (CHECK constraint): aggiunge `content_approved`
(`content_pending`/`_ready` erano già presenti come scaffolding).

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-03
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # course_lesson — payload Fase 3 (10 colonne)
    op.add_column(
        "course_lesson",
        sa.Column(
            "content_status",
            sa.String(length=40),
            nullable=False,
            server_default="empty",
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "content_raw",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "content_tokens",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "content_attempts",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column("content_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "content_generated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "content_approved_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column("content_regeneration_hint", sa.Text(), nullable=True),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "content_progress",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "content_progress_phase",
            sa.String(length=50),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_course_lesson_content_status",
        "course_lesson",
        "content_status IN "
        "('empty','pending','processing','ready','approved','failed')",
    )
    op.create_check_constraint(
        "ck_course_lesson_content_progress",
        "course_lesson",
        "content_progress >= 0 AND content_progress <= 100",
    )

    # course — glossario (5 colonne)
    op.add_column(
        "course",
        sa.Column(
            "glossary_status",
            sa.String(length=40),
            nullable=False,
            server_default="empty",
        ),
    )
    op.add_column(
        "course",
        sa.Column(
            "glossary_raw",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "course",
        sa.Column(
            "glossary_tokens",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "course",
        sa.Column(
            "glossary_generated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "course",
        sa.Column("glossary_error", sa.String(length=500), nullable=True),
    )
    op.create_check_constraint(
        "ck_course_glossary_status",
        "course",
        "glossary_status IN "
        "('empty','pending','processing','ready','approved','failed')",
    )

    # course.status — estendi CHECK constraint per aggiungere
    # `content_approved` (content_pending e content_ready erano già lì).
    op.drop_constraint("ck_course_status_valid", "course", type_="check")
    op.create_check_constraint(
        "ck_course_status_valid",
        "course",
        "status IN ("
        "'draft','architecture_pending','architecture_ready',"
        "'architecture_approved','lessons_structure_pending',"
        "'lessons_structure_ready','lessons_structure_approved',"
        "'content_pending','content_ready','content_approved',"
        "'slides_pending','slides_ready',"
        "'speech_pending','speech_ready',"
        "'published','archived')",
    )


def downgrade() -> None:
    # Ripristina il CHECK constraint senza `content_approved`.
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

    # course — drop glossary
    op.drop_constraint("ck_course_glossary_status", "course", type_="check")
    op.drop_column("course", "glossary_error")
    op.drop_column("course", "glossary_generated_at")
    op.drop_column("course", "glossary_tokens")
    op.drop_column("course", "glossary_raw")
    op.drop_column("course", "glossary_status")

    # course_lesson — drop check + 10 colonne (ordine inverso)
    op.drop_constraint(
        "ck_course_lesson_content_progress", "course_lesson", type_="check"
    )
    op.drop_constraint(
        "ck_course_lesson_content_status", "course_lesson", type_="check"
    )
    op.drop_column("course_lesson", "content_progress_phase")
    op.drop_column("course_lesson", "content_progress")
    op.drop_column("course_lesson", "content_regeneration_hint")
    op.drop_column("course_lesson", "content_approved_at")
    op.drop_column("course_lesson", "content_generated_at")
    op.drop_column("course_lesson", "content_error")
    op.drop_column("course_lesson", "content_attempts")
    op.drop_column("course_lesson", "content_tokens")
    op.drop_column("course_lesson", "content_raw")
    op.drop_column("course_lesson", "content_status")
