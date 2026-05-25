"""course duplication in another language

Aggiunge la tabella `course_duplication_job` per orchestrare il job
asincrono di duplicazione di un corso in un'altra lingua. Il worker
`course_duplication_worker` legge le righe `status='pending'`, crea il
corso target (clone della shell con video/avatar/pdf resettati), e
traduce via OpenAI tutti i contenuti.

Schema tabella:
  - id (UUID PK)
  - source_course_id (UUID FK course CASCADE) — corso sorgente
  - target_course_id (UUID FK course SET NULL nullable) — popolata
    dopo la phase 'cloning_structure'
  - target_language_code (VARCHAR 10 FK languages RESTRICT)
  - status (VARCHAR 40, CHECK in 'pending|processing|ready|failed')
  - progress (SMALLINT 0-100, default 0)
  - progress_phase (VARCHAR 50 nullable) — loading_source |
    cloning_structure | translating_architecture |
    translating_content | translating_slides | translating_speech |
    translating_glossary_documents | finalizing
  - error (TEXT nullable)
  - attempts (SMALLINT default 0)
  - tokens (JSONB nullable) — aggregato cost/token consumati
  - requested_by_user_id (UUID FK users SET NULL nullable)
  - started_at, finished_at, created_at, updated_at (TIMESTAMPTZ)

Indici:
  - `ix_course_duplication_job_source` su source_course_id
  - `ix_course_duplication_job_target` su target_course_id
  - `ix_course_duplication_job_status` su status
  - UNIQUE PARZIALE `uq_course_duplication_active`:
    `(source_course_id, target_language_code) WHERE status IN
    ('pending','processing')`. Impedisce job concorrenti per la
    stessa coppia (source, lingua target) a livello DB; il service
    fa anche il controllo applicativo per messaggi d'errore migliori.

Revision ID: 0031
Revises: 0030
Create Date: 2026-05-25
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "course_duplication_job",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "source_course_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("course.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_course_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("course.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "target_language_code",
            sa.String(length=10),
            sa.ForeignKey("languages.code", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=40),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "progress",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("progress_phase", sa.String(length=50), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "attempts",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("tokens", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "requested_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('pending','processing','ready','failed')",
            name="ck_course_duplication_job_status",
        ),
        sa.CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name="ck_course_duplication_job_progress",
        ),
    )
    op.create_index(
        "ix_course_duplication_job_source",
        "course_duplication_job",
        ["source_course_id"],
    )
    op.create_index(
        "ix_course_duplication_job_target",
        "course_duplication_job",
        ["target_course_id"],
    )
    op.create_index(
        "ix_course_duplication_job_status",
        "course_duplication_job",
        ["status"],
    )
    # Unique parziale: impedisce job concorrenti per la stessa coppia
    # (source_course, target_language). Funziona solo su Postgres.
    op.execute(
        "CREATE UNIQUE INDEX uq_course_duplication_active "
        "ON course_duplication_job (source_course_id, target_language_code) "
        "WHERE status IN ('pending','processing')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_course_duplication_active")
    op.drop_index(
        "ix_course_duplication_job_status", table_name="course_duplication_job"
    )
    op.drop_index(
        "ix_course_duplication_job_target", table_name="course_duplication_job"
    )
    op.drop_index(
        "ix_course_duplication_job_source", table_name="course_duplication_job"
    )
    op.drop_table("course_duplication_job")
