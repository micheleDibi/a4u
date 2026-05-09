import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  Loader2,
  Minus,
  Plus,
  RotateCw,
  Save,
  Sparkles,
} from "lucide-react";
import {
  coursesApi,
  type CourseCreateInput,
  type CourseOut,
  type CourseUpdateInput,
  type TaxonomyAssignmentsInput,
} from "@/api/courses";
import { useAuth } from "@/auth/AuthContext";
import { useHasPermission } from "@/auth/PermissionGate";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Progress } from "@/components/ui/progress";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { useLanguages } from "@/hooks/useLanguages";
import { useTaskEta } from "@/hooks/useTaskEta";
import { flagFor } from "@/i18n/flags";
import { extractApiError } from "@/lib/errors";
import { formatDuration } from "@/lib/formatDuration";
import { P } from "@/lib/permissions";
import { CourseArchitectureView } from "./components/CourseArchitectureView";
import { CourseDocumentUploader } from "./components/CourseDocumentUploader";
import { CourseLessonStructureView } from "./components/CourseLessonStructureView";
import { CourseLessonContentView } from "./components/CourseLessonContentView";
import { CourseStatusBadge } from "./components/CourseStatusBadge";
import { GenerateArchitectureDialog } from "./components/GenerateArchitectureDialog";
import { KeywordTagsInput } from "./components/KeywordTagsInput";
import { MemberSelect } from "./components/MemberSelect";
import { TaxonomyTermSelect } from "./components/TaxonomyTermSelect";

type Mode = "create" | "edit";

interface DraftState {
  title: string;
  objectives: string;
  language_code: string;
  cfu: number;
  argomenti_chiave: string[];
  assignee_user_id: string;
  taxonomies: TaxonomyAssignmentsInput;
}

function emptyDraft(currentUserId: string, defaultLang: string): DraftState {
  return {
    title: "",
    objectives: "",
    language_code: defaultLang,
    cfu: 6,
    argomenti_chiave: [],
    assignee_user_id: currentUserId,
    taxonomies: {
      categoria: null,
      stile_insegnamento: null,
      profondita_contenuto: null,
      ruolo_docente: null,
      dimensione_pubblico: null,
      livello_conoscenza: null,
      destinatari: null,
      livello_eqf: null,
    },
  };
}

function fromCourse(course: CourseOut): DraftState {
  return {
    title: course.title,
    objectives: course.objectives,
    language_code: course.language_code,
    cfu: course.cfu,
    argomenti_chiave: [...course.argomenti_chiave],
    assignee_user_id: course.assignee.id,
    taxonomies: {
      categoria: course.categoria?.id ?? null,
      stile_insegnamento: course.stile_insegnamento?.id ?? null,
      profondita_contenuto: course.profondita_contenuto?.id ?? null,
      ruolo_docente: course.ruolo_docente?.id ?? null,
      dimensione_pubblico: course.dimensione_pubblico?.id ?? null,
      livello_conoscenza: course.livello_conoscenza?.id ?? null,
      destinatari: course.destinatari?.id ?? null,
      livello_eqf: course.livello_eqf?.id ?? null,
    },
  };
}

function buildUpdatePayload(
  draft: DraftState,
  currentAssigneeId: string
): { update: CourseUpdateInput; assigneeChange?: string } {
  const update: CourseUpdateInput = {
    title: draft.title,
    objectives: draft.objectives,
    language_code: draft.language_code,
    cfu: draft.cfu,
    argomenti_chiave: draft.argomenti_chiave,
    taxonomies: draft.taxonomies,
  };
  return {
    update,
    assigneeChange:
      draft.assignee_user_id !== currentAssigneeId
        ? draft.assignee_user_id
        : undefined,
  };
}

interface Props {
  mode: Mode;
}

export default function CourseEditorPage({ mode }: Props) {
  const { t, i18n } = useTranslation();
  const params = useParams();
  const orgId = params.orgId!;
  const courseId = params.courseId; // undefined in create
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { me } = useAuth();
  const langs = useLanguages();

  const canEdit = useHasPermission(P.COURSE_EDIT, orgId);
  const canAssign = useHasPermission(P.COURSE_ASSIGN, orgId);
  const canGenerate = useHasPermission(P.COURSE_GENERATE, orgId);

  const courseQuery = useQuery({
    queryKey: ["courses", "detail", orgId, courseId],
    queryFn: () => coursesApi.get(orgId, courseId!),
    enabled: mode === "edit" && !!courseId,
    // Polling: quando un documento è in elaborazione, oppure il corso è
    // in `architecture_pending` (worker sta generando), oppure almeno un
    // modulo è in elaborazione per la struttura delle lezioni (Fase 2).
    refetchInterval: (q) => {
      const data = q.state.data;
      if (!data) return false;
      if (data.status === "architecture_pending") return 5000;
      const anyActiveDoc = (data.documents ?? []).some(
        (d) =>
          d.summary_status === "pending" || d.summary_status === "processing"
      );
      if (anyActiveDoc) return 5000;
      const anyActiveLessonStructure = (data.modules ?? []).some(
        (m) =>
          m.lessons_structure_status === "pending" ||
          m.lessons_structure_status === "processing"
      );
      if (anyActiveLessonStructure) return 5000;
      const anyActiveLessonContent = (data.modules ?? []).some((m) =>
        (m.lessons ?? []).some(
          (l) =>
            l.content_status === "pending" || l.content_status === "processing"
        )
      );
      if (anyActiveLessonContent) return 5000;
      const anyActiveLessonPdf = (data.modules ?? []).some((m) =>
        (m.lessons ?? []).some(
          (l) =>
            l.pdf_status === "pending" || l.pdf_status === "processing"
        )
      );
      if (anyActiveLessonPdf) return 4000;
      if (
        data.glossary_status === "pending" ||
        data.glossary_status === "processing"
      ) {
        return 5000;
      }
      return false;
    },
  });

  const course = courseQuery.data ?? null;
  const readOnly = mode === "edit" && !canEdit;

  const defaultLang = i18n.resolvedLanguage?.split("-")[0] || "it";
  const [draft, setDraft] = useState<DraftState | null>(null);
  // Tracks the saved server-state used as baseline for diffing in auto-save.
  const baselineRef = useRef<DraftState | null>(null);
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [saveState, setSaveState] = useState<
    | { kind: "idle" }
    | { kind: "saving" }
    | { kind: "saved"; at: Date }
    | { kind: "error"; message: string }
  >({ kind: "idle" });

  // Inizializza draft.
  useEffect(() => {
    if (mode === "create" && me && !draft) {
      const d = emptyDraft(me.user.id, defaultLang);
      setDraft(d);
      baselineRef.current = d;
    } else if (mode === "edit" && course && !draft) {
      const d = fromCourse(course);
      setDraft(d);
      baselineRef.current = d;
    }
  }, [mode, me, course, defaultLang, draft]);

  const updateMut = useMutation({
    mutationFn: async (payload: CourseUpdateInput) => {
      return coursesApi.update(orgId, courseId!, payload);
    },
    onSuccess: (fresh) => {
      qc.setQueryData(["courses", "detail", orgId, courseId], fresh);
      qc.invalidateQueries({ queryKey: ["courses", "list", orgId] });
      // Aggiorna baseline (escluso assignee, che è gestito via mutation separata).
      const d = fromCourse(fresh);
      baselineRef.current = d;
      setSaveState({ kind: "saved", at: new Date() });
    },
    onError: (err) => {
      const msg = extractApiError(err).message;
      toast.error(msg);
      setSaveState({ kind: "error", message: msg });
    },
  });

  const assigneeMut = useMutation({
    mutationFn: (newAssigneeId: string) =>
      coursesApi.updateAssignee(orgId, courseId!, newAssigneeId),
    onSuccess: (fresh) => {
      qc.setQueryData(["courses", "detail", orgId, courseId], fresh);
      qc.invalidateQueries({ queryKey: ["courses", "list", orgId] });
      const d = fromCourse(fresh);
      baselineRef.current = d;
      toast.success(t("courses.assigneeUpdated"));
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const createMut = useMutation({
    mutationFn: (payload: CourseCreateInput) => coursesApi.create(orgId, payload),
    onSuccess: (fresh) => {
      qc.invalidateQueries({ queryKey: ["courses", "list", orgId] });
      qc.setQueryData(["courses", "detail", orgId, fresh.id], fresh);
      toast.success(t("courses.created"));
      // Naviga alla pagina edit conservando i valori del draft.
      navigate(`/orgs/${orgId}/corsi/${fresh.id}`, { replace: true });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  // Architettura — Fase 1.
  const [archDialogOpen, setArchDialogOpen] = useState(false);
  const generateArchMut = useMutation({
    mutationFn: (hint: string | null) =>
      coursesApi.architecture.generate(orgId, courseId!, hint),
    onSuccess: (fresh) => {
      qc.setQueryData(["courses", "detail", orgId, courseId], fresh);
      qc.invalidateQueries({ queryKey: ["courses", "list", orgId] });
      toast.success(t("courses.architecture.requested"));
      setArchDialogOpen(false);
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });
  const approveArchMut = useMutation({
    mutationFn: () => coursesApi.architecture.approve(orgId, courseId!),
    onSuccess: (fresh) => {
      qc.setQueryData(["courses", "detail", orgId, courseId], fresh);
      qc.invalidateQueries({ queryKey: ["courses", "list", orgId] });
      toast.success(t("courses.architecture.approved"));
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const performAutoSave = useCallback(() => {
    if (mode !== "edit" || !courseId || !draft || readOnly) return;
    const baseline = baselineRef.current;
    if (!baseline) return;
    const { update, assigneeChange } = buildUpdatePayload(
      draft,
      baseline.assignee_user_id
    );
    setSaveState({ kind: "saving" });

    // Update fields prima, poi (eventualmente) assignee.
    if (assigneeChange && canAssign) {
      assigneeMut.mutate(assigneeChange);
    }
    updateMut.mutate(update);
  }, [mode, courseId, draft, readOnly, canAssign, assigneeMut, updateMut]);

  // Auto-save debounce su draft change.
  useEffect(() => {
    if (mode !== "edit" || !courseId || !draft || readOnly) return;
    const baseline = baselineRef.current;
    if (!baseline) return;
    // Stable comparison via JSON.stringify (chiavi nello stesso ordine).
    if (JSON.stringify(draft) === JSON.stringify(baseline)) return;
    if (debounceTimer.current) clearTimeout(debounceTimer.current);
    debounceTimer.current = setTimeout(() => {
      performAutoSave();
    }, 1500);
    return () => {
      if (debounceTimer.current) clearTimeout(debounceTimer.current);
    };
  }, [draft, mode, courseId, readOnly, performAutoSave]);

  // Avviso prima di lasciare la pagina con modifiche non salvate.
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      const baseline = baselineRef.current;
      if (
        baseline &&
        draft &&
        JSON.stringify(draft) !== JSON.stringify(baseline)
      ) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [draft]);

  const submit = () => {
    if (!draft) return;
    if (!draft.title.trim()) {
      toast.error(t("courses.errors.titleRequired"));
      return;
    }
    if (mode === "create") {
      const payload: CourseCreateInput = {
        title: draft.title.trim(),
        objectives: draft.objectives.trim(),
        language_code: draft.language_code,
        cfu: draft.cfu,
        argomenti_chiave: draft.argomenti_chiave,
        assignee_user_id: draft.assignee_user_id,
        taxonomies: draft.taxonomies,
      };
      createMut.mutate(payload);
    } else {
      performAutoSave();
    }
  };

  const summary = useMemo(() => {
    if (!draft) return null;
    if (mode === "edit" && course) {
      const totalLessons = course.modules_count * course.lessons_per_module;
      const totalMinutes = totalLessons * course.lesson_duration_minutes;
      return {
        modules: course.modules_count,
        lessonsPerModule: course.lessons_per_module,
        totalLessons,
        totalMinutes,
        durationMinutes: course.lesson_duration_minutes,
        assessment: course.assessment_lesson_enabled,
        mc: course.multiple_choice_questions_count,
        open: course.open_questions_count,
      };
    }
    // In create non abbiamo ancora i settings org. Mostriamo solo CFU input,
    // gli altri si vedranno dopo il salvataggio.
    return null;
  }, [mode, course, draft]);

  if (!me || !draft || (mode === "edit" && courseQuery.isLoading)) {
    return (
      <div className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
        {t("common.loading")}
      </div>
    );
  }

  const headerTitle =
    mode === "create"
      ? t("courses.create")
      : course?.title || t("courses.edit");

  return (
    <div className="space-y-6">
      <PageHeader
        title={
          <span className="flex items-center gap-3">
            <Button
              variant="ghost"
              size="icon"
              onClick={() => navigate(`/orgs/${orgId}/corsi`)}
              aria-label={t("common.back")}
            >
              <ArrowLeft className="size-4" />
            </Button>
            <span>{headerTitle}</span>
            {course && <CourseStatusBadge status={course.status} />}
          </span>
        }
        description={
          mode === "create"
            ? t("courses.createSubtitle")
            : t("courses.editSubtitle")
        }
        actions={
          mode === "edit" && saveState.kind !== "idle" ? (
            <div className="flex items-center gap-2 text-xs">
              {saveState.kind === "saving" && (
                <span className="flex items-center gap-1.5 text-muted-foreground">
                  <Loader2 className="size-3.5 animate-spin" />
                  {t("courses.autoSave.saving")}
                </span>
              )}
              {saveState.kind === "saved" && (
                <span className="flex items-center gap-1.5 text-muted-foreground">
                  <CheckCircle2 className="size-3.5 text-green-600" />
                  {t("courses.autoSave.savedAt", {
                    time: saveState.at.toLocaleTimeString(),
                  })}
                </span>
              )}
              {saveState.kind === "error" && (
                <>
                  <span
                    className="flex items-center gap-1.5 text-destructive"
                    title={saveState.message}
                  >
                    <AlertCircle className="size-3.5" />
                    {t("courses.autoSave.errorShort")}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={performAutoSave}
                    disabled={updateMut.isPending}
                  >
                    <Save className="size-3.5" />
                    {t("courses.saveNow")}
                  </Button>
                </>
              )}
            </div>
          ) : null
        }
      />

      <Tabs defaultValue="base" className="space-y-4">
        <TabsList className="flex h-auto w-full flex-wrap justify-start gap-1">
          <TabsTrigger value="base">{t("courses.tabs.base")}</TabsTrigger>
          <TabsTrigger value="didactic">
            {t("courses.tabs.didactic")}
          </TabsTrigger>
          {mode === "edit" && (
            <TabsTrigger value="documents">
              {t("courses.tabs.documents")}
            </TabsTrigger>
          )}
          {mode === "edit" && (
            <TabsTrigger value="architecture">
              {t("courses.tabs.architecture")}
            </TabsTrigger>
          )}
          {mode === "edit" && (
            <TabsTrigger
              value="lessons-structure"
              disabled={
                !course ||
                (course.status !== "architecture_approved" &&
                  course.status !== "lessons_structure_pending" &&
                  course.status !== "lessons_structure_ready" &&
                  course.status !== "lessons_structure_approved" &&
                  !course.status.startsWith("content_") &&
                  !["slides_pending", "slides_ready", "speech_pending", "speech_ready", "published"].includes(
                    course.status
                  ))
              }
            >
              {t("courses.tabs.lessonsStructure")}
            </TabsTrigger>
          )}
          {mode === "edit" && (
            <TabsTrigger
              value="lesson-content"
              disabled={
                !course ||
                (course.status !== "lessons_structure_approved" &&
                  !course.status.startsWith("content_") &&
                  !["slides_pending", "slides_ready", "speech_pending", "speech_ready", "published"].includes(
                    course.status
                  ))
              }
            >
              {t("courses.tabs.lessonContent")}
            </TabsTrigger>
          )}
        </TabsList>

        {/* Tab — Informazioni di base */}
        <TabsContent value="base" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>{t("courses.sections.basics")}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="course-title">
                  {t("courses.fields.title")}
                </Label>
                <Input
                  id="course-title"
                  value={draft.title}
                  onChange={(e) =>
                    setDraft({ ...draft, title: e.target.value })
                  }
                  disabled={readOnly}
                  placeholder={t("courses.fields.titlePlaceholder")}
                  maxLength={200}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="course-objectives">
                  {t("courses.fields.objectives")}
                </Label>
                <Textarea
                  id="course-objectives"
                  rows={4}
                  value={draft.objectives}
                  onChange={(e) =>
                    setDraft({ ...draft, objectives: e.target.value })
                  }
                  disabled={readOnly}
                  placeholder={t("courses.fields.objectivesPlaceholder")}
                />
              </div>
              {/* Argomenti chiave — direttamente sotto Obiettivi */}
              <div className="space-y-1.5">
                <Label>{t("courses.sections.keywords")}</Label>
                <KeywordTagsInput
                  value={draft.argomenti_chiave}
                  onChange={(v) =>
                    setDraft({ ...draft, argomenti_chiave: v })
                  }
                  disabled={readOnly}
                />
              </div>
              {/* Categoria — spostata dall'Inquadramento */}
              <div className="space-y-1.5">
                <Label>{t("courses.taxonomies.categoria")}</Label>
                <TaxonomyTermSelect
                  taxonomyType="category"
                  hierarchical
                  value={draft.taxonomies.categoria}
                  onChange={(v) =>
                    setDraft({
                      ...draft,
                      taxonomies: { ...draft.taxonomies, categoria: v },
                    })
                  }
                  disabled={readOnly}
                />
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label>{t("courses.fields.language")}</Label>
                  <Select
                    value={draft.language_code}
                    onValueChange={(v) =>
                      setDraft({ ...draft, language_code: v })
                    }
                    disabled={readOnly}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {langs.map((l) => {
                        const Flag = flagFor(l.code, l.flag_country_code);
                        return (
                          <SelectItem key={l.code} value={l.code}>
                            <span className="inline-flex items-center gap-2">
                              <Flag className="size-4" />
                              <span className="uppercase">{l.code}</span>
                              <span className="text-muted-foreground">
                                — {l.name_native}
                              </span>
                            </span>
                          </SelectItem>
                        );
                      })}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label>{t("courses.fields.assignee")}</Label>
                  <MemberSelect
                    orgId={orgId}
                    value={draft.assignee_user_id}
                    onChange={(v) =>
                      setDraft({ ...draft, assignee_user_id: v })
                    }
                    disabled={readOnly || (mode === "edit" && !canAssign)}
                  />
                </div>
              </div>

              {/* CFU — spostato dentro Informazioni di base */}
              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-1.5">
                  <Label>{t("courses.fields.cfu")}</Label>
                  <div className="flex items-center gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      size="icon"
                      disabled={readOnly || draft.cfu <= 1}
                      onClick={() =>
                        setDraft({ ...draft, cfu: Math.max(1, draft.cfu - 1) })
                      }
                    >
                      <Minus className="size-4" />
                    </Button>
                    <Input
                      type="number"
                      min={1}
                      max={200}
                      value={draft.cfu}
                      onChange={(e) => {
                        const n = parseInt(e.target.value, 10);
                        if (!isNaN(n) && n >= 1) {
                          setDraft({ ...draft, cfu: n });
                        }
                      }}
                      disabled={readOnly}
                      className="text-center"
                    />
                    <Button
                      type="button"
                      variant="outline"
                      size="icon"
                      disabled={readOnly || draft.cfu >= 200}
                      onClick={() => setDraft({ ...draft, cfu: draft.cfu + 1 })}
                    >
                      <Plus className="size-4" />
                    </Button>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {t("courses.fields.cfuHint")}
                  </p>
                  {mode === "create" && (
                    <p className="text-xs text-muted-foreground">
                      {t("courses.summary.createHint")}
                    </p>
                  )}
                </div>

                {summary && (
                  <div className="rounded-md bg-muted/30 p-3 text-sm">
                    <h4 className="mb-2 font-medium">
                      {t("courses.summary.title")}
                    </h4>
                    <dl className="space-y-1 text-xs">
                      <div className="flex justify-between">
                        <dt className="text-muted-foreground">
                          {t("courses.summary.modules")}
                        </dt>
                        <dd>{summary.modules}</dd>
                      </div>
                      <div className="flex justify-between">
                        <dt className="text-muted-foreground">
                          {t("courses.summary.lessonsPerModule")}
                        </dt>
                        <dd>{summary.lessonsPerModule}</dd>
                      </div>
                      <div className="flex justify-between">
                        <dt className="text-muted-foreground">
                          {t("courses.summary.totalLessons")}
                        </dt>
                        <dd>{summary.totalLessons}</dd>
                      </div>
                      <div className="flex justify-between">
                        <dt className="text-muted-foreground">
                          {t("courses.summary.lessonDuration")}
                        </dt>
                        <dd>
                          {t("courses.summary.minutes", {
                            minutes: summary.durationMinutes,
                          })}
                        </dd>
                      </div>
                      <div className="flex justify-between">
                        <dt className="text-muted-foreground">
                          {t("courses.summary.totalDuration")}
                        </dt>
                        <dd>
                          {t("courses.summary.hoursAndMinutes", {
                            hours: Math.floor(summary.totalMinutes / 60),
                            minutes: summary.totalMinutes % 60,
                          })}
                        </dd>
                      </div>
                      <div className="border-t border-border pt-1">
                        <div className="flex justify-between">
                          <dt className="text-muted-foreground">
                            {t("courses.summary.assessment")}
                          </dt>
                          <dd>
                            {summary.assessment
                              ? t("courses.summary.assessmentEnabled", {
                                  mc: summary.mc,
                                  open: summary.open,
                                })
                              : t("courses.summary.assessmentDisabled")}
                          </dd>
                        </div>
                      </div>
                    </dl>
                  </div>
                )}
              </div>
            </CardContent>
          </Card>
          {mode === "create" && !readOnly && (
            <div className="flex justify-end">
              <Button
                onClick={submit}
                disabled={createMut.isPending || !draft.title.trim()}
                size="lg"
              >
                {createMut.isPending ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : null}
                {createMut.isPending
                  ? t("common.saving")
                  : t("courses.createAndContinue")}
                {!createMut.isPending && <ArrowRight className="size-4" />}
              </Button>
            </div>
          )}
        </TabsContent>

        {/* Tab — Inquadramento didattico (categoria spostata in Base) */}
        <TabsContent value="didactic">
          <Card>
            <CardHeader>
              <CardTitle>{t("courses.sections.didactic")}</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label>{t("courses.taxonomies.destinatari")}</Label>
                <TaxonomyTermSelect
                  taxonomyType="target_audience"
                  value={draft.taxonomies.destinatari}
                  onChange={(v) =>
                    setDraft({
                      ...draft,
                      taxonomies: { ...draft.taxonomies, destinatari: v },
                    })
                  }
                  disabled={readOnly}
                />
              </div>
              <div className="space-y-1.5">
                <Label>{t("courses.taxonomies.livelloEqf")}</Label>
                <TaxonomyTermSelect
                  taxonomyType="eqf_level"
                  hierarchical
                  value={draft.taxonomies.livello_eqf}
                  onChange={(v) =>
                    setDraft({
                      ...draft,
                      taxonomies: { ...draft.taxonomies, livello_eqf: v },
                    })
                  }
                  disabled={readOnly}
                />
              </div>
              <div className="space-y-1.5">
                <Label>{t("courses.taxonomies.livelloConoscenza")}</Label>
                <TaxonomyTermSelect
                  taxonomyType="knowledge_level"
                  value={draft.taxonomies.livello_conoscenza}
                  onChange={(v) =>
                    setDraft({
                      ...draft,
                      taxonomies: { ...draft.taxonomies, livello_conoscenza: v },
                    })
                  }
                  disabled={readOnly}
                />
              </div>
              <div className="space-y-1.5">
                <Label>{t("courses.taxonomies.dimensionePubblico")}</Label>
                <TaxonomyTermSelect
                  taxonomyType="audience_size"
                  value={draft.taxonomies.dimensione_pubblico}
                  onChange={(v) =>
                    setDraft({
                      ...draft,
                      taxonomies: { ...draft.taxonomies, dimensione_pubblico: v },
                    })
                  }
                  disabled={readOnly}
                />
              </div>
              <div className="space-y-1.5">
                <Label>{t("courses.taxonomies.ruoloDocente")}</Label>
                <TaxonomyTermSelect
                  taxonomyType="teacher_role"
                  hierarchical
                  value={draft.taxonomies.ruolo_docente}
                  onChange={(v) =>
                    setDraft({
                      ...draft,
                      taxonomies: { ...draft.taxonomies, ruolo_docente: v },
                    })
                  }
                  disabled={readOnly}
                />
              </div>
              <div className="space-y-1.5">
                <Label>{t("courses.taxonomies.stileInsegnamento")}</Label>
                <TaxonomyTermSelect
                  taxonomyType="teaching_style"
                  value={draft.taxonomies.stile_insegnamento}
                  onChange={(v) =>
                    setDraft({
                      ...draft,
                      taxonomies: { ...draft.taxonomies, stile_insegnamento: v },
                    })
                  }
                  disabled={readOnly}
                />
              </div>
              <div className="space-y-1.5">
                <Label>{t("courses.taxonomies.profonditaContenuto")}</Label>
                <TaxonomyTermSelect
                  taxonomyType="content_depth"
                  value={draft.taxonomies.profondita_contenuto}
                  onChange={(v) =>
                    setDraft({
                      ...draft,
                      taxonomies: { ...draft.taxonomies, profondita_contenuto: v },
                    })
                  }
                  disabled={readOnly}
                />
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* Tab — Documenti di riferimento (solo edit mode) */}
        {mode === "edit" && course && (
          <TabsContent value="documents">
            <Card>
              <CardHeader>
                <CardTitle>{t("courses.sections.documents")}</CardTitle>
              </CardHeader>
              <CardContent>
                <CourseDocumentUploader
                  orgId={orgId}
                  courseId={course.id}
                  documents={course.documents}
                  disabled={readOnly}
                />
              </CardContent>
            </Card>
          </TabsContent>
        )}

        {/* Tab — Architettura corso (Fase 1 AI, solo edit mode) */}
        {mode === "edit" && course && (
          <TabsContent value="architecture">
            <Card>
              <CardHeader>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <CardTitle>
                    {t("courses.sections.architecture")}
                  </CardTitle>
                  {canGenerate && (
                    <ArchitectureActions
                      course={course}
                      onOpenGenerate={() => setArchDialogOpen(true)}
                      onApprove={() => approveArchMut.mutate()}
                      isApproving={approveArchMut.isPending}
                    />
                  )}
                </div>
              </CardHeader>
              <CardContent>
                <ArchitectureSection
                  course={course}
                  orgId={orgId}
                  canEdit={canEdit}
                />
              </CardContent>
            </Card>
          </TabsContent>
        )}

        {/* Tab — Struttura delle lezioni (Fase 2 AI, solo edit mode) */}
        {mode === "edit" && course && (
          <TabsContent value="lessons-structure">
            <CourseLessonStructureView
              course={course}
              orgId={orgId}
              canEdit={canEdit}
              canGenerate={canGenerate}
            />
          </TabsContent>
        )}

        {/* Tab — Contenuti lezioni (Fase 3 AI + Glossario corso) */}
        {mode === "edit" && course && (
          <TabsContent value="lesson-content">
            <CourseLessonContentView
              course={course}
              orgId={orgId}
              canEdit={canEdit}
              canGenerate={canGenerate}
            />
          </TabsContent>
        )}
      </Tabs>

      {course && (
        <GenerateArchitectureDialog
          open={archDialogOpen}
          isRegeneration={
            !!course.architecture_generated_at ||
            course.modules.length > 0
          }
          isPending={generateArchMut.isPending}
          onClose={() => setArchDialogOpen(false)}
          onConfirm={(hint) => generateArchMut.mutate(hint)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Architecture section helpers (defined inline to avoid prop-drilling).
// ---------------------------------------------------------------------------

function ArchitectureActions({
  course,
  onOpenGenerate,
  onApprove,
  isApproving,
}: {
  course: CourseOut;
  onOpenGenerate: () => void;
  onApprove: () => void;
  isApproving: boolean;
}) {
  const { t } = useTranslation();
  const status = course.status;
  const canApprove = status === "architecture_ready";
  const canGenerate =
    status === "draft" ||
    status === "architecture_ready" ||
    status === "architecture_approved";
  const isPending = status === "architecture_pending";
  const isRegeneration = course.modules.length > 0;

  return (
    <div className="flex flex-wrap gap-2">
      {canApprove && (
        <Button onClick={onApprove} disabled={isApproving}>
          <CheckCircle2 className="size-4" />
          {isApproving
            ? t("courses.architecture.approving")
            : t("courses.architecture.approve")}
        </Button>
      )}
      {canGenerate && !isPending && (
        <Button
          variant={isRegeneration ? "outline" : "default"}
          onClick={onOpenGenerate}
        >
          {isRegeneration ? (
            <RotateCw className="size-4" />
          ) : (
            <Sparkles className="size-4" />
          )}
          {isRegeneration
            ? t("courses.architecture.regenerate")
            : t("courses.architecture.generate")}
        </Button>
      )}
    </div>
  );
}

function ArchitectureSection({
  course,
  orgId,
  canEdit,
}: {
  course: CourseOut;
  orgId: string;
  canEdit: boolean;
}) {
  const { t } = useTranslation();

  const isPending = course.status === "architecture_pending";
  const archPct = Math.max(0, Math.min(100, course.architecture_progress ?? 0));
  const archEta = useTaskEta(`arch:${course.id}`, isPending, archPct);

  if (isPending) {
    const phase = course.architecture_progress_phase;
    return (
      <div className="space-y-3 rounded-lg border border-dashed border-border p-6">
        <div className="flex items-center gap-2 text-sm">
          <Loader2 className="size-4 animate-spin text-primary" />
          <span className="font-medium">
            {phase
              ? t(`courses.architecture.phases.${phase}`, {
                  defaultValue: t("courses.architecture.pendingMessage"),
                })
              : t("courses.architecture.pendingMessage")}
          </span>
          <span className="ms-auto font-mono text-xs tabular-nums text-muted-foreground">
            {archPct}%
          </span>
        </div>
        <Progress value={archPct} />
        {(archEta.etaMs !== null || archEta.elapsedMs !== null) && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
            {archEta.etaMs !== null && (
              <span className="font-medium text-foreground">
                {t("courses.architecture.eta", {
                  time: formatDuration(archEta.etaMs),
                })}
              </span>
            )}
            {archEta.elapsedMs !== null && archEta.elapsedMs > 1_000 && (
              <span>
                {t("courses.architecture.elapsed", {
                  time: formatDuration(archEta.elapsedMs),
                })}
              </span>
            )}
          </div>
        )}
      </div>
    );
  }

  if (course.architecture_error && course.modules.length === 0) {
    return (
      <div className="space-y-2 rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-sm">
        <p className="font-medium text-destructive">
          {t("courses.architecture.errorTitle")}
        </p>
        <p className="text-muted-foreground">{course.architecture_error}</p>
      </div>
    );
  }

  // Editing manuale è permesso solo dopo che l'AI ha generato l'architettura
  // (status `architecture_ready` o `architecture_approved`).
  const editable =
    canEdit &&
    (course.status === "architecture_ready" ||
      course.status === "architecture_approved");

  return (
    <div className="space-y-3">
      {course.status === "architecture_approved" && (
        <div className="flex items-center gap-2 rounded-md bg-brand/10 p-2 text-sm text-brand">
          <CheckCircle2 className="size-4" />
          {t("courses.architecture.approvedBanner")}
        </div>
      )}
      {course.status === "architecture_ready" && (
        <div className="flex items-center gap-2 rounded-md bg-warning/10 p-2 text-sm">
          <Sparkles className="size-4" />
          {t("courses.architecture.readyBanner")}
        </div>
      )}
      <CourseArchitectureView
        course={course}
        canEdit={editable}
        orgId={orgId}
      />
    </div>
  );
}
