import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

export type LessonsStructureGenerateMode =
  | "generate-module"
  | "regenerate-module"
  | "generate-all"
  | "regenerate-all";

interface Props {
  open: boolean;
  mode: LessonsStructureGenerateMode;
  isPending: boolean;
  /** Etichetta opzionale del modulo (es. "Modulo 3"), per il titolo. */
  moduleLabel?: string;
  onClose: () => void;
  onConfirm: (hint: string | null) => void;
}

export function LessonsStructureGenerateDialog({
  open,
  mode,
  isPending,
  moduleLabel,
  onClose,
  onConfirm,
}: Props) {
  const { t } = useTranslation();
  const [hint, setHint] = useState("");

  useEffect(() => {
    if (!open) setHint("");
  }, [open]);

  const isRegeneration = mode === "regenerate-module" || mode === "regenerate-all";
  const isBatch = mode === "generate-all" || mode === "regenerate-all";

  const titleKey = (() => {
    switch (mode) {
      case "generate-module":
        return "courses.lessonsStructure.dialog.generateModule.title";
      case "regenerate-module":
        return "courses.lessonsStructure.dialog.regenerateModule.title";
      case "generate-all":
        return "courses.lessonsStructure.dialog.generateAll.title";
      case "regenerate-all":
        return "courses.lessonsStructure.dialog.regenerateAll.title";
    }
  })();

  const descKey = (() => {
    switch (mode) {
      case "generate-module":
        return "courses.lessonsStructure.dialog.generateModule.description";
      case "regenerate-module":
        return "courses.lessonsStructure.dialog.regenerateModule.description";
      case "generate-all":
        return "courses.lessonsStructure.dialog.generateAll.description";
      case "regenerate-all":
        return "courses.lessonsStructure.dialog.regenerateAll.description";
    }
  })();

  const ctaKey = (() => {
    if (isPending) return "courses.lessonsStructure.dialog.generating";
    if (isBatch) {
      return isRegeneration
        ? "courses.lessonsStructure.dialog.regenerateAllCta"
        : "courses.lessonsStructure.dialog.generateAllCta";
    }
    return isRegeneration
      ? "courses.lessonsStructure.dialog.regenerateModuleCta"
      : "courses.lessonsStructure.dialog.generateModuleCta";
  })();

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => (!v && !isPending ? onClose() : undefined)}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {t(titleKey, { module: moduleLabel ?? "" })}
          </DialogTitle>
          <DialogDescription>
            {t(descKey, { module: moduleLabel ?? "" })}
          </DialogDescription>
        </DialogHeader>

        {isRegeneration && (
          <div className="space-y-1.5">
            <Label htmlFor="lesson-structure-hint">
              {t("courses.lessonsStructure.dialog.hintLabel")}
            </Label>
            <Textarea
              id="lesson-structure-hint"
              rows={4}
              maxLength={2000}
              value={hint}
              onChange={(e) => setHint(e.target.value)}
              placeholder={t("courses.lessonsStructure.dialog.hintPlaceholder")}
              disabled={isPending}
            />
            <p className="text-xs text-muted-foreground">
              {t("courses.lessonsStructure.dialog.hintHelper")}
            </p>
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={isPending}>
            {t("common.cancel")}
          </Button>
          <Button
            onClick={() => onConfirm(hint.trim() || null)}
            disabled={isPending}
          >
            <Sparkles className="size-4" />
            {t(ctaKey)}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
