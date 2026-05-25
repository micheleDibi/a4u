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
import { ActivityList } from "@/components/dashboard/ActivityList";
import { DonutMini } from "@/components/dashboard/DonutMini";
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
import {
  COURSE_BUCKET_COLORS,
  COURSE_MACRO_ORDER,
  type CourseMacroBucket,
  courseBucketFor,
  sortByLifecycleOrder,
  statusColor,
} from "@/lib/statusColors";

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

/**
 * Aggrega i 17 status raw di `course.status` nelle 8 macro-fasi (vedi
 * `statusColors.COURSE_MACRO_ORDER`). Restituisce solo i bucket con count > 0
 * mantenendo l'ordine canonico.
 */
function bucketize(
  raw: StatusCount[],
): { bucket: CourseMacroBucket; count: number }[] {
  const counts = new Map<CourseMacroBucket, number>();
  for (const item of raw) {
    const b = courseBucketFor(item.status);
    if (!b) continue;
    counts.set(b, (counts.get(b) ?? 0) + item.count);
  }
  return COURSE_MACRO_ORDER.map((b) => ({
    bucket: b,
    count: counts.get(b) ?? 0,
  })).filter((x) => x.count > 0);
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

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">
              {t("dashboard.admin.pipelineCourses.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <StatusBarChart
              emptyLabel={t("dashboard.admin.pipelineCourses.empty")}
              items={
                data
                  ? bucketize(data.courses.by_status).map(
                      ({ bucket, count }) => ({
                        key: bucket,
                        label: t(
                          `dashboard.shared.statusBucket.${bucket}`,
                        ),
                        count,
                        color: COURSE_BUCKET_COLORS[bucket].bg,
                      }),
                    )
                  : []
              }
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">
              {t("dashboard.admin.pipelineLessons.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 pt-0">
            {PHASE_KEYS.map(({ key, field }) => (
              <div key={key} className="space-y-1">
                <div className="text-xs uppercase tracking-wide text-muted-foreground">
                  {t(`dashboard.admin.pipelineLessons.${key}`)}
                </div>
                <StatusBarChart
                  compact
                  emptyLabel={t("dashboard.admin.pipelineLessons.empty")}
                  items={
                    data ? lifecycleItems(data.lessons.phases[field], t) : []
                  }
                />
              </div>
            ))}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader className="pb-3">
            <CardTitle className="text-base">
              {t("dashboard.admin.cost.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4 pt-0">
            <div className="grid gap-3 sm:grid-cols-3">
              <Stat
                label={t("dashboard.admin.cost.total")}
                value={formatUsd(data?.cost.total_usd ?? 0)}
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
              <div className="mb-2 text-xs uppercase tracking-wide text-muted-foreground">
                {t("dashboard.admin.cost.byPhase")}
              </div>
              <ul className="space-y-1.5 text-sm">
                {(data?.cost.by_phase ?? []).map((p) => (
                  <li key={p.phase} className="flex items-center gap-2">
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
              {t("dashboard.admin.avatarClips.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <DonutMini
              items={(data?.avatar_clips.by_status ?? []).map((s) => ({
                key: s.status,
                label: t(`dashboard.shared.lifecycle.${s.status}`),
                count: s.count,
                color: statusColor(s.status).bg,
                hex: statusColor(s.status).hex,
              }))}
            />
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">
              {t("dashboard.admin.loginActivity.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <LoginActivityChart data={data?.login_activity.last_7d ?? []} />
            <div className="mt-3 flex gap-4 text-xs">
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

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">
              {t("dashboard.admin.auditRecent.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <ActivityList
              items={data?.audit_recent ?? []}
              emptyLabel={t("dashboard.admin.auditRecent.empty")}
            />
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

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="space-y-1">
      <div className="text-xs uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className="text-lg font-semibold tabular-nums">{value}</div>
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
        return (
          <div key={d.date} className="flex flex-1 flex-col items-stretch">
            <div className="flex flex-1 flex-col justify-end">
              <div
                className="w-full"
                style={{ height: `${totalRatio * 100}%` }}
              >
                <div className="flex h-full flex-col">
                  <div
                    className="bg-red-500"
                    style={{ flex: d.failure || 0 }}
                    title={`${d.failure}`}
                  />
                  <div
                    className="bg-emerald-500"
                    style={{ flex: d.success || 0 }}
                    title={`${d.success}`}
                  />
                </div>
              </div>
            </div>
            <div className="mt-1 text-center text-[10px] tabular-nums text-muted-foreground">
              {d.date.slice(5)}
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
              <Card className="h-full transition-colors group-hover:border-foreground/20">
                <CardContent className="flex flex-col gap-3 p-6">
                  <div className="grid size-9 place-items-center rounded-md bg-muted text-foreground">
                    <Icon className="size-4" />
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
