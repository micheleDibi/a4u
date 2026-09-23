"""Schemi Pydantic per la Fase 3 — Contenuti delle lezioni (§6).

Mirror dello schema JSON di `prompt_generazione_corsi.md` §6.3 (output
dell'AI per lezione) + tipi di input per gli endpoint CRUD manuale e di
trigger generazione/approve.

Validazione (§6.4) è in `course_lesson_content_service.materialize_lesson_content`:
- match `lesson_id == lesson.lesson_code`
- `section_id` univoci all'interno della lezione
- asset_id (visual_assets, tables, equations, examples) univoci per tipo
- ogni asset_id deve essere referenziato nel testo come [FIG:..]/[TAB:..]/[EQ:..]/[EX:..]
- objectives_addressed e topics_addressed vengono RICONCILIATI sui valori
  canonici di Fase 2 (`lesson_coverage_resolver`): il modello riceve i
  codici `O1..On` e i `topic_id`, ma sono accettati anche il testo e le
  sue varianti tipografiche; ciò che resta irrisolto viene scartato con
  warning + audit, non fa fallire la generazione
- coverage completa (unione su sections copre tutti gli obiettivi/temi):
  è l'unico controllo di contabilità rimasto bloccante
- coverage_check è DERIVATO dalle sections, non più confrontato

Normalizzazione delle liste (questione B5, decisione D18): `key_takeaways`
e `references` sono normalizzati DALLO SCHEMA con validatori in mode
"after": trim, voci vuote scartate (vuoto = `str.strip()`: U+200B non è
rimosso), dedup case-insensitive (`str.lower()`) con ordine e grafia della
prima occorrenza; le references si deduplicano a parità di `source`. Vale
per l'output AI (`LessonContentOutput`) e per il PATCH del docente
(`LessonContentUpdateInput`), MAI in lettura: PDF, vista web ed editor
mostrano `content_raw` com'è finché la lezione non viene rigenerata o
salvata dall'editor (nessun backfill). `min_length`/`max_length` contano
l'elenco grezzo, quindi la lista persistita può avere 1-2 punti chiave:
nessun round-trip `LessonContentOutput.model_validate(content_raw)` va
introdotto (fallirebbe con `too_short` su una lezione degradata).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Protocol

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import ORMModel

# ---------------------------------------------------------------------------
# Formato degli asset visivi (D1) — condiviso con la Fase 4
# ---------------------------------------------------------------------------

# Contratto di `visual_assets[].format` (Fase 3) e `new_assets[].format`
# (Fase 4, che importa questo alias). `content` resta sempre una stringa:
# - `mermaid`: codice Mermaid 11 (tipi ammessi in `figure_theme.MERMAID_ALLOWED_TYPES`);
# - `vegalite`: spec Vega-Lite JSON serializzata (senza `config`, iniettato dal renderer);
# - `dot`: sorgente Graphviz DOT;
# - `function`: `FunctionFigureSpec` JSON serializzata (figura calcolata da sympy/matplotlib);
# - `image`: path pubblico relativo dell'immagine caricata (servita da `/uploads/...`);
# - `image_prompt|image_search_query|description`: SOLO LEGACY in lettura
#   (corsi pre-refactor); nessun percorso di scrittura li produce più.
# Il render di ogni formato passa da `figure_render_service.REGISTRY`; lo
# schema strict OpenAI offre al modello solo i formati abilitati e
# disponibili (`available_formats()`), mai i legacy.
# Tetto di RISORSA sul sorgente di un asset visivo (A1): la lunghezza più
# alta ammessa da un renderer con i default (`settings.figure_dot_max_chars`,
# 12.000; Vega-Lite 4.000, soglia editoriale Mermaid 3.000 in
# `figure_compute.graph_rules`). Le soglie per formato stanno nei renderer,
# non qui: sull'output AI un errore Pydantic scarta la lezione intera senza
# passare dal fix degli asset, mentre `validate` del registro lascia al fix
# la possibilità di semplificare. Un `FIGURE_DOT_MAX_CHARS` più alto di
# questo valore non ha effetto oltre il tetto.
# Vale sugli asset GENERATI (`cap_visual_asset_content` negli output AI di
# Fase 3 e 4) e, nel PATCH, solo sugli asset cambiati
# (`figure_render_service.validate_visual_assets_or_raise`): il modello
# condiviso `LessonContentVisualAsset` non ha tetto, perché l'editor invia
# sempre tutti gli asset e un Mermaid storico più lungo (nessun tetto prima
# di WP5, nessun backfill) renderebbe impossibile correggere un refuso.
VISUAL_ASSET_CONTENT_MAX_CHARS = 12_000

# Numero di punti chiave (`key_takeaways`) per lezione. La spec §6.4 chiede
# 3-7 come linea guida; il modello può sforare di qualche unità in domini
# ricchi, quindi il tetto è 12 per l'output AI e per il PATCH del docente
# (domanda aperta 14: con 10 una lezione da 11-12 punti non era salvabile
# dall'editor). Entrambi i vincoli contano l'elenco GREZZO (vedi
# `_clean_key_takeaways`): dopo la dedup la lista può scendere sotto il
# minimo, e il worker lo segnala con `lesson_content_key_takeaways_below_min`.
KEY_TAKEAWAYS_MIN = 3
KEY_TAKEAWAYS_MAX = 12

VisualAssetFormat = Literal[
    "mermaid",
    "vegalite",
    "dot",
    "function",
    # WP6: figura vettoriale compilata con XeLaTeX (spenta di default).
    "tikz",
    "image",
    # — legacy, read-only —
    "image_prompt",
    "image_search_query",
    "description",
]

# Formati degli asset della DISPENSA (Fase 3): quelli condivisi più
# `source_figure`, la figura di fonte estratta da un documento (`content` =
# UUID della riga `course_document_figure`, risolta lato server con la riga
# «Fonte»). Scissione dell'alias: le slide (`new_assets`, Fase 4) restano su
# `VisualAssetFormat` e non possono creare figure di fonte (le referenziano
# da Fase 3); il modello di Fase 3 le sceglie solo tramite `source_figures`.
ContentVisualAssetFormat = Literal[
    "mermaid",
    "vegalite",
    "dot",
    "function",
    "tikz",
    "image",
    "source_figure",
    # — legacy, read-only —
    "image_prompt",
    "image_search_query",
    "description",
]
SOURCE_FIGURE_FORMAT = "source_figure"

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
    """Asset visivo della lezione: quattro famiglie renderizzate
    (Mermaid, Vega-Lite, DOT, `function`) più l'immagine caricata.

    `format` è `VisualAssetFormat` (contratto documentato sull'alias);
    `content` è sempre una stringa (codice, spec JSON serializzata, sorgente
    o path). I valori legacy `image_prompt|image_search_query|description`
    restano accettati in lettura per non far esplodere il parsing dei
    `content_raw` storici.

    `extra="ignore"` per tollerare il vecchio campo `asset_type`
    (rimosso dal refactor) presente nei record antecedenti.
    """

    model_config = ConfigDict(extra="ignore")
    asset_id: str = Field(min_length=1, max_length=50)
    format: ContentVisualAssetFormat
    content: str = Field(min_length=1)
    caption: str = Field(default="", max_length=600)
    alt_text: str = Field(default="", max_length=400)


class _HasContent(Protocol):
    content: str


def cap_visual_asset_content[AssetT: _HasContent](asset: AssetT) -> AssetT:
    """Tetto di risorsa A1 su un asset visivo generato: oltre
    `VISUAL_ASSET_CONTENT_MAX_CHARS` un `value_error` con `loc`
    sull'elemento della lista (l'output AI è scartato e la lezione
    rigenerata, come per ogni altro errore di schema)."""
    size = len(asset.content)
    if size > VISUAL_ASSET_CONTENT_MAX_CHARS:
        cap = VISUAL_ASSET_CONTENT_MAX_CHARS
        raise ValueError(f"content oltre {cap} caratteri ({size}, tetto di risorsa A1)")
    return asset


def reject_generated_source_figure(asset: LessonContentVisualAsset) -> LessonContentVisualAsset:
    """Il modello sceglie le figure di fonte solo in `source_figures`: un
    asset generato con quel formato è fuori contratto."""
    if asset.format == SOURCE_FIGURE_FORMAT:
        raise ValueError("format source_figure non ammesso negli asset generati")
    return asset


GeneratedVisualAsset = Annotated[
    LessonContentVisualAsset,
    AfterValidator(cap_visual_asset_content),
    AfterValidator(reject_generated_source_figure),
]


class SourceFigureChoice(BaseModel):
    """Figura del catalogo scelta dal PROMPT 3 (fusa lato server in un asset
    `source_figure`): id del catalogo, didascalia e testo alternativo nella
    lingua del corso, senza la fonte."""

    model_config = ConfigDict(extra="forbid")
    figure: str = Field(min_length=1, max_length=40)
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


def _clean_key_takeaways(v: list[str]) -> list[str]:
    """Trim, scarto dei vuoti, dedup case-insensitive con ordine e grafia
    della prima occorrenza.

    Stesso algoritmo di `_clean_argomenti` (course_objectives_generation.py)
    senza il tetto di 80 caratteri (un punto chiave è una frase) e senza il
    ramo `isinstance`: è un after-validator su `list[str]`, Pydantic ha già
    rifiutato i non-str. «Vuoto» è `str.strip()`: U+00A0 cade, U+200B no.
    Chiave `str.lower()` e non `casefold()`, come negli altri normalizzatori.
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in v:
        s = raw.strip()
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _clean_references(v: list[LessonContentReference]) -> list[LessonContentReference]:
    """Trim della `citation`, scarto delle citation vuote dopo il trim, dedup
    per chiave `(source, citation.lower())`.

    La stessa citazione come `documento_caricato` e come
    `suggerimento_generale` resta doppia. Gli item sono già validati
    (`min_length=1`, `Literal`): il trim può solo accorciare, quindi
    `model_copy(update=...)` senza rivalidazione è sicuro; le istanze già
    pulite sono restituite così come sono.
    """
    seen: set[tuple[str, str]] = set()
    out: list[LessonContentReference] = []
    for ref in v:
        citation = ref.citation.strip()
        if not citation:
            continue
        key = (ref.source, citation.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(
            ref if citation == ref.citation else ref.model_copy(update={"citation": citation})
        )
    return out


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
    objectives_covered: list[LessonContentObjectiveCovered] = Field(default_factory=list)
    topics_covered: list[LessonContentTopicCovered] = Field(default_factory=list)


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
    # Limiti sull'elenco grezzo: vedi `KEY_TAKEAWAYS_MIN`/`KEY_TAKEAWAYS_MAX`.
    key_takeaways: list[str] = Field(min_length=KEY_TAKEAWAYS_MIN, max_length=KEY_TAKEAWAYS_MAX)
    visual_assets: list[GeneratedVisualAsset] = Field(default_factory=list)
    tables: list[LessonContentTable] = Field(default_factory=list)
    equations: list[LessonContentEquation] = Field(default_factory=list)
    examples: list[LessonContentExample] = Field(default_factory=list)
    references: list[LessonContentReference] = Field(default_factory=list)
    coverage_check: LessonContentCoverageCheck
    # Scelte del catalogo delle figure di fonte (solo con catalogo nel
    # prompt): fuse in `visual_assets` da `source_figure_fusion` e poi
    # svuotate. `exclude=True`: non entrano mai in `content_raw` (il PATCH e
    # la materializzazione riscrivono `model_dump()`).
    source_figures: list[SourceFigureChoice] = Field(
        default_factory=list, max_length=20, exclude=True
    )

    @field_validator("key_takeaways")
    @classmethod
    def _dedup_key_takeaways(cls, v: list[str]) -> list[str]:
        # After-validator: `min_length`/`max_length` sono già stati
        # verificati sull'elenco grezzo. La dedup può scendere sotto il
        # minimo: la lezione degrada a 1-2 punti chiave, non viene
        # rigenerata. Vuota dopo il cleanup (solo voci bianche): rifiuto →
        # `OpenAILessonContentError` → retry recuperabile del worker.
        out = _clean_key_takeaways(v)
        if not out:
            raise ValueError("key_takeaways: lista vuota dopo cleanup")
        return out

    @field_validator("references")
    @classmethod
    def _dedup_references(cls, v: list[LessonContentReference]) -> list[LessonContentReference]:
        return _clean_references(v)


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
    multiple_choice_questions: list[AssessmentMCQuestion] = Field(default_factory=list)
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
    `key_takeaways` e `references`, quando presenti, sono normalizzati
    come nell'output AI (trim, vuoti scartati, dedup case-insensitive);
    `[]` è un azzeramento ammesso.
    """

    model_config = ConfigDict(extra="forbid")

    introduction: str | None = None
    sections: list[LessonContentSection] | None = None
    summary: str | None = None
    key_takeaways: list[str] | None = Field(default=None, max_length=KEY_TAKEAWAYS_MAX)
    visual_assets: list[LessonContentVisualAsset] | None = None
    tables: list[LessonContentTable] | None = None
    equations: list[LessonContentEquation] | None = None
    examples: list[LessonContentExample] | None = None
    references: list[LessonContentReference] | None = None
    coverage_check: LessonContentCoverageCheck | None = None

    @field_validator("key_takeaways")
    @classmethod
    def _dedup_key_takeaways(cls, v: list[str] | None) -> list[str] | None:
        # None = campo assente nel PATCH (il CRUD non lo riscrive: usa
        # `is not None`); [] = il docente ha svuotato l'elenco, ammesso
        # senza ValueError (l'editor invia [] filtrando le righe vuote).
        return None if v is None else _clean_key_takeaways(v)

    @field_validator("references")
    @classmethod
    def _dedup_references(
        cls, v: list[LessonContentReference] | None
    ) -> list[LessonContentReference] | None:
        return None if v is None else _clean_references(v)


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
    # Verdetto del revisore delle figure di fonte (PROMPT 19): solo avvisi
    # per l'editor, mai applicati al contenuto.
    content_figure_review: dict[str, Any] | None = None
