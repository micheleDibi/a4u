"""course_document: politica di citazione (visibilità delle fonti)

Blocco 2 "visibilità dei documenti": un docente può caricare materiale
che viene elaborato e usato per generare i contenuti MA non compare mai
come fonte referenziata (bibliografia, citazioni, nome file).

Colonna `citation_policy` String(40) NOT NULL server_default='citable'
+ CHECK: 'citable' (storico), 'content_only' ("Fonte riservata":
contenuto usato, origine mai citata), 'excluded' (il riassunto non
entra in alcun prompt).

Backfill implicito: tutte le righe esistenti diventano 'citable' =
comportamento odierno invariato. Su Postgres >= 11 l'add_column con
server_default è metadata-only (nessun rewrite della tabella).
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers
revision: str = "0036"
down_revision: str | None = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "course_document",
        sa.Column(
            "citation_policy",
            sa.String(length=40),
            nullable=False,
            server_default="citable",
        ),
    )
    op.create_check_constraint(
        "ck_course_document_citation_policy_valid",
        "course_document",
        "citation_policy IN ('citable','content_only','excluded')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_course_document_citation_policy_valid",
        "course_document",
        type_="check",
    )
    op.drop_column("course_document", "citation_policy")
