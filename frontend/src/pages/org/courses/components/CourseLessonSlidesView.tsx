import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Hourglass,
  Loader2,
  MoreHorizontal,
  Pencil,
  RotateCcw,
  Sparkles,
  StopCircle,
} from "lucide-react";

import {
  coursesApi,
  type CourseLessonOut,
  type CourseModuleOut,
  type CourseOut,
  type LessonSlidesStatus,
  type LessonSlidesUpdateInput,
} from "@/api/courses";
import { ApprovalBadge } from "@/components/shared/ApprovalBadge";
import { StalenessAlert } from "@/components/shared/StalenessAlert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Progress } from "@/components/ui/progress";
import { useBatchEta } from "@/hooks/useBatchEta";
import { extractApiError } from "@/lib/errors";
import { formatDuration } from "@/lib/formatDuration";
import { isSlidesStale } from "@/lib/staleness";

import { LessonSlidesEditDialog } from "./LessonSlidesEditDialog";
import { LessonSlidesView } from "./LessonSlidesView";
import {
  LessonSlidesGenerateDialog,
  type LessonSlidesGenerateMode,
} from "./LessonSlidesGenerateDialog";

interface Props {
  course: CourseOut;
  canEdit: boolean;
  canGenerate: boolean;
  orgId: string;
}

type GenerateDialogState =
  | { kind: "closed" }
  | {
      kind: "open";
      mode: LessonSlidesGenerateMode;
      lessonId?: string;
      lessonLabel?: string;
    };

type EditDialogState =
  | { kind: "closed" }
  | { kind: "open"; lesson: CourseLessonOut };

/**
 * Vista del tab "Slide" (Fase 4 §7). Gestisce trigger generazione (per
 * lezione e batch), approval (per lezione e batch), cancel, e il
 * rendering espandibile delle slide generate.
 *
 * Edit manuale: Step 6 aggiungerà `LessonSlidesEditDialog`.
 */
export function CourseLessonSlidesView({
  course,
  canEdit,
  canGenerate,
  orgId,
}: Props) {
  const { t } = useTranslation();
  const qc = useQueryClient();

  const [generateDialog, setGenerateDialog] = useState<GenerateDialogState>({
    kind: "closed",
  });
  const [editDialog, setEditDialog] = useState<EditDialogState>({
    kind: "closed",
  });
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const detailKey = ["courses", "detail", orgId, course.id];
  const setCache = (fresh: CourseOut) => {
    qc.setQueryData(detailKey, fresh);
    qc.invalidateQueries({ queryKey: detailKey });
    qc.invalidateQueries({ queryKey: ["courses", "list", orgId] });
  };

  const allLessons = useMemo(
    () => course.modules.flatMap((m) => m.lessons),
    [course.modules],
  );

  const moduleLabel = (code: string) => {
    const m = code.match(/^M(\d+)$/);
    return m ? t("courses.architecture.moduleLabel", { n: m[1] }) : code;
  };
  const lessonLabel = (code: string) => {
    const m = code.match(/^M\d+\.L(\d+)$/);
    return m ? t("courses.architecture.lessonLabel", { n: m[1] }) : code;
  };

  // Aggregate progress sulle slide.
  const aggregate = useMemo(() => {
    const total = allLessons.length;
    if (total === 0) {
      return {
        completed: 0,
        total: 0,
        percent: 0,
        activeCount: 0,
        failedCount: 0,
      };
    }
    let sum = 0;
    let completed = 0;
    let activeCount = 0;
    let failedCount = 0;
    for (const l of allLessons) {
      const status = l.slides_status;
      if (status === "ready" || status === "approved") {
        sum += 100;
        completed += 1;
      } else if (status === "processing" || status === "pending") {
        sum += l.slides_progress || 0;
        activeCount += 1;
      } else if (status === "failed") {
        sum += 0;
        failedCount += 1;
      }
    }
    return {
      completed,
      total,
      percent: Math.round(sum / total),
      activeCount,
      failedCount,
    };
  }, [allLessons]);

  const anyActive = aggregate.activeCount > 0;

  const eta = useBatchEta(
    allLessons.map((l) => ({
      status: l.slides_status,
      completedAt: l.slides_generated_at,
    })),
  );

  const allReadyOrApproved =
    allLessons.length > 0 &&
    allLessons.every(
      (l) => l.slides_status === "ready" || l.slides_status === "approved",
    );
  const allApproved =
    allLessons.length > 0 &&
    allLessons.every((l) => l.slides_status === "approved");
  const someEverGenerated = allLessons.some((l) =>
    ["ready", "approved", "failed"].includes(l.slides_status),
  );
  const missingCount = allLessons.filter(
    (l) =>
      l.slides_status === "empty" &&
      (l.content_status === "ready" || l.content_status === "approved"),
  ).length;
  const showGenerateMissing =
    missingCount > 0 && missingCount < allLessons.length && someEverGenerated;

  // Pre-condizione: serve content ready/approved su almeno una lezione.
  const eligibleForGen = allLessons.filter(
    (l) => l.content_status === "ready" || l.content_status === "approved",
  ).length;

  // ---------- Mutations ----------

  const generateAllMut = useMutation({
    mutationFn: (hint: string | null) =>
      coursesApi.lessonSlides.generateAll(orgId, course.id, hint),
    onSuccess: (fresh) => {
      setCache(fresh);
      setGenerateDialog({ kind: "closed" });
      toast.success(t("courses.lessonsSlides.toast.batchStarted"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.lessonsSlides.toast.error"),
      ),
  });

  const generateMissingMut = useMutation({
    mutationFn: () =>
      coursesApi.lessonSlides.generateMissing(orgId, course.id),
    onSuccess: (fresh) => {
      setCache(fresh);
      toast.success(t("courses.lessonsSlides.toast.batchStarted"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.lessonsSlides.toast.error"),
      ),
  });

  const generateLessonMut = useMutation({
    mutationFn: ({
      lessonId,
      hint,
    }: {
      lessonId: string;
      hint: string | null;
    }) =>
      coursesApi.lessonSlides.generateLesson(orgId, course.id, lessonId, hint),
    onSuccess: (fresh) => {
      setCache(fresh);
      setGenerateDialog({ kind: "closed" });
      toast.success(t("courses.lessonsSlides.toast.lessonStarted"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.lessonsSlides.toast.error"),
      ),
  });

  const approveLessonMut = useMutation({
    mutationFn: (lessonId: string) =>
      coursesApi.lessonSlides.approveLesson(orgId, course.id, lessonId),
    onSuccess: (fresh) => {
      setCache(fresh);
      toast.success(t("courses.lessonsSlides.toast.lessonApproved"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.lessonsSlides.toast.error"),
      ),
  });

  const approveAllMut = useMutation({
    mutationFn: () => coursesApi.lessonSlides.approveAll(orgId, course.id),
    onSuccess: (fresh) => {
      setCache(fresh);
      toast.success(t("courses.lessonsSlides.toast.allApproved"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.lessonsSlides.toast.error"),
      ),
  });

  const cancelAllMut = useMutation({
    mutationFn: () => coursesApi.lessonSlides.cancelAll(orgId, course.id),
    onSuccess: (fresh) => {
      setCache(fresh);
      toast.success(t("courses.lessonsSlides.toast.cancelled"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.lessonsSlides.toast.error"),
      ),
  });

  const updateLessonMut = useMutation({
    mutationFn: ({
      lessonId,
      payload,
    }: {
      lessonId: string;
      payload: LessonSlidesUpdateInput;
    }) =>
      coursesApi.lessonSlides.updateLesson(orgId, course.id, lessonId, payload),
    onSuccess: (fresh) => {
      setCache(fresh);
      setEditDialog({ kind: "closed" });
      toast.success(t("courses.lessonsSlides.toast.lessonUpdated"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.lessonsSlides.toast.error"),
      ),
  });

  // Empty state: nessuna lezione con content pronto → invita a Fase 3.
  if (eligibleForGen === 0) {
    return (
      <Card>
        <CardContent className="py-10 text-center">
          <Hourglass className="mx-auto size-8 text-muted-foreground" />
          <p className="mt-4 text-sm text-muted-foreground">
            {t("courses.lessonsSlides.contentNotReady")}
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-5">
      {/* === Header con aggregate progress === */}
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="space-y-1">
              <h3 className="text-base font-semibold">
                {t("courses.lessonsSlides.title")}
              </h3>
              <p className="text-sm text-muted-foreground">
                {t("courses.lessonsSlides.description")}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {canGenerate && anyActive && (
                <Button
                  variant="destructive"
                  onClick={() => cancelAllMut.mutate()}
                  disabled={cancelAllMut.isPending}
                >
                  <StopCircle className="size-4" />
                  {t("courses.lessonsSlides.cancelAll")}
                </Button>
              )}
              {canGenerate && (
                <Button
                  variant={someEverGenerated ? "outline" : "default"}
                  onClick={() =>
                    setGenerateDialog({
                      kind: "open",
                      mode: someEverGenerated
                        ? "regenerate-all"
                        : "generate-all",
                    })
                  }
                  disabled={anyActive || generateAllMut.isPending}
                >
                  <Sparkles className="size-4" />
                  {someEverGenerated
                    ? t("courses.lessonsSlides.regenerateAll")
                    : t("courses.lessonsSlides.generateAll")}
                </Button>
              )}
              {canGenerate && showGenerateMissing && (
                <Button
                  variant="default"
                  onClick={() => generateMissingMut.mutate()}
                  disabled={anyActive || generateMissingMut.isPending}
                >
                  <Sparkles className="size-4" />
                  {t("courses.lessonsSlides.generateMissing", {
                    count: missingCount,
                  })}
                </Button>
              )}
              {canGenerate && allReadyOrApproved && !allApproved && (
                <Button
                  variant="default"
                  onClick={() => approveAllMut.mutate()}
                  disabled={approveAllMut.isPending}
                >
                  <CheckCircle2 className="size-4" />
                  {t("courses.lessonsSlides.approveAll")}
                </Button>
              )}
            </div>
          </div>
        </CardHeader>
        {(anyActive || aggregate.completed > 0) && (
          <CardContent className="space-y-2">
            <div className="flex items-baseline justify-between text-sm">
              <span className="font-medium">
                {t("courses.lessonsSlides.aggregate.label", {
                  completed: aggregate.completed,
                  total: aggregate.total,
                })}
              </span>
              <span className="font-mono tabular-nums text-muted-foreground">
                {aggregate.percent}%
              </span>
            </div>
            <Progress value={aggregate.percent} />
            {anyActive && (eta.etaMs !== null || eta.avgPerTaskMs !== null) && (
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                {eta.etaMs !== null && (
                  <span className="font-medium text-foreground">
                    {t("courses.lessonsSlides.aggregate.eta", {
                      time: formatDuration(eta.etaMs),
                    })}
                  </span>
                )}
                {eta.avgPerTaskMs !== null && (
                  <span>
                    {t("courses.lessonsSlides.aggregate.avgPerTask", {
                      time: formatDuration(eta.avgPerTaskMs),
                    })}
                  </span>
                )}
              </div>
            )}
            {aggregate.failedCount > 0 && (
              <div className="flex items-center gap-2 text-xs text-destructive">
                <AlertCircle className="size-3.5" />
                {t("courses.lessonsSlides.aggregate.failed", {
                  count: aggregate.failedCount,
                })}
              </div>
            )}
          </CardContent>
        )}
      </Card>

      {/* === Lista moduli con lezioni === */}
      <div className="space-y-3">
        {course.modules.map((module) => (
          <ModuleSlidesCard
            key={module.id}
            module={module}
            canEdit={canEdit}
            canGenerate={canGenerate}
            moduleLabel={moduleLabel(module.module_code)}
            lessonLabel={lessonLabel}
            expanded={expanded}
            onToggle={(id) =>
              setExpanded((prev) => {
                const next = new Set(prev);
                if (next.has(id)) next.delete(id);
                else next.add(id);
                return next;
              })
            }
            onGenerate={(mode, lessonId, llabel) =>
              setGenerateDialog({
                kind: "open",
                mode,
                lessonId,
                lessonLabel: llabel,
              })
            }
            onApprove={(lessonId) => approveLessonMut.mutate(lessonId)}
            approvingId={
              approveLessonMut.isPending
                ? (approveLessonMut.variables as string | undefined)
                : undefined
            }
            onEdit={(lesson) => setEditDialog({ kind: "open", lesson })}
          />
        ))}
      </div>

      {/* === Dialogs === */}
      {generateDialog.kind === "open" && (
        <LessonSlidesGenerateDialog
          open={true}
          mode={generateDialog.mode}
          lessonLabel={generateDialog.lessonLabel}
          isPending={generateAllMut.isPending || generateLessonMut.isPending}
          onClose={() => setGenerateDialog({ kind: "closed" })}
          onConfirm={(hint) => {
            if (
              generateDialog.mode === "generate-all" ||
              generateDialog.mode === "regenerate-all"
            ) {
              generateAllMut.mutate(hint);
            } else if (generateDialog.lessonId) {
              generateLessonMut.mutate({
                lessonId: generateDialog.lessonId,
                hint,
              });
            }
          }}
        />
      )}

      {editDialog.kind === "open" && editDialog.lesson.slides_raw && (
        <LessonSlidesEditDialog
          open={true}
          lessonLabel={lessonLabel(editDialog.lesson.lesson_code)}
          initial={editDialog.lesson.slides_raw}
          contentRaw={editDialog.lesson.content_raw}
          isPending={updateLessonMut.isPending}
          onClose={() => setEditDialog({ kind: "closed" })}
          onSubmit={(payload) =>
            updateLessonMut.mutate({
              lessonId: editDialog.lesson.id,
              payload,
            })
          }
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ModuleSlidesCard
// ---------------------------------------------------------------------------

interface ModuleSlidesCardProps {
  module: CourseModuleOut;
  canEdit: boolean;
  canGenerate: boolean;
  moduleLabel: string;
  lessonLabel: (code: string) => string;
  expanded: Set<string>;
  onToggle: (id: string) => void;
  onGenerate: (
    mode: LessonSlidesGenerateMode,
    lessonId?: string,
    lessonLabel?: string,
  ) => void;
  onApprove: (lessonId: string) => void;
  approvingId: string | undefined;
  onEdit: (lesson: CourseLessonOut) => void;
}

function ModuleSlidesCard({
  module,
  canEdit,
  canGenerate,
  moduleLabel,
  lessonLabel,
  expanded,
  onToggle,
  onGenerate,
  onApprove,
  approvingId,
  onEdit,
}: ModuleSlidesCardProps) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="font-mono text-xs">
            {moduleLabel}
          </Badge>
          <h4 className="text-sm font-semibold">{module.title}</h4>
        </div>
      </CardHeader>
      {module.lessons.length > 0 && (
        <CardContent className="space-y-2">
          {module.lessons.map((lesson) => (
            <LessonSlidesRow
              key={lesson.id}
              lesson={lesson}
              parentModule={module}
              expanded={expanded.has(lesson.id)}
              onToggle={() => onToggle(lesson.id)}
              canEdit={canEdit}
              canGenerate={canGenerate}
              onGenerate={(mode) =>
                onGenerate(mode, lesson.id, lessonLabel(lesson.lesson_code))
              }
              onApprove={() => onApprove(lesson.id)}
              isApproving={approvingId === lesson.id}
              lessonLabel={lessonLabel(lesson.lesson_code)}
              onEdit={() => onEdit(lesson)}
            />
          ))}
        </CardContent>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// LessonSlidesRow
// ---------------------------------------------------------------------------

interface LessonSlidesRowProps {
  lesson: CourseLessonOut;
  parentModule: CourseModuleOut;
  expanded: boolean;
  onToggle: () => void;
  canEdit: boolean;
  canGenerate: boolean;
  onGenerate: (mode: LessonSlidesGenerateMode) => void;
  onApprove: () => void;
  isApproving: boolean;
  lessonLabel: string;
  onEdit: () => void;
}

function LessonSlidesRow({
  lesson,
  parentModule,
  expanded,
  onToggle,
  canEdit,
  canGenerate,
  onGenerate,
  onApprove,
  isApproving,
  lessonLabel,
  onEdit,
}: LessonSlidesRowProps) {
  const { t } = useTranslation();
  const status = lesson.slides_status;
  const isProcessing = status === "pending" || status === "processing";
  const isReady = status === "ready" || status === "approved";

  // Pre-condizione: per generare slide servono contenuti ready/approved.
  const canStartGeneration =
    canGenerate &&
    (lesson.content_status === "ready" || lesson.content_status === "approved");

  const stale = isSlidesStale(lesson, parentModule);

  // CTA primaria — l'azione "next-step" più ovvia per lo stato corrente.
  const primaryCta = (() => {
    if (!canStartGeneration) return null;
    if (status === "empty") {
      return (
        <Button size="sm" onClick={() => onGenerate("generate-lesson")}>
          <Sparkles className="size-3.5" />
          {t("courses.lessonsSlides.lesson.generate")}
        </Button>
      );
    }
    if (status === "failed") {
      return (
        <Button
          size="sm"
          variant="outline"
          onClick={() => onGenerate("generate-lesson")}
        >
          <RotateCcw className="size-3.5" />
          {t("courses.lessonsSlides.lesson.retry")}
        </Button>
      );
    }
    if (status === "ready") {
      return (
        <Button size="sm" onClick={onApprove} disabled={isApproving}>
          <CheckCircle2 className="size-3.5" />
          {t("courses.lessonsSlides.lesson.approve")}
        </Button>
      );
    }
    return null;
  })();

  // Voci kebab: rigenera quando ready/approved.
  const canRegenerate =
    canStartGeneration && (status === "ready" || status === "approved");
  const hasMenuItems = canRegenerate;

  const slidesCount = lesson.slides_raw?.total_slides ?? 0;

  return (
    <div className="rounded-md border bg-muted/10">
      <div className="flex w-full items-center gap-2 px-3 py-2">
        <button
          type="button"
          className="flex flex-1 items-center gap-2 text-left"
          onClick={onToggle}
        >
          {expanded ? (
            <ChevronDown className="size-4 shrink-0" />
          ) : (
            <ChevronRight className="size-4 shrink-0" />
          )}
          <Badge variant="outline" className="font-mono text-[11px]">
            {lessonLabel}
          </Badge>
          <span className="flex-1 truncate text-sm font-medium">
            {lesson.title}
          </span>
          {isReady && slidesCount > 0 && (
            <Badge variant="muted" className="text-[10px]">
              {t("courses.lessonsSlides.lesson.slidesCount", {
                count: slidesCount,
              })}
            </Badge>
          )}
        </button>
        <LessonSlidesStatusBadge status={status} />
        {status === "approved" && (
          <ApprovalBadge
            level="lessonSlides"
            approvedAt={lesson.slides_approved_at}
          />
        )}
        {primaryCta}
        {canEdit && isReady && lesson.slides_raw && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7"
            onClick={onEdit}
            title={t("courses.lessonsSlides.lesson.edit")}
          >
            <Pencil className="size-3.5" />
          </Button>
        )}
        {hasMenuItems && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="size-7"
                aria-label={t("common.moreActions")}
              >
                <MoreHorizontal className="size-3.5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {canRegenerate && (
                <DropdownMenuItem
                  onClick={() => onGenerate("regenerate-lesson")}
                >
                  <Sparkles className="size-3.5" />
                  {t("courses.lessonsSlides.lesson.regenerate")}
                </DropdownMenuItem>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>

      {/* Stale alert */}
      {stale && !isProcessing && (
        <div className="border-t px-4 py-3">
          <StalenessAlert
            kind="slides"
            variant="inline"
            onAction={
              canStartGeneration ? () => onGenerate("regenerate-lesson") : undefined
            }
            hideAction={!canStartGeneration}
          />
        </div>
      )}

      {/* Progress live */}
      {isProcessing && (
        <div className="space-y-2 border-t px-4 py-3">
          <div className="flex items-baseline justify-between text-xs">
            <span className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" />
              {lesson.slides_progress_phase
                ? t(
                    `courses.lessonsSlides.phases.${lesson.slides_progress_phase}`,
                    {
                      defaultValue: lesson.slides_progress_phase,
                    },
                  )
                : t("courses.lessonsSlides.phases.preparing_prompt")}
            </span>
            <span className="font-mono tabular-nums text-muted-foreground">
              {lesson.slides_progress}%
            </span>
          </div>
          <Progress value={lesson.slides_progress} />
        </div>
      )}

      {/* Render slide se ready/approved e expanded */}
      {expanded && isReady && lesson.slides_raw && (
        <div className="border-t px-4 py-4">
          <LessonSlidesView
            slides={lesson.slides_raw}
            contentRaw={lesson.content_raw}
          />
        </div>
      )}
    </div>
  );
}

function LessonSlidesStatusBadge({ status }: { status: LessonSlidesStatus }) {
  const { t } = useTranslation();
  // Nascondi quando ready/approved (l'utente ha visual cue dal numero
  // slide + approval badge). Mostra solo per stati non-finali utili:
  // pending, processing, failed.
  if (status === "empty" || status === "ready" || status === "approved")
    return null;
  const variant = (() => {
    switch (status) {
      case "pending":
      case "processing":
        return "warning" as const;
      case "failed":
        return "destructive" as const;
    }
  })();
  return (
    <Badge variant={variant} className="text-[10px]">
      {t(`courses.lessonsSlides.statuses.${status}`)}
    </Badge>
  );
}

export default CourseLessonSlidesView;
