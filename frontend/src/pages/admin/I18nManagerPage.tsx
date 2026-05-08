import { type ColumnDef } from "@tanstack/react-table";
import {
  AlertTriangle,
  Edit,
  MoreHorizontal,
  Pencil,
  Plus,
  Sparkles,
  Trash2,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import {
  i18nApi,
  type LanguageCreateInput,
  type LanguageOut,
  type LanguageUpdateInput,
} from "@/api/i18n";
import { PageHeader } from "@/components/layout/PageHeader";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { DataTable } from "@/components/shared/DataTable";
import { Badge } from "@/components/ui/badge";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { flagFor } from "@/i18n/flags";
import { reloadDbTranslations } from "@/i18n";
import { extractApiError } from "@/lib/errors";

export default function I18nManagerPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const navigate = useNavigate();

  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<LanguageOut | null>(null);
  const [toDelete, setToDelete] = useState<LanguageOut | null>(null);
  const [toAutoTranslate, setToAutoTranslate] = useState<LanguageOut | null>(null);

  const query = useQuery({
    queryKey: ["admin-languages"],
    queryFn: i18nApi.list,
  });

  const updateMut = useMutation({
    mutationFn: ({ code, data }: { code: string; data: LanguageUpdateInput }) =>
      i18nApi.update(code, data),
    onSuccess: () => {
      toast.success(t("i18n.updated"));
      qc.invalidateQueries({ queryKey: ["admin-languages"] });
      qc.invalidateQueries({ queryKey: ["i18n", "languages", "public"] });
      setEditing(null);
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const deleteMut = useMutation({
    mutationFn: (code: string) => i18nApi.remove(code),
    onSuccess: () => {
      toast.success(t("i18n.deleted"));
      qc.invalidateQueries({ queryKey: ["admin-languages"] });
      qc.invalidateQueries({ queryKey: ["i18n", "languages", "public"] });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const autoTranslateMut = useMutation({
    mutationFn: (code: string) => i18nApi.autoTranslate(code),
    onSuccess: async (data) => {
      toast.success(
        t("i18n.autoTranslateSuccess", {
          translated: data.translated,
          requested: data.requested,
        })
      );
      if (data.errors.length > 0) {
        toast.warning(data.errors[0]);
      }
      await reloadDbTranslations(data.code);
      qc.invalidateQueries({ queryKey: ["admin-languages"] });
      qc.invalidateQueries({ queryKey: ["admin-i18n-translations", data.code] });
      qc.invalidateQueries({ queryKey: ["i18n", "translations", data.code] });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const columns: ColumnDef<LanguageOut>[] = [
    {
      id: "flag",
      header: "",
      size: 56,
      cell: ({ row }) => {
        const Flag = flagFor(row.original.code, row.original.flag_country_code);
        return (
          <Flag className="size-5 rounded-sm shadow-[0_0_0_1px_rgba(0,0,0,0.08)]" />
        );
      },
    },
    {
      id: "code",
      header: t("i18n.fields.code"),
      cell: ({ row }) => (
        <span className="font-mono text-xs uppercase">{row.original.code}</span>
      ),
      size: 100,
    },
    {
      id: "name",
      header: t("i18n.fields.nameNative"),
      cell: ({ row }) => {
        const untranslated = row.original.untranslated_count ?? 0;
        return (
          <div className="flex items-center gap-2">
            <span className="truncate">{row.original.name_native}</span>
            {row.original.is_default && <Badge variant="brand">Default</Badge>}
            {row.original.rtl && <Badge variant="muted">RTL</Badge>}
            {untranslated > 0 && !row.original.is_default && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span
                    aria-label={t("i18n.untranslatedTooltip", {
                      count: untranslated,
                    })}
                    className="inline-flex items-center"
                  >
                    <AlertTriangle className="size-4 text-amber-500" />
                  </span>
                </TooltipTrigger>
                <TooltipContent className="max-w-xs">
                  {t("i18n.untranslatedTooltip", { count: untranslated })}
                </TooltipContent>
              </Tooltip>
            )}
          </div>
        );
      },
    },
    {
      id: "active",
      header: t("i18n.fields.active"),
      size: 100,
      cell: ({ row }) => (
        <Switch
          checked={row.original.is_active}
          disabled={row.original.is_default}
          onCheckedChange={(v) =>
            updateMut.mutate({ code: row.original.code, data: { is_active: v } })
          }
        />
      ),
    },
    {
      id: "actions",
      header: "",
      size: 64,
      cell: ({ row }) => {
        const canAutoTranslate =
          !row.original.is_default && (row.original.untranslated_count ?? 0) > 0;
        return (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button size="icon" variant="ghost">
                <MoreHorizontal className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem
                onSelect={() => navigate(`/admin/i18n/${row.original.code}`)}
              >
                <Pencil className="size-4" />
                {t("i18n.editTranslations")}
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => setEditing(row.original)}>
                <Edit className="size-4" />
                {t("i18n.edit")}
              </DropdownMenuItem>
              <DropdownMenuItem
                onSelect={() => setToAutoTranslate(row.original)}
                disabled={!canAutoTranslate || autoTranslateMut.isPending}
              >
                <Sparkles className="size-4" />
                {t("i18n.autoTranslate")}
              </DropdownMenuItem>
              <DropdownMenuItem
                onSelect={() => setToDelete(row.original)}
                disabled={row.original.is_default}
                className="text-destructive focus:text-destructive"
              >
                <Trash2 className="size-4" />
                {t("common.delete")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        );
      },
    },
  ];

  return (
    <TooltipProvider delayDuration={150}>
      <div className="space-y-6">
        <PageHeader
          title={t("i18n.title")}
          description={t("i18n.subtitle")}
          actions={
            <Button onClick={() => setCreateOpen(true)}>
              <Plus className="size-4" />
              {t("i18n.new")}
            </Button>
          }
        />

        <DataTable<LanguageOut>
          columns={columns}
          data={query.data ?? []}
          loading={query.isLoading}
          rowKey={(r) => r.code}
        />

        {createOpen && (
          <CreateLanguageDialog
            languages={query.data ?? []}
            onClose={() => setCreateOpen(false)}
            onCreated={() => {
              setCreateOpen(false);
              qc.invalidateQueries({ queryKey: ["admin-languages"] });
              qc.invalidateQueries({ queryKey: ["i18n", "languages", "public"] });
            }}
          />
        )}

        {editing && (
          <EditLanguageDialog
            language={editing}
            onClose={() => setEditing(null)}
            onSubmit={(data) =>
              updateMut.mutate({ code: editing.code, data })
            }
            loading={updateMut.isPending}
          />
        )}

        <ConfirmDialog
          open={!!toDelete}
          title={t("i18n.deleteConfirm.title")}
          message={t("i18n.deleteConfirm.message", { name: toDelete?.name_native ?? "" })}
          destructive
          confirmLabel={t("common.delete")}
          onClose={() => setToDelete(null)}
          onConfirm={() => {
            if (toDelete) {
              deleteMut.mutate(toDelete.code);
              setToDelete(null);
            }
          }}
        />

        <ConfirmDialog
          open={!!toAutoTranslate}
          title={t("i18n.autoTranslate")}
          message={t("i18n.autoTranslateConfirm", {
            name: toAutoTranslate?.name_native ?? "",
            count: toAutoTranslate?.untranslated_count ?? 0,
          })}
          confirmLabel={
            autoTranslateMut.isPending
              ? t("i18n.autoTranslateRunning")
              : t("i18n.autoTranslate")
          }
          onClose={() => setToAutoTranslate(null)}
          onConfirm={() => {
            if (toAutoTranslate) {
              autoTranslateMut.mutate(toAutoTranslate.code);
              setToAutoTranslate(null);
            }
          }}
        />
      </div>
    </TooltipProvider>
  );
}

function CreateLanguageDialog({
  languages,
  onClose,
  onCreated,
}: {
  languages: LanguageOut[];
  onClose: () => void;
  onCreated: () => void;
}) {
  const { t } = useTranslation();
  const [code, setCode] = useState("");
  const [nameNative, setNameNative] = useState("");
  const [flag, setFlag] = useState("");
  const [rtl, setRtl] = useState(false);
  const [copyFrom, setCopyFrom] = useState<string>("__none__");

  const create = useMutation({
    mutationFn: (data: LanguageCreateInput) => i18nApi.create(data),
    onSuccess: () => {
      toast.success(t("i18n.created"));
      onCreated();
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("i18n.new")}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="lng-code">{t("i18n.fields.code")}</Label>
            <Input
              id="lng-code"
              value={code}
              onChange={(e) => setCode(e.target.value.toLowerCase())}
              placeholder="it"
            />
            <p className="text-xs text-muted-foreground">{t("i18n.fields.codeHint")}</p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="lng-name">{t("i18n.fields.nameNative")}</Label>
            <Input
              id="lng-name"
              value={nameNative}
              onChange={(e) => setNameNative(e.target.value)}
              placeholder="Italiano"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="lng-flag">{t("i18n.fields.flagCountry")}</Label>
            <Input
              id="lng-flag"
              value={flag}
              onChange={(e) => setFlag(e.target.value.toUpperCase().slice(0, 2))}
              placeholder="IT"
              maxLength={2}
            />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={rtl} onCheckedChange={(v) => setRtl(Boolean(v))} />
            {t("i18n.fields.rtl")}
          </label>
          <div className="space-y-1.5">
            <Label>{t("i18n.fields.copyFrom")}</Label>
            <Select value={copyFrom} onValueChange={setCopyFrom}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__none__">—</SelectItem>
                {languages.map((l) => (
                  <SelectItem key={l.code} value={l.code}>
                    {l.code.toUpperCase()} — {l.name_native}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button
            disabled={create.isPending || !code || !nameNative}
            onClick={() =>
              create.mutate({
                code: code.trim(),
                name_native: nameNative.trim(),
                flag_country_code: flag || null,
                rtl,
                is_active: true,
                copy_translations_from: copyFrom !== "__none__" ? copyFrom : null,
              })
            }
          >
            {create.isPending ? t("common.creating") : t("common.add")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function EditLanguageDialog({
  language,
  onClose,
  onSubmit,
  loading,
}: {
  language: LanguageOut;
  onClose: () => void;
  onSubmit: (data: LanguageUpdateInput) => void;
  loading: boolean;
}) {
  const { t } = useTranslation();
  const [nameNative, setNameNative] = useState(language.name_native);
  const [flag, setFlag] = useState(language.flag_country_code ?? "");
  const [rtl, setRtl] = useState(language.rtl);
  const [isDefault, setIsDefault] = useState(language.is_default);

  useEffect(() => {
    setNameNative(language.name_native);
    setFlag(language.flag_country_code ?? "");
    setRtl(language.rtl);
    setIsDefault(language.is_default);
  }, [language]);

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("i18n.edit")}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label>{t("i18n.fields.code")}</Label>
            <Input value={language.code} disabled />
          </div>
          <div className="space-y-1.5">
            <Label>{t("i18n.fields.nameNative")}</Label>
            <Input
              value={nameNative}
              onChange={(e) => setNameNative(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label>{t("i18n.fields.flagCountry")}</Label>
            <Input
              value={flag}
              onChange={(e) => setFlag(e.target.value.toUpperCase().slice(0, 2))}
              maxLength={2}
            />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={rtl} onCheckedChange={(v) => setRtl(Boolean(v))} />
            {t("i18n.fields.rtl")}
          </label>
          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              checked={isDefault}
              onCheckedChange={(v) => setIsDefault(Boolean(v))}
              disabled={language.is_default}
            />
            {t("i18n.fields.default")}
          </label>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button
            disabled={loading}
            onClick={() =>
              onSubmit({
                name_native: nameNative,
                flag_country_code: flag || null,
                rtl,
                is_default: isDefault === language.is_default ? undefined : isDefault,
              })
            }
          >
            {loading ? t("common.saving") : t("common.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
