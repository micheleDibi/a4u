import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { authApi } from "@/api/auth";
import { useAuth } from "@/auth/AuthContext";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { extractApiError } from "@/lib/errors";
import { isPasswordStrong } from "@/lib/passwordSchema";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function formatDate(value: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

export default function ProfilePage() {
  const { t } = useTranslation();
  const { me, refresh } = useAuth();

  // Nome
  const [fullName, setFullName] = useState(me?.user.full_name ?? "");
  // Cambio email
  const [emailCurrentPw, setEmailCurrentPw] = useState("");
  const [newEmail, setNewEmail] = useState("");
  // Cambio password
  const [pwCurrent, setPwCurrent] = useState("");
  const [pwNew, setPwNew] = useState("");
  const [pwConfirm, setPwConfirm] = useState("");

  const nameMut = useMutation({
    mutationFn: (name: string) => authApi.updateMe(name),
    onSuccess: async () => {
      await refresh();
      toast.success(t("profile.personal.nameSaved"));
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const emailMut = useMutation({
    mutationFn: (vars: { current_password: string; new_email: string }) =>
      authApi.changeEmail(vars.current_password, vars.new_email),
    onSuccess: async () => {
      await refresh();
      setEmailCurrentPw("");
      setNewEmail("");
      toast.success(t("profile.personal.emailSaved"));
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const passwordMut = useMutation({
    mutationFn: (vars: { current_password: string; new_password: string }) =>
      authApi.changePassword(vars.current_password, vars.new_password),
    onSuccess: () => {
      setPwCurrent("");
      setPwNew("");
      setPwConfirm("");
      toast.success(t("profile.security.saved"));
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  if (!me) return null;

  const nameDirty =
    fullName.trim().length > 0 && fullName.trim() !== me.user.full_name;

  const emailValid =
    emailCurrentPw.length > 0 &&
    EMAIL_RE.test(newEmail.trim()) &&
    newEmail.trim().toLowerCase() !== me.user.email.toLowerCase();

  const pwMatch = pwNew.length > 0 && pwNew === pwConfirm;
  const pwValid =
    pwCurrent.length > 0 &&
    isPasswordStrong(pwNew) &&
    pwMatch &&
    pwNew !== pwCurrent;

  return (
    <div className="space-y-6">
      <PageHeader title={t("profile.title")} description={t("profile.subtitle")} />

      {/* Informazioni personali */}
      <Card>
        <CardHeader>
          <CardTitle>{t("profile.personal.title")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-6">
          {/* Nome */}
          <div className="space-y-1.5">
            <Label htmlFor="pf-name">{t("profile.personal.name")}</Label>
            <div className="flex items-center gap-2">
              <Input
                id="pf-name"
                value={fullName}
                maxLength={255}
                onChange={(e) => setFullName(e.target.value)}
                disabled={nameMut.isPending}
              />
              <Button
                onClick={() => nameMut.mutate(fullName.trim())}
                disabled={!nameDirty || nameMut.isPending}
              >
                {nameMut.isPending ? t("common.saving") : t("common.save")}
              </Button>
            </div>
          </div>

          <Separator />

          {/* Cambio email */}
          <div className="space-y-3">
            <div className="space-y-0.5">
              <h3 className="text-sm font-semibold">
                {t("profile.personal.emailSection")}
              </h3>
              <p className="text-xs text-muted-foreground">
                {t("profile.personal.currentEmail")}: {me.user.email}
              </p>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="pf-new-email">
                  {t("profile.personal.newEmail")}
                </Label>
                <Input
                  id="pf-new-email"
                  type="email"
                  value={newEmail}
                  onChange={(e) => setNewEmail(e.target.value)}
                  disabled={emailMut.isPending}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="pf-email-pw">
                  {t("profile.personal.currentPassword")}
                </Label>
                <Input
                  id="pf-email-pw"
                  type="password"
                  value={emailCurrentPw}
                  onChange={(e) => setEmailCurrentPw(e.target.value)}
                  disabled={emailMut.isPending}
                />
              </div>
            </div>
            <div className="flex justify-end">
              <Button
                onClick={() =>
                  emailMut.mutate({
                    current_password: emailCurrentPw,
                    new_email: newEmail.trim(),
                  })
                }
                disabled={!emailValid || emailMut.isPending}
              >
                {emailMut.isPending
                  ? t("common.saving")
                  : t("profile.personal.changeEmail")}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Sicurezza */}
      <Card>
        <CardHeader>
          <CardTitle>{t("profile.security.title")}</CardTitle>
          <CardDescription>{t("profile.security.hint")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="pf-cur-pw">
              {t("profile.security.currentPassword")}
            </Label>
            <Input
              id="pf-cur-pw"
              type="password"
              value={pwCurrent}
              onChange={(e) => setPwCurrent(e.target.value)}
              disabled={passwordMut.isPending}
            />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="pf-new-pw">
                {t("profile.security.newPassword")}
              </Label>
              <Input
                id="pf-new-pw"
                type="password"
                value={pwNew}
                onChange={(e) => setPwNew(e.target.value)}
                disabled={passwordMut.isPending}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="pf-confirm-pw">
                {t("profile.security.confirm")}
              </Label>
              <Input
                id="pf-confirm-pw"
                type="password"
                value={pwConfirm}
                onChange={(e) => setPwConfirm(e.target.value)}
                disabled={passwordMut.isPending}
              />
              {pwConfirm.length > 0 && !pwMatch && (
                <p className="text-xs text-destructive">
                  {t("profile.security.mismatch")}
                </p>
              )}
            </div>
          </div>
          <div className="flex justify-end">
            <Button
              onClick={() =>
                passwordMut.mutate({
                  current_password: pwCurrent,
                  new_password: pwNew,
                })
              }
              disabled={!pwValid || passwordMut.isPending}
            >
              {passwordMut.isPending
                ? t("common.saving")
                : t("profile.security.changePassword")}
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Riepilogo account (read-only) */}
      <Card>
        <CardHeader>
          <CardTitle>{t("profile.summary.title")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <SummaryRow label={t("profile.summary.email")} value={me.user.email} />
          <SummaryRow
            label={t("profile.summary.lastLogin")}
            value={formatDate(me.user.last_login_at)}
          />
          <SummaryRow
            label={t("profile.summary.createdAt")}
            value={formatDate(me.user.created_at)}
          />
          <Separator />
          <div className="space-y-1.5">
            <span className="text-muted-foreground">
              {t("profile.summary.organizations")}
            </span>
            {me.organizations.length === 0 ? (
              <p className="text-muted-foreground">
                {t("profile.summary.noOrganizations")}
              </p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {me.organizations.map((o) => (
                  <Badge
                    key={o.organization_id}
                    variant="secondary"
                    className="font-normal"
                  >
                    {o.organization_name} — {o.role_name_it}
                  </Badge>
                ))}
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium">{value}</span>
    </div>
  );
}
