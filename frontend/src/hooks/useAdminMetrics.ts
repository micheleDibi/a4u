import { useQuery } from "@tanstack/react-query";

import { adminMetricsApi, type AdminMetricsOut } from "@/api/adminMetrics";

const REFETCH_MS = 60_000; // backend cache TTL: refetch coerente con essa
const STALE_MS = 30_000;

/**
 * Snapshot metriche platform-wide per la dashboard admin.
 *
 * Backend cache TTL 60s → refetch ogni 60s, staleTime 30s.
 * Le query restano abilitate solo lato pagina admin (visibile solo a
 * `is_platform_admin=true`).
 */
export function useAdminMetrics() {
  return useQuery<AdminMetricsOut>({
    queryKey: ["admin-metrics"],
    queryFn: () => adminMetricsApi.get(),
    staleTime: STALE_MS,
    refetchInterval: REFETCH_MS,
    refetchOnWindowFocus: false,
  });
}
