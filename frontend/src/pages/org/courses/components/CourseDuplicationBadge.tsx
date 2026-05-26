import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Check, Languages, Loader2, X } from "lucide-react";

import {
  coursesApi,
  type CourseDuplicationJobCompact,
} from "@/api/courses";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
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
// "Fase X di Y" e la lista step nel tooltip.
const PHASE_ORDER: readonly string[] = [
  "loading_source",
  "cloning_structure",
  "translating_architecture",
  "translating_lesson_metadata",
  "translating_lesson_content_slides_speech",
  "translating_glossary_documents",
  "finalizing",
];

type EtaResult =
  | { kind: "none" }
  | { kind: "seconds"; seconds: number }
  | { kind: "minutes"; minutes: number }
  | { kind: "hours"; hours: number; minutes: number };

/**
 * Calcola l'ETA stimato basato su `started_at` + `progress`.
 * Restituisce `none` quando il dato non è ancora significativo
 * (progress<3% o elapsed<5s, dove la stima sarebbe troppo rumorosa).
 */
function computeEta(
  startedAt: string | null,
  progress: number,
  status: string,
): EtaResult {
  if (
    !startedAt ||
    progress <= 2 ||
    progress >= 100 ||
    status !== "processing"
  ) {
    return { kind: "none" };
  }
  const start = new Date(startedAt).getTime();
  if (Number.isNaN(start)) return { kind: "none" };
  const elapsed = Date.now() - start;
  if (elapsed < 5_000) return { kind: "none" };
  const totalEst = elapsed / (progress / 100);
  const remainingMs = Math.max(0, totalEst - elapsed);
  if (remainingMs < 1_000) return { kind: "none" };
  const remSec = Math.round(remainingMs / 1_000);
  if (remSec < 60) return { kind: "seconds", seconds: remSec };
  const remMin = Math.round(remSec / 60);
  if (remMin < 60) return { kind: "minutes", minutes: remMin };
  const hours = Math.floor(remMin / 60);
  return { kind: "hours", hours, minutes: remMin % 60 };
}

/**
 * Badge "Duplicazione" sulla riga del corso target durante il job.
 * UX:
 *   - Header con bandiera + nome lingua nativo + step indicator
 *     ("5/7") con tooltip pipeline + bottone annulla
 *   - Phase label corrente con spinner animato
 *   - (opzionale) Sotto-progresso "X/Y lezioni completate"
 *     + ETA stimato ("~4 min rimanenti")
 *   - Progress bar con animazione shimmer durante il processing
 *   - Percentuale prominente
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

  // Tick locale ogni 5s per ri-calcolare l'ETA tra un polling list
  // e l'altro (il polling corsi è ogni 3s in produzione). Senza
  // questo tick, l'ETA si aggiornerebbe SOLO quando arriva un nuovo
  // payload del job — accettabile ma meno "vivo".
  const [, setTick] = useState(0);
  useEffect(() => {
    if (job.status !== "processing") return;
    const id = window.setInterval(() => setTick((n) => n + 1), 5_000);
    return () => window.clearInterval(id);
  }, [job.status]);

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

  // Phase key effettiva con fallback.
  const phaseKey: string =
    job.progress_phase ??
    (job.status === "pending"
      ? "pending"
      : job.progress >= 95
        ? "finalizing"
        : "pending");

  const phaseLabel = t(
    `courses.duplicate.badge.phases.${phaseKey}`,
    phaseKey.replace(/_/g, " "),
  );

  const stepIdx = PHASE_ORDER.indexOf(phaseKey);
  const currentStep = stepIdx >= 0 ? stepIdx + 1 : 0;
  const totalSteps = PHASE_ORDER.length;
  const showStep = currentStep > 0 && job.status === "processing";

  const isActive = job.status === "processing" || job.status === "pending";

  const eta = computeEta(job.started_at, job.progress, job.status);

  // Stringa "sotto-progresso + ETA" mostrata sotto la phase label.
  const subProgressBits: string[] = [];
  if (job.progress_detail) subProgressBits.push(job.progress_detail);
  if (eta.kind === "seconds")
    subProgressBits.push(
      t("courses.duplicate.badge.etaSeconds", { seconds: eta.seconds }),
    );
  else if (eta.kind === "minutes")
    subProgressBits.push(
      t("courses.duplicate.badge.etaMinutes", { minutes: eta.minutes }),
    );
  else if (eta.kind === "hours")
    subProgressBits.push(
      t("courses.duplicate.badge.etaHours", {
        hours: eta.hours,
        minutes: eta.minutes,
      }),
    );

  return (
    <TooltipProvider delayDuration={150}>
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
              {targetLang?.name_native ??
                job.target_language_code.toUpperCase()}
            </span>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {showStep && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    className="cursor-help rounded px-1 text-[10px] font-medium tabular-nums text-muted-foreground hover:bg-muted hover:text-foreground"
                    aria-label={t("courses.duplicate.badge.step", {
                      current: currentStep,
                      total: totalSteps,
                    })}
                  >
                    {currentStep}/{totalSteps}
                  </button>
                </TooltipTrigger>
                <TooltipContent
                  side="top"
                  align="end"
                  className="max-w-[280px] p-0"
                >
                  <PipelineStepsList
                    currentStep={currentStep}
                    titleLabel={t("courses.duplicate.badge.stepsTitle")}
                  />
                </TooltipContent>
              </Tooltip>
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

        {/* Riga 2: phase label + spinner */}
        <div className="flex items-center gap-1.5">
          {isActive && (
            <Loader2 className="size-3 shrink-0 animate-spin text-primary" />
          )}
          <span className="truncate text-xs text-foreground">{phaseLabel}</span>
        </div>

        {/* Riga 3 (opzionale): sotto-progresso + ETA */}
        {subProgressBits.length > 0 && (
          <div className="text-[11px] text-muted-foreground">
            {subProgressBits.join(" • ")}
          </div>
        )}

        {/* Riga 4: progress bar + percentuale.
            Shimmer attivo SOLO durante processing — quando ready/failed
            la barra resta statica (niente animazione su stato finale). */}
        <div className="flex items-center gap-2">
          <Progress
            value={job.progress}
            className={cn(
              "h-2 flex-1",
              isActive && "progress-shimmer",
            )}
          />
          <span className="min-w-[2.5rem] text-right text-sm font-semibold tabular-nums text-foreground">
            {job.progress}%
          </span>
        </div>
      </div>
    </TooltipProvider>
  );
}

/**
 * Lista delle fasi mostrata nel tooltip dello step indicator.
 * Le fasi prima della corrente hanno Check, la corrente ha lo
 * spinner, quelle future sono grigie.
 */
function PipelineStepsList({
  currentStep,
  titleLabel,
}: {
  currentStep: number;
  titleLabel: string;
}) {
  const { t } = useTranslation();
  return (
    <div className="rounded-md bg-foreground text-background">
      <div className="border-b border-background/15 px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wider">
        {titleLabel}
      </div>
      <ol className="flex flex-col py-1.5">
        {PHASE_ORDER.map((phase, idx) => {
          const step = idx + 1;
          const isDone = step < currentStep;
          const isCurrent = step === currentStep;
          const label = t(`courses.duplicate.badge.phases.${phase}`, {
            defaultValue: phase.replace(/_/g, " "),
          });
          return (
            <li
              key={phase}
              className={cn(
                "flex items-center gap-2 px-3 py-1 text-xs",
                isCurrent && "font-semibold",
                !isCurrent && !isDone && "opacity-50",
              )}
            >
              <span className="grid size-4 shrink-0 place-items-center">
                {isDone ? (
                  <Check className="size-3.5" />
                ) : isCurrent ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <span className="size-1.5 rounded-full bg-background/40" />
                )}
              </span>
              <span className="flex-1">{label}</span>
              <span className="shrink-0 text-[10px] tabular-nums opacity-70">
                {step}/{PHASE_ORDER.length}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
