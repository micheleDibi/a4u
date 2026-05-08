import { apiClient } from "./client";
import type { OrganizationOut, Page, UUID } from "./types";

export interface OrganizationFormFields {
  name: string;
  email: string;
  phone?: string;
  website?: string;
  vat_number?: string;
  fiscal_code?: string;
  country?: string;
  address?: string;
  city?: string;
  province?: string;
  postal_code?: string;
}

function appendOrg(form: FormData, data: OrganizationFormFields) {
  for (const key of Object.keys(data) as (keyof OrganizationFormFields)[]) {
    const v = data[key];
    if (v !== undefined && v !== null && v !== "") form.append(key, String(v));
  }
}

export const organizationsApi = {
  async list(params: { page?: number; page_size?: number; q?: string } = {}) {
    const res = await apiClient.get<Page<OrganizationOut>>("/admin/organizations", {
      params,
    });
    return res.data;
  },
  async get(id: UUID) {
    const res = await apiClient.get<OrganizationOut>(`/admin/organizations/${id}`);
    return res.data;
  },
  async create(data: OrganizationFormFields, logo: File | null) {
    const form = new FormData();
    appendOrg(form, data);
    if (logo) form.append("logo", logo);
    const res = await apiClient.post<OrganizationOut>("/admin/organizations", form, {
      headers: { "Content-Type": "multipart/form-data" },
    });
    return res.data;
  },
  async update(
    id: UUID,
    data: OrganizationFormFields,
    options: { logo?: File | null; remove_logo?: boolean } = {}
  ) {
    const form = new FormData();
    appendOrg(form, data);
    if (options.logo) form.append("logo", options.logo);
    if (options.remove_logo) form.append("remove_logo", "true");
    const res = await apiClient.put<OrganizationOut>(`/admin/organizations/${id}`, form, {
      headers: { "Content-Type": "multipart/form-data" },
    });
    return res.data;
  },
  async remove(id: UUID) {
    await apiClient.delete(`/admin/organizations/${id}`);
  },
  async enrollUser(orgId: UUID, userId: UUID, roleCode: string) {
    await apiClient.post(`/admin/organizations/${orgId}/memberships`, {
      user_id: userId,
      role_code: roleCode,
    });
  },
};
