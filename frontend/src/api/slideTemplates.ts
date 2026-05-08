import { apiClient } from "./client";
import type { SlideTemplateOut, UUID } from "./types";

export interface SlideTemplateFields {
  name: string;
  text_color: string;
  primary_color: string;
  secondary_color: string;
  font_family: string;
  slide_size: "16:9" | "4:3";
}

export interface SlideTemplateFiles {
  background?: File | null;
  logo_left?: File | null;
  logo_right?: File | null;
  remove_background?: boolean;
  remove_logo_left?: boolean;
  remove_logo_right?: boolean;
}

function buildForm(fields: SlideTemplateFields, files: SlideTemplateFiles) {
  const form = new FormData();
  for (const [k, v] of Object.entries(fields)) form.append(k, String(v));
  if (files.background) form.append("background", files.background);
  if (files.logo_left) form.append("logo_left", files.logo_left);
  if (files.logo_right) form.append("logo_right", files.logo_right);
  if (files.remove_background) form.append("remove_background", "true");
  if (files.remove_logo_left) form.append("remove_logo_left", "true");
  if (files.remove_logo_right) form.append("remove_logo_right", "true");
  return form;
}

export const slideTemplatesApi = {
  async list(orgId: UUID) {
    const res = await apiClient.get<SlideTemplateOut[]>(
      `/orgs/${orgId}/templates/slide`
    );
    return res.data;
  },
  async get(orgId: UUID, id: UUID) {
    const res = await apiClient.get<SlideTemplateOut>(
      `/orgs/${orgId}/templates/slide/${id}`
    );
    return res.data;
  },
  async create(orgId: UUID, fields: SlideTemplateFields, files: SlideTemplateFiles) {
    const res = await apiClient.post<SlideTemplateOut>(
      `/orgs/${orgId}/templates/slide`,
      buildForm(fields, files),
      { headers: { "Content-Type": "multipart/form-data" } }
    );
    return res.data;
  },
  async update(
    orgId: UUID,
    id: UUID,
    fields: SlideTemplateFields,
    files: SlideTemplateFiles
  ) {
    const res = await apiClient.put<SlideTemplateOut>(
      `/orgs/${orgId}/templates/slide/${id}`,
      buildForm(fields, files),
      { headers: { "Content-Type": "multipart/form-data" } }
    );
    return res.data;
  },
  async remove(orgId: UUID, id: UUID) {
    await apiClient.delete(`/orgs/${orgId}/templates/slide/${id}`);
  },
  async setDefault(orgId: UUID, id: UUID) {
    const res = await apiClient.post<SlideTemplateOut>(
      `/orgs/${orgId}/templates/slide/${id}/default`
    );
    return res.data;
  },
};
