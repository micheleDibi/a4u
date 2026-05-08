import { Info } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Navigate } from "react-router-dom";
import { useAuth } from "@/auth/AuthContext";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

export default function RootRedirect() {
  const { me } = useAuth();
  const { t } = useTranslation();
  if (!me) return null;
  if (me.is_platform_admin) return <Navigate to="/admin" replace />;
  if (me.organizations.length >= 1) {
    return <Navigate to={`/orgs/${me.organizations[0].organization_id}`} replace />;
  }
  return (
    <div className="grid min-h-screen place-items-center px-4">
      <Alert className="max-w-lg">
        <Info className="size-4" />
        <AlertTitle>{t("dashboard.noOrg.title")}</AlertTitle>
        <AlertDescription>{t("dashboard.noOrg.message")}</AlertDescription>
      </Alert>
    </div>
  );
}
