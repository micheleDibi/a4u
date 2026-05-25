import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

// Contesto globale del widget Nova. Le pagine annunciano la pagina
// corrente e i campi "salient" (filtri attivi, titolo bozza, status,
// ecc.) via hook `useSetNovaContext({...})`. Quando l'utente apre il
// widget e manda un messaggio, questi dati vengono inviati al BE come
// `context: NovaPageContext` insieme al messaggio.
//
// Niente persistenza: lo stato del context vive solo in memoria per la
// durata della sessione browser, e viene resettato quando la pagina
// che l'aveva annunciato si smonta (cleanup nel useEffect del hook).

export interface NovaPageContext {
  /** Identificatore stringa della pagina (es. "courses.list",
   *  "course.editor"). Convenzione: namespace puntato. */
  page: string;
  /** Campi "salient" della UI corrente, serializzati a JSON dal BE.
   *  Esempi: `{ filterStatus: "draft", searchQuery: "matematica" }`
   *  o `{ courseId, currentTab, title, status }`. */
  fields: Record<string, unknown>;
  /** Organization corrente (opzionale, derivata dall'URL). */
  orgId?: string;
}

interface NovaContextValue {
  context: NovaPageContext | null;
  setContext: (ctx: NovaPageContext | null) => void;
}

const NovaContext = createContext<NovaContextValue | null>(null);

export function NovaContextProvider({ children }: { children: ReactNode }) {
  const [context, setContextState] = useState<NovaPageContext | null>(null);
  const setContext = useCallback((ctx: NovaPageContext | null) => {
    setContextState(ctx);
  }, []);
  const value = useMemo(() => ({ context, setContext }), [context, setContext]);
  return <NovaContext.Provider value={value}>{children}</NovaContext.Provider>;
}

/**
 * Hook usato dalle pagine per annunciare il contesto corrente a Nova.
 * Effect cleanup automatico su unmount: se l'utente cambia pagina, il
 * contesto torna a `null` finché la nuova pagina non lo richiama.
 *
 * Esempio:
 * ```tsx
 * useSetNovaContext({
 *   page: "courses.list",
 *   fields: { filterStatus: status, searchQuery: q },
 *   orgId,
 * });
 * ```
 *
 * Re-render: il setter viene chiamato ogni volta che `ctx` cambia
 * strutturalmente (JSON-string comparison) — niente loop infiniti.
 */
export function useSetNovaContext(ctx: NovaPageContext): void {
  const nova = useContext(NovaContext);
  // Stable string key: re-run dell'effect solo se ctx cambia davvero.
  const ctxKey = JSON.stringify(ctx);
  useEffect(() => {
    if (!nova) return;
    nova.setContext(ctx);
    return () => {
      nova.setContext(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ctxKey]);
}

/**
 * Hook per leggere il contesto corrente. Usato dal widget per
 * costruire il payload prima di inviare al BE.
 */
export function useNovaContext(): NovaPageContext | null {
  const nova = useContext(NovaContext);
  return nova?.context ?? null;
}
