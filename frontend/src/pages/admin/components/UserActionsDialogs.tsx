import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import type { UserOut } from "@/api/types";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { isPasswordStrong } from "@/lib/passwordSchema";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

// ---------------------------------------------------------------------------
// EditUserDialog — modifica nome + email di un utente
// ---------------------------------------------------------------------------

interface EditUserDialogProps {
  open: boolean;
  user: UserOut | null;
  isPending: boolean;
  onClose: () => void;
  onSubmit: (data: { full_name: string; email: string }) => void;
}

export function EditUserDialog({
  open,
  user,
  isPending,
  onClose,
  onSubmit,
}: EditUserDialogProps) {
  const { t } = useTranslation();
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");

  // Riallinea i campi all'apertura / al cambio utente (il dialog resta
  // montato tra un'apertura e l'altra).
  useEffect(() => {
    if (open && user) {
      setFullName(user.full_name);
      setEmail(user.email);
    }
  }, [open, user]);

  const trimmedName = fullName.trim();
  const trimmedEmail = email.trim();
  const valid = trimmedName.length > 0 && EMAIL_RE.test(trimmedEmail);
  const dirty =
    !!user &&
    (trimmedName !== user.full_name ||
      trimmedEmail.toLowerCase() !== user.email.toLowerCase());

  const submit = () => {
    if (!valid || isPending) return;
    onSubmit({ full_name: trimmedName, email: trimmedEmail });
  };

  return (
    <Dialog open={open} onOpenChange={(v) => (!v && !isPending ? onClose() : undefined)}>
      <DialogContent
        className="sm:max-w-md"
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
            e.preventDefault();
            submit();
          }
        }}
      >
        <DialogHeader>
          <DialogTitle>{t("users.editDialog.title")}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="eu-name">{t("users.editDialog.name")}</Label>
            <Input
              id="eu-name"
              value={fullName}
              maxLength={255}
              onChange={(e) => setFullName(e.target.value)}
              disabled={isPending}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="eu-email">{t("users.editDialog.email")}</Label>
            <Input
              id="eu-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={isPending}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={isPending}>
            {t("common.cancel")}
          </Button>
          <Button onClick={submit} disabled={!valid || !dirty || isPending}>
            {isPending ? t("common.saving") : t("common.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// SetPasswordDialog — reset password manuale (admin)
// ---------------------------------------------------------------------------

interface SetPasswordDialogProps {
  open: boolean;
  user: UserOut | null;
  isPending: boolean;
  onClose: () => void;
  onSubmit: (password: string) => void;
}

export function SetPasswordDialog({
  open,
  user,
  isPending,
  onClose,
  onSubmit,
}: SetPasswordDialogProps) {
  const { t } = useTranslation();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");

  useEffect(() => {
    if (open) {
      setPassword("");
      setConfirm("");
    }
  }, [open]);

  const strong = isPasswordStrong(password);
  const match = password.length > 0 && password === confirm;
  const valid = strong && match;

  const submit = () => {
    if (!valid || isPending) return;
    onSubmit(password);
  };

  return (
    <Dialog open={open} onOpenChange={(v) => (!v && !isPending ? onClose() : undefined)}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("users.resetPasswordDialog.title")}</DialogTitle>
          <DialogDescription>
            {user?.full_name} — {user?.email}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="sp-pw">{t("users.resetPasswordDialog.password")}</Label>
            <Input
              id="sp-pw"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={isPending}
            />
            <p className="text-xs text-muted-foreground">
              {t("users.resetPasswordDialog.hint")}
            </p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="sp-confirm">{t("users.resetPasswordDialog.confirm")}</Label>
            <Input
              id="sp-confirm"
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              disabled={isPending}
            />
            {confirm.length > 0 && !match && (
              <p className="text-xs text-destructive">
                {t("users.resetPasswordDialog.mismatch")}
              </p>
            )}
          </div>
          <p className="rounded-md border border-amber-300/50 bg-amber-50/50 px-3 py-2 text-xs text-amber-800 dark:border-amber-500/30 dark:bg-amber-900/10 dark:text-amber-200">
            {t("users.resetPasswordDialog.revokeWarning")}
          </p>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={isPending}>
            {t("common.cancel")}
          </Button>
          <Button onClick={submit} disabled={!valid || isPending}>
            {isPending ? t("common.saving") : t("common.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
