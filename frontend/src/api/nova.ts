import { apiClient } from "./client";

// Tipi mirror degli schemi Pydantic BE (`backend/app/schemas/nova.py`).

export interface NovaPageContextPayload {
  page: string;
  fields: Record<string, unknown>;
  org_id?: string | null;
}

export type NovaRole = "user" | "assistant";

export interface NovaMessage {
  role: NovaRole;
  content: string;
}

export interface NovaChatRequest {
  message: string;
  context: NovaPageContextPayload;
  history: NovaMessage[];
  language_code: string;
}

export interface NovaChatResponse {
  message: string;
}

export interface NovaWelcomeRequest {
  context: NovaPageContextPayload;
  language_code: string;
}

export interface NovaWelcomeResponse {
  message: string;
}

export const novaApi = {
  chat: async (payload: NovaChatRequest): Promise<NovaChatResponse> => {
    const res = await apiClient.post<NovaChatResponse>("/nova/chat", payload, {
      timeout: 60_000,
    });
    return res.data;
  },
  welcome: async (
    payload: NovaWelcomeRequest,
  ): Promise<NovaWelcomeResponse> => {
    const res = await apiClient.post<NovaWelcomeResponse>(
      "/nova/welcome",
      payload,
      { timeout: 30_000 },
    );
    return res.data;
  },
};
