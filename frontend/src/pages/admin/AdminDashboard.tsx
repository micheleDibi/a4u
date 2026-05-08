import { Building2, LockKeyhole, UsersRound } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Card, CardContent, CardDescription, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/layout/PageHeader";

export default function AdminDashboard() {
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
    <div className="space-y-6">
      <PageHeader
        title={t("dashboard.admin.title")}
        description={t("dashboard.admin.subtitle")}
      />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
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
