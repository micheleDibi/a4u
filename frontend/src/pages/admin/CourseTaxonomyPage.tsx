import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  ArrowDown,
  ArrowUp,
  Pencil,
  Plus,
  Sparkles,
  Trash2,
} from "lucide-react";
import {
  HIERARCHICAL_TAXONOMY_TYPES,
  TAXONOMIES_WITH_DESCRIPTION,
  TAXONOMY_TYPES,
  courseTaxonomyApi,
  type TaxonomyTermCreateInput,
  type TaxonomyTermOut,
  type TaxonomyTermUpdateInput,
  type TaxonomyType,
} from "@/api/courseTaxonomy";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { useLanguages } from "@/hooks/useLanguages";
import { flagFor } from "@/i18n/flags";
import { extractApiError } from "@/lib/errors";
import { cn } from "@/lib/utils";

const DEFAULT_LANG = "it";

function slugify(label: string): string {
  return label
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .replace(/^([0-9])/, "n$1")
    .slice(0, 80);
}

interface DraftState {
  mode: "create" | "edit";
  term: TaxonomyTermOut | null;
  slug: string;
  parentId: string | null;
  isActive: boolean;
  labels: Record<string, string>;
  descriptions: Record<string, string>;
}

function buildEmptyDraft(taxonomyType: TaxonomyType): DraftState {
  return {
    mode: "create",
    term: null,
    slug: "",
    parentId: null,
    isActive: true,
    labels: { [DEFAULT_LANG]: "" },
    descriptions: TAXONOMIES_WITH_DESCRIPTION.has(taxonomyType)
      ? { [DEFAULT_LANG]: "" }
      : {},
  };
}

function draftFromTerm(term: TaxonomyTermOut): DraftState {
  return {
    mode: "edit",
    term,
    slug: term.slug,
    parentId: term.parent_id,
    isActive: term.is_active,
    labels: { ...term.labels },
    descriptions: term.descriptions ? { ...term.descriptions } : {},
  };
}

export default function CourseTaxonomyPage() {
  const { t } = useTranslation();
  const [activeType, setActiveType] = useState<TaxonomyType>(TAXONOMY_TYPES[0]);

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("taxonomies.title")}
        description={t("taxonomies.subtitle")}
      />
      <Tabs
        value={activeType}
        onValueChange={(v) => setActiveType(v as TaxonomyType)}
      >
        <TabsList className="flex h-auto w-full flex-wrap justify-start gap-1">
          {TAXONOMY_TYPES.map((type) => (
            <TabsTrigger key={type} value={type} className="text-xs">
              {t(`taxonomies.${type}.title`)}
            </TabsTrigger>
          ))}
        </TabsList>
        {TAXONOMY_TYPES.map((type) => (
          <TabsContent key={type} value={type} className="space-y-4 pt-4">
            <TaxonomyPanel taxonomyType={type} />
          </TabsContent>
        ))}
      </Tabs>
    </div>
  );
}

function TaxonomyPanel({ taxonomyType }: { taxonomyType: TaxonomyType }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const isHierarchical = HIERARCHICAL_TAXONOMY_TYPES.has(taxonomyType);

  const query = useQuery({
    queryKey: ["course-taxonomy", taxonomyType],
    queryFn: () => courseTaxonomyApi.list(taxonomyType),
  });

  const [draft, setDraft] = useState<DraftState | null>(null);
  const [toDelete, setToDelete] = useState<TaxonomyTermOut | null>(null);

  const createMut = useMutation({
    mutationFn: (payload: TaxonomyTermCreateInput) =>
      courseTaxonomyApi.create(taxonomyType, payload),
    onSuccess: () => {
      toast.success(t("taxonomies.term.created"));
      setDraft(null);
      qc.invalidateQueries({ queryKey: ["course-taxonomy", taxonomyType] });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const updateMut = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: TaxonomyTermUpdateInput }) =>
      courseTaxonomyApi.update(taxonomyType, id, payload),
    onSuccess: () => {
      toast.success(t("taxonomies.term.saved"));
      setDraft(null);
      qc.invalidateQueries({ queryKey: ["course-taxonomy", taxonomyType] });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => courseTaxonomyApi.remove(taxonomyType, id),
    onSuccess: () => {
      toast.success(t("taxonomies.term.deleted"));
      qc.invalidateQueries({ queryKey: ["course-taxonomy", taxonomyType] });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const moveMut = useMutation({
    mutationFn: ({ id, direction }: { id: string; direction: "up" | "down" }) =>
      courseTaxonomyApi.move(taxonomyType, id, direction),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["course-taxonomy", taxonomyType] });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const toggleActiveMut = useMutation({
    mutationFn: ({ id, isActive }: { id: string; isActive: boolean }) =>
      courseTaxonomyApi.update(taxonomyType, id, { is_active: isActive }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["course-taxonomy", taxonomyType] });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const [bulkConfirmOpen, setBulkConfirmOpen] = useState(false);
  const bulkAutoTranslateMut = useMutation({
    mutationFn: () => courseTaxonomyApi.bulkAutoTranslate(taxonomyType),
    onSuccess: (res) => {
      toast.success(
        t("taxonomies.term.autoTranslateAllSuccess", {
          labels: res.translated_labels,
          descriptions: res.translated_descriptions,
          languages: res.languages_processed.length,
        })
      );
      qc.invalidateQueries({ queryKey: ["course-taxonomy", taxonomyType] });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const allTerms = query.data ?? [];
  const parents = useMemo(
    () => allTerms.filter((t) => t.parent_id === null),
    [allTerms]
  );
  const childrenByParent = useMemo(() => {
    const map = new Map<string, TaxonomyTermOut[]>();
    for (const term of allTerms) {
      if (term.parent_id) {
        const list = map.get(term.parent_id) ?? [];
        list.push(term);
        map.set(term.parent_id, list);
      }
    }
    return map;
  }, [allTerms]);

  // Per la conferma di delete, conta i figli da eliminare in cascata.
  const deleteChildrenCount = toDelete
    ? (childrenByParent.get(toDelete.id) ?? []).length
    : 0;

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div className="text-sm text-muted-foreground">
          {t(`taxonomies.${taxonomyType}.subtitle`)}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button
            variant="outline"
            onClick={() => setBulkConfirmOpen(true)}
            disabled={
              bulkAutoTranslateMut.isPending || allTerms.length === 0
            }
          >
            <Sparkles className="size-4" />
            {bulkAutoTranslateMut.isPending
              ? t("taxonomies.term.autoTranslateAllRunning")
              : t("taxonomies.term.autoTranslateAll")}
          </Button>
          <Button onClick={() => setDraft(buildEmptyDraft(taxonomyType))}>
            <Plus className="size-4" />
            {t("taxonomies.term.add")}
          </Button>
        </div>
      </div>

      {query.isLoading && (
        <div className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
          {t("common.loading")}
        </div>
      )}

      {!query.isLoading && parents.length === 0 && (
        <div className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
          {t(`taxonomies.${taxonomyType}.empty`)}
        </div>
      )}

      <div className="space-y-2">
        {parents.map((parent, parentIdx) => {
          const children = childrenByParent.get(parent.id) ?? [];
          return (
            <div key={parent.id} className="space-y-1">
              <TermRow
                term={parent}
                level={0}
                isFirst={parentIdx === 0}
                isLast={parentIdx === parents.length - 1}
                onEdit={() => setDraft(draftFromTerm(parent))}
                onDelete={() => setToDelete(parent)}
                onToggleActive={(v) =>
                  toggleActiveMut.mutate({ id: parent.id, isActive: v })
                }
                onMoveUp={() =>
                  moveMut.mutate({ id: parent.id, direction: "up" })
                }
                onMoveDown={() =>
                  moveMut.mutate({ id: parent.id, direction: "down" })
                }
              />
              {children.map((child, childIdx) => (
                <TermRow
                  key={child.id}
                  term={child}
                  level={1}
                  isFirst={childIdx === 0}
                  isLast={childIdx === children.length - 1}
                  onEdit={() => setDraft(draftFromTerm(child))}
                  onDelete={() => setToDelete(child)}
                  onToggleActive={(v) =>
                    toggleActiveMut.mutate({ id: child.id, isActive: v })
                  }
                  onMoveUp={() =>
                    moveMut.mutate({ id: child.id, direction: "up" })
                  }
                  onMoveDown={() =>
                    moveMut.mutate({ id: child.id, direction: "down" })
                  }
                />
              ))}
            </div>
          );
        })}
      </div>

      {draft && (
        <TermDialog
          taxonomyType={taxonomyType}
          isHierarchical={isHierarchical}
          parents={parents}
          draft={draft}
          setDraft={setDraft}
          onCancel={() => setDraft(null)}
          onSave={() => {
            const labels = Object.fromEntries(
              Object.entries(draft.labels)
                .map(([k, v]) => [k, (v ?? "").trim()])
                .filter(([, v]) => v)
            );
            const descriptions = TAXONOMIES_WITH_DESCRIPTION.has(taxonomyType)
              ? Object.fromEntries(
                  Object.entries(draft.descriptions)
                    .map(([k, v]) => [k, (v ?? "").trim()])
                    .filter(([, v]) => v)
                )
              : {};
            if (!labels[DEFAULT_LANG]) {
              toast.error(
                t("taxonomies.term.errors.defaultLabelRequired", {
                  lang: DEFAULT_LANG.toUpperCase(),
                })
              );
              return;
            }
            if (draft.mode === "create") {
              if (!draft.slug) {
                toast.error(t("taxonomies.term.errors.slugRequired"));
                return;
              }
              createMut.mutate({
                slug: draft.slug,
                parent_id: draft.parentId,
                is_active: draft.isActive,
                labels,
                descriptions:
                  Object.keys(descriptions).length > 0 ? descriptions : null,
              });
            } else if (draft.term) {
              const payload: TaxonomyTermUpdateInput = {
                is_active: draft.isActive,
                labels,
                descriptions:
                  Object.keys(descriptions).length > 0 ? descriptions : null,
              };
              if (isHierarchical) {
                if (draft.parentId === null && draft.term.parent_id !== null) {
                  payload.unset_parent = true;
                } else if (draft.parentId !== draft.term.parent_id) {
                  payload.parent_id = draft.parentId;
                }
              }
              updateMut.mutate({ id: draft.term.id, payload });
            }
          }}
          saving={createMut.isPending || updateMut.isPending}
        />
      )}

      <ConfirmDialog
        open={bulkConfirmOpen}
        title={t("taxonomies.term.autoTranslateAllConfirm.title")}
        message={t("taxonomies.term.autoTranslateAllConfirm.message", {
          count: allTerms.length,
        })}
        confirmLabel={t("taxonomies.term.autoTranslateAll")}
        onClose={() => setBulkConfirmOpen(false)}
        onConfirm={() => {
          setBulkConfirmOpen(false);
          bulkAutoTranslateMut.mutate();
        }}
      />

      <ConfirmDialog
        open={!!toDelete}
        title={t("taxonomies.term.deleteConfirm.title")}
        message={
          deleteChildrenCount > 0
            ? t("taxonomies.term.deleteConfirm.messageWithChildren", {
                name: toDelete?.labels[DEFAULT_LANG] ?? toDelete?.slug ?? "",
                count: deleteChildrenCount,
              })
            : t("taxonomies.term.deleteConfirm.message", {
                name: toDelete?.labels[DEFAULT_LANG] ?? toDelete?.slug ?? "",
              })
        }
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

interface TermRowProps {
  term: TaxonomyTermOut;
  level: number;
  isFirst: boolean;
  isLast: boolean;
  onEdit: () => void;
  onDelete: () => void;
  onToggleActive: (v: boolean) => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
}

function TermRow({
  term,
  level,
  isFirst,
  isLast,
  onEdit,
  onDelete,
  onToggleActive,
  onMoveUp,
  onMoveDown,
}: TermRowProps) {
  const { t, i18n } = useTranslation();
  const currentLng = i18n.resolvedLanguage || i18n.language || DEFAULT_LANG;
  const shortLng = currentLng.split("-")[0];
  const label =
    term.labels[currentLng] ||
    term.labels[shortLng] ||
    term.labels[DEFAULT_LANG] ||
    term.slug;

  return (
    <Card
      className={cn(
        !term.is_active && "bg-muted/30",
        level === 1 && "ms-8 border-dashed"
      )}
    >
      <CardContent className="flex flex-wrap items-center gap-2 p-3">
        <span
          className={cn(
            "min-w-0 flex-1 truncate text-sm",
            !term.is_active && "text-muted-foreground line-through"
          )}
        >
          {label}
        </span>
        <code className="shrink-0 rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
          {term.slug}
        </code>
        {!term.is_active && (
          <Badge variant="secondary" className="shrink-0 text-xs">
            {t("taxonomies.term.disabled")}
          </Badge>
        )}
        <div className="flex items-center gap-1">
          <Switch
            checked={term.is_active}
            onCheckedChange={onToggleActive}
            aria-label={t("taxonomies.term.fields.isActive")}
          />
          <Button
            variant="ghost"
            size="icon"
            disabled={isFirst}
            onClick={onMoveUp}
            title={t("taxonomies.term.moveUp")}
          >
            <ArrowUp className="size-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            disabled={isLast}
            onClick={onMoveDown}
            title={t("taxonomies.term.moveDown")}
          >
            <ArrowDown className="size-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={onEdit}
            title={t("common.edit")}
          >
            <Pencil className="size-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="text-destructive"
            onClick={onDelete}
            title={t("common.delete")}
          >
            <Trash2 className="size-4" />
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

interface TermDialogProps {
  taxonomyType: TaxonomyType;
  isHierarchical: boolean;
  parents: TaxonomyTermOut[];
  draft: DraftState;
  setDraft: (d: DraftState) => void;
  onCancel: () => void;
  onSave: () => void;
  saving: boolean;
}

function TermDialog({
  taxonomyType,
  isHierarchical,
  parents,
  draft,
  setDraft,
  onCancel,
  onSave,
  saving,
}: TermDialogProps) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const langs = useLanguages();
  const supportsDescriptions = TAXONOMIES_WITH_DESCRIPTION.has(taxonomyType);
  const [activeLang, setActiveLang] = useState<string>(DEFAULT_LANG);

  // Auto-slugify quando l'utente edita la label IT in modalità create.
  useEffect(() => {
    if (draft.mode !== "create") return;
    if (draft.slug && !draft.slug.startsWith("auto_")) return;
    const itLabel = (draft.labels[DEFAULT_LANG] ?? "").trim();
    if (!itLabel) return;
    const next = slugify(itLabel);
    if (next && next !== draft.slug.replace(/^auto_/, "")) {
      setDraft({ ...draft, slug: next });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft.labels[DEFAULT_LANG], draft.mode]);

  const autoTranslateMut = useMutation({
    mutationFn: () => {
      if (!draft.term) {
        throw new Error("auto-translate richiede un term salvato");
      }
      return courseTaxonomyApi.autoTranslate(taxonomyType, draft.term.id);
    },
    onSuccess: (res) => {
      toast.success(
        t("taxonomies.term.autoTranslateSuccess", {
          labels: res.translated_label_langs.length,
          descriptions: res.translated_description_langs.length,
        })
      );
      qc.invalidateQueries({ queryKey: ["course-taxonomy", taxonomyType] });
      // Aggiorna il draft con i nuovi valori senza chiudere il dialog.
      if (draft.term) {
        courseTaxonomyApi
          .get(taxonomyType, draft.term.id)
          .then((fresh) => setDraft(draftFromTerm(fresh)))
          .catch(() => undefined);
      }
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  return (
    <Dialog open onOpenChange={(v) => !v && onCancel()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            {draft.mode === "create"
              ? t("taxonomies.term.add")
              : t("taxonomies.term.edit")}
          </DialogTitle>
          <DialogDescription>
            {t(`taxonomies.${taxonomyType}.title`)}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="taxonomy-slug">
                {t("taxonomies.term.fields.slug")}
              </Label>
              <Input
                id="taxonomy-slug"
                value={draft.slug}
                onChange={(e) =>
                  setDraft({ ...draft, slug: e.target.value.toLowerCase() })
                }
                disabled={draft.mode === "edit"}
                placeholder="es. socratic"
                className="font-mono text-sm"
              />
              <p className="text-xs text-muted-foreground">
                {t("taxonomies.term.fields.slugHint")}
              </p>
            </div>
            <div className="space-y-1.5">
              <Label>{t("taxonomies.term.fields.isActive")}</Label>
              <div className="flex h-9 items-center gap-2">
                <Switch
                  checked={draft.isActive}
                  onCheckedChange={(v) => setDraft({ ...draft, isActive: v })}
                />
                <span className="text-sm text-muted-foreground">
                  {draft.isActive
                    ? t("taxonomies.term.activeYes")
                    : t("taxonomies.term.activeNo")}
                </span>
              </div>
            </div>
          </div>

          {isHierarchical && (
            <div className="space-y-1.5">
              <Label>{t("taxonomies.term.fields.parent")}</Label>
              <Select
                value={draft.parentId ?? "__none__"}
                onValueChange={(v) =>
                  setDraft({
                    ...draft,
                    parentId: v === "__none__" ? null : v,
                  })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none__">
                    {t("taxonomies.term.fields.parentNone")}
                  </SelectItem>
                  {parents
                    .filter((p) => p.id !== draft.term?.id)
                    .map((p) => (
                      <SelectItem key={p.id} value={p.id}>
                        {p.labels[DEFAULT_LANG] || p.slug}
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
            </div>
          )}

          <div className="space-y-2">
            <div className="flex items-center justify-between gap-2">
              <Label>{t("taxonomies.term.fields.labels")}</Label>
              {draft.mode === "edit" && draft.term && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => autoTranslateMut.mutate()}
                  disabled={autoTranslateMut.isPending}
                >
                  <Sparkles className="size-4" />
                  {autoTranslateMut.isPending
                    ? t("taxonomies.term.autoTranslateRunning")
                    : t("taxonomies.term.autoTranslate")}
                </Button>
              )}
            </div>
            <Tabs value={activeLang} onValueChange={setActiveLang}>
              <TabsList className="flex h-auto w-full flex-wrap justify-start gap-1">
                {langs.map((l) => {
                  const Flag = flagFor(l.code, l.flag_country_code);
                  return (
                    <TabsTrigger
                      key={l.code}
                      value={l.code}
                      className="text-xs"
                    >
                      <Flag className="me-1.5 size-3.5" />
                      <span className="uppercase">{l.code}</span>
                    </TabsTrigger>
                  );
                })}
              </TabsList>
              {langs.map((l) => (
                <TabsContent key={l.code} value={l.code} className="space-y-3 pt-3">
                  <div className="space-y-1.5">
                    <Label className="text-xs">
                      {t("taxonomies.term.fields.label")} ({l.name_native})
                    </Label>
                    <Input
                      value={draft.labels[l.code] ?? ""}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          labels: { ...draft.labels, [l.code]: e.target.value },
                        })
                      }
                    />
                  </div>
                  {supportsDescriptions && (
                    <div className="space-y-1.5">
                      <Label className="text-xs">
                        {t("taxonomies.term.fields.description")} ({l.name_native})
                      </Label>
                      <Textarea
                        rows={3}
                        value={draft.descriptions[l.code] ?? ""}
                        onChange={(e) =>
                          setDraft({
                            ...draft,
                            descriptions: {
                              ...draft.descriptions,
                              [l.code]: e.target.value,
                            },
                          })
                        }
                      />
                    </div>
                  )}
                </TabsContent>
              ))}
            </Tabs>
            {draft.mode === "create" && (
              <p className="text-xs text-muted-foreground">
                {t("taxonomies.term.autoTranslateHint")}
              </p>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onCancel}>
            {t("common.cancel")}
          </Button>
          <Button onClick={onSave} disabled={saving}>
            {saving ? t("common.saving") : t("common.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
