import { Loader2 } from "lucide-react";
import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "./AuthContext";

interface Props {
  children: ReactNode;
  requirePlatformAdmin?: boolean;
}

export function ProtectedRoute({ children, requirePlatformAdmin = false }: Props) {
  const { me, loading } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <div className="grid min-h-screen place-items-center">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  }
  if (!me) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  if (requirePlatformAdmin && !me.is_platform_admin) {
    return <Navigate to="/" replace />;
  }
  return <>{children}</>;
}
