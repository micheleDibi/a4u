from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from typing import Any

from pydantic import BaseModel, EmailStr, Field

from app.schemas.common import ORMModel
from app.schemas.course_architecture import CourseModuleOut
from app.schemas.document_summary import DocumentSummaryOut

CourseStatus = Literal[
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
    "video_pending",
    "video_ready",
    "avatar_video_pending",
    "avatar_video_ready",
    "published",
    "archived",
]

DocumentSummaryStatus = Literal["pending", "processing", "ready", "failed"]


class TaxonomyAssignments(BaseModel):
    """Mappa nome-tassonomia → term_id (nullable). I 8 valori coprono
    tutte le tassonomie definite in `course_taxonomy_term`."""

    categoria: uuid.UUID | None = None
    stile_insegnamento: uuid.UUID | None = None
    profondita_contenuto: uuid.UUID | None = None
    ruolo_docente: uuid.UUID | None = None
    dimensione_pubblico: uuid.UUID | None = None
    livello_conoscenza: uuid.UUID | None = None
    destinatari: uuid.UUID | None = None
    livello_eqf: uuid.UUID | None = None


class UserCompact(ORMModel):
    id: uuid.UUID
    email: EmailStr
    full_name: str


class TaxonomyTermCompact(ORMModel):
    id: uuid.UUID
    taxonomy_type: str
    parent_id: uuid.UUID | None = None
    slug: str
    labels: dict[str, str]
    descriptions: dict[str, str] | None = None


class CourseDocumentOut(ORMModel):
    id: uuid.UUID
    filename_original: str
    mime_type: str
    size_bytes: int
    summary_status: DocumentSummaryStatus
    summary_generated_at: datetime | None = None
    summary_error: str | None = None
    summary_attempts: int = 0
    summary_tokens: dict[str, object] | None = None
    text_chars_extracted: int | None = None
    created_at: datetime


class CourseDocumentDetailOut(CourseDocumentOut):
    """Variante con il riassunto strutturato in chiaro. Usato dall'endpoint
    `GET /documents/{id}` quando l'utente apre il dialog del riassunto."""

    summary: DocumentSummaryOut | None = None


class CourseListLessonsProgress(BaseModel):
    """Avanzamento pipeline delle lezioni didattiche di un corso (esclude
    le lezioni di verifica `is_assessment=true` dal denominatore).

    Criteri:
    - `content_ready`: `content_status` ∈ ('ready','approved')
    - `slides_ready`: `slides_status` ∈ ('ready','approved')
    - `videos_ready`: `video_status == 'ready'`
    - `avatar_videos_ready`: `avatar_video_status == 'ready'`

    `total == 0` → il corso non ha lezioni didattiche (es. solo
    verifica): la UI lo visualizza con "—" invece di "0/0".
    """

    total: int
    content_ready: int
    slides_ready: int
    videos_ready: int
    avatar_videos_ready: int


class CourseListItemOut(ORMModel):
    id: uuid.UUID
    title: str
    status: CourseStatus
    language_code: str
    assignee: UserCompact
    modules_count: int
    cfu: int
    updated_at: datetime
    created_at: datetime
    lessons_progress: CourseListLessonsProgress
    # Popolato dal service `list_courses` quando un job di duplicazione
    # è attivo (`status` ∈ pending|processing) e ha questo corso come
    # `target_course_id`. Usato dalla UI per il badge "Duplicazione in
    # corso XX%" sulla riga del corso target.
    duplication_job: "CourseDuplicationJobCompact | None" = None


# Late import per evitare ciclo con `course_duplication.py`.
from app.schemas.course_duplication import CourseDuplicationJobCompact  # noqa: E402

CourseListItemOut.model_rebuild()


class CourseOut(ORMModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    title: str
    objectives: str
    language_code: str
    # Override TTS per i video lezione (Fase 6 §9). NULL = usa language_code.
    # Quando valorizzato, deve essere in XTTS_SUPPORTED_LANGUAGES.
    video_language_code: str | None = None
    argomenti_chiave: list[str] = Field(default_factory=list)
    # Testo libero opzionale per il nome del corso di laurea
    # (es. "Informatica", "Ingegneria Gestionale"). Mostrato dal FE
    # solo quando il livello EQF e' Laurea triennale (eqf_6_bachelor)
    # o Laurea Magistrale (eqf_7_master_degree).
    corso_di_laurea: str | None = None
    cfu: int
    modules_count: int
    lessons_per_module: int
    lesson_duration_minutes: int
    assessment_lesson_enabled: bool
    multiple_choice_questions_count: int
    open_questions_count: int
    status: CourseStatus
    assignee: UserCompact
    created_by: UserCompact | None = None
    documents: list[CourseDocumentOut] = Field(default_factory=list)
    # Architettura corso (Fase 1) — popolata quando lo status è ≥ architecture_ready.
    modules: list[CourseModuleOut] = Field(default_factory=list)
    course_overview: str | None = None
    pedagogical_rationale: str | None = None
    architecture_attempts: int = 0
    architecture_tokens: dict[str, Any] | None = None
    architecture_error: str | None = None
    architecture_generated_at: datetime | None = None
    architecture_regeneration_hint: str | None = None
    architecture_progress: int = 0
    architecture_progress_phase: str | None = None
    # Timestamp di conferma del setup didattico (Tab 1 + Tab 2). Null =
    # editabile. Valorizzato = lock di tutti i campi parametri.
    didactic_setup_confirmed_at: datetime | None = None
    # Glossario corso (§10.1) — generato una volta, riusato in Fase 2/3/5.
    glossary_status: str = "empty"
    glossary_raw: dict[str, Any] | None = None
    glossary_tokens: dict[str, Any] | None = None
    glossary_generated_at: datetime | None = None
    glossary_error: str | None = None
    # Term completi (mappa nome → term) per render dei nomi senza extra round-trip.
    categoria: TaxonomyTermCompact | None = None
    stile_insegnamento: TaxonomyTermCompact | None = None
    profondita_contenuto: TaxonomyTermCompact | None = None
    ruolo_docente: TaxonomyTermCompact | None = None
    dimensione_pubblico: TaxonomyTermCompact | None = None
    livello_conoscenza: TaxonomyTermCompact | None = None
    destinatari: TaxonomyTermCompact | None = None
    livello_eqf: TaxonomyTermCompact | None = None
    created_at: datetime
    updated_at: datetime


class CourseCreateInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    objectives: str = Field(default="", max_length=8000)
    language_code: str = Field(min_length=2, max_length=10)
    cfu: int = Field(ge=1, le=200)
    argomenti_chiave: list[str] = Field(default_factory=list, max_length=30)
    corso_di_laurea: str | None = Field(default=None, max_length=200)
    assignee_user_id: uuid.UUID | None = None
    taxonomies: TaxonomyAssignments = Field(default_factory=TaxonomyAssignments)


class CourseUpdateInput(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    objectives: str | None = Field(default=None, max_length=8000)
    language_code: str | None = Field(default=None, min_length=2, max_length=10)
    # Override TTS — None lascia inalterato, "" o stringa speciale "__null__"
    # azzera (vedi `update_course` per la semantica). Per ora accettiamo
    # solo None (no change) o un codice in XTTS_SUPPORTED_LANGUAGES.
    video_language_code: str | None = Field(default=None, max_length=10)
    cfu: int | None = Field(default=None, ge=1, le=200)
    argomenti_chiave: list[str] | None = Field(default=None, max_length=30)
    # Stringa vuota "" -> azzera (lato service viene normalizzato a None).
    corso_di_laurea: str | None = Field(default=None, max_length=200)
    taxonomies: TaxonomyAssignments | None = None
    status: CourseStatus | None = None


class CourseAssigneeUpdateInput(BaseModel):
    assignee_user_id: uuid.UUID
