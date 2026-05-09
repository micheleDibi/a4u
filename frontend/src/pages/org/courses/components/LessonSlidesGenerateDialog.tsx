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

export type LessonSlidesGenerateMode =
  | "generate-lesson"
  | "regenerate-lesson"
  | "generate-all"
  | "regenerate-all";

interface Props {
  open: boolean;
  mode: LessonSlidesGenerateMode;
  isPending: boolean;
  /** Etichetta opzionale della lezione (es. "Lezione 3"), per il titolo. */
  lessonLabel?: string;
  onClose: () => void;
  onConfirm: (hint: string | null) => void;
}

/**
 * Dialog di pre-flight per la generazione/rigenerazione delle slide
 * (Fase 4 §7). Mirror di `LessonContentGenerateDialog` con i18n keys
 * della Fase 4. La textarea per il `regeneration_hint` è visibile solo
 * nei modes regenerate-*.
 */
export function LessonSlidesGenerateDialog({
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

  const isRegeneration =
    mode === "regenerate-lesson" || mode === "regenerate-all";
  const isBatch = mode === "generate-all" || mode === "regenerate-all";

  const titleKey = (() => {
    switch (mode) {
      case "generate-lesson":
        return "courses.lessonsSlides.dialog.generateLesson.title";
      case "regenerate-lesson":
        return "courses.lessonsSlides.dialog.regenerateLesson.title";
      case "generate-all":
        return "courses.lessonsSlides.dialog.generateAll.title";
      case "regenerate-all":
        return "courses.lessonsSlides.dialog.regenerateAll.title";
    }
  })();

  const descKey = (() => {
    switch (mode) {
      case "generate-lesson":
        return "courses.lessonsSlides.dialog.generateLesson.description";
      case "regenerate-lesson":
        return "courses.lessonsSlides.dialog.regenerateLesson.description";
      case "generate-all":
        return "courses.lessonsSlides.dialog.generateAll.description";
      case "regenerate-all":
        return "courses.lessonsSlides.dialog.regenerateAll.description";
    }
  })();

  const ctaKey = (() => {
    if (isPending) return "courses.lessonsSlides.dialog.generating";
    if (isBatch) {
      return isRegeneration
        ? "courses.lessonsSlides.dialog.regenerateAllCta"
        : "courses.lessonsSlides.dialog.generateAllCta";
    }
    return isRegeneration
      ? "courses.lessonsSlides.dialog.regenerateLessonCta"
      : "courses.lessonsSlides.dialog.generateLessonCta";
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
            <Label htmlFor="lesson-slides-hint">
              {t("courses.lessonsSlides.dialog.hintLabel")}
            </Label>
            <Textarea
              id="lesson-slides-hint"
              rows={4}
              maxLength={2000}
              value={hint}
              onChange={(e) => setHint(e.target.value)}
              placeholder={t("courses.lessonsSlides.dialog.hintPlaceholder")}
              disabled={isPending}
            />
            <p className="text-xs text-muted-foreground">
              {t("courses.lessonsSlides.dialog.hintHelper")}
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
