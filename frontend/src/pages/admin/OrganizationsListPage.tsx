import { type ColumnDef, type PaginationState } from "@tanstack/react-table";
import { Building2, Edit, ExternalLink, MoreHorizontal, Plus, Trash2, Users } from "lucide-react";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { organizationsApi } from "@/api/organizations";
import type { OrganizationOut } from "@/api/types";
import { PageHeader } from "@/components/layout/PageHeader";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { DataTable } from "@/components/shared/DataTable";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { extractApiError } from "@/lib/errors";

export default function OrganizationsListPage() {
  const { t } = useTranslation();
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 25 });
  const [q, setQ] = useState("");
  const [toDelete, setToDelete] = useState<OrganizationOut | null>(null);
  const navigate = useNavigate();
  const qc = useQueryClient();

  const query = useQuery({
    queryKey: ["organizations", pagination.pageIndex, pagination.pageSize, q],
    queryFn: () =>
      organizationsApi.list({
        page: pagination.pageIndex + 1,
        page_size: pagination.pageSize,
        q: q || undefined,
      }),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => organizationsApi.remove(id),
    onSuccess: () => {
      toast.success(t("organizations.deleted"));
      qc.invalidateQueries({ queryKey: ["organizations"] });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const columns: ColumnDef<OrganizationOut>[] = [
    {
      id: "logo",
      header: "",
      size: 56,
      cell: ({ row }) => (
        <Avatar className="size-9 rounded-md">
          {row.original.logo_path && (
            <AvatarImage src={row.original.logo_path} alt={row.original.name} />
          )}
          <AvatarFallback className="rounded-md">
            <Building2 className="size-4 text-muted-foreground" />
          </AvatarFallback>
        </Avatar>
      ),
    },
    {
      id: "name",
      header: t("organizations.fields.name"),
      cell: ({ row }) => (
        <span className="block max-w-[280px] truncate" title={row.original.name}>
          {row.original.name}
        </span>
      ),
    },
    {
      id: "email",
      header: t("organizations.fields.email"),
      cell: ({ row }) => (
        <span className="block max-w-[260px] truncate text-muted-foreground" title={row.original.email}>
          {row.original.email}
        </span>
      ),
    },
    { id: "city", header: t("organizations.fields.city"), accessorKey: "city" },
    { id: "country", header: t("organizations.fields.country"), accessorKey: "country" },
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
              onSelect={() => navigate(`/orgs/${row.original.id}`)}
            >
              <ExternalLink className="size-4" />
              {t("organizations.openDashboard")}
            </DropdownMenuItem>
            <DropdownMenuItem
              onSelect={() => navigate(`/admin/organizations/${row.original.id}/edit`)}
            >
              <Edit className="size-4" />
              {t("common.edit")}
            </DropdownMenuItem>
            <DropdownMenuItem
              onSelect={() => navigate(`/admin/organizations/${row.original.id}/members`)}
            >
              <Users className="size-4" />
              {t("organizations.members")}
            </DropdownMenuItem>
            <DropdownMenuItem
              onSelect={() => setToDelete(row.original)}
              className="text-destructive focus:text-destructive"
            >
              <Trash2 className="size-4" />
              {t("common.delete")}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("organizations.title")}
        description={t("organizations.subtitle")}
        actions={
          <Button asChild>
            <Link to="/admin/organizations/new">
              <Plus className="size-4" />
              {t("organizations.new")}
            </Link>
          </Button>
        }
      />

      <div className="max-w-md">
        <Input
          placeholder={t("organizations.search")}
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setPagination((p) => ({ ...p, pageIndex: 0 }));
          }}
        />
      </div>

      <DataTable<OrganizationOut>
        columns={columns}
        data={query.data?.items ?? []}
        loading={query.isLoading}
        rowCount={query.data?.meta.total}
        pagination={pagination}
        onPaginationChange={setPagination}
        rowKey={(r) => r.id}
        emptyMessage={t("organizations.empty")}
      />

      <ConfirmDialog
        open={!!toDelete}
        title={t("organizations.deleteConfirm.title")}
        message={t("organizations.deleteConfirm.message", { name: toDelete?.name ?? "" })}
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
