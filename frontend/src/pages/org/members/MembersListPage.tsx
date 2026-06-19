import { type ColumnDef } from "@tanstack/react-table";
import {
  ArrowLeftRight,
  Copy,
  Info,
  LockKeyhole,
  MoreHorizontal,
  Trash2,
  UserPlus,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { invitationsApi } from "@/api/invitations";
import { membershipsApi } from "@/api/memberships";
import type { MembershipOut } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { useHasPermission } from "@/auth/PermissionGate";
import { AvatarStatusDot } from "@/components/avatar/AvatarStatusDot";
import { PageHeader } from "@/components/layout/PageHeader";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { DataTable } from "@/components/shared/DataTable";
import { RolePermissionsBox } from "@/components/shared/RolePermissionsBox";
import { MemberAvatarDialog } from "./MemberAvatarDialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
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
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { extractApiError } from "@/lib/errors";
import { P, ROLES, type RoleCode } from "@/lib/permissions";

const ROLES_NO_CREATOR: RoleCode[] = [ROLES.ORG_ADMIN, ROLES.MANAGER, ROLES.MEMBER];

export default function MembersListPage() {
  const { orgId = "" } = useParams();
  const navigate = useNavigate();
  const { me } = useAuth();
  const qc = useQueryClient();
  const { t } = useTranslation();
  const canInvite = useHasPermission(P.MEMBER_INVITE);
  const canAssignRole = useHasPermission(P.MEMBER_ASSIGN_ROLE);
  const canRemove = useHasPermission(P.MEMBER_REMOVE);
  const canPermissions = useHasPermission(P.PERMISSION_MANAGE);
  const canTransfer = useHasPermission(P.ORG_TRANSFER_CREATOR);
  const canViewAvatar = useHasPermission(P.MEMBER_AVATAR_VIEW);

  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<RoleCode>(ROLES.MEMBER);
  const [inviteToken, setInviteToken] = useState<string | null>(null);
  const [toRemove, setToRemove] = useState<MembershipOut | null>(null);
  const [toTransfer, setToTransfer] = useState<MembershipOut | null>(null);
  const [avatarMember, setAvatarMember] = useState<MembershipOut | null>(null);

  // Quick action dalla command palette: `?invite=1` apre il dialog
  // d'invito (se l'utente ha il permesso). Il query param viene ripulito
  // così che un refresh non riapra il dialog.
  const [searchParams, setSearchParams] = useSearchParams();
  useEffect(() => {
    if (searchParams.get("invite") === "1" && canInvite) {
      setInviteOpen(true);
      const next = new URLSearchParams(searchParams);
      next.delete("invite");
      setSearchParams(next, { replace: true });
    }
  }, [searchParams, canInvite, setSearchParams]);

  const members = useQuery({
    queryKey: ["org", orgId, "members"],
    queryFn: () => membershipsApi.list(orgId),
  });

  const inviteMut = useMutation({
    mutationFn: () => invitationsApi.create(orgId, inviteEmail, inviteRole),
    onSuccess: (data) => setInviteToken(data.accept_url),
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const changeRoleMut = useMutation({
    mutationFn: (vars: { userId: string; role: RoleCode }) =>
      membershipsApi.changeRole(orgId, vars.userId, vars.role),
    onSuccess: () => {
      toast.success(t("members.roleChanged"));
      qc.invalidateQueries({ queryKey: ["org", orgId, "members"] });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const removeMut = useMutation({
    mutationFn: (userId: string) => membershipsApi.remove(orgId, userId),
    onSuccess: () => {
      toast.success(t("members.removed"));
      qc.invalidateQueries({ queryKey: ["org", orgId, "members"] });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const transferMut = useMutation({
    mutationFn: (userId: string) => membershipsApi.transferCreator(orgId, userId),
    onSuccess: () => {
      toast.success(t("members.transferConfirmed"));
      qc.invalidateQueries();
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const columns: ColumnDef<MembershipOut>[] = [
    {
      id: "name",
      header: t("members.fields.name"),
      cell: ({ row }) => (
        <span className="block max-w-[260px] truncate" title={row.original.user_full_name}>
          {row.original.user_full_name}
        </span>
      ),
    },
    {
      id: "email",
      header: t("members.fields.email"),
      cell: ({ row }) => (
        <span
          className="block max-w-[260px] truncate text-muted-foreground"
          title={row.original.user_email}
        >
          {row.original.user_email}
        </span>
      ),
    },
    {
      id: "role",
      header: t("members.fields.role"),
      cell: ({ row }) => {
        const m = row.original;
        const isCreator = m.role_code === ROLES.CREATOR;
        const isSelf = me?.user.id === m.user_id;
        if (!canAssignRole || isCreator || isSelf) {
          return <span className="text-sm">{m.role_name_it}</span>;
        }
        return (
          <div className="flex items-center gap-1.5">
            <Select
              value={m.role_code}
              onValueChange={(v) => changeRoleMut.mutate({ userId: m.user_id, role: v as RoleCode })}
            >
              <SelectTrigger className="h-8 w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ROLES_NO_CREATOR.map((r) => (
                  <SelectItem key={r} value={r}>
                    {t(`roles.${r}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Popover>
              <PopoverTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8 text-muted-foreground hover:text-foreground"
                  aria-label={t("roles.capabilitiesTitle", {
                    role: t(`roles.${m.role_code}`),
                  })}
                >
                  <Info className="size-4" />
                </Button>
              </PopoverTrigger>
              <PopoverContent
                className="w-80 max-h-[60vh] overflow-y-auto p-0"
                align="start"
              >
                <RolePermissionsBox
                  roleCode={m.role_code as RoleCode}
                  compact
                  className="border-0"
                />
              </PopoverContent>
            </Popover>
          </div>
        );
      },
    },
    {
      id: "actions",
      header: "",
      size: 64,
      cell: ({ row }) => {
        const m = row.original;
        const isCreator = m.role_code === ROLES.CREATOR;
        const isSelf = me?.user.id === m.user_id;
        const showPerm = canPermissions && !isCreator;
        const showTransfer = canTransfer && !isCreator && !isSelf;
        const showRemove = canRemove && !isCreator && !isSelf;
        if (!showPerm && !showTransfer && !showRemove) return null;
        return (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon">
                <MoreHorizontal className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {showPerm && (
                <DropdownMenuItem
                  onSelect={() => navigate(`/orgs/${orgId}/members/${m.user_id}/permissions`)}
                >
                  <LockKeyhole className="size-4" />
                  {t("members.actions.permissions")}
                </DropdownMenuItem>
              )}
              {showTransfer && (
                <DropdownMenuItem onSelect={() => setToTransfer(m)}>
                  <ArrowLeftRight className="size-4" />
                  {t("members.actions.transfer")}
                </DropdownMenuItem>
              )}
              {showRemove && (
                <DropdownMenuItem
                  onSelect={() => setToRemove(m)}
                  className="text-destructive focus:text-destructive"
                >
                  <Trash2 className="size-4" />
                  {t("members.actions.remove")}
                </DropdownMenuItem>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        );
      },
    },
  ];

  // Colonna "Avatar" (stato + anteprima) inserita prima delle azioni,
  // visibile solo a chi ha il permesso `member:avatar:view`.
  if (canViewAvatar) {
    columns.splice(columns.length - 1, 0, {
      id: "avatar",
      header: t("members.fields.avatar"),
      cell: ({ row }) => (
        <AvatarStatusDot
          status={row.original.avatar_status}
          audio={row.original.avatar_audio}
          onClick={() => setAvatarMember(row.original)}
        />
      ),
    });
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("members.title")}
        description={t("members.subtitle")}
        actions={
          canInvite && (
            <Button onClick={() => setInviteOpen(true)}>
              <UserPlus className="size-4" />
              {t("members.invite")}
            </Button>
          )
        }
      />

      <DataTable<MembershipOut>
        columns={columns}
        data={members.data ?? []}
        loading={members.isLoading}
        rowKey={(r) => r.id}
      />

      <Dialog
        open={inviteOpen}
        onOpenChange={(v) => {
          if (!v) {
            setInviteOpen(false);
            setInviteToken(null);
            setInviteEmail("");
          }
        }}
      >
        <DialogContent className="sm:max-w-md">
          {!inviteToken ? (
            <>
              <DialogHeader>
                <DialogTitle>{t("members.inviteDialog.title")}</DialogTitle>
              </DialogHeader>
              <div className="space-y-3">
                <div className="space-y-1.5">
                  <Label htmlFor="inv-email">{t("members.inviteDialog.email")}</Label>
                  <Input
                    id="inv-email"
                    type="email"
                    value={inviteEmail}
                    onChange={(e) => setInviteEmail(e.target.value)}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label>{t("members.inviteDialog.role")}</Label>
                  <Select value={inviteRole} onValueChange={(v) => setInviteRole(v as RoleCode)}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {ROLES_NO_CREATOR.map((r) => (
                        <SelectItem key={r} value={r}>
                          {t(`roles.${r}`)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <RolePermissionsBox roleCode={inviteRole} />
                {inviteMut.isError && (
                  <Alert variant="destructive">
                    <AlertDescription>{extractApiError(inviteMut.error).message}</AlertDescription>
                  </Alert>
                )}
              </div>
              <DialogFooter>
                <Button variant="outline" onClick={() => setInviteOpen(false)}>
                  {t("common.cancel")}
                </Button>
                <Button onClick={() => inviteMut.mutate()} disabled={inviteMut.isPending || !inviteEmail}>
                  {inviteMut.isPending
                    ? t("members.inviteDialog.submitting")
                    : t("members.inviteDialog.submit")}
                </Button>
              </DialogFooter>
            </>
          ) : (
            <>
              <DialogHeader>
                <DialogTitle>{t("members.inviteDialog.successTitle")}</DialogTitle>
                <DialogDescription>{t("members.inviteDialog.tokenLabel")}</DialogDescription>
              </DialogHeader>
              <div className="flex items-center gap-2">
                <Input value={inviteToken} readOnly />
                <Button
                  size="icon"
                  variant="outline"
                  onClick={() => {
                    void navigator.clipboard.writeText(inviteToken);
                    toast.success(t("common.copied"));
                  }}
                >
                  <Copy className="size-4" />
                </Button>
              </div>
              <DialogFooter>
                <Button onClick={() => { setInviteOpen(false); setInviteToken(null); setInviteEmail(""); }}>
                  {t("common.close")}
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={!!toRemove}
        title={t("members.removeConfirm.title")}
        message={t("members.removeConfirm.message", { name: toRemove?.user_full_name ?? "" })}
        destructive
        confirmLabel={t("common.remove")}
        onClose={() => setToRemove(null)}
        onConfirm={() => {
          if (toRemove) {
            removeMut.mutate(toRemove.user_id);
            setToRemove(null);
          }
        }}
      />
      <ConfirmDialog
        open={!!toTransfer}
        title={t("members.transferConfirm.title")}
        message={t("members.transferConfirm.message", { name: toTransfer?.user_full_name ?? "" })}
        confirmLabel={t("members.actions.transfer")}
        onClose={() => setToTransfer(null)}
        onConfirm={() => {
          if (toTransfer) {
            transferMut.mutate(toTransfer.user_id);
            setToTransfer(null);
          }
        }}
      />

      <MemberAvatarDialog
        orgId={orgId}
        member={avatarMember}
        onClose={() => setAvatarMember(null)}
      />
    </div>
  );
}
