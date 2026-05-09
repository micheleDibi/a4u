from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.course_document import CourseDocument
    from app.models.course_lesson import CourseLesson
    from app.models.course_module import CourseModule
    from app.models.course_taxonomy import CourseTaxonomyTerm
    from app.models.language import Language
    from app.models.organization import Organization
    from app.models.user import User


# Tutti i valori di status. Foundation usa solo `draft` e `archived`,
# gli altri sono già definiti per accomodare la pipeline AI a 5 fasi
# senza dover toccare il CHECK constraint nelle iterazioni successive.
COURSE_STATUSES: tuple[str, ...] = (
    "draft",
    "architecture_pending",
    "architecture_ready",
    "architecture_approved",
    "lessons_structure_pending",
    "lessons_structure_ready",
    "lessons_structure_approved",
    "content_pending",
    "content_ready",
    "content_approved",
    "slides_pending",
    "slides_ready",
    "slides_approved",
    "speech_pending",
    "speech_ready",
    "speech_approved",
    "published",
    "archived",
)


GLOSSARY_STATUSES: tuple[str, ...] = (
    "empty",
    "pending",
    "processing",
    "ready",
    "approved",
    "failed",
)


class Course(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "course"
    __table_args__ = (
        Index("ix_course_org_status", "organization_id", "status"),
        Index("ix_course_org_assignee", "organization_id", "assignee_user_id"),
        Index("ix_course_org_language", "organization_id", "language_code"),
        CheckConstraint("cfu >= 1", name="ck_course_cfu_min"),
        CheckConstraint("modules_count >= 1", name="ck_course_modules_count_min"),
        CheckConstraint(
            "lessons_per_module >= 1", name="ck_course_lessons_per_module_min"
        ),
        CheckConstraint(
            "lesson_duration_minutes >= 1",
            name="ck_course_lesson_duration_minutes_min",
        ),
        CheckConstraint(
            "multiple_choice_questions_count >= 0",
            name="ck_course_multiple_choice_questions_count_min",
        ),
        CheckConstraint(
            "open_questions_count >= 0", name="ck_course_open_questions_count_min"
        ),
        CheckConstraint(
            "status IN ('draft','architecture_pending','architecture_ready',"
            "'architecture_approved','lessons_structure_pending',"
            "'lessons_structure_ready','lessons_structure_approved',"
            "'content_pending','content_ready','content_approved',"
            "'slides_pending','slides_ready','slides_approved',"
            "'speech_pending','speech_ready','speech_approved',"
            "'published','archived')",
            name="ck_course_status_valid",
        ),
        CheckConstraint(
            "glossary_status IN "
            "('empty','pending','processing','ready','approved','failed')",
            name="ck_course_glossary_status",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    objectives: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    language_code: Mapped[str] = mapped_column(
        String(10),
        ForeignKey("languages.code", ondelete="RESTRICT"),
        nullable=False,
    )

    categoria_term_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("course_taxonomy_term.id", ondelete="SET NULL"),
        nullable=True,
    )
    stile_insegnamento_term_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("course_taxonomy_term.id", ondelete="SET NULL"),
        nullable=True,
    )
    profondita_contenuto_term_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("course_taxonomy_term.id", ondelete="SET NULL"),
        nullable=True,
    )
    ruolo_docente_term_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("course_taxonomy_term.id", ondelete="SET NULL"),
        nullable=True,
    )
    dimensione_pubblico_term_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("course_taxonomy_term.id", ondelete="SET NULL"),
        nullable=True,
    )
    livello_conoscenza_term_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("course_taxonomy_term.id", ondelete="SET NULL"),
        nullable=True,
    )
    destinatari_term_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("course_taxonomy_term.id", ondelete="SET NULL"),
        nullable=True,
    )
    livello_eqf_term_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("course_taxonomy_term.id", ondelete="SET NULL"),
        nullable=True,
    )

    argomenti_chiave: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    # Snapshot dei parametri organizzazione al momento della creazione.
    # Immutabili dopo la creazione: cambi a OrganizationCourseSettings non
    # si propagano retroattivamente ai corsi esistenti.
    cfu: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    modules_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    lessons_per_module: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    lesson_duration_minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    assessment_lesson_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    multiple_choice_questions_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False
    )
    open_questions_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    assignee_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="draft", server_default="draft"
    )

    # Architettura del corso (Fase 1 della pipeline AI, §4 prompt_generazione_corsi.md).
    # Popolata dal worker `course_architecture_worker`. La materializzazione
    # vera (modules + lessons) è in tabelle separate; questi campi contengono
    # solo i metadati di alto livello + audit del run.
    course_overview: Mapped[str | None] = mapped_column(Text, nullable=True)
    pedagogical_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    architecture_raw: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    architecture_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    architecture_tokens: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    architecture_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    architecture_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Timestamp di conferma del setup didattico (Tab 1 + Tab 2 del wizard).
    # Quando NULL, i parametri del corso sono editabili. Quando valorizzato,
    # sono read-only: il setup è "confermato" e non si tocca più senza un
    # esplicito sblocco (creator/org_admin only). Vedi
    # `course_service.update_course` per il gating server-side.
    didactic_setup_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Hint utente passato all'ultimo run di rigenerazione (testo libero).
    # Persistito su `course` (non sulla riga di run) perché in foundation
    # eseguiamo un'unica rigenerazione full-architecture alla volta.
    architecture_regeneration_hint: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    # Progresso live della generazione (0-100). Aggiornato dal worker ai
    # checkpoint del flusso e da un ticker di sfondo durante la chiamata
    # OpenAI per dare feedback continuo in UI.
    architecture_progress: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    architecture_progress_phase: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )

    # Glossario corso (§10.1).
    # Generato una sola volta (auto-trigger al primo task del worker
    # Fase 3, oppure manualmente via POST /glossary/regenerate).
    # Riusato come `{{glossario}}` nei prompt di Fasi 2, 3, 5.
    glossary_status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="empty", server_default="empty"
    )
    glossary_raw: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    glossary_tokens: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    glossary_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    glossary_error: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )

    # Relationships
    organization: Mapped["Organization"] = relationship("Organization")
    language: Mapped["Language"] = relationship("Language")
    assignee: Mapped["User"] = relationship(
        "User", foreign_keys=[assignee_user_id]
    )
    created_by: Mapped["User | None"] = relationship(
        "User", foreign_keys=[created_by_user_id]
    )
    documents: Mapped[list["CourseDocument"]] = relationship(
        "CourseDocument",
        back_populates="course",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    modules: Mapped[list["CourseModule"]] = relationship(
        "CourseModule",
        back_populates="course",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="CourseModule.position",
    )
    lessons: Mapped[list["CourseLesson"]] = relationship(
        "CourseLesson",
        back_populates="course",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="CourseLesson.position",
    )
    categoria: Mapped["CourseTaxonomyTerm | None"] = relationship(
        "CourseTaxonomyTerm", foreign_keys=[categoria_term_id]
    )
    stile_insegnamento: Mapped["CourseTaxonomyTerm | None"] = relationship(
        "CourseTaxonomyTerm", foreign_keys=[stile_insegnamento_term_id]
    )
    profondita_contenuto: Mapped["CourseTaxonomyTerm | None"] = relationship(
        "CourseTaxonomyTerm", foreign_keys=[profondita_contenuto_term_id]
    )
    ruolo_docente: Mapped["CourseTaxonomyTerm | None"] = relationship(
        "CourseTaxonomyTerm", foreign_keys=[ruolo_docente_term_id]
    )
    dimensione_pubblico: Mapped["CourseTaxonomyTerm | None"] = relationship(
        "CourseTaxonomyTerm", foreign_keys=[dimensione_pubblico_term_id]
    )
    livello_conoscenza: Mapped["CourseTaxonomyTerm | None"] = relationship(
        "CourseTaxonomyTerm", foreign_keys=[livello_conoscenza_term_id]
    )
    destinatari: Mapped["CourseTaxonomyTerm | None"] = relationship(
        "CourseTaxonomyTerm", foreign_keys=[destinatari_term_id]
    )
    livello_eqf: Mapped["CourseTaxonomyTerm | None"] = relationship(
        "CourseTaxonomyTerm", foreign_keys=[livello_eqf_term_id]
    )
