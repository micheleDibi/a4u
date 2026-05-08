import { apiClient } from "./client";
import type { AvatarClipPromptOut, AvatarVoiceScriptOut, UUID } from "./types";

export interface AvatarClipPromptCreate {
  prompt: string;
  label_it?: string | null;
  is_active?: boolean;
}

export interface AvatarClipPromptUpdate {
  prompt?: string;
  label_it?: string | null;
  is_active?: boolean;
}

export const avatarConfigApi = {
  async listPrompts() {
    const res = await apiClient.get<AvatarClipPromptOut[]>("/admin/avatar-config/prompts");
    return res.data;
  },
  async createPrompt(payload: AvatarClipPromptCreate) {
    const res = await apiClient.post<AvatarClipPromptOut>(
      "/admin/avatar-config/prompts",
      payload
    );
    return res.data;
  },
  async updatePrompt(id: UUID, payload: AvatarClipPromptUpdate) {
    const res = await apiClient.put<AvatarClipPromptOut>(
      `/admin/avatar-config/prompts/${id}`,
      payload
    );
    return res.data;
  },
  async deletePrompt(id: UUID) {
    await apiClient.delete(`/admin/avatar-config/prompts/${id}`);
  },
  async reorderPrompts(orderedIds: UUID[]) {
    const res = await apiClient.put<AvatarClipPromptOut[]>(
      "/admin/avatar-config/prompts/reorder",
      { ordered_ids: orderedIds }
    );
    return res.data;
  },
  async listVoiceScripts() {
    const res = await apiClient.get<AvatarVoiceScriptOut[]>(
      "/admin/avatar-config/voice-scripts"
    );
    return res.data;
  },
  async upsertVoiceScript(languageCode: string, text: string) {
    const res = await apiClient.put<AvatarVoiceScriptOut>(
      `/admin/avatar-config/voice-scripts/${languageCode}`,
      { text }
    );
    return res.data;
  },
  async deleteVoiceScript(languageCode: string) {
    await apiClient.delete(`/admin/avatar-config/voice-scripts/${languageCode}`);
  },
};
