import { z } from "zod";

/**
 * Regole password allineate al backend (`is_password_strong`):
 * ≥10 caratteri, ≥1 maiuscola, ≥1 cifra. I messaggi d'errore vengono
 * mappati all'i18n al call-site (qui solo codici stabili).
 */
export const passwordSchema = z
  .string()
  .min(10, { message: "min" })
  .regex(/[A-Z]/, { message: "upper" })
  .regex(/[0-9]/, { message: "digit" });

/** True se la password rispetta le regole di robustezza. */
export function isPasswordStrong(value: string): boolean {
  return passwordSchema.safeParse(value).success;
}
