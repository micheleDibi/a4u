import { useQuery } from "@tanstack/react-query";
import { courseTaxonomyPublicApi } from "@/api/courses";
import type { TaxonomyType } from "@/api/courseTaxonomy";

/**
 * Lista i term ATTIVI di una tassonomia. Utilizzato dai Select del form
 * di creazione/modifica corso. Riusa l'endpoint pubblico `/course-taxonomy/{type}`
 * disponibile a tutti gli utenti autenticati.
 */
export function useTaxonomyTerms(type: TaxonomyType) {
  return useQuery({
    queryKey: ["course-taxonomy", "public", type, "active"],
    queryFn: () => courseTaxonomyPublicApi.listActive(type),
    staleTime: 5 * 60 * 1000,
  });
}
