"""course_taxonomy_term

Crea la tabella `course_taxonomy_term`: un'unica tabella con discriminatore
`taxonomy_type` per gestire le 8 tassonomie usate nella creazione corso
(categoria, stile_insegnamento, profondita_contenuto, ruolo_docente,
dimensione_pubblico, livello_conoscenza, destinatari, livello_EQF).

Le label e le description sono multilingua (JSONB `{lang_code: text}`).
La tabella supporta gerarchie a 2 livelli tramite self-FK `parent_id`
(cascade on delete).

Seed iniziale: ~120 termini in italiano canonico (le altre lingue vanno
popolate dall'admin via "Traduci con AI" in UI).

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-03
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Seed data — etichette IT canoniche; slug snake_case derivato.
# Struttura: lista di tuple (slug, label_it, [opzionale] description_it,
#            [opzionale] children: list[tuple[slug, label, [description]]])
# ---------------------------------------------------------------------------

CATEGORIES: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        "science_tech",
        "Scienza e tecnologia",
        [
            ("computer_science_ai", "Informatica e Intelligenza Artificiale"),
            ("robotics_automation", "Robotica e Automazione"),
            (
                "engineering",
                "Ingegneria (Meccanica, Elettrica, Chimica, Aereospaziale)",
            ),
            ("physics_applied_math", "Fisica e Matematica applicata"),
            ("chemistry_materials", "Chimica e Scienza dei materiali"),
            ("energy_sustainability_tech", "Tecnologie dell'energia e della sostenibilità"),
        ],
    ),
    (
        "life_medical_sciences",
        "Scienza della vita e della medicina",
        [
            ("biology_biotech", "Biologia e biotecnologia"),
            ("medicine_surgery", "Medicina e Chirurgia"),
            ("pharmacology_clinical", "Farmacologia e Ricerca Clinica"),
            ("psychology_neuroscience", "Psicologia e Neuroscienze"),
            ("nutrition_dietetics", "Scienze nutrizionali e dietistica"),
            ("public_health", "Sanità pubblica e gestione sanitaria"),
        ],
    ),
    (
        "economy_business",
        "Economia, business e management",
        [
            ("global_economy_finance", "Economia globale e finanza"),
            ("marketing_communication", "Marketing e comunicazione aziendale"),
            ("entrepreneurship", "Imprenditorialità e start-up"),
            ("hr_management", "Gestione delle risorse umane"),
            ("accounting_audit", "Contabilità e revisione"),
            ("logistics_supply_chain", "Logistica e supply chain"),
        ],
    ),
    (
        "social_legal",
        "Scienze sociali e giuridiche",
        [
            ("sociology_anthropology", "Sociologia e Antropologia"),
            ("political_science_intl", "Scienze politiche e relazioni internazionali"),
            ("law", "Diritto (civile, penale, internazionale)"),
            ("criminology_security", "Criminologia e sicurezza"),
            ("social_psychology", "Psicologia sociale e comportamentale"),
            ("behavioral_economics", "Economia comportamentale"),
        ],
    ),
    (
        "humanities_communication",
        "Arti umanistiche e comunicazione",
        [
            ("literature_linguistics", "Letteratura e linguistica"),
            ("philosophy_ethics", "Filosofia ed etica"),
            ("history_archaeology", "Storia e archeologia"),
            ("art_design", "Arte e Design"),
            ("media_journalism", "Media e giornalismo"),
            ("performing_arts", "Cinema, teatro e performing arts"),
        ],
    ),
    (
        "architecture_construction",
        "Architettura e costruzioni",
        [
            ("architecture_urban", "Architettura e urbanistica"),
            ("civil_engineering", "Ingegneria civile e infrastrutture"),
            ("interior_design", "Design d'interni e arredamento"),
            ("restoration", "Restauro e conservazione dei beni culturali"),
            ("green_building", "Sostenibilità e edilizia verde"),
            ("advanced_construction", "Tecnologie costruttive avanzate"),
        ],
    ),
    (
        "education_training",
        "Educazione e formazione",
        [
            ("pedagogy_didactics", "Pedagogia e didattica"),
            ("digital_training_elearning", "Formazione digitale e e-learning"),
            ("learning_psychology", "Psicologia dell'apprendimento"),
            ("inclusive_education", "Educazione inclusiva e specializzata"),
            ("education_management", "Management dell'istruzione"),
            ("coaching_personal_dev", "Coaching e sviluppo personale"),
        ],
    ),
    (
        "environment_sustainability",
        "Ambiente e sostenibilità",
        [
            ("ecology_biodiversity", "Ecologia e biodiversità"),
            ("waste_recycling", "Gestione dei rifiuti e riciclo"),
            ("renewable_energy", "Energia rinnovabile"),
            ("climate_change", "Cambiamenti climatici"),
            ("environmental_policy", "Politiche ambientali"),
        ],
    ),
    (
        "emerging_tech_innovation",
        "Tecnologie emergenti e innovazione",
        [
            ("ai_machine_learning", "Intelligenza artificiale e machine learning"),
            ("blockchain_fintech", "Blockchain e fintech"),
            ("ar_vr", "Realtà aumentata e virtuale"),
            ("advanced_biotech", "Biotecnologie avanzate"),
            ("iot", "Internet of Things (IoT)"),
        ],
    ),
    (
        "sport_wellness_health",
        "Sport, benessere e salute",
        [
            ("sport_sciences", "Scienze motorie e sport"),
            ("sport_psychology", "Psicologia dello sport"),
            ("nutrition_fitness", "Nutrizione e fitness"),
            ("sport_medicine", "Medicina dello sport"),
            ("sport_facility_management", "Gestione dei centri sportivi"),
        ],
    ),
    (
        "tourism_hospitality_culture",
        "Turismo, ospitalità e cultura",
        [
            ("tourism_management", "Management turistico"),
            ("cultural_heritage", "Patrimonio culturale"),
            ("food_hospitality", "Enogastronomia e hospitality"),
            ("event_management", "Eventi e organizzazione congressuale"),
            ("sustainable_tourism", "Turismo sostenibile"),
        ],
    ),
]


TEACHING_STYLES: list[tuple[str, str]] = [
    ("theoretical", "Teorico"),
    ("practical", "Pratico"),
    ("interactive", "Interattivo"),
    ("multimedia", "Multimediale"),
    ("collaborative", "Collaborativo"),
    ("gamified", "Gamificato"),
    ("other", "Altro"),
]


CONTENT_DEPTHS: list[tuple[str, str]] = [
    ("base", "Base"),
    ("intermediate", "Intermedio"),
    ("advanced", "Avanzato"),
    ("expert", "Esperto"),
]


TEACHER_ROLES: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        "didactic_academic",
        "Ruoli didattici e Accademici",
        [
            ("university_professor", "Professore universitario"),
            ("school_tutor", "Tutor scolastico"),
            ("academic_mentor", "Mentore accademico"),
        ],
    ),
    (
        "practical_operational",
        "Ruoli pratici e operativi",
        [
            ("corporate_trainer", "Formatore aziendale"),
            ("skills_coach", "Coach delle competenze"),
            ("technical_instructor", "Istruttore tecnico"),
        ],
    ),
    (
        "support_tutoring",
        "Ruoli di supporto e Tutoraggio",
        [
            ("facilitator", "Facilitatore"),
            ("interactive_tutor", "Tutor interattivo"),
            ("study_buddy", "Compagno di studio"),
        ],
    ),
    (
        "creative_innovative",
        "Ruoli creativi e innovativi",
        [
            ("storyteller", "Narratore"),
            ("real_case_simulator", "Simulatore di Casi Reali"),
            ("virtual_examiner", "Esaminatore virtuale"),
        ],
    ),
    (
        "hybrid_advanced",
        "Ruoli ibridi e Avanzati",
        [
            ("strategic_consultant", "Consulente Strategico"),
            ("international_expert", "Esperto Internazionale"),
            ("scientific_researcher", "Ricercatore Scientifico"),
        ],
    ),
]


AUDIENCE_SIZES: list[tuple[str, str, str]] = [
    ("small_class", "Classe piccola", "1-10 persone"),
    ("medium_class", "Classe media", "11-30 persone"),
    ("large_class", "Classe grande", "31-100 persone"),
    ("crowded_class", "Classe numerosa", "100+ persone"),
]


KNOWLEDGE_LEVELS: list[tuple[str, str]] = [
    ("beginner", "Principiante"),
    ("intermediate", "Intermedio"),
    ("advanced", "Avanzato"),
    ("expert", "Esperto"),
]


TARGET_AUDIENCES: list[tuple[str, str, str]] = [
    ("student", "Studente", "Studente in formazione iniziale"),
    (
        "professional_training",
        "Lavoratore",
        "Formazione professionale specialistica",
    ),
    (
        "upskill",
        "UpSkill",
        "Accrescere le competenze di un esperto del settore",
    ),
]


EQF_LEVELS: list[tuple[str, str, str, list[tuple[str, str]]]] = [
    (
        "eqf_1",
        "Livello 1",
        "Conoscenze e abilità di base.",
        [
            (
                "eqf_1_first_cycle_diploma",
                "Diploma di licenza conclusiva del I ciclo di istruzione",
            ),
        ],
    ),
    (
        "eqf_2",
        "Livello 2",
        "Conoscenze pratiche di base in un campo di lavoro o studio.",
        [
            ("eqf_2_basic_skills_cert", "Certificazione delle competenze di base"),
        ],
    ),
    (
        "eqf_3",
        "Livello 3",
        "Conoscenze di fatti, principi, processi e concetti generali.",
        [
            (
                "eqf_3_operator_qualification",
                "Attestato di qualifica di operatore professionale",
            ),
        ],
    ),
    (
        "eqf_4",
        "Livello 4",
        "Conoscenze pratiche e teoriche in ampi contesti.",
        [
            ("eqf_4_technical_diploma", "Diploma professionale di tecnico"),
            ("eqf_4_high_school_lyceum", "Diploma liceale"),
            ("eqf_4_technical_school", "Diploma di istruzione tecnica"),
            ("eqf_4_professional_school", "Diploma di istruzione professionale"),
            (
                "eqf_4_higher_technical_specialization",
                "Certificato di specializzazione tecnica superiore",
            ),
        ],
    ),
    (
        "eqf_5",
        "Livello 5",
        "Conoscenze teoriche e pratiche esaurienti e specializzate.",
        [
            ("eqf_5_higher_technician", "Diploma di tecnico superiore"),
        ],
    ),
    (
        "eqf_6",
        "Livello 6",
        "Conoscenze avanzate in un ambito di lavoro o di studio.",
        [
            ("eqf_6_bachelor", "Laurea triennale"),
            ("eqf_6_academic_first_level", "Diploma accademico di I livello"),
        ],
    ),
    (
        "eqf_7",
        "Livello 7",
        "Conoscenze altamente specializzate, alcune all'avanguardia.",
        [
            ("eqf_7_master_degree", "Laurea Magistrale"),
            ("eqf_7_academic_second_level", "Diploma accademico di II livello"),
            ("eqf_7_master_first_level", "Master universitario di I livello"),
            (
                "eqf_7_academic_specialization_first",
                "Diploma accademico di specializzazione (I)",
            ),
            (
                "eqf_7_perfecting_master_first",
                "Diploma di perfezionamento o master (I)",
            ),
        ],
    ),
    (
        "eqf_8",
        "Livello 8",
        "Conoscenze più avanzate in un ambito di lavoro o di studio.",
        [
            ("eqf_8_phd", "Dottorato di ricerca"),
            (
                "eqf_8_research_academic_diploma",
                "Diploma accademico di formazione alla ricerca",
            ),
            ("eqf_8_specialization_diploma", "Diploma di specializzazione"),
            ("eqf_8_master_second_level", "Master universitario di II livello"),
            (
                "eqf_8_academic_specialization_second",
                "Diploma accademico di specializzazione (II)",
            ),
            (
                "eqf_8_perfecting_master_second",
                "Diploma di perfezionamento o master (II)",
            ),
        ],
    ),
]


def _row(
    *,
    taxonomy_type: str,
    parent_id: str | None,
    slug: str,
    sort_order: int,
    label_it: str,
    description_it: str | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "id": str(uuid.uuid4()),
        "taxonomy_type": taxonomy_type,
        "parent_id": parent_id,
        "slug": slug,
        "sort_order": sort_order,
        "is_active": True,
        "labels": json.dumps({"it": label_it}),
        "descriptions": (
            json.dumps({"it": description_it}) if description_it else None
        ),
    }
    return row


def _seed_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []

    # Categories (gerarchica)
    for i, (slug, label, children) in enumerate(CATEGORIES):
        parent = _row(
            taxonomy_type="category",
            parent_id=None,
            slug=slug,
            sort_order=i,
            label_it=label,
        )
        rows.append(parent)
        for j, (cslug, clabel) in enumerate(children):
            rows.append(
                _row(
                    taxonomy_type="category",
                    parent_id=str(parent["id"]),
                    slug=cslug,
                    sort_order=j,
                    label_it=clabel,
                )
            )

    # Teaching styles (flat)
    for i, (slug, label) in enumerate(TEACHING_STYLES):
        rows.append(
            _row(
                taxonomy_type="teaching_style",
                parent_id=None,
                slug=slug,
                sort_order=i,
                label_it=label,
            )
        )

    # Content depth (flat ordinato)
    for i, (slug, label) in enumerate(CONTENT_DEPTHS):
        rows.append(
            _row(
                taxonomy_type="content_depth",
                parent_id=None,
                slug=slug,
                sort_order=i,
                label_it=label,
            )
        )

    # Teacher role (gerarchica)
    for i, (slug, label, children) in enumerate(TEACHER_ROLES):
        parent = _row(
            taxonomy_type="teacher_role",
            parent_id=None,
            slug=slug,
            sort_order=i,
            label_it=label,
        )
        rows.append(parent)
        for j, (cslug, clabel) in enumerate(children):
            rows.append(
                _row(
                    taxonomy_type="teacher_role",
                    parent_id=str(parent["id"]),
                    slug=cslug,
                    sort_order=j,
                    label_it=clabel,
                )
            )

    # Audience size (flat con description)
    for i, (slug, label, desc) in enumerate(AUDIENCE_SIZES):
        rows.append(
            _row(
                taxonomy_type="audience_size",
                parent_id=None,
                slug=slug,
                sort_order=i,
                label_it=label,
                description_it=desc,
            )
        )

    # Knowledge level (flat ordinato)
    for i, (slug, label) in enumerate(KNOWLEDGE_LEVELS):
        rows.append(
            _row(
                taxonomy_type="knowledge_level",
                parent_id=None,
                slug=slug,
                sort_order=i,
                label_it=label,
            )
        )

    # Target audience (flat con description)
    for i, (slug, label, desc) in enumerate(TARGET_AUDIENCES):
        rows.append(
            _row(
                taxonomy_type="target_audience",
                parent_id=None,
                slug=slug,
                sort_order=i,
                label_it=label,
                description_it=desc,
            )
        )

    # EQF levels (gerarchica con description sui parent)
    for i, (slug, label, desc, children) in enumerate(EQF_LEVELS):
        parent = _row(
            taxonomy_type="eqf_level",
            parent_id=None,
            slug=slug,
            sort_order=i,
            label_it=label,
            description_it=desc,
        )
        rows.append(parent)
        for j, (cslug, clabel) in enumerate(children):
            rows.append(
                _row(
                    taxonomy_type="eqf_level",
                    parent_id=str(parent["id"]),
                    slug=cslug,
                    sort_order=j,
                    label_it=clabel,
                )
            )

    return rows


def upgrade() -> None:
    op.create_table(
        "course_taxonomy_term",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("taxonomy_type", sa.String(40), nullable=False),
        sa.Column(
            "parent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("course_taxonomy_term.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column(
            "sort_order",
            sa.SmallInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "labels",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "descriptions",
            postgresql.JSONB(astext_type=sa.Text()),
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
        sa.UniqueConstraint(
            "taxonomy_type", "slug", name="uq_course_taxonomy_term_type_slug"
        ),
        sa.CheckConstraint(
            "taxonomy_type IN ('category','teaching_style','content_depth',"
            "'teacher_role','audience_size','knowledge_level',"
            "'target_audience','eqf_level')",
            name="ck_course_taxonomy_term_taxonomy_type_valid",
        ),
    )
    op.create_index(
        "ix_course_taxonomy_term_taxonomy_type",
        "course_taxonomy_term",
        ["taxonomy_type"],
    )
    op.create_index(
        "ix_course_taxonomy_term_type_parent_sort",
        "course_taxonomy_term",
        ["taxonomy_type", "parent_id", "sort_order"],
    )

    # Bulk-insert via raw SQL: bulk_insert con JSONB richiede passaggi
    # specifici, mentre INSERT ... VALUES con jsonb cast funziona dritto.
    bind = op.get_bind()
    insert_sql = sa.text(
        """
        INSERT INTO course_taxonomy_term
            (id, taxonomy_type, parent_id, slug, sort_order, is_active,
             labels, descriptions)
        VALUES
            (CAST(:id AS UUID), :taxonomy_type, CAST(:parent_id AS UUID),
             :slug, :sort_order, :is_active,
             CAST(:labels AS JSONB), CAST(:descriptions AS JSONB))
        """
    )
    for row in _seed_rows():
        bind.execute(insert_sql, row)


def downgrade() -> None:
    op.drop_index(
        "ix_course_taxonomy_term_type_parent_sort",
        table_name="course_taxonomy_term",
    )
    op.drop_index(
        "ix_course_taxonomy_term_taxonomy_type",
        table_name="course_taxonomy_term",
    )
    op.drop_table("course_taxonomy_term")
