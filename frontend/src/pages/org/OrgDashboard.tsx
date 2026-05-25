import {
  BookOpen,
  GraduationCap,
  Mail,
  Presentation,
  ScrollText,
  Smile,
  UserCheck,
  Users,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { organizationsApi } from "@/api/organizations";
import type {
  AssigneeWorkload,
  AvatarReadiness as AvatarReadinessData,
  OrgMetricsOut,
} from "@/api/orgMetrics";
import { useAuth } from "@/auth/AuthContext";
import { useHasPermission } from "@/auth/PermissionGate";
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
import { useOrgMetrics } from "@/hooks/useOrgMetrics";
import { P } from "@/lib/permissions";
import {
  COURSE_BUCKET_COLORS,
  COURSE_MACRO_ORDER,
  type CourseMacroBucket,
  courseBucketFor,
  sortByLifecycleOrder,
  statusColor,
} from "@/lib/statusColors";

// ---------------------------------------------------------------------------
// Palette ruoli (locale al dominio org dashboard).
// ---------------------------------------------------------------------------

const ROLE_COLOR: Record<string, { bg: string; hex: string }> = {
  creator: { bg: "bg-emerald-600", hex: "#059669" },
  org_admin: { bg: "bg-blue-500", hex: "#3b82f6" },
  manager: { bg: "bg-violet-500", hex: "#8b5cf6" },
  member: { bg: "bg-zinc-500", hex: "#71717a" },
};
const ROLE_FALLBACK = { bg: "bg-zinc-400", hex: "#a1a1aa" };

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function bucketize(
  raw: { status: string; count: number }[],
): { bucket: CourseMacroBucket; count: number }[] {
  const counts = new Map<CourseMacroBucket, number>();
  for (const it of raw) {
    const b = courseBucketFor(it.status);
    if (!b) continue;
    counts.set(b, (counts.get(b) ?? 0) + it.count);
  }
  return COURSE_MACRO_ORDER.map((b) => ({
    bucket: b,
    count: counts.get(b) ?? 0,
  })).filter((x) => x.count > 0);
}

function lifecycleItems(
  raw: { status: string; count: number }[],
  t: (k: string) => string,
): StatusBarItem[] {
  return sortByLifecycleOrder(raw).map((s) => ({
    key: s.status,
    label: t(`dashboard.shared.lifecycle.${s.status}`),
    count: s.count,
    color: statusColor(s.status).bg,
  }));
}

function pickCount(
  raw: { status: string; count: number }[] | undefined,
  status: string,
): number {
  return raw?.find((s) => s.status === status)?.count ?? 0;
}

const PHASE_KEYS = [
  { key: "content", field: "content" },
  { key: "slides", field: "slides" },
  { key: "speech", field: "speech" },
  { key: "video", field: "video" },
  { key: "avatarVideo", field: "avatar_video" },
] as const;

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function OrgDashboard() {
  const { orgId = "" } = useParams();
  const { me } = useAuth();
  const { t } = useTranslation();

  // Nome org: dalla membership corrente; fallback per platform admin che
  // visita un'org senza essere membro (riusa la query già esistente).
  const membership = me?.organizations.find(
    (o) => o.organization_id === orgId,
  );
  const fallbackQuery = useQuery({
    queryKey: ["admin-org", orgId],
    queryFn: () => organizationsApi.get(orgId),
    enabled: !!orgId && !membership && !!me?.is_platform_admin,
  });
  const orgName =
    membership?.organization_name ?? fallbackQuery.data?.name ?? "—";
  const roleLabel =
    membership?.role_name_it ?? t("dashboard.org.platformAdmin");

  const { data } = useOrgMetrics(orgId);

  return (
    <div className="space-y-6">
      <PageHeader
        title={orgName}
        description={t("dashboard.org.subtitle", { role: roleLabel })}
      />

      <KpiStrip data={data} />

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">
              {t("dashboard.org.pipelineCourses.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <StatusBarChart
              emptyLabel={t("dashboard.org.pipelineCourses.empty")}
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
              {t("dashboard.org.pipelineLessons.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 pt-0">
            {PHASE_KEYS.map(({ key, field }) => (
              <div key={key} className="space-y-1">
                <div className="text-xs uppercase tracking-wide text-muted-foreground">
                  {t(`dashboard.org.pipelineLessons.${key}`)}
                </div>
                <StatusBarChart
                  compact
                  emptyLabel={t("dashboard.org.pipelineLessons.empty")}
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
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">
              {t("dashboard.org.workload.title")}
            </CardTitle>
            <CardDescription>
              {t("dashboard.org.workload.subtitle")}
            </CardDescription>
          </CardHeader>
          <CardContent className="pt-0">
            <WorkloadList
              items={data?.courses.by_assignee ?? []}
              emptyLabel={t("dashboard.org.workload.empty")}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">
              {t("dashboard.org.avatarReadiness.title")}
            </CardTitle>
            <CardDescription>
              {t("dashboard.org.avatarReadiness.subtitle")}
            </CardDescription>
          </CardHeader>
          <CardContent className="pt-0">
            <AvatarReadinessWidget data={data?.avatar_readiness} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">
              {t("dashboard.org.members.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-0 space-y-3">
            <StatusBarChart
              emptyLabel={t("dashboard.org.members.empty")}
              items={
                data
                  ? data.members.by_role.map((r) => ({
                      key: r.role_code,
                      label: r.role_name_it ?? r.role_code,
                      count: r.count,
                      color: (ROLE_COLOR[r.role_code] ?? ROLE_FALLBACK).bg,
                    }))
                  : []
              }
            />
            {data && data.members.pending_invitations > 0 && (
              <div className="flex items-center gap-2 rounded-md bg-amber-50 px-3 py-2 text-xs dark:bg-amber-950/40">
                <Mail className="size-3.5 text-amber-700 dark:text-amber-400" />
                <span className="text-amber-900 dark:text-amber-300">
                  {t("dashboard.org.members.pendingInvitations", {
                    count: data.members.pending_invitations,
                  })}
                </span>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">
            {t("dashboard.org.auditRecent.title")}
          </CardTitle>
        </CardHeader>
        <CardContent className="pt-0">
          <ActivityList
            items={data?.audit_recent ?? []}
            emptyLabel={t("dashboard.org.auditRecent.empty")}
          />
        </CardContent>
      </Card>

      <ManageSection orgId={orgId} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Inline subcomponents
// ---------------------------------------------------------------------------

function KpiStrip({ data }: { data: OrgMetricsOut | undefined }) {
  const { t } = useTranslation();
  const fmt = (n: number | undefined) => (n ?? 0).toLocaleString();
  const coursesPublished = pickCount(data?.courses.by_status, "published");
  const lessonsVideoReady = pickCount(data?.lessons.phases.video, "ready");

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
      <KpiCard
        icon={GraduationCap}
        label={t("dashboard.org.kpi.coursesTotal")}
        value={fmt(data?.courses.total)}
      />
      <KpiCard
        icon={GraduationCap}
        label={t("dashboard.org.kpi.coursesPublished")}
        value={fmt(coursesPublished)}
      />
      <KpiCard
        icon={BookOpen}
        label={t("dashboard.org.kpi.lessonsTotal")}
        value={fmt(data?.lessons.total)}
      />
      <KpiCard
        icon={Smile}
        label={t("dashboard.org.kpi.lessonsVideoReady")}
        value={fmt(lessonsVideoReady)}
      />
      <KpiCard
        icon={Users}
        label={t("dashboard.org.kpi.members")}
        value={fmt(data?.members.total)}
      />
      <KpiCard
        icon={UserCheck}
        label={t("dashboard.org.kpi.pendingInvitations")}
        value={fmt(data?.members.pending_invitations)}
        tone={
          data && data.members.pending_invitations > 0 ? "default" : "muted"
        }
      />
    </div>
  );
}

function WorkloadList({
  items,
  emptyLabel,
}: {
  items: AssigneeWorkload[];
  emptyLabel: string;
}) {
  if (items.length === 0) {
    return (
      <div className="py-6 text-center text-sm text-muted-foreground">
        {emptyLabel}
      </div>
    );
  }
  const max = Math.max(1, ...items.map((i) => i.course_count));
  return (
    <ul className="space-y-2">
      {items.map((it) => (
        <li key={it.user_id} className="space-y-1">
          <div className="flex items-center text-sm">
            <span className="truncate">{it.name ?? "—"}</span>
            <span className="ms-auto font-medium tabular-nums">
              {it.course_count}
            </span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
            <div
              className="h-full bg-blue-500"
              style={{ width: `${(it.course_count / max) * 100}%` }}
            />
          </div>
        </li>
      ))}
    </ul>
  );
}

function AvatarReadinessWidget({
  data,
}: {
  data: AvatarReadinessData | undefined;
}) {
  const { t } = useTranslation();
  if (!data || data.total_assignees === 0) {
    return (
      <div className="py-6 text-center text-sm text-muted-foreground">
        {t("dashboard.org.avatarReadiness.empty")}
      </div>
    );
  }
  return (
    <DonutMini
      centerLabel={t("dashboard.org.avatarReadiness.centerLabel")}
      items={[
        {
          key: "ready",
          label: t("dashboard.org.avatarReadiness.ready"),
          count: data.ready,
          color: statusColor("ready").bg,
          hex: statusColor("ready").hex,
        },
        {
          key: "partial",
          label: t("dashboard.org.avatarReadiness.partial"),
          count: data.partial,
          color: statusColor("partial").bg,
          hex: statusColor("partial").hex,
        },
        {
          key: "not_ready",
          label: t("dashboard.org.avatarReadiness.notReady"),
          count: data.not_ready,
          color: "bg-zinc-300 dark:bg-zinc-600",
          hex: "#d4d4d8",
        },
      ]}
    />
  );
}

function ManageSection({ orgId }: { orgId: string }) {
  const { t } = useTranslation();
  const items = [
    useHasPermission(P.MEMBER_VIEW) && {
      to: `/orgs/${orgId}/members`,
      title: t("nav.members"),
      description: t("members.subtitle"),
      icon: Users,
    },
    useHasPermission(P.TEMPLATE_SLIDE_MANAGE) && {
      to: `/orgs/${orgId}/templates/slide`,
      title: t("nav.templatesSlide"),
      description: t("templates.slide.subtitle"),
      icon: Presentation,
    },
    useHasPermission(P.TEMPLATE_PDF_MANAGE) && {
      to: `/orgs/${orgId}/templates/pdf`,
      title: t("nav.templatesPdf"),
      description: t("templates.pdf.subtitle"),
      icon: ScrollText,
    },
  ].filter(Boolean) as {
    to: string;
    title: string;
    description: string;
    icon: typeof Users;
  }[];

  if (items.length === 0) return null;

  return (
    <div className="space-y-3">
      <div>
        <h2 className="text-base font-medium">
          {t("dashboard.org.manage.title")}
        </h2>
        <p className="text-sm text-muted-foreground">
          {t("dashboard.org.manage.subtitle")}
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {items.map((it) => {
          const Icon = it.icon;
          return (
            <Link key={it.to} to={it.to} className="group">
              <Card className="h-full transition-colors group-hover:border-foreground/20">
                <CardContent className="flex flex-col gap-3 p-6">
                  <div className="grid size-9 place-items-center rounded-md bg-muted text-foreground">
                    <Icon className="size-4" />
                  </div>
                  <div>
                    <CardTitle className="text-base">{it.title}</CardTitle>
                    <CardDescription className="mt-1 text-sm">
                      {it.description}
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
