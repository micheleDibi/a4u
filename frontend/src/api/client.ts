import axios, { AxiosError, AxiosResponse, InternalAxiosRequestConfig } from "axios";

const baseURL = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

export const apiClient = axios.create({
  baseURL,
  withCredentials: true,
  headers: { "Content-Type": "application/json" },
  timeout: 20_000,
});

let refreshing: Promise<void> | null = null;

apiClient.interceptors.response.use(
  (res: AxiosResponse) => res,
  async (error: AxiosError) => {
    const cfg = error.config as (InternalAxiosRequestConfig & { _retry?: boolean }) | undefined;
    if (
      error.response?.status === 401 &&
      cfg &&
      !cfg._retry &&
      !cfg.url?.includes("/auth/login") &&
      !cfg.url?.includes("/auth/refresh")
    ) {
      cfg._retry = true;
      try {
        if (!refreshing) {
          refreshing = apiClient.post("/auth/refresh").then(() => undefined);
        }
        await refreshing;
        refreshing = null;
        return apiClient(cfg);
      } catch (refreshErr) {
        refreshing = null;
        return Promise.reject(refreshErr);
      }
    }
    return Promise.reject(error);
  }
);
