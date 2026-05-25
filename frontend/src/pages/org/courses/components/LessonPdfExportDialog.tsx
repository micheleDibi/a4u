import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { FileText, Star } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { pdfTemplatesApi } from "@/api/pdfTemplates";
import type { PdfTemplateOut } from "@/api/types";

export type LessonPdfExportMode = "single" | "all" | "missing";

interface Props {
  open: boolean;
  mode: LessonPdfExportMode;
  /** Numero di lezioni che verranno esportate (per il riepilogo in modalità batch). */
  exportableCount?: number;
  /** Etichetta della lezione singola (es. "M1.L1 — Introduzione"). */
  lessonLabel?: string;
  /** Template attualmente associato alla lezione (preselezione). */
  initialTemplateId?: string | null;
  orgId: string;
  isPending: boolean;
  onClose: () => void;
  /** Chiamato con l'id del template scelto, oppure `null` se l'org
   *  non ha template — il backend userà il default integrato. */
  onConfirm: (templateId: string | null) => void;
}

export function LessonPdfExportDialog({
  open,
  mode,
  exportableCount,
  lessonLabel,
  initialTemplateId,
  orgId,
  isPending,
  onClose,
  onConfirm,
}: Props) {
  const { t } = useTranslation();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const tplQuery = useQuery({
    queryKey: ["org", orgId, "pdf-templates"],
    queryFn: () => pdfTemplatesApi.list(orgId),
    enabled: open,
    staleTime: 30_000,
  });

  // Quando i template arrivano (o cambia preselezione), pre-seleziona il
  // template iniziale → altrimenti il default → altrimenti il primo.
  useEffect(() => {
    if (!open) return;
    const list = tplQuery.data ?? [];
    if (list.length === 0) return;
    if (initialTemplateId && list.some((t) => t.id === initialTemplateId)) {
      setSelectedId(initialTemplateId);
      return;
    }
    const def = list.find((t) => t.is_default);
    setSelectedId((def ?? list[0]).id);
  }, [open, tplQuery.data, initialTemplateId]);

  const noTemplates =
    !tplQuery.isLoading && (tplQuery.data?.length ?? 0) === 0;

  const handleConfirm = () => {
    if (noTemplates) {
      onConfirm(null);
      return;
    }
    if (!selectedId) return;
    onConfirm(selectedId);
  };

  const titleKey =
    mode === "all"
      ? "courses.lessonsPdf.dialog.exportAll.title"
      : mode === "missing"
        ? "courses.lessonsPdf.dialog.exportMissing.title"
        : "courses.lessonsPdf.dialog.exportLesson.title";
  const descriptionKey =
    mode === "all"
      ? "courses.lessonsPdf.dialog.exportAll.description"
      : mode === "missing"
        ? "courses.lessonsPdf.dialog.exportMissing.description"
        : "courses.lessonsPdf.dialog.exportLesson.description";
  const ctaKey =
    mode === "all"
      ? "courses.lessonsPdf.dialog.exportAllCta"
      : mode === "missing"
        ? "courses.lessonsPdf.dialog.exportMissingCta"
        : "courses.lessonsPdf.dialog.exportCta";

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FileText className="size-5" />
            {t(titleKey, {
              lesson: lessonLabel ?? "",
              count: exportableCount ?? 0,
              defaultValue: "Genera PDF",
            })}
          </DialogTitle>
          <DialogDescription>
            {t(descriptionKey, {
              count: exportableCount ?? 0,
              defaultValue:
                "Scegli il template grafico da applicare al PDF.",
            })}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 py-2">
          <Label className="text-sm font-medium">
            {t("courses.lessonsPdf.dialog.templateLabel", {
              defaultValue: "Template",
            })}
          </Label>

          {tplQuery.isLoading && (
            <div className="space-y-2">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
            </div>
          )}

          {!tplQuery.isLoading && (tplQuery.data?.length ?? 0) === 0 && (
            <div className="rounded-md border border-dashed border-muted-foreground/30 bg-muted/30 px-4 py-6 text-center text-sm text-muted-foreground">
              {t("courses.lessonsPdf.dialog.noTemplates", {
                defaultValue:
                  "Nessun template configurato per l'organizzazione. " +
                  "Verrà usato il template di default integrato.",
              })}
            </div>
          )}

          {(tplQuery.data?.length ?? 0) > 0 && (
            <ScrollArea className="max-h-[50vh] pr-2">
              <div className="space-y-2">
                {tplQuery.data!.map((tpl) => (
                  <TemplateOption
                    key={tpl.id}
                    tpl={tpl}
                    selected={selectedId === tpl.id}
                    onSelect={() => setSelectedId(tpl.id)}
                  />
                ))}
              </div>
            </ScrollArea>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={isPending}>
            {t("common.cancel")}
          </Button>
          <Button
            onClick={handleConfirm}
            disabled={
              isPending ||
              tplQuery.isLoading ||
              (!noTemplates && !selectedId)
            }
            className="min-w-[140px]"
          >
            <FileText className="size-4" />
            {t(ctaKey, { defaultValue: "Esporta" })}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

interface TemplateOptionProps {
  tpl: PdfTemplateOut;
  selected: boolean;
  onSelect: () => void;
}

function TemplateOption({ tpl, selected, onSelect }: TemplateOptionProps) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={
        "flex w-full items-center gap-3 rounded-md border px-3 py-2.5 text-left transition-colors " +
        (selected
          ? "border-primary bg-primary/5 ring-1 ring-primary"
          : "border-border bg-background hover:bg-muted/40")
      }
    >
      {/* Swatch dei colori */}
      <div className="flex shrink-0 gap-1">
        <span
          className="block size-6 rounded-md border border-border"
          style={{ backgroundColor: tpl.primary_color }}
          aria-label="primary color"
        />
        <span
          className="block size-6 rounded-md border border-border"
          style={{ backgroundColor: tpl.secondary_color }}
          aria-label="secondary color"
        />
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate font-medium">{tpl.name}</span>
          {tpl.is_default && (
            <Badge variant="secondary" className="gap-1 text-[11px]">
              <Star className="size-3" />
              {t("courses.lessonsPdf.dialog.defaultBadge", {
                defaultValue: "Default",
              })}
            </Badge>
          )}
        </div>
        <div className="mt-0.5 text-xs text-muted-foreground">
          {tpl.font_family} · {tpl.page_size} · {tpl.margin_mm}mm
        </div>
      </div>

      <div
        className={
          "size-4 shrink-0 rounded-full border-2 " +
          (selected
            ? "border-primary bg-primary ring-2 ring-primary/30"
            : "border-muted-foreground/40")
        }
        aria-hidden
      />
    </button>
  );
}
