import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  type ColumnDef,
  type PaginationState,
} from "@tanstack/react-table";
import {
  BookOpenCheck,
  Edit,
  MoreHorizontal,
  Plus,
  Trash2,
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
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
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
import { flagFor } from "@/i18n/flags";
import { extractApiError } from "@/lib/errors";
import { P } from "@/lib/permissions";
import { CourseStatusBadge } from "./components/CourseStatusBadge";

const STATUS_FILTERS: CourseStatus[] = [
  "draft",
  "architecture_pending",
  "architecture_ready",
  "architecture_approved",
  "lessons_structure_pending",
  "lessons_structure_ready",
  "content_pending",
  "content_ready",
  "slides_pending",
  "slides_ready",
  "speech_pending",
  "speech_ready",
  "published",
  "archived",
];

const ALL_STATUS = "__all__";

export default function CoursesListPage() {
  const { t } = useTranslation();
  const params = useParams();
  const orgId = params.orgId!;
  const navigate = useNavigate();
  const qc = useQueryClient();

  const canCreate = useHasPermission(P.COURSE_CREATE, orgId);
  const canDelete = useHasPermission(P.COURSE_DELETE, orgId);

  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: 25,
  });
  const [q, setQ] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>(ALL_STATUS);
  const [toDelete, setToDelete] = useState<CourseListItemOut | null>(null);

  const query = useQuery({
    queryKey: ["courses", "list", orgId, pagination.pageIndex, pagination.pageSize, q, statusFilter],
    queryFn: () =>
      coursesApi.list(orgId, {
        page: pagination.pageIndex + 1,
        page_size: pagination.pageSize,
        q: q || undefined,
        status: statusFilter !== ALL_STATUS ? (statusFilter as CourseStatus) : undefined,
      }),
  });

  const deleteMut = useMutation({
    mutationFn: (courseId: string) => coursesApi.remove(orgId, courseId),
    onSuccess: () => {
      toast.success(t("courses.deleted"));
      qc.invalidateQueries({ queryKey: ["courses", "list", orgId] });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const columns: ColumnDef<CourseListItemOut>[] = [
    {
      id: "title",
      header: t("courses.fields.title"),
      cell: ({ row }) => (
        <Link
          to={`/orgs/${orgId}/corsi/${row.original.id}`}
          className="block max-w-[320px] truncate font-medium hover:underline"
        >
          {row.original.title}
        </Link>
      ),
    },
    {
      id: "assignee",
      header: t("courses.fields.assignee"),
      cell: ({ row }) => (
        <span className="text-sm text-muted-foreground">
          {row.original.assignee.full_name}
        </span>
      ),
    },
    {
      id: "status",
      header: t("courses.fields.status"),
      cell: ({ row }) => <CourseStatusBadge status={row.original.status} />,
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
      id: "modules",
      header: t("courses.fields.modulesCount"),
      cell: ({ row }) => (
        <span className="text-sm text-muted-foreground">
          {t("courses.summary.modulesShort", {
            modules: row.original.modules_count,
            cfu: row.original.cfu,
          })}
        </span>
      ),
    },
    {
      id: "updated",
      header: t("courses.fields.updatedAt"),
      cell: ({ row }) => (
        <span className="text-sm text-muted-foreground">
          {new Date(row.original.updated_at).toLocaleString()}
        </span>
      ),
    },
    {
      id: "actions",
      header: "",
      size: 64,
      cell: ({ row }) => (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon">
              <MoreHorizontal className="size-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem
              onSelect={() =>
                navigate(`/orgs/${orgId}/corsi/${row.original.id}`)
              }
            >
              <Edit className="size-4" />
              {t("common.edit")}
            </DropdownMenuItem>
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
      ),
    },
  ];

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

      <div className="flex flex-wrap items-center gap-2">
        <Input
          placeholder={t("courses.search")}
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setPagination((p) => ({ ...p, pageIndex: 0 }));
          }}
          className="max-w-md"
        />
        <Select
          value={statusFilter}
          onValueChange={(v) => {
            setStatusFilter(v);
            setPagination((p) => ({ ...p, pageIndex: 0 }));
          }}
        >
          <SelectTrigger className="w-56">
            <SelectValue placeholder={t("courses.filters.allStatuses")} />
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
      </div>

      {query.data?.items.length === 0 && !query.isLoading ? (
        <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-border p-10 text-center">
          <BookOpenCheck className="size-8 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">
            {t("courses.empty")}
          </p>
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
          onPaginationChange={setPagination}
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
    </div>
  );
}
