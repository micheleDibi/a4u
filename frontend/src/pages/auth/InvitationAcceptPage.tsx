import { type FormEvent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router-dom";
import { AlertTriangle, Loader2 } from "lucide-react";
import { invitationsApi } from "@/api/invitations";
import { useAuth } from "@/auth/AuthContext";
import { extractApiError } from "@/lib/errors";
import type { InvitationPreview } from "@/api/types";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export default function InvitationAcceptPage() {
  const { token = "" } = useParams();
  const navigate = useNavigate();
  const { refresh } = useAuth();
  const [preview, setPreview] = useState<InvitationPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const { t } = useTranslation();

  useEffect(() => {
    void (async () => {
      try {
        setPreview(await invitationsApi.preview(token));
      } catch (err) {
        setError(extractApiError(err).message);
      } finally {
        setLoading(false);
      }
    })();
  }, [token]);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await invitationsApi.accept(token, {
        full_name: fullName || undefined,
        password: password || undefined,
      });
      await refresh();
      navigate("/", { replace: true });
    } catch (err) {
      setError(extractApiError(err).message);
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="grid min-h-screen place-items-center">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!preview || !preview.valid) {
    return (
      <div className="grid min-h-screen place-items-center px-4">
        <Alert variant="destructive" className="max-w-md">
          <AlertTriangle className="size-4" />
          <AlertDescription>{t("auth.invitation.invalidOrExpired")}</AlertDescription>
        </Alert>
      </div>
    );
  }

  const newUser = !preview.user_exists;

  return (
    <div className="grid min-h-screen place-items-center bg-muted/40 px-4 py-12">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>{t("auth.invitation.title")}</CardTitle>
          <CardDescription>
            {t("auth.invitation.subtitle", {
              org: preview.organization_name,
              role: preview.role_name_it,
            })}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-4">
            {newUser ? (
              <>
                <div className="space-y-2">
                  <Label htmlFor="fullName">{t("auth.invitation.fullName")}</Label>
                  <Input
                    id="fullName"
                    required
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="password">{t("auth.login.password")}</Label>
                  <Input
                    id="password"
                    type="password"
                    required
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />
                  <p className="text-xs text-muted-foreground">
                    {t("auth.invitation.passwordHint")}
                  </p>
                </div>
              </>
            ) : (
              <Alert>
                <AlertDescription>{t("auth.invitation.alreadyRegistered")}</AlertDescription>
              </Alert>
            )}
            {error && (
              <Alert variant="destructive">
                <AlertTriangle className="size-4" />
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}
            <Button type="submit" className="w-full" disabled={submitting}>
              {submitting ? t("auth.invitation.submitting") : t("auth.invitation.submit")}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
