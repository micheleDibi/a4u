import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  type ColumnDef,
  type PaginationState,
} from "@tanstack/react-table";
import {
  ArrowDown,
  ArrowUp,
  BookOpenCheck,
  Edit,
  Languages,
  MoreHorizontal,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import {
  coursesApi,
  type CourseListItemOut,
  type CourseStatus,
} from "@/api/courses";
import { useHasPermission } from "@/auth/PermissionGate";
import { PageHeader } from "@/components/layout/PageHeader";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { DataTable } from "@/components/shared/DataTable";
import {
  DateRangeField,
  type DateRangeValue,
} from "@/components/forms/DateRangeField";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useSetNovaContext } from "@/contexts/NovaContext";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { useLanguages } from "@/hooks/useLanguages";
import { useOrgMembers } from "@/hooks/useOrgMembers";
import { flagFor } from "@/i18n/flags";
import { extractApiError } from "@/lib/errors";
import { P } from "@/lib/permissions";
import { CourseDuplicationBadge } from "./components/CourseDuplicationBadge";
import { CoursePipelineRowChips } from "./components/CoursePipelineRowChips";
import { CourseStatusBadge } from "./components/CourseStatusBadge";
import { DuplicateCourseDialog } from "./components/DuplicateCourseDialog";

// Tutti i 17 valori di `course.status` (mirror di backend
// `CourseStatus`). Esposti raw nel filtro: il bucketing macro-fase è
// solo nella dashboard, qui l'utente filtra per stato esatto.
const STATUS_FILTERS: CourseStatus[] = [
  "draft",
  "architecture_pending",
  "architecture_ready",
  "architecture_approved",
  "lessons_structure_pending",
  "lessons_structure_ready",
  "lessons_structure_approved",
  "content_pending",
  "content_ready",
  "content_approved",
  "slides_pending",
  "slides_ready",
  "slides_approved",
  "speech_pending",
  "speech_ready",
  "speech_approved",
  "video_pending",
  "video_ready",
  "avatar_video_pending",
  "avatar_video_ready",
  "published",
  "archived",
];

const ALL_STATUS = "__all__";
const ALL_ASSIGNEES = "__all__";
const ALL_LANGUAGES = "__all__";

type SortBy = "created_at" | "updated_at";
type SortDir = "asc" | "desc";

// ---------------------------------------------------------------------------
// URL state helpers — la sorgente di verità per i filtri è il
// querystring (refresh-safe + condivisibile). Le funzioni qui sotto
// (de)serializzano i tipi.
// ---------------------------------------------------------------------------

interface ListFilters {
  q: string;
  status: string; // CourseStatus | ALL_STATUS
  assignee_user_id: string; // UUID | ALL_ASSIGNEES
  language_code: string; // ISO | ALL_LANGUAGES
  created: DateRangeValue;
  updated: DateRangeValue;
  sort_by: SortBy;
  sort_dir: SortDir;
}

function readFiltersFromURL(sp: URLSearchParams): ListFilters {
  return {
    q: sp.get("q") ?? "",
    status: sp.get("status") ?? ALL_STATUS,
    assignee_user_id: sp.get("assignee_user_id") ?? ALL_ASSIGNEES,
    language_code: sp.get("language_code") ?? ALL_LANGUAGES,
    created: {
      from: sp.get("created_after") ?? undefined,
      to: sp.get("created_before") ?? undefined,
    },
    updated: {
      from: sp.get("updated_after") ?? undefined,
      to: sp.get("updated_before") ?? undefined,
    },
    sort_by: ((sp.get("sort_by") as SortBy) ?? "updated_at") as SortBy,
    sort_dir: ((sp.get("sort_dir") as SortDir) ?? "desc") as SortDir,
  };
}

function hasActiveFilters(f: ListFilters): boolean {
  return (
    !!f.q ||
    f.status !== ALL_STATUS ||
    f.assignee_user_id !== ALL_ASSIGNEES ||
    f.language_code !== ALL_LANGUAGES ||
    !!f.created.from ||
    !!f.created.to ||
    !!f.updated.from ||
    !!f.updated.to ||
    f.sort_by !== "updated_at" ||
    f.sort_dir !== "desc"
  );
}

// Converte "YYYY-MM-DD" → datetime ISO con orario di inizio/fine giornata
// (così "Creato a 31/03" include tutto il 31/03, non solo 00:00).
function dateToIsoLower(d: string | undefined): string | undefined {
  return d ? `${d}T00:00:00Z` : undefined;
}
function dateToIsoUpper(d: string | undefined): string | undefined {
  return d ? `${d}T23:59:59Z` : undefined;
}

export default function CoursesListPage() {
  const { t, i18n } = useTranslation();
  const params = useParams();
  const orgId = params.orgId!;
  const navigate = useNavigate();
  const qc = useQueryClient();

  const canCreate = useHasPermission(P.COURSE_CREATE, orgId);
  const canDelete = useHasPermission(P.COURSE_DELETE, orgId);
  const canDuplicate = useHasPermission(P.COURSE_DUPLICATE, orgId);

  useSetNovaContext({
    page: "courses.list",
    fields: {
      filterStatus:
        filters.status !== ALL_STATUS ? filters.status : null,
      filterAssignee:
        filters.assignee_user_id !== ALL_ASSIGNEES
          ? filters.assignee_user_id
          : null,
      filterLanguage:
        filters.language_code !== ALL_LANGUAGES
          ? filters.language_code
          : null,
      searchQuery: filters.q || null,
      sortBy: filters.sort_by,
      sortDir: filters.sort_dir,
    },
    orgId,
  });

  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useMemo(() => readFiltersFromURL(searchParams), [searchParams]);

  // Search input: state locale per typing reattivo + debounce 300ms in URL.
  const [qInput, setQInput] = useState(filters.q);
  const debouncedQ = useDebouncedValue(qInput, 300);

  // Sync `qInput` ↔ URL nelle due direzioni:
  // 1) Quando l'utente digita → debouncedQ aggiorna l'URL.
  // 2) Quando l'URL cambia da fuori (es. reset) → `qInput` si allinea.
  useEffect(() => {
    if (debouncedQ === filters.q) return;
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (debouncedQ) next.set("q", debouncedQ);
        else next.delete("q");
        next.delete("page");
        return next;
      },
      { replace: true },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedQ]);

  useEffect(() => {
    if (filters.q !== qInput) setQInput(filters.q);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters.q]);

  function updateFilter(key: string, value: string | null) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value === null || value === "" || value === ALL_STATUS) {
        next.delete(key);
      } else {
        next.set(key, value);
      }
      next.delete("page"); // ogni cambio filtro torna a pagina 1
      return next;
    });
  }

  function updateDateRange(
    prefix: "created" | "updated",
    range: DateRangeValue,
  ) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      const afterKey = `${prefix}_after`;
      const beforeKey = `${prefix}_before`;
      if (range.from) next.set(afterKey, range.from);
      else next.delete(afterKey);
      if (range.to) next.set(beforeKey, range.to);
      else next.delete(beforeKey);
      next.delete("page");
      return next;
    });
  }

  function resetAllFilters() {
    setSearchParams(new URLSearchParams());
    setQInput("");
  }

  function setSort(by: SortBy, dir: SortDir) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (by === "updated_at" && dir === "desc") {
        // Default: pulisci i parametri (URL più leggibile).
        next.delete("sort_by");
        next.delete("sort_dir");
      } else {
        next.set("sort_by", by);
        next.set("sort_dir", dir);
      }
      return next;
    });
  }

  // Pagination state derivata da URL.
  const pageNumber = parseInt(searchParams.get("page") ?? "1", 10) || 1;
  const pageSize =
    parseInt(searchParams.get("page_size") ?? "25", 10) || 25;
  const pagination: PaginationState = {
    pageIndex: Math.max(0, pageNumber - 1),
    pageSize,
  };
  function onPaginationChange(next: PaginationState) {
    setSearchParams((prev) => {
      const sp = new URLSearchParams(prev);
      if (next.pageIndex === 0) sp.delete("page");
      else sp.set("page", String(next.pageIndex + 1));
      if (next.pageSize === 25) sp.delete("page_size");
      else sp.set("page_size", String(next.pageSize));
      return sp;
    });
  }

  // Membri + lingue per i dropdown dei filtri.
  const membersQuery = useOrgMembers(orgId);
  const languages = useLanguages();

  const [toDelete, setToDelete] = useState<CourseListItemOut | null>(null);
  const [toDuplicate, setToDuplicate] =
    useState<CourseListItemOut | null>(null);

  const query = useQuery({
    queryKey: [
      "courses",
      "list",
      orgId,
      pagination.pageIndex,
      pagination.pageSize,
      filters,
    ],
    queryFn: () =>
      coursesApi.list(orgId, {
        page: pagination.pageIndex + 1,
        page_size: pagination.pageSize,
        q: filters.q || undefined,
        status:
          filters.status !== ALL_STATUS
            ? (filters.status as CourseStatus)
            : undefined,
        assignee_user_id:
          filters.assignee_user_id !== ALL_ASSIGNEES
            ? filters.assignee_user_id
            : undefined,
        language_code:
          filters.language_code !== ALL_LANGUAGES
            ? filters.language_code
            : undefined,
        created_after: dateToIsoLower(filters.created.from),
        created_before: dateToIsoUpper(filters.created.to),
        updated_after: dateToIsoLower(filters.updated.from),
        updated_before: dateToIsoUpper(filters.updated.to),
        sort_by: filters.sort_by,
        sort_dir: filters.sort_dir,
      }),
    // Polling automatico se almeno un corso della pagina ha un job di
    // duplicazione attivo: il badge mostra il progress in tempo reale.
    refetchInterval: (q) => {
      const data = q.state.data;
      if (!data) return false;
      const hasActiveJob = data.items.some(
        (it) =>
          it.duplication_job &&
          (it.duplication_job.status === "pending" ||
            it.duplication_job.status === "processing"),
      );
      return hasActiveJob ? 3000 : false;
    },
  });

  const deleteMut = useMutation({
    mutationFn: (courseId: string) => coursesApi.remove(orgId, courseId),
    onSuccess: () => {
      toast.success(t("courses.deleted"));
      qc.invalidateQueries({ queryKey: ["courses", "list", orgId] });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  // Format esteso (data + ora) per il pannello info nel dropdown dei
  // tre-puntini di ogni riga. La lista non mostra più le colonne data.
  const dateFormatter = useMemo(
    () =>
      new Intl.DateTimeFormat(i18n.language, {
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      }),
    [i18n.language],
  );

  const columns: ColumnDef<CourseListItemOut>[] = [
    {
      id: "title",
      header: t("courses.fields.title"),
      cell: ({ row }) => {
        const isDuplicating = !!row.original.duplication_job;
        return (
          <div className="flex flex-col gap-1">
            {isDuplicating ? (
              // Riga "locked" durante la duplicazione: niente link al
              // course editor, solo testo + badge con bottone Annulla.
              <span
                className="block max-w-[320px] truncate font-medium text-muted-foreground"
                title={row.original.title}
              >
                {row.original.title}
              </span>
            ) : (
              <Link
                to={`/orgs/${orgId}/corsi/${row.original.id}`}
                className="block max-w-[320px] truncate font-medium hover:underline"
              >
                {row.original.title}
              </Link>
            )}
            {row.original.duplication_job && (
              <CourseDuplicationBadge
                orgId={orgId}
                job={row.original.duplication_job}
              />
            )}
          </div>
        );
      },
    },
    {
      id: "assignee",
      header: t("courses.fields.assignee"),
      cell: ({ row }) => (
        <span
          className="block max-w-[180px] truncate text-sm text-muted-foreground"
          title={row.original.assignee.full_name}
        >
          {row.original.assignee.full_name}
        </span>
      ),
    },
    {
      id: "status",
      header: t("courses.fields.status"),
      cell: ({ row }) => (
        <span className="whitespace-nowrap">
          <CourseStatusBadge status={row.original.status} />
        </span>
      ),
    },
    {
      id: "lang",
      header: t("courses.fields.language"),
      cell: ({ row }) => {
        const Flag = flagFor(row.original.language_code);
        return (
          <span className="inline-flex items-center gap-1.5">
            <Flag className="size-4 rounded-sm shadow-[0_0_0_1px_rgba(0,0,0,0.08)]" />
            <span className="text-xs uppercase tracking-wider text-muted-foreground">
              {row.original.language_code}
            </span>
          </span>
        );
      },
    },
    {
      id: "pipeline",
      header: () => (
        <span className="whitespace-nowrap">
          {t("courses.list.pipelineHeader")}
        </span>
      ),
      // Min-width sulla cella così i 4 chip (≈ 195px) non causano
      // overflow né wrap quando il browser distribuisce le colonne.
      cell: ({ row }) => (
        <div className="min-w-[200px]">
          <CoursePipelineRowChips progress={row.original.lessons_progress} />
        </div>
      ),
    },
    {
      id: "actions",
      header: "",
      size: 64,
      // Il dropdown ospita anche le info "Creato" / "Aggiornato"
      // (rimosse dalla tabella per evitare lo scroll orizzontale).
      // Ordinamento per data si fa con il Select "Ordina" nella toolbar.
      // Durante una duplicazione attiva (`duplication_job != null`) il
      // dropdown è completamente nascosto: l'unica azione consentita
      // sulla riga è "Annulla" nel `CourseDuplicationBadge` del titolo.
      cell: ({ row }) => {
        if (row.original.duplication_job) {
          return null;
        }
        return (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon">
                <MoreHorizontal className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-60">
              <DropdownMenuLabel className="font-normal">
                <div className="space-y-1 text-xs">
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-muted-foreground">
                      {t("courses.fields.createdAt")}
                    </span>
                    <span className="tabular-nums">
                      {dateFormatter.format(new Date(row.original.created_at))}
                    </span>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-muted-foreground">
                      {t("courses.fields.updatedAt")}
                    </span>
                    <span className="tabular-nums">
                      {dateFormatter.format(new Date(row.original.updated_at))}
                    </span>
                  </div>
                </div>
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                onSelect={() =>
                  navigate(`/orgs/${orgId}/corsi/${row.original.id}`)
                }
              >
                <Edit className="size-4" />
                {t("common.edit")}
              </DropdownMenuItem>
              {canDuplicate && (
                <DropdownMenuItem
                  onSelect={() => setToDuplicate(row.original)}
                >
                  <Languages className="size-4" />
                  {t("courses.duplicate.action")}
                </DropdownMenuItem>
              )}
              {canDelete && (
                <DropdownMenuItem
                  onSelect={() => setToDelete(row.original)}
                  className="text-destructive focus:text-destructive"
                >
                  <Trash2 className="size-4" />
                  {t("common.delete")}
                </DropdownMenuItem>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        );
      },
    },
  ];

  const activeFilters = hasActiveFilters(filters);

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("courses.title")}
        description={t("courses.subtitle")}
        actions={
          canCreate ? (
            <Button asChild>
              <Link to={`/orgs/${orgId}/corsi/nuovo`}>
                <Plus className="size-4" />
                {t("courses.create")}
              </Link>
            </Button>
          ) : undefined
        }
      />

      {/* Toolbar filtri — tutto su una sola riga (wrap su schermi
          stretti). Niente Label sopra i controlli: ogni Select mostra
          "Tutti …" come placeholder/selezione di default, e il
          DateRangeField include la propria label nel trigger button. */}
      <Card>
        <CardContent className="flex flex-wrap items-center gap-2 p-3">
          <Input
            placeholder={t("courses.search")}
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            className="h-9 min-w-[200px] max-w-sm flex-1"
          />

          <Select
            value={filters.assignee_user_id}
            onValueChange={(v) => updateFilter("assignee_user_id", v)}
          >
            <SelectTrigger className="h-9 w-[180px]">
              <SelectValue
                placeholder={t("courses.filters.allAssignees")}
              />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_ASSIGNEES}>
                {t("courses.filters.allAssignees")}
              </SelectItem>
              {(membersQuery.data ?? []).map((m) => (
                <SelectItem key={m.user_id} value={m.user_id}>
                  {m.user_full_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select
            value={filters.status}
            onValueChange={(v) => updateFilter("status", v)}
          >
            <SelectTrigger className="h-9 w-[200px]">
              <SelectValue
                placeholder={t("courses.filters.allStatuses")}
              />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_STATUS}>
                {t("courses.filters.allStatuses")}
              </SelectItem>
              {STATUS_FILTERS.map((s) => (
                <SelectItem key={s} value={s}>
                  {t(`courses.statuses.${s}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select
            value={filters.language_code}
            onValueChange={(v) => updateFilter("language_code", v)}
          >
            <SelectTrigger className="h-9 w-[160px]">
              <SelectValue
                placeholder={t("courses.filters.allLanguages")}
              />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_LANGUAGES}>
                {t("courses.filters.allLanguages")}
              </SelectItem>
              {languages.map((l) => {
                const Flag = flagFor(l.code, l.flag_country_code);
                return (
                  <SelectItem key={l.code} value={l.code}>
                    <span className="inline-flex items-center gap-2">
                      <Flag className="size-3.5 rounded-sm shadow-[0_0_0_1px_rgba(0,0,0,0.08)]" />
                      <span>{l.name_native}</span>
                    </span>
                  </SelectItem>
                );
              })}
            </SelectContent>
          </Select>

          <DateRangeField
            label={t("courses.fields.createdAt")}
            value={filters.created}
            onChange={(v) => updateDateRange("created", v)}
            className="w-[200px]"
          />

          <DateRangeField
            label={t("courses.fields.updatedAt")}
            value={filters.updated}
            onChange={(v) => updateDateRange("updated", v)}
            className="w-[200px]"
          />

          {/* Ordinamento — sostituisce gli header sortable delle colonne
              data (rimosse). Encoding "sort_by:sort_dir" come singolo
              value per il Select. */}
          <Select
            value={`${filters.sort_by}:${filters.sort_dir}`}
            onValueChange={(v) => {
              const [by, dir] = v.split(":") as [SortBy, SortDir];
              setSort(by, dir);
            }}
          >
            <SelectTrigger className="h-9 w-[200px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="updated_at:desc">
                <span className="inline-flex items-center gap-1.5">
                  <ArrowDown className="size-3.5" />
                  {t("courses.fields.updatedAt")}
                </span>
              </SelectItem>
              <SelectItem value="updated_at:asc">
                <span className="inline-flex items-center gap-1.5">
                  <ArrowUp className="size-3.5" />
                  {t("courses.fields.updatedAt")}
                </span>
              </SelectItem>
              <SelectItem value="created_at:desc">
                <span className="inline-flex items-center gap-1.5">
                  <ArrowDown className="size-3.5" />
                  {t("courses.fields.createdAt")}
                </span>
              </SelectItem>
              <SelectItem value="created_at:asc">
                <span className="inline-flex items-center gap-1.5">
                  <ArrowUp className="size-3.5" />
                  {t("courses.fields.createdAt")}
                </span>
              </SelectItem>
            </SelectContent>
          </Select>

          {activeFilters && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={resetAllFilters}
              className="h-9 shrink-0"
            >
              <X className="size-4" />
              {t("courses.filters.reset")}
            </Button>
          )}
        </CardContent>
      </Card>

      {query.data?.items.length === 0 && !query.isLoading ? (
        <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-border p-10 text-center">
          <BookOpenCheck className="size-8 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">{t("courses.empty")}</p>
          {canCreate && (
            <Button asChild variant="outline" size="sm">
              <Link to={`/orgs/${orgId}/corsi/nuovo`}>
                <Plus className="size-4" />
                {t("courses.create")}
              </Link>
            </Button>
          )}
        </div>
      ) : (
        <DataTable<CourseListItemOut>
          columns={columns}
          data={query.data?.items ?? []}
          loading={query.isLoading}
          rowCount={query.data?.meta.total}
          pagination={pagination}
          onPaginationChange={onPaginationChange}
          rowKey={(r) => r.id}
          emptyMessage={t("courses.empty")}
        />
      )}

      <ConfirmDialog
        open={!!toDelete}
        title={t("courses.deleteConfirm.title")}
        message={t("courses.deleteConfirm.message", {
          title: toDelete?.title ?? "",
        })}
        destructive
        confirmLabel={t("common.delete")}
        onClose={() => setToDelete(null)}
        onConfirm={() => {
          if (toDelete) {
            deleteMut.mutate(toDelete.id);
            setToDelete(null);
          }
        }}
      />

      {toDuplicate && (
        <DuplicateCourseDialog
          orgId={orgId}
          course={toDuplicate}
          onClose={() => setToDuplicate(null)}
        />
      )}
    </div>
  );
}
