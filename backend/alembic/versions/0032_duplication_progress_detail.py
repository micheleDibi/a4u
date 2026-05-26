"""course duplication: progress_detail per sotto-progresso UX

Aggiunge la colonna `progress_detail` (VARCHAR 200 nullable) a
`course_duplication_job`. Il worker la popola durante la combined
phase con messaggi come "23/48 lezioni completate" per dare
all'utente un feedback piu fine rispetto al solo `progress_phase`.

Il campo e mostrato dal FE come riga aggiuntiva sotto la phase label
nel `CourseDuplicationBadge`. NULL se non applicabile (es. fasi
brevi come `loading_source` o `finalizing`).
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0032"
down_revision: str | None = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "course_duplication_job",
        sa.Column("progress_detail", sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("course_duplication_job", "progress_detail")
