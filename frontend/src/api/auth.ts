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
};
