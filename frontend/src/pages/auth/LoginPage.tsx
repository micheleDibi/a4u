import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, useNavigate } from "react-router-dom";
import { AlertTriangle, PaintbrushVertical } from "lucide-react";
import { useAuth } from "@/auth/AuthContext";
import { extractApiError } from "@/lib/errors";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ThemeToggle } from "@/components/ThemeToggle";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";

export default function LoginPage() {
  const { login, me } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const { t } = useTranslation();

  if (me) return <Navigate to="/" replace />;

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(email.trim(), password);
      navigate("/", { replace: true });
    } catch (err) {
      setError(extractApiError(err).message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="grid min-h-screen grid-cols-1 lg:grid-cols-2">
      <div className="flex flex-col px-6 py-8 sm:px-12 md:px-16 lg:px-24">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="grid size-8 place-items-center rounded-md bg-brand text-brand-foreground">
              <PaintbrushVertical className="size-4" />
            </div>
            <span className="font-semibold">{t("app.name")}</span>
          </div>
          <div className="flex items-center gap-1">
            <LanguageSwitcher />
            <ThemeToggle />
          </div>
        </div>

        <div className="my-auto w-full max-w-sm space-y-6 self-center">
          <div className="space-y-1">
            <h1 className="text-2xl font-semibold tracking-tight">{t("auth.login.title")}</h1>
            <p className="text-sm text-muted-foreground">{t("auth.login.subtitle")}</p>
          </div>
          <form onSubmit={onSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="email">{t("auth.login.email")}</Label>
              <Input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
                required
                autoFocus
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">{t("auth.login.password")}</Label>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
            </div>
            {error && (
              <Alert variant="destructive">
                <AlertTriangle className="size-4" />
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}
            <Button type="submit" className="w-full" disabled={submitting}>
              {submitting ? t("auth.login.submitting") : t("auth.login.submit")}
            </Button>
          </form>
        </div>

        <div className="text-xs text-muted-foreground">© a4u</div>
      </div>

      <div className="relative hidden overflow-hidden bg-muted lg:block">
        <div className="absolute inset-0 bg-gradient-to-br from-brand/20 via-transparent to-brand/5 dark:from-brand/15 dark:to-brand/5" />
        <div className="relative flex h-full flex-col items-center justify-center p-12 text-center">
          <div className="grid size-16 place-items-center rounded-2xl bg-brand text-brand-foreground shadow-lg">
            <PaintbrushVertical className="size-8" />
          </div>
          <h2 className="mt-6 text-3xl font-semibold tracking-tight">a4u</h2>
          <p className="mt-2 max-w-md text-balance text-sm text-muted-foreground">
            {t("app.tagline")}
          </p>
        </div>
      </div>
    </div>
  );
}
