"""figure di fonte: ingressi della risoluzione effettiva e ri-ritaglio

Campagna «risoluzione effettiva e copertura per concetto» (WP2, doc 18
§22). La classe di risoluzione (good, acceptable, low, unusable) NON si
salva: si calcola in lettura da questi ingressi
(`services/source_figure_resolution.py`), così non va mai fuori sincrono.

- `course_document_figure.native_ppi`: ppi nativo del raster dominante,
  nelle unità della pagina della fonte (NULL = vettoriale o ignoto);
- `natural_width_mm`: misura naturale della figura, normalizzata alla
  pagina (`min(1, 612/page_w)`: le slide 16:9 esportate in PDF non
  valgono come fogli da 34 cm);
- `crop_mode`, `crop_version`: come è stato fatto il ritaglio (v1 = il
  ritaglio storico a 150-300 dpi, JPEG per i raster; v2 = griglia nativa,
  PNG per il tratto);
- `recropped_at`, `recrop_previous`: ri-ritaglio sul posto (stesso UUID)
  con i metadati e i percorsi del ritaglio v1, per `--revert`;
- `course_document.figures_recrop_requested_at` (richiesta a lease del
  ri-ritaglio, eseguita dal worker delle figure) e `figures_recrop_stats`
  (esito dell'ultimo ri-ritaglio: conteggi, tentativi, errore).

Solo ADD: le righe esistenti restano `crop_version = 1` con gli ingressi
NULL. La classe usa allora il bbox e i dpi del render (righe dei
documenti) o la misura convenzionale di 90 mm (Commons: sotto circa
355 px diventano `unusable`); solo le righe senza bbox né dpi, non di
Commons, si stimano e non sono mai `unusable`. Downgrade pulito.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers
revision: str = "0039"
down_revision: str | None = "0038"
branch_labels = None
depends_on = None

# Condizioni dei CHECK (uguali a quelle dei modelli: un test ne verifica la
# parità).
_FIGURE_CHECKS: tuple[tuple[str, str], ...] = (
    (
        "ck_course_document_figure_native_ppi_positive",
        "native_ppi IS NULL OR native_ppi > 0",
    ),
    (
        "ck_course_document_figure_natural_width_positive",
        "natural_width_mm IS NULL OR natural_width_mm > 0",
    ),
    (
        "ck_course_document_figure_crop_mode_valid",
        "crop_mode IS NULL OR crop_mode IN ('raster_native','mixed','vector','office','external')",
    ),
    (
        "ck_course_document_figure_crop_version_min",
        "crop_version >= 1",
    ),
)


def upgrade() -> None:
    op.add_column(
        "course_document_figure",
        sa.Column("native_ppi", sa.REAL(), nullable=True),
    )
    op.add_column(
        "course_document_figure",
        sa.Column("natural_width_mm", sa.REAL(), nullable=True),
    )
    op.add_column(
        "course_document_figure",
        sa.Column("crop_mode", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "course_document_figure",
        sa.Column("crop_version", sa.SmallInteger(), nullable=False, server_default="1"),
    )
    op.add_column(
        "course_document_figure",
        sa.Column("recropped_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "course_document_figure",
        sa.Column("recrop_previous", postgresql.JSONB(), nullable=True),
    )
    for name, condition in _FIGURE_CHECKS:
        op.create_check_constraint(name, "course_document_figure", condition)

    op.add_column(
        "course_document",
        sa.Column("figures_recrop_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "course_document",
        sa.Column("figures_recrop_stats", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    for column in ("figures_recrop_stats", "figures_recrop_requested_at"):
        op.drop_column("course_document", column)
    for name, _condition in reversed(_FIGURE_CHECKS):
        op.drop_constraint(name, "course_document_figure", type_="check")
    for column in (
        "recrop_previous",
        "recropped_at",
        "crop_version",
        "crop_mode",
        "natural_width_mm",
        "native_ppi",
    ):
        op.drop_column("course_document_figure", column)
