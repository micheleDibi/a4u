import { useEffect, useState } from "react";

export interface BatchEtaTask {
  /** Status del task; viene confrontato con i set sotto. */
  status: string;
  /** ISO string del momento di completamento (`*_generated_at`), o null. */
  completedAt: string | null;
}

export interface BatchEtaResult {
  /** Task completati (status ∈ COMPLETED_STATUSES). */
  completed: number;
  /** Task in corso (status ∈ ACTIVE_STATUSES). */
  active: number;
  /** completed + active. Esclude empty/failed (non parte del batch corrente). */
  total: number;
  /** Quanti restano da completare. */
  remaining: number;
  /** Tempo medio per task in ms, basato sui completati nel recent window. */
  avgPerTaskMs: number | null;
  /** Stima ms residui per il batch; null se non calcolabile. */
  etaMs: number | null;
}

const COMPLETED_STATUSES = new Set(["ready", "approved"]);
const ACTIVE_STATUSES = new Set(["pending", "processing"]);

/** Finestra temporale entro cui consideriamo i timestamp di completamento
 *  "appartenenti al batch corrente". Senza questo, se ci sono task ready
 *  da una sessione precedente (giorni fa) la velocità calcolata sarebbe
 *  artificialmente bassa. 90 minuti è un buon compromesso. */
const RECENT_WINDOW_MS = 90 * 60_000;

/**
 * Calcola un'ETA approssimato per un batch di task in lavorazione,
 * derivando la velocità dai timestamp di completamento dei task già
 * pronti (`*_generated_at`).
 *
 * Robusto al refresh della pagina (i timestamp arrivano dal backend).
 * Auto-ricalibra man mano che nuovi task si completano. Re-renderizza
 * ogni 5 secondi così il countdown scorre anche senza polling nuovo.
 */
export function useBatchEta(tasks: BatchEtaTask[]): BatchEtaResult {
  // Tick periodico per re-render del display (ETA decrescente, "fa N min").
  const [, force] = useState(0);
  useEffect(() => {
    const id = setInterval(() => force((n) => n + 1), 5_000);
    return () => clearInterval(id);
  }, []);

  let completed = 0;
  let active = 0;
  const completedTimes: number[] = [];
  const now = Date.now();

  for (const t of tasks) {
    if (COMPLETED_STATUSES.has(t.status)) {
      completed += 1;
      if (t.completedAt) {
        const ts = new Date(t.completedAt).getTime();
        if (Number.isFinite(ts) && now - ts < RECENT_WINDOW_MS) {
          completedTimes.push(ts);
        }
      }
    } else if (ACTIVE_STATUSES.has(t.status)) {
      active += 1;
    }
  }

  const total = completed + active;
  const remaining = active;

  let avgPerTaskMs: number | null = null;
  if (completedTimes.length >= 2) {
    completedTimes.sort((a, b) => a - b);
    const span = completedTimes[completedTimes.length - 1] - completedTimes[0];
    avgPerTaskMs = span / (completedTimes.length - 1);
  }

  const etaMs =
    avgPerTaskMs !== null && remaining > 0 && avgPerTaskMs > 0
      ? Math.round(avgPerTaskMs * remaining)
      : null;

  return { completed, active, total, remaining, avgPerTaskMs, etaMs };
}
