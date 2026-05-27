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
  Lock,
  LockOpen,
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
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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
import { ApprovalBadge } from "@/components/shared/ApprovalBadge";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { useSetNovaContext } from "@/contexts/NovaContext";
import { useLanguages } from "@/hooks/useLanguages";
import { useTaxonomyTermsBulk } from "@/hooks/useTaxonomyTerms";
import type { TaxonomyType } from "@/api/courseTaxonomy";

// Tutte le tassonomie che la pagina monta in `<TaxonomyTermSelect>`.
// Pre-caricarle in bulk evita 8 roundtrip separati al mount.
const TAXONOMY_TYPES_USED: readonly TaxonomyType[] = [
  "category",
  "target_audience",
  "eqf_level",
  "knowledge_level",
  "audience_size",
  "teacher_role",
  "teaching_style",
  "content_depth",
] as const;
import { useTaskEta } from "@/hooks/useTaskEta";
import { flagFor } from "@/i18n/flags";
import { extractApiError } from "@/lib/errors";
import { formatDuration } from "@/lib/formatDuration";
import { P } from "@/lib/permissions";
import { CourseArchitectureView } from "./components/CourseArchitectureView";
import { CourseDocumentUploader } from "./components/CourseDocumentUploader";
import { CourseLessonStructureView } from "./components/CourseLessonStructureView";
import { CourseLessonContentView } from "./components/CourseLessonContentView";
import { CourseLessonSlidesView } from "./components/CourseLessonSlidesView";
import { CourseLessonSpeechView } from "./components/CourseLessonSpeechView";
import { CourseLessonVideoView } from "./components/CourseLessonVideoView";
import { CourseLessonAvatarVideoView } from "./components/CourseLessonAvatarVideoView";
import { CourseObjectivesAIGenerator } from "./components/CourseObjectivesAIGenerator";
import { CourseObjectivesAIPreviewDialog } from "./components/CourseObjectivesAIPreviewDialog";
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

  // Prefetch in batch di tutte le tassonomie usate nei Select. Un solo
  // roundtrip invece di 8; popola la cache TanStack per-tipo così i
  // `<TaxonomyTermSelect>` figli leggono cache hit istantanei.
  useTaxonomyTermsBulk(TAXONOMY_TYPES_USED);

  const canEditOrg = useHasPermission(P.COURSE_EDIT, orgId);
  const canAssign = useHasPermission(P.COURSE_ASSIGN, orgId);
  const canGenerate = useHasPermission(P.COURSE_GENERATE, orgId);
  const canSaveDraft = useHasPermission(P.COURSE_SAVE_DRAFT, orgId);

  useSetNovaContext({
    page: mode === "create" ? "course.create" : "course.editor",
    fields: {
      courseId: courseId ?? null,
    },
    orgId,
  });
  // `canEdit` finale: include il caso "assegnatario del corso" (anche un
  // Member senza COURSE_EDIT) — per consentire all'utente a cui il corso
  // è stato passato come bozza di completarne basic info + didattico.
  // L'assegnatario viene letto da `course?.assignee` ed è calcolato sotto
  // dopo che `courseQuery` ha caricato i dati.

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
      const anyActiveLessonSlides = (data.modules ?? []).some((m) =>
        (m.lessons ?? []).some(
          (l) =>
            l.slides_status === "pending" ||
            l.slides_status === "processing"
        )
      );
      if (anyActiveLessonSlides) return 5000;
      const anyActiveSlidesPdf = (data.modules ?? []).some((m) =>
        (m.lessons ?? []).some(
          (l) =>
            l.slides_pdf_status === "pending" ||
            l.slides_pdf_status === "processing"
        )
      );
      if (anyActiveSlidesPdf) return 4000;
      const anyActiveLessonSpeech = (data.modules ?? []).some((m) =>
        (m.lessons ?? []).some(
          (l) =>
            l.speech_status === "pending" ||
            l.speech_status === "processing"
        )
      );
      if (anyActiveLessonSpeech) return 5000;
      const anyActiveSpeechPdf = (data.modules ?? []).some((m) =>
        (m.lessons ?? []).some(
          (l) =>
            l.speech_pdf_status === "pending" ||
            l.speech_pdf_status === "processing"
        )
      );
      if (anyActiveSpeechPdf) return 4000;
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
  // Lock del setup didattico (Tab 1 + Tab 2). Quando confermato, gli
  // input dei parametri sono read-only finché un creator/org_admin non
  // chiama `unlock`. Il lock viene applicato anche server-side.
  const setupLocked = !!course?.didactic_setup_confirmed_at;
  // Solo creator / org_admin / platform_admin possono sbloccare il setup.
  const myRoleCode = me?.organizations.find(
    (o) => o.organization_id === orgId,
  )?.role_code;
  const canUnlockSetup =
    me?.user.is_platform_admin || myRoleCode === "creator" || myRoleCode === "org_admin";
  // L'assegnatario può editare basic info + didattico anche senza COURSE_EDIT
  // sull'org (mirror del check BE in `_ensure_can_edit_basic`).
  const isAssignee = !!course && !!me && course.assignee?.id === me.user.id;
  const canEdit = canEditOrg || isAssignee;
  const readOnly = (mode === "edit" && !canEdit) || setupLocked;

  // Wizard tab — controlled + persisted in localStorage per courseId.
  // L'utente può navigare avanti e indietro tra le tab; lo stato è
  // ricordato così un refresh non lo riporta a Tab 1.
  const TAB_ORDER = [
    "base",
    "didactic",
    "objectives",
    "documents",
    "architecture",
    "lessons-structure",
    "lesson-content",
    "lesson-slides",
    "lesson-speech",
    "lesson-video",
    "lesson-avatar-video",
  ] as const;
  type TabId = (typeof TAB_ORDER)[number];
  const tabStorageKey = courseId ? `course-editor-tab:${courseId}` : null;
  const [activeTab, setActiveTab] = useState<TabId>(() => {
    if (mode !== "edit" || !tabStorageKey) return "base";
    try {
      const saved = localStorage.getItem(tabStorageKey);
      if (saved && (TAB_ORDER as readonly string[]).includes(saved)) {
        return saved as TabId;
      }
    } catch {
      // ignore
    }
    return "base";
  });
  useEffect(() => {
    if (mode !== "edit" || !tabStorageKey) return;
    try {
      localStorage.setItem(tabStorageKey, activeTab);
    } catch {
      // ignore
    }
  }, [activeTab, mode, tabStorageKey]);
  // Pre-confirm setup: solo "base", "didactic" e "objectives" sono
  // accessibili. Se localStorage aveva salvato un tab più avanti (es.
  // corso ripreso dopo un unlock), rientriamo su "objectives" (l'ultima
  // tab di setup, dove c'è il pulsante "Conferma e continua") così
  // l'utente vede dove serve agire prima di poter continuare.
  useEffect(() => {
    if (mode !== "edit" || !course) return;
    if (setupLocked) return;
    if (
      activeTab !== "base" &&
      activeTab !== "didactic" &&
      activeTab !== "objectives"
    ) {
      setActiveTab("objectives");
    }
  }, [mode, course, setupLocked, activeTab]);

  const defaultLang = i18n.resolvedLanguage?.split("-")[0] || "it";
  const [draft, setDraft] = useState<DraftState | null>(null);
  // Dialog di preview per la generazione AI di obiettivi + argomenti
  // chiave (tab "Obiettivi e Argomenti chiave"). Aperto quando l'utente
  // ha generato una proposta dal documento caricato.
  const [aiPreviewOpen, setAiPreviewOpen] = useState(false);
  const [aiProposal, setAiProposal] = useState<{
    objectives: string;
    argomenti_chiave: string[];
  } | null>(null);
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
      // Auto-advance al prossimo step del wizard una volta navigato in
      // edit mode: pre-settiamo il tab su "objectives" sotto la chiave
      // del nuovo corso, così quando il componente edit monta lo legge
      // e mostra il pulsante "Conferma e continua" (l'utente puo' anche
      // usare la generazione AI prima di confermare).
      try {
        localStorage.setItem(`course-editor-tab:${fresh.id}`, "objectives");
      } catch {
        // ignore
      }
      navigate(`/orgs/${orgId}/corsi/${fresh.id}`, { replace: true });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  // "Salva come bozza": in create mode crea il corso ma preserva il tab
  // corrente (così l'utente resta dov'era invece di essere spinto a "didactic"
  // come fa createMut). In edit mode il flusso è gestito direttamente da
  // saveDraft() chiamando performAutoSave + toast — niente network call qui.
  const saveDraftMut = useMutation({
    mutationFn: (payload: CourseCreateInput) => coursesApi.create(orgId, payload),
    onSuccess: (fresh) => {
      qc.invalidateQueries({ queryKey: ["courses", "list", orgId] });
      qc.setQueryData(["courses", "detail", orgId, fresh.id], fresh);
      toast.success(t("courses.savedAsDraftToast"));
      try {
        localStorage.setItem(`course-editor-tab:${fresh.id}`, activeTab);
      } catch {
        // ignore
      }
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

  // Wizard setup confirm/unlock (Tab 2 → blocca, banner Tab 1 sblocca).
  // Il confirm è preceduto da un dialog di conferma esplicita: dopo
  // questa azione le tabs 1+2 diventano read-only fino a quando un
  // creator/org_admin non chiama `unlock`.
  const [confirmDidacticDialogOpen, setConfirmDidacticDialogOpen] =
    useState(false);
  const confirmDidacticMut = useMutation({
    mutationFn: () => coursesApi.setup.confirmDidactic(orgId, courseId!),
    onSuccess: (fresh) => {
      qc.setQueryData(["courses", "detail", orgId, courseId], fresh);
      qc.invalidateQueries({ queryKey: ["courses", "list", orgId] });
      toast.success(t("courses.setup.toast.confirmed"));
      setConfirmDidacticDialogOpen(false);
      setActiveTab("documents");
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });
  const unlockSetupMut = useMutation({
    mutationFn: () => coursesApi.setup.unlock(orgId, courseId!),
    onSuccess: (fresh) => {
      qc.setQueryData(["courses", "detail", orgId, courseId], fresh);
      qc.invalidateQueries({ queryKey: ["courses", "list", orgId] });
      toast.success(t("courses.setup.toast.unlocked"));
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

  // "Salva come bozza": come submit() ma in create mode usa saveDraftMut
  // (che preserva il tab corrente) e in edit mode mostra un toast esplicito
  // dopo il flush dell'auto-save. Niente conferma del setup didattico.
  const saveDraft = () => {
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
      saveDraftMut.mutate(payload);
    } else {
      performAutoSave();
      toast.success(t("courses.savedAsDraftToast"));
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

      <Tabs
        value={activeTab}
        onValueChange={(v) => setActiveTab(v as TabId)}
        className="space-y-4"
      >
        <TabsList className="flex h-auto w-full flex-wrap justify-start gap-1">
          <TabsTrigger value="base">{t("courses.tabs.base")}</TabsTrigger>
          <TabsTrigger value="didactic">
            {t("courses.tabs.didactic")}
          </TabsTrigger>
          <TabsTrigger value="objectives">
            {t("courses.tabs.objectives")}
          </TabsTrigger>
          {mode === "edit" && setupLocked && (
            <TabsTrigger value="documents">
              {t("courses.tabs.documents")}
            </TabsTrigger>
          )}
          {mode === "edit" && setupLocked && (
            <TabsTrigger value="architecture">
              {t("courses.tabs.architecture")}
            </TabsTrigger>
          )}
          {mode === "edit" && setupLocked && (
            <TabsTrigger
              value="lessons-structure"
              disabled={
                !course ||
                (course.status !== "architecture_approved" &&
                  course.status !== "lessons_structure_pending" &&
                  course.status !== "lessons_structure_ready" &&
                  course.status !== "lessons_structure_approved" &&
                  !course.status.startsWith("content_") &&
                  !["slides_pending", "slides_ready", "slides_approved", "speech_pending", "speech_ready", "speech_approved", "published"].includes(
                    course.status
                  ))
              }
            >
              {t("courses.tabs.lessonsStructure")}
            </TabsTrigger>
          )}
          {mode === "edit" && setupLocked && (
            <TabsTrigger
              value="lesson-content"
              disabled={
                !course ||
                (course.status !== "lessons_structure_approved" &&
                  !course.status.startsWith("content_") &&
                  !["slides_pending", "slides_ready", "slides_approved", "speech_pending", "speech_ready", "speech_approved", "published"].includes(
                    course.status
                  ))
              }
            >
              {t("courses.tabs.lessonContent")}
            </TabsTrigger>
          )}
          {mode === "edit" && setupLocked && (
            <TabsTrigger
              value="lesson-slides"
              disabled={
                !course ||
                (course.status !== "content_ready" &&
                  course.status !== "content_approved" &&
                  !["slides_pending", "slides_ready", "slides_approved", "speech_pending", "speech_ready", "speech_approved", "published"].includes(
                    course.status
                  ))
              }
            >
              {t("courses.tabs.lessonSlides")}
            </TabsTrigger>
          )}
          {mode === "edit" && setupLocked && (
            <TabsTrigger
              value="lesson-speech"
              disabled={
                !course ||
                !course.modules?.some((m) =>
                  m.lessons?.some(
                    (l) =>
                      l.slides_status === "ready" ||
                      l.slides_status === "approved",
                  ),
                )
              }
            >
              {t("courses.tabs.lessonSpeech")}
            </TabsTrigger>
          )}
          {mode === "edit" && setupLocked && (
            <TabsTrigger
              value="lesson-video"
              disabled={
                !course ||
                !course.modules?.some((m) =>
                  m.lessons?.some(
                    (l) =>
                      l.speech_status === "approved" &&
                      l.slides_status === "approved",
                  ),
                )
              }
            >
              {t("courses.tabs.lessonVideo")}
            </TabsTrigger>
          )}
          {mode === "edit" && setupLocked && (
            <TabsTrigger
              value="lesson-avatar-video"
              disabled={
                !course ||
                !course.modules?.some((m) =>
                  m.lessons?.some(
                    (l) =>
                      l.speech_status === "approved" &&
                      l.slides_status === "approved",
                  ),
                )
              }
            >
              {t("courses.tabs.lessonAvatarVideo")}
            </TabsTrigger>
          )}
        </TabsList>

        {/* Tab — Informazioni di base */}
        <TabsContent value="base" className="space-y-4">
          {setupLocked && (
            <div className="flex flex-wrap items-center gap-3 rounded-md border border-amber-300/40 bg-amber-50/40 p-3 text-sm dark:border-amber-500/30 dark:bg-amber-900/10">
              <Lock className="size-4 shrink-0 text-amber-600 dark:text-amber-500" />
              <span className="flex-1">
                {t("courses.setup.lockedBanner")}
              </span>
              {canUnlockSetup && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => unlockSetupMut.mutate()}
                  disabled={unlockSetupMut.isPending}
                >
                  <LockOpen className="size-3.5" />
                  {t("courses.setup.unlock")}
                </Button>
              )}
            </div>
          )}
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
              {/* Obiettivi e Argomenti chiave: spostati nella tab dedicata
                  "Obiettivi e Argomenti chiave" (terza tab). Includono
                  anche la generazione AI da documento. */}
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
                    fallback={course?.assignee ?? null}
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
          <div className="flex flex-wrap justify-end gap-2">
            {canSaveDraft && !setupLocked && (
              <Button
                size="lg"
                variant="secondary"
                onClick={saveDraft}
                disabled={
                  !draft.title.trim() ||
                  createMut.isPending ||
                  saveDraftMut.isPending
                }
              >
                {saveDraftMut.isPending ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Save className="size-4" />
                )}
                {saveDraftMut.isPending
                  ? t("common.saving")
                  : t("courses.saveAsDraft")}
              </Button>
            )}
            <Button
              size="lg"
              variant="outline"
              onClick={() => setActiveTab("didactic")}
              disabled={mode === "create" && !draft.title.trim()}
            >
              {t("courses.wizard.continueToDidactic")}
              <ArrowRight className="size-4" />
            </Button>
          </div>
        </TabsContent>

        {/* Tab — Inquadramento didattico (categoria spostata in Base) */}
        <TabsContent value="didactic" className="space-y-4">
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
          <div className="flex flex-wrap justify-end gap-2">
            {mode === "create" && canSaveDraft && (
              <Button
                size="lg"
                variant="secondary"
                onClick={saveDraft}
                disabled={
                  !draft.title.trim() ||
                  createMut.isPending ||
                  saveDraftMut.isPending
                }
              >
                {saveDraftMut.isPending ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Save className="size-4" />
                )}
                {saveDraftMut.isPending
                  ? t("common.saving")
                  : t("courses.saveAsDraft")}
              </Button>
            )}
            {mode === "edit" && canSaveDraft && !setupLocked && (
              <Button
                size="lg"
                variant="secondary"
                onClick={saveDraft}
                disabled={!draft.title.trim()}
              >
                <Save className="size-4" />
                {t("courses.saveAsDraft")}
              </Button>
            )}
            <Button
              size="lg"
              variant="outline"
              onClick={() => setActiveTab("objectives")}
            >
              {t("courses.wizard.continueToObjectives")}
              <ArrowRight className="size-4" />
            </Button>
          </div>
        </TabsContent>

        {/* Tab — Obiettivi e Argomenti chiave (sempre visibile, gestita
            dal lock didattico server-side come per "base"/"didactic") */}
        <TabsContent value="objectives" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>
                {t("courses.sections.objectivesAndTopics.title")}
              </CardTitle>
              <CardDescription>
                {t("courses.sections.objectivesAndTopics.subtitle")}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="course-objectives-tab">
                  {t("courses.fields.objectives")}
                </Label>
                <Textarea
                  id="course-objectives-tab"
                  rows={6}
                  value={draft.objectives}
                  onChange={(e) =>
                    setDraft({ ...draft, objectives: e.target.value })
                  }
                  disabled={readOnly}
                  placeholder={t("courses.fields.objectivesPlaceholder")}
                  maxLength={8000}
                />
              </div>
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
            </CardContent>
          </Card>

          <CourseObjectivesAIGenerator
            orgId={orgId}
            courseId={course?.id ?? null}
            disabled={readOnly}
            onGenerated={(data) => {
              setAiProposal(data);
              setAiPreviewOpen(true);
            }}
          />

          <CourseObjectivesAIPreviewDialog
            open={aiPreviewOpen}
            onOpenChange={setAiPreviewOpen}
            current={{
              objectives: draft.objectives,
              argomenti_chiave: draft.argomenti_chiave,
            }}
            proposed={aiProposal}
            onApply={(next) => {
              setDraft({
                ...draft,
                objectives: next.objectives,
                argomenti_chiave: next.argomenti_chiave,
              });
            }}
          />

          {mode === "create" && (
            <div className="flex flex-wrap justify-end gap-2">
              {canSaveDraft && (
                <Button
                  size="lg"
                  variant="secondary"
                  onClick={saveDraft}
                  disabled={
                    !draft.title.trim() ||
                    createMut.isPending ||
                    saveDraftMut.isPending
                  }
                >
                  {saveDraftMut.isPending ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <Save className="size-4" />
                  )}
                  {saveDraftMut.isPending
                    ? t("common.saving")
                    : t("courses.saveAsDraft")}
                </Button>
              )}
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
          {mode === "edit" && (
            <div className="flex flex-wrap justify-end gap-2">
              {canSaveDraft && !setupLocked && (
                <Button
                  size="lg"
                  variant="secondary"
                  onClick={saveDraft}
                  disabled={!draft.title.trim()}
                >
                  <Save className="size-4" />
                  {t("courses.saveAsDraft")}
                </Button>
              )}
              {setupLocked ? (
                <Button
                  size="lg"
                  variant="outline"
                  onClick={() => setActiveTab("documents")}
                >
                  {t("courses.wizard.continueToDocuments")}
                  <ArrowRight className="size-4" />
                </Button>
              ) : (
                <Button
                  size="lg"
                  onClick={() => setConfirmDidacticDialogOpen(true)}
                  disabled={confirmDidacticMut.isPending}
                >
                  {confirmDidacticMut.isPending ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <CheckCircle2 className="size-4" />
                  )}
                  {confirmDidacticMut.isPending
                    ? t("common.saving")
                    : t("courses.wizard.confirmDidacticAndContinue")}
                  {!confirmDidacticMut.isPending && (
                    <ArrowRight className="size-4" />
                  )}
                </Button>
              )}
            </div>
          )}
        </TabsContent>

        {/* Tab — Documenti di riferimento (solo edit mode) */}
        {mode === "edit" && course && (
          <TabsContent value="documents" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>{t("courses.sections.documents")}</CardTitle>
              </CardHeader>
              <CardContent>
                <CourseDocumentUploader
                  orgId={orgId}
                  courseId={course.id}
                  documents={course.documents}
                  disabled={mode === "edit" && !canEdit}
                />
              </CardContent>
            </Card>
            <div className="flex justify-end">
              <Button
                size="lg"
                variant="outline"
                onClick={() => setActiveTab("architecture")}
              >
                {t("courses.wizard.continueToArchitecture")}
                <ArrowRight className="size-4" />
              </Button>
            </div>
          </TabsContent>
        )}

        {/* Tab — Architettura corso (Fase 1 AI, solo edit mode) */}
        {mode === "edit" && course && (
          <TabsContent value="architecture" className="space-y-4">
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
            {(course.status === "architecture_approved" ||
              course.status === "lessons_structure_pending" ||
              course.status === "lessons_structure_ready" ||
              course.status === "lessons_structure_approved" ||
              course.status.startsWith("content_") ||
              [
                "slides_pending",
                "slides_ready",
                "speech_pending",
                "speech_ready",
                "published",
              ].includes(course.status)) && (
              <div className="flex justify-end">
                <Button
                  size="lg"
                  variant="outline"
                  onClick={() => setActiveTab("lessons-structure")}
                >
                  {t("courses.wizard.continueToLessonsStructure")}
                  <ArrowRight className="size-4" />
                </Button>
              </div>
            )}
          </TabsContent>
        )}

        {/* Tab — Struttura delle lezioni (Fase 2 AI, solo edit mode) */}
        {mode === "edit" && course && (
          <TabsContent value="lessons-structure" className="space-y-4">
            <CourseLessonStructureView
              course={course}
              orgId={orgId}
              canEdit={canEdit}
              canGenerate={canGenerate}
            />
            {(course.status === "lessons_structure_approved" ||
              course.status.startsWith("content_") ||
              [
                "slides_pending",
                "slides_ready",
                "speech_pending",
                "speech_ready",
                "published",
              ].includes(course.status)) && (
              <div className="flex justify-end">
                <Button
                  size="lg"
                  variant="outline"
                  onClick={() => setActiveTab("lesson-content")}
                >
                  {t("courses.wizard.continueToLessonContent")}
                  <ArrowRight className="size-4" />
                </Button>
              </div>
            )}
          </TabsContent>
        )}

        {/* Tab — Contenuti lezioni (Fase 3 AI + Glossario corso) */}
        {mode === "edit" && course && (
          <TabsContent value="lesson-content" className="space-y-4">
            <CourseLessonContentView
              course={course}
              orgId={orgId}
              canEdit={canEdit}
              canGenerate={canGenerate}
            />
            {(course.status === "content_approved" ||
              course.status.startsWith("slides_") ||
              course.status.startsWith("speech_") ||
              course.status === "published") && (
              <div className="flex justify-end">
                <Button
                  size="lg"
                  variant="outline"
                  onClick={() => setActiveTab("lesson-slides")}
                >
                  {t("courses.wizard.continueToLessonSlides")}
                  <ArrowRight className="size-4" />
                </Button>
              </div>
            )}
          </TabsContent>
        )}

        {/* Tab — Slide lezioni (Fase 4 AI) */}
        {mode === "edit" && course && (
          <TabsContent value="lesson-slides" className="space-y-4">
            <CourseLessonSlidesView
              course={course}
              orgId={orgId}
              canEdit={canEdit}
              canGenerate={canGenerate}
            />
            {course.modules?.some((m) =>
              m.lessons?.some(
                (l) =>
                  l.slides_status === "ready" || l.slides_status === "approved",
              ),
            ) && (
              <div className="flex justify-end">
                <Button
                  size="lg"
                  variant="outline"
                  onClick={() => setActiveTab("lesson-speech")}
                >
                  {t("courses.wizard.continueToLessonSpeech")}
                  <ArrowRight className="size-4" />
                </Button>
              </div>
            )}
          </TabsContent>
        )}

        {/* Tab — Discorso temporizzato (Fase 5 AI) */}
        {mode === "edit" && course && (
          <TabsContent value="lesson-speech" className="space-y-4">
            <CourseLessonSpeechView
              course={course}
              orgId={orgId}
              canEdit={canEdit}
              canGenerate={canGenerate}
            />
          </TabsContent>
        )}

        {/* Tab — Generazione video MP4 (Fase 6) */}
        {mode === "edit" && course && (
          <TabsContent value="lesson-video" className="space-y-4">
            <CourseLessonVideoView
              course={course}
              orgId={orgId}
              canGenerate={canGenerate}
            />
          </TabsContent>
        )}

        {/* Tab — Video con Avatar (Fase 6b) */}
        {mode === "edit" && course && (
          <TabsContent value="lesson-avatar-video" className="space-y-4">
            <CourseLessonAvatarVideoView
              course={course}
              orgId={orgId}
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

      <ConfirmDialog
        open={confirmDidacticDialogOpen}
        title={t("courses.setup.confirmDialog.title")}
        message={t("courses.setup.confirmDialog.message")}
        confirmLabel={t("courses.setup.confirmDialog.confirmLabel")}
        cancelLabel={t("common.cancel")}
        onConfirm={() => confirmDidacticMut.mutate()}
        onClose={() => setConfirmDidacticDialogOpen(false)}
      />
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

  // Editing manuale dell'architettura è sempre permesso, allineato al
  // pattern di lezioni-struttura/contenuti/slide/discorso: il worker AI
  // di architettura scrive solo quando lo status è `architecture_pending`
  // (caso gestito sopra con il branch `isPending`); negli altri stati il
  // backend accetta i PATCH e lo stale-detection a cascata propaga le
  // invalidazioni downstream. Restano fuori solo `published`/`archived`
  // (corso terminato) e `draft` (nessuna architettura ancora generata).
  const editable =
    canEdit &&
    course.status !== "draft" &&
    course.status !== "architecture_pending" &&
    course.status !== "published" &&
    course.status !== "archived";

  return (
    <div className="space-y-3">
      {course.status === "architecture_approved" && (
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-brand/20 bg-brand/5 p-2 text-sm">
          <ApprovalBadge
            level="architecture"
            approvedAt={course.architecture_generated_at}
          />
          <span className="text-muted-foreground">
            {t("courses.architecture.approvedBanner")}
          </span>
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
