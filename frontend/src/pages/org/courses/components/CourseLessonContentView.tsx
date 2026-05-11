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
  type LessonContentStatus,
  type LessonContentUpdateInput,
  type LessonPdfStatus,
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
import { isContentStale, isPdfStale } from "@/lib/staleness";

import { LessonContentEditDialog } from "./LessonContentEditDialog";
import {
  LessonContentGenerateDialog,
  type LessonContentGenerateMode,
} from "./LessonContentGenerateDialog";
import { LessonContentView } from "./LessonContentView";
import {
  LessonPdfExportDialog,
  type LessonPdfExportMode,
} from "./LessonPdfExportDialog";

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
      mode: LessonContentGenerateMode;
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
      mode: LessonPdfExportMode;
      lessonId?: string;
      lessonLabel?: string;
      initialTemplateId?: string | null;
    };

export function CourseLessonContentView({
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

  // Aggregate progress
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
      const status = l.content_status;
      if (status === "ready" || status === "approved") {
        sum += 100;
        completed += 1;
      } else if (status === "processing" || status === "pending") {
        sum += l.content_progress || 0;
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

  // ETA del batch contenuti: derivata dai timestamp di completamento delle
  // lezioni (`content_generated_at`).
  const eta = useBatchEta(
    allLessons.map((l) => ({
      status: l.content_status,
      completedAt: l.content_generated_at,
    })),
  );
  const allReadyOrApproved =
    allLessons.length > 0 &&
    allLessons.every(
      (l) => l.content_status === "ready" || l.content_status === "approved",
    );
  const allApproved =
    allLessons.length > 0 &&
    allLessons.every((l) => l.content_status === "approved");
  const someEverGenerated = allLessons.some((l) =>
    ["ready", "approved", "failed"].includes(l.content_status),
  );
  // Lezioni "vuote": per il bottone "Genera mancanti". Visibile solo
  // quando ci sono lezioni da generare E almeno una è già stata
  // generata (altrimenti l'utente userebbe "Genera tutti").
  const missingCount = allLessons.filter(
    (l) => l.content_status === "empty",
  ).length;
  const showGenerateMissing =
    missingCount > 0 && missingCount < allLessons.length && someEverGenerated;

  // PDF aggregate
  const pdfAggregate = useMemo(() => {
    let pdfActive = 0;
    let pdfReady = 0;
    let pdfFailed = 0;
    for (const l of allLessons) {
      if (l.pdf_status === "pending" || l.pdf_status === "processing")
        pdfActive += 1;
      else if (l.pdf_status === "ready") pdfReady += 1;
      else if (l.pdf_status === "failed") pdfFailed += 1;
    }
    return { pdfActive, pdfReady, pdfFailed };
  }, [allLessons]);
  const anyPdfActive = pdfAggregate.pdfActive > 0;
  const exportableCount = allLessons.filter(
    (l) =>
      (l.content_status === "ready" || l.content_status === "approved") &&
      (l.pdf_status === "empty" ||
        l.pdf_status === "ready" ||
        l.pdf_status === "failed"),
  ).length;

  // ---------- Mutations ----------

  const generateAllMut = useMutation({
    mutationFn: (hint: string | null) =>
      coursesApi.lessonContent.generateAll(orgId, course.id, hint),
    onSuccess: (fresh) => {
      setCache(fresh);
      setGenerateDialog({ kind: "closed" });
      toast.success(t("courses.lessonsContent.toast.batchStarted"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.lessonsContent.toast.error"),
      ),
  });

  const generateMissingMut = useMutation({
    mutationFn: () =>
      coursesApi.lessonContent.generateMissing(orgId, course.id),
    onSuccess: (fresh) => {
      setCache(fresh);
      toast.success(t("courses.lessonsContent.toast.batchStarted"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.lessonsContent.toast.error"),
      ),
  });

  const generateLessonMut = useMutation({
    mutationFn: ({
      lessonId,
      hint,
    }: {
      lessonId: string;
      hint: string | null;
    }) => coursesApi.lessonContent.generateLesson(orgId, course.id, lessonId, hint),
    onSuccess: (fresh) => {
      setCache(fresh);
      setGenerateDialog({ kind: "closed" });
      toast.success(t("courses.lessonsContent.toast.lessonStarted"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.lessonsContent.toast.error"),
      ),
  });

  const approveLessonMut = useMutation({
    mutationFn: (lessonId: string) =>
      coursesApi.lessonContent.approveLesson(orgId, course.id, lessonId),
    onSuccess: (fresh) => {
      setCache(fresh);
      toast.success(t("courses.lessonsContent.toast.lessonApproved"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.lessonsContent.toast.error"),
      ),
  });

  const approveAllMut = useMutation({
    mutationFn: () => coursesApi.lessonContent.approveAll(orgId, course.id),
    onSuccess: (fresh) => {
      setCache(fresh);
      toast.success(t("courses.lessonsContent.toast.allApproved"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.lessonsContent.toast.error"),
      ),
  });

  const cancelAllMut = useMutation({
    mutationFn: () => coursesApi.lessonContent.cancelAll(orgId, course.id),
    onSuccess: (fresh) => {
      setCache(fresh);
      toast.success(t("courses.lessonsContent.toast.cancelled"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.lessonsContent.toast.error"),
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
      coursesApi.lessonPdf.exportLesson(orgId, course.id, lessonId, templateId),
    onSuccess: (fresh) => {
      setCache(fresh);
      setPdfExportDialog({ kind: "closed" });
      toast.success(t("courses.lessonsPdf.toast.lessonStarted"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.lessonsPdf.toast.error"),
      ),
  });

  const exportAllPdfMut = useMutation({
    mutationFn: (templateId: string | null) =>
      coursesApi.lessonPdf.exportAll(orgId, course.id, templateId),
    onSuccess: (fresh) => {
      setCache(fresh);
      setPdfExportDialog({ kind: "closed" });
      toast.success(t("courses.lessonsPdf.toast.batchStarted"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.lessonsPdf.toast.error"),
      ),
  });

  const cancelAllPdfMut = useMutation({
    mutationFn: () => coursesApi.lessonPdf.cancelAll(orgId, course.id),
    onSuccess: (fresh) => {
      setCache(fresh);
      toast.success(t("courses.lessonsPdf.toast.cancelled"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.lessonsPdf.toast.error"),
      ),
  });

  const downloadPdf = async (lesson: CourseLessonOut) => {
    try {
      const { blob, filename } = await coursesApi.lessonPdf.download(
        orgId,
        course.id,
        lesson.id,
      );
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename ?? `${lesson.lesson_code}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      // Lascia il blob un attimo prima di revocare (per evitare browser quirks).
      setTimeout(() => URL.revokeObjectURL(url), 5_000);
    } catch (err) {
      toast.error(
        extractApiError(err).message ?? t("courses.lessonsPdf.toast.error"),
      );
    }
  };

  const updateLessonMut = useMutation({
    mutationFn: ({
      lessonId,
      payload,
    }: {
      lessonId: string;
      payload: LessonContentUpdateInput;
    }) => coursesApi.lessonContent.updateLesson(orgId, course.id, lessonId, payload),
    onSuccess: (fresh) => {
      setCache(fresh);
      setEditDialog({ kind: "closed" });
      toast.success(t("courses.lessonsContent.toast.lessonUpdated"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.lessonsContent.toast.error"),
      ),
  });

  // Stato Fase 2 non approvato → empty state
  if (course.status === "lessons_structure_pending"
    || course.status === "lessons_structure_ready"
    || course.status.startsWith("architecture_")
    || course.status === "draft") {
    if (
      course.status !== "lessons_structure_approved" &&
      !course.status.startsWith("content_") &&
      !["slides_pending", "slides_ready", "slides_approved", "speech_pending", "speech_ready", "speech_approved", "published", "archived"].includes(
        course.status,
      )
    ) {
      return (
        <Card>
          <CardContent className="py-10 text-center">
            <Hourglass className="mx-auto size-8 text-muted-foreground" />
            <p className="mt-4 text-sm text-muted-foreground">
              {t("courses.lessonsContent.lessonsStructureNotApproved")}
            </p>
          </CardContent>
        </Card>
      );
    }
  }

  return (
    <div className="space-y-5">
      {/* === Header con aggregate progress === */}
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="space-y-1">
              <h3 className="text-base font-semibold">
                {t("courses.lessonsContent.title")}
              </h3>
              <p className="text-sm text-muted-foreground">
                {t("courses.lessonsContent.description")}
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
                  {t("courses.lessonsContent.cancelAll")}
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
                    ? t("courses.lessonsContent.regenerateAll")
                    : t("courses.lessonsContent.generateAll")}
                </Button>
              )}
              {canGenerate && showGenerateMissing && (
                <Button
                  variant="default"
                  onClick={() => generateMissingMut.mutate()}
                  disabled={anyActive || generateMissingMut.isPending}
                >
                  <Sparkles className="size-4" />
                  {t("courses.lessonsContent.generateMissing", {
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
                  {t("courses.lessonsContent.approveAll")}
                </Button>
              )}
              {canGenerate && anyPdfActive && (
                <Button
                  variant="destructive"
                  onClick={() => cancelAllPdfMut.mutate()}
                  disabled={cancelAllPdfMut.isPending}
                >
                  <StopCircle className="size-4" />
                  {t("courses.lessonsPdf.cancelAll")}
                </Button>
              )}
              {canGenerate && exportableCount > 0 && !anyPdfActive && (
                <Button
                  variant="outline"
                  onClick={() =>
                    setPdfExportDialog({ kind: "open", mode: "all" })
                  }
                  disabled={exportAllPdfMut.isPending}
                >
                  <FileText className="size-4" />
                  {t("courses.lessonsPdf.exportAll", { count: exportableCount })}
                </Button>
              )}
            </div>
          </div>
        </CardHeader>
        {(anyActive || aggregate.completed > 0) && (
          <CardContent className="space-y-2">
            <div className="flex items-baseline justify-between text-sm">
              <span className="font-medium">
                {t("courses.lessonsContent.aggregate.label", {
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
                    {t("courses.lessonsContent.aggregate.eta", {
                      time: formatDuration(eta.etaMs),
                    })}
                  </span>
                )}
                {eta.avgPerTaskMs !== null && (
                  <span>
                    {t("courses.lessonsContent.aggregate.avgPerTask", {
                      time: formatDuration(eta.avgPerTaskMs),
                    })}
                  </span>
                )}
              </div>
            )}
            {aggregate.failedCount > 0 && (
              <div className="flex items-center gap-2 text-xs text-destructive">
                <AlertCircle className="size-3.5" />
                {t("courses.lessonsContent.aggregate.failed", {
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
          <ModuleContentCard
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
            onEdit={(lesson) => setEditDialog({ kind: "open", lesson })}
            onApprove={(lessonId) => approveLessonMut.mutate(lessonId)}
            approvingId={
              approveLessonMut.isPending
                ? (approveLessonMut.variables as string | undefined)
                : undefined
            }
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
        <LessonContentGenerateDialog
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

      {editDialog.kind === "open" && editDialog.lesson.content_raw && (
        <LessonContentEditDialog
          open={true}
          lessonLabel={lessonLabel(editDialog.lesson.lesson_code)}
          initial={editDialog.lesson.content_raw}
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
        <LessonPdfExportDialog
          open={true}
          mode={pdfExportDialog.mode}
          lessonLabel={pdfExportDialog.lessonLabel}
          exportableCount={exportableCount}
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
// ModuleContentCard
// ---------------------------------------------------------------------------

interface ModuleContentCardProps {
  module: CourseModuleOut;
  canEdit: boolean;
  canGenerate: boolean;
  moduleLabel: string;
  lessonLabel: (code: string) => string;
  expanded: Set<string>;
  onToggle: (id: string) => void;
  onGenerate: (
    mode: LessonContentGenerateMode,
    lessonId?: string,
    lessonLabel?: string,
  ) => void;
  onEdit: (lesson: CourseLessonOut) => void;
  onApprove: (lessonId: string) => void;
  approvingId: string | undefined;
  onExportPdf: (
    lessonId: string,
    lessonLabel: string,
    currentTemplateId: string | null,
  ) => void;
  onDownloadPdf: (lesson: CourseLessonOut) => void;
  exportingPdfId: string | undefined;
}

function ModuleContentCard({
  module,
  canEdit,
  canGenerate,
  moduleLabel,
  lessonLabel,
  expanded,
  onToggle,
  onGenerate,
  onEdit,
  onApprove,
  approvingId,
  onExportPdf,
  onDownloadPdf,
  exportingPdfId,
}: ModuleContentCardProps) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start gap-2">
          <Badge variant="outline" className="font-mono text-xs mt-0.5">
            {moduleLabel}
          </Badge>
          <div>
            <h4 className="text-sm font-semibold">{module.title}</h4>
            {module.description && (
              <p className="text-xs text-muted-foreground line-clamp-2 max-w-xl mt-0.5">
                {module.description}
              </p>
            )}
          </div>
        </div>
      </CardHeader>
      {module.lessons.length > 0 && (
        <CardContent className="space-y-2">
          {module.lessons.map((lesson) => (
            <LessonContentRow
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
              onEdit={() => onEdit(lesson)}
              onApprove={() => onApprove(lesson.id)}
              isApproving={approvingId === lesson.id}
              lessonLabel={lessonLabel(lesson.lesson_code)}
              onExportPdf={() =>
                onExportPdf(
                  lesson.id,
                  lessonLabel(lesson.lesson_code),
                  lesson.pdf_template_id,
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
// LessonContentRow
// ---------------------------------------------------------------------------

interface LessonContentRowProps {
  lesson: CourseLessonOut;
  parentModule: CourseModuleOut;
  expanded: boolean;
  onToggle: () => void;
  canEdit: boolean;
  canGenerate: boolean;
  onGenerate: (mode: LessonContentGenerateMode) => void;
  onEdit: () => void;
  onApprove: () => void;
  isApproving: boolean;
  lessonLabel: string;
  onExportPdf: () => void;
  onDownloadPdf: () => void;
  isExportingPdf: boolean;
}

function LessonContentRow({
  lesson,
  parentModule,
  expanded,
  onToggle,
  canEdit,
  canGenerate,
  onGenerate,
  onEdit,
  onApprove,
  isApproving,
  lessonLabel,
  onExportPdf,
  onDownloadPdf,
  isExportingPdf,
}: LessonContentRowProps) {
  const { t } = useTranslation();
  const status = lesson.content_status;
  const isProcessing = status === "pending" || status === "processing";
  const isReady = status === "ready" || status === "approved";
  const pdfStatus = lesson.pdf_status;
  const isPdfProcessing =
    pdfStatus === "pending" || pdfStatus === "processing";
  const canExportPdf =
    isReady &&
    (pdfStatus === "empty" ||
      pdfStatus === "ready" ||
      pdfStatus === "failed");

  // CTA primaria contenuto — l'azione "next-step" più ovvia per lo stato
  // corrente. Quando assente il bottone non viene reso (lo stato è
  // visibile dal badge o dalla progress bar).
  const primaryContentCta = (() => {
    if (!canGenerate) return null;
    if (status === "empty") {
      return (
        <Button size="sm" onClick={() => onGenerate("generate-lesson")}>
          <Sparkles className="size-3.5" />
          {t("courses.lessonsContent.lesson.generate")}
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
          {t("courses.lessonsContent.lesson.retry")}
        </Button>
      );
    }
    if (status === "ready") {
      return (
        <Button size="sm" onClick={onApprove} disabled={isApproving}>
          <CheckCircle2 className="size-3.5" />
          {t("courses.lessonsContent.lesson.approve")}
        </Button>
      );
    }
    return null;
  })();

  // CTA primaria PDF — l'azione più ovvia per lo stato corrente.
  // Solo una alla volta, niente bottoni multipli affiancati per il PDF.
  const primaryPdfCta = (() => {
    if (isPdfProcessing) {
      return (
        <Button
          size="sm"
          variant="outline"
          disabled
          title={t("courses.lessonsPdf.lesson.exporting")}
        >
          <Loader2 className="size-3.5 animate-spin" />
          {lesson.pdf_progress}%
        </Button>
      );
    }
    if (pdfStatus === "ready") {
      return (
        <Button
          size="sm"
          variant="outline"
          onClick={onDownloadPdf}
          title={t("courses.lessonsPdf.lesson.download")}
        >
          <Download className="size-3.5" />
          {t("courses.lessonsPdf.lesson.download")}
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
            ? t("courses.lessonsPdf.lesson.retry")
            : t("courses.lessonsPdf.lesson.export")}
        </Button>
      );
    }
    return null;
  })();

  // Voci del menu kebab (azioni secondarie, meno frequenti). Mostriamo
  // il trigger SOLO se almeno una voce è applicabile, così su lezioni
  // empty/processing la riga resta minimale.
  const canRegenerateContent =
    canGenerate && (status === "ready" || status === "approved");
  const canRegeneratePdf =
    canGenerate && pdfStatus === "ready" && isReady && !isExportingPdf;
  const hasMenuItems = canRegenerateContent || canRegeneratePdf;

  // Stale-detection: il contenuto è stale se la struttura della lezione o
  // l'architettura del modulo padre sono state modificate dopo l'ultima
  // generazione del content. Il PDF è stale se il content è cambiato dopo.
  const contentStale = isContentStale(lesson, parentModule);
  const pdfStale = isPdfStale(lesson);

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
        </button>
        <LessonContentStatusBadge status={status} />
        {status === "approved" && (
          <ApprovalBadge
            level="lessonContent"
            approvedAt={lesson.content_approved_at}
          />
        )}
        <LessonPdfStatusBadge status={pdfStatus} />
        {primaryContentCta}
        {primaryPdfCta}
        {canEdit && isReady && lesson.content_raw && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7"
            onClick={onEdit}
            title={t("courses.lessonsContent.lesson.edit")}
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
              {canRegenerateContent && (
                <DropdownMenuItem
                  onClick={() => onGenerate("regenerate-lesson")}
                >
                  <Sparkles className="size-3.5" />
                  {t("courses.lessonsContent.lesson.regenerate")}
                </DropdownMenuItem>
              )}
              {canRegeneratePdf && (
                <DropdownMenuItem onClick={onExportPdf}>
                  <FileText className="size-3.5" />
                  {t("courses.lessonsPdf.lesson.regenerate")}
                </DropdownMenuItem>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>

      {/* Stale-detection alerts. Mostriamo PRIMA il content stale (a
          monte del PDF) e POI il PDF stale: l'utente sa quale è la
          regenerazione "principale" in cascata. Se il contenuto è stale
          il PDF lo è di conseguenza, ma anche viceversa il PDF può
          essere stale anche solo per un edit di content_modified_at. */}
      {(contentStale || pdfStale) && !isProcessing && !isPdfProcessing && (
        <div className="border-t px-4 py-3 space-y-2">
          {contentStale && canGenerate && (
            <StalenessAlert
              kind="content"
              variant="inline"
              onAction={() => onGenerate("regenerate-lesson")}
            />
          )}
          {pdfStale && (
            <StalenessAlert
              kind="pdf"
              variant="inline"
              onAction={canGenerate ? onExportPdf : undefined}
              hideAction={!canGenerate}
            />
          )}
        </div>
      )}

      {/* Progress bar live — priorità: contenuto > PDF.
          Se la lezione sta ancora generando il contenuto, NON mostriamo
          la PDF progress (il PDF non può partire prima che il contenuto
          sia ready, quindi è uno stato impossibile in pratica). */}
      {isProcessing ? (
        <div className="space-y-2 border-t px-4 py-3">
          <div className="flex items-baseline justify-between text-xs">
            <span className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" />
              {lesson.content_progress_phase
                ? t(
                    `courses.lessonsContent.phases.${lesson.content_progress_phase}`,
                    {
                      defaultValue: lesson.content_progress_phase,
                    },
                  )
                : t("courses.lessonsContent.phases.preparing_prompt")}
            </span>
            <span className="font-mono tabular-nums text-muted-foreground">
              {lesson.content_progress}%
            </span>
          </div>
          <Progress value={lesson.content_progress} />
        </div>
      ) : isPdfProcessing ? (
        <div className="space-y-2 border-t px-4 py-3">
          <div className="flex items-baseline justify-between text-xs">
            <span className="flex items-center gap-2 text-muted-foreground">
              <FileText className="size-3.5" />
              {t(
                `courses.lessonsPdf.phases.${lesson.pdf_progress_phase ?? "preparing"}`,
                { defaultValue: lesson.pdf_progress_phase ?? "" },
              )}
            </span>
            <span className="font-mono tabular-nums text-muted-foreground">
              {lesson.pdf_progress}%
            </span>
          </div>
          <Progress value={lesson.pdf_progress} />
        </div>
      ) : null}

      {/* Render contenuto se ready/approved e expanded */}
      {expanded && isReady && lesson.content_raw && (
        <div className="border-t px-4 py-4">
          <LessonContentView content={lesson.content_raw} />
        </div>
      )}

      {/* Stato failed (raggiunto solo dopo 5 retry automatici esauriti).
          Niente messaggio di errore tecnico — il badge di stato e il
          pulsante "Riprova" sono sufficienti per l'utente. */}
    </div>
  );
}

function LessonPdfStatusBadge({ status }: { status: LessonPdfStatus }) {
  const { t } = useTranslation();
  // Nascondi quando il PDF è ok (empty = mai esportato, ready = scaricabile
  // → l'azione "Scarica PDF" già comunica lo stato senza ridondanza).
  // Mostra solo quando c'è informazione utile: in coda, in corso, fallito.
  if (status === "empty" || status === "ready") return null;
  const variant = (() => {
    switch (status) {
      case "pending":
      case "processing":
        return "outline";
      case "failed":
        return "destructive";
      default:
        return "outline";
    }
  })();
  return (
    <Badge variant={variant} className="text-[11px]">
      {t(`courses.lessonsPdf.statuses.${status}`)}
    </Badge>
  );
}

function LessonContentStatusBadge({
  status,
}: {
  status: LessonContentStatus;
}) {
  const { t } = useTranslation();
  // Per `approved` rendiamo l'ApprovalBadge cross-fase (gestito a livello
  // di LessonContentRow). Per `ready` la CTA "Approva" comunica il next
  // step, quindi badge nascosto. Mostriamo solo gli stati di
  // transizione/errore: empty, pending, processing, failed.
  if (status === "ready" || status === "approved") return null;
  const variant = (() => {
    switch (status) {
      case "pending":
      case "processing":
        return "outline";
      case "failed":
        return "destructive";
      case "empty":
      default:
        return "outline";
    }
  })();
  return (
    <Badge variant={variant} className="text-[11px]">
      {t(`courses.lessonsContent.statuses.${status}`)}
    </Badge>
  );
}

export default CourseLessonContentView;
