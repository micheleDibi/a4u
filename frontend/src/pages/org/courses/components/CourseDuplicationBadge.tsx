import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Languages, Loader2, X } from "lucide-react";

import {
  coursesApi,
  type CourseDuplicationJobCompact,
} from "@/api/courses";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { flagFor } from "@/i18n/flags";
import { useLanguages } from "@/hooks/useLanguages";
import { extractApiError } from "@/lib/errors";
import { cn } from "@/lib/utils";

interface Props {
  orgId: string;
  job: CourseDuplicationJobCompact;
}

// Ordine canonico delle phase del worker (vedi
// `course_duplication_worker._process_one`). Serve a derivare il
// "Fase X di Y" mostrato all'utente.
const PHASE_ORDER: readonly string[] = [
  "loading_source",
  "cloning_structure",
  "translating_architecture",
  "translating_lesson_metadata",
  "translating_lesson_content_slides_speech",
  "translating_glossary_documents",
  "finalizing",
];

/**
 * Badge "Duplicazione" sulla riga del corso target durante il job.
 * Mostra: lingua target con bandiera, fase corrente leggibile con
 * spinner, indicatore "Fase X di Y", barra di progresso prominente
 * con percentuale, bottone "annulla".
 */
export function CourseDuplicationBadge({ orgId, job }: Props) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const languages = useLanguages();
  const targetLang = languages.find((l) => l.code === job.target_language_code);
  const Flag = flagFor(
    job.target_language_code,
    targetLang?.flag_country_code,
  );

  const cancelMut = useMutation({
    mutationFn: () => coursesApi.cancelDuplication(orgId, job.id),
    onSuccess: () => {
      toast.success(t("courses.duplicate.badge.cancelled"));
      qc.invalidateQueries({ queryKey: ["courses", "list", orgId] });
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.duplicate.toast.error"),
      ),
  });

  // Phase key effettiva: `progress_phase` può essere null quando il
  // worker non ha ancora settato la prima phase (status=pending) o
  // quando ha completato (progress=100). Mappiamo a "pending" o
  // "finalizing" per avere sempre un label.
  const phaseKey: string =
    job.progress_phase ??
    (job.status === "pending"
      ? "pending"
      : job.progress >= 95
        ? "finalizing"
        : "pending");

  const phaseLabel = t(
    `courses.duplicate.badge.phases.${phaseKey}`,
    // Fallback: identifier raw se la key non esiste (es. phase nuova
    // aggiunta backend e non ancora tradotta).
    phaseKey.replace(/_/g, " "),
  );

  // Step indicator: posizione nella sequenza canonica. Phase ignote
  // → step 0 (non mostriamo l'indicatore).
  const stepIdx = PHASE_ORDER.indexOf(phaseKey);
  const currentStep = stepIdx >= 0 ? stepIdx + 1 : 0;
  const totalSteps = PHASE_ORDER.length;
  const showStep = currentStep > 0 && job.status === "processing";

  const isActive = job.status === "processing" || job.status === "pending";

  return (
    <div className="flex min-w-[260px] max-w-[360px] flex-col gap-2 rounded-md border border-border bg-card/60 p-2.5 shadow-sm">
      {/* Riga 1: lingua target + step indicator + bottone annulla */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1.5">
          <Languages className="size-3.5 shrink-0 text-muted-foreground" />
          <span className="text-xs font-semibold uppercase tracking-wide">
            {t("courses.duplicate.badge.label")}
          </span>
          <span className="text-muted-foreground">→</span>
          <Flag className="size-4 shrink-0 rounded-sm shadow-[0_0_0_1px_rgba(0,0,0,0.08)]" />
          <span className="truncate text-xs font-medium">
            {targetLang?.name_native ?? job.target_language_code.toUpperCase()}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {showStep && (
            <span
              className="text-[10px] font-medium tabular-nums text-muted-foreground"
              title={t("courses.duplicate.badge.step", {
                current: currentStep,
                total: totalSteps,
              })}
            >
              {currentStep}/{totalSteps}
            </span>
          )}
          <Button
            variant="ghost"
            size="icon"
            className="size-6"
            onClick={() => cancelMut.mutate()}
            disabled={cancelMut.isPending}
            title={t("courses.duplicate.badge.cancel")}
            aria-label={t("courses.duplicate.badge.cancel")}
          >
            <X className="size-3.5" />
          </Button>
        </div>
      </div>

      {/* Riga 2: phase label + spinner animato */}
      <div className="flex items-center gap-1.5">
        {isActive && (
          <Loader2 className="size-3 shrink-0 animate-spin text-primary" />
        )}
        <span className="truncate text-xs text-foreground">{phaseLabel}</span>
      </div>

      {/* Riga 3: progress bar h-2 + percentuale prominente */}
      <div className="flex items-center gap-2">
        <Progress
          value={job.progress}
          className={cn(
            "h-2 flex-1",
            isActive && "[&>div]:bg-primary",
          )}
        />
        <span className="min-w-[2.5rem] text-right text-sm font-semibold tabular-nums text-foreground">
          {job.progress}%
        </span>
      </div>
    </div>
  );
}
