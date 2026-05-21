import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  AlertCircle,
  BookOpen,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Hourglass,
  ListChecks,
  ListOrdered,
  Loader2,
  Pencil,
  RotateCcw,
  Sparkles,
  Target,
  GraduationCap,
} from "lucide-react";
import {
  coursesApi,
  type CourseLessonOut,
  type CourseModuleOut,
  type CourseOut,
  type LessonStructureUpdateInput,
  type LessonsStructureModuleStatus,
} from "@/api/courses";
import { ApprovalBadge } from "@/components/shared/ApprovalBadge";
import { StalenessAlert } from "@/components/shared/StalenessAlert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { useBatchEta } from "@/hooks/useBatchEta";
import { extractApiError } from "@/lib/errors";
import { formatDuration } from "@/lib/formatDuration";
import { isStructureStale } from "@/lib/staleness";
import { LessonStructureEditDialog } from "./LessonStructureEditDialog";
import {
  LessonsStructureGenerateDialog,
  type LessonsStructureGenerateMode,
} from "./LessonsStructureGenerateDialog";

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
      mode: LessonsStructureGenerateMode;
      moduleId?: string;
      moduleLabel?: string;
    };

type EditDialogState =
  | { kind: "closed" }
  | { kind: "open"; lesson: CourseLessonOut; module: CourseModuleOut };

const ACTIVE_STATUSES: LessonsStructureModuleStatus[] = ["pending", "processing"];

export function CourseLessonStructureView({
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
  const [expandedLessons, setExpandedLessons] = useState<Set<string>>(
    new Set()
  );

  const detailKey = ["courses", "detail", orgId, course.id];
  const setCache = (fresh: CourseOut) => {
    qc.setQueryData(detailKey, fresh);
    qc.invalidateQueries({ queryKey: detailKey });
    qc.invalidateQueries({ queryKey: ["courses", "list", orgId] });
  };

  const moduleLabel = (code: string) => {
    const m = code.match(/^M(\d+)$/);
    return m ? t("courses.architecture.moduleLabel", { n: m[1] }) : code;
  };
  const lessonLabel = (code: string) => {
    const m = code.match(/^M\d+\.L(\d+)$/);
    return m ? t("courses.architecture.lessonLabel", { n: m[1] }) : code;
  };

  // Aggregate progress (sempre calcolato, mostrato quando rilevante)
  const aggregate = useMemo(() => {
    const modules = course.modules;
    const total = modules.length;
    if (total === 0) return { completed: 0, total: 0, percent: 0 };
    let sum = 0;
    let completed = 0;
    for (const m of modules) {
      const status = m.lessons_structure_status;
      if (status === "ready" || status === "approved") {
        sum += 100;
        completed += 1;
      } else if (status === "processing" || status === "pending") {
        sum += m.lessons_structure_progress || 0;
      } else if (status === "failed") {
        sum += 0;
      }
    }
    return {
      completed,
      total,
      percent: Math.round(sum / total),
      activeCount: modules.filter((m) =>
        ACTIVE_STATUSES.includes(m.lessons_structure_status)
      ).length,
      failedCount: modules.filter((m) => m.lessons_structure_status === "failed")
        .length,
    };
  }, [course.modules]);

  const anyActive = (aggregate.activeCount ?? 0) > 0;

  // ETA del batch struttura: derivata dai timestamp di completamento dei
  // moduli (`lessons_structure_generated_at`).
  const eta = useBatchEta(
    course.modules.map((m) => ({
      status: m.lessons_structure_status,
      completedAt: m.lessons_structure_generated_at,
    })),
  );
  const allReadyOrApproved =
    course.modules.length > 0 &&
    course.modules.every(
      (m) =>
        m.lessons_structure_status === "ready" ||
        m.lessons_structure_status === "approved"
    );
  const allApproved =
    course.modules.length > 0 &&
    course.modules.every((m) => m.lessons_structure_status === "approved");
  const someEverGenerated = course.modules.some((m) =>
    ["ready", "approved", "failed"].includes(m.lessons_structure_status)
  );

  // ---------- Mutations ----------

  const generateAllMut = useMutation({
    mutationFn: (hint: string | null) =>
      coursesApi.lessonsStructure.generateAll(orgId, course.id, hint),
    onSuccess: (fresh) => {
      setCache(fresh);
      setGenerateDialog({ kind: "closed" });
      toast.success(t("courses.lessonsStructure.toast.batchStarted"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.lessonsStructure.toast.error")
      ),
  });

  const generateModuleMut = useMutation({
    mutationFn: ({
      moduleId,
      hint,
    }: {
      moduleId: string;
      hint: string | null;
    }) =>
      coursesApi.lessonsStructure.generateModule(
        orgId,
        course.id,
        moduleId,
        hint
      ),
    onSuccess: (fresh) => {
      setCache(fresh);
      setGenerateDialog({ kind: "closed" });
      toast.success(t("courses.lessonsStructure.toast.moduleStarted"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.lessonsStructure.toast.error")
      ),
  });

  const approveModuleMut = useMutation({
    mutationFn: (moduleId: string) =>
      coursesApi.lessonsStructure.approveModule(orgId, course.id, moduleId),
    onSuccess: (fresh) => {
      setCache(fresh);
      toast.success(t("courses.lessonsStructure.toast.moduleApproved"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.lessonsStructure.toast.error")
      ),
  });

  const approveAllMut = useMutation({
    mutationFn: () => coursesApi.lessonsStructure.approveAll(orgId, course.id),
    onSuccess: (fresh) => {
      setCache(fresh);
      toast.success(t("courses.lessonsStructure.toast.allApproved"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.lessonsStructure.toast.error")
      ),
  });

  const updateLessonMut = useMutation({
    mutationFn: ({
      lessonId,
      payload,
    }: {
      lessonId: string;
      payload: LessonStructureUpdateInput;
    }) =>
      coursesApi.lessonsStructure.updateLesson(
        orgId,
        course.id,
        lessonId,
        payload
      ),
    onSuccess: (fresh) => {
      setCache(fresh);
      setEditDialog({ kind: "closed" });
      toast.success(t("courses.lessonsStructure.toast.lessonUpdated"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.lessonsStructure.toast.error")
      ),
  });

  // ---------- Render ----------

  // Stato architettura non approvato → empty state
  if (course.status === "draft" || course.status.startsWith("architecture_")) {
    if (course.status !== "architecture_approved") {
      return (
        <Card>
          <CardContent className="py-10 text-center">
            <Hourglass className="mx-auto size-8 text-muted-foreground" />
            <p className="mt-4 text-sm text-muted-foreground">
              {t("courses.lessonsStructure.architectureNotApproved")}
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
                {t("courses.lessonsStructure.title")}
              </h3>
              <p className="text-sm text-muted-foreground">
                {t("courses.lessonsStructure.description")}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
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
                    ? t("courses.lessonsStructure.regenerateAll")
                    : t("courses.lessonsStructure.generateAll")}
                </Button>
              )}
              {canGenerate && allReadyOrApproved && !allApproved && (
                <Button
                  variant="default"
                  onClick={() => approveAllMut.mutate()}
                  disabled={approveAllMut.isPending}
                >
                  <CheckCircle2 className="size-4" />
                  {t("courses.lessonsStructure.approveAll")}
                </Button>
              )}
            </div>
          </div>
        </CardHeader>
        {(anyActive || aggregate.completed > 0) && (
          <CardContent className="space-y-2">
            <div className="flex items-baseline justify-between text-sm">
              <span className="font-medium">
                {t("courses.lessonsStructure.aggregate.label", {
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
                    {t("courses.lessonsStructure.aggregate.eta", {
                      time: formatDuration(eta.etaMs),
                    })}
                  </span>
                )}
                {eta.avgPerTaskMs !== null && (
                  <span>
                    {t("courses.lessonsStructure.aggregate.avgPerTask", {
                      time: formatDuration(eta.avgPerTaskMs),
                    })}
                  </span>
                )}
              </div>
            )}
            {aggregate.failedCount && aggregate.failedCount > 0 ? (
              <div className="flex items-center gap-2 text-xs text-destructive">
                <AlertCircle className="size-3.5" />
                {t("courses.lessonsStructure.aggregate.failed", {
                  count: aggregate.failedCount,
                })}
              </div>
            ) : null}
          </CardContent>
        )}
      </Card>

      {/* === Lista moduli === */}
      <div className="space-y-3">
        {course.modules.map((module) => (
          <ModuleCard
            key={module.id}
            module={module}
            canEdit={canEdit}
            canGenerate={canGenerate}
            moduleLabel={moduleLabel(module.module_code)}
            lessonLabel={lessonLabel}
            expandedLessons={expandedLessons}
            onToggleLesson={(id) => {
              setExpandedLessons((prev) => {
                const next = new Set(prev);
                if (next.has(id)) next.delete(id);
                else next.add(id);
                return next;
              });
            }}
            onEditLesson={(lesson) =>
              setEditDialog({ kind: "open", lesson, module })
            }
            onGenerate={(mode) =>
              setGenerateDialog({
                kind: "open",
                mode,
                moduleId: module.id,
                moduleLabel: moduleLabel(module.module_code),
              })
            }
            onApprove={() => approveModuleMut.mutate(module.id)}
            isApproving={
              approveModuleMut.isPending &&
              approveModuleMut.variables === module.id
            }
          />
        ))}
      </div>

      {/* === Dialogs === */}
      {generateDialog.kind === "open" && (
        <LessonsStructureGenerateDialog
          open={true}
          mode={generateDialog.mode}
          moduleLabel={generateDialog.moduleLabel}
          isPending={
            generateAllMut.isPending || generateModuleMut.isPending
          }
          onClose={() => setGenerateDialog({ kind: "closed" })}
          onConfirm={(hint) => {
            if (generateDialog.mode === "generate-all" || generateDialog.mode === "regenerate-all") {
              generateAllMut.mutate(hint);
            } else if (generateDialog.moduleId) {
              generateModuleMut.mutate({
                moduleId: generateDialog.moduleId,
                hint,
              });
            }
          }}
        />
      )}

      {editDialog.kind === "open" && (
        <LessonStructureEditDialog
          open={true}
          languageCode={course.language_code}
          lessonLabel={lessonLabel(editDialog.lesson.lesson_code)}
          moduleLabel={moduleLabel(editDialog.module.module_code)}
          initial={{
            learning_objectives: editDialog.lesson.learning_objectives,
            mandatory_topics: editDialog.lesson.mandatory_topics,
            prerequisites: editDialog.lesson.prerequisites,
            section_outline: editDialog.lesson.section_outline,
          }}
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
// ModuleCard
// ---------------------------------------------------------------------------

interface ModuleCardProps {
  module: CourseModuleOut;
  canEdit: boolean;
  canGenerate: boolean;
  moduleLabel: string;
  lessonLabel: (code: string) => string;
  expandedLessons: Set<string>;
  onToggleLesson: (id: string) => void;
  onEditLesson: (lesson: CourseLessonOut) => void;
  onGenerate: (mode: LessonsStructureGenerateMode) => void;
  onApprove: () => void;
  isApproving: boolean;
}

function ModuleCard({
  module,
  canEdit,
  canGenerate,
  moduleLabel,
  lessonLabel,
  expandedLessons,
  onToggleLesson,
  onEditLesson,
  onGenerate,
  onApprove,
  isApproving,
}: ModuleCardProps) {
  const { t } = useTranslation();
  const status = module.lessons_structure_status;
  const isProcessing = status === "pending" || status === "processing";
  const isReady = status === "ready" || status === "approved";
  const stale = isStructureStale(module);

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
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
          <div className="flex items-center gap-2">
            <ModuleStatusBadge status={status} />
            {status === "approved" && (
              <ApprovalBadge
                level="module"
                approvedAt={module.lessons_structure_approved_at}
              />
            )}
            {canGenerate && status === "empty" && (
              <Button
                size="sm"
                variant="default"
                onClick={() => onGenerate("generate-module")}
              >
                <Sparkles className="size-3.5" />
                {t("courses.lessonsStructure.module.generate")}
              </Button>
            )}
            {canGenerate && (status === "ready" || status === "approved") && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => onGenerate("regenerate-module")}
              >
                <Sparkles className="size-3.5" />
                {t("courses.lessonsStructure.module.regenerate")}
              </Button>
            )}
            {canGenerate && status === "failed" && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => onGenerate("generate-module")}
              >
                <RotateCcw className="size-3.5" />
                {t("courses.lessonsStructure.module.retry")}
              </Button>
            )}
            {canGenerate && status === "ready" && (
              <Button
                size="sm"
                variant="default"
                onClick={onApprove}
                disabled={isApproving}
              >
                <CheckCircle2 className="size-3.5" />
                {t("courses.lessonsStructure.module.approve")}
              </Button>
            )}
          </div>
        </div>
      </CardHeader>

      {/* Stale-detection: l'architettura è stata modificata dopo
          l'ultima generazione AI di Fase 2. Suggerisci la rigenerazione
          della struttura del modulo. */}
      {stale && canGenerate && isReady && !isProcessing && (
        <CardContent className="pt-0">
          <StalenessAlert
            kind="structure"
            variant="block"
            onAction={() => onGenerate("regenerate-module")}
          />
        </CardContent>
      )}

      {/* Stato pending/processing — Progress live */}
      {isProcessing && (
        <CardContent className="space-y-2">
          <div className="flex items-baseline justify-between text-sm">
            <span className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" />
              {module.lessons_structure_progress_phase
                ? t(
                    `courses.lessonsStructure.phases.${module.lessons_structure_progress_phase}`,
                    {
                      defaultValue: module.lessons_structure_progress_phase,
                    }
                  )
                : t("courses.lessonsStructure.phases.preparing_prompt")}
            </span>
            <span className="font-mono tabular-nums text-muted-foreground">
              {module.lessons_structure_progress}%
            </span>
          </div>
          <Progress value={module.lessons_structure_progress} />
        </CardContent>
      )}

      {/* Stato failed — raggiunto solo dopo 5 retry automatici esauriti.
          Niente messaggio tecnico: il badge di stato e il pulsante
          "Riprova" sono sufficienti. */}

      {/* Lista lezioni — sempre visibile (titoli da Fase 1).
          Edit della struttura abilitato solo quando il modulo è ready/approved. */}
      {module.lessons.length > 0 && (
        <CardContent className="space-y-2">
          {module.lessons.map((lesson) => (
            <LessonStructureRow
              key={lesson.id}
              lesson={lesson}
              expanded={expandedLessons.has(lesson.id)}
              onToggle={() => onToggleLesson(lesson.id)}
              canEdit={canEdit && isReady}
              onEdit={() => onEditLesson(lesson)}
              lessonLabel={lessonLabel(lesson.lesson_code)}
            />
          ))}
        </CardContent>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// LessonStructureRow — accordion per ogni lezione con 4 sezioni
// ---------------------------------------------------------------------------

interface LessonRowProps {
  lesson: CourseLessonOut;
  expanded: boolean;
  onToggle: () => void;
  canEdit: boolean;
  onEdit: () => void;
  lessonLabel: string;
}

function LessonStructureRow({
  lesson,
  expanded,
  onToggle,
  canEdit,
  onEdit,
  lessonLabel,
}: LessonRowProps) {
  const { t } = useTranslation();
  const hasContent =
    lesson.learning_objectives.length > 0 ||
    lesson.mandatory_topics.length > 0 ||
    lesson.prerequisites.length > 0 ||
    lesson.section_outline.length > 0;

  return (
    <div className="rounded-md border bg-muted/10">
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-muted/30"
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
        {lesson.is_introductory && (
          <Badge variant="secondary" className="text-[11px]">
            <GraduationCap className="size-3" />
            {t("courses.architecture.lesson.introductory")}
          </Badge>
        )}
        {lesson.is_assessment && (
          <Badge variant="secondary" className="text-[11px]">
            <ListChecks className="size-3" />
            {t("courses.architecture.lesson.assessment")}
          </Badge>
        )}
        <span className="flex-1 text-sm font-medium truncate">
          {lesson.title}
        </span>
        {!hasContent && (
          <span className="text-xs text-muted-foreground italic">
            {t("courses.lessonsStructure.lesson.emptyHint")}
          </span>
        )}
        {canEdit && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7"
            onClick={(e) => {
              e.stopPropagation();
              onEdit();
            }}
          >
            <Pencil className="size-3.5" />
          </Button>
        )}
      </button>

      {expanded && (
        <div className="space-y-4 border-t px-4 py-3">
          {/* Sintesi Fase 1 — sempre visibile, è il "perché" della lezione. */}
          {lesson.summary && (
            <Subsection
              icon={<BookOpen className="size-3.5" />}
              title={t("courses.architecture.lesson.fields.summary")}
              empty={false}
            >
              <p className="text-sm leading-relaxed text-foreground">
                {lesson.summary}
              </p>
            </Subsection>
          )}

          {/* Obiettivi */}
          <Subsection
            icon={<Target className="size-3.5" />}
            title={t("courses.lessonsStructure.fields.learningObjectives")}
            empty={lesson.learning_objectives.length === 0}
          >
            <ol className="space-y-1 text-sm">
              {lesson.learning_objectives.map((o, i) => (
                <li key={i} className="leading-relaxed">
                  <span className="font-mono text-xs text-muted-foreground mr-2">
                    {i + 1}.
                  </span>
                  {o}
                </li>
              ))}
            </ol>
          </Subsection>

          {/* Temi */}
          <Subsection
            icon={<GraduationCap className="size-3.5" />}
            title={t("courses.lessonsStructure.fields.mandatoryTopics")}
            empty={lesson.mandatory_topics.length === 0}
          >
            <ul className="space-y-2 text-sm">
              {lesson.mandatory_topics.map((tt, idx) => {
                const match = tt.topic_id.match(/\.T(\d+)$/);
                const topicNum = match ? match[1] : String(idx + 1);
                return (
                  <li key={tt.topic_id}>
                    <div className="flex items-baseline gap-2">
                      <Badge variant="outline" className="text-[10px] shrink-0">
                        {t("courses.architecture.topicLabel", { n: topicNum })}
                      </Badge>
                      <span className="font-medium">{tt.topic}</span>
                    </div>
                    {tt.rationale && (
                      <p className="mt-0.5 ml-12 text-xs text-muted-foreground leading-relaxed">
                        {tt.rationale}
                      </p>
                    )}
                  </li>
                );
              })}
            </ul>
          </Subsection>

          {/* Prerequisiti */}
          <Subsection
            icon={<ListChecks className="size-3.5" />}
            title={t("courses.lessonsStructure.fields.prerequisites")}
            empty={lesson.prerequisites.length === 0}
          >
            <ul className="list-disc space-y-0.5 pl-5 text-sm">
              {lesson.prerequisites.map((p, i) => (
                <li key={i}>{p}</li>
              ))}
            </ul>
          </Subsection>

          {/* Scaletta */}
          <Subsection
            icon={<ListOrdered className="size-3.5" />}
            title={t("courses.lessonsStructure.fields.sectionOutline")}
            empty={lesson.section_outline.length === 0}
          >
            <ol className="space-y-2 text-sm">
              {lesson.section_outline.map((s, idx) => {
                const sMatch = s.section_id.match(/\.S(\d+)$/);
                const sectionNum = sMatch ? sMatch[1] : String(idx + 1);
                return (
                <li key={s.section_id}>
                  <div className="flex items-baseline gap-2">
                    <Badge variant="outline" className="text-[10px] shrink-0">
                      {t("courses.architecture.sectionLabel", {
                        n: sectionNum,
                      })}
                    </Badge>
                    <span className="font-medium">{s.title}</span>
                  </div>
                  {s.purpose && (
                    <p className="mt-0.5 ml-12 text-xs text-muted-foreground leading-relaxed">
                      {s.purpose}
                    </p>
                  )}
                  {s.covers_topic_ids.length > 0 && (
                    <div className="mt-1 ml-12 flex flex-wrap gap-1">
                      {s.covers_topic_ids.map((cid) => {
                        const tMatch = cid.match(/\.T(\d+)$/);
                        const topicNum = tMatch ? tMatch[1] : cid;
                        return (
                          <Badge
                            key={cid}
                            variant="secondary"
                            className="text-[10px]"
                          >
                            {t("courses.architecture.topicLabel", {
                              n: topicNum,
                            })}
                          </Badge>
                        );
                      })}
                    </div>
                  )}
                </li>
                );
              })}
            </ol>
          </Subsection>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function ModuleStatusBadge({ status }: { status: LessonsStructureModuleStatus }) {
  const { t } = useTranslation();
  // Per `approved` rendiamo l'ApprovalBadge cross-fase. Mostriamo il
  // badge nativo solo per gli stati di transizione/lavorazione/errore.
  if (status === "approved") return null;
  const variant = (() => {
    switch (status) {
      case "ready":
        return "secondary";
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
      {t(`courses.lessonsStructure.statuses.${status}`)}
    </Badge>
  );
}

interface SubsectionProps {
  icon: React.ReactNode;
  title: string;
  empty: boolean;
  children: React.ReactNode;
}

function Subsection({ icon, title, empty, children }: SubsectionProps) {
  const { t } = useTranslation();
  return (
    <div>
      <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {icon}
        {title}
      </div>
      {empty ? (
        <p className="text-xs italic text-muted-foreground/60">
          {t("courses.lessonsStructure.subsection.empty")}
        </p>
      ) : (
        children
      )}
    </div>
  );
}
