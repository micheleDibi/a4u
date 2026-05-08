"""course + course_document + 6 nuovi permessi corso

Crea le tabelle `course` (parametri del corso, snapshot dei parametri org,
status lifecycle pronto per le 5 fasi AI future) e `course_document`
(documenti di riferimento caricati dal docente, con campi summary
predisposti per il pre-processing dell'Appendice A).

Aggiunge 6 permessi org-scoped:
  - course:view, course:create, course:assign, course:edit,
    course:delete, course:generate
e li lega ai ruoli di default:
  - member:    view
  - manager:   view, create, assign, edit, generate
  - creator:   view, create, assign, edit, delete, generate
  - org_admin: come creator

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-03
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Permission seed data — codici, descrizioni, role bindings
# ---------------------------------------------------------------------------

PERMISSION_DEFINITIONS: list[tuple[str, str]] = [
    (
        "course:view",
        "Vedere l'elenco dei corsi dell'organizzazione. Senza il permesso "
        "di modifica, il membro vede solo i corsi a lui assegnati.",
    ),
    (
        "course:create",
        "Creare nuovi corsi nell'organizzazione, scegliendo titolo, "
        "obiettivi, tassonomie didattiche, numero di CFU e caricando i "
        "documenti di riferimento.",
    ),
    (
        "course:assign",
        "Cambiare l'assegnatario di un corso esistente, scegliendo tra i "
        "membri attivi dell'organizzazione.",
    ),
    (
        "course:edit",
        "Modificare i parametri di un corso (titolo, obiettivi, "
        "tassonomie, CFU, argomenti chiave, lingua) e gestire i documenti "
        "di riferimento. Necessario anche per visualizzare tutti i corsi "
        "dell'organizzazione (non solo quelli assegnati).",
    ),
    (
        "course:delete",
        "Eliminare un corso e tutti i suoi documenti di riferimento. "
        "Operazione irreversibile.",
    ),
    (
        "course:generate",
        "Avviare la generazione AI dei contenuti del corso (architettura, "
        "lezioni, slide, discorso TTS) a partire dai parametri configurati.",
    ),
]

ROLE_BINDINGS: dict[str, list[str]] = {
    "member": ["course:view"],
    "manager": [
        "course:view",
        "course:create",
        "course:assign",
        "course:edit",
        "course:generate",
    ],
    "creator": [
        "course:view",
        "course:create",
        "course:assign",
        "course:edit",
        "course:delete",
        "course:generate",
    ],
    "org_admin": [
        "course:view",
        "course:create",
        "course:assign",
        "course:edit",
        "course:delete",
        "course:generate",
    ],
}


def upgrade() -> None:
    # 1) Tabella course
    op.create_table(
        "course",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("objectives", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "language_code",
            sa.String(10),
            sa.ForeignKey("languages.code", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "categoria_term_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("course_taxonomy_term.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "stile_insegnamento_term_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("course_taxonomy_term.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "profondita_contenuto_term_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("course_taxonomy_term.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "ruolo_docente_term_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("course_taxonomy_term.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "dimensione_pubblico_term_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("course_taxonomy_term.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "livello_conoscenza_term_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("course_taxonomy_term.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "destinatari_term_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("course_taxonomy_term.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "livello_eqf_term_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("course_taxonomy_term.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "argomenti_chiave",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("cfu", sa.SmallInteger(), nullable=False),
        sa.Column("modules_count", sa.SmallInteger(), nullable=False),
        sa.Column("lessons_per_module", sa.SmallInteger(), nullable=False),
        sa.Column("lesson_duration_minutes", sa.SmallInteger(), nullable=False),
        sa.Column("assessment_lesson_enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "multiple_choice_questions_count", sa.SmallInteger(), nullable=False
        ),
        sa.Column("open_questions_count", sa.SmallInteger(), nullable=False),
        sa.Column(
            "assignee_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(40),
            nullable=False,
            server_default="draft",
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
        sa.CheckConstraint("cfu >= 1", name="ck_course_cfu_min"),
        sa.CheckConstraint(
            "modules_count >= 1", name="ck_course_modules_count_min"
        ),
        sa.CheckConstraint(
            "lessons_per_module >= 1", name="ck_course_lessons_per_module_min"
        ),
        sa.CheckConstraint(
            "lesson_duration_minutes >= 1",
            name="ck_course_lesson_duration_minutes_min",
        ),
        sa.CheckConstraint(
            "multiple_choice_questions_count >= 0",
            name="ck_course_multiple_choice_questions_count_min",
        ),
        sa.CheckConstraint(
            "open_questions_count >= 0",
            name="ck_course_open_questions_count_min",
        ),
        sa.CheckConstraint(
            "status IN ('draft','architecture_pending','architecture_ready',"
            "'architecture_approved','lessons_structure_pending',"
            "'lessons_structure_ready','content_pending','content_ready',"
            "'slides_pending','slides_ready','speech_pending','speech_ready',"
            "'published','archived')",
            name="ck_course_status_valid",
        ),
    )
    op.create_index("ix_course_org_status", "course", ["organization_id", "status"])
    op.create_index(
        "ix_course_org_assignee", "course", ["organization_id", "assignee_user_id"]
    )
    op.create_index(
        "ix_course_org_language", "course", ["organization_id", "language_code"]
    )

    # 2) Tabella course_document
    op.create_table(
        "course_document",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "course_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("course.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename_original", sa.String(300), nullable=False),
        sa.Column("filename_stored", sa.String(300), nullable=False, unique=True),
        sa.Column("file_path", sa.String(500), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "uploaded_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "summary_status",
            sa.String(40),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("summary_error", sa.Text(), nullable=True),
        sa.Column(
            "summary_generated_at",
            sa.DateTime(timezone=True),
            nullable=True,
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
        sa.CheckConstraint(
            "size_bytes >= 0", name="ck_course_document_size_bytes_min"
        ),
        sa.CheckConstraint(
            "summary_status IN ('pending','processing','ready','failed')",
            name="ck_course_document_summary_status_valid",
        ),
    )
    op.create_index(
        "ix_course_document_course_id", "course_document", ["course_id"]
    )

    # 3) 6 nuovi permessi
    for code, description in PERMISSION_DEFINITIONS:
        op.execute(
            sa.text(
                """
                INSERT INTO permissions (id, code, description, scope)
                SELECT uuid_generate_v4(), :code, :description, 'organization'
                WHERE NOT EXISTS (
                    SELECT 1 FROM permissions WHERE code = :code
                )
                """
            ).bindparams(code=code, description=description)
        )

    # 4) Bindings ai ruoli di default
    bind = op.get_bind()
    insert_binding_sql = sa.text(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM organization_roles r
        CROSS JOIN permissions p
        WHERE r.code = :role_code
          AND p.code = :perm_code
          AND NOT EXISTS (
              SELECT 1 FROM role_permissions rp
              WHERE rp.role_id = r.id AND rp.permission_id = p.id
          )
        """
    )
    for role_code, perms in ROLE_BINDINGS.items():
        for perm_code in perms:
            bind.execute(insert_binding_sql, {"role_code": role_code, "perm_code": perm_code})


def downgrade() -> None:
    # Rimuovi override e bindings prima dei permessi
    perm_codes_csv = ",".join(f"'{code}'" for code, _ in PERMISSION_DEFINITIONS)
    op.execute(
        f"""
        DELETE FROM organization_role_permissions
        WHERE permission_id IN (
            SELECT id FROM permissions WHERE code IN ({perm_codes_csv})
        )
        """
    )
    op.execute(
        f"""
        DELETE FROM membership_permission_overrides
        WHERE permission_id IN (
            SELECT id FROM permissions WHERE code IN ({perm_codes_csv})
        )
        """
    )
    op.execute(
        f"""
        DELETE FROM role_permissions
        WHERE permission_id IN (
            SELECT id FROM permissions WHERE code IN ({perm_codes_csv})
        )
        """
    )
    op.execute(
        f"DELETE FROM permissions WHERE code IN ({perm_codes_csv})"
    )

    # Tabelle (course_document prima per via FK)
    op.drop_index(
        "ix_course_document_course_id", table_name="course_document"
    )
    op.drop_table("course_document")
    op.drop_index("ix_course_org_language", table_name="course")
    op.drop_index("ix_course_org_assignee", table_name="course")
    op.drop_index("ix_course_org_status", table_name="course")
    op.drop_table("course")
