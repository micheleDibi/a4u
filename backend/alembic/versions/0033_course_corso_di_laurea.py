"""course: campo corso_di_laurea opzionale per livello EQF 6/7

Aggiunge la colonna `corso_di_laurea` (VARCHAR 200 nullable) a `course`.
Il campo e' un testo libero opzionale che il FE mostra solo quando
il termine `livello_eqf_term_id` selezionato corrisponde a Laurea
triennale (slug `eqf_6_bachelor`) o Laurea Magistrale
(slug `eqf_7_master_degree`). E' un campo di "setup didattico" e
viene quindi protetto dal lock `didactic_setup_confirmed_at` lato
service.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision: str = "0033"
down_revision: str | None = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "course",
        sa.Column("corso_di_laurea", sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("course", "corso_di_laurea")
