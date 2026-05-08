import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Layers } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

const TITLE_MAX = 300;
const DESCRIPTION_MAX = 4000;

export interface ModuleDraft {
  title: string;
  description: string;
}

export interface ModuleEditMeta {
  code?: string;
  lessonsCount?: number;
}

interface Props {
  open: boolean;
  mode: "create" | "edit";
  initial?: ModuleDraft;
  meta?: ModuleEditMeta;
  isPending: boolean;
  onClose: () => void;
  onSubmit: (draft: ModuleDraft) => void;
}

export function ModuleEditDialog({
  open,
  mode,
  initial,
  meta,
  isPending,
  onClose,
  onSubmit,
}: Props) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<ModuleDraft>({
    title: "",
    description: "",
  });
  const titleRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setDraft({
        title: initial?.title ?? "",
        description: initial?.description ?? "",
      });
      // Autofocus dopo l'animazione di apertura del Dialog Radix.
      const id = window.setTimeout(() => {
        titleRef.current?.focus();
        titleRef.current?.select();
      }, 80);
      return () => window.clearTimeout(id);
    }
  }, [open, initial]);

  const trimmedTitle = draft.title.trim();
  const valid = trimmedTitle.length > 0;

  const submit = () => {
    if (!valid || isPending) return;
    onSubmit({
      title: trimmedTitle,
      description: draft.description.trim(),
    });
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      submit();
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => (!v && !isPending ? onClose() : undefined)}
    >
      <DialogContent
        className="max-w-2xl"
        onKeyDown={handleKeyDown}
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {meta?.code && (
              <Badge variant="secondary" className="font-mono text-xs">
                {meta.code}
              </Badge>
            )}
            {mode === "create"
              ? t("courses.architecture.module.createTitle")
              : t("courses.architecture.module.editTitle")}
          </DialogTitle>
          <DialogDescription>
            {t("courses.architecture.module.dialogDescription")}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-5 py-2">
          {/* Titolo */}
          <FormField
            label={
              <>
                {t("courses.architecture.module.fields.title")}
                <span className="ms-1 text-destructive">*</span>
              </>
            }
            htmlFor="module-title"
            current={draft.title.length}
            max={TITLE_MAX}
            hint={t("courses.architecture.module.fields.titleHint")}
          >
            <Input
              ref={titleRef}
              id="module-title"
              value={draft.title}
              maxLength={TITLE_MAX}
              onChange={(e) =>
                setDraft({ ...draft, title: e.target.value })
              }
              placeholder={t(
                "courses.architecture.module.fields.titlePlaceholder"
              )}
              disabled={isPending}
              className={cn(
                "text-base",
                !valid && draft.title.length > 0 && "border-destructive"
              )}
            />
          </FormField>

          {/* Descrizione */}
          <FormField
            label={t("courses.architecture.module.fields.description")}
            htmlFor="module-description"
            current={draft.description.length}
            max={DESCRIPTION_MAX}
            hint={t("courses.architecture.module.fields.descriptionHint")}
          >
            <Textarea
              id="module-description"
              rows={7}
              maxLength={DESCRIPTION_MAX}
              value={draft.description}
              onChange={(e) =>
                setDraft({ ...draft, description: e.target.value })
              }
              placeholder={t(
                "courses.architecture.module.fields.descriptionPlaceholder"
              )}
              disabled={isPending}
              className="resize-y leading-relaxed"
            />
          </FormField>

          {/* Riepilogo edit-mode */}
          {mode === "edit" && meta?.lessonsCount !== undefined && (
            <div className="flex items-center gap-2 rounded-md border border-dashed border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
              <Layers className="size-3.5" />
              <span>
                {t("courses.architecture.module.lessonsCount", {
                  count: meta.lessonsCount,
                })}
              </span>
            </div>
          )}
        </div>

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

interface FieldProps {
  label: React.ReactNode;
  htmlFor: string;
  current: number;
  max: number;
  hint?: string;
  children: React.ReactNode;
}

function FormField({ label, htmlFor, current, max, hint, children }: FieldProps) {
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
