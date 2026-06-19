import type { ReactNode } from "react";

import type { CourseLessonOut } from "@/api/courses";

/**
 * Sottoinsieme comune di `LessonVideoStatusOut` e
 * `LessonAvatarVideoStatusOut` usato dai componenti media condivisi.
 * Entrambi i tipi BE sono strutturalmente assegnabili a questo.
 */
export interface MediaStatusItem {
  lesson_id: string;
  status: "empty" | "pending" | "processing" | "ready" | "failed" | "cancelled";
  progress: number;
  progress_phase: string | null;
  video_url: string | null;
  error: string | null;
  is_stale: boolean;
}

/**
 * Adapter di rendering forniti da ciascuna pagina (Video / Avatar): le parti
 * specifiche per variante (badge stato, avvisi, pulsanti azione, chip token,
 * label di fase, nome file di download) restano nella view che le possiede.
 */
export interface MediaRenderers<TItem extends MediaStatusItem> {
  statusBadge: (item: TItem) => ReactNode;
  warnings: (item: TItem) => ReactNode;
  actions: (lesson: CourseLessonOut, item: TItem) => ReactNode;
  tokens: (item: TItem) => ReactNode;
  phaseLabel: (item: TItem) => string;
  downloadName: (lesson: CourseLessonOut, item: TItem) => string;
}
