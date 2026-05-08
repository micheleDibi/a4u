import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { permissionsApi } from "@/api/permissions";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { extractApiError } from "@/lib/errors";
import { ALL_PERMISSIONS, ROLE_CODES, ROLES, type RoleCode } from "@/lib/permissions";

export default function PermissionsManagerPage() {
  const { t } = useTranslation();
  const [role, setRole] = useState<RoleCode>(ROLES.ORG_ADMIN);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const qc = useQueryClient();

  const query = useQuery({
    queryKey: ["role-defaults", role],
    queryFn: () => permissionsApi.getRoleDefaults(role),
  });

  useEffect(() => {
    if (query.data) setSelected(new Set(query.data.permissions));
  }, [query.data]);

  const save = useMutation({
    mutationFn: () => permissionsApi.setRoleDefaults(role, Array.from(selected)),
    onSuccess: () => {
      toast.success(t("globalPermissions.saved"));
      qc.invalidateQueries({ queryKey: ["role-defaults", role] });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("globalPermissions.title")}
        description={t("globalPermissions.subtitle")}
      />
      <Card>
        <CardContent className="space-y-4 p-6">
          <div className="max-w-sm space-y-1.5">
            <Label>{t("globalPermissions.rolePicker")}</Label>
            <Select value={role} onValueChange={(v) => setRole(v as RoleCode)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ROLE_CODES.map((r) => (
                  <SelectItem key={r} value={r}>
                    {t(`roles.${r}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid gap-2 sm:grid-cols-2">
            {ALL_PERMISSIONS.map((code) => (
              <label
                key={code}
                className="flex cursor-pointer items-start gap-2 rounded-md border border-border p-3 transition-colors hover:bg-accent/50"
              >
                <Checkbox
                  className="mt-0.5"
                  checked={selected.has(code)}
                  onCheckedChange={(v) => {
                    const next = new Set(selected);
                    if (v) next.add(code);
                    else next.delete(code);
                    setSelected(next);
                  }}
                />
                <div className="flex min-w-0 flex-col gap-0.5">
                  <span className="text-sm font-medium" title={code}>
                    {t(`permissions.${code}`)}
                  </span>
                  <span className="text-xs leading-snug text-muted-foreground">
                    {t(`permissionDescriptions.${code}`)}
                  </span>
                </div>
              </label>
            ))}
          </div>

          <div className="flex justify-end">
            <Button onClick={() => save.mutate()} disabled={save.isPending || query.isLoading}>
              {save.isPending ? t("common.saving") : t("globalPermissions.save")}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
