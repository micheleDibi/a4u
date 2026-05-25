import { useEffect, useState } from "react";

/**
 * Debounce di un valore reattivo: ritorna `value` solo dopo che è
 * rimasto stabile per `delayMs` ms. Utile per ricerche testuali e
 * filtri che colpiscono la rete.
 *
 * Esempio:
 *   const [q, setQ] = useState("");
 *   const debouncedQ = useDebouncedValue(q, 300);
 *   useQuery({ queryKey: ["search", debouncedQ], ... });
 */
export function useDebouncedValue<T>(value: T, delayMs: number = 300): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(id);
  }, [value, delayMs]);

  return debounced;
}
