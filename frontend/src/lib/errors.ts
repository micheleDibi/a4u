import { AxiosError } from "axios";

/**
 * Voce di `meta.errors` di un 422: stessa forma `loc/msg/type` del handler
 * Pydantic (`core/errors.py`). `asset_id` e `format` sono aggiunti dal gate
 * degli asset visivi (`figure_render_service.validate_visual_assets_or_raise`,
 * `loc = ["visual_assets" | "new_assets", i, "content"]`); l'endpoint
 * `render-function` emette solo `loc/msg/type` con la `loc` del campo della
 * spec (`["expressions", 0, "expr"]`).
 */
export interface ApiErrorEntry {
  loc: Array<string | number>;
  msg: string;
  type: string;
  asset_id?: string;
  format?: string;
}

export interface ApiErrorMeta {
  errors?: ApiErrorEntry[];
  [key: string]: unknown;
}

export interface ApiErrorBody {
  code: string;
  message: string;
  request_id?: string;
  meta?: ApiErrorMeta;
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

/** Chiave di campo di una voce: `loc` unita con `.`, senza l'eventuale
 *  `body` iniziale del 422 strutturale di FastAPI (`expressions.0.expr`). */
export function apiErrorFieldKey(entry: ApiErrorEntry): string {
  const loc = entry.loc[0] === "body" ? entry.loc.slice(1) : entry.loc;
  return loc.map(String).join(".");
}

/** Testo leggibile di più voci: `campo: messaggio; campo: messaggio`. */
export function formatApiErrorEntries(entries: ApiErrorEntry[]): string {
  return entries
    .map((e) => {
      const key = apiErrorFieldKey(e);
      return key ? `${key}: ${e.msg}` : e.msg;
    })
    .join("; ");
}

/** Messaggio leggibile di un errore API: le voci `meta.errors` per campo se
 *  presenti, altrimenti il `message` del corpo. */
export function describeApiError(err: unknown): string {
  const body = extractApiError(err);
  const entries = apiErrorEntries(body);
  return entries.length > 0 ? formatApiErrorEntries(entries) : body.message;
}

/**
 * Errori per asset del 422 del PATCH (`loc = [locRoot, indice, "content"]`),
 * indicizzati per posizione nell'array inviato (`visual_assets` delle
 * Dispense, `new_assets` delle slide): il dialog li mostra accanto alla
 * card corrispondente. Più voci sullo stesso asset sono concatenate.
 */
export function assetErrorsByIndex(
  err: unknown,
  locRoot: string,
): Record<number, string> {
  const out: Record<number, string> = {};
  for (const entry of apiErrorEntries(extractApiError(err))) {
    if (entry.loc[0] !== locRoot) continue;
    const index = Number(entry.loc[1]);
    if (!Number.isInteger(index) || index < 0) continue;
    out[index] = out[index] ? `${out[index]}; ${entry.msg}` : entry.msg;
  }
  return out;
}

/**
 * Mappa per posizione dopo l'eliminazione della card `removed` nel dialog:
 * la sua voce cade, le successive scalano di uno, così gli errori restano
 * accanto all'asset giusto fino al salvataggio seguente.
 */
export function assetErrorsAfterRemoval(
  errors: Record<number, string> | undefined,
  removed: number,
): Record<number, string> | undefined {
  if (!errors) return errors;
  const out: Record<number, string> = {};
  for (const [key, msg] of Object.entries(errors)) {
    const index = Number(key);
    if (index === removed) continue;
    out[index > removed ? index - 1 : index] = msg;
  }
  return out;
}

/** Le voci `meta.errors` di un errore API, filtrate sulla forma attesa
 *  (`loc` array, `msg` stringa); lista vuota se assenti o malformate. */
export function apiErrorEntries(body: ApiErrorBody): ApiErrorEntry[] {
  const raw = body.meta?.errors;
  if (!Array.isArray(raw)) return [];
  return raw.filter(
    (e): e is ApiErrorEntry =>
      !!e &&
      typeof e === "object" &&
      Array.isArray((e as ApiErrorEntry).loc) &&
      typeof (e as ApiErrorEntry).msg === "string",
  );
}
