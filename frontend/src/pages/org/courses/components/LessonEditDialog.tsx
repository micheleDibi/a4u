import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { BookPlus, GraduationCap, Trash2 } from "lucide-react";
import type { RecommendedBibliographyItem } from "@/api/courses";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

const TITLE_MAX = 300;
const SUMMARY_MAX = 4000;
const BIB_MAX_ITEMS = 20;

export interface LessonDraft {
  title: string;
  summary: string;
  is_introductory: boolean;
  recommended_bibliography: RecommendedBibliographyItem[];
}

export interface LessonEditMeta {
  code?: string;
  moduleLabel?: string;
}

interface Props {
  open: boolean;
  mode: "create" | "edit";
  initial?: LessonDraft;
  meta?: LessonEditMeta;
  isPending: boolean;
  onClose: () => void;
  onSubmit: (draft: LessonDraft) => void;
}

function emptyBibItem(): RecommendedBibliographyItem {
  return {
    authors: "",
    title: "",
    publisher: "",
    year: "",
    note: "",
    source: "from_uploaded_documents",
    confidence: "confirmed",
  };
}

export function LessonEditDialog({
  open,
  mode,
  initial,
  meta,
  isPending,
  onClose,
  onSubmit,
}: Props) {
  const { t } = useTranslation();
  // Lazy init dello state: clona `initial` UNA volta al mount. Vedi note in
  // LessonStructureEditDialog: `initial` è inline-object dal parent e cambia
  // riferimento ad ogni re-render, quindi un useEffect con `[open, initial]`
  // resetterebbe lo state mentre l'utente sta modificando.
  const [draft, setDraft] = useState<LessonDraft>(() => ({
    title: initial?.title ?? "",
    summary: initial?.summary ?? "",
    is_introductory: initial?.is_introductory ?? false,
    recommended_bibliography: initial?.recommended_bibliography ?? [],
  }));
  const titleRef = useRef<HTMLInputElement>(null);

  // Focus iniziale: solo al mount, una volta.
  useEffect(() => {
    if (!open) return;
    const id = window.setTimeout(() => {
      titleRef.current?.focus();
      titleRef.current?.select();
    }, 80);
    return () => window.clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const trimmedTitle = draft.title.trim();
  const valid = trimmedTitle.length > 0;

  const submit = () => {
    if (!valid || isPending) return;
    onSubmit({
      title: trimmedTitle,
      summary: draft.summary.trim(),
      is_introductory: draft.is_introductory,
      // Quando la lezione non è introduttiva, scartiamo i libri (§4.4).
      recommended_bibliography: draft.is_introductory
        ? draft.recommended_bibliography
        : [],
    });
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      submit();
    }
  };

  const updateBibItem = (
    idx: number,
    patch: Partial<RecommendedBibliographyItem>
  ) => {
    setDraft((prev) => {
      const next = [...prev.recommended_bibliography];
      next[idx] = { ...next[idx], ...patch };
      // Regola §4.4: source 'general_knowledge_suggestion' implica confidence 'to_verify'.
      if (
        patch.source === "general_knowledge_suggestion" &&
        next[idx].confidence === "confirmed"
      ) {
        next[idx].confidence = "to_verify";
      }
      return { ...prev, recommended_bibliography: next };
    });
  };

  const addBibItem = () => {
    setDraft((prev) => ({
      ...prev,
      recommended_bibliography: [
        ...prev.recommended_bibliography,
        emptyBibItem(),
      ],
    }));
  };

  const removeBibItem = (idx: number) => {
    setDraft((prev) => ({
      ...prev,
      recommended_bibliography: prev.recommended_bibliography.filter(
        (_, i) => i !== idx
      ),
    }));
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => (!v && !isPending ? onClose() : undefined)}
    >
      <DialogContent
        className="max-w-3xl"
        onKeyDown={handleKeyDown}
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {meta?.code && (
              <Badge variant="outline" className="font-mono text-xs">
                {meta.code}
              </Badge>
            )}
            {mode === "create"
              ? t("courses.architecture.lesson.createTitle")
              : t("courses.architecture.lesson.editTitle")}
            {meta?.moduleLabel && (
              <span className="text-sm font-normal text-muted-foreground">
                — {meta.moduleLabel}
              </span>
            )}
          </DialogTitle>
          <DialogDescription>
            {t("courses.architecture.lesson.dialogDescription")}
          </DialogDescription>
        </DialogHeader>

        <ScrollArea className="max-h-[65vh] pr-3">
          <div className="space-y-5 py-2">
            {/* Titolo */}
            <FormField
              label={
                <>
                  {t("courses.architecture.lesson.fields.title")}
                  <span className="ms-1 text-destructive">*</span>
                </>
              }
              htmlFor="lesson-title"
              current={draft.title.length}
              max={TITLE_MAX}
              hint={t("courses.architecture.lesson.fields.titleHint")}
            >
              <Input
                ref={titleRef}
                id="lesson-title"
                value={draft.title}
                maxLength={TITLE_MAX}
                onChange={(e) =>
                  setDraft({ ...draft, title: e.target.value })
                }
                placeholder={t(
                  "courses.architecture.lesson.fields.titlePlaceholder"
                )}
                disabled={isPending}
                className="text-base"
              />
            </FormField>

            {/* Sintesi */}
            <FormField
              label={t("courses.architecture.lesson.fields.summary")}
              htmlFor="lesson-summary"
              current={draft.summary.length}
              max={SUMMARY_MAX}
              hint={t("courses.architecture.lesson.fields.summaryHint")}
            >
              <Textarea
                id="lesson-summary"
                rows={5}
                maxLength={SUMMARY_MAX}
                value={draft.summary}
                onChange={(e) =>
                  setDraft({ ...draft, summary: e.target.value })
                }
                placeholder={t(
                  "courses.architecture.lesson.fields.summaryPlaceholder"
                )}
                disabled={isPending}
                className="resize-y leading-relaxed"
              />
            </FormField>

            {/* Toggle introduttiva */}
            <label
              htmlFor="lesson-intro"
              className={cn(
                "flex cursor-pointer items-start gap-3 rounded-md border border-border bg-muted/20 p-3 transition-colors",
                draft.is_introductory && "border-primary/40 bg-primary/5"
              )}
            >
              <Checkbox
                id="lesson-intro"
                checked={draft.is_introductory}
                onCheckedChange={(v) =>
                  setDraft({ ...draft, is_introductory: v === true })
                }
                disabled={isPending}
                className="mt-0.5"
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 text-sm font-medium">
                  <GraduationCap className="size-4" />
                  {t("courses.architecture.lesson.fields.isIntroductory")}
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {t("courses.architecture.lesson.fields.isIntroductoryHint")}
                </p>
              </div>
            </label>

            {/* Bibliografia (solo se introduttiva) */}
            {draft.is_introductory && (
              <BibliographyEditor
                items={draft.recommended_bibliography}
                disabled={isPending}
                onAdd={addBibItem}
                onUpdate={updateBibItem}
                onRemove={removeBibItem}
              />
            )}
          </div>
        </ScrollArea>

        <DialogFooter className="gap-2 sm:gap-0">
          <Button variant="ghost" onClick={onClose} disabled={isPending}>
            {t("common.cancel")}
          </Button>
          <Button
            onClick={submit}
            disabled={!valid || isPending}
            className="min-w-[120px]"
          >
            {isPending ? t("common.saving") : t("common.save")}
            <kbd className="ms-2 hidden rounded border border-primary-foreground/30 px-1.5 py-0.5 font-mono text-[10px] sm:inline">
              ⌘↵
            </kbd>
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Sotto-componenti
// ---------------------------------------------------------------------------

interface FieldProps {
  label: React.ReactNode;
  htmlFor: string;
  current: number;
  max: number;
  hint?: string;
  children: React.ReactNode;
}

function FormField({
  label,
  htmlFor,
  current,
  max,
  hint,
  children,
}: FieldProps) {
  const nearLimit = current >= max * 0.9;
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-3">
        <Label htmlFor={htmlFor}>{label}</Label>
        <span
          className={cn(
            "font-mono text-[11px] tabular-nums",
            nearLimit ? "text-warning" : "text-muted-foreground"
          )}
        >
          {current}/{max}
        </span>
      </div>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

interface BibProps {
  items: RecommendedBibliographyItem[];
  disabled: boolean;
  onAdd: () => void;
  onUpdate: (idx: number, patch: Partial<RecommendedBibliographyItem>) => void;
  onRemove: (idx: number) => void;
}

function BibliographyEditor({
  items,
  disabled,
  onAdd,
  onUpdate,
  onRemove,
}: BibProps) {
  const { t } = useTranslation();
  const atLimit = items.length >= BIB_MAX_ITEMS;

  return (
    <div className="space-y-3 rounded-lg border border-border p-4">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h4 className="text-sm font-semibold">
            {t("courses.architecture.lesson.bibliography.title")}
          </h4>
          <p className="text-xs text-muted-foreground">
            {t("courses.architecture.lesson.bibliography.hint")}
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={onAdd}
          disabled={disabled || atLimit}
        >
          <BookPlus className="size-4" />
          {t("courses.architecture.lesson.bibliography.add")}
        </Button>
      </div>

      {items.length === 0 ? (
        <p className="rounded border border-dashed border-border bg-muted/20 p-4 text-center text-xs text-muted-foreground">
          {t("courses.architecture.lesson.bibliography.empty")}
        </p>
      ) : (
        <ul className="space-y-3">
          {items.map((item, idx) => (
            <li
              key={idx}
              className="space-y-2 rounded-md border border-border bg-muted/10 p-3"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-mono text-muted-foreground">
                  #{idx + 1}
                </span>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="size-7 text-destructive"
                  onClick={() => onRemove(idx)}
                  disabled={disabled}
                  title={t("common.delete")}
                >
                  <Trash2 className="size-3.5" />
                </Button>
              </div>

              <div className="grid gap-2 sm:grid-cols-2">
                <div className="space-y-1 sm:col-span-2">
                  <Label className="text-xs">
                    {t("courses.architecture.lesson.bibliography.fields.authors")}
                  </Label>
                  <Input
                    value={item.authors}
                    onChange={(e) =>
                      onUpdate(idx, { authors: e.target.value })
                    }
                    placeholder={t(
                      "courses.architecture.lesson.bibliography.fields.authorsPlaceholder"
                    )}
                    disabled={disabled}
                  />
                </div>
                <div className="space-y-1 sm:col-span-2">
                  <Label className="text-xs">
                    {t("courses.architecture.lesson.bibliography.fields.title")}
                  </Label>
                  <Input
                    value={item.title}
                    onChange={(e) =>
                      onUpdate(idx, { title: e.target.value })
                    }
                    placeholder={t(
                      "courses.architecture.lesson.bibliography.fields.titlePlaceholder"
                    )}
                    disabled={disabled}
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">
                    {t("courses.architecture.lesson.bibliography.fields.publisher")}
                  </Label>
                  <Input
                    value={item.publisher}
                    onChange={(e) =>
                      onUpdate(idx, { publisher: e.target.value })
                    }
                    disabled={disabled}
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">
                    {t("courses.architecture.lesson.bibliography.fields.year")}
                  </Label>
                  <Input
                    value={item.year}
                    onChange={(e) =>
                      onUpdate(idx, { year: e.target.value })
                    }
                    placeholder="2024"
                    disabled={disabled}
                  />
                </div>
                <div className="space-y-1 sm:col-span-2">
                  <Label className="text-xs">
                    {t("courses.architecture.lesson.bibliography.fields.note")}
                  </Label>
                  <Textarea
                    value={item.note}
                    onChange={(e) =>
                      onUpdate(idx, { note: e.target.value })
                    }
                    rows={2}
                    placeholder={t(
                      "courses.architecture.lesson.bibliography.fields.notePlaceholder"
                    )}
                    disabled={disabled}
                    className="resize-y"
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">
                    {t("courses.architecture.lesson.bibliography.fields.source")}
                  </Label>
                  <Select
                    value={item.source}
                    onValueChange={(v) =>
                      onUpdate(idx, {
                        source:
                          v as RecommendedBibliographyItem["source"],
                      })
                    }
                    disabled={disabled}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="from_uploaded_documents">
                        {t(
                          "courses.architecture.view.bibliographySource.from_uploaded_documents"
                        )}
                      </SelectItem>
                      <SelectItem value="general_knowledge_suggestion">
                        {t(
                          "courses.architecture.view.bibliographySource.general_knowledge_suggestion"
                        )}
                      </SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">
                    {t(
                      "courses.architecture.lesson.bibliography.fields.confidence"
                    )}
                  </Label>
                  <Select
                    value={item.confidence}
                    onValueChange={(v) =>
                      onUpdate(idx, {
                        confidence:
                          v as RecommendedBibliographyItem["confidence"],
                      })
                    }
                    disabled={
                      disabled ||
                      // Source AI implica sempre to_verify (§4.4).
                      item.source === "general_knowledge_suggestion"
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="confirmed">
                        {t(
                          "courses.architecture.view.bibliographyConfidence.confirmed"
                        )}
                      </SelectItem>
                      <SelectItem value="to_verify">
                        {t(
                          "courses.architecture.view.bibliographyConfidence.to_verify"
                        )}
                      </SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
