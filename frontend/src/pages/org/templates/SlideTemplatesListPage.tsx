import { MoreVertical, Pencil, Plus, Star, Trash2 } from "lucide-react";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { slideTemplatesApi } from "@/api/slideTemplates";
import type { SlideTemplateOut } from "@/api/types";
import { PageHeader } from "@/components/layout/PageHeader";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { SlideTemplatePreview } from "@/components/templates/SlideTemplatePreview";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { extractApiError } from "@/lib/errors";

export default function SlideTemplatesListPage() {
  const { orgId = "" } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { t } = useTranslation();
  const [toDelete, setToDelete] = useState<SlideTemplateOut | null>(null);

  const query = useQuery({
    queryKey: ["org", orgId, "slide-templates"],
    queryFn: () => slideTemplatesApi.list(orgId),
  });

  const remove = useMutation({
    mutationFn: (id: string) => slideTemplatesApi.remove(orgId, id),
    onSuccess: () => {
      toast.success(t("templates.slide.deleted"));
      qc.invalidateQueries({ queryKey: ["org", orgId, "slide-templates"] });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const setDefault = useMutation({
    mutationFn: (id: string) => slideTemplatesApi.setDefault(orgId, id),
    onSuccess: () => {
      toast.success(t("templates.defaultSet"));
      qc.invalidateQueries({ queryKey: ["org", orgId, "slide-templates"] });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("templates.slide.title")}
        description={t("templates.slide.subtitle")}
        actions={
          <Button onClick={() => navigate(`/orgs/${orgId}/templates/slide/new`)}>
            <Plus className="size-4" />
            {t("templates.slide.new")}
          </Button>
        }
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {(query.data ?? []).map((tpl) => (
          <Card key={tpl.id} className="group relative overflow-hidden">
            {tpl.is_default && (
              <Badge
                variant="secondary"
                className="absolute end-3 top-3 z-10 gap-1 bg-amber-100 text-amber-900 dark:bg-amber-500/15 dark:text-amber-300"
              >
                <Star className="size-3 fill-current" />
                {t("templates.default")}
              </Badge>
            )}
            <button
              type="button"
              onClick={() => navigate(`/orgs/${orgId}/templates/slide/${tpl.id}`)}
              className="relative block w-full p-4 text-left"
              title={t("common.edit")}
            >
              <SlideTemplatePreview
                background={tpl.background_image_path}
                logoLeft={tpl.logo_left_path}
                logoRight={tpl.logo_right_path}
                textColor={tpl.text_color}
                primaryColor={tpl.primary_color}
                secondaryColor={tpl.secondary_color}
                fontFamily={tpl.font_family}
                slideSize={tpl.slide_size}
              />
              <span className="pointer-events-none absolute end-6 bottom-6 inline-flex size-7 items-center justify-center rounded-md bg-background/90 text-muted-foreground opacity-0 shadow-sm ring-1 ring-border transition-opacity group-hover:opacity-100">
                <Pencil className="size-3.5" />
              </span>
            </button>
            <CardContent className="flex items-start justify-between p-4 pt-0">
              <div>
                <div className="text-sm font-medium">{tpl.name}</div>
                <div className="text-xs text-muted-foreground">
                  {tpl.slide_size} · {tpl.font_family}
                </div>
              </div>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon">
                    <MoreVertical className="size-4" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem
                    onSelect={() => navigate(`/orgs/${orgId}/templates/slide/${tpl.id}`)}
                  >
                    <Pencil className="size-4" />
                    {t("common.edit")}
                  </DropdownMenuItem>
                  {!tpl.is_default && (
                    <DropdownMenuItem onSelect={() => setDefault.mutate(tpl.id)}>
                      <Star className="size-4" />
                      {t("templates.setDefault")}
                    </DropdownMenuItem>
                  )}
                  <DropdownMenuItem
                    onSelect={() => setToDelete(tpl)}
                    className="text-destructive focus:text-destructive"
                  >
                    <Trash2 className="size-4" />
                    {t("common.delete")}
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </CardContent>
          </Card>
        ))}
        {query.data && query.data.length === 0 && (
          <div className="col-span-full rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
            {t("templates.slide.empty")}
          </div>
        )}
      </div>

      <ConfirmDialog
        open={!!toDelete}
        title={t("templates.deleteConfirm.title")}
        message={t("templates.deleteConfirm.message", { name: toDelete?.name ?? "" })}
        destructive
        confirmLabel={t("common.delete")}
        onClose={() => setToDelete(null)}
        onConfirm={() => {
          if (toDelete) {
            remove.mutate(toDelete.id);
            setToDelete(null);
          }
        }}
      />
    </div>
  );
}
