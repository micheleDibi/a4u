import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  ArrowDown,
  ArrowUp,
  BookOpen,
  GraduationCap,
  Loader2,
  Pencil,
  Plus,
  Sparkles,
  Trash2,
} from "lucide-react";
import {
  coursesApi,
  type CourseLessonOut,
  type CourseModuleOut,
  type CourseOut,
} from "@/api/courses";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Separator } from "@/components/ui/separator";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { extractApiError } from "@/lib/errors";
import { LessonEditDialog, type LessonDraft } from "./LessonEditDialog";
import { ModuleEditDialog, type ModuleDraft } from "./ModuleEditDialog";

interface Props {
  course: CourseOut;
  canEdit: boolean;
  orgId: string;
}

type ModuleDialogState =
  | { kind: "closed" }
  | { kind: "create" }
  | { kind: "edit"; module: CourseModuleOut };

type LessonDialogState =
  | { kind: "closed" }
  | { kind: "create"; moduleId: string }
  | { kind: "edit"; lesson: CourseLessonOut };

export function CourseArchitectureView({ course, canEdit, orgId }: Props) {
  const { t } = useTranslation();
  const qc = useQueryClient();

  const [moduleDialog, setModuleDialog] = useState<ModuleDialogState>({
    kind: "closed",
  });
  const [lessonDialog, setLessonDialog] = useState<LessonDialogState>({
    kind: "closed",
  });
  const [confirmDelete, setConfirmDelete] = useState<
    | { kind: "module"; id: string; label: string }
    | { kind: "lesson"; id: string; label: string }
    | null
  >(null);

  const detailKey = ["courses", "detail", orgId, course.id];

  const setCache = (fresh: CourseOut) => {
    qc.setQueryData(detailKey, fresh);
    // Force a refetch as a safety net: garantisce che la UI si aggiorni
    // anche se l'observer principale non si rinfresca per qualche motivo.
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

  // Helper: applica un ri-ordinamento di moduli alla cache locale
  // ricalcolando module_code e lesson_code (specchio del backend).
  const renumberModulesInCache = (current: CourseOut, ids: string[]): CourseOut => {
    const byId = new Map(current.modules.map((m) => [m.id, m]));
    const reordered = ids
      .map((id, i) => {
        const m = byId.get(id);
        if (!m) return null;
        const newCode = `M${i + 1}`;
        return {
          ...m,
          position: i + 1,
          module_code: newCode,
          lessons: m.lessons.map((l, li) => ({
            ...l,
            lesson_code: `${newCode}.L${li + 1}`,
          })),
        };
      })
      .filter((m): m is CourseModuleOut => m !== null);
    return { ...current, modules: reordered };
  };

  const renumberLessonsInCache = (
    current: CourseOut,
    moduleId: string,
    ids: string[]
  ): CourseOut => {
    return {
      ...current,
      modules: current.modules.map((m) => {
        if (m.id !== moduleId) return m;
        const byId = new Map(m.lessons.map((l) => [l.id, l]));
        const reordered = ids
          .map((id, i) => {
            const l = byId.get(id);
            if (!l) return null;
            return {
              ...l,
              position: i + 1,
              lesson_code: `${m.module_code}.L${i + 1}`,
            };
          })
          .filter((l): l is CourseLessonOut => l !== null);
        return { ...m, lessons: reordered };
      }),
    };
  };

  const moduleGenerateLessonsMut = useMutation({
    mutationFn: (moduleId: string) =>
      coursesApi.modules.generateLessons(orgId, course.id, moduleId),
    onSuccess: (fresh) => {
      setCache(fresh);
      toast.success(t("courses.architecture.moduleLessonsGenerated"));
    },
    onError: (err) =>
      toast.error(
        `${t("courses.architecture.moduleLessonsGenerationFailed")}: ${
          extractApiError(err).message
        }`
      ),
  });

  // Progress sintetico per la generazione lezioni AI di un modulo.
  // Il backend è sincrono (~20-25s), quindi simuliamo un avanzamento
  // ease-out da 0 a 90% e lasciamo che la risposta del server porti
  // il modulo allo stato finale (la pill viene smontata).
  const [genProgress, setGenProgress] = useState(0);
  useEffect(() => {
    if (!moduleGenerateLessonsMut.isPending) {
      setGenProgress(0);
      return;
    }
    const start = Date.now();
    const estimatedMs = 25_000;
    const id = window.setInterval(() => {
      const elapsed = Date.now() - start;
      const ratio = Math.min(1, elapsed / estimatedMs);
      const eased = 1 - Math.pow(1 - ratio, 2);
      setGenProgress(Math.round(eased * 90));
    }, 400);
    return () => window.clearInterval(id);
  }, [moduleGenerateLessonsMut.isPending]);

  const moduleCreateMut = useMutation({
    mutationFn: (payload: ModuleDraft) =>
      coursesApi.modules.create(orgId, course.id, payload),
    onSuccess: (fresh) => {
      setCache(fresh);
      setModuleDialog({ kind: "closed" });
      toast.success(t("courses.architecture.module.created"));
      // Auto-trigger AI lesson generation per il nuovo modulo (l'ultimo
      // della lista, dato che i moduli sono ordinati per position).
      const newModule = fresh.modules.find((m) => m.lessons.length === 0);
      if (newModule) {
        moduleGenerateLessonsMut.mutate(newModule.id);
      }
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const moduleUpdateMut = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: ModuleDraft }) =>
      coursesApi.modules.update(orgId, course.id, id, payload),
    onSuccess: (fresh) => {
      setCache(fresh);
      setModuleDialog({ kind: "closed" });
      toast.success(t("courses.architecture.module.updated"));
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const moduleDeleteMut = useMutation({
    mutationFn: (id: string) =>
      coursesApi.modules.remove(orgId, course.id, id),
    onSuccess: (fresh) => {
      setCache(fresh);
      toast.success(t("courses.architecture.module.deleted"));
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const moduleReorderMut = useMutation({
    mutationFn: (ids: string[]) =>
      coursesApi.modules.reorder(orgId, course.id, ids),
    // Optimistic update: rinumera localmente prima della chiamata.
    onMutate: async (ids) => {
      await qc.cancelQueries({ queryKey: detailKey });
      const previous = qc.getQueryData<CourseOut>(detailKey);
      if (previous) {
        qc.setQueryData<CourseOut>(detailKey, renumberModulesInCache(previous, ids));
      }
      return { previous };
    },
    onSuccess: (fresh) => setCache(fresh),
    onError: (err, _ids, ctx) => {
      if (ctx?.previous) qc.setQueryData(detailKey, ctx.previous);
      toast.error(extractApiError(err).message);
    },
  });

  const lessonCreateMut = useMutation({
    mutationFn: ({
      moduleId,
      payload,
    }: {
      moduleId: string;
      payload: LessonDraft;
    }) => coursesApi.lessons.create(orgId, course.id, moduleId, payload),
    onSuccess: (fresh) => {
      setCache(fresh);
      setLessonDialog({ kind: "closed" });
      toast.success(t("courses.architecture.lesson.created"));
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const lessonUpdateMut = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: LessonDraft }) =>
      coursesApi.lessons.update(orgId, course.id, id, payload),
    onSuccess: (fresh) => {
      setCache(fresh);
      setLessonDialog({ kind: "closed" });
      toast.success(t("courses.architecture.lesson.updated"));
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const lessonDeleteMut = useMutation({
    mutationFn: (id: string) =>
      coursesApi.lessons.remove(orgId, course.id, id),
    onSuccess: (fresh) => {
      setCache(fresh);
      toast.success(t("courses.architecture.lesson.deleted"));
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const lessonReorderMut = useMutation({
    mutationFn: ({
      moduleId,
      ids,
    }: {
      moduleId: string;
      ids: string[];
    }) => coursesApi.lessons.reorder(orgId, course.id, moduleId, ids),
    onMutate: async ({ moduleId, ids }) => {
      await qc.cancelQueries({ queryKey: detailKey });
      const previous = qc.getQueryData<CourseOut>(detailKey);
      if (previous) {
        qc.setQueryData<CourseOut>(
          detailKey,
          renumberLessonsInCache(previous, moduleId, ids)
        );
      }
      return { previous };
    },
    onSuccess: (fresh) => setCache(fresh),
    onError: (err, _vars, ctx) => {
      if (ctx?.previous) qc.setQueryData(detailKey, ctx.previous);
      toast.error(extractApiError(err).message);
    },
  });

  const moveModule = (idx: number, dir: -1 | 1) => {
    const ids = course.modules.map((m) => m.id);
    const target = idx + dir;
    if (target < 0 || target >= ids.length) return;
    [ids[idx], ids[target]] = [ids[target], ids[idx]];
    moduleReorderMut.mutate(ids);
  };

  const moveLesson = (
    moduleId: string,
    lessons: CourseLessonOut[],
    idx: number,
    dir: -1 | 1
  ) => {
    const ids = lessons.map((l) => l.id);
    const target = idx + dir;
    if (target < 0 || target >= ids.length) return;
    [ids[idx], ids[target]] = [ids[target], ids[idx]];
    lessonReorderMut.mutate({ moduleId, ids });
  };

  if (!course.modules || course.modules.length === 0) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-muted-foreground">
          {t("courses.architecture.empty")}
        </p>
        {canEdit && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => setModuleDialog({ kind: "create" })}
          >
            <Plus className="size-4" />
            {t("courses.architecture.module.add")}
          </Button>
        )}
        <ModuleEditDialog
          open={moduleDialog.kind !== "closed"}
          mode={moduleDialog.kind === "edit" ? "edit" : "create"}
          isPending={moduleCreateMut.isPending}
          onClose={() => setModuleDialog({ kind: "closed" })}
          onSubmit={(draft) => moduleCreateMut.mutate(draft)}
        />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Overview + Razionale */}
      {(course.course_overview || course.pedagogical_rationale) && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <Sparkles className="size-4" />
              {t("courses.architecture.view.overviewTitle")}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {course.course_overview && (
              <div>
                <h4 className="mb-1 text-xs font-medium uppercase tracking-wider text-muted-foreground">
                  {t("courses.architecture.view.overview")}
                </h4>
                <p className="whitespace-pre-line text-sm">
                  {course.course_overview}
                </p>
              </div>
            )}
            {course.pedagogical_rationale && (
              <div>
                <h4 className="mb-1 text-xs font-medium uppercase tracking-wider text-muted-foreground">
                  {t("courses.architecture.view.rationale")}
                </h4>
                <p className="whitespace-pre-line text-sm">
                  {course.pedagogical_rationale}
                </p>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Modules */}
      {course.modules.map((module, mIdx) => (
        <Card key={module.id}>
          <CardHeader className="pb-3">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <CardTitle className="flex items-center gap-2 text-base">
                <Badge variant="secondary">{moduleLabel(module.module_code)}</Badge>
                <span>{module.title}</span>
              </CardTitle>
              {canEdit && (
                <div className="flex items-center gap-1">
                  <Button
                    variant="ghost"
                    size="icon"
                    title={t("common.moveUp")}
                    disabled={mIdx === 0}
                    onClick={() => moveModule(mIdx, -1)}
                  >
                    <ArrowUp className="size-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    title={t("common.moveDown")}
                    disabled={mIdx === course.modules.length - 1}
                    onClick={() => moveModule(mIdx, 1)}
                  >
                    <ArrowDown className="size-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    title={t("common.edit")}
                    onClick={() =>
                      setModuleDialog({ kind: "edit", module })
                    }
                  >
                    <Pencil className="size-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="text-destructive"
                    title={t("common.delete")}
                    onClick={() =>
                      setConfirmDelete({
                        kind: "module",
                        id: module.id,
                        label: `${moduleLabel(module.module_code)} — ${module.title}`,
                      })
                    }
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              )}
            </div>
            {module.description && (
              <p className="mt-1 text-sm text-muted-foreground">
                {module.description}
              </p>
            )}
          </CardHeader>
          <CardContent className="space-y-3">
            {module.lessons.length === 0 &&
              moduleGenerateLessonsMut.isPending &&
              moduleGenerateLessonsMut.variables === module.id && (
                <div className="space-y-2 rounded-md border border-dashed border-primary/40 bg-primary/5 p-4 text-sm">
                  <div className="flex items-center gap-2">
                    <Loader2 className="size-4 animate-spin text-primary" />
                    <span className="font-medium">
                      {t("courses.architecture.moduleGenerating")}
                    </span>
                    <span className="ms-auto font-mono text-xs tabular-nums text-muted-foreground">
                      {genProgress}%
                    </span>
                  </div>
                  <Progress value={genProgress} />
                </div>
              )}
            {module.lessons.length === 0 &&
              !(
                moduleGenerateLessonsMut.isPending &&
                moduleGenerateLessonsMut.variables === module.id
              ) &&
              canEdit && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => moduleGenerateLessonsMut.mutate(module.id)}
                  disabled={moduleGenerateLessonsMut.isPending}
                >
                  <Sparkles className="size-4" />
                  {t("courses.architecture.moduleGenerateLessons")}
                </Button>
              )}
            {module.lessons.map((lesson, lIdx) => (
              <div
                key={lesson.id}
                className="rounded-md border border-border p-3"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="outline">
                      {lessonLabel(lesson.lesson_code)}
                    </Badge>
                    <h5 className="text-sm font-medium">{lesson.title}</h5>
                    {lesson.is_introductory && (
                      <Badge variant="brand">
                        <GraduationCap className="size-3" />
                        {t("courses.architecture.view.introductory")}
                      </Badge>
                    )}
                  </div>
                  {canEdit && (
                    <div className="flex items-center gap-1">
                      <Button
                        variant="ghost"
                        size="icon"
                        title={t("common.moveUp")}
                        disabled={lIdx === 0}
                        onClick={() =>
                          moveLesson(module.id, module.lessons, lIdx, -1)
                        }
                      >
                        <ArrowUp className="size-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        title={t("common.moveDown")}
                        disabled={lIdx === module.lessons.length - 1}
                        onClick={() =>
                          moveLesson(module.id, module.lessons, lIdx, 1)
                        }
                      >
                        <ArrowDown className="size-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        title={t("common.edit")}
                        onClick={() =>
                          setLessonDialog({ kind: "edit", lesson })
                        }
                      >
                        <Pencil className="size-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="text-destructive"
                        title={t("common.delete")}
                        onClick={() =>
                          setConfirmDelete({
                            kind: "lesson",
                            id: lesson.id,
                            label: `${lessonLabel(lesson.lesson_code)} — ${lesson.title}`,
                          })
                        }
                      >
                        <Trash2 className="size-4" />
                      </Button>
                    </div>
                  )}
                </div>
                {lesson.summary && (
                  <p className="mt-2 text-xs text-muted-foreground">
                    {lesson.summary}
                  </p>
                )}
                {lesson.is_introductory &&
                  lesson.recommended_bibliography.length > 0 && (
                    <>
                      <Separator className="my-3" />
                      <h6 className="mb-2 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-muted-foreground">
                        <BookOpen className="size-3" />
                        {t("courses.architecture.view.bibliography")}
                      </h6>
                      <ul className="space-y-1.5 text-xs">
                        {lesson.recommended_bibliography.map((b, i) => (
                          <li
                            key={i}
                            className="rounded bg-muted/40 p-2"
                          >
                            <div className="flex items-baseline gap-2">
                              <span className="font-medium">{b.authors}</span>
                              <span className="italic">{b.title}</span>
                            </div>
                            <div className="text-[11px] text-muted-foreground">
                              {b.publisher}, {b.year}
                            </div>
                            {b.note && (
                              <p className="mt-1 text-[11px]">{b.note}</p>
                            )}
                            <div className="mt-1 flex flex-wrap gap-1">
                              <Badge
                                variant={
                                  b.source === "from_uploaded_documents"
                                    ? "brand"
                                    : "muted"
                                }
                                className="text-[10px]"
                              >
                                {t(
                                  `courses.architecture.view.bibliographySource.${b.source}`
                                )}
                              </Badge>
                              <Badge
                                variant={
                                  b.confidence === "confirmed"
                                    ? "default"
                                    : "warning"
                                }
                                className="text-[10px]"
                              >
                                {t(
                                  `courses.architecture.view.bibliographyConfidence.${b.confidence}`
                                )}
                              </Badge>
                            </div>
                          </li>
                        ))}
                      </ul>
                    </>
                  )}
              </div>
            ))}
            {canEdit && (
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  setLessonDialog({ kind: "create", moduleId: module.id })
                }
              >
                <Plus className="size-4" />
                {t("courses.architecture.lesson.add")}
              </Button>
            )}
          </CardContent>
        </Card>
      ))}

      {canEdit && (
        <Button
          variant="outline"
          onClick={() => setModuleDialog({ kind: "create" })}
        >
          <Plus className="size-4" />
          {t("courses.architecture.module.add")}
        </Button>
      )}

      <ModuleEditDialog
        open={moduleDialog.kind !== "closed"}
        mode={moduleDialog.kind === "edit" ? "edit" : "create"}
        initial={
          moduleDialog.kind === "edit"
            ? {
                title: moduleDialog.module.title,
                description: moduleDialog.module.description,
              }
            : undefined
        }
        meta={
          moduleDialog.kind === "edit"
            ? {
                code: moduleLabel(moduleDialog.module.module_code),
                lessonsCount: moduleDialog.module.lessons.length,
              }
            : undefined
        }
        isPending={moduleCreateMut.isPending || moduleUpdateMut.isPending}
        onClose={() => setModuleDialog({ kind: "closed" })}
        onSubmit={(draft) => {
          if (moduleDialog.kind === "edit") {
            moduleUpdateMut.mutate({
              id: moduleDialog.module.id,
              payload: draft,
            });
          } else {
            moduleCreateMut.mutate(draft);
          }
        }}
      />

      <LessonEditDialog
        open={lessonDialog.kind !== "closed"}
        mode={lessonDialog.kind === "edit" ? "edit" : "create"}
        initial={
          lessonDialog.kind === "edit"
            ? {
                title: lessonDialog.lesson.title,
                summary: lessonDialog.lesson.summary,
                is_introductory: lessonDialog.lesson.is_introductory,
                recommended_bibliography:
                  lessonDialog.lesson.recommended_bibliography,
              }
            : undefined
        }
        meta={(() => {
          if (lessonDialog.kind === "edit") {
            const parent = course.modules.find(
              (m) => m.id === lessonDialog.lesson.module_id
            );
            return {
              code: lessonLabel(lessonDialog.lesson.lesson_code),
              moduleLabel: parent
                ? `${moduleLabel(parent.module_code)} — ${parent.title}`
                : undefined,
            };
          }
          if (lessonDialog.kind === "create") {
            const parent = course.modules.find(
              (m) => m.id === lessonDialog.moduleId
            );
            return {
              moduleLabel: parent
                ? `${moduleLabel(parent.module_code)} — ${parent.title}`
                : undefined,
            };
          }
          return undefined;
        })()}
        isPending={lessonCreateMut.isPending || lessonUpdateMut.isPending}
        onClose={() => setLessonDialog({ kind: "closed" })}
        onSubmit={(draft) => {
          if (lessonDialog.kind === "edit") {
            lessonUpdateMut.mutate({
              id: lessonDialog.lesson.id,
              payload: draft,
            });
          } else if (lessonDialog.kind === "create") {
            lessonCreateMut.mutate({
              moduleId: lessonDialog.moduleId,
              payload: draft,
            });
          }
        }}
      />

      <ConfirmDialog
        open={!!confirmDelete}
        title={
          confirmDelete?.kind === "module"
            ? t("courses.architecture.module.deleteConfirmTitle")
            : t("courses.architecture.lesson.deleteConfirmTitle")
        }
        message={
          confirmDelete?.kind === "module"
            ? t("courses.architecture.module.deleteConfirmMessage", {
                label: confirmDelete?.label,
              })
            : t("courses.architecture.lesson.deleteConfirmMessage", {
                label: confirmDelete?.label,
              })
        }
        destructive
        confirmLabel={t("common.delete")}
        onClose={() => setConfirmDelete(null)}
        onConfirm={() => {
          if (!confirmDelete) return;
          if (confirmDelete.kind === "module") {
            moduleDeleteMut.mutate(confirmDelete.id);
          } else {
            lessonDeleteMut.mutate(confirmDelete.id);
          }
          setConfirmDelete(null);
        }}
      />
    </div>
  );
}
