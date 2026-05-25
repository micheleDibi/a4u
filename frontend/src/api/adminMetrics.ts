import { apiClient } from "./client";

// ---------------------------------------------------------------------------
// Tipi — mirror di `backend/app/schemas/admin_metrics.py`.
// ---------------------------------------------------------------------------

export interface StatusCount {
  status: string;
  count: number;
}

export interface UsersMetrics {
  total: number;
  active: number;
  active_last_30d: number;
}

export interface OrgsMetrics {
  total: number;
}

export interface CoursesMetrics {
  total: number;
  by_status: StatusCount[];
}

export interface LessonsPhaseBreakdown {
  content: StatusCount[];
  slides: StatusCount[];
  speech: StatusCount[];
  video: StatusCount[];
  avatar_video: StatusCount[];
}

export interface LessonsMetrics {
  total: number;
  phases: LessonsPhaseBreakdown;
}

export interface CostByPhase {
  /** architecture | structure | content | slides | speech */
  phase: string;
  cost_usd: number;
}

export interface CostMetrics {
  total_usd: number;
  last_7d_usd: number;
  last_30d_usd: number;
  by_phase: CostByPhase[];
}

export interface LoginDayMetric {
  date: string; // YYYY-MM-DD UTC
  success: number;
  failure: number;
}

export interface LoginActivityMetrics {
  last_7d: LoginDayMetric[];
  success_total_7d: number;
  failure_total_7d: number;
}

export interface AdminMetricsOut {
  generated_at: string;
  users: UsersMetrics;
  orgs: OrgsMetrics;
  courses: CoursesMetrics;
  lessons: LessonsMetrics;
  cost: CostMetrics;
  login_activity: LoginActivityMetrics;
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

export const adminMetricsApi = {
  /** Snapshot di metriche platform-wide. Cached lato server TTL 60s.
   *  Richiede `is_platform_admin=true`. */
  async get(): Promise<AdminMetricsOut> {
    const res = await apiClient.get<AdminMetricsOut>("/admin/metrics");
    return res.data;
  },
};
