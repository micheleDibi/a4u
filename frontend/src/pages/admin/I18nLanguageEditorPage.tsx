import { AlertTriangle, ChevronLeft, Eraser, Search, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { i18nApi } from "@/api/i18n";
import { PageHeader } from "@/components/layout/PageHeader";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { flagFor } from "@/i18n/flags";
import { reloadDbTranslations } from "@/i18n";
import { isMeaningfulTranslation } from "@/i18n/scripts";
import { extractApiError } from "@/lib/errors";

export default function I18nLanguageEditorPage() {
  const { code = "" } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { t } = useTranslation();
  const [search, setSearch] = useState("");
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [autoTranslateOpen, setAutoTranslateOpen] = useState(false);
  const [clearAllOpen, setClearAllOpen] = useState(false);
  const [onlyUntranslated, setOnlyUntranslated] = useState(false);

  const query = useQuery({
    queryKey: ["admin-i18n-translations", code],
    queryFn: () => i18nApi.getTranslations(code),
  });

  const referenceQuery = useQuery({
    queryKey: ["admin-i18n-translations", "it"],
    queryFn: () => i18nApi.getTranslations("it"),
    enabled: code !== "it",
  });

  const save = useMutation({
    mutationFn: (translations: Record<string, string>) =>
      i18nApi.patchTranslations(code, translations),
    onSuccess: (data) => {
      toast.success(t("i18n.translationsSaved", { count: data.upserted }));
      qc.invalidateQueries({ queryKey: ["admin-i18n-translations", code] });
      qc.invalidateQueries({ queryKey: ["i18n", "translations", code] });
      setEdits({});
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const autoTranslate = useMutation({
    mutationFn: () => i18nApi.autoTranslate(code),
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
      await reloadDbTranslations(code);
      qc.invalidateQueries({ queryKey: ["admin-languages"] });
      qc.invalidateQueries({ queryKey: ["admin-i18n-translations", code] });
      qc.invalidateQueries({ queryKey: ["i18n", "translations", code] });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const clearAll = useMutation({
    mutationFn: () => i18nApi.clearTranslations(code),
    onSuccess: async (data) => {
      toast.success(t("i18n.clearAllSuccess", { count: data.deleted }));
      setEdits({});
      await reloadDbTranslations(code);
      qc.invalidateQueries({ queryKey: ["admin-languages"] });
      qc.invalidateQueries({ queryKey: ["admin-i18n-translations", code] });
      qc.invalidateQueries({ queryKey: ["i18n", "translations", code] });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const language = query.data?.language;
  const Flag = flagFor(code, language?.flag_country_code);
  const translations = query.data?.translations ?? {};
  const reference = referenceQuery.data?.translations ?? {};
  const isItalian = code === "it";

  const keys = useMemo(() => {
    const set = new Set<string>([
      ...Object.keys(translations),
      ...Object.keys(reference),
    ]);
    return Array.from(set).sort();
  }, [translations, reference]);

  const isRowUntranslated = (k: string): boolean => {
    if (isItalian) return false;
    const ref = reference[k] ?? "";
    if (!ref.trim()) return false;
    const current = (k in edits ? edits[k] : translations[k]) ?? "";
    if (!current.trim()) return true;
    // Per script non-Latini: valore privo di caratteri dello script atteso
    // (echo del source italiano) → non tradotta. Per script Latini ritorna
    // sempre true qui (non-vuoto = tradotto).
    return !isMeaningfulTranslation(current, code);
  };

  const liveUntranslatedCount = useMemo(() => {
    if (isItalian) return 0;
    return keys.reduce((acc, k) => acc + (isRowUntranslated(k) ? 1 : 0), 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keys, edits, translations, reference, isItalian]);

  const untranslatedCount =
    liveUntranslatedCount || (language?.untranslated_count ?? 0);
  const canAutoTranslate = !isItalian && liveUntranslatedCount > 0;
  const totalEntries = Object.keys(translations).length;
  const canClearAll = !isItalian && totalEntries > 0;

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return keys.filter((k) => {
      if (onlyUntranslated && !isRowUntranslated(k)) return false;
      if (!q) return true;
      if (k.toLowerCase().includes(q)) return true;
      const v = (edits[k] ?? translations[k] ?? "").toLowerCase();
      if (v.includes(q)) return true;
      const r = (reference[k] ?? "").toLowerCase();
      return r.includes(q);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keys, search, onlyUntranslated, edits, translations, reference]);

  const dirtyCount = Object.keys(edits).length;

  const setVal = (k: string, v: string) => {
    setEdits((prev) => {
      const next = { ...prev };
      if (v === (translations[k] ?? "")) {
        delete next[k];
      } else {
        next[k] = v;
      }
      return next;
    });
  };

  return (
    <div className="space-y-6">
      <Button variant="ghost" size="sm" onClick={() => navigate("/admin/i18n")}>
        <ChevronLeft className="size-4" />
        {t("common.back")}
      </Button>

      <PageHeader
        title={
          <span className="inline-flex items-center gap-2">
            <Flag className="size-5 rounded-sm shadow-[0_0_0_1px_rgba(0,0,0,0.08)]" />
            {t("i18n.editor.title", { name: language?.name_native ?? code })}
          </span>
        }
        description={
          <span className="inline-flex items-center gap-2">
            <span className="font-mono text-xs uppercase">{code}</span>
            {language?.is_default && <Badge variant="brand">Default</Badge>}
            {untranslatedCount > 0 && (
              <Badge variant="warning" className="gap-1">
                <AlertTriangle className="size-3" />
                {t("i18n.untranslatedTooltip", { count: untranslatedCount })}
              </Badge>
            )}
            {dirtyCount > 0 ? (
              <Badge variant="muted">
                {t("i18n.editor.modifiedCount", { count: dirtyCount })}
              </Badge>
            ) : (
              <Badge variant="outline">{t("i18n.editor.noChanges")}</Badge>
            )}
          </span>
        }
        actions={
          <div className="flex items-center gap-2">
            {canClearAll && (
              <Button
                variant="outline"
                onClick={() => setClearAllOpen(true)}
                disabled={clearAll.isPending}
                className="text-destructive hover:text-destructive"
              >
                <Eraser className="size-4" />
                {clearAll.isPending
                  ? t("i18n.clearAllRunning")
                  : t("i18n.clearAll")}
              </Button>
            )}
            {canAutoTranslate && (
              <Button
                variant="outline"
                onClick={() => setAutoTranslateOpen(true)}
                disabled={autoTranslate.isPending}
              >
                <Sparkles className="size-4" />
                {autoTranslate.isPending
                  ? t("i18n.autoTranslateRunning")
                  : t("i18n.autoTranslate")}
              </Button>
            )}
            <Button
              onClick={() => save.mutate(edits)}
              disabled={save.isPending || dirtyCount === 0}
            >
              {save.isPending ? t("common.saving") : t("i18n.editor.save")}
            </Button>
          </div>
        }
      />

      <ConfirmDialog
        open={autoTranslateOpen}
        title={t("i18n.autoTranslate")}
        message={t("i18n.autoTranslateConfirm", {
          name: language?.name_native ?? code,
          count: untranslatedCount,
        })}
        confirmLabel={
          autoTranslate.isPending
            ? t("i18n.autoTranslateRunning")
            : t("i18n.autoTranslate")
        }
        onClose={() => setAutoTranslateOpen(false)}
        onConfirm={() => {
          autoTranslate.mutate();
          setAutoTranslateOpen(false);
        }}
      />

      <ConfirmDialog
        open={clearAllOpen}
        title={t("i18n.clearAllConfirm.title")}
        message={t("i18n.clearAllConfirm.message", {
          name: language?.name_native ?? code,
          count: totalEntries,
        })}
        destructive
        confirmLabel={
          clearAll.isPending ? t("i18n.clearAllRunning") : t("i18n.clearAll")
        }
        onClose={() => setClearAllOpen(false)}
        onConfirm={() => {
          clearAll.mutate();
          setClearAllOpen(false);
        }}
      />

      <div className="flex flex-wrap items-center gap-3">
        <div className="relative max-w-md flex-1 min-w-[240px]">
          <Search className="absolute start-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder={t("i18n.editor.search")}
            className="ps-9"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        {!isItalian && liveUntranslatedCount > 0 && (
          <label className="inline-flex items-center gap-2 text-sm">
            <Checkbox
              checked={onlyUntranslated}
              onCheckedChange={(v) => setOnlyUntranslated(Boolean(v))}
            />
            <AlertTriangle className="size-4 text-amber-500" />
            {t("i18n.editor.onlyUntranslated", { count: liveUntranslatedCount })}
          </label>
        )}
      </div>

      <Card>
        <CardContent className="divide-y divide-border p-0">
          {filtered.length === 0 ? (
            <div className="p-6 text-center text-sm text-muted-foreground">
              {t("i18n.editor.empty")}
            </div>
          ) : (
            filtered.map((k) => {
              const ref = reference[k] ?? "";
              const dirty = k in edits;
              const value = dirty ? edits[k] : translations[k] ?? "";
              const isLong = (value?.length ?? 0) > 80 || (ref?.length ?? 0) > 80;
              const untranslated = isRowUntranslated(k);
              return (
                <div
                  key={k}
                  className={cn(
                    "grid gap-3 p-4 lg:grid-cols-[280px_1fr_1fr]",
                    untranslated &&
                      "bg-amber-50/60 dark:bg-amber-500/5 border-l-2 border-amber-400"
                  )}
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5">
                      {untranslated && (
                        <AlertTriangle
                          className="size-4 shrink-0 text-amber-500"
                          aria-label={t("i18n.editor.untranslatedRow")}
                        />
                      )}
                      <code
                        className="block truncate text-xs text-muted-foreground"
                        title={k}
                      >
                        {k}
                      </code>
                    </div>
                    <div className="mt-1 flex items-center gap-1.5">
                      {dirty && <Badge variant="brand">●</Badge>}
                      {untranslated && (
                        <Badge variant="warning" className="text-[10px]">
                          {t("i18n.editor.untranslatedRow")}
                        </Badge>
                      )}
                    </div>
                  </div>
                  <div className="space-y-1">
                    <Label className="text-[10px] uppercase tracking-wider text-muted-foreground">
                      {code}
                    </Label>
                    {isLong ? (
                      <Textarea
                        rows={2}
                        value={value}
                        onChange={(e) => setVal(k, e.target.value)}
                        className={cn(
                          untranslated &&
                            "border-amber-300 focus-visible:ring-amber-400"
                        )}
                      />
                    ) : (
                      <Input
                        value={value}
                        onChange={(e) => setVal(k, e.target.value)}
                        className={cn(
                          untranslated &&
                            "border-amber-300 focus-visible:ring-amber-400"
                        )}
                      />
                    )}
                  </div>
                  {!isItalian && (
                    <div className="space-y-1">
                      <Label className="text-[10px] uppercase tracking-wider text-muted-foreground">
                        {t("i18n.editor.reference", { lang: "it" })}
                      </Label>
                      <div className="rounded-md border border-dashed border-border bg-muted/40 px-3 py-2 text-sm text-muted-foreground">
                        {ref || "—"}
                      </div>
                    </div>
                  )}
                </div>
              );
            })
          )}
        </CardContent>
      </Card>
    </div>
  );
}
