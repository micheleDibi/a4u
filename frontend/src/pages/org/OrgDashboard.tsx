import { Presentation, ScrollText, Users } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@/auth/AuthContext";
import { useHasPermission } from "@/auth/PermissionGate";
import { Card, CardContent, CardDescription, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/layout/PageHeader";
import { organizationsApi } from "@/api/organizations";
import { P } from "@/lib/permissions";

export default function OrgDashboard() {
  const { orgId = "" } = useParams();
  const { me } = useAuth();
  const { t } = useTranslation();
  const membership = me?.organizations.find((o) => o.organization_id === orgId);
  const fallbackQuery = useQuery({
    queryKey: ["admin-org", orgId],
    queryFn: () => organizationsApi.get(orgId),
    enabled: !!orgId && !membership && !!me?.is_platform_admin,
  });
  const orgName = membership?.organization_name ?? fallbackQuery.data?.name ?? "—";
  const roleLabel = membership?.role_name_it ?? t("dashboard.org.platformAdmin");

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
  ].filter(Boolean) as { to: string; title: string; description: string; icon: typeof Users }[];

  return (
    <div className="space-y-6">
      <PageHeader
        title={orgName}
        description={t("dashboard.org.subtitle", { role: roleLabel })}
      />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
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
