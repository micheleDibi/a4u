import { AxiosError } from "axios";

export interface ApiErrorBody {
  code: string;
  message: string;
  request_id?: string;
  meta?: Record<string, unknown>;
}

export function extractApiError(err: unknown): ApiErrorBody {
  if (err instanceof AxiosError) {
    const data = err.response?.data as ApiErrorBody | undefined;
    if (data && typeof data === "object" && "message" in data) {
      return data;
    }
    return {
      code: "network_error",
      message: err.message ?? "Errore di rete.",
    };
  }
  return { code: "unknown_error", message: "Errore inatteso." };
}
