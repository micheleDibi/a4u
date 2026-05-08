import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/api/client";

export interface MembershipOut {
  id: string;
  user_id: string;
  user_email: string;
  user_full_name: string;
  organization_id: string;
  role_id: string;
  role_code: string;
  role_name_it: string;
  joined_at: string;
}

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
