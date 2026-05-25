/**
 * Palette unificata per gli status mostrati nelle dashboard (admin + org).
 *
 * Ogni voce ha sia la classe Tailwind (per backgrounds in DOM elements) sia
 * il valore hex equivalente (necessario per `conic-gradient` inline nei
 * widget tipo `DonutMini`, dove non si può usare una classe Tailwind dentro
 * `style="background"`).
 */

export interface StatusColor {
  bg: string; // Tailwind background class
  hex: string; // hex per `style.background`
}

const FALLBACK: StatusColor = { bg: "bg-zinc-400", hex: "#a1a1aa" };

const STATUS_COLORS: Record<string, StatusColor> = {
  // -- Lifecycle generico ----------------------------------------------------
  empty: { bg: "bg-zinc-300 dark:bg-zinc-600", hex: "#d4d4d8" },
  pending: { bg: "bg-amber-500", hex: "#f59e0b" },
  processing: { bg: "bg-blue-500", hex: "#3b82f6" },
  ready: { bg: "bg-emerald-500", hex: "#10b981" },
  approved: { bg: "bg-emerald-600", hex: "#059669" },
  failed: { bg: "bg-red-500", hex: "#ef4444" },
  cancelled: { bg: "bg-zinc-500", hex: "#71717a" },
  partial: { bg: "bg-amber-400", hex: "#fbbf24" },

  // -- Course status (alcuni overlap col lifecycle) --------------------------
  draft: { bg: "bg-zinc-300 dark:bg-zinc-600", hex: "#d4d4d8" },
  published: { bg: "bg-emerald-700", hex: "#047857" },
  archived: { bg: "bg-zinc-500", hex: "#71717a" },

  // -- Login activity --------------------------------------------------------
  success: { bg: "bg-emerald-500", hex: "#10b981" },
  failure: { bg: "bg-red-500", hex: "#ef4444" },
};

export function statusColor(status: string): StatusColor {
  return STATUS_COLORS[status] ?? FALLBACK;
}

// ---------------------------------------------------------------------------
// Bucketing dei 17 status di `course.status` in 8 macro-fasi visivamente
// più leggibili. La sequenza riflette il flow della pipeline AI corsi.
// ---------------------------------------------------------------------------

export type CourseMacroBucket =
  | "draft"
  | "architecture"
  | "structure"
  | "content"
  | "slides"
  | "speech"
  | "published"
  | "archived";

export const COURSE_MACRO_ORDER: CourseMacroBucket[] = [
  "draft",
  "architecture",
  "structure",
  "content",
  "slides",
  "speech",
  "published",
  "archived",
];

const COURSE_STATUS_TO_BUCKET: Record<string, CourseMacroBucket> = {
  draft: "draft",
  architecture_pending: "architecture",
  architecture_ready: "architecture",
  architecture_approved: "architecture",
  lessons_structure_pending: "structure",
  lessons_structure_ready: "structure",
  lessons_structure_approved: "structure",
  content_pending: "content",
  content_ready: "content",
  content_approved: "content",
  slides_pending: "slides",
  slides_ready: "slides",
  slides_approved: "slides",
  speech_pending: "speech",
  speech_ready: "speech",
  speech_approved: "speech",
  published: "published",
  archived: "archived",
};

export function courseBucketFor(status: string): CourseMacroBucket | null {
  return COURSE_STATUS_TO_BUCKET[status] ?? null;
}

export const COURSE_BUCKET_COLORS: Record<CourseMacroBucket, StatusColor> = {
  draft: { bg: "bg-zinc-300 dark:bg-zinc-600", hex: "#d4d4d8" },
  architecture: { bg: "bg-violet-500", hex: "#8b5cf6" },
  structure: { bg: "bg-sky-500", hex: "#0ea5e9" },
  content: { bg: "bg-blue-500", hex: "#3b82f6" },
  slides: { bg: "bg-cyan-500", hex: "#06b6d4" },
  speech: { bg: "bg-teal-500", hex: "#14b8a6" },
  published: { bg: "bg-emerald-600", hex: "#059669" },
  archived: { bg: "bg-zinc-500", hex: "#71717a" },
};

// ---------------------------------------------------------------------------
// Ordine canonico per gli status lifecycle (per stabilità visiva nei
// `StatusBarChart`: rosso/ambra/blu/verde sempre nello stesso ordine).
// ---------------------------------------------------------------------------

export const LIFECYCLE_ORDER: string[] = [
  "approved",
  "ready",
  "processing",
  "pending",
  "partial",
  "failed",
  "cancelled",
  "empty",
];

export function sortByLifecycleOrder<T extends { status: string }>(
  items: readonly T[],
): T[] {
  const idx = new Map<string, number>(
    LIFECYCLE_ORDER.map((s, i) => [s, i]),
  );
  return [...items].sort((a, b) => {
    const ia = idx.get(a.status) ?? Number.MAX_SAFE_INTEGER;
    const ib = idx.get(b.status) ?? Number.MAX_SAFE_INTEGER;
    return ia - ib;
  });
}
