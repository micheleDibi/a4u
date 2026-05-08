import { apiClient } from "./client";
import type { AvatarOut, AvatarVoiceScriptOut } from "./types";

export interface MyAvatarUpsertFields {
  audio_lang?: string;
}

export interface MyAvatarUpsertFiles {
  image?: File | null;
  audio?: File | null;
}

function buildForm(fields: MyAvatarUpsertFields, files: MyAvatarUpsertFiles) {
  const form = new FormData();
  if (fields.audio_lang !== undefined) form.append("audio_lang", fields.audio_lang);
  if (files.image) form.append("image", files.image);
  if (files.audio) form.append("audio", files.audio);
  return form;
}

export const myAvatarApi = {
  async get(): Promise<AvatarOut | null> {
    const res = await apiClient.get<AvatarOut | null>("/me/avatar");
    return res.data;
  },
  async upsert(fields: MyAvatarUpsertFields, files: MyAvatarUpsertFiles) {
    const res = await apiClient.put<AvatarOut>(
      "/me/avatar",
      buildForm(fields, files),
      { headers: { "Content-Type": "multipart/form-data" } }
    );
    return res.data;
  },
  async remove() {
    await apiClient.delete("/me/avatar");
  },
  async regenerateClips() {
    const res = await apiClient.post<AvatarOut>("/me/avatar/clips/regenerate");
    return res.data;
  },
  async getVoiceScript(lang?: string): Promise<AvatarVoiceScriptOut | null> {
    const res = await apiClient.get<AvatarVoiceScriptOut | null>(
      "/me/avatar/voice-script",
      { params: lang ? { lang } : undefined }
    );
    return res.data;
  },
};
