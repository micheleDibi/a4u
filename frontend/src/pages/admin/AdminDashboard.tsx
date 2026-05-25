import {
  BookOpen,
  Building2,
  DollarSign,
  GraduationCap,
  LockKeyhole,
  UserCheck,
  Users,
  UsersRound,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import type {
  AdminMetricsOut,
  LessonsPhaseBreakdown,
  LoginDayMetric,
  StatusCount,
} from "@/api/adminMetrics";
import { CoursePipelineDetail } from "@/components/dashboard/CoursePipelineDetail";
import { KpiCard } from "@/components/dashboard/KpiCard";
import {
  StatusBarChart,
  type StatusBarItem,
} from "@/components/dashboard/StatusBarChart";
import { PageHeader } from "@/components/layout/PageHeader";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useAdminMetrics } from "@/hooks/useAdminMetrics";
import { sortByLifecycleOrder, statusColor } from "@/lib/statusColors";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatUsd(n: number): string {
  if (n === 0) return "$0";
  if (n < 0.01) return "<$0.01";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: n < 1 ? 4 : 2,
  }).format(n);
}

function formatInt(n: number | undefined): string {
  return (n ?? 0).toLocaleString();
}

function lifecycleItems(
  raw: StatusCount[],
  t: (k: string) => string,
): StatusBarItem[] {
  return sortByLifecycleOrder(raw).map((s) => ({
    key: s.status,
    label: t(`dashboard.shared.lifecycle.${s.status}`),
    count: s.count,
    color: statusColor(s.status).bg,
  }));
}

const PHASE_KEYS: {
  key: string;
  field: keyof LessonsPhaseBreakdown;
}[] = [
  { key: "content", field: "content" },
  { key: "slides", field: "slides" },
  { key: "speech", field: "speech" },
  { key: "video", field: "video" },
  { key: "avatarVideo", field: "avatar_video" },
];

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function AdminDashboard() {
  const { t } = useTranslation();
  const { data } = useAdminMetrics();

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("dashboard.admin.title")}
        description={t("dashboard.admin.subtitle")}
      />

      <KpiStrip data={data} />

      {/* Pipeline corsi — tutti i 17 stati raggruppati per fase */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">
            {t("dashboard.admin.pipelineCourses.title")}
          </CardTitle>
          <CardDescription>
            {t("dashboard.admin.pipelineCourses.subtitle")}
          </CardDescription>
        </CardHeader>
        <CardContent className="pt-0">
          <CoursePipelineDetail
            items={data?.courses.by_status ?? []}
            total={data?.courses.total ?? 0}
            emptyLabel={t("dashboard.admin.pipelineCourses.empty")}
          />
        </CardContent>
      </Card>

      {/* Pipeline lezioni */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">
            {t("dashboard.admin.pipelineLessons.title")}
          </CardTitle>
          <CardDescription>
            {t("dashboard.admin.pipelineLessons.subtitle", {
              total: formatInt(data?.lessons.total),
            })}
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 pt-0 md:grid-cols-2 xl:grid-cols-5">
          {PHASE_KEYS.map(({ key, field }) => {
            const items = data ? data.lessons.phases[field] : [];
            const phaseTotal = items.reduce((s, i) => s + i.count, 0);
            return (
              <div
                key={key}
                className="space-y-2 rounded-lg border p-3 transition-colors hover:border-foreground/15"
              >
                <div className="flex items-baseline justify-between">
                  <div className="truncate text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                    {t(`dashboard.admin.pipelineLessons.${key}`)}
                  </div>
                  <div className="text-sm font-semibold tabular-nums">
                    {phaseTotal}
                  </div>
                </div>
                <StatusBarChart
                  compact
                  emptyLabel={t("dashboard.admin.pipelineLessons.empty")}
                  items={lifecycleItems(items, t)}
                />
              </div>
            );
          })}
        </CardContent>
      </Card>

      {/* Costo AI + Login activity */}
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader className="pb-3">
            <CardTitle className="text-base">
              {t("dashboard.admin.cost.title")}
            </CardTitle>
            <CardDescription>
              {t("dashboard.admin.cost.subtitle")}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5 pt-0">
            <div className="grid gap-4 sm:grid-cols-3">
              <Stat
                label={t("dashboard.admin.cost.total")}
                value={formatUsd(data?.cost.total_usd ?? 0)}
                emphasize
              />
              <Stat
                label={t("dashboard.admin.cost.last7d")}
                value={formatUsd(data?.cost.last_7d_usd ?? 0)}
              />
              <Stat
                label={t("dashboard.admin.cost.last30d")}
                value={formatUsd(data?.cost.last_30d_usd ?? 0)}
              />
            </div>
            <div>
              <div className="mb-2 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                {t("dashboard.admin.cost.byPhase")}
              </div>
              <ul className="space-y-0.5">
                {(data?.cost.by_phase ?? []).map((p) => (
                  <li
                    key={p.phase}
                    className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors hover:bg-muted/50"
                  >
                    <span className="truncate">
                      {t(`dashboard.admin.cost.phaseLabel.${p.phase}`)}
                    </span>
                    <span className="ms-auto font-medium tabular-nums">
                      {formatUsd(p.cost_usd)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">
              {t("dashboard.admin.loginActivity.title")}
            </CardTitle>
            <CardDescription>
              {t("dashboard.admin.loginActivity.subtitle")}
            </CardDescription>
          </CardHeader>
          <CardContent className="pt-0">
            <LoginActivityChart data={data?.login_activity.last_7d ?? []} />
            <div className="mt-4 flex flex-wrap gap-x-4 gap-y-1 text-xs">
              <Legend
                color="bg-emerald-500"
                label={t("dashboard.admin.loginActivity.success")}
                count={data?.login_activity.success_total_7d ?? 0}
              />
              <Legend
                color="bg-red-500"
                label={t("dashboard.admin.loginActivity.failure")}
                count={data?.login_activity.failure_total_7d ?? 0}
              />
            </div>
          </CardContent>
        </Card>
      </div>

      <ManageSection />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Inline subcomponents
// ---------------------------------------------------------------------------

function KpiStrip({ data }: { data: AdminMetricsOut | undefined }) {
  const { t } = useTranslation();
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
      <KpiCard
        icon={Users}
        label={t("dashboard.admin.kpi.users")}
        value={formatInt(data?.users.total)}
        sublabel={
          data
            ? t("dashboard.admin.kpi.usersActiveSublabel", {
                active: data.users.active,
              })
            : undefined
        }
      />
      <KpiCard
        icon={UserCheck}
        label={t("dashboard.admin.kpi.usersActive30d")}
        value={formatInt(data?.users.active_last_30d)}
      />
      <KpiCard
        icon={Building2}
        label={t("dashboard.admin.kpi.orgs")}
        value={formatInt(data?.orgs.total)}
      />
      <KpiCard
        icon={GraduationCap}
        label={t("dashboard.admin.kpi.courses")}
        value={formatInt(data?.courses.total)}
      />
      <KpiCard
        icon={BookOpen}
        label={t("dashboard.admin.kpi.lessons")}
        value={formatInt(data?.lessons.total)}
      />
      <KpiCard
        icon={DollarSign}
        label={t("dashboard.admin.kpi.costAi")}
        value={formatUsd(data?.cost.total_usd ?? 0)}
        sublabel={
          data
            ? t("dashboard.admin.kpi.costAiSublabel", {
                last30d: formatUsd(data.cost.last_30d_usd),
              })
            : undefined
        }
      />
    </div>
  );
}

function Stat({
  label,
  value,
  emphasize = false,
}: {
  label: string;
  value: string;
  emphasize?: boolean;
}) {
  return (
    <div className="space-y-1">
      <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div
        className={
          emphasize
            ? "text-2xl font-semibold tabular-nums"
            : "text-lg font-semibold tabular-nums"
        }
      >
        {value}
      </div>
    </div>
  );
}

function Legend({
  color,
  label,
  count,
}: {
  color: string;
  label: string;
  count: number;
}) {
  return (
    <div className="flex items-center gap-1.5">
      <span className={`inline-block size-2 rounded-full ${color}`} />
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium tabular-nums">{count}</span>
    </div>
  );
}

function LoginActivityChart({ data }: { data: LoginDayMetric[] }) {
  const { i18n } = useTranslation();
  if (data.length === 0) {
    return (
      <div className="py-6 text-center text-sm text-muted-foreground">—</div>
    );
  }
  const max = Math.max(1, ...data.map((d) => d.success + d.failure));
  return (
    <div className="flex h-32 gap-1.5">
      {data.map((d) => {
        const totalRatio = (d.success + d.failure) / max;
        const dayName = new Date(d.date + "T00:00:00Z").toLocaleDateString(
          i18n.language,
          { weekday: "short" },
        );
        return (
          <div key={d.date} className="flex flex-1 flex-col items-stretch">
            <div className="flex flex-1 flex-col justify-end">
              <div
                className="w-full transition-all duration-500"
                style={{ height: `${totalRatio * 100}%` }}
              >
                <div className="flex h-full flex-col">
                  <div
                    className="bg-red-500"
                    style={{ flex: d.failure || 0 }}
                    title={`${d.date}: ${d.failure}`}
                  />
                  <div
                    className="bg-emerald-500"
                    style={{ flex: d.success || 0 }}
                    title={`${d.date}: ${d.success}`}
                  />
                </div>
              </div>
            </div>
            <div className="mt-1.5 text-center text-[10px] capitalize tabular-nums text-muted-foreground">
              {dayName}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ManageSection() {
  const { t } = useTranslation();
  const cards = [
    {
      to: "/admin/organizations",
      title: t("nav.organizations"),
      description: t("organizations.subtitle"),
      icon: Building2,
    },
    {
      to: "/admin/users",
      title: t("nav.users"),
      description: t("users.subtitle"),
      icon: UsersRound,
    },
    {
      to: "/admin/permissions",
      title: t("nav.permissions"),
      description: t("globalPermissions.subtitle"),
      icon: LockKeyhole,
    },
  ];
  return (
    <div className="space-y-3">
      <div>
        <h2 className="text-base font-medium">
          {t("dashboard.admin.manage.title")}
        </h2>
        <p className="text-sm text-muted-foreground">
          {t("dashboard.admin.manage.subtitle")}
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {cards.map((c) => {
          const Icon = c.icon;
          return (
            <Link key={c.to} to={c.to} className="group">
              <Card className="h-full transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md hover:border-foreground/15">
                <CardContent className="flex flex-col gap-3 p-6">
                  <div className="grid size-10 place-items-center rounded-lg bg-primary/10 text-primary transition-colors">
                    <Icon className="size-5" />
                  </div>
                  <div>
                    <CardTitle className="text-base">{c.title}</CardTitle>
                    <CardDescription className="mt-1 text-sm">
                      {c.description}
                    </CardDescription>
                  </div>
                </CardContent>
              </Card>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
