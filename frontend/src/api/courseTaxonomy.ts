import { apiClient } from "./client";

export type TaxonomyType =
  | "category"
  | "teaching_style"
  | "content_depth"
  | "teacher_role"
  | "audience_size"
  | "knowledge_level"
  | "target_audience"
  | "eqf_level";

export const TAXONOMY_TYPES: TaxonomyType[] = [
  "category",
  "teaching_style",
  "content_depth",
  "teacher_role",
  "audience_size",
  "knowledge_level",
  "target_audience",
  "eqf_level",
];

export const HIERARCHICAL_TAXONOMY_TYPES: ReadonlySet<TaxonomyType> = new Set([
  "category",
  "teacher_role",
  "eqf_level",
]);

export const TAXONOMIES_WITH_DESCRIPTION: ReadonlySet<TaxonomyType> = new Set([
  "audience_size",
  "target_audience",
  "eqf_level",
]);

export interface TaxonomyTermOut {
  id: string;
  taxonomy_type: TaxonomyType;
  parent_id: string | null;
  slug: string;
  sort_order: number;
  is_active: boolean;
  labels: Record<string, string>;
  descriptions: Record<string, string> | null;
  created_at: string;
  updated_at: string;
}

export interface TaxonomyTermCreateInput {
  slug: string;
  parent_id?: string | null;
  sort_order?: number | null;
  is_active?: boolean;
  labels: Record<string, string>;
  descriptions?: Record<string, string> | null;
}

export interface TaxonomyTermUpdateInput {
  parent_id?: string | null;
  sort_order?: number | null;
  is_active?: boolean;
  labels?: Record<string, string>;
  descriptions?: Record<string, string> | null;
  unset_parent?: boolean;
}

export interface TermAutoTranslateResponse {
  term_id: string;
  translated_label_langs: string[];
  translated_description_langs: string[];
  skipped_label_langs: string[];
  skipped_description_langs: string[];
  errors: string[];
}

export interface TaxonomyBulkAutoTranslateResponse {
  taxonomy_type: TaxonomyType;
  terms_total: number;
  languages_processed: string[];
  translated_labels: number;
  translated_descriptions: number;
  errors: string[];
}

const base = (type: TaxonomyType) => `/admin/course-taxonomy/${type}`;

export const courseTaxonomyApi = {
  list: async (type: TaxonomyType): Promise<TaxonomyTermOut[]> => {
    const res = await apiClient.get<TaxonomyTermOut[]>(base(type));
    return res.data;
  },
  get: async (type: TaxonomyType, id: string): Promise<TaxonomyTermOut> => {
    const res = await apiClient.get<TaxonomyTermOut>(`${base(type)}/${id}`);
    return res.data;
  },
  create: async (
    type: TaxonomyType,
    payload: TaxonomyTermCreateInput
  ): Promise<TaxonomyTermOut> => {
    const res = await apiClient.post<TaxonomyTermOut>(base(type), payload);
    return res.data;
  },
  update: async (
    type: TaxonomyType,
    id: string,
    payload: TaxonomyTermUpdateInput
  ): Promise<TaxonomyTermOut> => {
    const res = await apiClient.patch<TaxonomyTermOut>(
      `${base(type)}/${id}`,
      payload
    );
    return res.data;
  },
  remove: async (type: TaxonomyType, id: string): Promise<void> => {
    await apiClient.delete(`${base(type)}/${id}`);
  },
  move: async (
    type: TaxonomyType,
    id: string,
    direction: "up" | "down"
  ): Promise<TaxonomyTermOut> => {
    const res = await apiClient.post<TaxonomyTermOut>(
      `${base(type)}/${id}/move`,
      { direction }
    );
    return res.data;
  },
  autoTranslate: async (
    type: TaxonomyType,
    id: string
  ): Promise<TermAutoTranslateResponse> => {
    const res = await apiClient.post<TermAutoTranslateResponse>(
      `${base(type)}/${id}/auto-translate`,
      undefined,
      // Una singola label/desc per ogni lingua attiva → 1 chiamata OpenAI
      // per lingua. Allunghiamo il timeout per stare al sicuro.
      { timeout: 600_000 }
    );
    return res.data;
  },
  bulkAutoTranslate: async (
    type: TaxonomyType
  ): Promise<TaxonomyBulkAutoTranslateResponse> => {
    const res = await apiClient.post<TaxonomyBulkAutoTranslateResponse>(
      `${base(type)}/auto-translate-all`,
      undefined,
      // Bulk: ~1 chiamata OpenAI per lingua, ma ogni chiamata può avere
      // 100+ termini in batch. Timeout molto generoso (30 min).
      { timeout: 1_800_000 }
    );
    return res.data;
  },
};
