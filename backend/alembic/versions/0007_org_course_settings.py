"""organization_course_settings + course_config:manage permission

Crea la tabella `organization_course_settings` (1:1 con `organizations`)
con i parametri di default per la creazione dei corsi:
  - modules_per_cfu (default 1)
  - lessons_per_module (default 8)
  - lesson_duration_minutes (default 15)
  - assessment_lesson_enabled (default true) — la "Verifica di apprendimento
    finale" è l'ultima lezione di ogni modulo.
  - multiple_choice_questions_count (default 30)
  - open_questions_count (default 6)

Backfilla con i default tutte le organizzazioni esistenti (deleted_at IS NULL).
Per le NUOVE org create dopo questa migrazione, è il service `org_service`
che crea il record di default in transazione.

Aggiunge il permesso `course_config:manage` e lo collega ai ruoli Creator
e OrgAdmin di default (Manager e Member non lo ricevono).

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-28
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) Tabella parametri corso (1:1 con organizations).
    op.create_table(
        "organization_course_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "modules_per_cfu",
            sa.SmallInteger(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "lessons_per_module",
            sa.SmallInteger(),
            nullable=False,
            server_default="8",
        ),
        sa.Column(
            "lesson_duration_minutes",
            sa.SmallInteger(),
            nullable=False,
            server_default="15",
        ),
        sa.Column(
            "assessment_lesson_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "multiple_choice_questions_count",
            sa.SmallInteger(),
            nullable=False,
            server_default="30",
        ),
        sa.Column(
            "open_questions_count",
            sa.SmallInteger(),
            nullable=False,
            server_default="6",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "organization_id", name="uq_organization_course_settings_organization_id"
        ),
        sa.CheckConstraint(
            "modules_per_cfu >= 1",
            name="ck_organization_course_settings_modules_per_cfu_min",
        ),
        sa.CheckConstraint(
            "lessons_per_module >= 1",
            name="ck_organization_course_settings_lessons_per_module_min",
        ),
        sa.CheckConstraint(
            "lesson_duration_minutes >= 1",
            name="ck_organization_course_settings_lesson_duration_minutes_min",
        ),
        sa.CheckConstraint(
            "multiple_choice_questions_count >= 0",
            name="ck_organization_course_settings_multiple_choice_questions_count_min",
        ),
        sa.CheckConstraint(
            "open_questions_count >= 0",
            name="ck_organization_course_settings_open_questions_count_min",
        ),
    )

    # 2) Backfill: una riga di default per ogni organizzazione attiva.
    op.execute(
        """
        INSERT INTO organization_course_settings (id, organization_id)
        SELECT uuid_generate_v4(), id FROM organizations WHERE deleted_at IS NULL;
        """
    )

    # 3) Inserimento del nuovo permesso e collegamento ai ruoli di default.
    op.execute(
        """
        INSERT INTO permissions (id, code, description, scope)
        SELECT uuid_generate_v4(), 'course_config:manage',
               'Configura i parametri di default per la creazione dei corsi.',
               'organization'
        WHERE NOT EXISTS (
            SELECT 1 FROM permissions WHERE code = 'course_config:manage'
        );
        """
    )
    op.execute(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM organization_roles r
        CROSS JOIN permissions p
        WHERE r.code IN ('creator', 'org_admin')
          AND p.code = 'course_config:manage'
          AND NOT EXISTS (
              SELECT 1 FROM role_permissions rp
              WHERE rp.role_id = r.id AND rp.permission_id = p.id
          );
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM organization_role_permissions
        WHERE permission_id IN (
            SELECT id FROM permissions WHERE code = 'course_config:manage'
        );
        """
    )
    op.execute(
        """
        DELETE FROM membership_permission_overrides
        WHERE permission_id IN (
            SELECT id FROM permissions WHERE code = 'course_config:manage'
        );
        """
    )
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE permission_id IN (
            SELECT id FROM permissions WHERE code = 'course_config:manage'
        );
        """
    )
    op.execute("DELETE FROM permissions WHERE code = 'course_config:manage';")
    op.drop_table("organization_course_settings")
