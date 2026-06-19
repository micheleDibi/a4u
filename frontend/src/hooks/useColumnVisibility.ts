import { useCallback, useState } from "react";
import type { VisibilityState } from "@tanstack/react-table";

/**
 * Visibilità colonne di una tabella, persistita in localStorage per-browser.
 * Modellato sul pattern di `useMediaView` (lazy init + try/catch).
 *
 * Lo stato salvato viene unito sopra ai `defaults` (`{...defaults, ...saved}`):
 * così se in futuro si aggiunge una colonna, questa eredita il suo default
 * finché l'utente non la tocca, senza rompere la preferenza già salvata.
 */
export function useColumnVisibility(
  storageKey: string,
  defaults: VisibilityState,
) {
  const [columnVisibility, setState] = useState<VisibilityState>(() => {
    try {
      const saved = localStorage.getItem(storageKey);
      if (saved) {
        const parsed = JSON.parse(saved) as VisibilityState;
        return { ...defaults, ...parsed };
      }
    } catch {
      // ignore
    }
    return defaults;
  });

  const setColumnVisibility = useCallback(
    (next: VisibilityState) => {
      setState(next);
      try {
        localStorage.setItem(storageKey, JSON.stringify(next));
      } catch {
        // ignore
      }
    },
    [storageKey],
  );

  return { columnVisibility, setColumnVisibility };
}
