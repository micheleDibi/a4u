import { type ColumnDef } from "@tanstack/react-table";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router-dom";
import { toast } from "sonner";
import { membershipsApi } from "@/api/memberships";
import { organizationsApi } from "@/api/organizations";
import { usersApi } from "@/api/users";
import type { MembershipOut, UserOut } from "@/api/types";
import { PageHeader } from "@/components/layout/PageHeader";
import { DataTable } from "@/components/shared/DataTable";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { extractApiError } from "@/lib/errors";
import { ROLES, ROLE_CODES, type RoleCode } from "@/lib/permissions";

export default function OrganizationMembersPage() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const { t } = useTranslation();
  const [selectedUser, setSelectedUser] = useState<UserOut | null>(null);
  const [role, setRole] = useState<RoleCode>(ROLES.ORG_ADMIN);
  const [userSearch, setUserSearch] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);

  const orgQuery = useQuery({ queryKey: ["organization", id], queryFn: () => organizationsApi.get(id) });
  const membersQuery = useQuery({ queryKey: ["org", id, "members"], queryFn: () => membershipsApi.list(id) });
  const usersQuery = useQuery({
    queryKey: ["users", "search", userSearch],
    queryFn: () => usersApi.list({ q: userSearch || undefined, page_size: 25 }),
    placeholderData: (prev) => prev,
  });

  const enroll = useMutation({
    mutationFn: () => {
      if (!selectedUser) throw new Error("user_required");
      return organizationsApi.enrollUser(id, selectedUser.id, role);
    },
    onSuccess: () => {
      toast.success(t("members.invited"));
      qc.invalidateQueries({ queryKey: ["org", id, "members"] });
      setSelectedUser(null);
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const columns: ColumnDef<MembershipOut>[] = [
    {
      id: "name",
      header: t("members.fields.name"),
      cell: ({ row }) => (
        <span className="block max-w-[280px] truncate" title={row.original.user_full_name}>
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
    { id: "role", header: t("members.fields.role"), accessorKey: "role_name_it" },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title={`${t("members.title")} — ${orgQuery.data?.name ?? "…"}`}
        description={t("members.subtitleAdmin")}
      />

      <Card>
        <CardContent className="space-y-3 p-6">
          <h2 className="text-base font-semibold">{t("members.enroll")}</h2>
          <div className="flex flex-col gap-2 md:flex-row">
            <Popover open={pickerOpen} onOpenChange={setPickerOpen}>
              <PopoverTrigger asChild>
                <Button variant="outline" className="flex-1 justify-start text-left">
                  {selectedUser
                    ? `${selectedUser.full_name} <${selectedUser.email}>`
                    : t("members.userPicker")}
                </Button>
              </PopoverTrigger>
              <PopoverContent className="w-96 p-0">
                <Command shouldFilter={false}>
                  <CommandInput
                    placeholder={t("common.search")}
                    value={userSearch}
                    onValueChange={setUserSearch}
                  />
                  <CommandList>
                    <CommandEmpty>{t("common.noResults")}</CommandEmpty>
                    <CommandGroup>
                      {(usersQuery.data?.items ?? []).map((u) => (
                        <CommandItem
                          key={u.id}
                          value={u.email}
                          onSelect={() => {
                            setSelectedUser(u);
                            setPickerOpen(false);
                          }}
                        >
                          <span className="font-medium">{u.full_name}</span>
                          <span className="text-xs text-muted-foreground">{u.email}</span>
                        </CommandItem>
                      ))}
                    </CommandGroup>
                  </CommandList>
                </Command>
              </PopoverContent>
            </Popover>
            <Select value={role} onValueChange={(v) => setRole(v as RoleCode)}>
              <SelectTrigger className="md:w-60">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ROLE_CODES.map((r) => (
                  <SelectItem key={r} value={r}>
                    {t(`roles.${r}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button onClick={() => enroll.mutate()} disabled={!selectedUser || enroll.isPending}>
              {t("members.enrollSubmit")}
            </Button>
          </div>
        </CardContent>
      </Card>

      <DataTable<MembershipOut>
        columns={columns}
        data={membersQuery.data ?? []}
        loading={membersQuery.isLoading}
        rowKey={(r) => r.id}
      />
    </div>
  );
}
