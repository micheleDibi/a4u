import { apiClient } from "./client";
import type { PdfTemplateKind, PdfTemplateOut, UUID } from "./types";

export interface PdfTemplateFields {
  name: string;
  kind: PdfTemplateKind;
  text_color: string;
  primary_color: string;
  secondary_color: string;
  font_family: string;
  page_size: "A4" | "Letter";
  header_height_mm: number;
  footer_height_mm: number;
  margin_mm: number;
  background_opacity_pct: number;
}

export interface PdfTemplateFiles {
  background?: File | null;
  logo_left?: File | null;
  logo_right?: File | null;
  remove_background?: boolean;
  remove_logo_left?: boolean;
  remove_logo_right?: boolean;
}

function buildForm(fields: PdfTemplateFields, files: PdfTemplateFiles) {
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

export const pdfTemplatesApi = {
  async list(orgId: UUID, kind?: PdfTemplateKind) {
    const res = await apiClient.get<PdfTemplateOut[]>(
      `/orgs/${orgId}/templates/pdf`,
      { params: kind ? { kind } : undefined }
    );
    return res.data;
  },
  async get(orgId: UUID, id: UUID) {
    const res = await apiClient.get<PdfTemplateOut>(
      `/orgs/${orgId}/templates/pdf/${id}`
    );
    return res.data;
  },
  async create(orgId: UUID, fields: PdfTemplateFields, files: PdfTemplateFiles) {
    const res = await apiClient.post<PdfTemplateOut>(
      `/orgs/${orgId}/templates/pdf`,
      buildForm(fields, files),
      { headers: { "Content-Type": "multipart/form-data" } }
    );
    return res.data;
  },
  async update(
    orgId: UUID,
    id: UUID,
    fields: PdfTemplateFields,
    files: PdfTemplateFiles
  ) {
    const res = await apiClient.put<PdfTemplateOut>(
      `/orgs/${orgId}/templates/pdf/${id}`,
      buildForm(fields, files),
      { headers: { "Content-Type": "multipart/form-data" } }
    );
    return res.data;
  },
  async remove(orgId: UUID, id: UUID) {
    await apiClient.delete(`/orgs/${orgId}/templates/pdf/${id}`);
  },
  async setDefault(orgId: UUID, id: UUID) {
    const res = await apiClient.post<PdfTemplateOut>(
      `/orgs/${orgId}/templates/pdf/${id}/default`
    );
    return res.data;
  },
};
