import { apiClient } from "./client";
import type { MeOut } from "./types";

export const authApi = {
  async login(email: string, password: string) {
    await apiClient.post("/auth/login", { email, password });
  },
  async logout() {
    await apiClient.post("/auth/logout");
  },
  async me(): Promise<MeOut> {
    const res = await apiClient.get<MeOut>("/auth/me");
    return res.data;
  },
  async updateMe(full_name: string): Promise<MeOut> {
    const res = await apiClient.patch<MeOut>("/auth/me", { full_name });
    return res.data;
  },
  async changeEmail(
    current_password: string,
    new_email: string,
  ): Promise<MeOut> {
    const res = await apiClient.post<MeOut>("/auth/me/change-email", {
      current_password,
      new_email,
    });
    return res.data;
  },
  async changePassword(
    current_password: string,
    new_password: string,
  ): Promise<MeOut> {
    const res = await apiClient.post<MeOut>("/auth/me/change-password", {
      current_password,
      new_password,
    });
    return res.data;
  },
};
