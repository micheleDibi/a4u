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
  FileArchive,
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
  isAssessmentRaw,
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
import { isSlidesPdfStale, isSlidesStale } from "@/lib/staleness";

import { LessonSlidesEditDialog } from "./LessonSlidesEditDialog";
import { LessonSlidesView } from "./LessonSlidesView";
import {
  LessonSlidesGenerateDialog,
  type LessonSlidesGenerateMode,
} from "./LessonSlidesGenerateDialog";
import {
  LessonSlidesPdfExportDialog,
  type LessonSlidesPdfExportMode,
} from "./LessonSlidesPdfExportDialog";

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

type PdfExportDialogState =
  | { kind: "closed" }
  | {
      kind: "open";
      mode: LessonSlidesPdfExportMode;
      lessonId?: string;
      lessonLabel?: string;
      initialTemplateId?: string | null;
    };

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
  // Su errore di una mutation, forziamo refetch per riallineare cache
  // con verità del BE (evita loop "errore → cache stale → riprovo → errore").
  const refetchOnError = () => {
    qc.invalidateQueries({ queryKey: detailKey });
  };

  // La lezione-verifica (is_assessment) non genera slide: esclusa da
  // tutti i conteggi aggregati, dal progress e dai trigger batch di
  // questa vista (le righe sono già filtrate in ModuleSlidesCard).
  const allLessons = useMemo(
    () =>
      course.modules
        .flatMap((m) => m.lessons)
        .filter((l) => !l.is_assessment),
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
    // La percentuale riflette solo le lezioni EFFETTIVAMENTE completate
    // (ready/approved). Le lezioni in produzione non contribuiscono al
    // percent: vedi commento equivalente in CourseLessonContentView.
    let completed = 0;
    let activeCount = 0;
    let failedCount = 0;
    for (const l of allLessons) {
      const status = l.slides_status;
      if (status === "ready" || status === "approved") {
        completed += 1;
      } else if (status === "processing" || status === "pending") {
        activeCount += 1;
      } else if (status === "failed") {
        failedCount += 1;
      }
    }
    return {
      completed,
      total,
      percent: Math.round((completed / total) * 100),
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
    onError: (err) => {
      refetchOnError();
      toast.error(
        extractApiError(err).message ?? t("courses.lessonsSlides.toast.error"),
      );
    },
  });

  const approveAllMut = useMutation({
    mutationFn: () => coursesApi.lessonSlides.approveAll(orgId, course.id),
    onSuccess: (fresh) => {
      setCache(fresh);
      toast.success(t("courses.lessonsSlides.toast.allApproved"));
    },
    onError: (err) => {
      refetchOnError();
      toast.error(
        extractApiError(err).message ?? t("courses.lessonsSlides.toast.error"),
      );
    },
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

  // === PDF export ===

  const exportPdfMut = useMutation({
    mutationFn: ({
      lessonId,
      templateId,
    }: {
      lessonId: string;
      templateId: string | null;
    }) =>
      coursesApi.lessonSlidesPdf.exportLesson(
        orgId,
        course.id,
        lessonId,
        templateId,
      ),
    onSuccess: (fresh) => {
      setCache(fresh);
      setPdfExportDialog({ kind: "closed" });
      toast.success(t("courses.lessonsSlidesPdf.toast.lessonStarted"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ??
          t("courses.lessonsSlidesPdf.toast.error"),
      ),
  });

  const exportAllPdfMut = useMutation({
    mutationFn: ({
      templateId,
      onlyMissing,
    }: {
      templateId: string | null;
      onlyMissing: boolean;
    }) =>
      coursesApi.lessonSlidesPdf.exportAll(
        orgId,
        course.id,
        templateId,
        onlyMissing,
      ),
    onSuccess: (fresh, vars) => {
      setCache(fresh);
      setPdfExportDialog({ kind: "closed" });
      toast.success(
        t(
          vars.onlyMissing
            ? "courses.lessonsSlidesPdf.toast.missingStarted"
            : "courses.lessonsSlidesPdf.toast.batchStarted",
        ),
      );
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ??
          t("courses.lessonsSlidesPdf.toast.error"),
      ),
  });

  const cancelAllPdfMut = useMutation({
    mutationFn: () => coursesApi.lessonSlidesPdf.cancelAll(orgId, course.id),
    onSuccess: (fresh) => {
      setCache(fresh);
      toast.success(t("courses.lessonsSlidesPdf.toast.cancelled"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ??
          t("courses.lessonsSlidesPdf.toast.error"),
      ),
  });

  const downloadPdf = async (lesson: CourseLessonOut) => {
    try {
      const { blob, filename } = await coursesApi.lessonSlidesPdf.download(
        orgId,
        course.id,
        lesson.id,
      );
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename ?? `${lesson.lesson_code}_slides.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5_000);
    } catch (err) {
      toast.error(
        extractApiError(err).message ??
          t("courses.lessonsSlidesPdf.toast.error"),
      );
    }
  };

  const downloadModuleMerged = async (
    moduleId: string,
    fallbackName: string,
  ) => {
    try {
      const { blob, filename } =
        await coursesApi.lessonSlidesPdf.downloadModuleMerged(
          orgId,
          course.id,
          moduleId,
        );
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename ?? `${fallbackName}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5_000);
    } catch (err) {
      toast.error(
        extractApiError(err).message ??
          t("courses.lessonsSlidesPdf.toast.error"),
      );
    }
  };

  const downloadModuleZip = async (
    moduleId: string,
    fallbackName: string,
  ) => {
    try {
      const { blob, filename } =
        await coursesApi.lessonSlidesPdf.downloadModuleZip(
          orgId,
          course.id,
          moduleId,
        );
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename ?? `${fallbackName}.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5_000);
    } catch (err) {
      toast.error(
        extractApiError(err).message ??
          t("courses.lessonsSlidesPdf.toast.error"),
      );
    }
  };

  const downloadAllCourse = async (fmt: "merged" | "zip") => {
    try {
      const { blob, filename } =
        fmt === "merged"
          ? await coursesApi.lessonSlidesPdf.downloadAllMerged(orgId, course.id)
          : await coursesApi.lessonSlidesPdf.downloadAllZip(orgId, course.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename ?? `${course.title}.${fmt === "merged" ? "pdf" : "zip"}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5_000);
    } catch (err) {
      toast.error(
        extractApiError(err).message ??
          t("courses.lessonsSlidesPdf.toast.error"),
      );
    }
  };

  // PDF aggregate
  const pdfAggregate = useMemo(() => {
    let pdfActive = 0;
    for (const l of allLessons) {
      if (
        l.slides_pdf_status === "pending" ||
        l.slides_pdf_status === "processing"
      )
        pdfActive += 1;
    }
    return { pdfActive };
  }, [allLessons]);
  const anyPdfActive = pdfAggregate.pdfActive > 0;
  const exportablePdfCount = allLessons.filter(
    (l) =>
      (l.slides_status === "ready" || l.slides_status === "approved") &&
      (l.slides_pdf_status === "empty" ||
        l.slides_pdf_status === "ready" ||
        l.slides_pdf_status === "failed"),
  ).length;
  // Lezioni eleggibili senza PDF slide pronto. Driver del pulsante
  // "Genera PDF slide mancanti".
  const missingPdfCount = allLessons.filter(
    (l) =>
      (l.slides_status === "ready" || l.slides_status === "approved") &&
      (l.slides_pdf_status === "empty" || l.slides_pdf_status === "failed"),
  ).length;
  // "Scarica tutto" (intero corso): solo quando ogni lezione non-verifica
  // ha il PDF slide pronto.
  const bundleLessons = allLessons.filter((l) => !l.is_assessment);
  const allCoursePdfsReady =
    bundleLessons.length > 0 &&
    bundleLessons.every((l) => l.slides_pdf_status === "ready");

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
              {canGenerate && anyPdfActive && (
                <Button
                  variant="destructive"
                  onClick={() => cancelAllPdfMut.mutate()}
                  disabled={cancelAllPdfMut.isPending}
                >
                  <StopCircle className="size-4" />
                  {t("courses.lessonsSlidesPdf.cancelAll")}
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
                  {t("courses.lessonsSlidesPdf.exportAll", {
                    count: exportablePdfCount,
                  })}
                </Button>
              )}
              {allCoursePdfsReady && (
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="outline">
                      <Download className="size-4" />
                      {t("courses.lessonsSlidesPdf.downloadAll.label")}
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem onSelect={() => downloadAllCourse("merged")}>
                      <FileText className="size-3.5" />
                      {t("courses.lessonsSlidesPdf.downloadAll.merged")}
                    </DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => downloadAllCourse("zip")}>
                      <FileArchive className="size-3.5" />
                      {t("courses.lessonsSlidesPdf.downloadAll.zip")}
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              )}
              {canGenerate &&
                missingPdfCount > 0 &&
                missingPdfCount < exportablePdfCount &&
                !anyPdfActive && (
                  <Button
                    variant="outline"
                    onClick={() =>
                      setPdfExportDialog({ kind: "open", mode: "missing" })
                    }
                    disabled={exportAllPdfMut.isPending}
                  >
                    <FileText className="size-4" />
                    {t("courses.lessonsSlidesPdf.generateMissing", {
                      count: missingPdfCount,
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
            onDownloadModuleMerged={(moduleId, fallback) => {
              void downloadModuleMerged(moduleId, fallback);
            }}
            onDownloadModuleZip={(moduleId, fallback) => {
              void downloadModuleZip(moduleId, fallback);
            }}
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
          orgId={orgId}
          courseId={course.id}
          initial={editDialog.lesson.slides_raw}
          contentRaw={
            isAssessmentRaw(editDialog.lesson.content_raw)
              ? null
              : editDialog.lesson.content_raw
          }
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
        <LessonSlidesPdfExportDialog
          open={true}
          mode={pdfExportDialog.mode}
          lessonLabel={pdfExportDialog.lessonLabel}
          exportableCount={
            pdfExportDialog.mode === "missing"
              ? missingPdfCount
              : exportablePdfCount
          }
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
              exportAllPdfMut.mutate({ templateId, onlyMissing: false });
            } else if (pdfExportDialog.mode === "missing") {
              exportAllPdfMut.mutate({ templateId, onlyMissing: true });
            }
          }}
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
  onExportPdf: (
    lessonId: string,
    lessonLabel: string,
    currentTemplateId: string | null,
  ) => void;
  onDownloadPdf: (lesson: CourseLessonOut) => void;
  exportingPdfId: string | undefined;
  onDownloadModuleMerged: (moduleId: string, fallbackName: string) => void;
  onDownloadModuleZip: (moduleId: string, fallbackName: string) => void;
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
  onExportPdf,
  onDownloadPdf,
  exportingPdfId,
  onDownloadModuleMerged,
  onDownloadModuleZip,
}: ModuleSlidesCardProps) {
  const { t } = useTranslation();
  // La lezione-verifica non genera slide: esclusa da liste e conteggi.
  const slidesLessons = module.lessons.filter((l) => !l.is_assessment);
  const allPdfsReady =
    slidesLessons.length > 0 &&
    slidesLessons.every((l) => l.slides_pdf_status === "ready");
  const fallbackName = `${module.module_code} ${module.title}`;
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2 min-w-0">
            <Badge variant="outline" className="font-mono text-xs">
              {moduleLabel}
            </Badge>
            <h4 className="text-sm font-semibold truncate">{module.title}</h4>
          </div>
          {allPdfsReady && (
            <div className="flex items-center gap-2 shrink-0">
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => onDownloadModuleMerged(module.id, fallbackName)}
                title={t("courses.lessonsSlidesPdf.module.downloadMerged.title")}
              >
                <Download className="size-3.5" />
                {t("courses.lessonsSlidesPdf.module.downloadMerged.label")}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => onDownloadModuleZip(module.id, fallbackName)}
                title={t("courses.lessonsSlidesPdf.module.exportZip.title")}
              >
                <FileArchive className="size-3.5" />
                {t("courses.lessonsSlidesPdf.module.exportZip.label")}
              </Button>
            </div>
          )}
        </div>
      </CardHeader>
      {slidesLessons.length > 0 && (
        <CardContent className="space-y-2">
          {slidesLessons.map((lesson) => (
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
              onExportPdf={() =>
                onExportPdf(
                  lesson.id,
                  lessonLabel(lesson.lesson_code),
                  lesson.slides_pdf_template_id,
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
  onExportPdf: () => void;
  onDownloadPdf: () => void;
  isExportingPdf: boolean;
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
  onExportPdf,
  onDownloadPdf,
  isExportingPdf,
}: LessonSlidesRowProps) {
  const { t } = useTranslation();
  const status = lesson.slides_status;
  const isProcessing = status === "pending" || status === "processing";
  const isReady = status === "ready" || status === "approved";
  const pdfStatus = lesson.slides_pdf_status;
  const isPdfProcessing =
    pdfStatus === "pending" || pdfStatus === "processing";
  const canExportPdf =
    isReady &&
    (pdfStatus === "empty" ||
      pdfStatus === "ready" ||
      pdfStatus === "failed");
  const pdfStale = isSlidesPdfStale(lesson);

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

  // Voci kebab: rigenera + (futuro) Esporta/Rigenera PDF.
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
          title={t("courses.lessonsSlidesPdf.lesson.exporting", {
            defaultValue: "Export in corso…",
          })}
        >
          <Loader2 className="size-3.5 animate-spin" />
          {lesson.slides_pdf_progress}%
        </Button>
      );
    }
    if (pdfStatus === "ready") {
      return (
        <Button
          size="sm"
          variant="outline"
          onClick={onDownloadPdf}
          title={t("courses.lessonsSlidesPdf.lesson.download", {
            defaultValue: "Scarica PDF",
          })}
        >
          <Download className="size-3.5" />
          {t("courses.lessonsSlidesPdf.lesson.download", {
            defaultValue: "Scarica PDF",
          })}
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
            ? t("courses.lessonsSlidesPdf.lesson.retry", {
                defaultValue: "Riprova",
              })
            : t("courses.lessonsSlidesPdf.lesson.export", {
                defaultValue: "Esporta PDF",
              })}
        </Button>
      );
    }
    return null;
  })();

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
        {primaryPdfCta}
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
              {canRegeneratePdf && (
                <DropdownMenuItem onClick={onExportPdf}>
                  <FileText className="size-3.5" />
                  {t("courses.lessonsSlidesPdf.lesson.regenerate", {
                    defaultValue: "Rigenera PDF",
                  })}
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
              kind="slides"
              variant="inline"
              onAction={
                canStartGeneration ? () => onGenerate("regenerate-lesson") : undefined
              }
              hideAction={!canStartGeneration}
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
            contentRaw={
              isAssessmentRaw(lesson.content_raw)
                ? null
                : lesson.content_raw
            }
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
