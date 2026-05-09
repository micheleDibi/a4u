import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Download,
  FileText,
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
  type LessonSpeechStatus,
  type LessonSpeechUpdateInput,
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
import { isSpeechPdfStale, isSpeechStale } from "@/lib/staleness";

import { LessonSpeechEditDialog } from "./LessonSpeechEditDialog";
import { LessonSpeechView } from "./LessonSpeechView";
import {
  LessonSpeechGenerateDialog,
  type LessonSpeechGenerateMode,
} from "./LessonSpeechGenerateDialog";
import {
  LessonSpeechPdfExportDialog,
  type LessonSpeechPdfExportMode,
} from "./LessonSpeechPdfExportDialog";

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
      mode: LessonSpeechGenerateMode;
      lessonId?: string;
      lessonLabel?: string;
    };

type EditDialogState =
  | { kind: "closed" }
  | { kind: "open"; lesson: CourseLessonOut };

type PdfExportDialogState =
  | { kind: "closed" }
  | {
      kind: "open";
      mode: LessonSpeechPdfExportMode;
      lessonId?: string;
      lessonLabel?: string;
      initialTemplateId?: string | null;
    };

/**
 * Vista del tab "Discorso" (Fase 5 §8). Mirror della vista slides ma
 * scoped sul `speech_*` della lezione. Pre-condizione: serve almeno
 * una lezione con slide ready/approved.
 *
 * PDF discorso: gli hook sono già pronti come scaffold (kebab "Esporta
 * PDF"), Step 7 aggiungerà mutations + dialog dedicati.
 */
export function CourseLessonSpeechView({
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
  const [pdfExportDialog, setPdfExportDialog] = useState<PdfExportDialogState>({
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

  // Aggregate progress sul discorso.
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
      const status = l.speech_status;
      if (status === "ready" || status === "approved") {
        sum += 100;
        completed += 1;
      } else if (status === "processing" || status === "pending") {
        sum += l.speech_progress || 0;
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
      status: l.speech_status,
      completedAt: l.speech_generated_at,
    })),
  );

  const allReadyOrApproved =
    allLessons.length > 0 &&
    allLessons.every(
      (l) => l.speech_status === "ready" || l.speech_status === "approved",
    );
  const allApproved =
    allLessons.length > 0 &&
    allLessons.every((l) => l.speech_status === "approved");
  const someEverGenerated = allLessons.some((l) =>
    ["ready", "approved", "failed"].includes(l.speech_status),
  );
  const missingCount = allLessons.filter(
    (l) =>
      l.speech_status === "empty" &&
      (l.slides_status === "ready" || l.slides_status === "approved"),
  ).length;
  const showGenerateMissing =
    missingCount > 0 && missingCount < allLessons.length && someEverGenerated;

  // Pre-condizione: serve slides ready/approved su almeno una lezione.
  const eligibleForGen = allLessons.filter(
    (l) => l.slides_status === "ready" || l.slides_status === "approved",
  ).length;

  // ---------- Mutations ----------

  const generateAllMut = useMutation({
    mutationFn: (hint: string | null) =>
      coursesApi.lessonSpeech.generateAll(orgId, course.id, hint),
    onSuccess: (fresh) => {
      setCache(fresh);
      setGenerateDialog({ kind: "closed" });
      toast.success(t("courses.lessonsSpeech.toast.batchStarted"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.lessonsSpeech.toast.error"),
      ),
  });

  const generateMissingMut = useMutation({
    mutationFn: () =>
      coursesApi.lessonSpeech.generateMissing(orgId, course.id),
    onSuccess: (fresh) => {
      setCache(fresh);
      toast.success(t("courses.lessonsSpeech.toast.batchStarted"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.lessonsSpeech.toast.error"),
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
      coursesApi.lessonSpeech.generateLesson(orgId, course.id, lessonId, hint),
    onSuccess: (fresh) => {
      setCache(fresh);
      setGenerateDialog({ kind: "closed" });
      toast.success(t("courses.lessonsSpeech.toast.lessonStarted"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.lessonsSpeech.toast.error"),
      ),
  });

  const approveLessonMut = useMutation({
    mutationFn: (lessonId: string) =>
      coursesApi.lessonSpeech.approveLesson(orgId, course.id, lessonId),
    onSuccess: (fresh) => {
      setCache(fresh);
      toast.success(t("courses.lessonsSpeech.toast.lessonApproved"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.lessonsSpeech.toast.error"),
      ),
  });

  const approveAllMut = useMutation({
    mutationFn: () => coursesApi.lessonSpeech.approveAll(orgId, course.id),
    onSuccess: (fresh) => {
      setCache(fresh);
      toast.success(t("courses.lessonsSpeech.toast.allApproved"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.lessonsSpeech.toast.error"),
      ),
  });

  const cancelAllMut = useMutation({
    mutationFn: () => coursesApi.lessonSpeech.cancelAll(orgId, course.id),
    onSuccess: (fresh) => {
      setCache(fresh);
      toast.success(t("courses.lessonsSpeech.toast.cancelled"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.lessonsSpeech.toast.error"),
      ),
  });

  const updateLessonMut = useMutation({
    mutationFn: ({
      lessonId,
      payload,
    }: {
      lessonId: string;
      payload: LessonSpeechUpdateInput;
    }) =>
      coursesApi.lessonSpeech.updateLesson(orgId, course.id, lessonId, payload),
    onSuccess: (fresh) => {
      setCache(fresh);
      setEditDialog({ kind: "closed" });
      toast.success(t("courses.lessonsSpeech.toast.lessonUpdated"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.lessonsSpeech.toast.error"),
      ),
  });

  // === PDF export ===

  const exportPdfMut = useMutation({
    mutationFn: ({
      lessonId,
      templateId,
    }: {
      lessonId: string;
      templateId: string | null;
    }) =>
      coursesApi.lessonSpeechPdf.exportLesson(
        orgId,
        course.id,
        lessonId,
        templateId,
      ),
    onSuccess: (fresh) => {
      setCache(fresh);
      setPdfExportDialog({ kind: "closed" });
      toast.success(t("courses.lessonsSpeechPdf.toast.lessonStarted"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ??
          t("courses.lessonsSpeechPdf.toast.error"),
      ),
  });

  const exportAllPdfMut = useMutation({
    mutationFn: (templateId: string | null) =>
      coursesApi.lessonSpeechPdf.exportAll(orgId, course.id, templateId),
    onSuccess: (fresh) => {
      setCache(fresh);
      setPdfExportDialog({ kind: "closed" });
      toast.success(t("courses.lessonsSpeechPdf.toast.batchStarted"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ??
          t("courses.lessonsSpeechPdf.toast.error"),
      ),
  });

  const cancelAllPdfMut = useMutation({
    mutationFn: () => coursesApi.lessonSpeechPdf.cancelAll(orgId, course.id),
    onSuccess: (fresh) => {
      setCache(fresh);
      toast.success(t("courses.lessonsSpeechPdf.toast.cancelled"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ??
          t("courses.lessonsSpeechPdf.toast.error"),
      ),
  });

  const downloadPdf = async (lesson: CourseLessonOut) => {
    try {
      const { blob, filename } = await coursesApi.lessonSpeechPdf.download(
        orgId,
        course.id,
        lesson.id,
      );
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename ?? `${lesson.lesson_code}_speech.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5_000);
    } catch (err) {
      toast.error(
        extractApiError(err).message ??
          t("courses.lessonsSpeechPdf.toast.error"),
      );
    }
  };

  // PDF aggregate
  const pdfAggregate = useMemo(() => {
    let pdfActive = 0;
    for (const l of allLessons) {
      if (
        l.speech_pdf_status === "pending" ||
        l.speech_pdf_status === "processing"
      )
        pdfActive += 1;
    }
    return { pdfActive };
  }, [allLessons]);
  const anyPdfActive = pdfAggregate.pdfActive > 0;
  const exportablePdfCount = allLessons.filter(
    (l) =>
      (l.speech_status === "ready" || l.speech_status === "approved") &&
      (l.speech_pdf_status === "empty" ||
        l.speech_pdf_status === "ready" ||
        l.speech_pdf_status === "failed" ||
        l.speech_pdf_status === undefined),
  ).length;

  // Empty state: nessuna lezione con slide pronte → invita a Fase 4.
  if (eligibleForGen === 0) {
    return (
      <Card>
        <CardContent className="py-10 text-center">
          <Hourglass className="mx-auto size-8 text-muted-foreground" />
          <p className="mt-4 text-sm text-muted-foreground">
            {t("courses.lessonsSpeech.slidesNotReady")}
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
                {t("courses.lessonsSpeech.title")}
              </h3>
              <p className="text-sm text-muted-foreground">
                {t("courses.lessonsSpeech.description")}
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
                  {t("courses.lessonsSpeech.cancelAll")}
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
                    ? t("courses.lessonsSpeech.regenerateAll")
                    : t("courses.lessonsSpeech.generateAll")}
                </Button>
              )}
              {canGenerate && showGenerateMissing && (
                <Button
                  variant="default"
                  onClick={() => generateMissingMut.mutate()}
                  disabled={anyActive || generateMissingMut.isPending}
                >
                  <Sparkles className="size-4" />
                  {t("courses.lessonsSpeech.generateMissing", {
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
                  {t("courses.lessonsSpeech.approveAll")}
                </Button>
              )}
              {canGenerate && anyPdfActive && (
                <Button
                  variant="destructive"
                  onClick={() => cancelAllPdfMut.mutate()}
                  disabled={cancelAllPdfMut.isPending}
                >
                  <StopCircle className="size-4" />
                  {t("courses.lessonsSpeechPdf.cancelAll")}
                </Button>
              )}
              {canGenerate && exportablePdfCount > 0 && !anyPdfActive && (
                <Button
                  variant="outline"
                  onClick={() =>
                    setPdfExportDialog({ kind: "open", mode: "all" })
                  }
                  disabled={exportAllPdfMut.isPending}
                >
                  <FileText className="size-4" />
                  {t("courses.lessonsSpeechPdf.exportAll", {
                    count: exportablePdfCount,
                  })}
                </Button>
              )}
            </div>
          </div>
        </CardHeader>
        {(anyActive || aggregate.completed > 0) && (
          <CardContent className="space-y-2">
            <div className="flex items-baseline justify-between text-sm">
              <span className="font-medium">
                {t("courses.lessonsSpeech.aggregate.label", {
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
                    {t("courses.lessonsSpeech.aggregate.eta", {
                      time: formatDuration(eta.etaMs),
                    })}
                  </span>
                )}
                {eta.avgPerTaskMs !== null && (
                  <span>
                    {t("courses.lessonsSpeech.aggregate.avgPerTask", {
                      time: formatDuration(eta.avgPerTaskMs),
                    })}
                  </span>
                )}
              </div>
            )}
            {aggregate.failedCount > 0 && (
              <div className="flex items-center gap-2 text-xs text-destructive">
                <AlertCircle className="size-3.5" />
                {t("courses.lessonsSpeech.aggregate.failed", {
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
          <ModuleSpeechCard
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
            onExportPdf={(lessonId, llabel, currentTemplateId) =>
              setPdfExportDialog({
                kind: "open",
                mode: "single",
                lessonId,
                lessonLabel: llabel,
                initialTemplateId: currentTemplateId,
              })
            }
            onDownloadPdf={(lesson) => {
              void downloadPdf(lesson);
            }}
            exportingPdfId={
              exportPdfMut.isPending
                ? (exportPdfMut.variables as { lessonId: string } | undefined)
                    ?.lessonId
                : undefined
            }
          />
        ))}
      </div>

      {/* === Dialogs === */}
      {generateDialog.kind === "open" && (
        <LessonSpeechGenerateDialog
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

      {editDialog.kind === "open" && editDialog.lesson.speech_raw && (
        <LessonSpeechEditDialog
          open={true}
          lessonLabel={lessonLabel(editDialog.lesson.lesson_code)}
          initial={editDialog.lesson.speech_raw}
          slidesRaw={editDialog.lesson.slides_raw}
          targetDurationSeconds={course.lesson_duration_minutes * 60}
          languageCode={course.language_code}
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

      {pdfExportDialog.kind === "open" && (
        <LessonSpeechPdfExportDialog
          open={true}
          mode={pdfExportDialog.mode}
          lessonLabel={pdfExportDialog.lessonLabel}
          exportableCount={exportablePdfCount}
          initialTemplateId={pdfExportDialog.initialTemplateId}
          orgId={orgId}
          isPending={exportPdfMut.isPending || exportAllPdfMut.isPending}
          onClose={() => setPdfExportDialog({ kind: "closed" })}
          onConfirm={(templateId) => {
            if (pdfExportDialog.mode === "single" && pdfExportDialog.lessonId) {
              exportPdfMut.mutate({
                lessonId: pdfExportDialog.lessonId,
                templateId,
              });
            } else if (pdfExportDialog.mode === "all") {
              exportAllPdfMut.mutate(templateId);
            }
          }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ModuleSpeechCard
// ---------------------------------------------------------------------------

interface ModuleSpeechCardProps {
  module: CourseModuleOut;
  canEdit: boolean;
  canGenerate: boolean;
  moduleLabel: string;
  lessonLabel: (code: string) => string;
  expanded: Set<string>;
  onToggle: (id: string) => void;
  onGenerate: (
    mode: LessonSpeechGenerateMode,
    lessonId?: string,
    lessonLabel?: string,
  ) => void;
  onApprove: (lessonId: string) => void;
  approvingId: string | undefined;
  onEdit: (lesson: CourseLessonOut) => void;
  onExportPdf: (
    lessonId: string,
    lessonLabel: string,
    currentTemplateId: string | null,
  ) => void;
  onDownloadPdf: (lesson: CourseLessonOut) => void;
  exportingPdfId: string | undefined;
}

function ModuleSpeechCard({
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
  onExportPdf,
  onDownloadPdf,
  exportingPdfId,
}: ModuleSpeechCardProps) {
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
            <LessonSpeechRow
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
              onExportPdf={() =>
                onExportPdf(
                  lesson.id,
                  lessonLabel(lesson.lesson_code),
                  lesson.speech_pdf_template_id ?? null,
                )
              }
              onDownloadPdf={() => onDownloadPdf(lesson)}
              isExportingPdf={exportingPdfId === lesson.id}
            />
          ))}
        </CardContent>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// LessonSpeechRow
// ---------------------------------------------------------------------------

interface LessonSpeechRowProps {
  lesson: CourseLessonOut;
  parentModule: CourseModuleOut;
  expanded: boolean;
  onToggle: () => void;
  canEdit: boolean;
  canGenerate: boolean;
  onGenerate: (mode: LessonSpeechGenerateMode) => void;
  onApprove: () => void;
  isApproving: boolean;
  lessonLabel: string;
  onEdit: () => void;
  onExportPdf: () => void;
  onDownloadPdf: () => void;
  isExportingPdf: boolean;
}

function LessonSpeechRow({
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
  onExportPdf,
  onDownloadPdf,
  isExportingPdf,
}: LessonSpeechRowProps) {
  const { t } = useTranslation();
  const status = lesson.speech_status;
  const isProcessing = status === "pending" || status === "processing";
  const isReady = status === "ready" || status === "approved";
  const pdfStatus = lesson.speech_pdf_status;
  const isPdfProcessing =
    pdfStatus === "pending" || pdfStatus === "processing";
  const canExportPdf =
    isReady &&
    (pdfStatus === "empty" ||
      pdfStatus === "ready" ||
      pdfStatus === "failed" ||
      pdfStatus === undefined);
  const pdfStale = isSpeechPdfStale(lesson);

  // Pre-condizione: per generare il discorso servono slide ready/approved.
  const canStartGeneration =
    canGenerate &&
    (lesson.slides_status === "ready" || lesson.slides_status === "approved");

  const stale = isSpeechStale(lesson, parentModule);

  // CTA primaria — l'azione "next-step" più ovvia per lo stato corrente.
  const primaryCta = (() => {
    if (!canStartGeneration) return null;
    if (status === "empty") {
      return (
        <Button size="sm" onClick={() => onGenerate("generate-lesson")}>
          <Sparkles className="size-3.5" />
          {t("courses.lessonsSpeech.lesson.generate")}
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
          {t("courses.lessonsSpeech.lesson.retry")}
        </Button>
      );
    }
    if (status === "ready") {
      return (
        <Button size="sm" onClick={onApprove} disabled={isApproving}>
          <CheckCircle2 className="size-3.5" />
          {t("courses.lessonsSpeech.lesson.approve")}
        </Button>
      );
    }
    return null;
  })();

  const canRegenerate =
    canStartGeneration && (status === "ready" || status === "approved");
  const canRegeneratePdf =
    canGenerate && pdfStatus === "ready" && isReady && !isExportingPdf;
  const hasMenuItems = canRegenerate || canRegeneratePdf;

  // CTA primaria PDF (Scarica / Esporta), quando applicabile.
  const primaryPdfCta = (() => {
    if (!isReady) return null;
    if (isPdfProcessing) {
      return (
        <Button
          size="sm"
          variant="outline"
          disabled
          title={t("courses.lessonsSpeechPdf.lesson.exporting")}
        >
          <Loader2 className="size-3.5 animate-spin" />
          {lesson.speech_pdf_progress ?? 0}%
        </Button>
      );
    }
    if (pdfStatus === "ready") {
      return (
        <Button
          size="sm"
          variant="outline"
          onClick={onDownloadPdf}
          title={t("courses.lessonsSpeechPdf.lesson.download")}
        >
          <Download className="size-3.5" />
          {t("courses.lessonsSpeechPdf.lesson.download")}
        </Button>
      );
    }
    if (canGenerate && canExportPdf) {
      return (
        <Button
          size="sm"
          variant="outline"
          onClick={onExportPdf}
          disabled={isExportingPdf}
        >
          <FileText className="size-3.5" />
          {pdfStatus === "failed"
            ? t("courses.lessonsSpeechPdf.lesson.retry")
            : t("courses.lessonsSpeechPdf.lesson.export")}
        </Button>
      );
    }
    return null;
  })();

  const segmentsCount = lesson.speech_raw?.speech_segments.length ?? 0;

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
          {isReady && segmentsCount > 0 && (
            <Badge variant="muted" className="text-[10px]">
              {t("courses.lessonsSpeech.lesson.segmentsCount", {
                count: segmentsCount,
              })}
            </Badge>
          )}
        </button>
        <LessonSpeechStatusBadge status={status} />
        {status === "approved" && (
          <ApprovalBadge
            level="lessonSpeech"
            approvedAt={lesson.speech_approved_at}
          />
        )}
        {primaryCta}
        {primaryPdfCta}
        {canEdit && isReady && lesson.speech_raw && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7"
            onClick={onEdit}
            title={t("courses.lessonsSpeech.lesson.edit")}
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
                  {t("courses.lessonsSpeech.lesson.regenerate")}
                </DropdownMenuItem>
              )}
              {canRegeneratePdf && (
                <DropdownMenuItem onClick={onExportPdf}>
                  <FileText className="size-3.5" />
                  {t("courses.lessonsSpeechPdf.lesson.regenerate")}
                </DropdownMenuItem>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>

      {/* Stale alerts */}
      {(stale || pdfStale) && !isProcessing && !isPdfProcessing && (
        <div className="space-y-2 border-t px-4 py-3">
          {stale && (
            <StalenessAlert
              kind="speech"
              variant="inline"
              onAction={
                canStartGeneration ? () => onGenerate("regenerate-lesson") : undefined
              }
              hideAction={!canStartGeneration}
            />
          )}
          {pdfStale && (
            <StalenessAlert
              kind="speechPdf"
              variant="inline"
              onAction={canGenerate ? onExportPdf : undefined}
              hideAction={!canGenerate}
            />
          )}
        </div>
      )}

      {/* Progress live */}
      {isProcessing && (
        <div className="space-y-2 border-t px-4 py-3">
          <div className="flex items-baseline justify-between text-xs">
            <span className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" />
              {lesson.speech_progress_phase
                ? t(
                    `courses.lessonsSpeech.phases.${lesson.speech_progress_phase}`,
                    {
                      defaultValue: lesson.speech_progress_phase,
                    },
                  )
                : t("courses.lessonsSpeech.phases.preparing_prompt")}
            </span>
            <span className="font-mono tabular-nums text-muted-foreground">
              {lesson.speech_progress}%
            </span>
          </div>
          <Progress value={lesson.speech_progress} />
        </div>
      )}

      {/* Render discorso se ready/approved e expanded */}
      {expanded && isReady && lesson.speech_raw && (
        <div className="border-t px-4 py-4">
          <LessonSpeechView
            speech={lesson.speech_raw}
            slides={lesson.slides_raw}
          />
        </div>
      )}
    </div>
  );
}

function LessonSpeechStatusBadge({ status }: { status: LessonSpeechStatus }) {
  const { t } = useTranslation();
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
      {t(`courses.lessonsSpeech.statuses.${status}`)}
    </Badge>
  );
}

export default CourseLessonSpeechView;
