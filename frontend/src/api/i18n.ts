import { apiClient } from "./client";

export interface LanguageOut {
  code: string;
  name_native: string;
  is_active: boolean;
  is_default: boolean;
  rtl: boolean;
  flag_country_code: string | null;
  created_at: string;
  updated_at: string;
  untranslated_count: number;
}

export interface AutoTranslateResponse {
  code: string;
  requested: number;
  translated: number;
  skipped: number;
  errors: string[];
}

export interface PublicLanguageOut {
  code: string;
  name_native: string;
  rtl: boolean;
  flag_country_code: string | null;
  is_default: boolean;
}

export interface LanguageCreateInput {
  code: string;
  name_native: string;
  flag_country_code?: string | null;
  rtl?: boolean;
  is_active?: boolean;
  copy_translations_from?: string | null;
}

export interface LanguageUpdateInput {
  name_native?: string;
  flag_country_code?: string | null;
  rtl?: boolean;
  is_active?: boolean;
  is_default?: boolean;
}

export const i18nApi = {
  // Public (no auth required)
  publicLanguages: async (): Promise<PublicLanguageOut[]> => {
    const res = await apiClient.get<PublicLanguageOut[]>("/i18n/languages");
    return res.data;
  },
  publicTranslations: async (
    code: string
  ): Promise<{ code: string; translations: Record<string, string> }> => {
    const res = await apiClient.get<{ code: string; translations: Record<string, string> }>(
      `/i18n/translations/${code}`
    );
    return res.data;
  },

  // Admin
  list: async (): Promise<LanguageOut[]> => {
    const res = await apiClient.get<LanguageOut[]>("/admin/i18n/languages");
    return res.data;
  },
  get: async (code: string): Promise<LanguageOut> => {
    const res = await apiClient.get<LanguageOut>(`/admin/i18n/languages/${code}`);
    return res.data;
  },
  create: async (data: LanguageCreateInput): Promise<LanguageOut> => {
    const res = await apiClient.post<LanguageOut>("/admin/i18n/languages", data);
    return res.data;
  },
  update: async (code: string, data: LanguageUpdateInput): Promise<LanguageOut> => {
    const res = await apiClient.patch<LanguageOut>(`/admin/i18n/languages/${code}`, data);
    return res.data;
  },
  remove: async (code: string): Promise<void> => {
    await apiClient.delete(`/admin/i18n/languages/${code}`);
  },
  getTranslations: async (
    code: string
  ): Promise<{ language: LanguageOut; translations: Record<string, string> }> => {
    const res = await apiClient.get<{
      language: LanguageOut;
      translations: Record<string, string>;
    }>(`/admin/i18n/languages/${code}/translations`);
    return res.data;
  },
  putTranslations: async (
    code: string,
    translations: Record<string, string>
  ): Promise<{ upserted: number }> => {
    const res = await apiClient.put<{ upserted: number }>(
      `/admin/i18n/languages/${code}/translations`,
      { translations }
    );
    return res.data;
  },
  patchTranslations: async (
    code: string,
    translations: Record<string, string>
  ): Promise<{ upserted: number }> => {
    const res = await apiClient.patch<{ upserted: number }>(
      `/admin/i18n/languages/${code}/translations`,
      { translations }
    );
    return res.data;
  },
  autoTranslate: async (code: string): Promise<AutoTranslateResponse> => {
    const res = await apiClient.post<AutoTranslateResponse>(
      `/admin/i18n/languages/${code}/auto-translate`,
      undefined,
      // ~400 chiavi in batch da 80 verso OpenAI possono richiedere 1-3 min;
      // alziamo il timeout per evitare che il client molli mentre il
      // backend sta ancora lavorando.
      { timeout: 600_000 }
    );
    return res.data;
  },
  clearTranslations: async (code: string): Promise<{ deleted: number }> => {
    const res = await apiClient.delete<{ deleted: number }>(
      `/admin/i18n/languages/${code}/translations`
    );
    return res.data;
  },
};
