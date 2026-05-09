"""unify slide PDF templates on slide_templates

Decisione di prodotto: i template per le slide (avatar video + export
PDF Fase 4) vengono unificati sulla tabella `slide_templates`. Il
discriminatore `kind` introdotto in 0021 su `pdf_templates` viene
ritirato — `pdf_templates` resta dedicata SOLO al PDF lezione testo.

Cambi:
1. `course_lesson.slides_pdf_template_id` cambia destinazione FK:
   `pdf_templates(id)` → `slide_templates(id)`. Eventuali valori
   esistenti vengono azzerati prima di ricostruire la FK (riferiscono
   id di `pdf_templates` che non esistono in `slide_templates`).
2. `slide_templates` riceve i due campi necessari al render PDF:
   `margin_mm SMALLINT NOT NULL DEFAULT 20` (CHECK 0..60)
   `background_opacity_pct SMALLINT NOT NULL DEFAULT 15` (CHECK 0..100)
3. `pdf_templates.kind` viene rimosso (rollback di 0021).

Revision ID: 0022
Revises: 0021
Create Date: 2026-05-09
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Sposta la FK course_lesson.slides_pdf_template_id da
    #    pdf_templates → slide_templates. Il naming convention della FK
    #    creata da SQLAlchemy via `ForeignKey(...)` inline produce:
    #    `fk_<table>_<column>_<target_table>`.
    op.drop_constraint(
        "fk_course_lesson_slides_pdf_template_id_pdf_templates",
        "course_lesson",
        type_="foreignkey",
    )
    # I valori esistenti puntano a pdf_templates, non sono validi come
    # slide_templates. Si azzerano (i template verranno ri-selezionati
    # alla prossima richiesta di export).
    op.execute(
        "UPDATE course_lesson SET slides_pdf_template_id = NULL "
        "WHERE slides_pdf_template_id IS NOT NULL"
    )
    op.create_foreign_key(
        "fk_course_lesson_slides_pdf_template_id_slide_templates",
        "course_lesson",
        "slide_templates",
        ["slides_pdf_template_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 2. Campi di render aggiuntivi su slide_templates.
    op.add_column(
        "slide_templates",
        sa.Column(
            "margin_mm",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("20"),
        ),
    )
    op.add_column(
        "slide_templates",
        sa.Column(
            "background_opacity_pct",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("15"),
        ),
    )
    op.create_check_constraint(
        "ck_slide_templates_margin_mm",
        "slide_templates",
        "margin_mm BETWEEN 0 AND 60",
    )
    op.create_check_constraint(
        "ck_slide_templates_background_opacity_pct",
        "slide_templates",
        "background_opacity_pct BETWEEN 0 AND 100",
    )

    # 3. Rollback kind su pdf_templates (introdotto in 0021).
    op.drop_constraint("ck_pdf_templates_kind", "pdf_templates", type_="check")
    op.drop_column("pdf_templates", "kind")


def downgrade() -> None:
    # Riapplica kind su pdf_templates (default 'lesson').
    op.add_column(
        "pdf_templates",
        sa.Column(
            "kind",
            sa.String(length=20),
            nullable=False,
            server_default="lesson",
        ),
    )
    op.create_check_constraint(
        "ck_pdf_templates_kind",
        "pdf_templates",
        "kind IN ('lesson', 'slides')",
    )

    # Rimuovi i campi aggiunti su slide_templates.
    op.drop_constraint(
        "ck_slide_templates_background_opacity_pct",
        "slide_templates",
        type_="check",
    )
    op.drop_constraint(
        "ck_slide_templates_margin_mm", "slide_templates", type_="check"
    )
    op.drop_column("slide_templates", "background_opacity_pct")
    op.drop_column("slide_templates", "margin_mm")

    # Ripristina la FK course_lesson.slides_pdf_template_id → pdf_templates.
    op.drop_constraint(
        "fk_course_lesson_slides_pdf_template_id_slide_templates",
        "course_lesson",
        type_="foreignkey",
    )
    op.execute(
        "UPDATE course_lesson SET slides_pdf_template_id = NULL "
        "WHERE slides_pdf_template_id IS NOT NULL"
    )
    op.create_foreign_key(
        "fk_course_lesson_slides_pdf_template_id_pdf_templates",
        "course_lesson",
        "pdf_templates",
        ["slides_pdf_template_id"],
        ["id"],
        ondelete="SET NULL",
    )
