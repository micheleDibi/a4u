import { apiClient } from "./client";
import type { Page, UserOut, UUID } from "./types";

export interface UserCreateFields {
  email: string;
  full_name: string;
  password: string;
  is_platform_admin?: boolean;
}

export const usersApi = {
  async list(params: { page?: number; page_size?: number; q?: string } = {}) {
    const res = await apiClient.get<Page<UserOut>>("/admin/users", { params });
    return res.data;
  },
  async create(data: UserCreateFields) {
    const res = await apiClient.post<UserOut>("/admin/users", data);
    return res.data;
  },
  async update(
    id: UUID,
    data: Partial<{
      full_name: string;
      email: string;
      is_active: boolean;
      is_platform_admin: boolean;
    }>,
  ) {
    const res = await apiClient.put<UserOut>(`/admin/users/${id}`, data);
    return res.data;
  },
  async setPassword(id: UUID, password: string) {
    const res = await apiClient.post<UserOut>(`/admin/users/${id}/password`, {
      password,
    });
    return res.data;
  },
};
