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

export type LessonContentGenerateMode =
  | "generate-lesson"
  | "regenerate-lesson"
  | "generate-all"
  | "regenerate-all";

interface Props {
  open: boolean;
  mode: LessonContentGenerateMode;
  isPending: boolean;
  /** Etichetta opzionale della lezione (es. "Lezione 3"), per il titolo. */
  lessonLabel?: string;
  onClose: () => void;
  onConfirm: (hint: string | null) => void;
}

export function LessonContentGenerateDialog({
  open,
  mode,
  isPending,
  lessonLabel,
  onClose,
  onConfirm,
}: Props) {
  const { t } = useTranslation();
  const [hint, setHint] = useState("");

  useEffect(() => {
    if (!open) setHint("");
  }, [open]);

  const isRegeneration = mode === "regenerate-lesson" || mode === "regenerate-all";
  const isBatch = mode === "generate-all" || mode === "regenerate-all";

  const titleKey = (() => {
    switch (mode) {
      case "generate-lesson":
        return "courses.lessonsContent.dialog.generateLesson.title";
      case "regenerate-lesson":
        return "courses.lessonsContent.dialog.regenerateLesson.title";
      case "generate-all":
        return "courses.lessonsContent.dialog.generateAll.title";
      case "regenerate-all":
        return "courses.lessonsContent.dialog.regenerateAll.title";
    }
  })();

  const descKey = (() => {
    switch (mode) {
      case "generate-lesson":
        return "courses.lessonsContent.dialog.generateLesson.description";
      case "regenerate-lesson":
        return "courses.lessonsContent.dialog.regenerateLesson.description";
      case "generate-all":
        return "courses.lessonsContent.dialog.generateAll.description";
      case "regenerate-all":
        return "courses.lessonsContent.dialog.regenerateAll.description";
    }
  })();

  const ctaKey = (() => {
    if (isPending) return "courses.lessonsContent.dialog.generating";
    if (isBatch) {
      return isRegeneration
        ? "courses.lessonsContent.dialog.regenerateAllCta"
        : "courses.lessonsContent.dialog.generateAllCta";
    }
    return isRegeneration
      ? "courses.lessonsContent.dialog.regenerateLessonCta"
      : "courses.lessonsContent.dialog.generateLessonCta";
  })();

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => (!v && !isPending ? onClose() : undefined)}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {t(titleKey, { lesson: lessonLabel ?? "" })}
          </DialogTitle>
          <DialogDescription>
            {t(descKey, { lesson: lessonLabel ?? "" })}
          </DialogDescription>
        </DialogHeader>

        {isRegeneration && (
          <div className="space-y-1.5">
            <Label htmlFor="lesson-content-hint">
              {t("courses.lessonsContent.dialog.hintLabel")}
            </Label>
            <Textarea
              id="lesson-content-hint"
              rows={4}
              maxLength={2000}
              value={hint}
              onChange={(e) => setHint(e.target.value)}
              placeholder={t("courses.lessonsContent.dialog.hintPlaceholder")}
              disabled={isPending}
            />
            <p className="text-xs text-muted-foreground">
              {t("courses.lessonsContent.dialog.hintHelper")}
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
