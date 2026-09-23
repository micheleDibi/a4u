"""figure da letteratura: catalogo delle figure di fonte e provenienza

Le dispense possono contenere figure prese dalle fonti (documenti del
corso, poi letteratura aperta), numerate come le altre e sempre con la
riga di attribuzione calcolata a render. Questa migrazione aggiunge:

- `course_document`: provenienza (`origin`, `is_own_work`, `license`,
  `license_source`, `bibliography`, `bibliography_source`) e stato
  dell'estrazione figure, separato da quello del riassunto
  (`figures_status` e colonne collegate). Colonne nullable o con
  server_default: nessun backfill, NULL = estrazione mai richiesta;
- la tabella `course_document_figure` (catalogo per corso). La FK verso il
  documento è ON DELETE SET NULL: alla cancellazione del documento il
  servizio stacca le figure ancora usate (attribuzione congelata) e
  cancella le altre (decisione non retroattiva). `license` è NOT NULL
  senza default: ogni percorso di creazione la valorizza, anche
  'unknown';
- `course_lesson.content_figure_review`: esiti del revisore delle figure
  di fonte, fuori da `content_raw`;
- `organization_course_settings.figure_source_license_policy`: override
  per organizzazione di FIGURE_SOURCE_LICENSE_POLICY (NULL = eredita).

Cambio di decisione dichiarato: il lavoro «figure accademiche» teneva
gli asset solo in JSONB; qui il catalogo delle figure di fonte è una
tabella, mentre la lezione continua a citarle da `content_raw`.

Solo ADD + CREATE TABLE: nessuna riscrittura di righe esistenti;
downgrade pulito.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers
revision: str = "0037"
down_revision: str | None = "0036"
branch_labels = None
depends_on = None


_DOC_CHECKS: tuple[tuple[str, str], ...] = (
    (
        "ck_course_document_origin_valid",
        "origin IN ('upload','paper_import','paper_metadata')",
    ),
    (
        "ck_course_document_license_valid",
        "license IS NULL OR license IN ('cc0','public_domain','cc_by',"
        "'cc_by_sa','cc_by_nc','cc_by_nd','cc_by_nc_sa','cc_by_nc_nd',"
        "'all_rights_reserved','other')",
    ),
    (
        "ck_course_document_license_source_valid",
        "license_source IS NULL OR license_source IN ('user','openalex')",
    ),
    (
        "ck_course_document_bibliography_source_valid",
        "bibliography_source IS NULL OR bibliography_source IN "
        "('user','openalex','pdf_metadata','crossref','summary_proposal')",
    ),
    (
        "ck_course_document_figures_status_valid",
        "figures_status IS NULL OR figures_status IN "
        "('pending','processing','ready','failed','skipped')",
    ),
    (
        "ck_course_document_figures_error_code_valid",
        "figures_error_code IS NULL OR figures_error_code IN "
        "('policy_excluded','policy_content_only','unsupported_format',"
        "'extraction_disabled','source_missing','encrypted','corrupt',"
        "'engine_unavailable','timeout','crashed','oom','crashed_repeatedly',"
        "'resources_unavailable','vision_unavailable','storage_error',"
        "'attempts_exhausted')",
    ),
    (
        "ck_course_document_figures_coverage_valid",
        "figures_coverage IS NULL OR figures_coverage IN ('full','partial')",
    ),
)


def upgrade() -> None:
    # --- course_document: provenienza -----------------------------------
    op.add_column(
        "course_document",
        sa.Column(
            "origin",
            sa.String(length=20),
            nullable=False,
            server_default="upload",
        ),
    )
    op.add_column(
        "course_document",
        sa.Column(
            "is_own_work",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "course_document",
        sa.Column("license", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "course_document",
        sa.Column("license_source", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "course_document",
        sa.Column("bibliography", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "course_document",
        sa.Column("bibliography_source", sa.String(length=20), nullable=True),
    )

    # --- course_document: stato dell'estrazione figure -------------------
    op.add_column(
        "course_document",
        sa.Column("figures_status", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "course_document",
        sa.Column("figures_error_code", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "course_document",
        sa.Column("figures_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "course_document",
        sa.Column("figures_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "course_document",
        sa.Column(
            "figures_attempts",
            sa.SmallInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "course_document",
        sa.Column("figures_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "course_document",
        sa.Column("figures_next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "course_document",
        sa.Column("figures_coverage", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "course_document",
        sa.Column("figures_engine", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "course_document",
        sa.Column("figures_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "course_document",
        sa.Column("figures_pages_total", sa.Integer(), nullable=True),
    )
    op.add_column(
        "course_document",
        sa.Column("figures_pages_done", sa.Integer(), nullable=True),
    )
    op.add_column(
        "course_document",
        sa.Column("figures_progress", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "course_document",
        sa.Column("figures_stats", postgresql.JSONB(), nullable=True),
    )
    for name, condition in _DOC_CHECKS:
        op.create_check_constraint(name, "course_document", condition)

    # --- course_document_figure -------------------------------------------
    op.create_table(
        "course_document_figure",
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
        # Riferimenti
        sa.Column(
            "course_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("course.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("course_document.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("detached_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_kind", sa.String(length=20), nullable=False),
        # Posizione ed estrazione
        sa.Column("locator", sa.String(length=60), nullable=False),
        sa.Column("extraction_version", sa.SmallInteger(), nullable=False),
        sa.Column("engine", sa.String(length=20), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("bbox", postgresql.JSONB(), nullable=True),
        sa.Column("source_label", sa.String(length=60), nullable=True),
        sa.Column("source_caption", sa.Text(), nullable=True),
        sa.Column("context_excerpt", sa.Text(), nullable=True),
        # File
        sa.Column("storage_path", sa.String(length=500), nullable=True),
        sa.Column("preview_path", sa.String(length=500), nullable=True),
        sa.Column("mime_type", sa.String(length=40), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("dpi", sa.SmallInteger(), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=True),
        sa.Column("is_vector", sa.Boolean(), nullable=True),
        sa.Column("phash", sa.String(length=16), nullable=True),
        sa.Column("detector_class", sa.String(length=40), nullable=True),
        sa.Column("detector_confidence", sa.Float(), nullable=True),
        # Analisi Vision
        sa.Column("kind", sa.String(length=40), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("keywords", postgresql.JSONB(), nullable=True),
        sa.Column("quality_score", sa.SmallInteger(), nullable=True),
        sa.Column("is_useful_for_teaching", sa.Boolean(), nullable=True),
        sa.Column("legibility", sa.String(length=20), nullable=True),
        sa.Column("described_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("describe_model", sa.String(length=80), nullable=True),
        sa.Column(
            "describe_source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("course_document_figure.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("vision_usage", postgresql.JSONB(), nullable=True),
        sa.Column("vision_usage_at", sa.DateTime(timezone=True), nullable=True),
        # Stato
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reject_reason", sa.String(length=40), nullable=True),
        sa.Column(
            "duplicate_of_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("course_document_figure.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "excluded_by_user",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        # Diritti e attribuzione (license: NOT NULL senza server_default)
        sa.Column("license", sa.String(length=30), nullable=False),
        sa.Column("license_source", sa.String(length=20), nullable=True),
        sa.Column("license_url", sa.String(length=500), nullable=True),
        sa.Column("attribution", postgresql.JSONB(), nullable=True),
        sa.UniqueConstraint(
            "document_id",
            "locator",
            name="uq_course_document_figure_locator",
        ),
        sa.CheckConstraint(
            "source_kind IN ('uploaded','openalex','wikimedia')",
            name="ck_course_document_figure_source_kind_valid",
        ),
        sa.CheckConstraint(
            "source_kind <> 'uploaded' OR document_id IS NOT NULL OR detached_at IS NOT NULL",
            name="ck_course_document_figure_uploaded_has_document",
        ),
        sa.CheckConstraint(
            "source_kind = 'uploaded' OR (attribution IS NOT NULL "
            "AND jsonb_typeof(attribution) = 'object')",
            name="ck_course_document_figure_external_has_attribution",
        ),
        sa.CheckConstraint(
            "detached_at IS NULL OR (attribution IS NOT NULL "
            "AND jsonb_typeof(attribution) = 'object')",
            name="ck_course_document_figure_detached_has_attribution",
        ),
        sa.CheckConstraint(
            "license IN ('cc0','public_domain','cc_by','cc_by_sa','cc_by_nc',"
            "'cc_by_nd','cc_by_nc_sa','cc_by_nc_nd','all_rights_reserved',"
            "'other','unknown')",
            name="ck_course_document_figure_license_valid",
        ),
        sa.CheckConstraint(
            "license_source IS NULL OR license_source IN "
            "('document','openalex','wikimedia','user')",
            name="ck_course_document_figure_license_source_valid",
        ),
        sa.CheckConstraint(
            "status IN ('extracted','ready','rejected','failed')",
            name="ck_course_document_figure_status_valid",
        ),
        sa.CheckConstraint(
            "reject_reason IS NULL OR reject_reason IN ('too_small',"
            "'bad_aspect','blank','header_footer','detector_noise','repeated',"
            "'duplicate','unsupported_image_format','too_large',"
            "'describe_capped','superseded')",
            name="ck_course_document_figure_reject_reason_valid",
        ),
        sa.CheckConstraint(
            "mime_type IS NULL OR mime_type IN ('image/png','image/jpeg')",
            name="ck_course_document_figure_mime_type_valid",
        ),
        sa.CheckConstraint(
            "status <> 'ready' OR storage_path IS NOT NULL",
            name="ck_course_document_figure_ready_has_file",
        ),
        sa.CheckConstraint(
            "page IS NULL OR page >= 1",
            name="ck_course_document_figure_page_min",
        ),
        sa.CheckConstraint(
            "(width IS NULL OR width > 0) AND (height IS NULL OR height > 0)",
            name="ck_course_document_figure_size_positive",
        ),
        sa.CheckConstraint(
            "quality_score IS NULL OR quality_score BETWEEN 1 AND 5",
            name="ck_course_document_figure_quality_score_range",
        ),
    )
    op.create_index(
        "ix_course_document_figure_document_id",
        "course_document_figure",
        ["document_id"],
    )
    op.create_index(
        "ix_course_document_figure_course_status",
        "course_document_figure",
        ["course_id", "status"],
    )
    op.create_index(
        "ix_course_document_figure_course_phash",
        "course_document_figure",
        ["course_id", "phash"],
    )

    # --- course_lesson: esiti del revisore delle figure di fonte ---------
    op.add_column(
        "course_lesson",
        sa.Column("content_figure_review", postgresql.JSONB(), nullable=True),
    )

    # --- organization_course_settings: override della politica ----------
    op.add_column(
        "organization_course_settings",
        sa.Column("figure_source_license_policy", sa.String(length=20), nullable=True),
    )
    op.create_check_constraint(
        "figure_source_license_policy_valid",
        "organization_course_settings",
        "figure_source_license_policy IS NULL OR "
        "figure_source_license_policy IN ('cite_all','open_only')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "figure_source_license_policy_valid",
        "organization_course_settings",
        type_="check",
    )
    op.drop_column("organization_course_settings", "figure_source_license_policy")

    op.drop_column("course_lesson", "content_figure_review")

    op.drop_index(
        "ix_course_document_figure_course_phash",
        table_name="course_document_figure",
    )
    op.drop_index(
        "ix_course_document_figure_course_status",
        table_name="course_document_figure",
    )
    op.drop_index(
        "ix_course_document_figure_document_id",
        table_name="course_document_figure",
    )
    op.drop_table("course_document_figure")

    for name, _condition in reversed(_DOC_CHECKS):
        op.drop_constraint(name, "course_document", type_="check")
    for column in (
        "figures_stats",
        "figures_progress",
        "figures_pages_done",
        "figures_pages_total",
        "figures_fingerprint",
        "figures_engine",
        "figures_coverage",
        "figures_next_attempt_at",
        "figures_requested_at",
        "figures_attempts",
        "figures_count",
        "figures_error",
        "figures_error_code",
        "figures_status",
        "bibliography_source",
        "bibliography",
        "license_source",
        "license",
        "is_own_work",
        "origin",
    ):
        op.drop_column("course_document", column)
