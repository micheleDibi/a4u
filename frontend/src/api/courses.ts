import { apiClient } from "./client";
import type { TaxonomyTermOut, TaxonomyType } from "./courseTaxonomy";

export type CourseStatus =
  | "draft"
  | "architecture_pending"
  | "architecture_ready"
  | "architecture_approved"
  | "lessons_structure_pending"
  | "lessons_structure_ready"
  | "lessons_structure_approved"
  | "content_pending"
  | "content_ready"
  | "content_approved"
  | "slides_pending"
  | "slides_ready"
  | "slides_approved"
  | "speech_pending"
  | "speech_ready"
  | "speech_approved"
  | "video_pending"
  | "video_ready"
  | "avatar_video_pending"
  | "avatar_video_ready"
  | "published"
  | "archived";

export type LessonsStructureModuleStatus =
  | "empty"
  | "pending"
  | "processing"
  | "ready"
  | "approved"
  | "failed";

export type DocumentSummaryStatus =
  | "pending"
  | "processing"
  | "ready"
  | "failed";

export interface UserCompact {
  id: string;
  email: string;
  full_name: string;
}

export interface TaxonomyAssignmentsInput {
  categoria?: string | null;
  stile_insegnamento?: string | null;
  profondita_contenuto?: string | null;
  ruolo_docente?: string | null;
  dimensione_pubblico?: string | null;
  livello_conoscenza?: string | null;
  destinatari?: string | null;
  livello_eqf?: string | null;
}

export interface DocumentSummaryTokens {
  prompt: number;
  completion: number;
  total: number;
  model: string;
}

export interface DocumentSummaryKeyConcept {
  name: string;
  explanation: string;
}
export interface DocumentSummaryDefinition {
  term: string;
  definition: string;
}
export interface DocumentSummaryExample {
  title: string;
  synthesis: string;
}
export interface DocumentSummaryFormula {
  label: string;
  latex_or_text: string;
  meaning: string;
}
export interface DocumentSummaryAuthor {
  type: "author" | "cited_reference";
  value: string;
}

export interface DocumentSummaryOut {
  source_title: string;
  detected_language: string;
  abstract: string;
  structure_outline: string[];
  key_concepts: DocumentSummaryKeyConcept[];
  definitions: DocumentSummaryDefinition[];
  examples_or_cases: DocumentSummaryExample[];
  formulas_or_rules: DocumentSummaryFormula[];
  authors_and_references: DocumentSummaryAuthor[];
  didactic_relevance_tags: string[];
}

export interface CourseDocumentOut {
  id: string;
  filename_original: string;
  mime_type: string;
  size_bytes: number;
  summary_status: DocumentSummaryStatus;
  summary_generated_at: string | null;
  summary_error: string | null;
  summary_attempts: number;
  summary_tokens: DocumentSummaryTokens | null;
  text_chars_extracted: number | null;
  created_at: string;
}

export interface CourseDocumentDetailOut extends CourseDocumentOut {
  summary: DocumentSummaryOut | null;
}

export interface RecommendedBibliographyItem {
  authors: string;
  title: string;
  publisher: string;
  year: string;
  note: string;
  source: "from_uploaded_documents" | "general_knowledge_suggestion";
  confidence: "confirmed" | "to_verify";
}

export interface LessonStructureMandatoryTopic {
  topic_id: string;
  topic: string;
  rationale: string;
}

export interface LessonStructureSectionOutline {
  section_id: string;
  title: string;
  purpose: string;
  covers_topic_ids: string[];
}

export type LessonContentStatus =
  | "empty"
  | "pending"
  | "processing"
  | "ready"
  | "approved"
  | "failed";

export type LessonPdfStatus =
  | "empty"
  | "pending"
  | "processing"
  | "ready"
  | "failed";

export interface LessonContentSection {
  section_id: string;
  title: string;
  content: string;
  objectives_addressed: string[];
  topics_addressed: string[];
}

/**
 * Asset visivo di una lezione.
 *
 * Nuovi asset prodotti dal frontend hanno solo `format` ∈ { "mermaid", "image" }.
 * I valori `image_prompt | image_search_query | description` sono LEGACY:
 * presenti nei corsi pre-refactor, vengono ancora renderizzati come
 * placeholder testuale (vedi `MarkdownRenderer.VisualAssetBlock`) ma l'editor
 * non li produce più.
 *
 * Il campo `asset_type` è stato rimosso (era puramente metadata; nessun
 * codice di rendering lo leggeva). Vecchi record JSONB possono ancora
 * contenerlo: Pydantic backend ha `extra="ignore"` per tollerarlo.
 */
export type LessonContentVisualAssetFormat =
  | "mermaid"
  | "image"
  // — legacy read-only —
  | "image_prompt"
  | "image_search_query"
  | "description";

export interface LessonContentVisualAsset {
  asset_id: string;
  format: LessonContentVisualAssetFormat;
  /**
   * - `format="mermaid"`: codice Mermaid.
   * - `format="image"`: path pubblico relativo (es. `lesson_assets/{cid}/{uuid}.png`).
   *   Per renderizzare l'immagine usare `/uploads/${content}`.
   * - legacy: testo libero (prompt, query, descrizione).
   */
  content: string;
  caption: string;
  alt_text: string;
}

export interface LessonContentTable {
  table_id: string;
  markdown: string;
  caption: string;
}

export interface LessonContentEquation {
  equation_id: string;
  latex: string;
  label: string;
  explanation: string;
}

export interface LessonContentExample {
  example_id: string;
  title: string;
  content: string;
}

export interface LessonContentReference {
  citation: string;
  source: "documento_caricato" | "suggerimento_generale";
}

export interface LessonContentObjectiveCovered {
  objective: string;
  covered_in_section_ids: string[];
}

export interface LessonContentTopicCovered {
  topic_id: string;
  covered_in_section_ids: string[];
}

export interface LessonContentCoverageCheck {
  objectives_covered: LessonContentObjectiveCovered[];
  topics_covered: LessonContentTopicCovered[];
}

export interface LessonContentRaw {
  lesson_id: string;
  lesson_title: string;
  is_introductory: boolean;
  estimated_word_count: number;
  introduction: string;
  sections: LessonContentSection[];
  summary: string;
  key_takeaways: string[];
  visual_assets: LessonContentVisualAsset[];
  tables: LessonContentTable[];
  equations: LessonContentEquation[];
  examples: LessonContentExample[];
  references: LessonContentReference[];
  coverage_check: LessonContentCoverageCheck;
}

export interface LessonContentTokens {
  prompt: number;
  completion: number;
  total: number;
  model: string;
}

export interface LessonContentUpdateInput {
  introduction?: string;
  sections?: LessonContentSection[];
  summary?: string;
  key_takeaways?: string[];
  visual_assets?: LessonContentVisualAsset[];
  tables?: LessonContentTable[];
  equations?: LessonContentEquation[];
  examples?: LessonContentExample[];
  references?: LessonContentReference[];
  coverage_check?: LessonContentCoverageCheck;
}

// --- Verifica delle competenze (content_raw quando is_assessment) ---

export interface AssessmentMCOption {
  option_id: string;
  text: string;
}

export interface AssessmentMCQuestion {
  question_id: string;
  text: string;
  options: AssessmentMCOption[];
  correct_option_id: string;
}

export interface AssessmentOpenQuestion {
  question_id: string;
  text: string;
  expected_answer: string;
}

export interface LessonAssessmentRaw {
  lesson_id: string;
  lesson_title: string;
  is_assessment: true;
  multiple_choice_questions: AssessmentMCQuestion[];
  open_questions: AssessmentOpenQuestion[];
}

export interface LessonAssessmentUpdateInput {
  multiple_choice_questions?: AssessmentMCQuestion[];
  open_questions?: AssessmentOpenQuestion[];
}

/** Narrowing: `content_raw` di una lezione-verifica vs lezione normale. */
export function isAssessmentRaw(
  raw: LessonContentRaw | LessonAssessmentRaw | null | undefined,
): raw is LessonAssessmentRaw {
  return !!raw && (raw as LessonAssessmentRaw).is_assessment === true;
}

// ---------------------------------------------------------------------------
// Fase 4 — Slide della lezione (§7)
// ---------------------------------------------------------------------------

export type LessonSlidesStatus =
  | "empty"
  | "pending"
  | "processing"
  | "ready"
  | "approved"
  | "failed";

export type SlideType =
  | "title"
  | "agenda"
  | "prerequisites"
  | "concept"
  | "definition"
  | "diagram"
  | "formula"
  | "table"
  | "example"
  | "case_study"
  | "exercise"
  | "discussion"
  | "summary"
  | "takeaways"
  | "references"
  | "bibliography";

export interface LessonSlideItem {
  slide_number: number;
  slide_id: string;
  type: SlideType;
  title: string;
  body: string;
  bullets: string[];
  references_assets: string[];
  source_section_id: string;
}

export interface LessonSlideNewAsset {
  asset_id: string;
  format: LessonContentVisualAssetFormat;
  content: string;
  caption: string;
  alt_text: string;
}

export interface LessonSlidesRaw {
  lesson_id: string;
  total_slides: number;
  slides: LessonSlideItem[];
  new_assets: LessonSlideNewAsset[];
  // Asset NUOVI non visivi creati in Fase 4 (parità con le Dispense).
  // Opzionali per retro-compatibilità con slides_raw pre-feature.
  new_tables?: LessonContentTable[];
  new_equations?: LessonContentEquation[];
  new_examples?: LessonContentExample[];
}

export interface LessonSlidesTokens {
  prompt: number;
  completion: number;
  total: number;
  model: string;
}

export interface LessonSlidesUpdateInput {
  slides?: LessonSlideItem[];
  new_assets?: LessonSlideNewAsset[];
  new_tables?: LessonContentTable[];
  new_equations?: LessonContentEquation[];
  new_examples?: LessonContentExample[];
}

// ---------------------------------------------------------------------------
// Fase 5 — Discorso temporizzato (§8)
// ---------------------------------------------------------------------------

export type LessonSpeechStatus =
  | "empty"
  | "pending"
  | "processing"
  | "ready"
  | "approved"
  | "failed";

export interface LessonSpeechSegment {
  segment_id: string;
  slide_id: string;
  text: string;
  estimated_duration_seconds: number;
  delivery_notes: string;
}

export interface LessonSlideSegmentsMapEntry {
  slide_id: string;
  segment_ids: string[];
  slide_total_duration_seconds: number;
}

export interface LessonSpeechRaw {
  lesson_id: string;
  language: string;
  target_duration_seconds: number;
  estimated_total_duration_seconds: number;
  estimated_total_word_count: number;
  speech_segments: LessonSpeechSegment[];
  slide_to_segments_map: LessonSlideSegmentsMapEntry[];
}

export interface LessonSpeechTokens {
  prompt: number;
  completion: number;
  total: number;
  model: string;
}

export interface LessonSpeechUpdateInput {
  speech_segments?: LessonSpeechSegment[];
  slide_to_segments_map?: LessonSlideSegmentsMapEntry[];
}

export interface CourseLessonOut {
  id: string;
  module_id: string;
  course_id: string;
  position: number;
  lesson_code: string;
  title: string;
  summary: string;
  is_introductory: boolean;
  // Lezione di verifica delle competenze (ultima del modulo quando
  // assessment_lesson_enabled è attivo). content_raw è LessonAssessmentRaw.
  is_assessment: boolean;
  recommended_bibliography: RecommendedBibliographyItem[];
  // Fase 2 — struttura formativa (§5)
  learning_objectives: string[];
  mandatory_topics: LessonStructureMandatoryTopic[];
  prerequisites: string[];
  section_outline: LessonStructureSectionOutline[];
  // Fase 3 — contenuti (§6)
  content_status: LessonContentStatus;
  content_progress: number;
  content_progress_phase: string | null;
  content_error: string | null;
  content_attempts: number;
  content_generated_at: string | null;
  content_approved_at: string | null;
  content_tokens: LessonContentTokens | null;
  content_regeneration_hint: string | null;
  // Stale-detection — set solo da CRUD manuale, non dai worker AI.
  lesson_structure_modified_at: string | null;
  content_modified_at: string | null;
  content_raw: LessonContentRaw | LessonAssessmentRaw | null;
  // §7 — Export PDF
  pdf_status: LessonPdfStatus;
  pdf_progress: number;
  pdf_progress_phase: string | null;
  pdf_error: string | null;
  pdf_attempts: number;
  pdf_generated_at: string | null;
  pdf_template_id: string | null;
  pdf_path: string | null;
  // Fase 4 — Slide della lezione (§7)
  slides_status: LessonSlidesStatus;
  slides_progress: number;
  slides_progress_phase: string | null;
  slides_error: string | null;
  slides_attempts: number;
  slides_generated_at: string | null;
  slides_approved_at: string | null;
  slides_tokens: LessonSlidesTokens | null;
  slides_regeneration_hint: string | null;
  // Stale-detection — set solo da CRUD manuale, non dal worker AI.
  slides_modified_at: string | null;
  slides_raw: LessonSlidesRaw | null;
  // §7 — Export PDF delle slide
  slides_pdf_status: LessonPdfStatus;
  slides_pdf_progress: number;
  slides_pdf_progress_phase: string | null;
  slides_pdf_error: string | null;
  slides_pdf_attempts: number;
  slides_pdf_generated_at: string | null;
  slides_pdf_template_id: string | null;
  slides_pdf_path: string | null;
  // Fase 5 — Discorso temporizzato (§8)
  speech_status: LessonSpeechStatus;
  speech_progress: number;
  speech_progress_phase: string | null;
  speech_error: string | null;
  speech_attempts: number;
  speech_generated_at: string | null;
  speech_approved_at: string | null;
  speech_tokens: LessonSpeechTokens | null;
  speech_regeneration_hint: string | null;
  // Stale-detection — set solo da CRUD manuale, non dal worker AI.
  speech_modified_at: string | null;
  speech_raw: LessonSpeechRaw | null;
  // §8 — Export PDF del discorso (Step 7). Tipi presenti subito così
  // il polling e la cache li gestiscono uniformemente.
  speech_pdf_status?: LessonPdfStatus;
  speech_pdf_progress?: number;
  speech_pdf_progress_phase?: string | null;
  speech_pdf_error?: string | null;
  speech_pdf_attempts?: number;
  speech_pdf_generated_at?: string | null;
  speech_pdf_template_id?: string | null;
  speech_pdf_path?: string | null;
}

export interface LessonStructureTokens {
  prompt: number;
  completion: number;
  total: number;
  model: string;
}

export interface CourseModuleOut {
  id: string;
  course_id: string;
  position: number;
  module_code: string;
  title: string;
  description: string;
  lessons: CourseLessonOut[];
  // Fase 2 — meta della struttura lezioni
  lessons_structure_status: LessonsStructureModuleStatus;
  lessons_structure_progress: number;
  lessons_structure_progress_phase: string | null;
  lessons_structure_error: string | null;
  lessons_structure_attempts: number;
  lessons_structure_generated_at: string | null;
  lessons_structure_approved_at: string | null;
  lessons_structure_tokens: LessonStructureTokens | null;
  lessons_structure_regeneration_hint: string | null;
  // Stale-detection — set quando l'utente modifica modulo o sue lezioni
  // architettura. NON toccato dai worker AI.
  architecture_modified_at: string | null;
}

export interface LessonStructureUpdateInput {
  learning_objectives?: string[];
  mandatory_topics?: LessonStructureMandatoryTopic[];
  prerequisites?: string[];
  section_outline?: LessonStructureSectionOutline[];
}

export interface ArchitectureTokens {
  prompt: number;
  completion: number;
  total: number;
  model: string;
}

export type GlossaryStatus =
  | "empty"
  | "pending"
  | "processing"
  | "ready"
  | "approved"
  | "failed";

export interface GlossaryTerm {
  term: string;
  translation: string;
  usage_note: string;
}

export interface GlossaryRaw {
  course_id: string;
  terms: GlossaryTerm[];
}

export interface GlossaryTokens {
  prompt: number;
  completion: number;
  total: number;
  model: string;
}

export type CourseDuplicationJobStatus =
  | "pending"
  | "processing"
  | "ready"
  | "failed";

export interface CourseDuplicationJobCompact {
  id: string;
  source_course_id: string;
  target_course_id: string | null;
  target_language_code: string;
  status: CourseDuplicationJobStatus;
  progress: number;
  progress_phase: string | null;
  /** Sotto-progresso a granularità fine (es. "23/48 lezioni completate"). */
  progress_detail: string | null;
  /** ISO timestamp. Usato dal FE per calcolare l'ETA stimato. */
  started_at: string | null;
}

export interface CourseDuplicationJobOut extends CourseDuplicationJobCompact {
  error: string | null;
  attempts: number;
  tokens: Record<string, unknown> | null;
  requested_by_user_id: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface CourseListLessonsProgress {
  /** Conteggio lezioni didattiche (esclude `is_assessment=true`). */
  total: number;
  /** Lezioni con `content_status` in ('ready','approved'). */
  content_ready: number;
  /** Lezioni con `slides_status` in ('ready','approved'). */
  slides_ready: number;
  /** Lezioni con `video_status == 'ready'`. */
  videos_ready: number;
  /** Lezioni con `avatar_video_status == 'ready'`. */
  avatar_videos_ready: number;
}

export interface CourseListItemOut {
  id: string;
  title: string;
  status: CourseStatus;
  language_code: string;
  assignee: UserCompact;
  modules_count: number;
  cfu: number;
  updated_at: string;
  created_at: string;
  lessons_progress: CourseListLessonsProgress;
  /** Popolato quando il corso è target di un job di duplicazione attivo
   *  (status pending|processing). Driver del badge "Duplicazione in
   *  corso XX%" sulla riga. */
  duplication_job?: CourseDuplicationJobCompact | null;
}

export interface CourseOut {
  id: string;
  organization_id: string;
  title: string;
  objectives: string;
  language_code: string;
  // Override TTS per i video lezione (Fase 6 §9). Null = usa language_code.
  video_language_code: string | null;
  argomenti_chiave: string[];
  /** Testo libero opzionale (es. "Informatica"). Popolato solo quando
   * il livello EQF e' Laurea triennale o Laurea Magistrale. */
  corso_di_laurea: string | null;
  cfu: number;
  modules_count: number;
  lessons_per_module: number;
  lesson_duration_minutes: number;
  assessment_lesson_enabled: boolean;
  multiple_choice_questions_count: number;
  open_questions_count: number;
  status: CourseStatus;
  assignee: UserCompact;
  created_by: UserCompact | null;
  documents: CourseDocumentOut[];
  // Architettura corso (Fase 1) — popolata solo per status >= architecture_ready.
  modules: CourseModuleOut[];
  course_overview: string | null;
  pedagogical_rationale: string | null;
  architecture_attempts: number;
  architecture_tokens: ArchitectureTokens | null;
  architecture_error: string | null;
  architecture_generated_at: string | null;
  architecture_regeneration_hint: string | null;
  architecture_progress: number;
  architecture_progress_phase: string | null;
  // Wizard setup lock — null = editabile, valorizzato = locked.
  didactic_setup_confirmed_at: string | null;
  // Glossario corso (§10.1)
  glossary_status: GlossaryStatus;
  glossary_raw: GlossaryRaw | null;
  glossary_tokens: GlossaryTokens | null;
  glossary_generated_at: string | null;
  glossary_error: string | null;
  categoria: TaxonomyTermOut | null;
  stile_insegnamento: TaxonomyTermOut | null;
  profondita_contenuto: TaxonomyTermOut | null;
  ruolo_docente: TaxonomyTermOut | null;
  dimensione_pubblico: TaxonomyTermOut | null;
  livello_conoscenza: TaxonomyTermOut | null;
  destinatari: TaxonomyTermOut | null;
  livello_eqf: TaxonomyTermOut | null;
  created_at: string;
  updated_at: string;
}

export interface CourseCreateInput {
  title: string;
  objectives?: string;
  language_code: string;
  cfu: number;
  argomenti_chiave?: string[];
  corso_di_laurea?: string | null;
  assignee_user_id?: string | null;
  taxonomies?: TaxonomyAssignmentsInput;
}

export interface CourseUpdateInput {
  title?: string;
  objectives?: string;
  language_code?: string;
  // Override TTS — passare un codice XTTS_SUPPORTED o "" per resettare a null.
  video_language_code?: string | null;
  cfu?: number;
  argomenti_chiave?: string[];
  /** Testo libero opzionale; "" o null azzera lato server. */
  corso_di_laurea?: string | null;
  taxonomies?: TaxonomyAssignmentsInput;
  status?: CourseStatus;
}

// ---------------------------------------------------------------------
// Paper scientifici (ricerca multi-source via OpenAlex + Semantic
// Scholar + Crossref). Vedi `coursesApi.papers.*`.
// ---------------------------------------------------------------------

export type PaperType = "article" | "preprint" | "review" | "other";

export interface PaperSearchFilters {
  year_from?: number | null;
  year_to?: number | null;
  is_oa?: boolean | null;
  min_citations?: number | null;
  author_name?: string | null;
  venue_name?: string | null;
  work_type?: PaperType | null;
}

export interface PaperSearchInput {
  query?: string;
  filters?: PaperSearchFilters;
  cursor?: string | null;
  per_page?: number;
}

export interface PaperOut {
  /** OpenAlex Work ID (URL completo "https://openalex.org/W..."). */
  id: string;
  doi: string | null;
  title: string;
  abstract: string | null;
  authors: string[];
  year: number | null;
  journal: string | null;
  citations: number;
  is_oa: boolean;
  oa_pdf_url: string | null;
  doi_url: string | null;
  work_type: PaperType | null;
  keywords: string[];
  /** Relevance score normalizzato 0..1 (OpenAlex `relevance_score`
   * compresso via x/(x+5)). Null se non calcolato. */
  relevance_score: number | null;
  /** Popolato on-demand da Semantic Scholar. */
  tldr: string | null;
  /** Popolato on-demand da Crossref. */
  subjects: string[];
  references_count: number | null;
}

export interface PaperSearchResultsOut {
  results: PaperOut[];
  next_cursor: string | null;
  total_count: number;
}

export interface PaperAISummaryOut {
  short_summary: string;
  technical_summary: string;
  keywords: string[];
  study_limitations: string;
}

export interface PaperImportItemResultOut {
  document_id: string;
  filename: string;
  mode: "pdf" | "metadata";
  paper_id: string;
}

export interface PaperImportResultOut {
  imported: PaperImportItemResultOut[];
  pdf_count: number;
  metadata_count: number;
}

export interface CourseListParams {
  page?: number;
  page_size?: number;
  q?: string;
  status?: CourseStatus;
  assignee_user_id?: string;
  language_code?: string;
  /** ISO 8601 datetime string (inclusive lower bound on `created_at`). */
  created_after?: string;
  /** ISO 8601 datetime string (inclusive upper bound on `created_at`). */
  created_before?: string;
  /** ISO 8601 datetime string (inclusive lower bound on `updated_at`). */
  updated_after?: string;
  /** ISO 8601 datetime string (inclusive upper bound on `updated_at`). */
  updated_before?: string;
  sort_by?: "created_at" | "updated_at";
  sort_dir?: "asc" | "desc";
}

export interface PageMeta {
  page: number;
  page_size: number;
  total: number;
}

export interface PageOf<T> {
  items: T[];
  meta: PageMeta;
}

const base = (orgId: string) => `/orgs/${orgId}/courses`;

export const coursesApi = {
  list: async (
    orgId: string,
    params: CourseListParams = {}
  ): Promise<PageOf<CourseListItemOut>> => {
    const res = await apiClient.get<PageOf<CourseListItemOut>>(base(orgId), {
      params: {
        page: params.page ?? 1,
        page_size: params.page_size ?? 20,
        q: params.q || undefined,
        status: params.status || undefined,
        assignee_user_id: params.assignee_user_id || undefined,
        language_code: params.language_code || undefined,
        created_after: params.created_after || undefined,
        created_before: params.created_before || undefined,
        updated_after: params.updated_after || undefined,
        updated_before: params.updated_before || undefined,
        sort_by: params.sort_by || undefined,
        sort_dir: params.sort_dir || undefined,
      },
    });
    return res.data;
  },
  get: async (orgId: string, courseId: string): Promise<CourseOut> => {
    const res = await apiClient.get<CourseOut>(`${base(orgId)}/${courseId}`);
    return res.data;
  },
  create: async (
    orgId: string,
    payload: CourseCreateInput
  ): Promise<CourseOut> => {
    const res = await apiClient.post<CourseOut>(base(orgId), payload);
    return res.data;
  },
  update: async (
    orgId: string,
    courseId: string,
    payload: CourseUpdateInput
  ): Promise<CourseOut> => {
    const res = await apiClient.patch<CourseOut>(
      `${base(orgId)}/${courseId}`,
      payload
    );
    return res.data;
  },
  remove: async (orgId: string, courseId: string): Promise<void> => {
    await apiClient.delete(`${base(orgId)}/${courseId}`);
  },
  // --- Duplicazione corso in altra lingua (background job) ----------
  duplicate: async (
    orgId: string,
    courseId: string,
    target_language_code: string
  ): Promise<CourseDuplicationJobOut> => {
    const res = await apiClient.post<CourseDuplicationJobOut>(
      `${base(orgId)}/${courseId}/duplicate`,
      undefined,
      { params: { target_language_code } }
    );
    return res.data;
  },
  listDuplications: async (
    orgId: string,
    courseId: string
  ): Promise<CourseDuplicationJobOut[]> => {
    const res = await apiClient.get<CourseDuplicationJobOut[]>(
      `${base(orgId)}/${courseId}/duplications`
    );
    return res.data;
  },
  cancelDuplication: async (
    orgId: string,
    jobId: string
  ): Promise<CourseDuplicationJobOut> => {
    const res = await apiClient.post<CourseDuplicationJobOut>(
      `${base(orgId)}/duplication-jobs/${jobId}/cancel`
    );
    return res.data;
  },
  updateAssignee: async (
    orgId: string,
    courseId: string,
    assignee_user_id: string
  ): Promise<CourseOut> => {
    const res = await apiClient.patch<CourseOut>(
      `${base(orgId)}/${courseId}/assignee`,
      { assignee_user_id }
    );
    return res.data;
  },
  setup: {
    confirmDidactic: async (
      orgId: string,
      courseId: string
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/setup/confirm-didactic`
      );
      return res.data;
    },
    unlock: async (orgId: string, courseId: string): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/setup/unlock`
      );
      return res.data;
    },
  },
  documents: {
    list: async (
      orgId: string,
      courseId: string
    ): Promise<CourseDocumentOut[]> => {
      const res = await apiClient.get<CourseDocumentOut[]>(
        `${base(orgId)}/${courseId}/documents`
      );
      return res.data;
    },
    get: async (
      orgId: string,
      courseId: string,
      docId: string,
      opts: { includeSummary?: boolean } = {}
    ): Promise<CourseDocumentDetailOut> => {
      const res = await apiClient.get<CourseDocumentDetailOut>(
        `${base(orgId)}/${courseId}/documents/${docId}`,
        {
          params: opts.includeSummary
            ? { include_summary: true }
            : undefined,
        }
      );
      return res.data;
    },
    upload: async (
      orgId: string,
      courseId: string,
      file: File
    ): Promise<CourseDocumentOut> => {
      const form = new FormData();
      form.append("file", file);
      const res = await apiClient.post<CourseDocumentOut>(
        `${base(orgId)}/${courseId}/documents`,
        form,
        {
          headers: { "Content-Type": "multipart/form-data" },
          // L'upload può richiedere tempo per file fino a 25MB.
          timeout: 120_000,
        }
      );
      return res.data;
    },
    reprocess: async (
      orgId: string,
      courseId: string,
      docId: string
    ): Promise<CourseDocumentOut> => {
      const res = await apiClient.post<CourseDocumentOut>(
        `${base(orgId)}/${courseId}/documents/${docId}/reprocess`
      );
      return res.data;
    },
    remove: async (
      orgId: string,
      courseId: string,
      docId: string
    ): Promise<void> => {
      await apiClient.delete(`${base(orgId)}/${courseId}/documents/${docId}`);
    },
  },
  papers: {
    /**
     * Cerca paper scientifici via OpenAlex (primary). Cursor-based
     * pagination. NON include enrichment (chiamato solo on-demand
     * dagli endpoint `aiSummary` e `import`).
     */
    search: async (
      orgId: string,
      courseId: string,
      payload: PaperSearchInput,
    ): Promise<PaperSearchResultsOut> => {
      const res = await apiClient.post<PaperSearchResultsOut>(
        `${base(orgId)}/${courseId}/papers/search`,
        payload,
        { timeout: 60_000 },
      );
      return res.data;
    },
    /**
     * Riassunto AI sincrono di un paper (4 sezioni). Esegue
     * enrichment server-side se il DOI e' presente (Semantic
     * Scholar + Crossref in parallelo).
     */
    aiSummary: async (
      orgId: string,
      courseId: string,
      paper: PaperOut,
    ): Promise<PaperAISummaryOut> => {
      const res = await apiClient.post<PaperAISummaryOut>(
        `${base(orgId)}/${courseId}/papers/ai-summary`,
        { paper },
        { timeout: 180_000 },
      );
      return res.data;
    },
    /**
     * Importa N paper come `CourseDocument`. PDF reale se OA,
     * altrimenti `.md` con metadati. Il worker
     * `course_document_worker` prendera' in carico l'analisi AI.
     */
    importMany: async (
      orgId: string,
      courseId: string,
      papers: PaperOut[],
    ): Promise<PaperImportResultOut> => {
      const res = await apiClient.post<PaperImportResultOut>(
        `${base(orgId)}/${courseId}/papers/import`,
        { papers },
        { timeout: 300_000 },
      );
      return res.data;
    },
  },
  objectives: {
    /**
     * Genera proposta di `objectives` + `argomenti_chiave` a partire da
     * un documento di riferimento caricato dall'utente. Il file e'
     * one-shot temporaneo: NON viene persistito come documento del
     * corso. La risposta NON modifica il corso — l'utente conferma
     * esplicitamente in un dialog di preview prima di applicare.
     */
    generateFromFile: async (
      orgId: string,
      courseId: string,
      file: File,
    ): Promise<{ objectives: string; argomenti_chiave: string[] }> => {
      const form = new FormData();
      form.append("file", file);
      const res = await apiClient.post<{
        objectives: string;
        argomenti_chiave: string[];
      }>(
        `${base(orgId)}/${courseId}/objectives/generate-from-file`,
        form,
        {
          headers: { "Content-Type": "multipart/form-data" },
          // L'AI puo' richiedere 10-30s per documenti grandi.
          timeout: 180_000,
        },
      );
      return res.data;
    },
  },
  architecture: {
    generate: async (
      orgId: string,
      courseId: string,
      regeneration_hint?: string | null
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/architecture/generate`,
        { regeneration_hint: regeneration_hint || null }
      );
      return res.data;
    },
    approve: async (orgId: string, courseId: string): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/architecture/approve`
      );
      return res.data;
    },
  },
  modules: {
    create: async (
      orgId: string,
      courseId: string,
      payload: { title: string; description?: string }
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/modules`,
        payload
      );
      return res.data;
    },
    update: async (
      orgId: string,
      courseId: string,
      moduleId: string,
      payload: { title?: string; description?: string }
    ): Promise<CourseOut> => {
      const res = await apiClient.patch<CourseOut>(
        `${base(orgId)}/${courseId}/modules/${moduleId}`,
        payload
      );
      return res.data;
    },
    remove: async (
      orgId: string,
      courseId: string,
      moduleId: string
    ): Promise<CourseOut> => {
      const res = await apiClient.delete<CourseOut>(
        `${base(orgId)}/${courseId}/modules/${moduleId}`
      );
      return res.data;
    },
    reorder: async (
      orgId: string,
      courseId: string,
      ids: string[]
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/modules/reorder`,
        { ids }
      );
      return res.data;
    },
    generateLessons: async (
      orgId: string,
      courseId: string,
      moduleId: string
    ): Promise<CourseOut> => {
      // La chiamata è sync e attende OpenAI (~20-30s). Override del default
      // axios di 20s per evitare un timeout client mentre il server completa.
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/modules/${moduleId}/generate-lessons`,
        undefined,
        { timeout: 300_000 }
      );
      return res.data;
    },
  },
  lessons: {
    create: async (
      orgId: string,
      courseId: string,
      moduleId: string,
      payload: {
        title: string;
        summary?: string;
        is_introductory?: boolean;
        recommended_bibliography?: RecommendedBibliographyItem[];
      }
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/modules/${moduleId}/lessons`,
        payload
      );
      return res.data;
    },
    update: async (
      orgId: string,
      courseId: string,
      lessonId: string,
      payload: {
        title?: string;
        summary?: string;
        is_introductory?: boolean;
        recommended_bibliography?: RecommendedBibliographyItem[];
      }
    ): Promise<CourseOut> => {
      const res = await apiClient.patch<CourseOut>(
        `${base(orgId)}/${courseId}/lessons/${lessonId}`,
        payload
      );
      return res.data;
    },
    remove: async (
      orgId: string,
      courseId: string,
      lessonId: string
    ): Promise<CourseOut> => {
      const res = await apiClient.delete<CourseOut>(
        `${base(orgId)}/${courseId}/lessons/${lessonId}`
      );
      return res.data;
    },
    reorder: async (
      orgId: string,
      courseId: string,
      moduleId: string,
      ids: string[]
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/modules/${moduleId}/lessons/reorder`,
        { ids }
      );
      return res.data;
    },
  },
  lessonsStructure: {
    generateModule: async (
      orgId: string,
      courseId: string,
      moduleId: string,
      regeneration_hint?: string | null
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/modules/${moduleId}/lessons-structure/generate`,
        { regeneration_hint: regeneration_hint || null }
      );
      return res.data;
    },
    generateAll: async (
      orgId: string,
      courseId: string,
      regeneration_hint?: string | null
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/lessons-structure/generate-all`,
        { regeneration_hint: regeneration_hint || null }
      );
      return res.data;
    },
    approveModule: async (
      orgId: string,
      courseId: string,
      moduleId: string
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/modules/${moduleId}/lessons-structure/approve`
      );
      return res.data;
    },
    approveAll: async (
      orgId: string,
      courseId: string
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/lessons-structure/approve-all`
      );
      return res.data;
    },
    updateLesson: async (
      orgId: string,
      courseId: string,
      lessonId: string,
      payload: LessonStructureUpdateInput
    ): Promise<CourseOut> => {
      const res = await apiClient.patch<CourseOut>(
        `${base(orgId)}/${courseId}/lessons/${lessonId}/structure`,
        payload
      );
      return res.data;
    },
  },
  lessonContent: {
    generateLesson: async (
      orgId: string,
      courseId: string,
      lessonId: string,
      regeneration_hint?: string | null
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/lessons/${lessonId}/content/generate`,
        { regeneration_hint: regeneration_hint || null }
      );
      return res.data;
    },
    generateAll: async (
      orgId: string,
      courseId: string,
      regeneration_hint?: string | null
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/lessons-content/generate-all`,
        { regeneration_hint: regeneration_hint || null }
      );
      return res.data;
    },
    generateMissing: async (
      orgId: string,
      courseId: string
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/lessons-content/generate-missing`
      );
      return res.data;
    },
    approveLesson: async (
      orgId: string,
      courseId: string,
      lessonId: string
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/lessons/${lessonId}/content/approve`
      );
      return res.data;
    },
    cancelAll: async (
      orgId: string,
      courseId: string
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/lessons-content/cancel-all`
      );
      return res.data;
    },
    approveAll: async (
      orgId: string,
      courseId: string
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/lessons-content/approve-all`
      );
      return res.data;
    },
    updateLesson: async (
      orgId: string,
      courseId: string,
      lessonId: string,
      payload: LessonContentUpdateInput
    ): Promise<CourseOut> => {
      const res = await apiClient.patch<CourseOut>(
        `${base(orgId)}/${courseId}/lessons/${lessonId}/content`,
        payload
      );
      return res.data;
    },
    updateAssessment: async (
      orgId: string,
      courseId: string,
      lessonId: string,
      payload: LessonAssessmentUpdateInput
    ): Promise<CourseOut> => {
      const res = await apiClient.patch<CourseOut>(
        `${base(orgId)}/${courseId}/lessons/${lessonId}/assessment`,
        payload
      );
      return res.data;
    },
  },
  lessonSlides: {
    generateLesson: async (
      orgId: string,
      courseId: string,
      lessonId: string,
      regeneration_hint?: string | null
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/lessons/${lessonId}/slides/generate`,
        { regeneration_hint: regeneration_hint || null }
      );
      return res.data;
    },
    generateAll: async (
      orgId: string,
      courseId: string,
      regeneration_hint?: string | null
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/lessons-slides/generate-all`,
        { regeneration_hint: regeneration_hint || null }
      );
      return res.data;
    },
    generateMissing: async (
      orgId: string,
      courseId: string
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/lessons-slides/generate-missing`
      );
      return res.data;
    },
    approveLesson: async (
      orgId: string,
      courseId: string,
      lessonId: string
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/lessons/${lessonId}/slides/approve`
      );
      return res.data;
    },
    cancelAll: async (
      orgId: string,
      courseId: string
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/lessons-slides/cancel-all`
      );
      return res.data;
    },
    approveAll: async (
      orgId: string,
      courseId: string
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/lessons-slides/approve-all`
      );
      return res.data;
    },
    updateLesson: async (
      orgId: string,
      courseId: string,
      lessonId: string,
      payload: LessonSlidesUpdateInput
    ): Promise<CourseOut> => {
      const res = await apiClient.patch<CourseOut>(
        `${base(orgId)}/${courseId}/lessons/${lessonId}/slides`,
        payload
      );
      return res.data;
    },
  },
  lessonSpeech: {
    generateLesson: async (
      orgId: string,
      courseId: string,
      lessonId: string,
      regeneration_hint?: string | null
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/lessons/${lessonId}/speech/generate`,
        { regeneration_hint: regeneration_hint || null }
      );
      return res.data;
    },
    generateAll: async (
      orgId: string,
      courseId: string,
      regeneration_hint?: string | null
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/lessons-speech/generate-all`,
        { regeneration_hint: regeneration_hint || null }
      );
      return res.data;
    },
    generateMissing: async (
      orgId: string,
      courseId: string
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/lessons-speech/generate-missing`
      );
      return res.data;
    },
    approveLesson: async (
      orgId: string,
      courseId: string,
      lessonId: string
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/lessons/${lessonId}/speech/approve`
      );
      return res.data;
    },
    cancelAll: async (
      orgId: string,
      courseId: string
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/lessons-speech/cancel-all`
      );
      return res.data;
    },
    approveAll: async (
      orgId: string,
      courseId: string
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/lessons-speech/approve-all`
      );
      return res.data;
    },
    updateLesson: async (
      orgId: string,
      courseId: string,
      lessonId: string,
      payload: LessonSpeechUpdateInput
    ): Promise<CourseOut> => {
      const res = await apiClient.patch<CourseOut>(
        `${base(orgId)}/${courseId}/lessons/${lessonId}/speech`,
        payload
      );
      return res.data;
    },
  },
  glossary: {
    regenerate: async (
      orgId: string,
      courseId: string
    ): Promise<CourseOut> => {
      // Sync OpenAI call — gpt-5.5 può richiedere 10-30s di reasoning.
      // Override del timeout default (20s) per non interrompere prima.
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/glossary/regenerate`,
        {},
        { timeout: 180_000 }
      );
      return res.data;
    },
  },
  lessonPdf: {
    exportLesson: async (
      orgId: string,
      courseId: string,
      lessonId: string,
      pdfTemplateId?: string | null
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/lessons/${lessonId}/pdf/export`,
        undefined,
        pdfTemplateId
          ? { params: { pdf_template_id: pdfTemplateId } }
          : undefined
      );
      return res.data;
    },
    exportAll: async (
      orgId: string,
      courseId: string,
      pdfTemplateId?: string | null,
      onlyMissing?: boolean
    ): Promise<CourseOut> => {
      const params: Record<string, string | boolean> = {};
      if (pdfTemplateId) params.pdf_template_id = pdfTemplateId;
      if (onlyMissing) params.only_missing = true;
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/lessons-pdf/export-all`,
        undefined,
        Object.keys(params).length ? { params } : undefined
      );
      return res.data;
    },
    cancelAll: async (
      orgId: string,
      courseId: string
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/lessons-pdf/cancel-all`
      );
      return res.data;
    },
    /** URL diretto per il download del PDF (apri in nuova tab o
     *  download tramite anchor href). Il backend risponde con
     *  `Content-Disposition: attachment; filename=...`. */
    downloadUrl: (
      orgId: string,
      courseId: string,
      lessonId: string
    ): string =>
      `${apiClient.defaults.baseURL ?? ""}${base(orgId)}/${courseId}/lessons/${lessonId}/pdf/download`,
    /** Trigger download via blob (usa l'auth cookie/jwt automaticamente). */
    download: async (
      orgId: string,
      courseId: string,
      lessonId: string
    ): Promise<{ blob: Blob; filename: string | null }> => {
      const res = await apiClient.get<Blob>(
        `${base(orgId)}/${courseId}/lessons/${lessonId}/pdf/download`,
        { responseType: "blob" }
      );
      const cd = (res.headers["content-disposition"] as string | undefined) ?? "";
      const m = /filename\*?="?([^";]+)"?/i.exec(cd);
      const filename = m ? decodeURIComponent(m[1]) : null;
      return { blob: res.data, filename };
    },
    /** Scarica un singolo PDF concatenato di tutte le lezioni del modulo. */
    downloadModuleMerged: async (
      orgId: string,
      courseId: string,
      moduleId: string
    ): Promise<{ blob: Blob; filename: string | null }> => {
      const res = await apiClient.get<Blob>(
        `${base(orgId)}/${courseId}/modules/${moduleId}/lessons-pdf/download-merged`,
        { responseType: "blob" }
      );
      const cd = (res.headers["content-disposition"] as string | undefined) ?? "";
      const m = /filename\*?="?([^";]+)"?/i.exec(cd);
      const filename = m ? decodeURIComponent(m[1]) : null;
      return { blob: res.data, filename };
    },
    /** Scarica uno ZIP con un PDF per ogni lezione del modulo. */
    downloadModuleZip: async (
      orgId: string,
      courseId: string,
      moduleId: string
    ): Promise<{ blob: Blob; filename: string | null }> => {
      const res = await apiClient.get<Blob>(
        `${base(orgId)}/${courseId}/modules/${moduleId}/lessons-pdf/download-zip`,
        { responseType: "blob" }
      );
      const cd = (res.headers["content-disposition"] as string | undefined) ?? "";
      const m = /filename\*?="?([^";]+)"?/i.exec(cd);
      const filename = m ? decodeURIComponent(m[1]) : null;
      return { blob: res.data, filename };
    },
    /** "Scarica tutto": PDF unico di tutte le lezioni del corso. */
    downloadAllMerged: async (
      orgId: string,
      courseId: string
    ): Promise<{ blob: Blob; filename: string | null }> => {
      const res = await apiClient.get<Blob>(
        `${base(orgId)}/${courseId}/lessons-pdf/download-all-merged`,
        { responseType: "blob" }
      );
      const cd = (res.headers["content-disposition"] as string | undefined) ?? "";
      const m = /filename\*?="?([^";]+)"?/i.exec(cd);
      const filename = m ? decodeURIComponent(m[1]) : null;
      return { blob: res.data, filename };
    },
    /** "Scarica tutto": ZIP di tutto il corso (una cartella per modulo). */
    downloadAllZip: async (
      orgId: string,
      courseId: string
    ): Promise<{ blob: Blob; filename: string | null }> => {
      const res = await apiClient.get<Blob>(
        `${base(orgId)}/${courseId}/lessons-pdf/download-all-zip`,
        { responseType: "blob" }
      );
      const cd = (res.headers["content-disposition"] as string | undefined) ?? "";
      const m = /filename\*?="?([^";]+)"?/i.exec(cd);
      const filename = m ? decodeURIComponent(m[1]) : null;
      return { blob: res.data, filename };
    },
  },
  lessonSlidesPdf: {
    exportLesson: async (
      orgId: string,
      courseId: string,
      lessonId: string,
      pdfTemplateId?: string | null
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/lessons/${lessonId}/slides-pdf/export`,
        undefined,
        pdfTemplateId
          ? { params: { pdf_template_id: pdfTemplateId } }
          : undefined
      );
      return res.data;
    },
    exportAll: async (
      orgId: string,
      courseId: string,
      pdfTemplateId?: string | null,
      onlyMissing?: boolean
    ): Promise<CourseOut> => {
      const params: Record<string, string | boolean> = {};
      if (pdfTemplateId) params.pdf_template_id = pdfTemplateId;
      if (onlyMissing) params.only_missing = true;
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/lessons-slides-pdf/export-all`,
        undefined,
        Object.keys(params).length ? { params } : undefined
      );
      return res.data;
    },
    cancelAll: async (
      orgId: string,
      courseId: string
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/lessons-slides-pdf/cancel-all`
      );
      return res.data;
    },
    download: async (
      orgId: string,
      courseId: string,
      lessonId: string
    ): Promise<{ blob: Blob; filename: string | null }> => {
      const res = await apiClient.get<Blob>(
        `${base(orgId)}/${courseId}/lessons/${lessonId}/slides-pdf/download`,
        { responseType: "blob" }
      );
      const cd = (res.headers["content-disposition"] as string | undefined) ?? "";
      const m = /filename\*?="?([^";]+)"?/i.exec(cd);
      const filename = m ? decodeURIComponent(m[1]) : null;
      return { blob: res.data, filename };
    },
    downloadModuleMerged: async (
      orgId: string,
      courseId: string,
      moduleId: string
    ): Promise<{ blob: Blob; filename: string | null }> => {
      const res = await apiClient.get<Blob>(
        `${base(orgId)}/${courseId}/modules/${moduleId}/lessons-slides-pdf/download-merged`,
        { responseType: "blob" }
      );
      const cd = (res.headers["content-disposition"] as string | undefined) ?? "";
      const m = /filename\*?="?([^";]+)"?/i.exec(cd);
      const filename = m ? decodeURIComponent(m[1]) : null;
      return { blob: res.data, filename };
    },
    downloadModuleZip: async (
      orgId: string,
      courseId: string,
      moduleId: string
    ): Promise<{ blob: Blob; filename: string | null }> => {
      const res = await apiClient.get<Blob>(
        `${base(orgId)}/${courseId}/modules/${moduleId}/lessons-slides-pdf/download-zip`,
        { responseType: "blob" }
      );
      const cd = (res.headers["content-disposition"] as string | undefined) ?? "";
      const m = /filename\*?="?([^";]+)"?/i.exec(cd);
      const filename = m ? decodeURIComponent(m[1]) : null;
      return { blob: res.data, filename };
    },
    /** "Scarica tutto": PDF unico slide di tutte le lezioni del corso. */
    downloadAllMerged: async (
      orgId: string,
      courseId: string
    ): Promise<{ blob: Blob; filename: string | null }> => {
      const res = await apiClient.get<Blob>(
        `${base(orgId)}/${courseId}/lessons-slides-pdf/download-all-merged`,
        { responseType: "blob" }
      );
      const cd = (res.headers["content-disposition"] as string | undefined) ?? "";
      const m = /filename\*?="?([^";]+)"?/i.exec(cd);
      const filename = m ? decodeURIComponent(m[1]) : null;
      return { blob: res.data, filename };
    },
    /** "Scarica tutto": ZIP slide di tutto il corso (cartella per modulo). */
    downloadAllZip: async (
      orgId: string,
      courseId: string
    ): Promise<{ blob: Blob; filename: string | null }> => {
      const res = await apiClient.get<Blob>(
        `${base(orgId)}/${courseId}/lessons-slides-pdf/download-all-zip`,
        { responseType: "blob" }
      );
      const cd = (res.headers["content-disposition"] as string | undefined) ?? "";
      const m = /filename\*?="?([^";]+)"?/i.exec(cd);
      const filename = m ? decodeURIComponent(m[1]) : null;
      return { blob: res.data, filename };
    },
  },
  lessonSpeechPdf: {
    exportLesson: async (
      orgId: string,
      courseId: string,
      lessonId: string,
      pdfTemplateId?: string | null
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/lessons/${lessonId}/speech-pdf/export`,
        undefined,
        pdfTemplateId
          ? { params: { pdf_template_id: pdfTemplateId } }
          : undefined
      );
      return res.data;
    },
    exportAll: async (
      orgId: string,
      courseId: string,
      pdfTemplateId?: string | null,
      onlyMissing?: boolean
    ): Promise<CourseOut> => {
      const params: Record<string, string | boolean> = {};
      if (pdfTemplateId) params.pdf_template_id = pdfTemplateId;
      if (onlyMissing) params.only_missing = true;
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/lessons-speech-pdf/export-all`,
        undefined,
        Object.keys(params).length ? { params } : undefined
      );
      return res.data;
    },
    cancelAll: async (
      orgId: string,
      courseId: string
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/lessons-speech-pdf/cancel-all`
      );
      return res.data;
    },
    downloadUrl: (
      orgId: string,
      courseId: string,
      lessonId: string
    ): string =>
      `${apiClient.defaults.baseURL ?? ""}${base(orgId)}/${courseId}/lessons/${lessonId}/speech-pdf/download`,
    download: async (
      orgId: string,
      courseId: string,
      lessonId: string
    ): Promise<{ blob: Blob; filename: string | null }> => {
      const res = await apiClient.get<Blob>(
        `${base(orgId)}/${courseId}/lessons/${lessonId}/speech-pdf/download`,
        { responseType: "blob" }
      );
      const cd = (res.headers["content-disposition"] as string | undefined) ?? "";
      const m = /filename\*?="?([^";]+)"?/i.exec(cd);
      const filename = m ? decodeURIComponent(m[1]) : null;
      return { blob: res.data, filename };
    },
    downloadModuleMerged: async (
      orgId: string,
      courseId: string,
      moduleId: string
    ): Promise<{ blob: Blob; filename: string | null }> => {
      const res = await apiClient.get<Blob>(
        `${base(orgId)}/${courseId}/modules/${moduleId}/lessons-speech-pdf/download-merged`,
        { responseType: "blob" }
      );
      const cd = (res.headers["content-disposition"] as string | undefined) ?? "";
      const m = /filename\*?="?([^";]+)"?/i.exec(cd);
      const filename = m ? decodeURIComponent(m[1]) : null;
      return { blob: res.data, filename };
    },
    downloadModuleZip: async (
      orgId: string,
      courseId: string,
      moduleId: string
    ): Promise<{ blob: Blob; filename: string | null }> => {
      const res = await apiClient.get<Blob>(
        `${base(orgId)}/${courseId}/modules/${moduleId}/lessons-speech-pdf/download-zip`,
        { responseType: "blob" }
      );
      const cd = (res.headers["content-disposition"] as string | undefined) ?? "";
      const m = /filename\*?="?([^";]+)"?/i.exec(cd);
      const filename = m ? decodeURIComponent(m[1]) : null;
      return { blob: res.data, filename };
    },
    /** "Scarica tutto": PDF unico discorso di tutte le lezioni del corso. */
    downloadAllMerged: async (
      orgId: string,
      courseId: string
    ): Promise<{ blob: Blob; filename: string | null }> => {
      const res = await apiClient.get<Blob>(
        `${base(orgId)}/${courseId}/lessons-speech-pdf/download-all-merged`,
        { responseType: "blob" }
      );
      const cd = (res.headers["content-disposition"] as string | undefined) ?? "";
      const m = /filename\*?="?([^";]+)"?/i.exec(cd);
      const filename = m ? decodeURIComponent(m[1]) : null;
      return { blob: res.data, filename };
    },
    /** "Scarica tutto": ZIP discorso di tutto il corso (cartella per modulo). */
    downloadAllZip: async (
      orgId: string,
      courseId: string
    ): Promise<{ blob: Blob; filename: string | null }> => {
      const res = await apiClient.get<Blob>(
        `${base(orgId)}/${courseId}/lessons-speech-pdf/download-all-zip`,
        { responseType: "blob" }
      );
      const cd = (res.headers["content-disposition"] as string | undefined) ?? "";
      const m = /filename\*?="?([^";]+)"?/i.exec(cd);
      const filename = m ? decodeURIComponent(m[1]) : null;
      return { blob: res.data, filename };
    },
  },
  lessonAssets: {
    /** Carica un'immagine come asset visivo per il corso. Ritorna il path
     *  pubblico (`/uploads/lesson_assets/...`) e il path relativo (da
     *  usare come `content` dell'asset con `format="image"`). */
    upload: async (
      orgId: string,
      courseId: string,
      file: File,
    ): Promise<{ path: string; url: string }> => {
      const formData = new FormData();
      formData.append("file", file);
      const res = await apiClient.post<{ path: string; url: string }>(
        `${base(orgId)}/${courseId}/lesson-assets/upload`,
        formData,
        { headers: { "Content-Type": "multipart/form-data" } },
      );
      return res.data;
    },
    /** Chiede al backend di trasformare l'immagine caricata in codice
     *  Mermaid via OpenAI Vision. `path` è quello restituito da `upload`.
     *  Solleva su errore (la UI mostra toast e lascia l'asset invariato). */
    convertToMermaid: async (
      orgId: string,
      courseId: string,
      path: string,
    ): Promise<{ mermaid_code: string; usage: Record<string, unknown> }> => {
      const res = await apiClient.post<{
        mermaid_code: string;
        usage: Record<string, unknown>;
      }>(
        `${base(orgId)}/${courseId}/lesson-assets/convert-to-mermaid`,
        { path },
      );
      return res.data;
    },
  },
  lessonVideo: {
    /** Trigger generazione video MP4 per una singola lezione (Fase 6 §9).
     *  Pre-condizioni runtime: speech_status='approved' AND
     *  slides_status='approved' AND voce assegnatario presente.
     *  Errori dettagliati via `code` (`voice_sample_missing`, ...). */
    generateLesson: async (
      orgId: string,
      courseId: string,
      lessonId: string,
    ): Promise<LessonVideoStatusOut> => {
      const res = await apiClient.post<LessonVideoStatusOut>(
        `${base(orgId)}/${courseId}/lessons/${lessonId}/video/generate`,
        {},
      );
      return res.data;
    },
    generateBatch: async (
      orgId: string,
      courseId: string,
    ): Promise<LessonVideoBatchOut> => {
      const res = await apiClient.post<LessonVideoBatchOut>(
        `${base(orgId)}/${courseId}/lessons-video/generate-batch`,
        {},
      );
      return res.data;
    },
    cancelLesson: async (
      orgId: string,
      courseId: string,
      lessonId: string,
    ): Promise<LessonVideoStatusOut> => {
      const res = await apiClient.post<LessonVideoStatusOut>(
        `${base(orgId)}/${courseId}/lessons/${lessonId}/video/cancel`,
        {},
      );
      return res.data;
    },
    cancelBatch: async (
      orgId: string,
      courseId: string,
    ): Promise<LessonVideoBatchOut> => {
      const res = await apiClient.post<LessonVideoBatchOut>(
        `${base(orgId)}/${courseId}/lessons-video/cancel-batch`,
        {},
      );
      return res.data;
    },
    /** Polling-friendly status per UI progress bar. */
    getLessonStatus: async (
      orgId: string,
      courseId: string,
      lessonId: string,
    ): Promise<LessonVideoStatusOut> => {
      const res = await apiClient.get<LessonVideoStatusOut>(
        `${base(orgId)}/${courseId}/lessons/${lessonId}/video/status`,
      );
      return res.data;
    },
    getCourseStatus: async (
      orgId: string,
      courseId: string,
    ): Promise<LessonVideoBatchOut> => {
      const res = await apiClient.get<LessonVideoBatchOut>(
        `${base(orgId)}/${courseId}/lessons-video/status`,
      );
      return res.data;
    },
  },
  lessonAvatarVideo: {
    /** Trigger generazione del «Video con Avatar» per una singola lezione
     *  (Fase 6b §9b): lip-sync MuseTalk sovrapposto al video MP4 della
     *  lezione. Pre-condizioni: `video_status='ready'` AND avatar
     *  dell'assegnatario con clip pronte. Errori via `code`
     *  (`lesson_video_not_ready`, `avatar_clips_not_ready`, ...). */
    generateLesson: async (
      orgId: string,
      courseId: string,
      lessonId: string,
    ): Promise<LessonAvatarVideoStatusOut> => {
      const res = await apiClient.post<LessonAvatarVideoStatusOut>(
        `${base(orgId)}/${courseId}/lessons/${lessonId}/avatar-video/generate`,
        {},
      );
      return res.data;
    },
    generateBatch: async (
      orgId: string,
      courseId: string,
    ): Promise<LessonAvatarVideoBatchOut> => {
      const res = await apiClient.post<LessonAvatarVideoBatchOut>(
        `${base(orgId)}/${courseId}/lessons-avatar-video/generate-batch`,
        {},
      );
      return res.data;
    },
    cancelLesson: async (
      orgId: string,
      courseId: string,
      lessonId: string,
    ): Promise<LessonAvatarVideoStatusOut> => {
      const res = await apiClient.post<LessonAvatarVideoStatusOut>(
        `${base(orgId)}/${courseId}/lessons/${lessonId}/avatar-video/cancel`,
        {},
      );
      return res.data;
    },
    cancelBatch: async (
      orgId: string,
      courseId: string,
    ): Promise<LessonAvatarVideoBatchOut> => {
      const res = await apiClient.post<LessonAvatarVideoBatchOut>(
        `${base(orgId)}/${courseId}/lessons-avatar-video/cancel-batch`,
        {},
      );
      return res.data;
    },
    /** Polling-friendly status per UI progress bar. */
    getLessonStatus: async (
      orgId: string,
      courseId: string,
      lessonId: string,
    ): Promise<LessonAvatarVideoStatusOut> => {
      const res = await apiClient.get<LessonAvatarVideoStatusOut>(
        `${base(orgId)}/${courseId}/lessons/${lessonId}/avatar-video/status`,
      );
      return res.data;
    },
    getCourseStatus: async (
      orgId: string,
      courseId: string,
    ): Promise<LessonAvatarVideoBatchOut> => {
      const res = await apiClient.get<LessonAvatarVideoBatchOut>(
        `${base(orgId)}/${courseId}/lessons-avatar-video/status`,
      );
      return res.data;
    },
  },
};

// ---------------------------------------------------------------------------
// Tipi Fase 6 — Video MP4 (§9)
// ---------------------------------------------------------------------------

export type LessonVideoStatus =
  | "empty"
  | "pending"
  | "processing"
  | "ready"
  | "failed"
  | "cancelled";

export type LessonVideoPhase =
  | "preparing"
  | "tts"
  | "rendering_slides"
  | "encoding"
  | null;

export interface LessonVideoTokens {
  audio_duration_s?: number;
  video_duration_s?: number;
  encode_duration_ms?: number;
  tts_duration_ms?: number;
  device?: string;
  model_xtts?: string;
  num_segments?: number;
  num_slides?: number;
  file_size_bytes?: number;
}

export interface LessonVideoStatusOut {
  lesson_id: string;
  lesson_code: string;
  status: LessonVideoStatus;
  progress: number;
  progress_phase: LessonVideoPhase;
  video_url: string | null;
  error: string | null;
  attempts: number;
  generated_at: string | null;
  tokens: LessonVideoTokens | null;
  is_stale: boolean;
  speech_approved: boolean;
  slides_approved: boolean;
  voice_sample_available: boolean;
}

// XTTS-v2 supporta 16 lingue (allineato a `clone_voice.py:14-17` dello
// script di riferimento). Filtra il dropdown lingua TTS nel tab Video.
export const XTTS_SUPPORTED_LANGUAGES = [
  "it", "en", "es", "fr", "de", "pt", "pl", "tr",
  "ru", "nl", "cs", "ar", "zh-cn", "ja", "hu", "ko",
] as const;
export type XttsLanguage = (typeof XTTS_SUPPORTED_LANGUAGES)[number];

export function isXttsLanguage(code: string | null | undefined): code is XttsLanguage {
  if (!code) return false;
  const lower = code.toLowerCase();
  if (lower.startsWith("zh")) return true;
  const head = lower.includes("-") ? lower.split("-")[0] : lower;
  return (XTTS_SUPPORTED_LANGUAGES as readonly string[]).includes(head);
}

export interface LessonVideoBatchOut {
  items: LessonVideoStatusOut[];
  total: number;
  ready_count: number;
  processing_count: number;
  pending_count: number;
  failed_count: number;
  eligible_count: number;
  aggregate_progress: number;
}

// ---------------------------------------------------------------------------
// Tipi Fase 6b — Video con Avatar (lip-sync MuseTalk) (§9b)
// ---------------------------------------------------------------------------

export type LessonAvatarVideoStatus =
  | "empty"
  | "pending"
  | "processing"
  | "ready"
  | "failed"
  | "cancelled";

export type LessonAvatarVideoPhase =
  | "preparing"
  | "lipsync"
  | "overlay"
  | null;

export interface LessonAvatarVideoTokens {
  audio_duration_s?: number;
  lipsync_duration_s?: number;
  overlay_duration_ms?: number;
  total_duration_s?: number;
  runpod_job_id?: string;
  num_ready_clips?: number;
  overlay_scale?: number;
  file_size_bytes?: number;
}

export interface LessonAvatarVideoStatusOut {
  lesson_id: string;
  lesson_code: string;
  status: LessonAvatarVideoStatus;
  progress: number;
  progress_phase: LessonAvatarVideoPhase;
  video_url: string | null;
  error: string | null;
  attempts: number;
  generated_at: string | null;
  tokens: LessonAvatarVideoTokens | null;
  is_stale: boolean;
  // Pre-requisiti runtime per disabilitare "Genera" con tooltip mirato.
  lesson_video_ready: boolean;
  avatar_clips_ready: boolean;
}

export interface LessonAvatarVideoBatchOut {
  items: LessonAvatarVideoStatusOut[];
  total: number;
  ready_count: number;
  processing_count: number;
  pending_count: number;
  failed_count: number;
  eligible_count: number;
  aggregate_progress: number;
  avatar_clips_ready: boolean;
}

// Per il Select read-only delle tassonomie attive (riusa endpoint
// pubblico `/course-taxonomy/{type}`).
export const courseTaxonomyPublicApi = {
  listActive: async (type: TaxonomyType): Promise<TaxonomyTermOut[]> => {
    const res = await apiClient.get<TaxonomyTermOut[]>(
      `/course-taxonomy/${type}`
    );
    return res.data;
  },
  /**
   * Batch lookup: una sola request HTTP per ottenere più tassonomie in
   * un colpo. Riduce 7-8 roundtrip a 1. Combinato con la cache TanStack
   * popolata da `useTaxonomyTermsBulk`, le successive `useTaxonomyTerms`
   * leggono dalla cache senza network.
   */
  listActiveBulk: async (
    types: readonly TaxonomyType[],
  ): Promise<Record<TaxonomyType, TaxonomyTermOut[]>> => {
    const res = await apiClient.get<Record<TaxonomyType, TaxonomyTermOut[]>>(
      "/course-taxonomy/bulk",
      { params: { types: types.join(",") } },
    );
    return res.data;
  },
};
