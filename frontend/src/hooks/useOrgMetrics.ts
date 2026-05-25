import { useQuery } from "@tanstack/react-query";

import { orgMetricsApi, type OrgMetricsOut } from "@/api/orgMetrics";

const REFETCH_MS = 60_000;
const STALE_MS = 30_000;

/**
 * Snapshot metriche org-scoped per la dashboard organizzazione.
 *
 * Niente cache backend (org-scoped, traffico già ridotto): qui usiamo
 * staleTime 30s / refetch 60s come compromesso ragionevole. Disabilitata
 * se `orgId` è vuoto.
 */
export function useOrgMetrics(orgId: string | null | undefined) {
  return useQuery<OrgMetricsOut>({
    queryKey: ["org-metrics", orgId ?? ""],
    queryFn: () => orgMetricsApi.get(orgId!),
    enabled: !!orgId,
    staleTime: STALE_MS,
    refetchInterval: REFETCH_MS,
    refetchOnWindowFocus: false,
  });
}
