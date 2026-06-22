"""Schemi Pydantic per la Fase 3 — Contenuti delle lezioni (§6).

Mirror dello schema JSON di `prompt_generazione_corsi.md` §6.3 (output
dell'AI per lezione) + tipi di input per gli endpoint CRUD manuale e di
trigger generazione/approve.

Validazione (§6.4) è in `course_lesson_content_service.materialize_lesson_content`:
- match `lesson_id == lesson.lesson_code`
- `section_id` univoci all'interno della lezione
- asset_id (visual_assets, tables, equations, examples) univoci per tipo
- ogni asset_id deve essere referenziato nel testo come [FIG:..]/[TAB:..]/[EQ:..]/[EX:..]
- objectives_addressed e topics_addressed riferiscono ID validi di Fase 2
- coverage completa (unione su sections copre tutti gli obiettivi/temi)
- coverage_check coerente con il calcolo effettivo
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import ORMModel

# ---------------------------------------------------------------------------
# Output AI (§6.3) — validato dopo la chiamata OpenAI
# ---------------------------------------------------------------------------


class LessonContentSection(BaseModel):
    """Una sezione del testo della lezione (§6.3 sections[*])."""

    model_config = ConfigDict(extra="forbid")
    section_id: str = Field(min_length=1, max_length=50)
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1)
    objectives_addressed: list[str] = Field(default_factory=list, max_length=20)
    topics_addressed: list[str] = Field(default_factory=list, max_length=20)


class LessonContentVisualAsset(BaseModel):
    """Asset visivo: oggi solo Mermaid + immagine caricata.

    `format`:
    - `mermaid`: `content` è codice Mermaid (renderizzato live).
    - `image`: `content` è un path pubblico relativo (es.
      `lesson_assets/{course_id}/{uuid}.png`); l'immagine viene
      servita da StaticFiles in `/uploads/...`.
    - `image_prompt|image_search_query|description`: SOLO LEGACY in
      lettura. Sono valori di vecchi corsi pre-refactor; il frontend
      di scrittura non li produce più. Restano accettati qui per non
      far esplodere il parsing dei `content_raw` storici.

    `extra="ignore"` per tollerare il vecchio campo `asset_type`
    (rimosso dal refactor) presente nei record antecedenti.
    """

    model_config = ConfigDict(extra="ignore")
    asset_id: str = Field(min_length=1, max_length=50)
    format: Literal[
        "mermaid",
        "image",
        # — legacy, read-only —
        "image_prompt",
        "image_search_query",
        "description",
    ]
    content: str = Field(min_length=1)
    caption: str = Field(default="", max_length=600)
    alt_text: str = Field(default="", max_length=400)


class LessonContentTable(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table_id: str = Field(min_length=1, max_length=50)
    markdown: str = Field(min_length=1)
    caption: str = Field(default="", max_length=400)


class ProofStep(BaseModel):
    """Un passaggio della dimostrazione di un teorema/proposizione."""

    model_config = ConfigDict(extra="forbid")
    # LaTeX del passaggio (SENZA delimitatori $...$); può essere vuoto se il
    # passo è solo testuale.
    latex: str = Field(default="", max_length=2000)
    # Spiegazione del passaggio (markdown; può contenere math inline $..$).
    text: str = Field(default="", max_length=1500)


class LessonContentEquation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    equation_id: str = Field(min_length=1, max_length=50)
    latex: str = Field(min_length=1)
    label: str = Field(default="", max_length=200)
    explanation: str = Field(default="", max_length=1200)
    # Tipo dell'asset: l'AI classifica per decidere se generare la
    # dimostrazione. definition/formula/identity → di norma `proof` vuota;
    # theorem/proposition/lemma/corollary → enunciato + dimostrazione.
    kind: str = Field(default="formula", max_length=20)
    # Enunciato formale (markdown + math inline $..$). Vuoto per le formule
    # "nude" senza enunciato dedicato.
    statement: str = Field(default="", max_length=3000)
    # Dimostrazione a passaggi; vuota quando non applicabile.
    proof: list[ProofStep] = Field(default_factory=list)


class LessonContentExample(BaseModel):
    model_config = ConfigDict(extra="forbid")
    example_id: str = Field(min_length=1, max_length=50)
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1)


class LessonContentReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    citation: str = Field(min_length=1, max_length=600)
    source: Literal["documento_caricato", "suggerimento_generale"]


class LessonContentObjectiveCovered(BaseModel):
    model_config = ConfigDict(extra="forbid")
    objective: str = Field(min_length=1)
    covered_in_section_ids: list[str] = Field(default_factory=list)


class LessonContentTopicCovered(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic_id: str = Field(min_length=1)
    covered_in_section_ids: list[str] = Field(default_factory=list)


class LessonContentCoverageCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    objectives_covered: list[LessonContentObjectiveCovered] = Field(
        default_factory=list
    )
    topics_covered: list[LessonContentTopicCovered] = Field(
        default_factory=list
    )


class LessonContentOutput(BaseModel):
    """Output AI per una singola lezione (§6.3)."""

    model_config = ConfigDict(extra="forbid")
    lesson_id: str
    lesson_title: str
    is_introductory: bool
    estimated_word_count: int = Field(ge=0)
    introduction: str = Field(min_length=1)
    sections: list[LessonContentSection] = Field(min_length=1)
    summary: str = Field(min_length=1)
    # Spec §6.4 chiede 3-7 come linea guida; il modello può sforare di
     # qualche unità in domini ricchi → cap a 12 per evitare reject inutili.
    key_takeaways: list[str] = Field(min_length=3, max_length=12)
    visual_assets: list[LessonContentVisualAsset] = Field(default_factory=list)
    tables: list[LessonContentTable] = Field(default_factory=list)
    equations: list[LessonContentEquation] = Field(default_factory=list)
    examples: list[LessonContentExample] = Field(default_factory=list)
    references: list[LessonContentReference] = Field(default_factory=list)
    coverage_check: LessonContentCoverageCheck


# ---------------------------------------------------------------------------
# Verifica delle competenze (`content_raw` quando `lesson.is_assessment`)
# ---------------------------------------------------------------------------


class AssessmentMCOption(BaseModel):
    """Una opzione di risposta di una domanda a scelta multipla."""

    model_config = ConfigDict(extra="forbid")
    option_id: str = Field(min_length=1, max_length=10)  # es. "A".."D"
    text: str = Field(min_length=1, max_length=1000)


class AssessmentMCQuestion(BaseModel):
    """Domanda a scelta multipla: testo, opzioni, opzione corretta."""

    model_config = ConfigDict(extra="forbid")
    question_id: str = Field(min_length=1, max_length=20)
    text: str = Field(min_length=1, max_length=2000)
    options: list[AssessmentMCOption] = Field(min_length=2, max_length=6)
    correct_option_id: str = Field(min_length=1, max_length=10)


class AssessmentOpenQuestion(BaseModel):
    """Domanda aperta: testo + traccia di risposta attesa (per la correzione)."""

    model_config = ConfigDict(extra="forbid")
    question_id: str = Field(min_length=1, max_length=20)
    text: str = Field(min_length=1, max_length=2000)
    expected_answer: str = Field(min_length=1, max_length=4000)


class LessonAssessmentOutput(BaseModel):
    """Output AI per una lezione di verifica delle competenze.

    Polimorfico con `LessonContentOutput`: entrambi vivono nella colonna
    `course_lesson.content_raw`. Il discriminante è la chiave
    `is_assessment` (qui sempre True) + il flag `lesson.is_assessment`.
    """

    model_config = ConfigDict(extra="forbid")
    lesson_id: str
    lesson_title: str
    is_assessment: Literal[True] = True
    multiple_choice_questions: list[AssessmentMCQuestion] = Field(
        default_factory=list
    )
    open_questions: list[AssessmentOpenQuestion] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Input dal frontend
# ---------------------------------------------------------------------------


class LessonContentGenerateInput(BaseModel):
    """Body opzionale per `POST /lessons/{lid}/content/generate`
    e `POST /lessons-content/generate-all`. Vuoto = prima generazione."""

    regeneration_hint: str | None = Field(default=None, max_length=2000)


class LessonContentUpdateInput(BaseModel):
    """Body per `PATCH /lessons/{lid}/content` (CRUD manuale).

    Tutti i campi sono opzionali. Edit non degrada lo status (`approved`
    resta `approved`). Validazione di consistenza in service.
    """

    model_config = ConfigDict(extra="forbid")

    introduction: str | None = None
    sections: list[LessonContentSection] | None = None
    summary: str | None = None
    key_takeaways: list[str] | None = Field(default=None, max_length=10)
    visual_assets: list[LessonContentVisualAsset] | None = None
    tables: list[LessonContentTable] | None = None
    equations: list[LessonContentEquation] | None = None
    examples: list[LessonContentExample] | None = None
    references: list[LessonContentReference] | None = None
    coverage_check: LessonContentCoverageCheck | None = None


class LessonAssessmentUpdateInput(BaseModel):
    """Body per `PATCH /lessons/{lid}/assessment` (CRUD manuale verifica).

    Entrambe le liste opzionali; l'edit non degrada lo status. Validazione
    di consistenza (id univoci, una sola opzione corretta) nel service.
    """

    model_config = ConfigDict(extra="forbid")

    multiple_choice_questions: list[AssessmentMCQuestion] | None = None
    open_questions: list[AssessmentOpenQuestion] | None = None


# ---------------------------------------------------------------------------
# Output verso il frontend (DTO read-only)
# ---------------------------------------------------------------------------


class LessonContentLessonOut(ORMModel):
    """Sub-DTO con il payload Fase 3 di `course_lesson` (parsa `content_raw`).
    Usato come embed in `CourseLessonOut`. Tutti i campi opzionali per
    consentire serializzazione anche con content_raw=None."""

    estimated_word_count: int | None = None
    introduction: str | None = None
    sections: list[dict[str, Any]] = Field(default_factory=list)
    summary: str | None = None
    key_takeaways: list[str] = Field(default_factory=list)
    visual_assets: list[dict[str, Any]] = Field(default_factory=list)
    tables: list[dict[str, Any]] = Field(default_factory=list)
    equations: list[dict[str, Any]] = Field(default_factory=list)
    examples: list[dict[str, Any]] = Field(default_factory=list)
    references: list[dict[str, Any]] = Field(default_factory=list)
    coverage_check: dict[str, Any] | None = None


class LessonContentMetaOut(ORMModel):
    """Meta della lezione per Fase 3 — esposto in `CourseLessonOut`."""

    content_status: str
    content_progress: int = 0
    content_progress_phase: str | None = None
    content_error: str | None = None
    content_attempts: int = 0
    content_generated_at: datetime | None = None
    content_approved_at: datetime | None = None
    content_tokens: dict[str, Any] | None = None
    content_regeneration_hint: str | None = None
