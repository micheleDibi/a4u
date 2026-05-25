import {
  BookOpen,
  GraduationCap,
  Presentation,
  ScrollText,
  Smile,
  UserCheck,
  Users,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import type {
  LessonsPhaseBreakdown,
  StatusCount,
} from "@/api/adminMetrics";
import { organizationsApi } from "@/api/organizations";
import type { OrgMetricsOut } from "@/api/orgMetrics";
import { useAuth } from "@/auth/AuthContext";
import { useHasPermission } from "@/auth/PermissionGate";
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
import { useOrgMetrics } from "@/hooks/useOrgMetrics";
import { P } from "@/lib/permissions";
import { sortByLifecycleOrder, statusColor } from "@/lib/statusColors";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function pickCount(
  raw: StatusCount[] | undefined,
  status: string,
): number {
  return raw?.find((s) => s.status === status)?.count ?? 0;
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

export default function OrgDashboard() {
  const { orgId = "" } = useParams();
  const { me } = useAuth();
  const { t } = useTranslation();

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

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">
            {t("dashboard.org.pipelineCourses.title")}
          </CardTitle>
          <CardDescription>
            {t("dashboard.org.pipelineCourses.subtitle")}
          </CardDescription>
        </CardHeader>
        <CardContent className="pt-0">
          <CoursePipelineDetail
            items={data?.courses.by_status ?? []}
            total={data?.courses.total ?? 0}
            emptyLabel={t("dashboard.org.pipelineCourses.empty")}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">
            {t("dashboard.org.pipelineLessons.title")}
          </CardTitle>
          <CardDescription>
            {t("dashboard.org.pipelineLessons.subtitle", {
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
                    {t(`dashboard.org.pipelineLessons.${key}`)}
                  </div>
                  <div className="text-sm font-semibold tabular-nums">
                    {phaseTotal}
                  </div>
                </div>
                <StatusBarChart
                  compact
                  emptyLabel={t("dashboard.org.pipelineLessons.empty")}
                  items={lifecycleItems(items, t)}
                />
              </div>
            );
          })}
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
  const coursesPublished = pickCount(data?.courses.by_status, "published");
  const lessonsVideoReady = pickCount(data?.lessons.phases.video, "ready");

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
      <KpiCard
        icon={GraduationCap}
        label={t("dashboard.org.kpi.coursesTotal")}
        value={formatInt(data?.courses.total)}
      />
      <KpiCard
        icon={GraduationCap}
        label={t("dashboard.org.kpi.coursesPublished")}
        value={formatInt(coursesPublished)}
      />
      <KpiCard
        icon={BookOpen}
        label={t("dashboard.org.kpi.lessonsTotal")}
        value={formatInt(data?.lessons.total)}
      />
      <KpiCard
        icon={Smile}
        label={t("dashboard.org.kpi.lessonsVideoReady")}
        value={formatInt(lessonsVideoReady)}
      />
      <KpiCard
        icon={Users}
        label={t("dashboard.org.kpi.members")}
        value={formatInt(data?.members.total)}
      />
      <KpiCard
        icon={UserCheck}
        label={t("dashboard.org.kpi.pendingInvitations")}
        value={formatInt(data?.members.pending_invitations)}
        tone={
          data && data.members.pending_invitations > 0 ? "default" : "muted"
        }
      />
    </div>
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
              <Card className="h-full transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md hover:border-foreground/15">
                <CardContent className="flex flex-col gap-3 p-6">
                  <div className="grid size-10 place-items-center rounded-lg bg-primary/10 text-primary">
                    <Icon className="size-5" />
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
