import { useEffect } from "react";
import { useParams } from "react-router-dom";

const LAST_ORG_KEY = "a4u.lastOrgId";

/**
 * Ritorna l'organizzazione "in contesto" anche su rotte che non includono
 * `:orgId` (es. `/me/avatar`, `/admin/...`). Persiste in localStorage l'ultima
 * org visitata in cui l'URL aveva `:orgId`, e fa fallback su quella quando
 * non è presente nei params.
 */
export function useEffectiveOrgId(): string | undefined {
  const { orgId } = useParams();

  useEffect(() => {
    if (orgId) {
      try {
        window.localStorage.setItem(LAST_ORG_KEY, orgId);
      } catch {
        /* ignore */
      }
    }
  }, [orgId]);

  if (orgId) return orgId;
  try {
    return window.localStorage.getItem(LAST_ORG_KEY) ?? undefined;
  } catch {
    return undefined;
  }
}
