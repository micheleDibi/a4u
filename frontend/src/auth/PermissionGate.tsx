import type { ReactNode } from "react";
import { useParams } from "react-router-dom";
import { useAuth } from "./AuthContext";

interface Props {
  code: string | string[];
  children: ReactNode;
  fallback?: ReactNode;
  orgId?: string;
}

export function PermissionGate({ code, children, fallback = null, orgId: orgIdProp }: Props) {
  const { me } = useAuth();
  const params = useParams();
  const orgId = orgIdProp ?? params.orgId;

  if (!me) return <>{fallback}</>;
  if (me.is_platform_admin) return <>{children}</>;

  const org = me.organizations.find((o) => o.organization_id === orgId);
  if (!org) return <>{fallback}</>;

  const codes = Array.isArray(code) ? code : [code];
  const ok = codes.every((c) => org.permissions.includes(c));
  return <>{ok ? children : fallback}</>;
}

export function useHasPermission(code: string | string[], orgId?: string): boolean {
  const { me } = useAuth();
  const params = useParams();
  const targetOrg = orgId ?? params.orgId;
  if (!me) return false;
  if (me.is_platform_admin) return true;
  const org = me.organizations.find((o) => o.organization_id === targetOrg);
  if (!org) return false;
  const codes = Array.isArray(code) ? code : [code];
  return codes.every((c) => org.permissions.includes(c));
}
