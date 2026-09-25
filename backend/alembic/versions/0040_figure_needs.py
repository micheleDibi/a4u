"""piano delle figure di fonte: fabbisogni per lezione (PROMPT 22)

Campagna «risoluzione effettiva e copertura per concetto», WP4 (doc 18
§23). I fabbisogni di figure di fonte di una lezione si calcolano quando
si chiede la sua Fase 3 e valgono finché l'impronta dell'input non cambia.

- `course_lesson.figure_needs` (JSONB): `{v, fingerprint, model, needs,
  dropped}`;
- `figure_needs_status`: NULL (mai chiesti) | pending | processing |
  ready | failed | skipped, con CHECK;
- `figure_needs_attempts`, `figure_needs_requested_at` (richiesta di Fase
  3: conta il tetto dell'attesa), `figure_needs_checked_at`;
- `figure_needs_usage` (JSONB): costo cumulativo del PROMPT 22;
- indice parziale sulle lezioni `pending` (coda del worker).

Solo ADD: nessuna riscrittura delle righe esistenti; downgrade pulito.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers
revision: str = "0040"
down_revision: str | None = "0039"
branch_labels = None
depends_on = None

_STATUS_CHECK = (
    "figure_needs_status IS NULL OR figure_needs_status IN "
    "('pending','processing','ready','failed','skipped')"
)


def upgrade() -> None:
    op.add_column("course_lesson", sa.Column("figure_needs", postgresql.JSONB(), nullable=True))
    op.add_column(
        "course_lesson", sa.Column("figure_needs_status", sa.String(length=20), nullable=True)
    )
    op.add_column(
        "course_lesson",
        sa.Column("figure_needs_attempts", sa.SmallInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "course_lesson",
        sa.Column("figure_needs_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "course_lesson",
        sa.Column("figure_needs_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "course_lesson", sa.Column("figure_needs_usage", postgresql.JSONB(), nullable=True)
    )
    op.create_check_constraint(
        "ck_course_lesson_figure_needs_status", "course_lesson", _STATUS_CHECK
    )
    op.create_index(
        "ix_course_lesson_figure_needs_pending",
        "course_lesson",
        ["figure_needs_requested_at"],
        postgresql_where=sa.text("figure_needs_status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("ix_course_lesson_figure_needs_pending", table_name="course_lesson")
    op.drop_constraint("ck_course_lesson_figure_needs_status", "course_lesson", type_="check")
    for column in (
        "figure_needs_usage",
        "figure_needs_checked_at",
        "figure_needs_requested_at",
        "figure_needs_attempts",
        "figure_needs_status",
        "figure_needs",
    ):
        op.drop_column("course_lesson", column)
