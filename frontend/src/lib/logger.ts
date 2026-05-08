import { apiClient } from "../api/client";

const isDev = import.meta.env.DEV;

type Level = "debug" | "info" | "warn" | "error";

async function ship(level: Level, message: string, meta?: Record<string, unknown>) {
  if (isDev) return;
  try {
    await apiClient.post("/system/log-client", {
      level,
      message,
      meta: meta ?? {},
      ts: new Date().toISOString(),
    });
  } catch {
    /* noop */
  }
}

export const logger = {
  debug(msg: string, meta?: Record<string, unknown>) {
    if (isDev) console.debug(msg, meta);
  },
  info(msg: string, meta?: Record<string, unknown>) {
    if (isDev) console.info(msg, meta);
    void ship("info", msg, meta);
  },
  warn(msg: string, meta?: Record<string, unknown>) {
    console.warn(msg, meta);
    void ship("warn", msg, meta);
  },
  error(msg: string, meta?: Record<string, unknown>) {
    console.error(msg, meta);
    void ship("error", msg, meta);
  },
};
