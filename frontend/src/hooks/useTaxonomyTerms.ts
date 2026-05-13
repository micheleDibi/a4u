import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { courseTaxonomyPublicApi } from "@/api/courses";
import type { TaxonomyType } from "@/api/courseTaxonomy";

const STALE_MS = 5 * 60 * 1000;

const keyFor = (type: TaxonomyType) =>
  ["course-taxonomy", "public", type, "active"] as const;

/**
 * Lista i term ATTIVI di una tassonomia. Utilizzato dai Select del form
 * di creazione/modifica corso. Riusa l'endpoint pubblico `/course-taxonomy/{type}`
 * disponibile a tutti gli utenti autenticati.
 */
export function useTaxonomyTerms(type: TaxonomyType) {
  return useQuery({
    queryKey: keyFor(type),
    queryFn: () => courseTaxonomyPublicApi.listActive(type),
    staleTime: STALE_MS,
  });
}

/**
 * Pre-carica più tassonomie in UNA sola richiesta HTTP via
 * `GET /course-taxonomy/bulk?types=...` e popola la cache TanStack con
 * lo stesso `queryKey` di `useTaxonomyTerms(type)`. Le successive
 * `useTaxonomyTerms` leggono dalla cache senza fare network.
 *
 * Pensato per pagine come `CourseEditorPage` che hanno 7-8
 * `<TaxonomyTermSelect>` montati contemporaneamente: invocando
 * `useTaxonomyTermsBulk(TYPES)` come primissimo hook nel componente,
 * tutte le query downstream sono hit di cache.
 */
export function useTaxonomyTermsBulk(types: readonly TaxonomyType[]) {
  const qc = useQueryClient();
  // queryKey stabile sull'array ordinato per evitare refetch su riordini
  // accidentali del caller.
  const sortedKey = [...types].sort();
  const query = useQuery({
    queryKey: ["course-taxonomy", "public", "bulk", sortedKey],
    queryFn: () => courseTaxonomyPublicApi.listActiveBulk(sortedKey),
    staleTime: STALE_MS,
    enabled: sortedKey.length > 0,
  });

  // Sincronizza i risultati nella cache per-tipo, così
  // `useTaxonomyTerms(t)` chiamato dai child Select hit-a la cache.
  useEffect(() => {
    if (!query.data) return;
    for (const t of sortedKey) {
      const data = query.data[t];
      if (data) {
        qc.setQueryData(keyFor(t), data);
      }
    }
    // sortedKey è ricostruito ogni render ma il contenuto è stabile.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query.data]);

  return query;
}
