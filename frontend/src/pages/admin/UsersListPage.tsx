import { type ColumnDef, type PaginationState } from "@tanstack/react-table";
import { KeyRound, MoreHorizontal, Pencil, Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { usersApi } from "@/api/users";
import type { UserOut } from "@/api/types";
import { PageHeader } from "@/components/layout/PageHeader";
import { DataTable } from "@/components/shared/DataTable";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { extractApiError } from "@/lib/errors";
import {
  EditUserDialog,
  SetPasswordDialog,
} from "./components/UserActionsDialogs";

export default function UsersListPage() {
  const { t } = useTranslation();
  const [q, setQ] = useState("");
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 25 });
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<UserOut | null>(null);
  const [pwTarget, setPwTarget] = useState<UserOut | null>(null);
  const qc = useQueryClient();

  const query = useQuery({
    queryKey: ["users", pagination.pageIndex, pagination.pageSize, q],
    queryFn: () =>
      usersApi.list({
        page: pagination.pageIndex + 1,
        page_size: pagination.pageSize,
        q: q || undefined,
      }),
  });

  const createMut = useMutation({
    mutationFn: usersApi.create,
    onSuccess: () => {
      toast.success(t("users.created"));
      qc.invalidateQueries({ queryKey: ["users"] });
      setCreateOpen(false);
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  // Le guardie lato server (last_active_admin, cannot_deactivate_self, …)
  // possono rifiutare il toggle: in onError mostriamo il messaggio e
  // invalidiamo la query così lo Switch torna allo stato reale del server.
  const toggleActive = useMutation({
    mutationFn: (row: UserOut) => usersApi.update(row.id, { is_active: !row.is_active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
    onError: (err) => {
      toast.error(extractApiError(err).message);
      qc.invalidateQueries({ queryKey: ["users"] });
    },
  });

  const togglePlatformAdmin = useMutation({
    mutationFn: (row: UserOut) =>
      usersApi.update(row.id, { is_platform_admin: !row.is_platform_admin }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
    onError: (err) => {
      toast.error(extractApiError(err).message);
      qc.invalidateQueries({ queryKey: ["users"] });
    },
  });

  const editMut = useMutation({
    mutationFn: (vars: { id: string; full_name: string; email: string }) =>
      usersApi.update(vars.id, { full_name: vars.full_name, email: vars.email }),
    onSuccess: () => {
      toast.success(t("users.editDialog.saved"));
      qc.invalidateQueries({ queryKey: ["users"] });
      setEditTarget(null);
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const setPasswordMut = useMutation({
    mutationFn: (vars: { id: string; password: string }) =>
      usersApi.setPassword(vars.id, vars.password),
    onSuccess: () => {
      toast.success(t("users.resetPasswordDialog.saved"));
      setPwTarget(null);
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const columns: ColumnDef<UserOut>[] = [
    {
      id: "name",
      header: t("users.fields.name"),
      cell: ({ row }) => (
        <span className="block max-w-[280px] truncate" title={row.original.full_name}>
          {row.original.full_name}
        </span>
      ),
    },
    {
      id: "email",
      header: t("users.fields.email"),
      cell: ({ row }) => (
        <span
          className="block max-w-[260px] truncate text-muted-foreground"
          title={row.original.email}
        >
          {row.original.email}
        </span>
      ),
    },
    {
      id: "active",
      header: t("users.fields.active"),
      cell: ({ row }) => (
        <Switch
          checked={row.original.is_active}
          onCheckedChange={() => toggleActive.mutate(row.original)}
        />
      ),
      size: 100,
    },
    {
      id: "platformAdmin",
      header: t("users.fields.platformAdmin"),
      cell: ({ row }) => (
        <Switch
          checked={row.original.is_platform_admin}
          onCheckedChange={() => togglePlatformAdmin.mutate(row.original)}
        />
      ),
      size: 160,
    },
    {
      id: "actions",
      header: "",
      cell: ({ row }) => (
        <div className="flex justify-end">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="size-8"
                aria-label={t("users.actions.menu")}
              >
                <MoreHorizontal className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={() => setEditTarget(row.original)}>
                <Pencil className="size-3.5" />
                {t("users.actions.edit")}
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => setPwTarget(row.original)}>
                <KeyRound className="size-3.5" />
                {t("users.actions.resetPassword")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      ),
      size: 64,
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("users.title")}
        description={t("users.subtitle")}
        actions={
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="size-4" />
            {t("users.new")}
          </Button>
        }
      />
      <div className="max-w-md">
        <Input
          placeholder={t("users.search")}
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setPagination((p) => ({ ...p, pageIndex: 0 }));
          }}
        />
      </div>
      <DataTable<UserOut>
        columns={columns}
        data={query.data?.items ?? []}
        loading={query.isLoading}
        rowCount={query.data?.meta.total}
        pagination={pagination}
        onPaginationChange={setPagination}
        rowKey={(r) => r.id}
      />

      <CreateUserDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onSubmit={(data) => createMut.mutate(data)}
        loading={createMut.isPending}
        error={createMut.isError ? extractApiError(createMut.error).message : null}
      />

      <EditUserDialog
        open={!!editTarget}
        user={editTarget}
        isPending={editMut.isPending}
        onClose={() => setEditTarget(null)}
        onSubmit={(data) =>
          editTarget && editMut.mutate({ id: editTarget.id, ...data })
        }
      />

      <SetPasswordDialog
        open={!!pwTarget}
        user={pwTarget}
        isPending={setPasswordMut.isPending}
        onClose={() => setPwTarget(null)}
        onSubmit={(password) =>
          pwTarget && setPasswordMut.mutate({ id: pwTarget.id, password })
        }
      />
    </div>
  );
}

function CreateUserDialog({
  open,
  onClose,
  onSubmit,
  loading,
  error,
}: {
  open: boolean;
  onClose: () => void;
  onSubmit: (d: { email: string; full_name: string; password: string; is_platform_admin: boolean }) => void;
  loading: boolean;
  error: string | null;
}) {
  const { t } = useTranslation();
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  // Reset dei campi a ogni riapertura del dialog: il componente resta
  // montato tra un'apertura e l'altra (Radix gestisce solo la visibility),
  // quindi senza questo lo stato locale persiste dalla creazione precedente.
  useEffect(() => {
    if (open) {
      setEmail("");
      setFullName("");
      setPassword("");
      setIsAdmin(false);
    }
  }, [open]);
  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("users.createDialog.title")}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="cu-name">{t("users.createDialog.name")}</Label>
            <Input id="cu-name" value={fullName} onChange={(e) => setFullName(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="cu-email">{t("users.createDialog.email")}</Label>
            <Input id="cu-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="cu-pw">{t("users.createDialog.password")}</Label>
            <Input id="cu-pw" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              checked={isAdmin}
              onCheckedChange={(v) => setIsAdmin(Boolean(v))}
            />
            {t("users.createDialog.platformAdmin")}
          </label>
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button
            disabled={loading}
            onClick={() =>
              onSubmit({
                email,
                full_name: fullName,
                password,
                is_platform_admin: isAdmin,
              })
            }
          >
            {loading ? t("users.createDialog.submitting") : t("users.createDialog.submit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
