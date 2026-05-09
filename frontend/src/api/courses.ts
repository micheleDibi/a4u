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
  | "speech_pending"
  | "speech_ready"
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

export interface LessonContentVisualAsset {
  asset_id: string;
  asset_type: "diagram" | "schema" | "image" | "illustration" | "chart";
  format: "mermaid" | "image_prompt" | "image_search_query" | "description";
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

export interface CourseLessonOut {
  id: string;
  module_id: string;
  course_id: string;
  position: number;
  lesson_code: string;
  title: string;
  summary: string;
  is_introductory: boolean;
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
  content_raw: LessonContentRaw | null;
  // §7 — Export PDF
  pdf_status: LessonPdfStatus;
  pdf_progress: number;
  pdf_progress_phase: string | null;
  pdf_error: string | null;
  pdf_attempts: number;
  pdf_generated_at: string | null;
  pdf_template_id: string | null;
  pdf_path: string | null;
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
}

export interface CourseOut {
  id: string;
  organization_id: string;
  title: string;
  objectives: string;
  language_code: string;
  argomenti_chiave: string[];
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
  assignee_user_id?: string | null;
  taxonomies?: TaxonomyAssignmentsInput;
}

export interface CourseUpdateInput {
  title?: string;
  objectives?: string;
  language_code?: string;
  cfu?: number;
  argomenti_chiave?: string[];
  taxonomies?: TaxonomyAssignmentsInput;
  status?: CourseStatus;
}

export interface CourseListParams {
  page?: number;
  page_size?: number;
  q?: string;
  status?: CourseStatus;
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
      pdfTemplateId?: string | null
    ): Promise<CourseOut> => {
      const res = await apiClient.post<CourseOut>(
        `${base(orgId)}/${courseId}/lessons-pdf/export-all`,
        undefined,
        pdfTemplateId
          ? { params: { pdf_template_id: pdfTemplateId } }
          : undefined
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
  },
};

// Per il Select read-only delle tassonomie attive (riusa endpoint
// pubblico `/course-taxonomy/{type}`).
export const courseTaxonomyPublicApi = {
  listActive: async (type: TaxonomyType): Promise<TaxonomyTermOut[]> => {
    const res = await apiClient.get<TaxonomyTermOut[]>(
      `/course-taxonomy/${type}`
    );
    return res.data;
  },
};
