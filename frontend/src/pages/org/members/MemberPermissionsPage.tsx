import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { membershipsApi } from "@/api/memberships";
import type { PermissionOverrideEntry } from "@/api/types";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { extractApiError } from "@/lib/errors";
import { ALL_PERMISSIONS } from "@/lib/permissions";

type Mode = "default" | "grant" | "revoke";

export default function MemberPermissionsPage() {
  const { orgId = "", userId = "" } = useParams();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const [overrides, setOverrides] = useState<Record<string, Mode>>({});

  const memberQuery = useQuery({
    queryKey: ["org", orgId, "members"],
    queryFn: () => membershipsApi.list(orgId),
  });
  const member = memberQuery.data?.find((m) => m.user_id === userId);

  const overrideQuery = useQuery({
    queryKey: ["member-permissions", orgId, userId],
    queryFn: () => membershipsApi.getMemberPermissions(orgId, userId),
  });

  useEffect(() => {
    if (!overrideQuery.data) return;
    const next: Record<string, Mode> = {};
    for (const o of overrideQuery.data.overrides) {
      next[o.code] = o.granted ? "grant" : "revoke";
    }
    setOverrides(next);
  }, [overrideQuery.data]);

  const save = useMutation({
    mutationFn: () => {
      const payload: PermissionOverrideEntry[] = Object.entries(overrides)
        .filter(([, m]) => m !== "default")
        .map(([code, m]) => ({ code, granted: m === "grant" }));
      return membershipsApi.setMemberPermissions(orgId, userId, payload);
    },
    onSuccess: () => {
      toast.success(t("memberPermissions.saved"));
      qc.invalidateQueries({ queryKey: ["member-permissions", orgId, userId] });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("memberPermissions.title", { name: member?.user_full_name ?? "…" })}
        description={t("memberPermissions.subtitle")}
      />
      <Card>
        <CardContent className="divide-y divide-border p-0">
          {ALL_PERMISSIONS.map((code) => {
            const mode = overrides[code] ?? "default";
            return (
              <div
                key={code}
                className="grid items-center gap-2 px-4 py-3 sm:grid-cols-[1fr_auto] sm:gap-6"
              >
                <div className="min-w-0">
                  <div className="text-sm font-medium" title={code}>
                    {t(`permissions.${code}`)}
                  </div>
                  <div className="text-xs leading-snug text-muted-foreground">
                    {t(`permissionDescriptions.${code}`)}
                  </div>
                </div>
                <RadioGroup
                  value={mode}
                  onValueChange={(v) => setOverrides({ ...overrides, [code]: v as Mode })}
                  className="flex items-center gap-3 sm:gap-5"
                >
                  {(["default", "grant", "revoke"] as Mode[]).map((m) => (
                    <label key={m} className="flex items-center gap-1.5 text-sm">
                      <RadioGroupItem value={m} id={`${code}-${m}`} />
                      <span>{t(`memberPermissions.${m}`)}</span>
                    </label>
                  ))}
                </RadioGroup>
              </div>
            );
          })}
        </CardContent>
      </Card>
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={() => navigate(-1)}>
          {t("common.cancel")}
        </Button>
        <Button onClick={() => save.mutate()} disabled={save.isPending}>
          {save.isPending ? t("common.saving") : t("common.save")}
        </Button>
      </div>
    </div>
  );
}
