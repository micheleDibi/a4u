import { apiClient } from "./client";
import type {
  LessonsPhaseBreakdown,
  StatusCount,
} from "./adminMetrics";

// ---------------------------------------------------------------------------
// Tipi — mirror di `backend/app/schemas/org_metrics.py`.
// Niente campi `cost_usd` / token totals: i costi AI non sono mai esposti
// nelle viste org-scoped (scelta di prodotto).
// ---------------------------------------------------------------------------

export interface OrgCoursesMetrics {
  total: number;
  by_status: StatusCount[];
}

export interface OrgLessonsMetrics {
  total: number;
  phases: LessonsPhaseBreakdown;
}

export interface OrgMembersMetrics {
  total: number;
  pending_invitations: number;
}

export interface OrgMetricsOut {
  generated_at: string;
  courses: OrgCoursesMetrics;
  lessons: OrgLessonsMetrics;
  members: OrgMembersMetrics;
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

export const orgMetricsApi = {
  /** Snapshot metriche org-scoped per la dashboard organizzazione.
   *  Gate backend: `course:view`. */
  async get(orgId: string): Promise<OrgMetricsOut> {
    const res = await apiClient.get<OrgMetricsOut>(
      `/orgs/${orgId}/metrics`,
    );
    return res.data;
  },
};
