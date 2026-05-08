import { useEffect, useRef, useState } from "react";

export interface TaskEtaResult {
  /** Tempo trascorso da quando il task è entrato in stato attivo (ms). */
  elapsedMs: number | null;
  /** Stima ms residui derivata dal progress %. Null se progress < 5%. */
  etaMs: number | null;
}

const STORAGE_PREFIX = "task_eta_started_at:";

/**
 * Stima ETA per un task SINGOLO (es. generazione architettura corso) quando
 * il backend non espone uno `started_at`.
 *
 * Persiste il timestamp di inizio in `sessionStorage` con chiave
 * `task_eta_started_at:{taskKey}` così sopravvive a refresh / navigation
 * tab finché la sessione browser è aperta. La pulizia avviene quando
 * `isActive` torna false (task completato/fallito).
 *
 * ETA ≈ elapsed * (100 - progress) / progress. Sotto progress=5% lo
 * stimatore è troppo rumoroso → ritorna etaMs=null e si mostra solo
 * `elapsedMs`.
 */
export function useTaskEta(
  taskKey: string,
  isActive: boolean,
  progress: number,
): TaskEtaResult {
  const [, setTick] = useState(0);
  const startedAtRef = useRef<number | null>(null);
  const storageKey = STORAGE_PREFIX + taskKey;

  // Lifecycle: piazza/legge/pulisce il timestamp in sessionStorage.
  useEffect(() => {
    if (!isActive) {
      sessionStorage.removeItem(storageKey);
      startedAtRef.current = null;
      return;
    }
    const existing = sessionStorage.getItem(storageKey);
    if (existing) {
      const n = parseInt(existing, 10);
      if (Number.isFinite(n)) {
        startedAtRef.current = n;
        setTick((t) => t + 1);
        return;
      }
    }
    const now = Date.now();
    sessionStorage.setItem(storageKey, String(now));
    startedAtRef.current = now;
    setTick((t) => t + 1);
  }, [isActive, storageKey]);

  // Tick periodico per re-render del display.
  useEffect(() => {
    if (!isActive) return;
    const id = setInterval(() => setTick((t) => t + 1), 5_000);
    return () => clearInterval(id);
  }, [isActive]);

  if (!isActive || startedAtRef.current === null) {
    return { elapsedMs: null, etaMs: null };
  }

  const elapsedMs = Date.now() - startedAtRef.current;
  let etaMs: number | null = null;
  if (progress >= 5 && progress < 100 && elapsedMs > 0) {
    etaMs = Math.round((elapsedMs / progress) * (100 - progress));
  }
  return { elapsedMs, etaMs };
}
