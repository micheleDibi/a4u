import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/api/client";
import type { MembershipOut } from "@/api/types";

export type { MembershipOut };

/**
 * Lista i membri dell'organizzazione, ordinati per rank ruolo poi per nome.
 * Richiede `member:view` su backend (gate `require(P.MEMBER_VIEW)`).
 */
export function useOrgMembers(orgId: string | undefined) {
  return useQuery({
    queryKey: ["org-members", orgId],
    queryFn: async () => {
      const res = await apiClient.get<MembershipOut[]>(
        `/orgs/${orgId}/members`
      );
      return res.data;
    },
    enabled: !!orgId,
    staleTime: 60 * 1000,
  });
}
