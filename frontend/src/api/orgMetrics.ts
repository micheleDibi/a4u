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

export interface AssigneeWorkload {
  user_id: string;
  name: string | null;
  course_count: number;
}

export interface RoleCount {
  role_code: string; // creator | org_admin | manager | member
  role_name_it: string | null;
  count: number;
}

export interface AvatarReadiness {
  total_assignees: number;
  ready: number;
  partial: number;
  not_ready: number;
}

export interface OrgCoursesMetrics {
  total: number;
  by_status: StatusCount[];
  by_assignee: AssigneeWorkload[]; // top 10
}

export interface OrgLessonsMetrics {
  total: number;
  phases: LessonsPhaseBreakdown;
}

export interface OrgMembersMetrics {
  total: number;
  by_role: RoleCount[];
  pending_invitations: number;
}

export interface OrgAuditRecentEntry {
  id: string;
  created_at: string;
  action: string;
  actor_user_name: string | null;
  target_type: string | null;
  target_id: string | null;
}

export interface OrgMetricsOut {
  generated_at: string;
  courses: OrgCoursesMetrics;
  lessons: OrgLessonsMetrics;
  modules_total: number;
  members: OrgMembersMetrics;
  avatar_readiness: AvatarReadiness;
  audit_recent: OrgAuditRecentEntry[];
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
