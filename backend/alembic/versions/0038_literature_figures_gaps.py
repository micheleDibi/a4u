"""figure della letteratura aperta: identità esterna e buchi per lezione

WP5 della campagna «figure da letteratura»: se le figure di fonte
pertinenti dai documenti del corso sono meno di
FIGURE_SOURCE_MIN_PER_LESSON, la letteratura aperta (Wikimedia Commons,
OpenAlex) integra, prima della Fase 3, con righe del catalogo senza
documento (`source_kind` wikimedia/openalex, attribuzione congelata).
Questa migrazione aggiunge:

- `course_document_figure.external_id`, `source_url`, `retrieved_at`:
  identità e pagina della figura esterna; indice unico parziale
  (course_id, source_kind, external_id) sulle righe senza documento con
  `external_id`: la stessa figura esterna entra una volta per corso;
- `course_lesson.figures_gap_*`: stato della verifica dei buchi della
  lezione (NULL = mai verificata), tentativi, date, costo delle chiamate
  AI della ricerca (termini e pertinenza, sommato dalla dashboard admin)
  ed esito.

Solo ADD + CREATE INDEX: nessuna riscrittura di righe esistenti;
downgrade pulito.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers
revision: str = "0038"
down_revision: str | None = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- course_document_figure: identità esterna ------------------------
    op.add_column(
        "course_document_figure",
        sa.Column("external_id", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "course_document_figure",
        sa.Column("source_url", sa.String(length=1000), nullable=True),
    )
    op.add_column(
        "course_document_figure",
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_course_document_figure_external",
        "course_document_figure",
        ["course_id", "source_kind", "external_id"],
        unique=True,
        postgresql_where=sa.text("document_id IS NULL AND external_id IS NOT NULL"),
    )

    # --- course_lesson: buchi di figure di fonte --------------------------
    op.add_column(
        "course_lesson",
        sa.Column("figures_gap_status", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "course_lesson",
        sa.Column(
            "figures_gap_attempts",
            sa.SmallInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "course_lesson",
        sa.Column("figures_gap_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "course_lesson",
        sa.Column("figures_gap_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "course_lesson",
        sa.Column("figures_gap_usage", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "course_lesson",
        sa.Column("figures_gap_stats", postgresql.JSONB(), nullable=True),
    )
    op.create_check_constraint(
        "ck_course_lesson_figures_gap_status",
        "course_lesson",
        "figures_gap_status IS NULL OR figures_gap_status IN "
        "('pending','processing','done','skipped','failed')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_course_lesson_figures_gap_status", "course_lesson", type_="check")
    for column in (
        "figures_gap_stats",
        "figures_gap_usage",
        "figures_gap_checked_at",
        "figures_gap_requested_at",
        "figures_gap_attempts",
        "figures_gap_status",
    ):
        op.drop_column("course_lesson", column)

    op.drop_index(
        "uq_course_document_figure_external",
        table_name="course_document_figure",
    )
    for column in ("retrieved_at", "source_url", "external_id"):
        op.drop_column("course_document_figure", column)
