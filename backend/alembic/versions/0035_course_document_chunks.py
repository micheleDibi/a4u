"""course_document: copertura totale dell'analisi (chunk + coverage)

Blocco 1 "copertura completa": la pipeline di riassunto passa da
single-shot troncato a 120k char a map→merge→reduce per i documenti
lunghi. Questa migrazione aggiunge:

- la tabella `course_document_chunk` (risultato per-chunk, insert-only
  -on-success, UNIQUE (document_id, chunk_index), CASCADE sul delete
  del documento — righe effimere: cancellate al successo del reduce,
  conservate su failure per la ripresa);
- 4 colonne nullable su `course_document`: `summary_coverage`
  ('full'|'partial', NULL = riassunto legacy pre-feature — nessun
  backfill), `summary_chunks_total` / `summary_chunks_done` (progresso
  per la UI), `summary_fingerprint` (sha256 di bytes file + parametri:
  invalida i chunk se il run cambia).

Solo ADD nullable + CREATE TABLE: nessuna riscrittura di righe
esistenti; downgrade pulito.
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers
revision: str = "0035"
down_revision: str | None = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "course_document",
        sa.Column("summary_coverage", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "course_document",
        sa.Column("summary_chunks_total", sa.Integer(), nullable=True),
    )
    op.add_column(
        "course_document",
        sa.Column("summary_chunks_done", sa.Integer(), nullable=True),
    )
    op.add_column(
        "course_document",
        sa.Column("summary_fingerprint", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        "ck_course_document_summary_coverage_valid",
        "course_document",
        "summary_coverage IN ('full','partial')",
    )

    op.create_table(
        "course_document_chunk",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
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
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("course_document.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("tokens", postgresql.JSONB(), nullable=True),
        sa.UniqueConstraint(
            "document_id", "chunk_index",
            name="uq_course_document_chunk_index",
        ),
        sa.CheckConstraint(
            "chunk_index >= 0",
            name="ck_course_document_chunk_index_min",
        ),
        sa.CheckConstraint(
            "char_end > char_start",
            name="ck_course_document_chunk_char_range",
        ),
    )
    op.create_index(
        "ix_course_document_chunk_document_id",
        "course_document_chunk",
        ["document_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_course_document_chunk_document_id",
        table_name="course_document_chunk",
    )
    op.drop_table("course_document_chunk")
    op.drop_constraint(
        "ck_course_document_summary_coverage_valid",
        "course_document",
        type_="check",
    )
    op.drop_column("course_document", "summary_fingerprint")
    op.drop_column("course_document", "summary_chunks_done")
    op.drop_column("course_document", "summary_chunks_total")
    op.drop_column("course_document", "summary_coverage")
